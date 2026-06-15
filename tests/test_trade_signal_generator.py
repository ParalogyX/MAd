import copy
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from find_signal import calculate_daily_sl_tp
from trade_signal_generator import (
    DEFAULT_SESSION_RULES,
    SESSION_RULES_FILE,
    analyze_group_tickers,
    build_ticker_trading_times,
    build_close_result_rows,
    call_price_loader,
    build_trade_plan_rows,
    candidate_output_path,
    classify_ticker,
    classify_ticker_from_metadata,
    convert_exchange_session_to_local,
    due_session_events,
    due_session_events_since,
    is_entry_price_still_valid,
    load_classification_overrides,
    load_mt5_symbol_sessions,
    load_session_rules,
    parse_trading_days,
    read_ticker_trading_times,
    run_analysis_for_group,
    run_close_results_for_groups,
    run_trade_plans_for_all_available_groups,
    results_output_path,
    trade_plan_output_path,
)
import trade_signal_generator as tsg


def test_session_rules_loading_and_invalid_json_fallback(tmp_path):
    rules_path = tmp_path / SESSION_RULES_FILE
    rules = load_session_rules(rules_path)

    assert rules_path.exists()
    assert rules["timezone"] == "Europe/Amsterdam"
    assert {"host", "port", "max_bars"} <= set(rules["mt5"])

    rules_path.write_text("{ invalid", encoding="utf-8")
    assert load_session_rules(rules_path, previous_rules=rules) is rules


def test_generated_output_paths_are_split_by_type(monkeypatch, tmp_path):
    monkeypatch.setattr(tsg, "OUTPUT_DIR", tmp_path)
    local_time = datetime(2026, 6, 12, 9, 5, tzinfo=ZoneInfo("Europe/Amsterdam"))

    candidate_path = candidate_output_path("forex_major", local_time)
    trade_path = trade_plan_output_path("forex_major", local_time)

    assert candidate_path.parent == tmp_path / "Best signals"
    assert trade_path.parent == tmp_path / "Trade plans"
    assert results_output_path(local_time).parent == tmp_path / "Results"
    assert candidate_path.name.startswith("best_signals_forex_major_")
    assert trade_path.name.startswith("trade_plan_forex_major_")


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
    rules["session_groups"]["forex_major"]["analysis_time"] = "08:45"
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


def test_scheduler_catches_event_missed_while_busy():
    metadata = pd.DataFrame(
        [
            {"ticker": "BTCUSD", "session_group": "crypto_24_7"},
            {"ticker": "AAPL", "session_group": "us_stock_index"},
        ]
    )
    local_zone = ZoneInfo("Europe/Amsterdam")
    rules = copy.deepcopy(DEFAULT_SESSION_RULES)
    for group_name, group_rule in rules["session_groups"].items():
        group_rule["enabled"] = group_name in {"crypto_24_7", "us_stock_index"}
    rules["session_groups"]["crypto_24_7"]["open_time"] = "15:10"

    previous_check = datetime(2026, 6, 12, 15, 9, tzinfo=local_zone)
    current_check = datetime(2026, 6, 12, 15, 12, tzinfo=local_zone)

    assert due_session_events(rules, metadata, current_check, set()) == []

    missed_events = due_session_events_since(
        rules,
        metadata,
        previous_check,
        current_check,
        set(),
    )

    assert any(
        event[2:] == ("crypto_24_7", "open")
        and event[0] == datetime(2026, 6, 12, 15, 10, tzinfo=local_zone)
        for event in missed_events
    )
    executed_key = next(
        event[1]
        for event in missed_events
        if event[2:] == ("crypto_24_7", "open")
    )
    assert all(
        event[2:] != ("crypto_24_7", "open")
        for event in due_session_events_since(
            rules,
            metadata,
            previous_check,
            current_check,
            {executed_key},
        )
    )


def test_entry_price_validation():
    assert is_entry_price_still_valid(100, 100.1, 0.03, "buy")[0] is True
    assert is_entry_price_still_valid(100, 104, 0.03, "buy")[0] is False

    strict = is_entry_price_still_valid(100, 100.7, 0.10, "buy")
    loose = is_entry_price_still_valid(100, 99.3, 0.10, "buy")

    assert strict[0] is False
    assert loose[0] is True


def test_side_aware_price_loader_receives_direction():
    calls = []

    def side_aware_loader(ticker, side=None):
        calls.append((ticker, side))
        return 1.2

    def one_argument_loader(ticker):
        calls.append((ticker, None))
        return 1.1

    assert call_price_loader(side_aware_loader, "EURUSD", "buy") == 1.2
    assert call_price_loader(one_argument_loader, "GBPUSD", "sell") == 1.1
    assert calls == [("EURUSD", "buy"), ("GBPUSD", None)]


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


def test_startup_reads_existing_metadata_without_update(monkeypatch, tmp_path):
    monkeypatch.setattr(tsg, "OUTPUT_DIR", tmp_path)
    metadata_path = tmp_path / "ticker_trading_times.csv"
    metadata_path.write_text(
        "ticker,description,session_group\nEURUSD,Euro,forex_major\n",
        encoding="utf-8",
    )

    def fail_update(*args, **kwargs):
        raise AssertionError("startup must not refresh MT5 metadata")

    monkeypatch.setattr(tsg, "update_ticker_trading_times", fail_update)
    data = tsg._safe_read_existing_metadata()

    assert list(data["ticker"]) == ["EURUSD"]


def test_manual_signals_command_skips_missing_candidates(monkeypatch, tmp_path):
    monkeypatch.setattr(tsg, "OUTPUT_DIR", tmp_path)
    rules = copy.deepcopy(DEFAULT_SESSION_RULES)
    metadata = pd.DataFrame([{"ticker": "EURUSD", "session_group": "forex_major"}])

    output_paths = run_trade_plans_for_all_available_groups(
        rules,
        metadata,
        now=datetime(2026, 6, 12, 9, 5, tzinfo=ZoneInfo("UTC")),
        price_loader=lambda ticker: 100.0,
    )

    assert output_paths == []


def test_manual_signals_command_generates_existing_candidate(monkeypatch, tmp_path):
    monkeypatch.setattr(tsg, "OUTPUT_DIR", tmp_path)
    rules = copy.deepcopy(DEFAULT_SESSION_RULES)
    for group_name, group_rule in rules["session_groups"].items():
        group_rule["enabled"] = group_name == "forex_major"

    local_time = datetime(2026, 6, 12, 9, 5, tzinfo=ZoneInfo("Europe/Amsterdam"))
    candidate_path = tsg.candidate_output_path("forex_major", local_time)
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
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
    ).to_csv(candidate_path, index=False)
    metadata = pd.DataFrame(
        [{"ticker": "EURUSD", "description": "Euro", "session_group": "forex_major"}]
    )

    output_paths = run_trade_plans_for_all_available_groups(
        rules,
        metadata,
        now=local_time.astimezone(ZoneInfo("UTC")),
        price_loader=lambda ticker: 100.1,
    )

    assert len(output_paths) == 1
    trade_plan = pd.read_csv(output_paths[0])
    assert list(trade_plan["ticker"]) == ["EURUSD"]


def test_min_signal_strength_filters_trade_plan_only(monkeypatch, tmp_path):
    monkeypatch.setattr(tsg, "OUTPUT_DIR", tmp_path)
    rules = copy.deepcopy(DEFAULT_SESSION_RULES)
    rules["session_groups"]["forex_major"]["min_signal_strength"] = 60
    metadata = pd.DataFrame(
        [
            {"ticker": "LOW", "session_group": "forex_major"},
            {"ticker": "HIGH", "session_group": "forex_major"},
        ]
    )

    def processor(ticker):
        strength = 59 if ticker == "LOW" else 60
        return {
            "ticker": ticker,
            "current_price": 100,
            "direction": "buy",
            "signal_strength": strength,
            "reason": "test",
            "timestamp_utc": "2026-06-12T07:00:00+00:00",
            "atr_1d": 3,
            "atr_percent_1d": 0.03,
            "stop_loss": 99,
            "take_profit": 101,
        }

    output_path = run_analysis_for_group(
        "forex_major",
        rules,
        metadata,
        now=datetime(2026, 6, 12, 7, 0, tzinfo=ZoneInfo("UTC")),
        processor=processor,
    )

    candidates = pd.read_csv(output_path)
    assert list(candidates["ticker"]) == ["HIGH", "LOW"]

    trade_paths = run_trade_plans_for_all_available_groups(
        rules,
        metadata,
        now=datetime(2026, 6, 12, 7, 5, tzinfo=ZoneInfo("UTC")),
        price_loader=lambda ticker: 100.1,
    )
    trade_plan = pd.read_csv(trade_paths[0])
    assert list(trade_plan["ticker"]) == ["HIGH"]


def test_close_result_rows_detect_tp_sl_and_profitability():
    trade_plan = pd.DataFrame(
        [
            {
                "ticker": "BUYTP",
                "entry_time_local": "2026-06-12 09:05",
                "entry_price": 100,
                "direction": "buy",
                "take_profit": 105,
                "stop_loss": 95,
            },
            {
                "ticker": "SELLSL",
                "entry_time_local": "2026-06-12 09:05",
                "entry_price": 100,
                "direction": "sell",
                "take_profit": 95,
                "stop_loss": 105,
            },
            {
                "ticker": "BUYWIN",
                "entry_time_local": "2026-06-12 09:05",
                "entry_price": 100,
                "direction": "buy",
                "take_profit": 110,
                "stop_loss": 90,
            },
        ]
    )

    def price_loader(ticker, side=None):
        return {"BUYTP": 103, "SELLSL": 99, "BUYWIN": 102}[ticker]

    def data_loader(symbol, timeframe, begin_time, end_time, provider="fallback"):
        values = {
            "BUYTP": (100, 106, 99, 103),
            "SELLSL": (100, 106, 98, 99),
            "BUYWIN": (100, 103, 99, 102),
        }[symbol]
        return pd.DataFrame(
            [
                {
                    "timestamp": begin_time,
                    "open": values[0],
                    "high": values[1],
                    "low": values[2],
                    "close": values[3],
                    "volume": 1,
                }
            ]
        )

    rows = build_close_result_rows(
        trade_plan,
        datetime(2026, 6, 12, 21, 45, tzinfo=ZoneInfo("Europe/Amsterdam")),
        price_loader=price_loader,
        data_loader=data_loader,
    )

    by_ticker = {row["Ticker"]: row for row in rows}
    assert by_ticker["BUYTP"]["TP triggered (yes/no)"] == "yes"
    assert by_ticker["BUYTP"]["SL triggered (yes/no)"] == "no"
    assert by_ticker["BUYTP"]["profitable (yes/no)"] == "yes"
    assert by_ticker["SELLSL"]["SL triggered (yes/no)"] == "yes"
    assert by_ticker["SELLSL"]["profitable (yes/no)"] == "no"
    assert by_ticker["BUYWIN"]["TP triggered (yes/no)"] == "no"
    assert by_ticker["BUYWIN"]["profitable (yes/no)"] == "yes"


def test_close_results_for_groups_combines_trade_plans(monkeypatch, tmp_path):
    monkeypatch.setattr(tsg, "OUTPUT_DIR", tmp_path)
    rules = copy.deepcopy(DEFAULT_SESSION_RULES)
    local_time = datetime(2026, 6, 12, 21, 45, tzinfo=ZoneInfo("Europe/Amsterdam"))
    for group_name in ("forex_major", "commodity_us"):
        path = tsg.trade_plan_output_path(group_name, local_time)
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [
                {
                    "ticker": group_name,
                    "entry_time_local": "2026-06-12 09:05",
                    "entry_price": 100,
                    "direction": "buy",
                    "take_profit": 110,
                    "stop_loss": 90,
                }
            ]
        ).to_csv(path, index=False)

    def data_loader(symbol, timeframe, begin_time, end_time, provider="fallback"):
        return pd.DataFrame(
            [
                {
                    "timestamp": begin_time,
                    "open": 100,
                    "high": 103,
                    "low": 99,
                    "close": 102,
                    "volume": 1,
                }
            ]
        )

    output_path = run_close_results_for_groups(
        ["forex_major", "commodity_us"],
        rules,
        local_time,
        price_loader=lambda ticker, side=None: 102,
        data_loader=data_loader,
    )

    assert output_path == tmp_path / "Results" / "results_2026-06-12_21-45.csv"
    result = pd.read_csv(output_path)
    assert list(result["Ticker"]) == ["forex_major", "commodity_us"]
    assert set(result["profitable (yes/no)"]) == {"yes"}
