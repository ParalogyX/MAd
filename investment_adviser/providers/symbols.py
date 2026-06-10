"""Provider-specific symbol alias helpers.

Libertex exposes CFD-style symbols and instrument labels. Public market data
providers usually need different identifiers, so aliases live in one module
instead of being mixed into provider network code.
"""

from __future__ import annotations

from collections.abc import Iterable

CURRENCY_CODES = {
    "AUD",
    "CAD",
    "CHF",
    "CLP",
    "CNH",
    "DKK",
    "EUR",
    "GBP",
    "JPY",
    "MXN",
    "NOK",
    "NZD",
    "PLN",
    "RUB",
    "SEK",
    "SGD",
    "TRY",
    "USD",
    "ZAR",
}

CRYPTO_ASSETS = {
    "AAVE",
    "ACT",
    "ADA",
    "ALGO",
    "ARB",
    "ARDR",
    "ARK",
    "ATOM",
    "AVAX",
    "AXS",
    "BCH",
    "BCN",
    "BNB",
    "BOME",
    "BONK",
    "BRETT",
    "BSV",
    "BTC",
    "BTG",
    "BTM",
    "BTS",
    "CAKE",
    "CHZ",
    "CMP",
    "DASH",
    "DGB",
    "DOGE",
    "DOT",
    "DRGN",
    "DSH",
    "EGLD",
    "ENJ",
    "EOS",
    "ETC",
    "ETH",
    "ETN",
    "FCT",
    "FIL",
    "FLOKI",
    "FUN",
    "GAS",
    "GIGA",
    "GOAT",
    "ICP",
    "ICX",
    "IOTA",
    "IOT",
    "KCS",
    "KMD",
    "KNC",
    "LEND",
    "LINK",
    "LRC",
    "LTC",
    "LUNA",
    "MATIC",
    "MELANIA",
    "MEME",
    "MEW",
    "MKR",
    "MOG",
    "MONA",
    "MOODENG",
    "NEIRO",
    "NEM",
    "NEO",
    "OMG",
    "ONT",
    "PIVX",
    "PNUT",
    "PONKE",
    "POPCAT",
    "PPT",
    "QTUM",
    "REP",
    "RUNE",
    "SALT",
    "SC",
    "SHIB",
    "SNT",
    "SNX",
    "SOL",
    "SPX",
    "STEEM",
    "SUSHI",
    "THETA",
    "TRUMP",
    "TRX",
    "TURBO",
    "UMA",
    "UNI",
    "USDT",
    "VERI",
    "VET",
    "WAVES",
    "WAX",
    "WIF",
    "XLM",
    "XMR",
    "XRP",
    "XTZ",
    "XVG",
    "YFI",
    "ZCL",
    "ZEC",
    "ZRX",
}

CRYPTO_QUOTES = ("USDT", "USD", "EUR", "BTC", "ETH", "BNB")

DISCOVERY_EXCLUDED_SYMBOLS = {
    "ACCOUNT",
    "CFD",
    "CFDS",
    "DEMO",
    "EURO",
    "GET",
    "HERE",
    "IN",
    "INC",
    "INVEST",
    "IS",
    "JP",
    "OFFICIAL",
    "PLN",
    "S.A",
    "SA",
    "SPA",
    "START",
    "STOCKS",
    "THE",
    "TOTAL",
    "TRADE",
    "U.S",
    "US",
    "USD",
    "XM",
    "XU",
}

YFINANCE_SYMBOL_ALIASES: dict[str, tuple[str, ...]] = {
    "A50": ("2823.HK", "XIN9.FGI"),
    "AEX": ("^AEX",),
    "AG": ("SI=F",),
    "ALCOA": ("AA",),
    "BASF": ("BAS.DE",),
    "BAYER": ("BAYN.DE",),
    "BNP": ("BNP.PA",),
    "BOLSA": ("BOLSAA.MX",),
    "BRN": ("BZ=F",),
    "CAC": ("^FCHI",),
    "CL": ("CL=F",),
    "COCOA": ("CC=F",),
    "COFFEE": ("KC=F",),
    "CORN": ("ZC=F",),
    "DAX": ("^GDAXI",),
    "ENEL": ("ENEL.MI",),
    "ENI": ("ENI.MI",),
    "ES": ("ES=F",),
    "FDAX": ("^GDAXI", "DAX=F"),
    "FTSE": ("^FTSE",),
    "HSI": ("^HSI",),
    "IBX": ("^IBEX",),
    "KLM": ("AF.PA",),
    "MEX": ("^MXX",),
    "MIB": ("FTSEMIB.MI",),
    "NASDAQ": ("^IXIC", "NQ=F"),
    "NG": ("NG=F",),
    "NKD": ("NKD=F",),
    "NQ": ("NQ=F",),
    "NVIDIA": ("NVDA",),
    "PA": ("PA=F",),
    "RUSSELL": ("^RUT",),
    "SAP": ("SAP.DE", "SAP"),
    "SOYBEAN": ("ZS=F",),
    "STOXX": ("^STOXX50E",),
    "SUGAR": ("SB=F",),
    "TA-35": ("TA35.TA",),
    "TUI": ("TUI1.DE",),
    "USDCLP": ("CLP=X",),
    "USDX": ("DX-Y.NYB",),
    "VINCI": ("DG.PA",),
    "VIX": ("^VIX",),
    "WHEAT": ("ZW=F",),
    "WT": ("CL=F",),
    "WTI": ("CL=F",),
    "XAGUSD": ("SI=F", "XAGUSD=X"),
    "XAUUSD": ("GC=F", "XAUUSD=X"),
    "YM": ("YM=F",),
}

STOOQ_SYMBOL_ALIASES: dict[str, tuple[str, ...]] = {
    "AEX": ("^aex",),
    "BASF": ("bas.de",),
    "BAYER": ("bayn.de",),
    "BNP": ("bnp.fr",),
    "CAC": ("^cac",),
    "DAX": ("^dax",),
    "ENEL": ("enel.it",),
    "ENI": ("eni.it",),
    "FTSE": ("^ftm",),
    "IBX": ("^ibex",),
    "MIB": ("^ftsemib",),
    "NASDAQ": ("^ndq",),
    "RUSSELL": ("^rut",),
    "SAP": ("sap.de", "sap.us"),
    "STOXX": ("^stoxx50e",),
    "TUI": ("tui1.de",),
    "USDX": ("dx.f",),
    "VINCI": ("dg.fr",),
    "VIX": ("^vix",),
}


def is_discovery_noise(symbol: str) -> bool:
    """Return whether a discovered token is likely page text, not an asset."""

    return normalize_provider_symbol(symbol) in DISCOVERY_EXCLUDED_SYMBOLS


def normalize_provider_symbol(symbol: str) -> str:
    """Normalize a symbol for alias lookups."""

    return symbol.strip().upper().replace("/", "")


def candidate_yfinance_symbols(symbol: str) -> list[str]:
    """Return yfinance symbols that may represent a Libertex instrument."""

    normalized = normalize_provider_symbol(symbol)
    alias_candidates: list[str] = []
    alias_candidates.extend(YFINANCE_SYMBOL_ALIASES.get(normalized, ()))

    forex_symbol = _as_forex_symbol(normalized)
    if forex_symbol:
        alias_candidates.append(forex_symbol)

    crypto_base, crypto_quote = _split_crypto_pair(normalized)
    if crypto_base:
        if crypto_quote:
            alias_candidates.append(f"{crypto_base}-{crypto_quote}")
        else:
            alias_candidates.append(f"{crypto_base}-USD")

    candidates = alias_candidates + [normalized] if alias_candidates else [normalized]
    return _unique(candidates)


def candidate_binance_symbols(symbol: str) -> list[str]:
    """Return Binance spot symbols that may represent a Libertex instrument."""

    normalized = normalize_provider_symbol(symbol)
    crypto_base, crypto_quote = _split_crypto_pair(normalized)
    candidates: list[str] = []

    if crypto_base and crypto_quote:
        if crypto_quote == "USD":
            candidates.append(f"{crypto_base}USDT")
        else:
            candidates.append(f"{crypto_base}{crypto_quote}")
    elif crypto_base:
        candidates.append(f"{crypto_base}USDT")

    return _unique(candidates)


def candidate_stooq_symbols(symbol: str) -> list[str]:
    """Return Stooq symbols that may represent a Libertex instrument."""

    normalized = normalize_provider_symbol(symbol)
    alias_candidates = list(STOOQ_SYMBOL_ALIASES.get(normalized, ()))

    forex_symbol = _as_forex_symbol(normalized)
    if forex_symbol:
        alias_candidates.append(normalized.lower())

    if _looks_like_plain_equity_symbol(normalized):
        alias_candidates.append(f"{normalized.lower()}.us")

    if alias_candidates:
        candidates = alias_candidates + [normalized.lower()]
    else:
        candidates = [normalized.lower()]
    return _unique(candidates)


def _as_forex_symbol(symbol: str) -> str | None:
    if len(symbol) != 6:
        return None
    base = symbol[:3]
    quote = symbol[3:]
    if base in CURRENCY_CODES and quote in CURRENCY_CODES:
        return f"{base}{quote}=X"
    return None


def _split_crypto_pair(symbol: str) -> tuple[str | None, str | None]:
    for quote in CRYPTO_QUOTES:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            base = symbol[: -len(quote)]
            if base in CRYPTO_ASSETS:
                normalized_quote = "USD" if quote == "USDT" else quote
                return base, normalized_quote
    if symbol in CRYPTO_ASSETS and symbol != "USDT":
        return symbol, None
    return None, None


def _looks_like_plain_equity_symbol(symbol: str) -> bool:
    return (
        symbol.isalpha()
        and 1 <= len(symbol) <= 5
        and symbol not in CURRENCY_CODES
        and symbol not in CRYPTO_ASSETS
    )


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        if value and value not in seen:
            unique_values.append(value)
            seen.add(value)
    return unique_values
