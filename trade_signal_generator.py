"""Session-aware scheduled signal and trade-plan generation.

This script analyses symbols and writes CSV files only. It does not place
orders, connect to broker execution APIs, size positions, or submit trades.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import queue
import re
import threading
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from csv_analysis import get_current_price
from find_signal import (
    OUTPUT_COLUMNS,
    calculate_daily_sl_tp,
    process_symbol,
    smart_round_price,
)
from investment_adviser.providers.mt5 import MT5InstrumentProvider

PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR_ENV_VAR = "M_AD_OUTPUT_DIR"
OUTPUT_DIR = Path(os.getenv(OUTPUT_DIR_ENV_VAR, str(PROJECT_ROOT))).expanduser()
SESSION_RULES_FILE = "session_rules.json"
TICKER_TRADING_TIMES_FILE = "ticker_trading_times.csv"
TRIGGER_COMMAND = "now"
UPDATE_COMMAND = "update"
RELOAD_COMMAND = "reload"
STATUS_COMMAND = "status"
QUIT_COMMAND = "quit"
LEGACY_EXIT_COMMAND = "stop"

DEFAULT_TIMEZONE = "Europe/Amsterdam"
METADATA_COLUMNS = [
    "ticker",
    "description",
    "start_trade_time",
    "end_trade_time",
    "trading_days",
    "ticker_type",
    "session_group",
    "last_updated_utc",
]
TRADE_PLAN_COLUMNS = [
    "Ticker name",
    "current price",
    "direction of trading",
    "Stop Loss level",
    "Take Profit level",
    "ticker",
    "description",
    "session_group",
    "entry_time_local",
    "close_time_local",
    "entry_price",
    "direction",
    "signal_strength",
    "stop_loss",
    "take_profit",
    "risk_reward_ratio",
    "analysis_price",
    "price_drift_percent",
    "entry_validation_result",
    "reason",
]

DEFAULT_SESSION_RULES: dict[str, Any] = {
    "timezone": DEFAULT_TIMEZONE,
    "best_signal_limit": 10,
    "entry_check_minutes_before_open": 0,
    "rules_reload_interval_seconds": 60,
    "session_groups": {
        "crypto_24_7": {
            "enabled": True,
            "analysis_time": "14:45",
            "open_time": "15:05",
            "close_time": "21:45",
            "trading_days": "mon-sun",
            "sl_multiplier": 0.40,
            "tp_base_multiplier": 0.50,
            "tp_strength_multiplier": 0.20,
        },
        "forex_major": {
            "enabled": True,
            "analysis_time": "08:45",
            "open_time": "09:05",
            "close_time": "21:45",
            "trading_days": "mon-fri",
            "sl_multiplier": 0.45,
            "tp_base_multiplier": 0.60,
            "tp_strength_multiplier": 0.25,
        },
        "forex_exotic": {
            "enabled": True,
            "analysis_time": "08:45",
            "open_time": "09:15",
            "close_time": "18:30",
            "trading_days": "mon-fri",
            "sl_multiplier": 0.45,
            "tp_base_multiplier": 0.55,
            "tp_strength_multiplier": 0.20,
        },
        "europe_stock_index": {
            "enabled": True,
            "analysis_time": "08:40",
            "open_time": "09:10",
            "close_time": "17:20",
            "trading_days": "mon-fri",
            "sl_multiplier": 0.40,
            "tp_base_multiplier": 0.50,
            "tp_strength_multiplier": 0.20,
        },
        "us_stock_index": {
            "enabled": True,
            "analysis_time": "15:00",
            "open_time": "15:45",
            "close_time": "21:45",
            "trading_days": "mon-fri",
            "sl_multiplier": 0.40,
            "tp_base_multiplier": 0.50,
            "tp_strength_multiplier": 0.20,
        },
        "commodity_us": {
            "enabled": True,
            "analysis_time": "15:00",
            "open_time": "15:45",
            "close_time": "21:45",
            "trading_days": "mon-fri",
            "sl_multiplier": 0.40,
            "tp_base_multiplier": 0.50,
            "tp_strength_multiplier": 0.20,
        },
        "unknown": {
            "enabled": False,
            "analysis_time": "08:45",
            "open_time": "09:05",
            "close_time": "21:45",
            "trading_days": "mon-fri",
            "sl_multiplier": 0.45,
            "tp_base_multiplier": 0.60,
            "tp_strength_multiplier": 0.25,
        },
    },
}

DAY_TO_INDEX = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}
FOREX_MAJOR_SYMBOLS = {
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "USDCAD",
    "AUDUSD",
    "NZDUSD",
}
FIAT_CODES = {
    "AUD",
    "CAD",
    "CHF",
    "CNH",
    "EUR",
    "GBP",
    "HKD",
    "JPY",
    "MXN",
    "NOK",
    "NZD",
    "PLN",
    "SEK",
    "SGD",
    "TRY",
    "USD",
    "ZAR",
}
CRYPTO_TOKENS = {
    "ADA",
    "AVAX",
    "BNB",
    "BTC",
    "DOGE",
    "DOT",
    "ETH",
    "LINK",
    "LTC",
    "MATIC",
    "SHIB",
    "SOL",
    "TRX",
    "XLM",
    "XRP",
}
COMMODITY_HINTS = {
    "BRENT",
    "COPPER",
    "GOLD",
    "NATGAS",
    "OIL",
    "SILVER",
    "WTI",
    "XAG",
    "XAU",
}
US_HINTS = {
    "AAPL",
    "AMZN",
    "DJI",
    "META",
    "NASDAQ",
    "NAS100",
    "NDX",
    "NFLX",
    "NVDA",
    "NYSE",
    "SPX",
    "TSLA",
    "US100",
    "US30",
    "US500",
}
EUROPE_HINTS = {
    "CAC",
    "DAX",
    "DE40",
    "EU50",
    "EURONEXT",
    "FRA40",
    "FTSE",
    "GER40",
    "IBEX",
    "STOXX",
    "UK100",
}
GROUP_DEFAULT_WINDOWS = {
    "crypto_24_7": ("", "", "mon-sun"),
    "forex_major": ("", "", "mon-fri"),
    "forex_exotic": ("", "", "mon-fri"),
    "europe_stock_index": ("09:00", "17:30", "mon-fri"),
    "us_stock_index": ("15:30", "22:00", "mon-fri"),
    "commodity_us": ("15:00", "21:45", "mon-fri"),
    "unknown": ("", "", "unknown"),
}


def console_listener(commands: queue.Queue[str]) -> None:
    """Read console input without keeping the main scheduler busy."""

    while True:
        try:
            command = input().strip()
        except EOFError:
            return
        commands.put(command)


def session_rules_path() -> Path:
    """Return the editable session rules path."""

    return OUTPUT_DIR / SESSION_RULES_FILE


def ticker_trading_times_path() -> Path:
    """Return the ticker trading metadata CSV path."""

    return OUTPUT_DIR / TICKER_TRADING_TIMES_FILE


def ensure_session_rules_file(path: Path | None = None) -> Path:
    """Create session_rules.json with defaults when it is missing."""

    rules_path = path or session_rules_path()
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    if not rules_path.exists():
        rules_path.write_text(
            json.dumps(DEFAULT_SESSION_RULES, indent=2),
            encoding="utf-8",
        )
    return rules_path


def load_session_rules(
    path: Path | None = None,
    previous_rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load editable session rules, keeping the last valid rules on JSON error."""

    rules_path = ensure_session_rules_file(path)
    try:
        raw_rules = json.loads(rules_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        if previous_rules is not None:
            print(
                f"WARNING: invalid {rules_path.name}; keeping last valid rules: {exc}",
                flush=True,
            )
            return previous_rules
        print(
            f"WARNING: invalid {rules_path.name}; using built-in defaults: {exc}",
            flush=True,
        )
        return copy.deepcopy(DEFAULT_SESSION_RULES)

    return merge_session_rules(raw_rules)


def merge_session_rules(raw_rules: dict[str, Any]) -> dict[str, Any]:
    """Merge user rules over defaults so missing keys stay safe."""

    merged = copy.deepcopy(DEFAULT_SESSION_RULES)
    if not isinstance(raw_rules, dict):
        return merged

    for key in [
        "timezone",
        "best_signal_limit",
        "entry_check_minutes_before_open",
        "rules_reload_interval_seconds",
    ]:
        if key in raw_rules:
            merged[key] = raw_rules[key]

    raw_groups = raw_rules.get("session_groups")
    if isinstance(raw_groups, dict):
        for group_name, group_rule in raw_groups.items():
            if not isinstance(group_rule, dict):
                continue
            base_rule = copy.deepcopy(
                merged["session_groups"].get(
                    group_name,
                    DEFAULT_SESSION_RULES["session_groups"]["unknown"],
                )
            )
            base_rule.update(group_rule)
            merged["session_groups"][group_name] = base_rule
    return merged


def rules_mtime(path: Path | None = None) -> float | None:
    """Return the session rule file mtime, or None if it does not exist."""

    rules_path = path or session_rules_path()
    if not rules_path.exists():
        return None
    return rules_path.stat().st_mtime


def parse_trading_days(value: str) -> set[int]:
    """Parse mon-fri, mon-sun, or mon,wed,fri into weekday indexes."""

    normalized = str(value).strip().lower()
    if not normalized or normalized == "unknown":
        return set()

    selected: set[int] = set()
    for part in normalized.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = [item.strip()[:3] for item in token.split("-", 1)]
            if start_text not in DAY_TO_INDEX or end_text not in DAY_TO_INDEX:
                continue
            start = DAY_TO_INDEX[start_text]
            end = DAY_TO_INDEX[end_text]
            if start <= end:
                selected.update(range(start, end + 1))
            else:
                selected.update(range(start, 7))
                selected.update(range(0, end + 1))
        else:
            day_text = token[:3]
            if day_text in DAY_TO_INDEX:
                selected.add(DAY_TO_INDEX[day_text])
    return selected


def is_trading_day(trading_days: str, local_day: date) -> bool:
    """Return True if local_day is allowed by the rule's trading_days."""

    allowed_days = parse_trading_days(trading_days)
    return local_day.weekday() in allowed_days


def update_ticker_trading_times() -> pd.DataFrame:
    """Discover MT5 instruments, classify sessions, save CSV, and return it."""

    print("Updating ticker trading metadata from MT5...", flush=True)
    print("Connecting to MT5 and requesting symbol metadata...", flush=True)
    provider = MT5InstrumentProvider()
    metadata_rows = provider.find_instrument_metadata()
    print(f"Received metadata for {len(metadata_rows)} tradable MT5 symbols.", flush=True)
    print("Classifying tickers into session groups...", flush=True)
    data = build_ticker_trading_times(metadata_rows)
    output_path = ticker_trading_times_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(output_path, index=False)
    group_counts = data["session_group"].value_counts().to_dict() if not data.empty else {}
    print(f"Saved {output_path.name} with {len(data)} tickers.", flush=True)
    print(f"Ticker session groups: {group_counts}", flush=True)
    return data


def build_ticker_trading_times(
    metadata_rows: list[dict[str, Any]],
    timestamp_utc: datetime | None = None,
) -> pd.DataFrame:
    """Build ticker trading metadata from provider metadata rows."""

    updated_at = timestamp_utc or datetime.now(timezone.utc)
    output_rows: list[dict[str, Any]] = []
    for metadata in metadata_rows:
        symbol = str(metadata.get("name") or metadata.get("ticker") or "").strip()
        if not symbol:
            continue
        description = str(
            metadata.get("description")
            or metadata.get("path")
            or metadata.get("category")
            or ""
        ).strip()
        start_time, end_time, trading_days = detect_trading_window(metadata)
        classified_metadata = {
            **metadata,
            "start_trade_time": start_time,
            "end_trade_time": end_time,
        }
        ticker_type, session_group = classify_ticker(
            symbol,
            description,
            classified_metadata,
        )
        if not start_time and not end_time:
            start_time, end_time, trading_days = GROUP_DEFAULT_WINDOWS.get(
                session_group,
                GROUP_DEFAULT_WINDOWS["unknown"],
            )

        output_rows.append(
            {
                "ticker": symbol,
                "description": description,
                "start_trade_time": start_time,
                "end_trade_time": end_time,
                "trading_days": trading_days,
                "ticker_type": ticker_type,
                "session_group": session_group,
                "last_updated_utc": updated_at.astimezone(timezone.utc).isoformat(),
            }
        )

    data = pd.DataFrame(output_rows, columns=METADATA_COLUMNS)
    if data.empty:
        return data
    return (
        data.drop_duplicates(subset=["ticker"], keep="last")
        .sort_values("ticker", key=lambda column: column.str.upper())
        .reset_index(drop=True)
    )


def detect_trading_window(metadata: dict[str, Any]) -> tuple[str, str, str]:
    """Return best-effort start/end/trading-days from metadata if available."""

    start = _normalize_hhmm(
        metadata.get("start_trade_time")
        or metadata.get("session_start")
        or metadata.get("trade_start")
    )
    end = _normalize_hhmm(
        metadata.get("end_trade_time")
        or metadata.get("session_end")
        or metadata.get("trade_end")
    )
    days = str(metadata.get("trading_days") or "").strip().lower()
    if start or end:
        return start, end, days or "mon-fri"
    return "", "", days or "unknown"


def classify_ticker(
    symbol: str,
    description: str,
    metadata: dict[str, Any],
) -> tuple[str, str]:
    """Classify a ticker into a ticker_type and scheduler session_group."""

    symbol_key = _symbol_key(symbol)
    searchable = f"{symbol_key} {description} {metadata.get('path', '')}".upper()
    start = str(metadata.get("start_trade_time") or "")
    end = str(metadata.get("end_trade_time") or "")

    if _looks_like_us_session(start, end):
        return "us_stock", "us_stock_index"
    if _looks_like_europe_session(start, end):
        return "europe_stock", "europe_stock_index"

    if any(token in searchable for token in CRYPTO_TOKENS):
        if symbol_key.endswith("USD") or "CRYPTO" in searchable:
            return "crypto", "crypto_24_7"
    if "CRYPTO" in searchable:
        return "crypto", "crypto_24_7"

    if any(token in searchable for token in COMMODITY_HINTS):
        return "commodity", "commodity_us"

    if symbol_key in FOREX_MAJOR_SYMBOLS:
        return "forex", "forex_major"
    if _looks_like_forex_pair(symbol_key):
        return "forex", "forex_exotic"

    if any(token in searchable for token in US_HINTS):
        ticker_type = "us_index" if _looks_like_index(searchable) else "us_stock"
        return ticker_type, "us_stock_index"

    if any(token in searchable for token in EUROPE_HINTS):
        ticker_type = (
            "europe_index" if _looks_like_index(searchable) else "europe_stock"
        )
        return ticker_type, "europe_stock_index"

    return "unknown", "unknown"


def is_entry_price_still_valid(
    analysis_price: float,
    entry_price: float,
    atr_percent_1d: float,
    direction: str,
) -> tuple[bool, str, float, float]:
    """Check whether entry price is still close enough to analysis price."""

    if analysis_price <= 0 or entry_price <= 0:
        return False, "Invalid price", 0.0, 0.0

    if atr_percent_1d <= 0:
        return False, "Invalid ATR percent", 0.0, 0.0

    normalized_direction = direction.lower().strip()
    if normalized_direction not in {"buy", "sell"}:
        return False, "Invalid direction", 0.0, 0.0

    price_drift = abs(entry_price - analysis_price) / analysis_price
    moved_in_signal_direction = (
        (normalized_direction == "buy" and entry_price > analysis_price)
        or (normalized_direction == "sell" and entry_price < analysis_price)
    )

    if moved_in_signal_direction:
        max_allowed_drift = min(0.006, 0.12 * atr_percent_1d)
        drift_type = "in signal direction"
    else:
        max_allowed_drift = min(0.010, 0.20 * atr_percent_1d)
        drift_type = "against signal direction"

    if price_drift > max_allowed_drift:
        return (
            False,
            f"Skipped: price moved {price_drift:.2%} {drift_type} "
            f"from analysis price, limit is {max_allowed_drift:.2%}",
            price_drift,
            max_allowed_drift,
        )

    return (
        True,
        f"Entry valid: price moved {price_drift:.2%} {drift_type}, "
        f"limit is {max_allowed_drift:.2%}",
        price_drift,
        max_allowed_drift,
    )


def analyze_group_tickers(
    tickers: list[str],
    processor: Callable[[str], dict[str, Any]] = process_symbol,
) -> list[dict[str, Any]]:
    """Analyse tickers without allowing one failure to stop the group."""

    rows: list[dict[str, Any]] = []
    total = len(tickers)
    for index, ticker in enumerate(tickers, start=1):
        print(f"[{index}/{total}] Processing {ticker}...", flush=True)
        try:
            rows.append(processor(ticker))
        except Exception as exc:
            rows.append(_analysis_error_row(ticker, exc))
    return rows


def run_analysis_for_group(
    session_group: str,
    rules: dict[str, Any],
    ticker_metadata: pd.DataFrame,
    now: datetime | None = None,
    processor: Callable[[str], dict[str, Any]] = process_symbol,
) -> Path:
    """Run signal analysis for one session group and write best-signals CSV."""

    timestamp = now or datetime.now(timezone.utc)
    timezone_info = rules_timezone(rules)
    local_timestamp = timestamp.astimezone(timezone_info)
    group_rule = rules["session_groups"][session_group]
    tickers = tickers_for_group(ticker_metadata, session_group)
    rows = analyze_group_tickers(tickers, processor=processor)
    result = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    if result.empty:
        result = pd.DataFrame(columns=[*OUTPUT_COLUMNS, "analysis_price"])

    tradable = result[
        result.get("direction", pd.Series(dtype=str))
        .astype(str)
        .str.lower()
        .isin({"buy", "sell"})
    ].copy()
    if "signal_strength" in tradable.columns:
        tradable["signal_strength"] = pd.to_numeric(
            tradable["signal_strength"],
            errors="coerce",
        )
        tradable = tradable.dropna(subset=["signal_strength"]).sort_values(
            "signal_strength",
            ascending=False,
            kind="mergesort",
        )

    best_limit = int(rules.get("best_signal_limit", 10))
    tradable = tradable.head(best_limit)
    tradable["analysis_price"] = tradable.get("current_price")
    tradable["session_group"] = session_group
    tradable = add_metadata_columns(tradable, ticker_metadata)

    output_path = candidate_output_path(session_group, local_timestamp)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tradable.to_csv(output_path, index=False)
    print(
        f"Saved {len(tradable)} candidates for {session_group} to {output_path.name}.",
        flush=True,
    )
    return output_path


def run_analysis_for_all_enabled_groups(
    rules: dict[str, Any],
    ticker_metadata: pd.DataFrame,
    now: datetime | None = None,
) -> list[Path]:
    """Run analysis immediately for all enabled session groups."""

    output_paths: list[Path] = []
    for group_name, group_rule in rules.get("session_groups", {}).items():
        if not group_rule.get("enabled", False):
            continue
        output_paths.append(
            run_analysis_for_group(group_name, rules, ticker_metadata, now=now)
        )
    return output_paths


def run_trade_plan_for_group(
    session_group: str,
    rules: dict[str, Any],
    ticker_metadata: pd.DataFrame,
    now: datetime | None = None,
    price_loader: Callable[[str], float | None] = get_current_price,
) -> Path:
    """Generate the final trade-plan CSV for one session group."""

    timestamp = now or datetime.now(timezone.utc)
    timezone_info = rules_timezone(rules)
    local_timestamp = timestamp.astimezone(timezone_info)
    candidate_path = find_latest_candidate_file(session_group, local_timestamp.date())
    candidates = pd.read_csv(candidate_path)
    group_rule = rules["session_groups"][session_group]
    rows = build_trade_plan_rows(
        candidates=candidates,
        ticker_metadata=ticker_metadata,
        session_group=session_group,
        group_rule=group_rule,
        local_timestamp=local_timestamp,
        price_loader=price_loader,
    )
    output_path = trade_plan_output_path(session_group, local_timestamp)
    pd.DataFrame(rows, columns=TRADE_PLAN_COLUMNS).to_csv(output_path, index=False)
    print(
        f"Saved {len(rows)} trade-plan rows for {session_group} "
        f"to {output_path.name}.",
        flush=True,
    )
    return output_path


def build_trade_plan_rows(
    candidates: pd.DataFrame,
    ticker_metadata: pd.DataFrame,
    session_group: str,
    group_rule: dict[str, Any],
    local_timestamp: datetime,
    price_loader: Callable[[str], float | None] = get_current_price,
) -> list[dict[str, Any]]:
    """Build validated trade-plan rows from candidate signals."""

    metadata_by_ticker = metadata_lookup(ticker_metadata)
    rows: list[dict[str, Any]] = []
    for _, candidate in candidates.iterrows():
        direction = str(candidate.get("direction", "")).strip().lower()
        if direction not in {"buy", "sell"}:
            continue

        ticker = str(candidate.get("ticker", "")).strip()
        if not ticker:
            continue

        try:
            entry_price = price_loader(ticker)
            entry_value = _to_float(entry_price)
            if entry_value is None or entry_value <= 0:
                continue
            analysis_price = _to_float(
                candidate.get("analysis_price", candidate.get("current_price"))
            )
            atr_percent = _to_float(candidate.get("atr_percent_1d"))
            if analysis_price is None or atr_percent is None:
                continue
            allowed, validation_reason, drift, _ = is_entry_price_still_valid(
                analysis_price=analysis_price,
                entry_price=entry_value,
                atr_percent_1d=atr_percent,
                direction=direction,
            )
            if not allowed:
                continue

            atr_1d = _to_float(candidate.get("atr_1d"))
            signal_strength = _to_float(candidate.get("signal_strength")) or 0.0
            sl_tp = calculate_daily_sl_tp(
                current_price=entry_value,
                direction=direction,
                signal_strength=signal_strength,
                atr_1d=atr_1d or 0.0,
                sl_multiplier=float(group_rule["sl_multiplier"]),
                tp_base_multiplier=float(group_rule["tp_base_multiplier"]),
                tp_strength_multiplier=float(group_rule["tp_strength_multiplier"]),
            )
            if sl_tp["stop_loss"] is None or sl_tp["take_profit"] is None:
                continue

            metadata = metadata_by_ticker.get(ticker, {})
            close_time_local = build_local_time_text(
                local_timestamp.date(),
                str(group_rule["close_time"]),
            )
            reason = f"{validation_reason}; {candidate.get('reason', '')}"[:500]
            row = {
                "Ticker name": ticker,
                "current price": smart_round_price(entry_value),
                "direction of trading": direction,
                "Stop Loss level": sl_tp["stop_loss"],
                "Take Profit level": sl_tp["take_profit"],
                "ticker": ticker,
                "description": metadata.get("description", ""),
                "session_group": session_group,
                "entry_time_local": local_timestamp.strftime("%Y-%m-%d %H:%M"),
                "close_time_local": close_time_local,
                "entry_price": smart_round_price(entry_value),
                "direction": direction,
                "signal_strength": signal_strength,
                "stop_loss": sl_tp["stop_loss"],
                "take_profit": sl_tp["take_profit"],
                "risk_reward_ratio": sl_tp["risk_reward_ratio"],
                "analysis_price": smart_round_price(analysis_price),
                "price_drift_percent": round(drift * 100.0, 4),
                "entry_validation_result": "valid",
                "reason": reason,
            }
            rows.append(row)
        except Exception as exc:
            print(f"WARNING: skipped {ticker} at entry validation: {exc}", flush=True)
            continue
    return rows


def add_metadata_columns(
    signals: pd.DataFrame,
    ticker_metadata: pd.DataFrame,
) -> pd.DataFrame:
    """Attach metadata columns to candidate signals."""

    enriched = signals.copy()
    metadata_by_ticker = metadata_lookup(ticker_metadata)
    enriched["description"] = [
        metadata_by_ticker.get(str(ticker), {}).get("description", "")
        for ticker in enriched.get("ticker", [])
    ]
    return enriched


def metadata_lookup(ticker_metadata: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """Return metadata rows keyed by ticker."""

    if ticker_metadata.empty or "ticker" not in ticker_metadata.columns:
        return {}
    return {
        str(row["ticker"]): row.to_dict()
        for _, row in ticker_metadata.iterrows()
        if str(row.get("ticker", "")).strip()
    }


def tickers_for_group(ticker_metadata: pd.DataFrame, session_group: str) -> list[str]:
    """Return tickers belonging to a session group."""

    if ticker_metadata.empty:
        return []
    filtered = ticker_metadata[
        ticker_metadata["session_group"].astype(str) == session_group
    ]
    return [str(ticker) for ticker in filtered["ticker"].dropna().tolist()]


def read_ticker_trading_times() -> pd.DataFrame:
    """Read ticker trading metadata CSV if available."""

    path = ticker_trading_times_path()
    if not path.exists():
        return pd.DataFrame(columns=METADATA_COLUMNS)
    return pd.read_csv(path)


def candidate_output_path(session_group: str, local_timestamp: datetime) -> Path:
    """Return group candidate filename."""

    return OUTPUT_DIR / (
        f"best_signals_{session_group}_{local_timestamp:%Y-%m-%d_%H-%M}.csv"
    )


def trade_plan_output_path(session_group: str, local_timestamp: datetime) -> Path:
    """Return group trade-plan filename."""

    return OUTPUT_DIR / (
        f"trade_plan_{session_group}_{local_timestamp:%Y-%m-%d_%H-%M}.csv"
    )


def find_latest_candidate_file(session_group: str, local_day: date) -> Path:
    """Find the latest candidate file for a group on a local date."""

    pattern = f"best_signals_{session_group}_{local_day:%Y-%m-%d}_*.csv"
    candidates = sorted(
        OUTPUT_DIR.glob(pattern),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(
            f"No candidate file found for {session_group} on {local_day}."
        )
    return candidates[0]


def due_session_events(
    rules: dict[str, Any],
    ticker_metadata: pd.DataFrame,
    local_timestamp: datetime,
    executed_events: set[str],
) -> list[tuple[str, str, str]]:
    """Return due scheduler events as (key, group, event_type)."""

    due_events: list[tuple[str, str, str]] = []
    local_date = local_timestamp.date()
    current_hm = local_timestamp.strftime("%H:%M")
    for group_name, group_rule in rules.get("session_groups", {}).items():
        if not group_rule.get("enabled", False):
            continue
        if not is_trading_day(str(group_rule.get("trading_days", "")), local_date):
            continue
        if tickers_for_group(ticker_metadata, group_name) == []:
            continue

        event_times = {
            "analysis": str(group_rule["analysis_time"]),
            "open": adjusted_open_event_time(
                str(group_rule["open_time"]),
                int(rules.get("entry_check_minutes_before_open", 0)),
            ),
            "close": str(group_rule["close_time"]),
        }
        for event_type, event_time in event_times.items():
            if current_hm != event_time:
                continue
            event_key = f"{local_date}:{group_name}:{event_type}:{event_time}"
            if event_key not in executed_events:
                due_events.append((event_key, group_name, event_type))
    return due_events


def execute_due_event(
    group_name: str,
    event_type: str,
    rules: dict[str, Any],
    ticker_metadata: pd.DataFrame,
    local_timestamp: datetime,
) -> None:
    """Execute one due scheduler event."""

    utc_timestamp = local_timestamp.astimezone(timezone.utc)
    if event_type == "analysis":
        run_analysis_for_group(group_name, rules, ticker_metadata, now=utc_timestamp)
    elif event_type == "open":
        run_trade_plan_for_group(group_name, rules, ticker_metadata, now=utc_timestamp)
    elif event_type == "close":
        print(
            f"Close event reached for {group_name} at "
            f"{local_timestamp:%Y-%m-%d %H:%M}. No order action is taken.",
            flush=True,
        )


def print_status(
    rules: dict[str, Any],
    ticker_metadata: pd.DataFrame,
    executed_events: set[str],
) -> None:
    """Print current scheduler status."""

    timezone_info = rules_timezone(rules)
    now = datetime.now(timezone_info)
    print(f"Status at {now:%Y-%m-%d %H:%M %Z}", flush=True)
    print(f"Output directory: {OUTPUT_DIR}", flush=True)
    print(f"Executed events today/session: {len(executed_events)}", flush=True)
    for group_name, group_rule in rules.get("session_groups", {}).items():
        count = len(tickers_for_group(ticker_metadata, group_name))
        enabled = bool(group_rule.get("enabled", False))
        print(
            f"- {group_name}: enabled={enabled}, tickers={count}, "
            f"analysis={group_rule.get('analysis_time')}, "
            f"open={group_rule.get('open_time')}, "
            f"close={group_rule.get('close_time')}, "
            f"days={group_rule.get('trading_days')}",
            flush=True,
        )


def run_self_test() -> None:
    """Run offline self-tests for session scheduling and trade-plan behavior."""

    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        rules_path = Path(temp_dir) / SESSION_RULES_FILE
        loaded_rules = load_session_rules(rules_path)
        assert rules_path.exists()
        assert loaded_rules["session_groups"]["crypto_24_7"]["enabled"] is True
        rules_path.write_text("{ invalid", encoding="utf-8")
        assert load_session_rules(rules_path, previous_rules=loaded_rules) is loaded_rules

    assert parse_trading_days("mon-fri") == {0, 1, 2, 3, 4}
    assert parse_trading_days("mon-sun") == {0, 1, 2, 3, 4, 5, 6}
    assert parse_trading_days("mon,wed,fri") == {0, 2, 4}

    assert classify_ticker("BTCUSD", "Bitcoin crypto", {}) == (
        "crypto",
        "crypto_24_7",
    )
    assert classify_ticker("EURUSD", "Euro vs US dollar", {}) == (
        "forex",
        "forex_major",
    )
    assert classify_ticker("EURJPY", "Euro vs yen", {}) == (
        "forex",
        "forex_exotic",
    )
    assert classify_ticker(
        "AAPL",
        "Apple Inc NASDAQ",
        {"start_trade_time": "15:30", "end_trade_time": "22:00"},
    ) == ("us_stock", "us_stock_index")
    assert classify_ticker(
        "DAX",
        "Germany DAX index",
        {"start_trade_time": "09:00", "end_trade_time": "17:30"},
    ) == ("europe_stock", "europe_stock_index")

    metadata = pd.DataFrame(
        [
            {"ticker": "EURUSD", "session_group": "forex_major", "description": ""},
            {"ticker": "BTCUSD", "session_group": "crypto_24_7", "description": ""},
        ]
    )
    rules = copy.deepcopy(DEFAULT_SESSION_RULES)
    rules["session_groups"]["crypto_24_7"]["enabled"] = False
    local_time = datetime(2026, 6, 12, 14, 45, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
    assert due_session_events(rules, metadata, local_time, set()) == []

    rules = copy.deepcopy(DEFAULT_SESSION_RULES)
    sunday = datetime(2026, 6, 14, 8, 45, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
    assert due_session_events(rules, metadata, sunday, set()) == []

    friday = datetime(2026, 6, 12, 8, 45, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
    due = due_session_events(rules, metadata, friday, set())
    assert any(event[1:] == ("forex_major", "analysis") for event in due)
    first_key = due[0][0]
    assert due_session_events(rules, metadata, friday, {first_key}) == []

    changed_rules = copy.deepcopy(rules)
    changed_rules["session_groups"]["forex_major"]["analysis_time"] = "08:46"
    changed_time = datetime(2026, 6, 12, 8, 46, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
    changed_due = due_session_events(changed_rules, metadata, changed_time, {first_key})
    assert any(event[1:] == ("forex_major", "analysis") for event in changed_due)

    assert is_entry_price_still_valid(100, 100.1, 0.03, "buy")[0] is True
    assert is_entry_price_still_valid(100, 104, 0.03, "buy")[0] is False
    strict = is_entry_price_still_valid(100, 100.7, 0.10, "buy")
    loose = is_entry_price_still_valid(100, 99.3, 0.10, "buy")
    assert strict[0] is False
    assert loose[0] is True

    entry_sl_tp = calculate_daily_sl_tp(
        current_price=110,
        direction="buy",
        signal_strength=80,
        atr_1d=5,
        sl_multiplier=0.40,
        tp_base_multiplier=0.50,
        tp_strength_multiplier=0.20,
    )
    assert entry_sl_tp["stop_loss"] == 108.0

    crypto_rule = DEFAULT_SESSION_RULES["session_groups"]["crypto_24_7"]
    forex_rule = DEFAULT_SESSION_RULES["session_groups"]["forex_major"]
    crypto_sl_tp = calculate_daily_sl_tp(100, "buy", 80, 5, **_sl_tp_kwargs(crypto_rule))
    forex_sl_tp = calculate_daily_sl_tp(100, "buy", 80, 5, **_sl_tp_kwargs(forex_rule))
    assert crypto_sl_tp["stop_loss"] > forex_sl_tp["stop_loss"]

    candidate = pd.DataFrame(
        [
            {
                "ticker": "EURUSD",
                "direction": "neutral",
                "current_price": 100,
                "analysis_price": 100,
                "atr_percent_1d": 0.03,
                "atr_1d": 3,
                "signal_strength": 50,
                "reason": "neutral",
            }
        ]
    )
    plan_rows = build_trade_plan_rows(
        candidates=candidate,
        ticker_metadata=metadata,
        session_group="forex_major",
        group_rule=forex_rule,
        local_timestamp=friday,
        price_loader=lambda ticker: 100.0,
    )
    assert plan_rows == []

    def synthetic_processor(ticker: str) -> dict[str, Any]:
        if ticker == "BAD":
            raise RuntimeError("synthetic failure")
        return {"ticker": ticker, "direction": "neutral"}

    rows = analyze_group_tickers(["OK", "BAD"], processor=synthetic_processor)
    assert len(rows) == 2
    assert rows[1]["ticker"] == "BAD"
    assert rows[1]["direction"] == "neutral"

    print("Self-test passed.")


def main() -> None:
    """Run the scheduler loop and listen for console commands."""

    parser = argparse.ArgumentParser(description="Session-aware signal scheduler.")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run offline self-tests and exit.",
    )
    args = parser.parse_args()
    if args.self_test:
        run_self_test()
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Starting trade_signal_generator.py...", flush=True)
    print(f"CSV output directory: {OUTPUT_DIR}", flush=True)

    commands: queue.Queue[str] = queue.Queue()
    threading.Thread(target=console_listener, args=(commands,), daemon=True).start()
    print(
        "Console listener started. Commands typed during startup will be queued.",
        flush=True,
    )

    print("Loading session rules...", flush=True)
    rules = load_session_rules()
    last_rules_mtime = rules_mtime()
    print(f"Session rules loaded from {session_rules_path()}.", flush=True)

    print(
        "Startup metadata refresh is beginning. The command loop becomes ready "
        "after this step finishes.",
        flush=True,
    )
    ticker_metadata = _safe_update_or_read_metadata()
    print(
        f"Startup metadata ready: {len(ticker_metadata)} tickers loaded.",
        flush=True,
    )
    executed_events: set[str] = set()

    print(
        "trade_signal_generator.py is ready. "
        f"Commands: {TRIGGER_COMMAND}, {UPDATE_COMMAND}, {RELOAD_COMMAND}, "
        f"{STATUS_COMMAND}, {QUIT_COMMAND}.",
        flush=True,
    )

    while True:
        timeout = min(60, int(rules.get("rules_reload_interval_seconds", 60)))
        try:
            command = commands.get(timeout=max(1, timeout))
        except queue.Empty:
            command = None

        if command is not None:
            normalized_command = command.strip().lower()
            if normalized_command in {QUIT_COMMAND, LEGACY_EXIT_COMMAND}:
                print("Stopping trade_signal_generator.py.", flush=True)
                return
            if normalized_command == TRIGGER_COMMAND:
                run_analysis_for_all_enabled_groups(rules, ticker_metadata)
            elif normalized_command == UPDATE_COMMAND:
                try:
                    ticker_metadata = update_ticker_trading_times()
                except Exception as exc:
                    print(f"ERROR updating ticker metadata: {exc}", flush=True)
            elif normalized_command == RELOAD_COMMAND:
                rules = load_session_rules(previous_rules=rules)
                last_rules_mtime = rules_mtime()
                print("Reloaded session rules.", flush=True)
            elif normalized_command == STATUS_COMMAND:
                print_status(rules, ticker_metadata, executed_events)
            else:
                print("unknown command", flush=True)

        current_mtime = rules_mtime()
        if current_mtime is not None and current_mtime != last_rules_mtime:
            rules = load_session_rules(previous_rules=rules)
            last_rules_mtime = current_mtime
            print("Reloaded changed session rules.", flush=True)

        timezone_info = rules_timezone(rules)
        local_timestamp = datetime.now(timezone_info).replace(second=0, microsecond=0)
        for event_key, group_name, event_type in due_session_events(
            rules,
            ticker_metadata,
            local_timestamp,
            executed_events,
        ):
            try:
                execute_due_event(
                    group_name,
                    event_type,
                    rules,
                    ticker_metadata,
                    local_timestamp,
                )
            except Exception as exc:
                print(f"ERROR running {event_type} for {group_name}: {exc}", flush=True)
            executed_events.add(event_key)


def _safe_update_or_read_metadata() -> pd.DataFrame:
    try:
        return update_ticker_trading_times()
    except Exception as exc:
        print(f"WARNING: could not update ticker metadata on startup: {exc}", flush=True)
        return read_ticker_trading_times()


def adjusted_open_event_time(open_time: str, minutes_before_open: int) -> str:
    base = datetime.combine(date(2000, 1, 1), parse_hhmm(open_time))
    adjusted = base - timedelta(minutes=max(0, minutes_before_open))
    return adjusted.strftime("%H:%M")


def parse_hhmm(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def rules_timezone(rules: dict[str, Any]) -> ZoneInfo:
    return ZoneInfo(str(rules.get("timezone") or DEFAULT_TIMEZONE))


def build_local_time_text(local_day: date, hhmm: str) -> str:
    return f"{local_day.isoformat()} {hhmm}"


def _safe_text(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value)


def _to_float(value: object) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _normalize_hhmm(value: object) -> str:
    text = _safe_text(value).strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        hour_text, minute_text = text.split(":")
        hour = int(hour_text)
        minute = int(minute_text)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    return ""


def _looks_like_us_session(start: str, end: str) -> bool:
    return _time_between(start, "15:00", "16:00") and _time_between(
        end,
        "21:00",
        "23:00",
    )


def _looks_like_europe_session(start: str, end: str) -> bool:
    return _time_between(start, "08:30", "09:30") and _time_between(
        end,
        "16:30",
        "18:00",
    )


def _time_between(value: str, start: str, end: str) -> bool:
    if not value:
        return False
    return parse_hhmm(start) <= parse_hhmm(value) <= parse_hhmm(end)


def _looks_like_forex_pair(symbol_key: str) -> bool:
    if not re.fullmatch(r"[A-Z]{6}", symbol_key):
        return False
    return symbol_key[:3] in FIAT_CODES and symbol_key[3:] in FIAT_CODES


def _looks_like_index(text: str) -> bool:
    return any(token in text for token in ["INDEX", "INDICE", "100", "500", "30", "40"])


def _symbol_key(symbol: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", symbol.upper())


def _sl_tp_kwargs(group_rule: dict[str, Any]) -> dict[str, float]:
    return {
        "sl_multiplier": float(group_rule["sl_multiplier"]),
        "tp_base_multiplier": float(group_rule["tp_base_multiplier"]),
        "tp_strength_multiplier": float(group_rule["tp_strength_multiplier"]),
    }


def _analysis_error_row(ticker: str, exc: Exception) -> dict[str, Any]:
    row = {column: None for column in OUTPUT_COLUMNS}
    row.update(
        {
            "ticker": ticker,
            "direction": "neutral",
            "signal_strength": 0.0,
            "reason": f"ERROR: {exc}"[:500],
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    return row


if __name__ == "__main__":
    main()
