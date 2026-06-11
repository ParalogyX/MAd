"""Market data providers and the public OHLCV loading function."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from io import StringIO
from typing import Iterable

import numpy as np
import pandas as pd
import requests

from investment_adviser.config import (
    BINANCE_INTERVALS,
    REQUEST_TIMEOUT_SECONDS,
    STOOQ_INTERVALS,
    SUPPORTED_TIMEFRAMES,
    USER_AGENT,
    YFINANCE_INTERVALS,
)
from investment_adviser.exceptions import DataProviderError, ValidationError
from investment_adviser.models import MarketDataRequest, SentimentDocument
from investment_adviser.providers.base import MarketDataProvider, SentimentSourceProvider
from investment_adviser.providers.mt5 import MT5MarketDataProvider
from investment_adviser.providers.symbols import (
    candidate_binance_symbols,
    candidate_stooq_symbols,
    candidate_yfinance_symbols,
)
from investment_adviser.utils.validation import (
    normalize_market_data_frame,
    normalize_symbol,
    validate_time_range,
    validate_timeframe,
)


class YFinanceMarketDataProvider(MarketDataProvider):
    """Load public OHLCV data using the optional yfinance package."""

    name = "yfinance"

    def load_data(self, request: MarketDataRequest) -> pd.DataFrame:
        """Return normalized OHLCV data from yfinance with symbol aliases."""

        try:
            import yfinance as yf
        except ImportError as exc:
            raise DataProviderError(
                "The yfinance provider requires yfinance. Install with "
                "`pip install investment-adviser[providers]`."
            ) from exc

        interval = YFINANCE_INTERVALS[request.timeframe]
        errors: list[str] = []
        for provider_symbol in candidate_yfinance_symbols(request.symbol):
            try:
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    raw = yf.download(
                        provider_symbol,
                        start=request.begin_time,
                        end=request.end_time,
                        interval=interval,
                        progress=False,
                        auto_adjust=False,
                        threads=False,
                        timeout=REQUEST_TIMEOUT_SECONDS,
                    )
            except Exception as exc:
                errors.append(f"{provider_symbol}: {exc}")
                continue

            if raw.empty:
                errors.append(f"{provider_symbol}: empty response")
                continue

            data = self._normalize_yfinance_frame(raw)
            data = _drop_incomplete_ohlcv_rows(data)
            if data.empty:
                errors.append(f"{provider_symbol}: no complete OHLCV rows")
                continue
            if request.timeframe == "4h":
                data = self._resample_to_4h(data)
            return data

        raise DataProviderError(
            "No yfinance market data returned for "
            f"{request.symbol}. Tried: {', '.join(errors)}"
        )

    @staticmethod
    def _normalize_yfinance_frame(raw: pd.DataFrame) -> pd.DataFrame:
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        data = raw.reset_index()
        timestamp_column = "Datetime" if "Datetime" in data.columns else "Date"
        data = data.rename(
            columns={
                timestamp_column: "timestamp",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )
        return data[["timestamp", "open", "high", "low", "close", "volume"]]

    @staticmethod
    def _resample_to_4h(data: pd.DataFrame) -> pd.DataFrame:
        normalized = normalize_market_data_frame(data)
        resampled = (
            normalized.set_index("timestamp")
            .resample("4h")
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna(subset=["open", "high", "low", "close"])
            .reset_index()
        )
        return resampled


class BinanceMarketDataProvider(MarketDataProvider):
    """Load crypto OHLCV data from Binance public market data endpoints."""

    name = "binance"

    def __init__(
        self,
        base_url: str = "https://api.binance.com",
        timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def load_data(self, request: MarketDataRequest) -> pd.DataFrame:
        """Return normalized crypto OHLCV data from Binance klines."""

        interval = BINANCE_INTERVALS[request.timeframe]
        errors: list[str] = []
        for provider_symbol in candidate_binance_symbols(request.symbol):
            try:
                data = self._load_symbol(request, provider_symbol, interval)
            except requests.RequestException as exc:
                errors.append(f"{provider_symbol}: {exc}")
                continue
            if not data.empty:
                return data
            errors.append(f"{provider_symbol}: empty response")

        tried = ", ".join(errors) or "no crypto aliases"
        raise DataProviderError(
            f"No Binance market data returned for {request.symbol}. Tried: {tried}"
        )

    def _load_symbol(
        self,
        request: MarketDataRequest,
        provider_symbol: str,
        interval: str,
    ) -> pd.DataFrame:
        rows: list[list[object]] = []
        start_ms = int(request.begin_time.timestamp() * 1000)
        end_ms = int(request.end_time.timestamp() * 1000)
        endpoint = f"{self.base_url}/api/v3/klines"

        while start_ms < end_ms:
            response = requests.get(
                endpoint,
                params={
                    "symbol": provider_symbol,
                    "interval": interval,
                    "startTime": start_ms,
                    "endTime": end_ms,
                    "limit": 1000,
                },
                headers={"User-Agent": USER_AGENT},
                timeout=self.timeout_seconds,
            )
            if response.status_code in {400, 404}:
                return pd.DataFrame()
            response.raise_for_status()
            batch = response.json()
            if not batch:
                break

            rows.extend(batch)
            next_start_ms = int(batch[-1][0]) + 1
            if next_start_ms <= start_ms:
                break
            start_ms = next_start_ms
            if len(batch) < 1000:
                break

        return pd.DataFrame(
            [
                {
                    "timestamp": pd.to_datetime(row[0], unit="ms", utc=True),
                    "open": row[1],
                    "high": row[2],
                    "low": row[3],
                    "close": row[4],
                    "volume": row[5],
                }
                for row in rows
            ]
        )


class StooqMarketDataProvider(MarketDataProvider):
    """Load daily OHLCV data from Stooq's public CSV endpoint."""

    name = "stooq"

    def __init__(
        self,
        base_url: str = "https://stooq.com/q/d/l/",
        timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds

    def load_data(self, request: MarketDataRequest) -> pd.DataFrame:
        """Return normalized daily OHLCV data from Stooq."""

        if request.timeframe not in STOOQ_INTERVALS:
            raise DataProviderError("Stooq fallback only supports timeframe='1d'.")

        errors: list[str] = []
        for provider_symbol in candidate_stooq_symbols(request.symbol):
            try:
                data = self._load_symbol(request, provider_symbol)
            except requests.RequestException as exc:
                errors.append(f"{provider_symbol}: {exc}")
                continue
            if not data.empty:
                return data
            errors.append(f"{provider_symbol}: empty response")

        raise DataProviderError(
            "No Stooq market data returned for "
            f"{request.symbol}. Tried: {', '.join(errors)}"
        )

    def _load_symbol(
        self,
        request: MarketDataRequest,
        provider_symbol: str,
    ) -> pd.DataFrame:
        response = requests.get(
            self.base_url,
            params={
                "s": provider_symbol,
                "d1": request.begin_time.strftime("%Y%m%d"),
                "d2": request.end_time.strftime("%Y%m%d"),
                "i": STOOQ_INTERVALS[request.timeframe],
            },
            headers={"User-Agent": USER_AGENT},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        if not response.text.strip() or response.text.lower().startswith("no data"):
            return pd.DataFrame()

        raw = pd.read_csv(StringIO(response.text))
        if raw.empty or "Date" not in raw.columns:
            return pd.DataFrame()
        data = raw.rename(
            columns={
                "Date": "timestamp",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )
        if "volume" not in data.columns:
            data["volume"] = 0.0
        data["volume"] = pd.to_numeric(data["volume"], errors="coerce").fillna(0.0)
        data = data[["timestamp", "open", "high", "low", "close", "volume"]]
        return _drop_incomplete_ohlcv_rows(data)


class CompositeMarketDataProvider(MarketDataProvider):
    """Try multiple public fallback market data providers in order."""

    name = "fallback"

    def __init__(self, providers: list[MarketDataProvider] | None = None):
        self.providers = providers or [
            YFinanceMarketDataProvider(),
            BinanceMarketDataProvider(),
            StooqMarketDataProvider(),
        ]

    def load_data(self, request: MarketDataRequest) -> pd.DataFrame:
        """Return data from the first fallback provider that succeeds."""

        errors: list[str] = []
        for provider in self.providers:
            try:
                return provider.load_data(request)
            except DataProviderError as exc:
                errors.append(f"{provider.name}: {exc}")
        raise DataProviderError(
            "No fallback market data provider succeeded. " + " | ".join(errors)
        )


class DeterministicMarketDataProvider(MarketDataProvider):
    """Generate deterministic OHLCV data for tests and local examples."""

    name = "mock"

    def load_data(self, request: MarketDataRequest) -> pd.DataFrame:
        """Return synthetic but deterministic OHLCV data for a request."""

        frequency = SUPPORTED_TIMEFRAMES[request.timeframe]
        timestamps = pd.date_range(
            start=request.begin_time,
            end=request.end_time,
            freq=frequency,
            inclusive="left",
            tz="UTC",
        )
        if timestamps.empty:
            raise DataProviderError("The requested time range produced no candles.")

        seed = sum(ord(char) for char in request.symbol)
        base = 50.0 + (seed % 100)
        index = np.arange(len(timestamps), dtype=float)
        close = base + index * 0.25 + np.sin(index / 5.0) * 1.5
        open_ = close - 0.15 + np.cos(index / 7.0) * 0.2
        high = np.maximum(open_, close) + 0.75
        low = np.minimum(open_, close) - 0.75
        volume = 1_000.0 + (seed % 250) + index * 10.0

        return pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )


class MockSentimentSourceProvider(SentimentSourceProvider):
    """Return deterministic sentiment documents supplied by tests."""

    name = "mock"

    def __init__(self, documents: Iterable[SentimentDocument | str] | None = None):
        self._documents = list(documents or [])

    def fetch_documents(self, symbol: str) -> list[SentimentDocument]:
        """Return configured mock sentiment documents."""

        documents: list[SentimentDocument] = []
        for index, document in enumerate(self._documents):
            if isinstance(document, SentimentDocument):
                documents.append(document)
            else:
                documents.append(
                    SentimentDocument(
                        source=self.name,
                        title=f"{symbol} mock document {index}",
                        summary=str(document),
                    )
                )
        return documents


_MT5_PROVIDER = MT5MarketDataProvider()

_MARKET_DATA_PROVIDERS: dict[str, MarketDataProvider] = {
    "mt5": _MT5_PROVIDER,
    "libertex": _MT5_PROVIDER,
    "fallback": _MT5_PROVIDER,
    "mock": DeterministicMarketDataProvider(),
}


def register_market_data_provider(name: str, provider: MarketDataProvider) -> None:
    """Register or replace a market data provider by name."""

    if not name.strip():
        raise ValueError("Provider name must not be empty.")
    _MARKET_DATA_PROVIDERS[name.strip().lower()] = provider


def load_symbol_data(
    symbol: str,
    timeframe: str,
    begin_time: datetime,
    end_time: datetime,
    provider: str = "auto",
) -> pd.DataFrame:
    """Load normalized OHLCV data for a symbol and time range.

    Args:
        symbol: Instrument ticker or symbol.
        timeframe: Candle timeframe. Supported values are 1m, 5m, 15m, 30m,
            1h, 4h, and 1d.
        begin_time: Start datetime. Naive datetimes are interpreted as UTC.
        end_time: End datetime. Naive datetimes are interpreted as UTC.
        provider: Data provider name: auto, mt5, libertex, fallback, or mock.

    Returns:
        A DataFrame containing timestamp, open, high, low, close, and volume.

    Raises:
        ValueError: If timeframe, provider, or date range arguments are invalid.
        DataProviderError: If no provider can return valid OHLCV data.
    """

    normalized_symbol = normalize_symbol(symbol)
    normalized_timeframe = validate_timeframe(timeframe)
    normalized_begin, normalized_end = validate_time_range(begin_time, end_time)
    request = MarketDataRequest(
        symbol=normalized_symbol,
        timeframe=normalized_timeframe,
        begin_time=normalized_begin,
        end_time=normalized_end,
    )

    provider_name = provider.strip().lower()
    if provider_name == "auto":
        return _load_with_first_successful_provider(
            request,
            provider_names=("mt5",),
        )

    if provider_name not in _MARKET_DATA_PROVIDERS:
        available = ", ".join(["auto", *_MARKET_DATA_PROVIDERS.keys()])
        raise ValueError(
            f"Unsupported provider '{provider}'. Choose one of: {available}."
        )

    return _load_with_provider(request, _MARKET_DATA_PROVIDERS[provider_name])


def _load_with_first_successful_provider(
    request: MarketDataRequest,
    provider_names: tuple[str, ...],
) -> pd.DataFrame:
    errors: list[str] = []
    for provider_name in provider_names:
        provider = _MARKET_DATA_PROVIDERS[provider_name]
        try:
            return _load_with_provider(request, provider)
        except DataProviderError as exc:
            errors.append(f"{provider_name}: {exc}")
    raise DataProviderError("No market data provider succeeded. " + " | ".join(errors))


def _load_with_provider(
    request: MarketDataRequest,
    provider: MarketDataProvider,
) -> pd.DataFrame:
    raw_data = provider.load_data(request)
    try:
        data = normalize_market_data_frame(raw_data)
    except ValidationError as exc:
        raise DataProviderError(
            f"Provider '{provider.name}' returned invalid OHLCV data: {exc}"
        ) from exc
    if data.empty:
        raise DataProviderError(f"Provider '{provider.name}' returned no OHLCV rows.")
    return data


def _drop_incomplete_ohlcv_rows(data: pd.DataFrame) -> pd.DataFrame:
    """Drop provider rows with incomplete OHLC values.

    Public providers can include a current in-progress daily candle with blank
    OHLC values. Dropping those provider artifacts avoids marking an otherwise
    valid symbol as unavailable.
    """

    cleaned = data.copy()
    required_price_columns = ["open", "high", "low", "close"]
    for column in required_price_columns:
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")
    cleaned = cleaned.dropna(subset=required_price_columns)
    if "volume" in cleaned.columns:
        cleaned["volume"] = pd.to_numeric(
            cleaned["volume"],
            errors="coerce",
        ).fillna(0.0)
    return cleaned.reset_index(drop=True)
