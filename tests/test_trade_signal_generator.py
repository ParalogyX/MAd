from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from find_signal import calculate_daily_sl_tp
from trade_signal_generator import (
    DEFAULT_SESSION_RULES,
    SESSION_RULES_FILE,
    analyze_group_tickers,
    build_trade_plan_rows,
    classify_ticker,
    due_session_events,
    is_entry_price_still_valid,
    load_session_rules,
    parse_trading_days,
)


def test_session_rules_loading_and_invalid_json_fallback(tmp_path):
    rules_path = tmp_path / SESSION_RULES_FILE
    rules = load_session_rules(rules_path)

    assert rules_path.exists()
    assert rules["timezone"] == "Europe/Amsterdam"

    rules_path.write_text("{ invalid", encoding="utf-8")
    assert load_session_rules(rules_path, previous_rules=rules) is rules


def test_trading_day_parser():
    assert parse_trading_days("mon-fri") == {0, 1, 2, 3, 4}
    assert parse_trading_days("mon-sun") == {0, 1, 2, 3, 4, 5, 6}
    assert parse_trading_days("mon,wed,fri") == {0, 2, 4}


def test_ticker_classification():
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


def test_scheduler_due_events_rules():
    metadata = pd.DataFrame(
        [
            {"ticker": "EURUSD", "session_group": "forex_major"},
            {"ticker": "BTCUSD", "session_group": "crypto_24_7"},
        ]
    )
    local_zone = ZoneInfo("Europe/Amsterdam")
    friday = datetime(2026, 6, 12, 8, 45, tzinfo=local_zone)
    sunday = datetime(2026, 6, 14, 8, 45, tzinfo=local_zone)

    rules = DEFAULT_SESSION_RULES.copy()
    rules = {
        **rules,
        "session_groups": {
            key: value.copy()
            for key, value in DEFAULT_SESSION_RULES["session_groups"].items()
        },
    }
    rules["session_groups"]["crypto_24_7"]["enabled"] = False
    assert due_session_events(rules, metadata, friday, set()) != []
    assert all(event[1] != "crypto_24_7" for event in due_session_events(
        rules,
        metadata,
        friday,
        set(),
    ))
    assert due_session_events(rules, metadata, sunday, set()) == []

    due = due_session_events(rules, metadata, friday, set())
    executed_key = due[0][0]
    assert due_session_events(rules, metadata, friday, {executed_key}) == []

    changed_rules = {
        **rules,
        "session_groups": {
            key: value.copy() for key, value in rules["session_groups"].items()
        },
    }
    changed_rules["session_groups"]["forex_major"]["analysis_time"] = "08:46"
    changed_time = datetime(2026, 6, 12, 8, 46, tzinfo=local_zone)
    changed_due = due_session_events(
        changed_rules,
        metadata,
        changed_time,
        {executed_key},
    )
    assert any(event[1:] == ("forex_major", "analysis") for event in changed_due)


def test_entry_price_validation():
    assert is_entry_price_still_valid(100, 100.1, 0.03, "buy")[0] is True
    assert is_entry_price_still_valid(100, 104, 0.03, "buy")[0] is False

    strict = is_entry_price_still_valid(100, 100.7, 0.10, "buy")
    loose = is_entry_price_still_valid(100, 99.3, 0.10, "buy")

    assert strict[0] is False
    assert loose[0] is True


def test_trade_plan_uses_entry_price_and_session_multipliers():
    crypto_rule = DEFAULT_SESSION_RULES["session_groups"]["crypto_24_7"]
    forex_rule = DEFAULT_SESSION_RULES["session_groups"]["forex_major"]

    crypto_sl_tp = calculate_daily_sl_tp(
        100,
        "buy",
        80,
        5,
        sl_multiplier=crypto_rule["sl_multiplier"],
        tp_base_multiplier=crypto_rule["tp_base_multiplier"],
        tp_strength_multiplier=crypto_rule["tp_strength_multiplier"],
    )
    forex_sl_tp = calculate_daily_sl_tp(
        100,
        "buy",
        80,
        5,
        sl_multiplier=forex_rule["sl_multiplier"],
        tp_base_multiplier=forex_rule["tp_base_multiplier"],
        tp_strength_multiplier=forex_rule["tp_strength_multiplier"],
    )

    assert crypto_sl_tp["stop_loss"] > forex_sl_tp["stop_loss"]

    candidate = pd.DataFrame(
        [
            {
                "ticker": "EURUSD",
                "direction": "buy",
                "current_price": 100,
                "analysis_price": 100,
                "atr_percent_1d": 0.03,
                "atr_1d": 3,
                "signal_strength": 80,
                "reason": "test",
            }
        ]
    )
    rows = build_trade_plan_rows(
        candidates=candidate,
        ticker_metadata=pd.DataFrame(
            [{"ticker": "EURUSD", "description": "Euro", "session_group": "forex_major"}]
        ),
        session_group="forex_major",
        group_rule=forex_rule,
        local_timestamp=datetime(2026, 6, 12, 9, 5, tzinfo=ZoneInfo("Europe/Amsterdam")),
        price_loader=lambda ticker: 100.1,
    )

    assert len(rows) == 1
    assert rows[0]["entry_price"] == 100.1
    assert rows[0]["analysis_price"] == 100


def test_neutral_signals_and_failed_tickers_do_not_stop_group():
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
    rows = build_trade_plan_rows(
        candidates=candidate,
        ticker_metadata=pd.DataFrame(),
        session_group="forex_major",
        group_rule=DEFAULT_SESSION_RULES["session_groups"]["forex_major"],
        local_timestamp=datetime(2026, 6, 12, 9, 5, tzinfo=ZoneInfo("Europe/Amsterdam")),
        price_loader=lambda ticker: 100,
    )
    assert rows == []

    def processor(ticker):
        if ticker == "BAD":
            raise RuntimeError("synthetic failure")
        return {"ticker": ticker, "direction": "neutral"}

    analysis_rows = analyze_group_tickers(["OK", "BAD"], processor=processor)

    assert len(analysis_rows) == 2
    assert analysis_rows[1]["ticker"] == "BAD"
    assert analysis_rows[1]["direction"] == "neutral"
