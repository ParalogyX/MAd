from datetime import datetime, timezone

import pandas as pd

from investment_adviser.analysis.candles import perform_candle_analysis


def _frame(rows):
    return pd.DataFrame(
        [
            {
                "timestamp": datetime(2024, 1, index + 1, tzinfo=timezone.utc),
                "open": row[0],
                "high": row[1],
                "low": row[2],
                "close": row[3],
                "volume": 1_000.0,
            }
            for index, row in enumerate(rows)
        ]
    )


def _types(result):
    return set(result["candle_type"])


def test_detects_doji():
    result = perform_candle_analysis(_frame([(100.0, 101.0, 99.0, 100.02)]))

    assert "doji" in _types(result)
    assert result.loc[result["candle_type"] == "doji", "direction"].iloc[0] == "neutral"


def test_detects_bullish_engulfing():
    result = perform_candle_analysis(
        _frame(
            [
                (10.0, 10.5, 7.5, 8.0),
                (7.5, 11.5, 7.0, 11.0),
            ]
        )
    )

    assert "bullish engulfing" in _types(result)
    direction = result.loc[
        result["candle_type"] == "bullish engulfing",
        "direction",
    ].iloc[0]
    assert direction == "bullish"


def test_detects_bearish_engulfing():
    result = perform_candle_analysis(
        _frame(
            [
                (8.0, 10.5, 7.5, 10.0),
                (10.5, 11.0, 7.0, 7.5),
            ]
        )
    )

    assert "bearish engulfing" in _types(result)
    direction = result.loc[
        result["candle_type"] == "bearish engulfing",
        "direction",
    ].iloc[0]
    assert direction == "bearish"


def test_detects_morning_star():
    result = perform_candle_analysis(
        _frame(
            [
                (10.0, 10.2, 5.8, 6.0),
                (5.8, 6.1, 5.5, 5.9),
                (6.2, 9.2, 6.0, 9.0),
            ]
        )
    )

    assert "morning star" in _types(result)
    assert result.loc[result["candle_type"] == "morning star", "direction"].iloc[0] == "bullish"


def test_detects_evening_star():
    result = perform_candle_analysis(
        _frame(
            [
                (6.0, 10.2, 5.8, 10.0),
                (10.2, 10.5, 9.9, 10.1),
                (9.8, 10.0, 6.8, 7.0),
            ]
        )
    )

    assert "evening star" in _types(result)
    assert result.loc[result["candle_type"] == "evening star", "direction"].iloc[0] == "bearish"


def test_no_false_positives_for_simple_neutral_candles():
    result = perform_candle_analysis(
        _frame(
            [
                (100.0, 101.0, 99.5, 100.5),
                (100.5, 101.5, 100.0, 101.0),
                (101.0, 102.0, 100.5, 101.5),
                (101.5, 102.5, 101.0, 102.0),
            ]
        )
    )

    assert result.empty
