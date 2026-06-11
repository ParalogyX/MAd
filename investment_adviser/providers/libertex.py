"""Best-effort Libertex provider implementation.

Libertex does not publish a stable official public historical market data API
for this library's OHLCV use case. Instrument discovery is implemented by
reading public Libertex pages and falling back to a documented local snapshot.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd
import requests

from investment_adviser.config import LIBERTEX_PUBLIC_URLS, ProviderSettings
from investment_adviser.exceptions import DataProviderError
from investment_adviser.models import MarketDataRequest
from investment_adviser.providers.base import InstrumentProvider, MarketDataProvider
from investment_adviser.providers.mt5 import MT5InstrumentProvider
from investment_adviser.providers.symbols import is_discovery_noise
from investment_adviser.utils.logging import get_logger

LOGGER = get_logger(__name__)

_FALLBACK_FILE = Path(__file__).parent / "data" / "libertex_instruments_fallback.json"
_SYMBOL_PATTERN = re.compile(r"\b[A-Z][A-Z0-9./-]{1,14}\b")
_PAREN_SYMBOL_PATTERN = re.compile(r"\(([A-Z0-9./-]{1,15})\)")
_IGNORED_TOKENS = {
    "API",
    "CFD",
    "CFDS",
    "ETF",
    "FAQ",
    "HTML",
    "HTTP",
    "HTTPS",
    "IOS",
    "KID",
    "MT4",
    "MT5",
    "PDF",
    "RSS",
    "UK",
    "USA",
}


class _AnchorTextParser(HTMLParser):
    """Collect anchor text from a public Libertex HTML page."""

    def __init__(self) -> None:
        super().__init__()
        self._in_anchor = False
        self._parts: list[str] = []
        self.anchor_texts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.lower() == "a":
            self._in_anchor = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._in_anchor:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._in_anchor:
            text = " ".join(part.strip() for part in self._parts if part.strip())
            if text:
                self.anchor_texts.append(re.sub(r"\s+", " ", text))
            self._in_anchor = False
            self._parts = []


def _normalize_libertex_symbol(raw_symbol: str) -> str:
    symbol = raw_symbol.strip().upper().replace("/", "")
    symbol = re.sub(r"\s+", "", symbol)
    return symbol


def _looks_like_symbol(raw_symbol: str) -> bool:
    symbol = _normalize_libertex_symbol(raw_symbol)
    if symbol in _IGNORED_TOKENS or is_discovery_noise(symbol):
        return False
    if len(symbol) < 2 or len(symbol) > 15:
        return False
    if not re.search(r"[A-Z]", symbol):
        return False
    return bool(re.fullmatch(r"[A-Z0-9.-]+", symbol))


def _extract_symbol_candidates(text: str) -> set[str]:
    symbols: set[str] = set()
    for raw_symbol in _PAREN_SYMBOL_PATTERN.findall(text):
        if _looks_like_symbol(raw_symbol):
            symbols.add(_normalize_libertex_symbol(raw_symbol))
    for raw_symbol in _SYMBOL_PATTERN.findall(text):
        if _looks_like_symbol(raw_symbol):
            symbols.add(_normalize_libertex_symbol(raw_symbol))
    return symbols


class LibertexInstrumentProvider(InstrumentProvider):
    """Discover Libertex instruments from public pages with snapshot fallback."""

    def __init__(
        self,
        urls: tuple[str, ...] = LIBERTEX_PUBLIC_URLS,
        settings: ProviderSettings | None = None,
    ) -> None:
        self.urls = urls
        self.settings = settings or ProviderSettings()

    def find_instruments(self) -> list[str]:
        """Return sorted Libertex symbols from live discovery or fallback data."""

        try:
            live_symbols = self._discover_live_instruments()
        except requests.RequestException as exc:
            LOGGER.warning("Libertex live instrument discovery failed: %s", exc)
            live_symbols = []

        if live_symbols:
            normalized_live_symbols = sorted(
                {
                    _normalize_libertex_symbol(symbol)
                    for symbol in live_symbols
                    if _looks_like_symbol(symbol)
                }
            )
            if normalized_live_symbols:
                return normalized_live_symbols

        fallback_symbols = self._load_fallback_instruments()
        if fallback_symbols:
            LOGGER.warning(
                "Using fallback Libertex instrument snapshot from %s.",
                _FALLBACK_FILE.name,
            )
            return fallback_symbols

        raise DataProviderError(
            "Could not discover Libertex instruments from public pages and no "
            "documented fallback snapshot was available."
        )

    def _discover_live_instruments(self) -> list[str]:
        headers = {"User-Agent": self.settings.user_agent}
        symbols: set[str] = set()
        with requests.Session() as session:
            for url in self.urls:
                try:
                    response = session.get(
                        url,
                        headers=headers,
                        timeout=self.settings.timeout_seconds,
                    )
                    response.raise_for_status()
                except requests.RequestException as exc:
                    LOGGER.debug(
                        "Skipping Libertex public page %s after request failure: %s",
                        url,
                        exc,
                    )
                    continue
                parser = _AnchorTextParser()
                parser.feed(response.text)
                for anchor_text in parser.anchor_texts:
                    symbols.update(_extract_symbol_candidates(anchor_text))
        return sorted(symbols)

    def _load_fallback_instruments(self) -> list[str]:
        if not _FALLBACK_FILE.exists():
            return []
        with _FALLBACK_FILE.open("r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
        if payload.get("source_type") != "official_public_snapshot":
            raise DataProviderError(
                "Libertex fallback snapshot is not marked as official public "
                "snapshot data."
            )
        raw_symbols = payload.get("symbols", [])
        if not isinstance(raw_symbols, list):
            raise DataProviderError("Libertex fallback snapshot is malformed.")
        return sorted(
            {
                _normalize_libertex_symbol(str(symbol))
                for symbol in raw_symbols
                if _looks_like_symbol(str(symbol))
            }
        )


class LibertexMarketDataProvider(MarketDataProvider):
    """Placeholder market data provider for Libertex historical OHLCV data."""

    name = "libertex"

    def load_data(self, request: MarketDataRequest) -> pd.DataFrame:
        """Raise because Libertex has no documented public OHLCV API here."""

        raise DataProviderError(
            "Libertex does not provide a documented public historical OHLCV API "
            "suitable for this library. Use provider='fallback' with optional "
            "yfinance support, or provider='mock' for deterministic tests."
        )


def find_libertex_instruments() -> list[str]:
    """Find all tradable instruments from the configured MT5 account.

    The public function name is kept for backward compatibility with existing
    notebooks and scripts. Market discovery now uses MT5 as the authoritative
    source instead of public Libertex pages.
    """

    return MT5InstrumentProvider().find_instruments()
