"""Python configuration for the scheduler/trade-plan script.

This module contains hardcoded defaults only. It does not read or write the
user-editable ``session_rules.json`` file.
"""

from __future__ import annotations

import os
from pathlib import Path

from investment_adviser.config import (
    MT5_DEFAULT_HOST,
    MT5_DEFAULT_MAX_BARS,
    MT5_DEFAULT_PORT,
)
from runtime_paths import output_root


def env_int(name: str, default: int) -> int:
    """Return an integer environment value, falling back on invalid input."""

    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    """Return a float environment value, falling back on invalid input."""

    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except ValueError:
        return default


def env_bool(name: str, default: bool) -> bool:
    """Return a boolean environment value from common true/false strings."""

    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = output_root(PROJECT_ROOT)

SESSION_RULES_FILE = "session_rules.json"
TICKER_TRADING_TIMES_FILE = "ticker_trading_times.csv"
MT5_SYMBOL_SESSIONS_FILE = "mt5_symbol_sessions.csv"
CLASSIFICATION_OVERRIDES_FILE = "ticker_classification_overrides.csv"

LOG_PREFIX = "trade_signal_generator"
LOG_RETENTION_DAYS = 30

TRIGGER_COMMAND = "now"
UPDATE_COMMAND = "update"
RELOAD_COMMAND = "reload"
STATUS_COMMAND = "status"
SIGNALS_COMMAND = "signals"
SIGNALS_ALIAS = "sig"
TEST_TRADE_COMMAND = "test_trade"
QUIT_COMMAND = "quit"
LEGACY_EXIT_COMMAND = "stop"

DEFAULT_TIMEZONE = "Europe/Amsterdam"
DEFAULT_MT5_HOST = os.getenv("MT5_HOST", MT5_DEFAULT_HOST)
DEFAULT_MT5_PORT = env_int("MT5_PORT", MT5_DEFAULT_PORT)
DEFAULT_MT5_MAX_BARS = env_int("MT5_MAX_BARS", MT5_DEFAULT_MAX_BARS)

AUTO_TRADE_ENABLED = env_bool("M_AD_AUTO_TRADE_ENABLED", True)
ALLOW_LIVE_TRADING = env_bool("M_AD_ALLOW_LIVE_TRADING", False)
TARGET_TRADE_NOTIONAL_EUR = env_float("M_AD_TARGET_TRADE_NOTIONAL_EUR", 1000.0)
TEST_TRADE_NOTIONAL_EUR = env_float("M_AD_TEST_TRADE_NOTIONAL_EUR", 50.0)
TEST_TRADE_HOLD_SECONDS = env_int("M_AD_TEST_TRADE_HOLD_SECONDS", 60)
MT5_STRATEGY_MAGIC = env_int("M_AD_MT5_STRATEGY_MAGIC", 26061801)
MT5_TEST_MAGIC = env_int("M_AD_MT5_TEST_MAGIC", 26061802)
MT5_TEST_SYMBOL = os.getenv("M_AD_MT5_TEST_SYMBOL", "BTCUSD")
APP_ORDER_COMMENT_PREFIX = os.getenv("M_AD_ORDER_COMMENT_PREFIX", "MAd")
EXECUTION_LEDGER_FILE = os.getenv(
    "M_AD_EXECUTION_LEDGER_FILE",
    "execution_ledger.sqlite3",
)

METADATA_COLUMNS = [
    "ticker",
    "description",
    "start_trade_time",
    "end_trade_time",
    "trading_days",
    "ticker_type",
    "session_group",
    "classification_source",
    "classification_reason",
    "exchange",
    "country",
    "category",
    "path",
    "currency_base",
    "currency_profit",
    "raw_sessions",
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

RESULT_COLUMNS = [
    "Ticker",
    "open time",
    "open price",
    "direction of the bid (buy/sell)",
    "close price",
    "close time",
    "TP triggered (yes/no)",
    "SL triggered (yes/no)",
    "profitable (yes/no)",
]

DEFAULT_SESSION_RULES: dict[str, object] = {
    "timezone": DEFAULT_TIMEZONE,
    "mt5": {
        "host": DEFAULT_MT5_HOST,
        "port": DEFAULT_MT5_PORT,
        "max_bars": DEFAULT_MT5_MAX_BARS,
    },
    "best_signal_limit": 10,
    "entry_check_minutes_before_open": 0,
    "rules_reload_interval_seconds": 60,
    "session_groups": {
        "crypto_24_7": {
            "enabled": True,
            "analysis_time": "15:00",
            "open_time": "15:10",
            "close_time": "21:45",
            "trading_days": "mon-sun",
            "min_signal_strength": 80,
            "sl_multiplier": 0.40,
            "tp_base_multiplier": 0.50,
            "tp_strength_multiplier": 0.20,
        },
        "forex_major": {
            "enabled": True,
            "analysis_time": "09:00",
            "open_time": "09:05",
            "close_time": "21:45",
            "trading_days": "mon-fri",
            "min_signal_strength": 70,
            "sl_multiplier": 0.45,
            "tp_base_multiplier": 0.60,
            "tp_strength_multiplier": 0.25,
        },
        "forex_exotic": {
            "enabled": True,
            "analysis_time": "09:00",
            "open_time": "09:15",
            "close_time": "18:30",
            "trading_days": "mon-fri",
            "min_signal_strength": 75,
            "sl_multiplier": 0.45,
            "tp_base_multiplier": 0.55,
            "tp_strength_multiplier": 0.20,
        },
        "europe_stock_index": {
            "enabled": True,
            "analysis_time": "09:00",
            "open_time": "09:10",
            "close_time": "17:20",
            "trading_days": "mon-fri",
            "min_signal_strength": 80,
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
            "min_signal_strength": 85,
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
            "min_signal_strength": 70,
            "sl_multiplier": 0.40,
            "tp_base_multiplier": 0.50,
            "tp_strength_multiplier": 0.20,
        },
        "asia_index": {
            "enabled": False,
            "analysis_time": "01:30",
            "open_time": "02:15",
            "close_time": "08:30",
            "trading_days": "mon-fri",
            "min_signal_strength": 60,
            "sl_multiplier": 0.40,
            "tp_base_multiplier": 0.50,
            "tp_strength_multiplier": 0.20,
        },
        "israel_index": {
            "enabled": False,
            "analysis_time": "08:30",
            "open_time": "09:00",
            "close_time": "16:00",
            "trading_days": "sun-thu",
            "min_signal_strength": 60,
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
            "min_signal_strength": 60,
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

GROUP_DEFAULT_WINDOWS = {
    "crypto_24_7": ("00:00", "23:59", "mon-sun"),
    "forex_major": ("00:05", "23:55", "mon-fri"),
    "forex_exotic": ("00:05", "23:55", "mon-fri"),
    "europe_stock_index": ("09:00", "17:30", "mon-fri"),
    "us_stock_index": ("15:30", "22:00", "mon-fri"),
    "commodity_us": ("15:00", "21:45", "mon-fri"),
    "asia_index": ("02:15", "08:30", "mon-fri"),
    "israel_index": ("09:00", "16:00", "sun-thu"),
    "unknown": ("", "", "unknown"),
}
