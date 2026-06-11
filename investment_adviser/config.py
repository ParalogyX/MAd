"""Configuration constants for providers and analysis modules."""

from __future__ import annotations

from dataclasses import dataclass

SUPPORTED_TIMEFRAMES: dict[str, str] = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1D",
}

YFINANCE_INTERVALS: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "60m",
    "4h": "60m",
    "1d": "1d",
}

BINANCE_INTERVALS: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}

STOOQ_INTERVALS: dict[str, str] = {
    "1d": "d",
}

MT5_DEFAULT_HOST = "192.168.2.125"
MT5_DEFAULT_PORT = 8001
MT5_DEFAULT_MAX_BARS = 20_000

MT5_TIMEFRAME_ATTRIBUTES: dict[str, str] = {
    "1m": "TIMEFRAME_M1",
    "5m": "TIMEFRAME_M5",
    "15m": "TIMEFRAME_M15",
    "30m": "TIMEFRAME_M30",
    "1h": "TIMEFRAME_H1",
    "4h": "TIMEFRAME_H4",
    "1d": "TIMEFRAME_D1",
}

MT5_TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
}

LIBERTEX_PUBLIC_URLS: tuple[str, ...] = (
    "https://libertex.com/",
    "https://libertex.com/shares",
    "https://libertex.com/cryptocurrencies",
    "https://libertex.com/currency",
    "https://libertex.com/metals",
    "https://libertex.com/indices",
    "https://libertex.com/agriculture",
    "https://libertex.com/oil-gas",
    "https://libertex.com/etfs",
    "https://libertex.com/bonds",
    "https://libertex.com/options-trading",
)

REQUEST_TIMEOUT_SECONDS = 10.0
USER_AGENT = (
    "investment-adviser/0.1 "
    "(public market data research; no trading; contact: none)"
)


@dataclass(frozen=True)
class ProviderSettings:
    """Runtime settings shared by HTTP-based data providers."""

    timeout_seconds: float = REQUEST_TIMEOUT_SECONDS
    user_agent: str = USER_AGENT
