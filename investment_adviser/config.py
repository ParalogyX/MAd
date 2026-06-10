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
