from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from investment_adviser.analysis.technical import perform_technical_analysis


def _sample_data(rows: int = 80) -> pd.DataFrame:
    timestamps = pd.date_range(
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        periods=rows,
        freq="1D",
    )
    close = pd.Series(np.linspace(100.0, 180.0, rows))
    open_ = close - 0.5
    high = close + 1.0
    low = close - 1.0
    volume = pd.Series(np.linspace(1_000.0, 2_000.0, rows))
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def test_perform_technical_analysis_required_indicators():
    result = perform_technical_analysis(_sample_data())

    assert {
        "indicator_name",
        "indicator_value",
        "indicator_category",
        "signal",
        "description",
    } <= set(result.columns)
    indicator_names = set(result["indicator_name"])
    assert {
        "SMA 20",
        "SMA 50",
        "EMA 20",
        "EMA 50",
        "RSI 14",
        "MACD",
        "MACD signal",
        "MACD histogram",
        "Bollinger Bands upper",
        "Bollinger Bands middle",
        "Bollinger Bands lower",
        "ATR 14",
        "Stochastic %K",
        "Stochastic %D",
        "ADX 14",
        "Latest close",
        "Latest volume",
    } <= indicator_names


def test_perform_technical_analysis_handles_short_data():
    result = perform_technical_analysis(_sample_data(rows=5))

    assert not result.empty
    sma_20 = result.loc[result["indicator_name"] == "SMA 20", "indicator_value"].iloc[0]
    assert np.isnan(sma_20)


def test_perform_technical_analysis_approximately_correct():
    data = _sample_data()
    result = perform_technical_analysis(data)
    values = result.set_index("indicator_name")["indicator_value"]

    expected_sma_20 = data["close"].tail(20).mean()
    expected_sma_50 = data["close"].tail(50).mean()
    ema_12 = data["close"].ewm(span=12, adjust=False, min_periods=12).mean()
    ema_26 = data["close"].ewm(span=26, adjust=False, min_periods=26).mean()
    expected_macd = (ema_12 - ema_26).iloc[-1]

    assert values["SMA 20"] == pytest.approx(expected_sma_20, rel=1e-6)
    assert values["SMA 50"] == pytest.approx(expected_sma_50, rel=1e-6)
    assert values["MACD"] == pytest.approx(expected_macd, rel=1e-6)
    assert values["RSI 14"] == pytest.approx(100.0, rel=1e-6)
