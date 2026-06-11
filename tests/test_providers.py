from datetime import datetime, timezone

import pandas as pd
import pytest

from investment_adviser import find_libertex_instruments, load_symbol_data
from investment_adviser.models import MarketDataRequest
from investment_adviser.providers.fallback import _drop_incomplete_ohlcv_rows
from investment_adviser.providers.mt5 import MT5InstrumentProvider, MT5MarketDataProvider
from investment_adviser.providers.symbols import (
    candidate_binance_symbols,
    candidate_stooq_symbols,
    candidate_yfinance_symbols,
    is_discovery_noise,
)


def test_find_libertex_instruments(monkeypatch):
    class FakeSymbol:
        def __init__(self, name, trade_mode=1):
            self.name = name
            self.trade_mode = trade_mode

    class FakeClient:
        def symbols_get(self):
            return [
                FakeSymbol("TSLA"),
                FakeSymbol("AAPL"),
                FakeSymbol("AAPL"),
                FakeSymbol("DISABLED", trade_mode=0),
                FakeSymbol("BTCUSD"),
            ]

    monkeypatch.setattr(MT5InstrumentProvider, "_get_client", lambda self: FakeClient())

    symbols = find_libertex_instruments()

    assert symbols == ["AAPL", "BTCUSD", "TSLA"]
    assert all(isinstance(symbol, str) for symbol in symbols)


def test_load_symbol_data():
    data = load_symbol_data(
        symbol="AAPL",
        timeframe="1d",
        begin_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_time=datetime(2024, 1, 10, tzinfo=timezone.utc),
        provider="mock",
    )

    assert isinstance(data, pd.DataFrame)
    assert {"timestamp", "open", "high", "low", "close", "volume"} <= set(data.columns)
    numeric_columns = ["open", "high", "low", "close", "volume"]
    assert all(pd.api.types.is_numeric_dtype(data[column]) for column in numeric_columns)
    assert data["timestamp"].is_monotonic_increasing
    assert data["timestamp"].dt.tz is not None


def test_mt5_market_data_provider_normalizes_rates():
    class FakeSymbol:
        name = "EURUSD"
        trade_mode = 1

    class FakeClient:
        TIMEFRAME_H1 = 1

        def initialize(self):
            return True

        def symbols_get(self):
            return [FakeSymbol()]

        def symbol_select(self, symbol, selected):
            return symbol == "EURUSD" and selected

        def copy_rates_range(self, symbol, timeframe, begin_time, end_time):
            return [
                {
                    "time": int(
                        datetime(2024, 1, 1, 0, tzinfo=timezone.utc).timestamp()
                    ),
                    "open": 1.10,
                    "high": 1.12,
                    "low": 1.09,
                    "close": 1.11,
                    "tick_volume": 100,
                    "real_volume": 0,
                },
                {
                    "time": int(
                        datetime(2024, 1, 1, 1, tzinfo=timezone.utc).timestamp()
                    ),
                    "open": 1.11,
                    "high": 1.13,
                    "low": 1.10,
                    "close": 1.12,
                    "tick_volume": 120,
                    "real_volume": 0,
                },
            ]

    request = MarketDataRequest(
        symbol="EURUSD",
        timeframe="1h",
        begin_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_time=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    provider = MT5MarketDataProvider(client_factory=FakeClient)

    data = provider.load_data(request)

    assert list(data.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert len(data) == 2
    assert data.loc[0, "volume"] == 100
    assert data["timestamp"].dt.tz is not None


def test_load_symbol_data_rejects_invalid_timeframe():
    with pytest.raises(ValueError):
        load_symbol_data(
            symbol="AAPL",
            timeframe="2h",
            begin_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2024, 1, 10, tzinfo=timezone.utc),
            provider="mock",
        )


def test_load_symbol_data_rejects_invalid_time_range():
    with pytest.raises(ValueError):
        load_symbol_data(
            symbol="AAPL",
            timeframe="1d",
            begin_time=datetime(2024, 1, 10, tzinfo=timezone.utc),
            end_time=datetime(2024, 1, 10, tzinfo=timezone.utc),
            provider="mock",
        )


def test_provider_symbol_aliases_cover_common_libertex_codes():
    assert "BTC-USD" in candidate_yfinance_symbols("BTCUSD")
    assert "BTCUSDT" in candidate_binance_symbols("BTCUSD")
    assert "EURUSD=X" in candidate_yfinance_symbols("EURUSD")
    assert "CL=F" in candidate_yfinance_symbols("WTI")
    assert "BAS.DE" in candidate_yfinance_symbols("BASF")
    assert "bas.de" in candidate_stooq_symbols("BASF")
    assert is_discovery_noise("ACCOUNT")
    assert not is_discovery_noise("AAPL")


def test_drop_incomplete_provider_rows():
    data = pd.DataFrame(
        {
            "timestamp": [
                datetime(2024, 1, 1, tzinfo=timezone.utc),
                datetime(2024, 1, 2, tzinfo=timezone.utc),
            ],
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.0, 100.0],
            "close": [100.5, None],
            "volume": [1_000.0, None],
        }
    )

    cleaned = _drop_incomplete_ohlcv_rows(data)

    assert len(cleaned) == 1
    assert cleaned.loc[0, "close"] == 100.5
