"""Deterministic candlestick pattern detection."""

from __future__ import annotations

import pandas as pd

from investment_adviser.utils.validation import normalize_market_data_frame


def perform_candle_analysis(data: pd.DataFrame) -> pd.DataFrame:
    """Detect candlestick patterns in OHLCV data.

    Multi-candle patterns are stamped with the final candle in the pattern.
    Detection uses deterministic textbook-style rules based on candle bodies,
    shadows, and short local trend context.
    """

    market_data = normalize_market_data_frame(data)
    detections: list[dict[str, object]] = []

    for index, row in market_data.iterrows():
        if _is_doji(row):
            detections.append(_detection(row, "doji", "neutral", 0.8))

        if _is_hammer_shape(row):
            trend = _local_trend(market_data, index)
            if trend == "up":
                detections.append(_detection(row, "hanging man", "bearish", 0.55))
            else:
                detections.append(_detection(row, "hammer", "bullish", 0.65))

        if _is_inverted_hammer_shape(row):
            trend = _local_trend(market_data, index)
            if trend == "up":
                detections.append(_detection(row, "shooting star", "bearish", 0.65))
            else:
                detections.append(
                    _detection(row, "inverted hammer", "bullish", 0.55)
                )

        if index >= 1:
            previous = market_data.iloc[index - 1]
            if _is_bullish_engulfing(previous, row):
                detections.append(
                    _detection(row, "bullish engulfing", "bullish", 0.8)
                )
            if _is_bearish_engulfing(previous, row):
                detections.append(
                    _detection(row, "bearish engulfing", "bearish", 0.8)
                )
            if _is_piercing_pattern(previous, row):
                detections.append(
                    _detection(row, "piercing pattern", "bullish", 0.65)
                )
            if _is_dark_cloud_cover(previous, row):
                detections.append(
                    _detection(row, "dark cloud cover", "bearish", 0.65)
                )

        if index >= 2:
            first = market_data.iloc[index - 2]
            second = market_data.iloc[index - 1]
            third = row
            if _is_morning_star(first, second, third):
                detections.append(_detection(third, "morning star", "bullish", 0.75))
            if _is_evening_star(first, second, third):
                detections.append(_detection(third, "evening star", "bearish", 0.75))
            if _is_three_white_soldiers(first, second, third):
                detections.append(
                    _detection(third, "three white soldiers", "bullish", 0.7)
                )
            if _is_three_black_crows(first, second, third):
                detections.append(
                    _detection(third, "three black crows", "bearish", 0.7)
                )

    return pd.DataFrame(
        detections,
        columns=["timestamp", "candle_type", "direction", "confidence"],
    )


def _detection(
    row: pd.Series,
    candle_type: str,
    direction: str,
    confidence: float,
) -> dict[str, object]:
    return {
        "timestamp": row["timestamp"],
        "candle_type": candle_type,
        "direction": direction,
        "confidence": confidence,
    }


def _body(row: pd.Series) -> float:
    return abs(float(row["close"]) - float(row["open"]))


def _range(row: pd.Series) -> float:
    return max(float(row["high"]) - float(row["low"]), 0.0)


def _upper_shadow(row: pd.Series) -> float:
    return float(row["high"]) - max(float(row["open"]), float(row["close"]))


def _lower_shadow(row: pd.Series) -> float:
    return min(float(row["open"]), float(row["close"])) - float(row["low"])


def _is_bullish(row: pd.Series) -> bool:
    return float(row["close"]) > float(row["open"])


def _is_bearish(row: pd.Series) -> bool:
    return float(row["close"]) < float(row["open"])


def _is_doji(row: pd.Series) -> bool:
    candle_range = _range(row)
    return candle_range > 0.0 and _body(row) <= candle_range * 0.1


def _is_small_body(row: pd.Series) -> bool:
    candle_range = _range(row)
    return candle_range > 0.0 and _body(row) <= candle_range * 0.35


def _is_long_body(row: pd.Series) -> bool:
    candle_range = _range(row)
    return candle_range > 0.0 and _body(row) >= candle_range * 0.5


def _is_hammer_shape(row: pd.Series) -> bool:
    body = _body(row)
    return (
        _range(row) > 0.0
        and body > 0.0
        and _lower_shadow(row) >= body * 2.0
        and _upper_shadow(row) <= body
        and body <= _range(row) * 0.4
    )


def _is_inverted_hammer_shape(row: pd.Series) -> bool:
    body = _body(row)
    return (
        _range(row) > 0.0
        and body > 0.0
        and _upper_shadow(row) >= body * 2.0
        and _lower_shadow(row) <= body
        and body <= _range(row) * 0.4
    )


def _local_trend(data: pd.DataFrame, index: int, lookback: int = 3) -> str:
    if index < lookback:
        return "unknown"
    previous_close = float(data.iloc[index - 1]["close"])
    earlier_close = float(data.iloc[index - lookback]["close"])
    if previous_close > earlier_close:
        return "up"
    if previous_close < earlier_close:
        return "down"
    return "unknown"


def _is_bullish_engulfing(previous: pd.Series, current: pd.Series) -> bool:
    return (
        _is_bearish(previous)
        and _is_bullish(current)
        and float(current["open"]) <= float(previous["close"])
        and float(current["close"]) >= float(previous["open"])
        and _body(current) > _body(previous)
    )


def _is_bearish_engulfing(previous: pd.Series, current: pd.Series) -> bool:
    return (
        _is_bullish(previous)
        and _is_bearish(current)
        and float(current["open"]) >= float(previous["close"])
        and float(current["close"]) <= float(previous["open"])
        and _body(current) > _body(previous)
    )


def _midpoint(row: pd.Series) -> float:
    return (float(row["open"]) + float(row["close"])) / 2.0


def _is_morning_star(
    first: pd.Series,
    second: pd.Series,
    third: pd.Series,
) -> bool:
    return (
        _is_bearish(first)
        and _is_long_body(first)
        and _is_small_body(second)
        and max(float(second["open"]), float(second["close"]))
        <= float(first["close"]) + _body(first) * 0.25
        and _is_bullish(third)
        and float(third["close"]) > _midpoint(first)
    )


def _is_evening_star(
    first: pd.Series,
    second: pd.Series,
    third: pd.Series,
) -> bool:
    return (
        _is_bullish(first)
        and _is_long_body(first)
        and _is_small_body(second)
        and min(float(second["open"]), float(second["close"]))
        >= float(first["close"]) - _body(first) * 0.25
        and _is_bearish(third)
        and float(third["close"]) < _midpoint(first)
    )


def _is_piercing_pattern(previous: pd.Series, current: pd.Series) -> bool:
    return (
        _is_bearish(previous)
        and _is_bullish(current)
        and float(current["open"]) < float(previous["close"])
        and _midpoint(previous) < float(current["close"]) < float(previous["open"])
    )


def _is_dark_cloud_cover(previous: pd.Series, current: pd.Series) -> bool:
    return (
        _is_bullish(previous)
        and _is_bearish(current)
        and float(current["open"]) > float(previous["close"])
        and _midpoint(previous) > float(current["close"]) > float(previous["open"])
    )


def _is_three_white_soldiers(
    first: pd.Series,
    second: pd.Series,
    third: pd.Series,
) -> bool:
    candles = (first, second, third)
    return (
        all(_is_bullish(candle) and _is_long_body(candle) for candle in candles)
        and float(second["close"]) > float(first["close"])
        and float(third["close"]) > float(second["close"])
        and float(second["open"]) >= min(float(first["open"]), float(first["close"]))
        and float(third["open"]) >= min(float(second["open"]), float(second["close"]))
    )


def _is_three_black_crows(
    first: pd.Series,
    second: pd.Series,
    third: pd.Series,
) -> bool:
    candles = (first, second, third)
    return (
        all(_is_bearish(candle) and _is_long_body(candle) for candle in candles)
        and float(second["close"]) < float(first["close"])
        and float(third["close"]) < float(second["close"])
        and float(second["open"]) <= max(float(first["open"]), float(first["close"]))
        and float(third["open"]) <= max(float(second["open"]), float(second["close"]))
    )
