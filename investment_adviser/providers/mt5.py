"""MetaTrader 5 market data provider using an mt5linux bridge server."""

from __future__ import annotations

import os
import re
import weakref
from datetime import datetime
from typing import Any, Callable

import pandas as pd

from investment_adviser.config import (
    MT5_DEFAULT_HOST,
    MT5_DEFAULT_MAX_BARS,
    MT5_DEFAULT_PORT,
    MT5_TIMEFRAME_ATTRIBUTES,
    MT5_TIMEFRAME_SECONDS,
)
from investment_adviser.exceptions import DataProviderError
from investment_adviser.models import MarketDataRequest
from investment_adviser.providers.base import InstrumentProvider, MarketDataProvider

MT5ClientFactory = Callable[[], Any]
_MT5_PROVIDER_INSTANCES: weakref.WeakSet["MT5BaseProvider"] = weakref.WeakSet()


class MT5BaseProvider:
    """Shared connection management for MT5 providers."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        max_bars: int | None = None,
        client_factory: MT5ClientFactory | None = None,
    ) -> None:
        self.host = host or os.getenv("MT5_HOST", MT5_DEFAULT_HOST)
        self.port = port if port is not None else _env_int("MT5_PORT", MT5_DEFAULT_PORT)
        self.max_bars = (
            max_bars
            if max_bars is not None
            else _env_int("MT5_MAX_BARS", MT5_DEFAULT_MAX_BARS)
        )
        self._client_factory = client_factory
        self._client: Any | None = None
        _MT5_PROVIDER_INSTANCES.add(self)

    def _get_client(self) -> Any:
        """Return an initialized mt5linux client."""

        if self._client is not None:
            return self._client

        if self._client_factory is not None:
            client = self._client_factory()
        else:
            try:
                from mt5linux import MetaTrader5
            except ImportError as exc:
                raise DataProviderError(
                    "MT5 data access requires the mt5linux package. Install "
                    "project dependencies and make sure the MT5 bridge is running."
                ) from exc
            client = MetaTrader5(host=self.host, port=self.port)

        if not _initialize_client(client):
            raise DataProviderError(
                "Could not initialize MT5 connection "
                f"at {self.host}:{self.port}. Last error: {_last_error(client)}"
            )

        self._client = client
        return client

    def configure_connection(
        self,
        host: str | None = None,
        port: int | None = None,
        max_bars: int | None = None,
    ) -> None:
        """Update MT5 connection settings and reset any existing client."""

        changed = False
        if host and host != self.host:
            self.host = host
            changed = True
        if port is not None and port != self.port:
            self.port = port
            changed = True
        if max_bars is not None and max_bars != self.max_bars:
            self.max_bars = max_bars
            changed = True
        if changed:
            self._client = None


def configure_mt5_connection(
    host: str | None = None,
    port: int | None = None,
    max_bars: int | None = None,
) -> None:
    """Configure all existing and future MT5 provider instances."""

    if host:
        os.environ["MT5_HOST"] = host
    if port is not None:
        os.environ["MT5_PORT"] = str(port)
    if max_bars is not None:
        os.environ["MT5_MAX_BARS"] = str(max_bars)

    for provider in list(_MT5_PROVIDER_INSTANCES):
        provider.configure_connection(host=host, port=port, max_bars=max_bars)


class MT5InstrumentProvider(MT5BaseProvider, InstrumentProvider):
    """Discover tradable instruments from MetaTrader 5."""

    def find_instruments(self) -> list[str]:
        """Return sorted tradable MT5 symbols."""

        client = self._get_client()
        symbols = client.symbols_get()
        if not symbols:
            raise DataProviderError(
                "MT5 returned no symbols. Last error: " f"{_last_error(client)}"
            )

        unique_symbols: dict[str, str] = {}
        for symbol_info in symbols:
            name = _symbol_name(symbol_info)
            if not name or not _is_tradable(symbol_info):
                continue
            unique_symbols[_symbol_key(name)] = name

        if not unique_symbols:
            raise DataProviderError("MT5 returned symbols, but none were tradable.")

        return sorted(unique_symbols.values(), key=lambda value: value.upper())

    def find_instrument_metadata(self) -> list[dict[str, Any]]:
        """Return tradable MT5 symbols with best-effort metadata."""

        client = self._get_client()
        symbols = client.symbols_get()
        if not symbols:
            raise DataProviderError(
                "MT5 returned no symbols. Last error: " f"{_last_error(client)}"
            )

        metadata_rows: list[dict[str, Any]] = []
        for symbol_info in symbols:
            name = _symbol_name(symbol_info)
            if not name or not _is_tradable(symbol_info):
                continue
            metadata = _symbol_info_to_dict(symbol_info)
            if hasattr(client, "symbol_select"):
                try:
                    client.symbol_select(name, True)
                except Exception:
                    pass
            if hasattr(client, "symbol_info"):
                try:
                    selected_info = client.symbol_info(name)
                    if selected_info is not None:
                        metadata.update(_symbol_info_to_dict(selected_info))
                except Exception:
                    pass
            metadata["name"] = name
            metadata_rows.append(metadata)

        if not metadata_rows:
            raise DataProviderError("MT5 returned symbols, but none were tradable.")
        return metadata_rows


class MT5MarketDataProvider(MT5BaseProvider, MarketDataProvider):
    """Load historical OHLCV candles from MetaTrader 5."""

    name = "mt5"

    def load_data(self, request: MarketDataRequest) -> pd.DataFrame:
        """Return normalized OHLCV candles for the requested MT5 symbol."""

        client = self._get_client()
        symbol_name = self._resolve_symbol_name(client, request.symbol)
        self._select_symbol(client, symbol_name)
        timeframe = self._mt5_timeframe(client, request.timeframe)

        rates = self._copy_rates_range(
            client=client,
            symbol=symbol_name,
            timeframe=timeframe,
            begin_time=request.begin_time,
            end_time=request.end_time,
            timeframe_name=request.timeframe,
        )
        data = _rates_to_frame(rates, request.begin_time, request.end_time)
        if data.empty:
            raise DataProviderError(
                f"MT5 returned no OHLCV rows for {symbol_name} "
                f"from {request.begin_time} to {request.end_time}."
            )
        return data

    def get_current_price(self, symbol: str, side: str | None = None) -> float:
        """Return the latest usable MT5 tick price for a symbol.

        Args:
            symbol: MT5 symbol name or normalized symbol alias.
            side: Optional trade side. For ``buy`` the ask price is preferred;
                for ``sell`` the bid price is preferred. Without a side, the
                latest trade price is preferred, then the bid/ask midpoint.

        Raises:
            DataProviderError: If MT5 cannot resolve the symbol or returns no
                usable bid, ask, or last price.
        """

        client = self._get_client()
        symbol_name = self._resolve_symbol_name(client, symbol)
        self._select_symbol(client, symbol_name)
        if not hasattr(client, "symbol_info_tick"):
            raise DataProviderError("MT5 client does not expose symbol_info_tick().")

        tick = client.symbol_info_tick(symbol_name)
        if tick is None:
            raise DataProviderError(
                f"MT5 returned no tick for {symbol_name}. "
                f"Last error: {_last_error(client)}"
            )

        price = _select_tick_price(_symbol_info_to_dict(tick), side)
        if price is None:
            raise DataProviderError(
                f"MT5 tick for {symbol_name} has no positive bid, ask, or last price."
            )
        return price

    def _resolve_symbol_name(self, client: Any, requested_symbol: str) -> str:
        symbols = client.symbols_get()
        if not symbols:
            raise DataProviderError(
                "MT5 returned no symbols while resolving "
                f"{requested_symbol}. Last error: {_last_error(client)}"
            )

        requested_key = _symbol_key(requested_symbol)
        names = [_symbol_name(symbol_info) for symbol_info in symbols]
        names = [name for name in names if name]

        for name in names:
            if name == requested_symbol:
                return name
        for name in names:
            if name.upper() == requested_symbol.upper():
                return name
        for name in names:
            if _symbol_key(name) == requested_key:
                return name
        for name in names:
            if _symbol_key(name).startswith(requested_key):
                return name

        raise DataProviderError(f"Symbol {requested_symbol} was not found in MT5.")

    @staticmethod
    def _select_symbol(client: Any, symbol_name: str) -> None:
        if not hasattr(client, "symbol_select"):
            return
        selected = client.symbol_select(symbol_name, True)
        if selected is False:
            raise DataProviderError(
                f"MT5 could not select symbol {symbol_name}. "
                f"Last error: {_last_error(client)}"
            )

    @staticmethod
    def _mt5_timeframe(client: Any, timeframe: str) -> Any:
        attribute_name = MT5_TIMEFRAME_ATTRIBUTES[timeframe]
        if not hasattr(client, attribute_name):
            raise DataProviderError(
                f"MT5 client does not expose {attribute_name}."
            )
        return getattr(client, attribute_name)

    def _copy_rates_range(
        self,
        client: Any,
        symbol: str,
        timeframe: Any,
        begin_time: datetime,
        end_time: datetime,
        timeframe_name: str,
    ) -> Any:
        if hasattr(client, "copy_rates_range"):
            rates = client.copy_rates_range(symbol, timeframe, begin_time, end_time)
            if not _rates_empty(rates):
                return rates

        count = self._estimated_bar_count(begin_time, end_time, timeframe_name)
        if hasattr(client, "copy_rates_from"):
            rates = client.copy_rates_from(symbol, timeframe, end_time, count)
            if not _rates_empty(rates):
                return rates

        if hasattr(client, "copy_rates_from_pos"):
            rates = client.copy_rates_from_pos(symbol, timeframe, 0, count)
            if not _rates_empty(rates):
                return rates

        raise DataProviderError(
            f"MT5 returned no rates for {symbol}. Last error: {_last_error(client)}"
        )

    def _estimated_bar_count(
        self,
        begin_time: datetime,
        end_time: datetime,
        timeframe: str,
    ) -> int:
        timeframe_seconds = MT5_TIMEFRAME_SECONDS[timeframe]
        span_seconds = max(1.0, (end_time - begin_time).total_seconds())
        estimated = int(span_seconds / timeframe_seconds) + 10
        return max(1, min(self.max_bars, estimated))


def _initialize_client(client: Any) -> bool:
    kwargs: dict[str, Any] = {}
    login = os.getenv("MT5_LOGIN")
    password = os.getenv("MT5_PASSWORD")
    server = os.getenv("MT5_SERVER")
    if login:
        kwargs["login"] = int(login)
    if password:
        kwargs["password"] = password
    if server:
        kwargs["server"] = server

    try:
        return bool(client.initialize(**kwargs))
    except TypeError:
        return bool(client.initialize())


def _rates_to_frame(
    rates: Any,
    begin_time: datetime,
    end_time: datetime,
) -> pd.DataFrame:
    raw = pd.DataFrame(rates)
    if raw.empty:
        return _empty_market_data_frame()
    if "time" not in raw.columns:
        raise DataProviderError("MT5 rates do not contain a 'time' column.")

    data = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(raw["time"], unit="s", utc=True),
            "open": raw["open"],
            "high": raw["high"],
            "low": raw["low"],
            "close": raw["close"],
            "volume": _volume_series(raw),
        }
    )
    data = data[(data["timestamp"] >= begin_time) & (data["timestamp"] < end_time)]
    data = data.dropna(subset=["open", "high", "low", "close"])
    return data.reset_index(drop=True)


def _volume_series(raw: pd.DataFrame) -> pd.Series:
    if "real_volume" in raw.columns:
        real_volume = pd.to_numeric(raw["real_volume"], errors="coerce").fillna(0.0)
        if real_volume.sum() > 0:
            return real_volume
    if "tick_volume" in raw.columns:
        return pd.to_numeric(raw["tick_volume"], errors="coerce").fillna(0.0)
    return pd.Series([0.0] * len(raw), index=raw.index)


def _empty_market_data_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])


def _symbol_name(symbol_info: Any) -> str:
    if isinstance(symbol_info, dict):
        return str(symbol_info.get("name", "")).strip()
    return str(getattr(symbol_info, "name", "")).strip()


def _symbol_info_to_dict(symbol_info: Any) -> dict[str, Any]:
    if symbol_info is None:
        return {}
    if isinstance(symbol_info, dict):
        return dict(symbol_info)
    if hasattr(symbol_info, "_asdict"):
        return dict(symbol_info._asdict())

    values: dict[str, Any] = {}
    for name in dir(symbol_info):
        if name.startswith("_"):
            continue
        try:
            value = getattr(symbol_info, name)
        except Exception:
            continue
        if callable(value):
            continue
        if isinstance(value, (str, int, float, bool, type(None))):
                values[name] = value
    return values


def _select_tick_price(tick: dict[str, Any], side: str | None = None) -> float | None:
    bid = _positive_float(tick.get("bid"))
    ask = _positive_float(tick.get("ask"))
    last = _positive_float(tick.get("last"))
    midpoint = (bid + ask) / 2.0 if bid is not None and ask is not None else None
    normalized_side = str(side or "").strip().lower()

    if normalized_side in {"buy", "long"}:
        candidates = (ask, last, midpoint, bid)
    elif normalized_side in {"sell", "short"}:
        candidates = (bid, last, midpoint, ask)
    else:
        candidates = (last, midpoint, bid, ask)

    for candidate in candidates:
        if candidate is not None and candidate > 0:
            return float(candidate)
    return None


def _positive_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(numeric) or numeric <= 0:
        return None
    return numeric


def _is_tradable(symbol_info: Any) -> bool:
    if isinstance(symbol_info, dict):
        trade_mode = symbol_info.get("trade_mode")
    else:
        trade_mode = getattr(symbol_info, "trade_mode", None)
    if trade_mode is None:
        return True
    try:
        return int(trade_mode) != 0
    except (TypeError, ValueError):
        return True


def _symbol_key(symbol: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", symbol.upper())


def _rates_empty(rates: Any) -> bool:
    if rates is None:
        return True
    try:
        return len(rates) == 0
    except TypeError:
        return False


def _last_error(client: Any) -> Any:
    if not hasattr(client, "last_error"):
        return "not available"
    try:
        return client.last_error()
    except Exception:
        return "not available"


def _env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default
