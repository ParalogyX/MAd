from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from find_signal import calculate_daily_sl_tp
from trade_signal_generator import (
    DEFAULT_SESSION_RULES,
    SESSION_RULES_FILE,
    analyze_group_tickers,
    build_ticker_trading_times,
    build_trade_plan_rows,
    classify_ticker,
    classify_ticker_from_metadata,
    convert_exchange_session_to_local,
    due_session_events,
    is_entry_price_still_valid,
    load_classification_overrides,
    load_mt5_symbol_sessions,
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


def test_extended_ticker_classification_examples():
    examples = [
        ("Adobe", "Adobe Systems Inc.", {}, ("us_stock", "us_stock_index")),
        ("nVidia", "nVidia Corp.", {}, ("us_stock", "us_stock_index")),
        ("Amazon", "Amazon.com Inc.", {}, ("us_stock", "us_stock_index")),
        ("SPY", "SPDR S&P 500 Trust ETF", {}, ("us_etf", "us_stock_index")),
        ("Adidas", "Adidas AG", {}, ("europe_stock", "europe_stock_index")),
        ("Airbus", "Airbus SE", {}, ("europe_stock", "europe_stock_index")),
        ("SAP", "SAP SE", {}, ("europe_stock", "europe_stock_index")),
        ("Siemens", "Siemens AG", {}, ("europe_stock", "europe_stock_index")),
        ("FTI", "Netherlands 25 (AEX)", {}, ("europe_index", "europe_stock_index")),
        ("NQCash", "US NDAQ 100", {}, ("us_index", "us_stock_index")),
        ("YM", "US DJ 30", {}, ("us_index", "us_stock_index")),
        ("NGASCash", "Natural Gas Cash", {}, ("commodity", "commodity_us")),
        ("COCOA", "Cocoa Cash", {}, ("commodity", "commodity_us")),
        ("WHEAT", "Wheat Cash", {}, ("commodity", "commodity_us")),
        ("EURUSD", "Euro vs US dollar", {}, ("forex", "forex_major")),
        ("USDCLP", "US Dollar vs Chile Peso", {}, ("forex", "forex_exotic")),
        ("BTCUSD", "Bitcoin vs US Dollar", {}, ("crypto", "crypto_24_7")),
        ("XVGUSD", "Verge crypto", {}, ("crypto", "crypto_24_7")),
        ("HSI", "China 50 (HSI) Cash", {}, ("asia_index", "asia_index")),
        ("NIY", "Japan 225 (Nikkei)", {}, ("asia_index", "asia_index")),
    ]

    for symbol, description, metadata, expected in examples:
        ticker_type, session_group, reason = classify_ticker_from_metadata(
            symbol,
            description,
            metadata,
        )
        assert (ticker_type, session_group) == expected
        assert reason


def test_manual_override_beats_automatic_classification(tmp_path):
    override_path = tmp_path / "ticker_classification_overrides.csv"
    override_path.write_text(
        "ticker,ticker_type,session_group,start_trade_time,end_trade_time,"
        "trading_days,description_override,reason\n"
        "Adobe,europe_stock,europe_stock_index,09:00,17:30,mon-fri,"
        "Adobe override,Manual test override\n",
        encoding="utf-8",
    )
    overrides = load_classification_overrides(override_path)
    data = build_ticker_trading_times(
        [{"name": "Adobe", "description": "Adobe Systems Inc."}],
        overrides=overrides,
        timestamp_utc=datetime(2026, 6, 12, tzinfo=ZoneInfo("UTC")),
    )

    row = data.iloc[0]
    assert row["ticker_type"] == "europe_stock"
    assert row["session_group"] == "europe_stock_index"
    assert row["classification_source"] == "manual_override"
    assert row["description"] == "Adobe override"


def test_invalid_or_missing_metadata_does_not_crash():
    data = build_ticker_trading_times(
        [{"name": "MysteryInstrument"}],
        timestamp_utc=datetime(2026, 6, 12, tzinfo=ZoneInfo("UTC")),
    )

    assert len(data) == 1
    assert data.iloc[0]["ticker_type"] == "unknown"
    assert data.iloc[0]["classification_reason"]


def test_mt5_session_csv_is_used(tmp_path):
    session_path = tmp_path / "mt5_symbol_sessions.csv"
    session_path.write_text(
        "symbol,day_of_week,session_index,from_seconds,to_seconds,from_time,to_time\n"
        "EURUSD,MONDAY,0,300,86100,00:05,23:55\n"
        "EURUSD,TUESDAY,0,300,86100,00:05,23:55\n"
        "EURUSD,WEDNESDAY,0,300,86100,00:05,23:55\n"
        "EURUSD,THURSDAY,0,300,86100,00:05,23:55\n"
        "EURUSD,FRIDAY,0,300,86100,00:05,23:55\n",
        encoding="utf-8",
    )
    session_map = load_mt5_symbol_sessions(session_path)
    data = build_ticker_trading_times(
        [{"name": "EURUSD", "description": "Euro vs US dollar"}],
        session_map=session_map,
        timestamp_utc=datetime(2026, 6, 12, tzinfo=ZoneInfo("UTC")),
    )
    row = data.iloc[0]

    assert row["start_trade_time"] == "00:05"
    assert row["end_trade_time"] == "23:55"
    assert row["trading_days"] == "mon-fri"
    assert row["classification_source"] == "mt5_sessions"
    assert "mon" in row["raw_sessions"]


def test_timezone_conversion():
    day = datetime(2026, 6, 12).date()

    assert convert_exchange_session_to_local(
        "09:30",
        "16:00",
        "America/New_York",
        "Europe/Amsterdam",
        day,
    ) == ("15:30", "22:00")
    assert convert_exchange_session_to_local(
        "09:00",
        "17:30",
        "Europe/Amsterdam",
        "Europe/Amsterdam",
        day,
    ) == ("09:00", "17:30")
    assert convert_exchange_session_to_local(
        "08:00",
        "16:30",
        "Europe/London",
        "Europe/Amsterdam",
        day,
    ) == ("09:00", "17:30")


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
