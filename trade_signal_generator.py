"""Session-aware scheduled signal and trade-plan generation.

This script analyses symbols and writes CSV files only. It does not place
orders, connect to broker execution APIs, size positions, or submit trades.
"""

from __future__ import annotations

import argparse
import copy
import inspect
import json
import math
import queue
import re
import threading
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from csv_analysis import get_current_price
from investment_adviser import load_symbol_data
from find_signal import (
    OUTPUT_COLUMNS,
    calculate_daily_sl_tp,
    normalize_ohlcv_columns,
    process_symbol,
    smart_round_price,
)
from investment_adviser.providers.mt5 import (
    MT5InstrumentProvider,
    configure_mt5_connection,
)
from runtime_paths import (
    best_signals_dir,
    ensure_runtime_directories,
    logs_dir,
    results_dir,
    trade_plans_dir,
)
from scheduler_config import (
    CLASSIFICATION_OVERRIDES_FILE,
    DAY_TO_INDEX,
    DEFAULT_MT5_HOST,
    DEFAULT_MT5_MAX_BARS,
    DEFAULT_MT5_PORT,
    DEFAULT_SESSION_RULES,
    DEFAULT_TIMEZONE,
    GROUP_DEFAULT_WINDOWS,
    LEGACY_EXIT_COMMAND,
    LOG_PREFIX,
    LOG_RETENTION_DAYS,
    METADATA_COLUMNS,
    MT5_SYMBOL_SESSIONS_FILE,
    OUTPUT_DIR,
    QUIT_COMMAND,
    RELOAD_COMMAND,
    RESULT_COLUMNS,
    SESSION_RULES_FILE,
    SIGNALS_ALIAS,
    SIGNALS_COMMAND,
    STATUS_COMMAND,
    TICKER_TRADING_TIMES_FILE,
    TRADE_PLAN_COLUMNS,
    TRIGGER_COMMAND,
    UPDATE_COMMAND,
)
from scheduler_logging import (
    LOGGER,
    cleanup_old_logs,
    ensure_daily_logging,
    log_file_path,
    setup_logging,
    timed_task,
)
import ticker_classification_rules as classification_rules


def print_command_help() -> None:
    """Print detailed console command help."""

    print("Console commands:", flush=True)
    print(
        f"  {TRIGGER_COMMAND:<7} Run analysis immediately for every enabled "
        "session group using current session_rules.json and current "
        "ticker_trading_times.csv.",
        flush=True,
    )
    print(
        f"  {UPDATE_COMMAND:<7} Connect to MT5, refresh symbol metadata, "
        "apply session data and manual overrides, then rewrite "
        "ticker_trading_times.csv.",
        flush=True,
    )
    print(
        f"  {RELOAD_COMMAND:<7} Reload session_rules.json now. If the JSON is "
        "invalid, keep the last valid rules and continue running.",
        flush=True,
    )
    print(
        f"  {STATUS_COMMAND:<7} Print loaded session groups, tickers per group, "
        "schedule times, trading days, and executed-event count.",
        flush=True,
    )
    print(
        f"  {SIGNALS_COMMAND:<7} Generate final trade-plan CSV files now for all "
        "enabled groups that already have a today's best_signals_<group>_*.csv "
        f"candidate file. Alias: {SIGNALS_ALIAS}.",
        flush=True,
    )
    print(
        f"  {QUIT_COMMAND:<7} Stop the scheduler cleanly. Legacy command "
        f"'{LEGACY_EXIT_COMMAND}' also stops it.",
        flush=True,
    )


def print_console_ready() -> None:
    """Tell the user that console commands are being listened for."""

    print("Console is listening for commands now.", flush=True)
    print_command_help()


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


def mt5_symbol_sessions_path() -> Path:
    """Return the optional MT5 symbol sessions CSV path."""

    return OUTPUT_DIR / MT5_SYMBOL_SESSIONS_FILE


def classification_overrides_path() -> Path:
    """Return the optional manual classification override CSV path."""

    return OUTPUT_DIR / CLASSIFICATION_OVERRIDES_FILE


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

    try:
        existing_rules = json.loads(rules_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return rules_path

    merged_rules = merge_session_rules(existing_rules)
    if session_rules_need_migration(existing_rules):
        rules_path.write_text(
            json.dumps(merged_rules, indent=2),
            encoding="utf-8",
        )
    return rules_path


def session_rules_need_migration(existing_rules: dict[str, Any]) -> bool:
    """Return True when the editable rules file is missing default keys."""

    mt5_rules = existing_rules.get("mt5")
    if not isinstance(mt5_rules, dict):
        return True
    for key in DEFAULT_SESSION_RULES["mt5"]:
        if key not in mt5_rules:
            return True

    existing_groups = existing_rules.get("session_groups", {})
    if not isinstance(existing_groups, dict):
        return True
    for group_name, default_group in DEFAULT_SESSION_RULES["session_groups"].items():
        existing_group = existing_groups.get(group_name)
        if not isinstance(existing_group, dict):
            return True
        for key in default_group:
            if key not in existing_group:
                return True
    for key in [
        "timezone",
        "best_signal_limit",
        "entry_check_minutes_before_open",
        "rules_reload_interval_seconds",
    ]:
        if key not in existing_rules:
            return True
    return False


def load_session_rules(
    path: Path | None = None,
    previous_rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load editable session rules, keeping the last valid rules on JSON error."""

    with timed_task("load_session_rules", path=path or session_rules_path()):
        rules_path = ensure_session_rules_file(path)
        try:
            raw_rules = json.loads(rules_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            if previous_rules is not None:
                print(
                    f"WARNING: invalid {rules_path.name}; keeping last valid rules: {exc}",
                    flush=True,
                )
                apply_mt5_settings_from_rules(previous_rules)
                return previous_rules
            print(
                f"WARNING: invalid {rules_path.name}; using built-in defaults: {exc}",
                flush=True,
            )
            rules = copy.deepcopy(DEFAULT_SESSION_RULES)
            apply_mt5_settings_from_rules(rules)
            return rules

        rules = merge_session_rules(raw_rules)
        apply_mt5_settings_from_rules(rules)
        return rules


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

    raw_mt5 = raw_rules.get("mt5")
    if isinstance(raw_mt5, dict):
        merged["mt5"].update(raw_mt5)

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


def apply_mt5_settings_from_rules(rules: dict[str, Any]) -> None:
    """Apply editable MT5 host/port settings to all MT5 providers."""

    mt5_rules = rules.get("mt5", {})
    if not isinstance(mt5_rules, dict):
        mt5_rules = {}
    host = str(mt5_rules.get("host") or DEFAULT_MT5_HOST).strip()
    port = _safe_int(mt5_rules.get("port"), DEFAULT_MT5_PORT)
    max_bars = _safe_int(mt5_rules.get("max_bars"), DEFAULT_MT5_MAX_BARS)
    configure_mt5_connection(host=host, port=port, max_bars=max_bars)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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


def update_ticker_trading_times(debug_symbol: str | None = None) -> pd.DataFrame:
    """Discover MT5 instruments, classify sessions, save CSV, and return it."""

    with timed_task("update_ticker_trading_times", debug_symbol=debug_symbol or ""):
        print("Updating ticker trading metadata from MT5...", flush=True)
        print("Connecting to MT5 and requesting symbol metadata...", flush=True)
        provider = MT5InstrumentProvider()
        with timed_task("mt5_fetch_symbol_metadata"):
            metadata_rows = provider.find_instrument_metadata()
        if debug_symbol:
            print_debug_symbol_metadata(metadata_rows, debug_symbol)
        print(
            f"Received metadata for {len(metadata_rows)} tradable MT5 symbols.",
            flush=True,
        )
        print("Classifying tickers into session groups...", flush=True)
        session_map = load_mt5_symbol_sessions()
        overrides = load_classification_overrides()
        with timed_task(
            "build_ticker_trading_times",
            metadata_rows=len(metadata_rows),
            sessions=len(session_map),
            overrides=len(overrides),
        ):
            data = build_ticker_trading_times(
                metadata_rows,
                session_map=session_map,
                overrides=overrides,
            )
        output_path = ticker_trading_times_path()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with timed_task("write_ticker_trading_times_csv", path=output_path, rows=len(data)):
            data.to_csv(output_path, index=False)
        group_counts = (
            data["session_group"].value_counts().to_dict() if not data.empty else {}
        )
        LOGGER.info("ticker_trading_times group_counts=%s", group_counts)
        print(f"Saved {output_path.name} with {len(data)} tickers.", flush=True)
        print(f"Ticker session groups: {group_counts}", flush=True)
        return data


def build_ticker_trading_times(
    metadata_rows: list[dict[str, Any]],
    session_map: dict[str, dict[str, list[list[str]]]] | None = None,
    overrides: dict[str, dict[str, str]] | None = None,
    timestamp_utc: datetime | None = None,
) -> pd.DataFrame:
    """Build ticker trading metadata from provider metadata rows."""

    updated_at = timestamp_utc or datetime.now(timezone.utc)
    session_map = session_map or {}
    overrides = overrides or {}
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
        raw_sessions = session_map.get(_symbol_key(symbol), {})
        start_time, end_time, trading_days, session_source = detect_trading_window(
            metadata,
            raw_sessions,
        )
        classifier_metadata = {
            **metadata,
            "start_trade_time": start_time,
            "end_trade_time": end_time,
            "raw_sessions": raw_sessions,
        }
        ticker_type, session_group, classification_reason = classify_ticker_from_metadata(
            symbol,
            description,
            classifier_metadata,
        )
        classification_source = (
            "mt5_metadata" if classification_reason.startswith("MT5") else "heuristic"
        )
        if ticker_type == "unknown" and session_group == "unknown":
            classification_source = "unknown"

        if not start_time and not end_time and session_group != "unknown":
            start_time, end_time, trading_days = default_window_for_classification(
                symbol=symbol,
                ticker_type=ticker_type,
                session_group=session_group,
                metadata=metadata,
                local_day=updated_at.astimezone(ZoneInfo(DEFAULT_TIMEZONE)).date(),
            )
        elif session_source == "mt5_sessions":
            classification_source = "mt5_sessions"

        override = overrides.get(_symbol_key(symbol))
        if override:
            ticker_type = override.get("ticker_type") or ticker_type
            session_group = override.get("session_group") or session_group
            start_time = override.get("start_trade_time") or start_time
            end_time = override.get("end_trade_time") or end_time
            trading_days = override.get("trading_days") or trading_days
            description = override.get("description_override") or description
            classification_source = "manual_override"
            override_reason = override.get("reason") or "Manual override"
            classification_reason = override_reason

        if not start_time and not end_time and session_group != "unknown":
            start_time, end_time, trading_days = default_window_for_classification(
                symbol=symbol,
                ticker_type=ticker_type,
                session_group=session_group,
                metadata=metadata,
                local_day=updated_at.astimezone(ZoneInfo(DEFAULT_TIMEZONE)).date(),
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
                "classification_source": classification_source,
                "classification_reason": classification_reason,
                "exchange": _safe_text(metadata.get("exchange")),
                "country": _safe_text(metadata.get("country")),
                "category": _safe_text(metadata.get("category")),
                "path": _safe_text(metadata.get("path")),
                "currency_base": _safe_text(metadata.get("currency_base")),
                "currency_profit": _safe_text(metadata.get("currency_profit")),
                "raw_sessions": json.dumps(raw_sessions, sort_keys=True),
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


def load_mt5_symbol_sessions(
    path: Path | None = None,
) -> dict[str, dict[str, list[list[str]]]]:
    """Read optional mt5_symbol_sessions.csv dumped by the MQL5 helper."""

    sessions_path = path or mt5_symbol_sessions_path()
    with timed_task("load_mt5_symbol_sessions", path=sessions_path):
        if not sessions_path.exists():
            LOGGER.info("No MT5 session CSV found at %s", sessions_path)
            return {}
        try:
            sessions = pd.read_csv(sessions_path)
        except Exception as exc:
            print(f"WARNING: could not read {sessions_path.name}: {exc}", flush=True)
            return {}
        required = {"symbol", "day_of_week", "from_time", "to_time"}
        if not required <= set(sessions.columns):
            print(
                f"WARNING: {sessions_path.name} is missing required columns: "
                f"{', '.join(sorted(required - set(sessions.columns)))}",
                flush=True,
            )
            return {}

        result: dict[str, dict[str, list[list[str]]]] = {}
        for _, row in sessions.iterrows():
            symbol = _symbol_key(str(row.get("symbol", "")))
            day = normalize_day_name(str(row.get("day_of_week", "")))
            start = _normalize_hhmm(row.get("from_time"))
            end = _normalize_hhmm(row.get("to_time"))
            if not symbol or not day or not start or not end:
                continue
            result.setdefault(symbol, {}).setdefault(day, []).append([start, end])
        LOGGER.info("Loaded MT5 sessions for %s symbols", len(result))
        return result


def load_classification_overrides(
    path: Path | None = None,
) -> dict[str, dict[str, str]]:
    """Read optional manual classification overrides."""

    override_path = path or classification_overrides_path()
    with timed_task("load_classification_overrides", path=override_path):
        if not override_path.exists():
            LOGGER.info("No classification override CSV found at %s", override_path)
            return {}
        try:
            overrides = pd.read_csv(override_path).fillna("")
        except Exception as exc:
            print(f"WARNING: could not read {override_path.name}: {exc}", flush=True)
            return {}

        result: dict[str, dict[str, str]] = {}
        for _, row in overrides.iterrows():
            ticker = _symbol_key(str(row.get("ticker", "")))
            if not ticker:
                continue
            result[ticker] = {
                "ticker_type": str(row.get("ticker_type", "")).strip(),
                "session_group": str(row.get("session_group", "")).strip(),
                "start_trade_time": _normalize_hhmm(row.get("start_trade_time")),
                "end_trade_time": _normalize_hhmm(row.get("end_trade_time")),
                "trading_days": str(row.get("trading_days", "")).strip(),
                "description_override": str(row.get("description_override", "")).strip(),
                "reason": str(row.get("reason", "")).strip(),
            }
        LOGGER.info("Loaded classification overrides for %s tickers", len(result))
        return result


def print_debug_symbol_metadata(
    metadata_rows: list[dict[str, Any]],
    debug_symbol: str,
) -> None:
    """Print the full MT5 metadata dictionary for one selected symbol."""

    wanted = _symbol_key(debug_symbol)
    for metadata in metadata_rows:
        symbol = str(metadata.get("name") or metadata.get("ticker") or "")
        if _symbol_key(symbol) == wanted:
            print(
                f"Debug metadata for {symbol}:",
                json.dumps(metadata, indent=2, sort_keys=True, default=str),
                sep="\n",
                flush=True,
            )
            return
    print(f"Debug symbol {debug_symbol!r} was not found in MT5 metadata.", flush=True)


def summarize_raw_sessions(
    raw_sessions: dict[str, list[list[str]]],
) -> tuple[str, str, str]:
    """Summarize detailed sessions into simple start/end/day columns."""

    days = [day for day in DAY_TO_INDEX if raw_sessions.get(day)]
    if not days:
        return "", "", "unknown"
    starts: list[str] = []
    ends: list[str] = []
    for day in days:
        for start, end in raw_sessions[day]:
            starts.append(start)
            ends.append(end)
    if not starts or not ends:
        return "", "", "unknown"
    return min(starts), max(ends), compress_trading_days(days)


def compress_trading_days(days: list[str]) -> str:
    """Compress day names into mon-fri style where possible."""

    indexes = sorted({DAY_TO_INDEX[day] for day in days if day in DAY_TO_INDEX})
    if indexes == [0, 1, 2, 3, 4]:
        return "mon-fri"
    if indexes == [0, 1, 2, 3, 4, 5, 6]:
        return "mon-sun"
    if indexes == [0, 1, 2, 3, 6]:
        return "sun-thu"
    reverse_days = {value: key for key, value in DAY_TO_INDEX.items()}
    return ",".join(reverse_days[index] for index in indexes)


def normalize_day_name(value: str) -> str:
    """Normalize MQL5 day names to mon/tue/..."""

    normalized = value.strip().lower()
    mapping = {
        "monday": "mon",
        "tuesday": "tue",
        "wednesday": "wed",
        "thursday": "thu",
        "friday": "fri",
        "saturday": "sat",
        "sunday": "sun",
    }
    return mapping.get(normalized, normalized[:3])


def default_window_for_classification(
    symbol: str,
    ticker_type: str,
    session_group: str,
    metadata: dict[str, Any],
    local_day: date,
) -> tuple[str, str, str]:
    """Return strategy session defaults, using timezone conversion when useful."""

    local_tz = DEFAULT_TIMEZONE
    text = build_classification_text(symbol, "", metadata)
    if session_group == "us_stock_index" and ticker_type in {"us_stock", "us_etf"}:
        start, end = convert_exchange_session_to_local(
            "09:30",
            "16:00",
            "America/New_York",
            local_tz,
            local_day,
        )
        return start, end, "mon-fri"
    if session_group == "europe_stock_index" and "london" in text:
        start, end = convert_exchange_session_to_local(
            "08:00",
            "16:30",
            "Europe/London",
            local_tz,
            local_day,
        )
        return start, end, "mon-fri"
    if session_group == "europe_stock_index" and ticker_type == "europe_stock":
        start, end = convert_exchange_session_to_local(
            "09:00",
            "17:30",
            "Europe/Amsterdam",
            local_tz,
            local_day,
        )
        return start, end, "mon-fri"
    return GROUP_DEFAULT_WINDOWS.get(session_group, GROUP_DEFAULT_WINDOWS["unknown"])


def convert_exchange_session_to_local(
    open_time: str,
    close_time: str,
    exchange_tz: str,
    local_tz: str,
    date: date,
) -> tuple[str, str]:
    """Convert exchange regular-session times to local times using zoneinfo."""

    exchange_zone = ZoneInfo(exchange_tz)
    local_zone = ZoneInfo(local_tz)
    open_dt = datetime.combine(date, parse_hhmm(open_time), tzinfo=exchange_zone)
    close_dt = datetime.combine(date, parse_hhmm(close_time), tzinfo=exchange_zone)
    return (
        open_dt.astimezone(local_zone).strftime("%H:%M"),
        close_dt.astimezone(local_zone).strftime("%H:%M"),
    )


def detect_trading_window(
    metadata: dict[str, Any],
    raw_sessions: dict[str, list[list[str]]] | None = None,
) -> tuple[str, str, str, str]:
    """Return start/end/days/source from exact sessions or metadata."""

    if raw_sessions:
        start, end, days = summarize_raw_sessions(raw_sessions)
        if start or end:
            return start, end, days, "mt5_sessions"

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
        return start, end, days or "mon-fri", "mt5_metadata"
    return "", "", days or "unknown", "unknown"


def classify_ticker_from_metadata(
    symbol: str,
    description: str,
    metadata: dict[str, Any],
) -> tuple[str, str, str]:
    """Classify a ticker into type, session_group, and readable reason."""

    symbol_key = _symbol_key(symbol)
    text = build_classification_text(symbol, description, metadata)
    text_upper = text.upper()
    text_tokens = set(re.findall(r"[a-z0-9]+", text))
    start = str(metadata.get("start_trade_time") or "")
    end = str(metadata.get("end_trade_time") or "")

    if _looks_like_us_session(start, end):
        return "us_stock", "us_stock_index", "MT5 session resembles US market hours"
    if _looks_like_europe_session(start, end):
        return (
            "europe_stock",
            "europe_stock_index",
            "MT5 session resembles European market hours",
        )

    crypto_base = symbol_key[:-3] if symbol_key.endswith("USD") else symbol_key
    if crypto_base in classification_rules.CRYPTO_BASE_CODES:
        return "crypto", "crypto_24_7", f"Symbol matched crypto code: {crypto_base}"
    for marker in classification_rules.CRYPTO_MARKERS:
        if _marker_matches(marker, text, text_tokens):
            return "crypto", "crypto_24_7", f"Text matched crypto marker: {marker}"

    if _symbol_looks_like_commodity(symbol_key):
        return "commodity", "commodity_us", f"Symbol matched commodity pattern: {symbol}"
    for marker in classification_rules.COMMODITY_MARKERS:
        if _marker_matches(marker, text, text_tokens):
            return "commodity", "commodity_us", f"Text matched commodity keyword: {marker}"

    if symbol_key in classification_rules.FOREX_MAJOR_SYMBOLS:
        return "forex", "forex_major", "Symbol matched forex major pair"
    if _looks_like_forex_pair(symbol_key):
        return "forex", "forex_exotic", "Symbol matched six-letter forex pair"

    if symbol_key in classification_rules.US_INDEX_ALIASES:
        return "us_index", "us_stock_index", f"Symbol matched US index: {symbol}"
    if symbol_key in classification_rules.EUROPE_INDEX_ALIASES:
        return (
            "europe_index",
            "europe_stock_index",
            f"Symbol matched European index: {symbol}",
        )
    if symbol_key in classification_rules.ASIA_INDEX_ALIASES:
        group = "israel_index" if symbol_key == "TA35" else "asia_index"
        ticker_type = "israel_index" if symbol_key == "TA35" else "asia_index"
        return ticker_type, group, f"Symbol matched Asian/Israel index: {symbol}"

    if "NDAQ 100" in text_upper or "NASDAQ 100" in text_upper or "DJ 30" in text_upper:
        return "us_index", "us_stock_index", "Description matched US index"
    if "NETHERLANDS 25" in text_upper or "FRANCE 40" in text_upper:
        return "europe_index", "europe_stock_index", "Description matched European index"
    if "CHINA 50" in text_upper or "JAPAN 225" in text_upper or "NIKKEI" in text_upper:
        return "asia_index", "asia_index", "Description matched Asian index"

    if symbol_key in classification_rules.KNOWN_US_ETFS:
        return "us_etf", "us_stock_index", f"Symbol matched known US ETF: {symbol}"
    if symbol_key in classification_rules.KNOWN_US_STOCKS:
        return "us_stock", "us_stock_index", f"Symbol matched known US stock: {symbol}"
    for marker in classification_rules.US_METADATA_MARKERS:
        if _marker_matches(marker, text, text_tokens):
            ticker_type = "us_index" if _looks_like_index(text_upper) else "us_stock"
            return ticker_type, "us_stock_index", f"Metadata matched US marker: {marker}"

    if symbol_key in classification_rules.KNOWN_EUROPE_STOCKS:
        return (
            "europe_stock",
            "europe_stock_index",
            f"Symbol matched known European stock: {symbol}",
        )
    for marker in classification_rules.EUROPE_METADATA_MARKERS:
        if _marker_matches(marker, text, text_tokens):
            ticker_type = (
                "europe_index" if _looks_like_index(text_upper) else "europe_stock"
            )
            return (
                ticker_type,
                "europe_stock_index",
                f"Metadata matched European marker: {marker}",
            )

    if "stock" in text or "shares" in text or "equities" in text:
        return "us_stock", "us_stock_index", "Metadata indicated stock/equity"
    if "etf" in text:
        return "us_etf", "us_stock_index", "Metadata indicated ETF"

    return "unknown", "unknown", "No metadata, session, rule, or override matched"


def _marker_matches(marker: str, text: str, tokens: set[str]) -> bool:
    """Return true for whole-token markers or exact phrase markers."""

    normalized = marker.lower().strip()
    if not normalized:
        return False
    if " " in normalized or "." in normalized:
        return normalized in text
    return normalized in tokens


def build_classification_text(
    symbol: str,
    description: str,
    metadata: dict[str, Any],
) -> str:
    """Build lowercase searchable classification text from MT5 metadata."""

    parts = [
        symbol,
        description or "",
        metadata.get("path", "") or "",
        metadata.get("category", "") or "",
        metadata.get("exchange", "") or "",
        metadata.get("country", "") or "",
        metadata.get("currency_base", "") or "",
        metadata.get("currency_profit", "") or "",
        metadata.get("sector", "") or "",
        metadata.get("industry", "") or "",
        metadata.get("sector_name", "") or "",
        metadata.get("industry_name", "") or "",
        metadata.get("page", "") or "",
    ]
    return " ".join(_safe_text(part) for part in parts).lower()


def classify_ticker(
    symbol: str,
    description: str,
    metadata: dict[str, Any],
) -> tuple[str, str]:
    """Backward-compatible wrapper returning ticker_type and session_group."""

    ticker_type, session_group, _ = classify_ticker_from_metadata(
        symbol,
        description,
        metadata,
    )
    return ticker_type, session_group


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

    with timed_task("analyze_group_tickers", tickers=len(tickers)):
        rows: list[dict[str, Any]] = []
        total = len(tickers)
        for index, ticker in enumerate(tickers, start=1):
            print(f"[{index}/{total}] Processing {ticker}...", flush=True)
            with timed_task("analyze_ticker", ticker=ticker, index=index, total=total):
                try:
                    rows.append(processor(ticker))
                except Exception as exc:
                    LOGGER.exception("Ticker analysis failed for %s", ticker)
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

    with timed_task("run_analysis_for_group", session_group=session_group):
        timestamp = now or datetime.now(timezone.utc)
        timezone_info = rules_timezone(rules)
        local_timestamp = timestamp.astimezone(timezone_info)
        group_rule = rules["session_groups"][session_group]
        tickers = tickers_for_group(ticker_metadata, session_group)
        rows = analyze_group_tickers(tickers, processor=processor)
        with timed_task("build_candidate_dataframe", session_group=session_group):
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
        with timed_task(
            "write_candidate_csv",
            session_group=session_group,
            path=output_path,
            rows=len(tradable),
        ):
            tradable.to_csv(output_path, index=False)
        print(
            f"Saved {len(tradable)} candidates for {session_group} "
            f"to {output_path.name}.",
            flush=True,
        )
        return output_path


def run_analysis_for_all_enabled_groups(
    rules: dict[str, Any],
    ticker_metadata: pd.DataFrame,
    now: datetime | None = None,
) -> list[Path]:
    """Run analysis immediately for all enabled session groups."""

    with timed_task("run_analysis_for_all_enabled_groups"):
        output_paths: list[Path] = []
        for group_name, group_rule in rules.get("session_groups", {}).items():
            if not group_rule.get("enabled", False):
                LOGGER.info("Skipping disabled session group %s", group_name)
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
    price_loader: Callable[..., float | None] = get_current_price,
) -> Path:
    """Generate the final trade-plan CSV for one session group."""

    with timed_task("run_trade_plan_for_group", session_group=session_group):
        timestamp = now or datetime.now(timezone.utc)
        timezone_info = rules_timezone(rules)
        local_timestamp = timestamp.astimezone(timezone_info)
        candidate_path = find_latest_candidate_file(session_group, local_timestamp.date())
        with timed_task("load_candidate_csv", path=candidate_path):
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
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with timed_task(
            "write_trade_plan_csv",
            session_group=session_group,
            path=output_path,
            rows=len(rows),
        ):
            pd.DataFrame(rows, columns=TRADE_PLAN_COLUMNS).to_csv(
                output_path,
                index=False,
            )
        print(
            f"Saved {len(rows)} trade-plan rows for {session_group} "
            f"to {output_path.name}.",
            flush=True,
        )
        return output_path


def run_trade_plans_for_all_available_groups(
    rules: dict[str, Any],
    ticker_metadata: pd.DataFrame,
    now: datetime | None = None,
    price_loader: Callable[..., float | None] = get_current_price,
) -> list[Path]:
    """Generate trade plans now for enabled groups with existing candidates."""

    with timed_task("run_trade_plans_for_all_available_groups"):
        output_paths: list[Path] = []
        for group_name, group_rule in rules.get("session_groups", {}).items():
            if not group_rule.get("enabled", False):
                LOGGER.info("Skipping disabled session group for signals: %s", group_name)
                continue
            try:
                output_paths.append(
                    run_trade_plan_for_group(
                        group_name,
                        rules,
                        ticker_metadata,
                        now=now,
                        price_loader=price_loader,
                    )
                )
            except FileNotFoundError as exc:
                LOGGER.info(
                    "Skipping signals command for %s: candidate file missing: %s",
                    group_name,
                    exc,
                )
                print(
                    f"No best_signals candidate file for {group_name}; skipped.",
                    flush=True,
                )
            except Exception:
                LOGGER.exception("Failed to generate trade plan for %s", group_name)
                raise
        print(
            f"Generated {len(output_paths)} trade-plan file(s) from existing candidates.",
            flush=True,
        )
        return output_paths


def run_close_results_for_groups(
    session_groups: list[str],
    rules: dict[str, Any],
    local_timestamp: datetime,
    price_loader: Callable[..., float | None] = get_current_price,
    data_loader: Callable[..., pd.DataFrame] = load_symbol_data,
) -> Path | None:
    """Write close-result rows for groups that close at the same local time."""

    with timed_task(
        "run_close_results_for_groups",
        session_groups=",".join(session_groups),
    ):
        timezone_info = rules_timezone(rules)
        close_timestamp = local_timestamp.astimezone(timezone_info)
        trade_plans: list[pd.DataFrame] = []
        for group_name in session_groups:
            try:
                plan_path = find_latest_trade_plan_file(
                    group_name,
                    close_timestamp.date(),
                )
            except FileNotFoundError as exc:
                LOGGER.info("Skipping close results for %s: %s", group_name, exc)
                print(
                    f"No trade plan file for {group_name}; close results skipped.",
                    flush=True,
                )
                continue
            with timed_task("load_trade_plan_csv", path=plan_path):
                trade_plans.append(pd.read_csv(plan_path))

        if not trade_plans:
            print("No trade plan rows found for close result generation.", flush=True)
            return None

        combined = pd.concat(trade_plans, ignore_index=True)
        rows = build_close_result_rows(
            trade_plan=combined,
            local_timestamp=close_timestamp,
            price_loader=price_loader,
            data_loader=data_loader,
        )
        if not rows:
            print("No close result rows to save.", flush=True)
            return None

        output_path = results_output_path(close_timestamp)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with timed_task("write_results_csv", path=output_path, rows=len(rows)):
            pd.DataFrame(rows, columns=RESULT_COLUMNS).to_csv(output_path, index=False)
        print(f"Saved {len(rows)} close result rows to {output_path.name}.", flush=True)
        return output_path


def build_close_result_rows(
    trade_plan: pd.DataFrame,
    local_timestamp: datetime,
    price_loader: Callable[..., float | None] = get_current_price,
    data_loader: Callable[..., pd.DataFrame] = load_symbol_data,
) -> list[dict[str, Any]]:
    """Build close-result rows from trade-plan rows."""

    timezone_info = local_timestamp.tzinfo or ZoneInfo(DEFAULT_TIMEZONE)
    close_text = local_timestamp.strftime("%Y-%m-%d %H:%M")
    rows: list[dict[str, Any]] = []
    for _, trade in trade_plan.iterrows():
        ticker = _safe_text(trade.get("ticker") or trade.get("Ticker name"))
        direction = _safe_text(
            trade.get("direction") or trade.get("direction of trading")
        ).lower()
        if not ticker or direction not in {"buy", "sell"}:
            continue

        open_price = _to_float(trade.get("entry_price") or trade.get("current price"))
        if open_price is None:
            LOGGER.info("Skipping %s close result because open price is missing", ticker)
            continue

        open_text = _safe_text(trade.get("entry_time_local") or trade.get("open time"))
        open_timestamp = parse_local_datetime(open_text, timezone_info)
        if open_timestamp is None:
            LOGGER.info("Skipping %s close result because open time is invalid", ticker)
            continue

        close_price = fetch_close_price(ticker, direction, price_loader)
        market_window = load_trade_window_data(
            ticker=ticker,
            open_timestamp=open_timestamp,
            close_timestamp=local_timestamp,
            data_loader=data_loader,
        )
        if close_price is None:
            close_price = latest_close_from_window(market_window)
        tp_triggered, sl_triggered = calculate_tp_sl_triggers(
            market_window=market_window,
            direction=direction,
            take_profit=_to_float(trade.get("take_profit") or trade.get("Take Profit level")),
            stop_loss=_to_float(trade.get("stop_loss") or trade.get("Stop Loss level")),
        )
        profitable = calculate_profitability(
            direction=direction,
            open_price=open_price,
            close_price=close_price,
            tp_triggered=tp_triggered,
            sl_triggered=sl_triggered,
        )
        rows.append(
            {
                "Ticker": ticker,
                "open time": open_timestamp.strftime("%Y-%m-%d %H:%M"),
                "open price": smart_round_price(open_price),
                "direction of the bid (buy/sell)": direction,
                "close price": smart_round_price(close_price) if close_price else None,
                "close time": close_text,
                "TP triggered (yes/no)": yes_no(tp_triggered),
                "SL triggered (yes/no)": yes_no(sl_triggered),
                "profitable (yes/no)": yes_no(profitable),
            }
        )
    return rows


def build_trade_plan_rows(
    candidates: pd.DataFrame,
    ticker_metadata: pd.DataFrame,
    session_group: str,
    group_rule: dict[str, Any],
    local_timestamp: datetime,
    price_loader: Callable[..., float | None] = get_current_price,
) -> list[dict[str, Any]]:
    """Build validated trade-plan rows from candidate signals."""

    with timed_task(
        "build_trade_plan_rows",
        session_group=session_group,
        candidates=len(candidates),
    ):
        metadata_by_ticker = metadata_lookup(ticker_metadata)
        rows: list[dict[str, Any]] = []
        for _, candidate in candidates.iterrows():
            direction = str(candidate.get("direction", "")).strip().lower()
            if direction not in {"buy", "sell"}:
                LOGGER.info("Skipping neutral/non-tradable candidate direction=%s", direction)
                continue

            ticker = str(candidate.get("ticker", "")).strip()
            if not ticker:
                LOGGER.info("Skipping candidate with missing ticker")
                continue

            try:
                signal_strength = _to_float(candidate.get("signal_strength")) or 0.0
                min_strength = float(group_rule.get("min_signal_strength", 60))
                if signal_strength < min_strength:
                    LOGGER.info(
                        "Skipping %s because signal_strength %.2f is below %.2f",
                        ticker,
                        signal_strength,
                        min_strength,
                    )
                    continue
                with timed_task("fetch_entry_price", ticker=ticker):
                    entry_price = call_price_loader(
                        price_loader=price_loader,
                        ticker=ticker,
                        direction=direction,
                    )
                entry_value = _to_float(entry_price)
                if entry_value is None or entry_value <= 0:
                    LOGGER.info("Skipping %s because entry price is invalid", ticker)
                    continue
                analysis_price = _to_float(
                    candidate.get("analysis_price", candidate.get("current_price"))
                )
                atr_percent = _to_float(candidate.get("atr_percent_1d"))
                if analysis_price is None or atr_percent is None:
                    LOGGER.info(
                        "Skipping %s because analysis price or ATR percent is missing",
                        ticker,
                    )
                    continue
                with timed_task("validate_entry_price", ticker=ticker):
                    allowed, validation_reason, drift, _ = is_entry_price_still_valid(
                        analysis_price=analysis_price,
                        entry_price=entry_value,
                        atr_percent_1d=atr_percent,
                        direction=direction,
                    )
                if not allowed:
                    LOGGER.info("Skipping %s: %s", ticker, validation_reason)
                    continue

                atr_1d = _to_float(candidate.get("atr_1d"))
                with timed_task("calculate_entry_sl_tp", ticker=ticker):
                    sl_tp = calculate_daily_sl_tp(
                        current_price=entry_value,
                        direction=direction,
                        signal_strength=signal_strength,
                        atr_1d=atr_1d or 0.0,
                        sl_multiplier=float(group_rule["sl_multiplier"]),
                        tp_base_multiplier=float(group_rule["tp_base_multiplier"]),
                        tp_strength_multiplier=float(
                            group_rule["tp_strength_multiplier"]
                        ),
                    )
                if sl_tp["stop_loss"] is None or sl_tp["take_profit"] is None:
                    LOGGER.info("Skipping %s because SL/TP calculation failed", ticker)
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
                LOGGER.exception("Entry validation failed for %s", ticker)
                print(f"WARNING: skipped {ticker} at entry validation: {exc}", flush=True)
                continue
        return rows


def call_price_loader(
    price_loader: Callable[..., float | None],
    ticker: str,
    direction: str,
) -> float | None:
    """Fetch a quote, passing trade direction when the loader supports it."""

    try:
        signature = inspect.signature(price_loader)
    except (TypeError, ValueError):
        return price_loader(ticker)

    parameters = signature.parameters.values()
    accepts_side = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        or parameter.name == "side"
        for parameter in parameters
    )
    if accepts_side:
        return price_loader(ticker, side=direction)
    return price_loader(ticker)


def fetch_close_price(
    ticker: str,
    direction: str,
    price_loader: Callable[..., float | None],
) -> float | None:
    """Fetch the close quote using the side that exits the trade direction."""

    close_side = "sell" if direction == "buy" else "buy"
    with timed_task("fetch_close_price", ticker=ticker, side=close_side):
        return call_price_loader(price_loader, ticker, close_side)


def parse_local_datetime(value: str, timezone_info: Any) -> datetime | None:
    """Parse local timestamp text from generated trade-plan CSV files."""

    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone_info)
    return parsed.astimezone(timezone_info)


def load_trade_window_data(
    ticker: str,
    open_timestamp: datetime,
    close_timestamp: datetime,
    data_loader: Callable[..., pd.DataFrame] = load_symbol_data,
) -> pd.DataFrame:
    """Load intraday OHLCV data between trade open and close times."""

    if close_timestamp <= open_timestamp:
        return pd.DataFrame()
    begin_time = open_timestamp.astimezone(timezone.utc)
    end_time = close_timestamp.astimezone(timezone.utc)
    for timeframe in ("1m", "5m", "15m"):
        try:
            data = data_loader(
                symbol=ticker,
                timeframe=timeframe,
                begin_time=begin_time,
                end_time=end_time,
                provider="fallback",
            )
            return normalize_ohlcv_columns(data)
        except Exception as exc:
            LOGGER.info(
                "Could not load %s trade-window data for %s: %s",
                timeframe,
                ticker,
                exc,
            )
    return pd.DataFrame()


def latest_close_from_window(market_window: pd.DataFrame) -> float | None:
    """Return the latest close from market-window data, if available."""

    if market_window.empty or "close" not in market_window.columns:
        return None
    close_values = pd.to_numeric(market_window["close"], errors="coerce").dropna()
    if close_values.empty:
        return None
    return float(close_values.iloc[-1])


def calculate_tp_sl_triggers(
    market_window: pd.DataFrame,
    direction: str,
    take_profit: float | None,
    stop_loss: float | None,
) -> tuple[bool, bool]:
    """Return whether TP/SL levels were touched inside the trade window."""

    if market_window.empty:
        return False, False
    if "high" not in market_window.columns or "low" not in market_window.columns:
        return False, False
    high = pd.to_numeric(market_window.get("high"), errors="coerce")
    low = pd.to_numeric(market_window.get("low"), errors="coerce")
    tp_triggered = False
    sl_triggered = False
    if direction == "buy":
        if take_profit is not None:
            tp_triggered = bool((high >= take_profit).any())
        if stop_loss is not None:
            sl_triggered = bool((low <= stop_loss).any())
    elif direction == "sell":
        if take_profit is not None:
            tp_triggered = bool((low <= take_profit).any())
        if stop_loss is not None:
            sl_triggered = bool((high >= stop_loss).any())
    return tp_triggered, sl_triggered


def calculate_profitability(
    direction: str,
    open_price: float,
    close_price: float | None,
    tp_triggered: bool,
    sl_triggered: bool,
) -> bool:
    """Return the requested yes/no profitability result.

    If both TP and SL are observed in coarse OHLCV data, SL is treated as
    decisive because the exact intrabar order is unknown.
    """

    if sl_triggered:
        return False
    if tp_triggered:
        return True
    if close_price is None:
        return False
    if direction == "buy":
        return close_price > open_price
    if direction == "sell":
        return close_price < open_price
    return False


def yes_no(value: bool) -> str:
    return "yes" if value else "no"


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
    with timed_task("read_ticker_trading_times", path=path):
        if not path.exists():
            LOGGER.info("Ticker metadata CSV does not exist at %s", path)
            return pd.DataFrame(columns=METADATA_COLUMNS)
        return pd.read_csv(path)


def candidate_output_path(session_group: str, local_timestamp: datetime) -> Path:
    """Return group candidate filename."""

    return best_signals_dir(OUTPUT_DIR) / (
        f"best_signals_{session_group}_{local_timestamp:%Y-%m-%d_%H-%M}.csv"
    )


def trade_plan_output_path(session_group: str, local_timestamp: datetime) -> Path:
    """Return group trade-plan filename."""

    return trade_plans_dir(OUTPUT_DIR) / (
        f"trade_plan_{session_group}_{local_timestamp:%Y-%m-%d_%H-%M}.csv"
    )


def results_output_path(local_timestamp: datetime) -> Path:
    """Return close-result filename for a local timestamp."""

    return results_dir(OUTPUT_DIR) / f"results_{local_timestamp:%Y-%m-%d_%H-%M}.csv"


def find_latest_candidate_file(session_group: str, local_day: date) -> Path:
    """Find the latest candidate file for a group on a local date."""

    pattern = f"best_signals_{session_group}_{local_day:%Y-%m-%d}_*.csv"
    candidates = sorted(
        best_signals_dir(OUTPUT_DIR).glob(pattern),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(
            f"No candidate file found for {session_group} on {local_day}."
        )
    return candidates[0]


def find_latest_trade_plan_file(session_group: str, local_day: date) -> Path:
    """Find the latest trade-plan file for a group on a local date."""

    pattern = f"trade_plan_{session_group}_{local_day:%Y-%m-%d}_*.csv"
    plans = sorted(
        trade_plans_dir(OUTPUT_DIR).glob(pattern),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not plans:
        raise FileNotFoundError(
            f"No trade-plan file found for {session_group} on {local_day}."
        )
    return plans[0]


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

    with timed_task("execute_due_event", group=group_name, event_type=event_type):
        utc_timestamp = local_timestamp.astimezone(timezone.utc)
        if event_type == "analysis":
            run_analysis_for_group(group_name, rules, ticker_metadata, now=utc_timestamp)
        elif event_type == "open":
            run_trade_plan_for_group(group_name, rules, ticker_metadata, now=utc_timestamp)
        elif event_type == "close":
            print(
                f"Close event reached for {group_name} at "
                f"{local_timestamp:%Y-%m-%d %H:%M}. Writing result CSV.",
                flush=True,
            )
            run_close_results_for_groups([group_name], rules, local_timestamp)
            LOGGER.info("Close event reached for %s; result CSV handled", group_name)


def print_status(
    rules: dict[str, Any],
    ticker_metadata: pd.DataFrame,
    executed_events: set[str],
) -> None:
    """Print current scheduler status."""

    timezone_info = rules_timezone(rules)
    now = datetime.now(timezone_info)
    mt5_rules = rules.get("mt5", {})
    print(f"Status at {now:%Y-%m-%d %H:%M %Z}", flush=True)
    print(f"Runtime root: {OUTPUT_DIR}", flush=True)
    print(f"Logs directory: {logs_dir(OUTPUT_DIR)}", flush=True)
    print(f"Best signals directory: {best_signals_dir(OUTPUT_DIR)}", flush=True)
    print(f"Trade plans directory: {trade_plans_dir(OUTPUT_DIR)}", flush=True)
    print(f"Results directory: {results_dir(OUTPUT_DIR)}", flush=True)
    if isinstance(mt5_rules, dict):
        print(
            f"MT5 server: {mt5_rules.get('host')}:{mt5_rules.get('port')}",
            flush=True,
        )
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

    friday = datetime(2026, 6, 12, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
    due = due_session_events(rules, metadata, friday, set())
    assert any(event[1:] == ("forex_major", "analysis") for event in due)
    first_key = due[0][0]
    assert due_session_events(rules, metadata, friday, {first_key}) == []

    changed_rules = copy.deepcopy(rules)
    changed_rules["session_groups"]["forex_major"]["analysis_time"] = "09:01"
    changed_time = datetime(2026, 6, 12, 9, 1, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
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
    parser.add_argument(
        "command",
        nargs="?",
        choices=[UPDATE_COMMAND],
        help="Optional one-shot command. Use 'update' to refresh ticker metadata.",
    )
    parser.add_argument(
        "--debug-symbol",
        default=None,
        help="With 'update', print full MT5 metadata for this symbol.",
    )
    args = parser.parse_args()
    if args.self_test:
        run_self_test()
        return
    if args.command == UPDATE_COMMAND:
        ensure_runtime_directories(OUTPUT_DIR)
        log_path = setup_logging()
        print(f"Logging to {log_path}", flush=True)
        rules = load_session_rules()
        print(
            f"Using MT5 server {rules['mt5']['host']}:{rules['mt5']['port']}",
            flush=True,
        )
        with timed_task("one_shot_update_command"):
            update_ticker_trading_times(debug_symbol=args.debug_symbol)
        return

    ensure_runtime_directories(OUTPUT_DIR)
    log_path = setup_logging()
    print("Starting trade_signal_generator.py...", flush=True)
    print(f"Runtime root: {OUTPUT_DIR}", flush=True)
    print(f"Best signals directory: {best_signals_dir(OUTPUT_DIR)}", flush=True)
    print(f"Trade plans directory: {trade_plans_dir(OUTPUT_DIR)}", flush=True)
    print(f"Results directory: {results_dir(OUTPUT_DIR)}", flush=True)
    print(f"Logs directory: {logs_dir(OUTPUT_DIR)}", flush=True)
    print(f"Log file: {log_path}", flush=True)
    LOGGER.info("Starting trade_signal_generator.py")

    commands: queue.Queue[str] = queue.Queue()
    threading.Thread(target=console_listener, args=(commands,), daemon=True).start()
    print(
        "Console listener started. Commands typed during startup will be queued.",
        flush=True,
    )

    print("Loading session rules...", flush=True)
    with timed_task("startup_load_session_rules"):
        rules = load_session_rules()
        last_rules_mtime = rules_mtime()
    print(f"Session rules loaded from {session_rules_path()}.", flush=True)
    print(
        f"Using MT5 server {rules['mt5']['host']}:{rules['mt5']['port']}",
        flush=True,
    )

    print(
        "Reading existing ticker_trading_times.csv. MT5 metadata is refreshed "
        "only when you type 'update'.",
        flush=True,
    )
    with timed_task("startup_read_ticker_metadata"):
        ticker_metadata = _safe_read_existing_metadata()
    print(
        f"Startup metadata ready: {len(ticker_metadata)} tickers loaded.",
        flush=True,
    )
    executed_events: set[str] = set()

    print("trade_signal_generator.py is ready.", flush=True)
    print_console_ready()

    while True:
        timeout = min(60, int(rules.get("rules_reload_interval_seconds", 60)))
        try:
            command = commands.get(timeout=max(1, timeout))
        except queue.Empty:
            command = None

        if command is not None:
            normalized_command = command.strip().lower()
            with timed_task("handle_console_command", command=normalized_command):
                if normalized_command in {QUIT_COMMAND, LEGACY_EXIT_COMMAND}:
                    print("Stopping trade_signal_generator.py.", flush=True)
                    LOGGER.info("Stopping scheduler after console command")
                    return
                if normalized_command == TRIGGER_COMMAND:
                    try:
                        run_analysis_for_all_enabled_groups(rules, ticker_metadata)
                    except Exception as exc:
                        LOGGER.exception("Immediate analysis command failed")
                        print(f"ERROR running immediate analysis: {exc}", flush=True)
                elif normalized_command == UPDATE_COMMAND:
                    try:
                        ticker_metadata = update_ticker_trading_times()
                    except Exception as exc:
                        LOGGER.exception("Ticker metadata update command failed")
                        print(f"ERROR updating ticker metadata: {exc}", flush=True)
                elif normalized_command == RELOAD_COMMAND:
                    rules = load_session_rules(previous_rules=rules)
                    last_rules_mtime = rules_mtime()
                    print("Reloaded session rules.", flush=True)
                elif normalized_command == STATUS_COMMAND:
                    print_status(rules, ticker_metadata, executed_events)
                elif normalized_command in {SIGNALS_COMMAND, SIGNALS_ALIAS}:
                    try:
                        run_trade_plans_for_all_available_groups(
                            rules,
                            ticker_metadata,
                        )
                    except Exception as exc:
                        LOGGER.exception("Manual signals command failed")
                        print(f"ERROR generating trade plans: {exc}", flush=True)
                else:
                    print("unknown command", flush=True)
            print_console_ready()

        current_mtime = rules_mtime()
        action_completed = False
        if current_mtime is not None and current_mtime != last_rules_mtime:
            with timed_task("auto_reload_changed_session_rules"):
                rules = load_session_rules(previous_rules=rules)
                last_rules_mtime = current_mtime
            print("Reloaded changed session rules.", flush=True)
            action_completed = True

        timezone_info = rules_timezone(rules)
        local_timestamp = datetime.now(timezone_info).replace(second=0, microsecond=0)
        due_events = due_session_events(
            rules,
            ticker_metadata,
            local_timestamp,
            executed_events,
        )
        close_events = [event for event in due_events if event[2] == "close"]
        non_close_events = [event for event in due_events if event[2] != "close"]

        if close_events:
            close_groups = [group_name for _, group_name, _ in close_events]
            try:
                run_close_results_for_groups(close_groups, rules, local_timestamp)
            except Exception as exc:
                LOGGER.exception("Scheduled close-result generation failed")
                print(f"ERROR writing close results: {exc}", flush=True)
            for event_key, _, _ in close_events:
                executed_events.add(event_key)
            action_completed = True

        for event_key, group_name, event_type in non_close_events:
            try:
                execute_due_event(
                    group_name,
                    event_type,
                    rules,
                    ticker_metadata,
                    local_timestamp,
                )
            except Exception as exc:
                LOGGER.exception("Scheduled event failed")
                print(f"ERROR running {event_type} for {group_name}: {exc}", flush=True)
            executed_events.add(event_key)
            action_completed = True

        if action_completed:
            print_console_ready()


def _safe_read_existing_metadata() -> pd.DataFrame:
    """Read existing ticker metadata without contacting MT5."""

    with timed_task("safe_read_existing_metadata"):
        metadata = read_ticker_trading_times()
        if metadata.empty:
            print(
                "WARNING: ticker_trading_times.csv is missing or empty. "
                "Type 'update' to fetch MT5 symbol metadata.",
                flush=True,
            )
            LOGGER.warning("ticker_trading_times.csv missing or empty at startup")
        return metadata


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
    return (
        symbol_key[:3] in classification_rules.CURRENCY_CODES
        and symbol_key[3:] in classification_rules.CURRENCY_CODES
    )


def _symbol_looks_like_commodity(symbol_key: str) -> bool:
    commodity_codes = {
        "BRENT",
        "COCOA",
        "COFFEE",
        "CORN",
        "COTTON",
        "GOLD",
        "NGAS",
        "OIL",
        "SILVER",
        "SOYBEAN",
        "SUGAR",
        "WHEAT",
        "WTI",
        "XAG",
        "XAU",
    }
    return any(symbol_key.startswith(code) for code in commodity_codes)


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
