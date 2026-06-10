"""Validation and normalization helpers for public inputs and DataFrames."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from investment_adviser.config import SUPPORTED_TIMEFRAMES
from investment_adviser.exceptions import ValidationError

MARKET_DATA_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
OHLCV_NUMERIC_COLUMNS = ["open", "high", "low", "close", "volume"]


def normalize_symbol(symbol: str) -> str:
    """Normalize a user-supplied market symbol."""

    if not isinstance(symbol, str):
        raise ValueError("Symbol must be a string.")
    normalized = symbol.strip().upper()
    if not normalized:
        raise ValueError("Symbol must not be empty.")
    return normalized


def validate_timeframe(timeframe: str) -> str:
    """Validate and normalize a candle timeframe string."""

    if not isinstance(timeframe, str):
        raise ValueError("Timeframe must be a string.")
    normalized = timeframe.strip().lower()
    if normalized not in SUPPORTED_TIMEFRAMES:
        supported = ", ".join(SUPPORTED_TIMEFRAMES)
        raise ValueError(f"Unsupported timeframe '{timeframe}'. Choose one of: {supported}.")
    return normalized


def ensure_utc_datetime(value: datetime, name: str) -> datetime:
    """Return a timezone-aware UTC datetime."""

    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime.")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def validate_time_range(
    begin_time: datetime,
    end_time: datetime,
) -> tuple[datetime, datetime]:
    """Validate and normalize a begin/end datetime range."""

    begin = ensure_utc_datetime(begin_time, "begin_time")
    end = ensure_utc_datetime(end_time, "end_time")
    if begin >= end:
        raise ValueError("begin_time must be before end_time.")
    return begin, end


def normalize_market_data_frame(data: pd.DataFrame) -> pd.DataFrame:
    """Validate, normalize, sort, and de-duplicate an OHLCV DataFrame."""

    if not isinstance(data, pd.DataFrame):
        raise ValidationError("Market data must be a pandas DataFrame.")
    missing_columns = [column for column in MARKET_DATA_COLUMNS if column not in data]
    if missing_columns:
        raise ValidationError(f"Missing OHLCV columns: {', '.join(missing_columns)}.")

    normalized = data[MARKET_DATA_COLUMNS].copy()
    normalized["timestamp"] = pd.to_datetime(
        normalized["timestamp"],
        utc=True,
        errors="coerce",
    )
    if normalized["timestamp"].isna().any():
        raise ValidationError("timestamp contains invalid datetime values.")

    for column in OHLCV_NUMERIC_COLUMNS:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
        if normalized[column].isna().any():
            raise ValidationError(f"{column} contains non-numeric values.")

    normalized = (
        normalized.drop_duplicates(subset=["timestamp"], keep="last")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    return normalized
