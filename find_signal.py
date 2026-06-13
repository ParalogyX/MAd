"""Scan Libertex symbols and write multi-timeframe signal scores to CSV.

This script calculates normalized analysis scores only. It does not place
trades, connect to broker order APIs, calculate position sizes, or generate
entry/stop/take-profit instructions.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

import pandas as pd

from investment_adviser import (
    find_libertex_instruments,
    load_symbol_data,
    perform_candle_analysis,
    perform_symbol_sentiment_analysis,
    perform_technical_analysis,
)

STRATEGY_NAME = "Multi-Timeframe Trend Momentum Consensus Signal"
OUTPUT_DIR_ENV_VAR = "M_AD_OUTPUT_DIR"
LOGGER = logging.getLogger("trade_signal_generator")
LOGGER.addHandler(logging.NullHandler())
LOGGER.propagate = False
OUTPUT_COLUMNS = [
    "ticker",
    "current_price",
    "direction",
    "signal_strength",
    "daily_trend_score",
    "h4_trend_score",
    "h4_momentum_score",
    "h1_confirmation_score",
    "rsi_score",
    "adx_score",
    "candle_score",
    "sentiment_score",
    "final_long_score",
    "final_short_score",
    "reason",
    "timestamp_utc",
    "atr_1d",
    "atr_percent_1d",
    "usable_atr_1d",
    "sl_distance",
    "tp_distance",
    "stop_loss",
    "take_profit",
    "risk_reward_ratio",
    "sl_tp_reason",
]

MIN_DAILY_CANDLES = 260
MIN_H4_CANDLES = 220
MIN_H1_CANDLES = 160

BULLISH_CANDLE_PATTERNS = {
    "bullish engulfing",
    "hammer",
    "inverted hammer",
    "morning star",
    "piercing pattern",
    "three white soldiers",
}

BEARISH_CANDLE_PATTERNS = {
    "bearish engulfing",
    "hanging man",
    "shooting star",
    "evening star",
    "dark cloud cover",
    "three black crows",
}


def normalize_ohlcv_columns(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize common OHLCV column names to lower-case schema."""

    if not isinstance(data, pd.DataFrame):
        raise ValueError("Market data must be a pandas DataFrame.")

    normalized = data.copy()
    if isinstance(normalized.columns, pd.MultiIndex):
        normalized.columns = [
            "_".join(str(part) for part in column if str(part))
            for column in normalized.columns
        ]

    column_map: dict[str, str] = {}
    normalized_lookup = {
        _normalize_key(str(column)): str(column) for column in normalized.columns
    }
    aliases = {
        "timestamp": ("timestamp", "datetime", "date", "time"),
        "open": ("open",),
        "high": ("high",),
        "low": ("low",),
        "close": ("close",),
        "volume": ("volume", "vol"),
    }

    for target, names in aliases.items():
        for name in names:
            existing = normalized_lookup.get(_normalize_key(name))
            if existing is not None:
                column_map[existing] = target
                break

    normalized = normalized.rename(columns=column_map)
    missing = [
        column
        for column in ("timestamp", "open", "high", "low", "close", "volume")
        if column not in normalized.columns
    ]
    if missing:
        raise ValueError(f"Missing OHLCV columns: {', '.join(missing)}.")

    normalized = normalized[
        ["timestamp", "open", "high", "low", "close", "volume"]
    ].copy()
    normalized["timestamp"] = pd.to_datetime(
        normalized["timestamp"],
        utc=True,
        errors="coerce",
    )
    for column in ["open", "high", "low", "close", "volume"]:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")

    # Public feeds can include an in-progress candle. We cannot always detect
    # exchange close status reliably, so we drop rows with incomplete OHLC data
    # and then use the latest remaining row as the latest closed/usable candle.
    normalized = normalized.dropna(subset=["timestamp", "open", "high", "low", "close"])
    normalized["volume"] = normalized["volume"].fillna(0.0)
    normalized = (
        normalized.drop_duplicates(subset=["timestamp"], keep="last")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    return normalized


def extract_latest_indicator(ta_result: Any, indicator_name: str) -> float | None:
    """Extract one latest indicator value from multiple possible result shapes."""

    target_key = _normalize_key(indicator_name)
    if ta_result is None:
        return None

    if isinstance(ta_result, dict):
        for key, value in ta_result.items():
            if _normalize_key(str(key)) == target_key:
                return _to_float(value)
        return None

    if isinstance(ta_result, pd.Series):
        for key, value in ta_result.items():
            if _normalize_key(str(key)) == target_key:
                return _to_float(value)
        return None

    if not isinstance(ta_result, pd.DataFrame) or ta_result.empty:
        return None

    columns_by_key = {
        _normalize_key(str(column)): str(column) for column in ta_result.columns
    }
    name_column = columns_by_key.get("indicatorname") or columns_by_key.get("name")
    value_column = columns_by_key.get("indicatorvalue") or columns_by_key.get("value")

    if name_column and value_column:
        for _, row in ta_result.iterrows():
            if _normalize_key(str(row[name_column])) == target_key:
                return _to_float(row[value_column])
        return None

    matched_column = columns_by_key.get(target_key)
    if matched_column:
        return _to_float(ta_result[matched_column].iloc[-1])
    return None


def ensure_indicators(data: pd.DataFrame, ta_result: Any) -> dict[str, float]:
    """Return latest indicator values, calculating missing values with pandas."""

    indicators = _calculate_indicator_values(data)
    requested = {
        "ema20": "EMA20",
        "ema50": "EMA50",
        "ema200": "EMA200",
        "rsi14": "RSI14",
        "macd": "MACD",
        "macd_signal": "MACD_signal",
        "macd_histogram": "MACD_histogram",
        "adx14": "ADX14",
        "atr14": "ATR14",
    }

    for key, indicator_name in requested.items():
        value = _extract_indicator_with_aliases(ta_result, indicator_name)
        if value is not None and math.isfinite(value):
            indicators[key] = value
    return indicators


def calculate_daily_trend_scores(
    data: pd.DataFrame,
    indicators: dict[str, float],
) -> tuple[float, float]:
    """Calculate daily long and short macro trend scores."""

    close = float(data["close"].iloc[-1])
    ema50 = indicators["ema50"]
    ema200 = indicators["ema200"]
    return_20d = _safe_return(close, data["close"].iloc[-21])

    long_score = 0.0
    if close > ema200:
        long_score += 45.0
    if ema50 > ema200:
        long_score += 35.0
    if return_20d > 0:
        long_score += min(20.0, return_20d / 0.10 * 20.0)

    short_score = 0.0
    if close < ema200:
        short_score += 45.0
    if ema50 < ema200:
        short_score += 35.0
    if return_20d < 0:
        short_score += min(20.0, abs(return_20d) / 0.10 * 20.0)

    return clip_score(long_score), clip_score(short_score)


def calculate_h4_trend_scores(
    data: pd.DataFrame,
    indicators: dict[str, float],
) -> tuple[float, float]:
    """Calculate 4H long and short trend scores."""

    close = float(data["close"].iloc[-1])
    ema20 = indicators["ema20"]
    ema50 = indicators["ema50"]
    ema200 = indicators["ema200"]
    spread = abs(ema20 - ema50) / close if close else 0.0

    long_score = 0.0
    if close > ema200:
        long_score += 30.0
    if close > ema50:
        long_score += 25.0
    if ema20 > ema50:
        long_score += 30.0
    long_score += min(15.0, spread / 0.03 * 15.0)

    short_score = 0.0
    if close < ema200:
        short_score += 30.0
    if close < ema50:
        short_score += 25.0
    if ema20 < ema50:
        short_score += 30.0
    short_score += min(15.0, spread / 0.03 * 15.0)

    return clip_score(long_score), clip_score(short_score)


def calculate_h4_momentum_scores(
    data: pd.DataFrame,
    indicators: dict[str, float],
) -> tuple[float, float]:
    """Calculate 4H long and short momentum scores."""

    close = float(data["close"].iloc[-1])
    return_10 = _safe_return(close, data["close"].iloc[-11])
    return_20 = _safe_return(close, data["close"].iloc[-21])
    macd_hist = indicators["macd_histogram"]
    macd_hist_previous = indicators["macd_histogram_previous"]
    macd_hist_slope = macd_hist - macd_hist_previous

    long_score = 0.0
    if macd_hist > 0:
        long_score += 35.0
    if macd_hist_slope > 0:
        long_score += 20.0
    if return_10 > 0:
        long_score += min(20.0, return_10 / 0.04 * 20.0)
    if return_20 > 0:
        long_score += min(25.0, return_20 / 0.08 * 25.0)

    short_score = 0.0
    if macd_hist < 0:
        short_score += 35.0
    if macd_hist_slope < 0:
        short_score += 20.0
    if return_10 < 0:
        short_score += min(20.0, abs(return_10) / 0.04 * 20.0)
    if return_20 < 0:
        short_score += min(25.0, abs(return_20) / 0.08 * 25.0)

    return clip_score(long_score), clip_score(short_score)


def calculate_h1_confirmation_scores(
    data: pd.DataFrame,
    indicators: dict[str, float],
) -> tuple[float, float]:
    """Calculate 1H long and short short-term confirmation scores."""

    close = float(data["close"].iloc[-1])
    ema20 = indicators["ema20"]
    ema50 = indicators["ema50"]
    macd_hist = indicators["macd_histogram"]

    long_score = 0.0
    if close > ema20:
        long_score += 30.0
    if ema20 > ema50:
        long_score += 40.0
    if macd_hist > 0:
        long_score += 30.0

    short_score = 0.0
    if close < ema20:
        short_score += 30.0
    if ema20 < ema50:
        short_score += 40.0
    if macd_hist < 0:
        short_score += 30.0

    return clip_score(long_score), clip_score(short_score)


def calculate_rsi_scores(rsi_4h: float) -> tuple[float, float]:
    """Convert 4H RSI into long and short trend-following scores."""

    if not math.isfinite(rsi_4h):
        return 0.0, 0.0

    if 45 <= rsi_4h <= 65:
        long_score = 100.0
    elif 35 <= rsi_4h < 45:
        long_score = 70.0
    elif 65 < rsi_4h <= 72:
        long_score = 60.0
    elif 72 < rsi_4h <= 80:
        long_score = 25.0
    else:
        long_score = 0.0

    if 35 <= rsi_4h <= 55:
        short_score = 100.0
    elif 55 < rsi_4h <= 65:
        short_score = 70.0
    elif 28 <= rsi_4h < 35:
        short_score = 60.0
    elif 20 <= rsi_4h < 28:
        short_score = 25.0
    else:
        short_score = 0.0

    return long_score, short_score


def calculate_adx_score(adx_4h: float) -> float:
    """Convert 4H ADX into a direction-agnostic trend-strength score."""

    if not math.isfinite(adx_4h):
        return 0.0
    if adx_4h < 15:
        return 0.0
    if 15 <= adx_4h < 20:
        return 30.0
    if 20 <= adx_4h < 25:
        return 60.0
    if 25 <= adx_4h < 35:
        return 100.0
    if 35 <= adx_4h < 50:
        return 85.0
    return 70.0


def calculate_candle_scores(
    candle_result: Any,
    data_4h: pd.DataFrame | None = None,
) -> tuple[float, float]:
    """Calculate small-weight candle scores from recent 4H candle patterns."""

    if not isinstance(candle_result, pd.DataFrame) or candle_result.empty:
        return 50.0, 50.0

    candles = candle_result.copy()
    if data_4h is not None and "timestamp" in candles.columns:
        recent_timestamps = set(pd.to_datetime(data_4h["timestamp"].tail(3), utc=True))
        candle_timestamps = pd.to_datetime(candles["timestamp"], utc=True)
        candles = candles[candle_timestamps.isin(recent_timestamps)]
    else:
        candles = candles.tail(3)

    bullish_found = False
    bearish_found = False
    for _, row in candles.iterrows():
        pattern = _normalize_pattern(str(row.get("candle_type", "")))
        direction = str(row.get("direction", "")).lower()
        if pattern in {_normalize_pattern(value) for value in BULLISH_CANDLE_PATTERNS}:
            bullish_found = True
        if pattern in {_normalize_pattern(value) for value in BEARISH_CANDLE_PATTERNS}:
            bearish_found = True
        if direction == "bullish":
            bullish_found = True
        if direction == "bearish":
            bearish_found = True

    if bullish_found and not bearish_found:
        return 100.0, 0.0
    if bearish_found and not bullish_found:
        return 0.0, 100.0
    return 50.0, 50.0


def calculate_sentiment_scores(sentiment: float) -> tuple[float, float]:
    """Convert raw sentiment from -100..100 into long and short scores."""

    normalized = max(-100.0, min(100.0, float(sentiment)))
    return (normalized + 100.0) / 2.0, (100.0 - normalized) / 2.0


def calculate_daily_sl_tp(
    current_price: float,
    direction: str,
    signal_strength: float,
    atr_1d: float,
    sl_multiplier: float = 0.45,
    tp_base_multiplier: float = 0.60,
    tp_strength_multiplier: float = 0.25,
) -> dict[str, Any]:
    """Calculate stop-loss and take-profit levels for daily signals.

    This function does not place trades. It only returns calculated levels.
    The current_price argument is the price used for the calculated levels.
    Session-specific multipliers can override the defaults.
    """

    atr_value = _to_float(atr_1d)
    price_value = _to_float(current_price)
    if price_value is None or price_value <= 0:
        return _empty_sl_tp_result(atr_value, "Invalid current price")

    normalized_direction = str(direction).strip().lower()
    if normalized_direction not in {"buy", "sell", "neutral"}:
        return _empty_sl_tp_result(atr_value, "Invalid direction")
    if normalized_direction == "neutral":
        return _empty_sl_tp_result(
            atr_value,
            "Neutral signal, SL/TP not calculated",
        )

    strength_value = _to_float(signal_strength)
    if strength_value is None:
        strength_value = 0.0
    strength_value = max(0.0, min(100.0, strength_value))

    if atr_value is None or atr_value <= 0:
        return _empty_sl_tp_result(atr_value, "Invalid ATR")

    atr_percent_1d = atr_value / price_value
    min_atr_percent = 0.015
    max_atr_percent = 0.12
    usable_atr_percent = min(
        max(atr_percent_1d, min_atr_percent),
        max_atr_percent,
    )
    usable_atr_1d = price_value * usable_atr_percent

    sl_distance = sl_multiplier * usable_atr_1d
    tp_multiplier = tp_base_multiplier + tp_strength_multiplier * (
        strength_value / 100.0
    )
    tp_distance = tp_multiplier * usable_atr_1d
    risk_reward_ratio = tp_distance / sl_distance

    if normalized_direction == "buy":
        stop_loss = price_value - sl_distance
        take_profit = price_value + tp_distance
    else:
        stop_loss = price_value + sl_distance
        take_profit = max(price_value - tp_distance, price_value * 0.0001)

    if usable_atr_percent > atr_percent_1d:
        clamp_note = (
            f"ATR clamped up from {atr_percent_1d:.2%} "
            f"to {usable_atr_percent:.2%}"
        )
    elif usable_atr_percent < atr_percent_1d:
        clamp_note = (
            f"ATR clamped down from {atr_percent_1d:.2%} "
            f"to {usable_atr_percent:.2%}"
        )
    else:
        clamp_note = "ATR not clamped"

    sl_tp_reason = (
        "ATR-based daily SL/TP: "
        f"SL={sl_multiplier:.2f}*ATR, "
        f"TP={tp_multiplier:.2f}*ATR, "
        f"{clamp_note}"
    )

    return {
        "atr_1d": atr_value,
        "atr_percent_1d": round(atr_percent_1d, 6),
        "usable_atr_1d": smart_round_price(usable_atr_1d),
        "sl_distance": smart_round_price(sl_distance),
        "tp_distance": smart_round_price(tp_distance),
        "stop_loss": smart_round_price(stop_loss),
        "take_profit": smart_round_price(take_profit),
        "risk_reward_ratio": round(risk_reward_ratio, 3),
        "sl_tp_reason": sl_tp_reason,
    }


def smart_round_price(price: float) -> float:
    """Round price by magnitude while preserving low-price precision."""

    abs_price = abs(float(price))
    if abs_price >= 1000:
        return round(price, 2)
    if abs_price >= 100:
        return round(price, 3)
    if abs_price >= 10:
        return round(price, 4)
    if abs_price >= 1:
        return round(price, 5)
    if abs_price >= 0.1:
        return round(price, 6)
    if abs_price >= 0.01:
        return round(price, 7)
    if abs_price >= 0.001:
        return round(price, 8)
    return round(price, 10)


def apply_contradiction_penalties(
    final_long_score: float,
    final_short_score: float,
    daily_long_trend_score: float,
    daily_short_trend_score: float,
    h4_long_trend_score: float,
    h4_short_trend_score: float,
    sentiment: float,
    rsi_4h: float,
    adx_4h: float,
) -> tuple[float, float]:
    """Apply long/short contradiction penalties and clip final scores."""

    long_score = final_long_score
    if daily_short_trend_score >= 75:
        long_score -= 15
    if h4_short_trend_score >= 75:
        long_score -= 20
    if sentiment <= -50:
        long_score -= 10
    if rsi_4h > 80:
        long_score -= 20
    if adx_4h < 15:
        long_score -= 20

    short_score = final_short_score
    if daily_long_trend_score >= 75:
        short_score -= 15
    if h4_long_trend_score >= 75:
        short_score -= 20
    if sentiment >= 50:
        short_score -= 10
    if rsi_4h < 20:
        short_score -= 20
    if adx_4h < 15:
        short_score -= 20

    return clip_score(long_score), clip_score(short_score)


def choose_direction(final_long_score: float, final_short_score: float) -> tuple[str, float]:
    """Choose buy, sell, or neutral from final long and short scores."""

    score_gap = abs(final_long_score - final_short_score)
    if score_gap < 8:
        return "neutral", max(final_long_score, final_short_score)
    if final_long_score >= 55 and final_long_score > final_short_score:
        return "buy", final_long_score
    if final_short_score >= 55 and final_short_score > final_long_score:
        return "sell", final_short_score
    return "neutral", max(final_long_score, final_short_score)


def process_symbol(
    symbol: str,
    now: datetime | None = None,
    data_loader: Callable[..., pd.DataFrame] = load_symbol_data,
    technical_analyzer: Callable[[pd.DataFrame], Any] = perform_technical_analysis,
    candle_analyzer: Callable[[pd.DataFrame], Any] = perform_candle_analysis,
    sentiment_analyzer: Callable[[str], Any] = perform_symbol_sentiment_analysis,
) -> dict[str, Any]:
    """Process one symbol and return one output row."""

    timestamp = now or datetime.now(timezone.utc)
    try:
        daily_begin = timestamp - timedelta(days=800)
        h4_begin = timestamp - timedelta(days=240)
        h1_begin = timestamp - timedelta(days=60)

        with _timed_log_task("load_symbol_data", symbol=symbol, timeframe="1d"):
            data_1d = normalize_ohlcv_columns(
                data_loader(symbol, "1d", daily_begin, timestamp, provider="fallback")
            )
        with _timed_log_task("load_symbol_data", symbol=symbol, timeframe="4h"):
            data_4h = normalize_ohlcv_columns(
                data_loader(symbol, "4h", h4_begin, timestamp, provider="fallback")
            )
        with _timed_log_task("load_symbol_data", symbol=symbol, timeframe="1h"):
            data_1h = normalize_ohlcv_columns(
                data_loader(symbol, "1h", h1_begin, timestamp, provider="fallback")
            )

        validate_minimum_length(data_1d, MIN_DAILY_CANDLES, "1D")
        validate_minimum_length(data_4h, MIN_H4_CANDLES, "4H")
        validate_minimum_length(data_1h, MIN_H1_CANDLES, "1H")

        with _timed_log_task("technical_analysis", symbol=symbol, timeframe="1d"):
            ta_1d = technical_analyzer(data_1d)
            indicators_1d = ensure_indicators(data_1d, ta_1d)
        with _timed_log_task("technical_analysis", symbol=symbol, timeframe="4h"):
            ta_4h = technical_analyzer(data_4h)
            indicators_4h = ensure_indicators(data_4h, ta_4h)
        with _timed_log_task("technical_analysis", symbol=symbol, timeframe="1h"):
            ta_1h = technical_analyzer(data_1h)
            indicators_1h = ensure_indicators(data_1h, ta_1h)

        daily_long, daily_short = calculate_daily_trend_scores(
            data_1d,
            indicators_1d,
        )
        h4_long_trend, h4_short_trend = calculate_h4_trend_scores(
            data_4h,
            indicators_4h,
        )
        h4_long_momentum, h4_short_momentum = calculate_h4_momentum_scores(
            data_4h,
            indicators_4h,
        )
        h1_long, h1_short = calculate_h1_confirmation_scores(
            data_1h,
            indicators_1h,
        )
        rsi_long, rsi_short = calculate_rsi_scores(indicators_4h["rsi14"])
        adx_score = calculate_adx_score(indicators_4h["adx14"])

        try:
            with _timed_log_task("candle_analysis", symbol=symbol, timeframe="4h"):
                candle_result = candle_analyzer(data_4h)
                candle_long, candle_short = calculate_candle_scores(
                    candle_result,
                    data_4h,
                )
        except Exception:
            candle_long, candle_short = 50.0, 50.0

        with _timed_log_task("sentiment_analysis", symbol=symbol):
            raw_sentiment = extract_sentiment_score(sentiment_analyzer, symbol)
        sentiment_long, sentiment_short = calculate_sentiment_scores(raw_sentiment)

        final_long = (
            0.25 * daily_long
            + 0.25 * h4_long_trend
            + 0.15 * h4_long_momentum
            + 0.10 * h1_long
            + 0.10 * rsi_long
            + 0.07 * adx_score
            + 0.04 * candle_long
            + 0.04 * sentiment_long
        )
        final_short = (
            0.25 * daily_short
            + 0.25 * h4_short_trend
            + 0.15 * h4_short_momentum
            + 0.10 * h1_short
            + 0.10 * rsi_short
            + 0.07 * adx_score
            + 0.04 * candle_short
            + 0.04 * sentiment_short
        )

        final_long, final_short = apply_contradiction_penalties(
            final_long,
            final_short,
            daily_long,
            daily_short,
            h4_long_trend,
            h4_short_trend,
            raw_sentiment,
            indicators_4h["rsi14"],
            indicators_4h["adx14"],
        )
        direction, signal_strength = choose_direction(final_long, final_short)
        current_price = float(data_1h["close"].iloc[-1])
        sl_tp = calculate_daily_sl_tp(
            current_price=current_price,
            direction=direction,
            signal_strength=signal_strength,
            atr_1d=indicators_1d["atr14"],
        )
        reason = build_human_readable_reason(
            direction,
            final_long,
            final_short,
            daily_long,
            daily_short,
            h4_long_trend,
            h4_short_trend,
            h4_long_momentum,
            h4_short_momentum,
            indicators_4h["rsi14"],
            indicators_4h["adx14"],
            raw_sentiment,
        )

        return _result_row(
            ticker=symbol,
            current_price=current_price,
            direction=direction,
            signal_strength=signal_strength,
            daily_trend_score=max(daily_long, daily_short),
            h4_trend_score=max(h4_long_trend, h4_short_trend),
            h4_momentum_score=max(h4_long_momentum, h4_short_momentum),
            h1_confirmation_score=max(h1_long, h1_short),
            rsi_score=max(rsi_long, rsi_short),
            adx_score=adx_score,
            candle_score=max(candle_long, candle_short),
            sentiment_score=raw_sentiment,
            final_long_score=final_long,
            final_short_score=final_short,
            reason=reason,
            timestamp_utc=timestamp,
            sl_tp=sl_tp,
        )
    except Exception as exc:
        return _neutral_error_row(symbol, f"ERROR: {exc}", timestamp)


def validate_minimum_length(data: pd.DataFrame, required: int, timeframe: str) -> None:
    """Raise if a timeframe DataFrame has fewer than required candles."""

    if len(data) < required:
        raise ValueError(
            f"insufficient data for {timeframe}: "
            f"{len(data)} candles available, {required} required"
        )


def extract_sentiment_score(
    sentiment_analyzer: Callable[[str], Any],
    symbol: str,
) -> float:
    """Call sentiment analysis and return a valid -100..100 raw value."""

    try:
        sentiment_result = sentiment_analyzer(symbol)
    except Exception:
        return 0.0

    value: Any = None
    if isinstance(sentiment_result, (int, float)):
        value = sentiment_result
    elif isinstance(sentiment_result, dict):
        value = sentiment_result.get("sentiment_score")
    elif isinstance(sentiment_result, pd.Series):
        value = sentiment_result.get("sentiment_score")
    elif isinstance(sentiment_result, pd.DataFrame) and not sentiment_result.empty:
        if "sentiment_score" in sentiment_result.columns:
            value = sentiment_result["sentiment_score"].iloc[-1]

    numeric_value = _to_float(value)
    if numeric_value is None or not math.isfinite(numeric_value):
        return 0.0
    return max(-100.0, min(100.0, numeric_value))


def build_human_readable_reason(
    direction: str,
    final_long_score: float,
    final_short_score: float,
    daily_long_score: float,
    daily_short_score: float,
    h4_long_trend_score: float,
    h4_short_trend_score: float,
    h4_long_momentum_score: float,
    h4_short_momentum_score: float,
    rsi_4h: float,
    adx_4h: float,
    sentiment: float,
) -> str:
    """Build a short reason string for the output row."""

    parts: list[str] = []
    if abs(final_long_score - final_short_score) < 8:
        parts.append("Neutral: long and short scores too close")
    elif direction == "buy":
        parts.append("Buy: consensus favors long")
    elif direction == "sell":
        parts.append("Sell: consensus favors short")
    else:
        parts.append("Neutral: no score crossed threshold")

    if daily_long_score > daily_short_score + 10:
        parts.append("daily uptrend")
    elif daily_short_score > daily_long_score + 10:
        parts.append("daily downtrend")
    else:
        parts.append("daily mixed")

    if h4_long_trend_score > h4_short_trend_score + 10:
        parts.append("4H uptrend")
    elif h4_short_trend_score > h4_long_trend_score + 10:
        parts.append("4H downtrend")
    else:
        parts.append("4H mixed")

    if h4_long_momentum_score > h4_short_momentum_score + 10:
        parts.append("positive 4H momentum")
    elif h4_short_momentum_score > h4_long_momentum_score + 10:
        parts.append("negative 4H momentum")

    if adx_4h < 15:
        parts.append("ADX too low")
    elif adx_4h >= 25:
        parts.append("ADX trend confirmed")

    if rsi_4h > 80:
        parts.append("RSI overextended high")
    elif rsi_4h < 20:
        parts.append("RSI overextended low")
    else:
        parts.append("RSI acceptable")

    if sentiment >= 20:
        parts.append("sentiment positive")
    elif sentiment <= -20:
        parts.append("sentiment negative")
    else:
        parts.append("sentiment neutral")

    return ", ".join(parts)[:500]


def run_scan(output: Path, sort_output: bool, limit: int | None) -> pd.DataFrame:
    """Scan all discovered tickers and save the signal CSV."""

    output.parent.mkdir(parents=True, exist_ok=True)
    tickers = find_libertex_instruments()
    if limit is not None:
        tickers = tickers[:limit]

    total = len(tickers)
    rows: list[dict[str, Any]] = []
    for index, ticker in enumerate(tickers, start=1):
        print(f"[{index}/{total}] Processing {ticker}...", flush=True)
        rows.append(process_symbol(ticker))

    result = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    if sort_output:
        result = result.sort_values(
            "signal_strength",
            ascending=False,
            kind="mergesort",
        )
    result.to_csv(output, index=False)
    print(f"Done. Saved {output}")
    return result


def default_output_path() -> Path:
    """Return the default signals.csv path, honoring M_AD_OUTPUT_DIR."""

    output_dir = Path(os.getenv(OUTPUT_DIR_ENV_VAR, ".")).expanduser()
    return output_dir / "signals.csv"


def run_self_test() -> None:
    """Run offline self-tests for core scoring and error behavior."""

    assert clip_score(-5) == 0.0
    assert clip_score(105) == 100.0
    assert choose_direction(60, 40) == ("buy", 60)
    assert choose_direction(40, 60) == ("sell", 60)
    assert choose_direction(60, 55)[0] == "neutral"
    assert calculate_sentiment_scores(100) == (100.0, 0.0)
    assert calculate_sentiment_scores(0) == (50.0, 50.0)
    assert calculate_sentiment_scores(-100) == (0.0, 100.0)
    assert calculate_rsi_scores(50) == (100.0, 100.0)
    assert calculate_rsi_scores(75) == (25.0, 0.0)
    assert calculate_adx_score(10) == 0.0
    assert calculate_adx_score(22) == 60.0
    assert calculate_adx_score(30) == 100.0

    buy_sl_tp = calculate_daily_sl_tp(100.0, "buy", 80.0, 5.0)
    assert buy_sl_tp["stop_loss"] == 97.75
    assert buy_sl_tp["take_profit"] == 104.0
    assert buy_sl_tp["risk_reward_ratio"] > 1.0

    sell_sl_tp = calculate_daily_sl_tp(100.0, "sell", 80.0, 5.0)
    assert sell_sl_tp["stop_loss"] == 102.25
    assert sell_sl_tp["take_profit"] == 96.0
    assert sell_sl_tp["risk_reward_ratio"] > 1.0

    neutral_sl_tp = calculate_daily_sl_tp(100.0, "neutral", 80.0, 5.0)
    assert neutral_sl_tp["stop_loss"] is None
    assert neutral_sl_tp["sl_tp_reason"] == "Neutral signal, SL/TP not calculated"

    clipped_strength = calculate_daily_sl_tp(100.0, "buy", 150.0, 5.0)
    assert clipped_strength["take_profit"] == 104.25

    clamped_up = calculate_daily_sl_tp(100.0, "buy", 50.0, 0.5)
    assert clamped_up["atr_percent_1d"] == 0.005
    assert clamped_up["usable_atr_1d"] == 1.5
    assert "clamped up" in clamped_up["sl_tp_reason"]

    clamped_down = calculate_daily_sl_tp(100.0, "buy", 50.0, 20.0)
    assert clamped_down["atr_percent_1d"] == 0.2
    assert clamped_down["usable_atr_1d"] == 12.0
    assert "clamped down" in clamped_down["sl_tp_reason"]

    low_price_sell = calculate_daily_sl_tp(0.001, "sell", 100.0, 0.01)
    assert low_price_sell["take_profit"] > 0

    invalid_price = calculate_daily_sl_tp(0.0, "buy", 80.0, 5.0)
    assert invalid_price["sl_tp_reason"] == "Invalid current price"

    invalid_atr = calculate_daily_sl_tp(100.0, "buy", 80.0, 0.0)
    assert invalid_atr["sl_tp_reason"] == "Invalid ATR"

    def failing_loader(*args: Any, **kwargs: Any) -> pd.DataFrame:
        raise RuntimeError("synthetic loader failure")

    row = process_symbol(
        "FAIL",
        now=datetime(2024, 1, 1, tzinfo=timezone.utc),
        data_loader=failing_loader,
    )
    assert row["ticker"] == "FAIL"
    assert row["direction"] == "neutral"
    assert row["signal_strength"] == 0.0
    assert row["stop_loss"] is None
    assert "synthetic loader failure" in row["reason"]
    print("Self-test passed.")


def main() -> None:
    """Parse command-line arguments and run the requested action."""

    parser = argparse.ArgumentParser(description=STRATEGY_NAME)
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV path. Defaults to signals.csv or M_AD_OUTPUT_DIR/signals.csv.",
    )
    parser.add_argument(
        "--sort",
        action="store_true",
        help="Sort by signal_strength descending.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N tickers.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run offline helper tests and exit.",
    )
    args = parser.parse_args()

    if args.self_test:
        run_self_test()
        return

    output_path = Path(args.output) if args.output else default_output_path()
    run_scan(output_path, sort_output=args.sort, limit=args.limit)


def _calculate_indicator_values(data: pd.DataFrame) -> dict[str, float]:
    close = data["close"]
    high = data["high"]
    low = data["low"]

    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    rsi14 = _calculate_rsi(close, period=14)
    macd, macd_signal, macd_histogram = _calculate_macd(close)
    atr14 = calculate_atr14(data)
    adx14 = _calculate_adx(high, low, close, period=14)

    return {
        "close": _latest_float(close),
        "ema20": _latest_float(ema20),
        "ema50": _latest_float(ema50),
        "ema200": _latest_float(ema200),
        "rsi14": _latest_float(rsi14),
        "macd": _latest_float(macd),
        "macd_signal": _latest_float(macd_signal),
        "macd_histogram": _latest_float(macd_histogram),
        "macd_histogram_previous": _series_float(macd_histogram, -2),
        "atr14": _latest_float(atr14),
        "adx14": _latest_float(adx14),
    }


def _calculate_rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    average_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    average_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = average_gain / average_loss.replace(0.0, math.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.mask((average_loss == 0.0) & (average_gain > 0.0), 100.0)
    rsi = rsi.mask((average_loss == 0.0) & (average_gain == 0.0), 50.0)
    return rsi


def _calculate_macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    macd_histogram = macd - macd_signal
    return macd, macd_signal, macd_histogram


def calculate_atr14(data: pd.DataFrame) -> pd.Series:
    """Calculate ATR14 from OHLC data using Wilder-style smoothing.

    The input dataframe must contain normalized open, high, low, and close
    columns. If a provider includes incomplete current candles, the provider
    normalization step is expected to remove incomplete OHLC rows first. There
    is no universal reliable market-close flag across all fallback providers.
    """

    high = data["high"]
    low = data["low"]
    close = data["close"]
    previous_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - previous_close).abs()
    tr3 = (low - previous_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / 14, adjust=False).mean()


def _calculate_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int,
) -> pd.Series:
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _calculate_adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int,
) -> pd.Series:
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        [
            up if up > down and up > 0.0 else 0.0
            for up, down in zip(up_move.fillna(0.0), down_move.fillna(0.0))
        ],
        index=high.index,
    )
    minus_dm = pd.Series(
        [
            down if down > up and down > 0.0 else 0.0
            for up, down in zip(up_move.fillna(0.0), down_move.fillna(0.0))
        ],
        index=high.index,
    )
    atr = _calculate_atr(high, low, close, period)
    plus_di = 100.0 * (
        plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        / atr.replace(0.0, math.nan)
    )
    minus_di = 100.0 * (
        minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        / atr.replace(0.0, math.nan)
    )
    denominator = (plus_di + minus_di).replace(0.0, math.nan)
    dx = ((plus_di - minus_di).abs() / denominator) * 100.0
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _extract_indicator_with_aliases(ta_result: Any, indicator_name: str) -> float | None:
    aliases = {
        "EMA20": ("EMA20", "EMA 20", "EMA_20"),
        "EMA50": ("EMA50", "EMA 50", "EMA_50"),
        "EMA200": ("EMA200", "EMA 200", "EMA_200"),
        "RSI14": ("RSI14", "RSI 14", "RSI_14"),
        "MACD": ("MACD",),
        "MACD_signal": ("MACD_signal", "MACD signal", "MACD Signal"),
        "MACD_histogram": (
            "MACD_histogram",
            "MACD histogram",
            "MACD Histogram",
        ),
        "ADX14": ("ADX14", "ADX 14", "ADX_14"),
        "ATR14": ("ATR14", "ATR 14", "ATR_14"),
    }
    for alias in aliases.get(indicator_name, (indicator_name,)):
        value = extract_latest_indicator(ta_result, alias)
        if value is not None:
            return value
    return None


def _result_row(
    ticker: str,
    current_price: float | None,
    direction: str,
    signal_strength: float,
    daily_trend_score: float,
    h4_trend_score: float,
    h4_momentum_score: float,
    h1_confirmation_score: float,
    rsi_score: float,
    adx_score: float,
    candle_score: float,
    sentiment_score: float,
    final_long_score: float,
    final_short_score: float,
    reason: str,
    timestamp_utc: datetime,
    sl_tp: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sl_tp_values = sl_tp or _empty_sl_tp_result(None, "SL/TP not calculated")
    return {
        "ticker": ticker,
        "current_price": smart_round_price(current_price) if current_price else None,
        "direction": direction,
        "signal_strength": round(clip_score(signal_strength), 2),
        "daily_trend_score": round(clip_score(daily_trend_score), 2),
        "h4_trend_score": round(clip_score(h4_trend_score), 2),
        "h4_momentum_score": round(clip_score(h4_momentum_score), 2),
        "h1_confirmation_score": round(clip_score(h1_confirmation_score), 2),
        "rsi_score": round(clip_score(rsi_score), 2),
        "adx_score": round(clip_score(adx_score), 2),
        "candle_score": round(clip_score(candle_score), 2),
        "sentiment_score": round(max(-100.0, min(100.0, sentiment_score)), 2),
        "final_long_score": round(clip_score(final_long_score), 2),
        "final_short_score": round(clip_score(final_short_score), 2),
        "reason": reason,
        "timestamp_utc": timestamp_utc.astimezone(timezone.utc).isoformat(),
        **sl_tp_values,
    }


def _neutral_error_row(
    ticker: str,
    reason: str,
    timestamp_utc: datetime,
) -> dict[str, Any]:
    return _result_row(
        ticker=ticker,
        current_price=None,
        direction="neutral",
        signal_strength=0.0,
        daily_trend_score=0.0,
        h4_trend_score=0.0,
        h4_momentum_score=0.0,
        h1_confirmation_score=0.0,
        rsi_score=0.0,
        adx_score=0.0,
        candle_score=0.0,
        sentiment_score=0.0,
        final_long_score=0.0,
        final_short_score=0.0,
        reason=reason[:500],
        timestamp_utc=timestamp_utc,
        sl_tp=_empty_sl_tp_result(None, reason[:500]),
    )


def _empty_sl_tp_result(atr_1d: float | None, reason: str) -> dict[str, Any]:
    return {
        "atr_1d": atr_1d,
        "atr_percent_1d": None,
        "usable_atr_1d": None,
        "sl_distance": None,
        "tp_distance": None,
        "stop_loss": None,
        "take_profit": None,
        "risk_reward_ratio": None,
        "sl_tp_reason": reason,
    }


def clip_score(value: float) -> float:
    """Clip a numeric score to the 0..100 range."""

    if not math.isfinite(float(value)):
        return 0.0
    return max(0.0, min(100.0, float(value)))


def _safe_return(latest: float, previous: float) -> float:
    previous_value = float(previous)
    if not math.isfinite(previous_value) or previous_value == 0.0:
        return 0.0
    return float(latest) / previous_value - 1.0


def _latest_float(series: pd.Series) -> float:
    return _series_float(series, -1)


def _series_float(series: pd.Series, index: int) -> float:
    if len(series) < abs(index):
        return math.nan
    value = _to_float(series.iloc[index])
    return value if value is not None else math.nan


def _to_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _normalize_pattern(value: str) -> str:
    return re.sub(r"[\s_-]+", " ", value.strip().lower())


@contextmanager
def _timed_log_task(task_name: str, **context: Any):
    """Log elapsed time for signal-analysis internals when logging is configured."""

    context_text = ""
    if context:
        context_text = " [" + ", ".join(
            f"{key}={value}" for key, value in context.items()
        ) + "]"
    LOGGER.info("START %s%s", task_name, context_text)
    started_at = perf_counter()
    try:
        yield
    except Exception:
        elapsed = perf_counter() - started_at
        LOGGER.exception("FAILED %s after %.3fs%s", task_name, elapsed, context_text)
        raise
    else:
        elapsed = perf_counter() - started_at
        LOGGER.info("DONE %s in %.3fs%s", task_name, elapsed, context_text)


if __name__ == "__main__":
    main()
