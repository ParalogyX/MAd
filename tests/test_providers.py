from datetime import datetime, timezone

import pandas as pd
import pytest

from investment_adviser import find_libertex_instruments, load_symbol_data
from investment_adviser.providers.fallback import _drop_incomplete_ohlcv_rows
from investment_adviser.providers.libertex import LibertexInstrumentProvider
from investment_adviser.providers.symbols import (
    candidate_binance_symbols,
    candidate_stooq_symbols,
    candidate_yfinance_symbols,
    is_discovery_noise,
)


def test_find_libertex_instruments(monkeypatch):
    def fake_discovery(self):
        return ["TSLA", "AAPL", "AAPL", "BTCUSD"]

    monkeypatch.setattr(
        LibertexInstrumentProvider,
        "_discover_live_instruments",
        fake_discovery,
    )

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
