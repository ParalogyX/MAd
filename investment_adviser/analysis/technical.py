"""Technical indicator calculations for normalized OHLCV data."""

from __future__ import annotations

import numpy as np
import pandas as pd

from investment_adviser.utils.validation import normalize_market_data_frame


def perform_technical_analysis(data: pd.DataFrame) -> pd.DataFrame:
    """Calculate the latest technical indicator values for OHLCV data.

    The function returns indicator values only. It does not provide trading
    recommendations or execution signals.
    """

    market_data = normalize_market_data_frame(data)
    close = market_data["close"]
    high = market_data["high"]
    low = market_data["low"]
    volume = market_data["volume"]

    sma_20 = close.rolling(window=20, min_periods=20).mean()
    sma_50 = close.rolling(window=50, min_periods=50).mean()
    ema_20 = close.ewm(span=20, adjust=False, min_periods=20).mean()
    ema_50 = close.ewm(span=50, adjust=False, min_periods=50).mean()

    rsi_14 = _calculate_rsi(close, period=14)
    macd, macd_signal, macd_histogram = _calculate_macd(close)
    bb_middle, bb_upper, bb_lower = _calculate_bollinger_bands(close, period=20)
    atr_14 = _calculate_atr(high, low, close, period=14)
    stochastic_k, stochastic_d = _calculate_stochastic(high, low, close, period=14)
    adx_14 = _calculate_adx(high, low, close, period=14)

    latest_sma_20 = _latest(sma_20)
    latest_sma_50 = _latest(sma_50)
    latest_rsi = _latest(rsi_14)
    latest_macd = _latest(macd)
    latest_macd_signal = _latest(macd_signal)

    rows = [
        _indicator_row(
            "SMA 20",
            latest_sma_20,
            "trend",
            _trend_signal(latest_sma_20, latest_sma_50),
            "20-period simple moving average.",
        ),
        _indicator_row(
            "SMA 50",
            latest_sma_50,
            "trend",
            _trend_signal(latest_sma_20, latest_sma_50),
            "50-period simple moving average.",
        ),
        _indicator_row(
            "EMA 20",
            _latest(ema_20),
            "trend",
            "neutral",
            "20-period exponential moving average.",
        ),
        _indicator_row(
            "EMA 50",
            _latest(ema_50),
            "trend",
            "neutral",
            "50-period exponential moving average.",
        ),
        _indicator_row(
            "RSI 14",
            latest_rsi,
            "momentum",
            _rsi_signal(latest_rsi),
            "14-period relative strength index.",
        ),
        _indicator_row(
            "MACD",
            latest_macd,
            "momentum",
            _macd_signal_label(latest_macd, latest_macd_signal),
            "MACD line using 12-period and 26-period EMAs.",
        ),
        _indicator_row(
            "MACD signal",
            latest_macd_signal,
            "momentum",
            _macd_signal_label(latest_macd, latest_macd_signal),
            "9-period EMA of the MACD line.",
        ),
        _indicator_row(
            "MACD histogram",
            _latest(macd_histogram),
            "momentum",
            "neutral",
            "Difference between MACD and MACD signal.",
        ),
        _indicator_row(
            "Bollinger Bands upper",
            _latest(bb_upper),
            "volatility",
            "neutral",
            "Upper Bollinger Band: SMA 20 plus two standard deviations.",
        ),
        _indicator_row(
            "Bollinger Bands middle",
            _latest(bb_middle),
            "volatility",
            "neutral",
            "Middle Bollinger Band: SMA 20.",
        ),
        _indicator_row(
            "Bollinger Bands lower",
            _latest(bb_lower),
            "volatility",
            "neutral",
            "Lower Bollinger Band: SMA 20 minus two standard deviations.",
        ),
        _indicator_row(
            "ATR 14",
            _latest(atr_14),
            "volatility",
            "neutral",
            "14-period average true range.",
        ),
        _indicator_row(
            "Stochastic %K",
            _latest(stochastic_k),
            "momentum",
            _stochastic_signal(_latest(stochastic_k)),
            "14-period stochastic oscillator %K.",
        ),
        _indicator_row(
            "Stochastic %D",
            _latest(stochastic_d),
            "momentum",
            _stochastic_signal(_latest(stochastic_d)),
            "3-period average of stochastic %K.",
        ),
        _indicator_row(
            "ADX 14",
            _latest(adx_14),
            "trend_strength",
            _adx_signal(_latest(adx_14)),
            "14-period average directional index.",
        ),
        _indicator_row(
            "Latest close",
            _latest(close),
            "price",
            "neutral",
            "Latest close price in the input data.",
        ),
        _indicator_row(
            "Latest volume",
            _latest(volume),
            "volume",
            "neutral",
            "Latest volume in the input data.",
        ),
    ]
    return pd.DataFrame(rows)


def _calculate_rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    average_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    average_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = average_gain / average_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.mask((average_loss == 0.0) & (average_gain > 0.0), 100.0)
    rsi = rsi.mask((average_loss == 0.0) & (average_gain == 0.0), 50.0)
    return rsi


def _calculate_macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema_26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    macd = ema_12 - ema_26
    signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    histogram = macd - signal
    return macd, signal, histogram


def _calculate_bollinger_bands(
    close: pd.Series,
    period: int,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    middle = close.rolling(window=period, min_periods=period).mean()
    deviation = close.rolling(window=period, min_periods=period).std(ddof=0)
    upper = middle + 2.0 * deviation
    lower = middle - 2.0 * deviation
    return middle, upper, lower


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
    return true_range.rolling(window=period, min_periods=period).mean()


def _calculate_stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int,
) -> tuple[pd.Series, pd.Series]:
    lowest_low = low.rolling(window=period, min_periods=period).min()
    highest_high = high.rolling(window=period, min_periods=period).max()
    denominator = (highest_high - lowest_low).replace(0.0, np.nan)
    percent_k = ((close - lowest_low) / denominator) * 100.0
    percent_d = percent_k.rolling(window=3, min_periods=3).mean()
    return percent_k, percent_d


def _calculate_adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int,
) -> pd.Series:
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0.0), up_move, 0.0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0.0), down_move, 0.0),
        index=high.index,
    )
    atr = _calculate_atr(high, low, close, period)
    plus_di = 100.0 * (
        plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        / atr
    )
    minus_di = 100.0 * (
        minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        / atr
    )
    denominator = (plus_di + minus_di).replace(0.0, np.nan)
    dx = ((plus_di - minus_di).abs() / denominator) * 100.0
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _indicator_row(
    name: str,
    value: float,
    category: str,
    signal: str,
    description: str,
) -> dict[str, object]:
    return {
        "indicator_name": name,
        "indicator_value": value,
        "indicator_category": category,
        "signal": signal,
        "description": description,
    }


def _latest(series: pd.Series) -> float:
    if series.empty:
        return float("nan")
    value = series.iloc[-1]
    if pd.isna(value):
        return float("nan")
    return float(value)


def _rsi_signal(value: float) -> str:
    if np.isnan(value):
        return "insufficient data"
    if value > 70.0:
        return "overbought"
    if value < 30.0:
        return "oversold"
    return "neutral"


def _trend_signal(sma_20: float, sma_50: float) -> str:
    if np.isnan(sma_20) or np.isnan(sma_50):
        return "insufficient data"
    if sma_20 > sma_50:
        return "bullish trend"
    if sma_20 < sma_50:
        return "bearish trend"
    return "neutral"


def _macd_signal_label(macd: float, signal: float) -> str:
    if np.isnan(macd) or np.isnan(signal):
        return "insufficient data"
    if macd > signal:
        return "bullish momentum"
    if macd < signal:
        return "bearish momentum"
    return "neutral"


def _stochastic_signal(value: float) -> str:
    if np.isnan(value):
        return "insufficient data"
    if value > 80.0:
        return "overbought"
    if value < 20.0:
        return "oversold"
    return "neutral"


def _adx_signal(value: float) -> str:
    if np.isnan(value):
        return "insufficient data"
    if value >= 25.0:
        return "strong trend"
    return "weak trend"
