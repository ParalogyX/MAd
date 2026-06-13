"""Extract the strongest buy/sell signals from signals.csv.

The script only reads and writes CSV files. It does not perform trading,
broker login, or order execution.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from investment_adviser import load_symbol_data
from investment_adviser.providers.mt5 import MT5MarketDataProvider
from find_signal import (
    calculate_atr14,
    calculate_daily_sl_tp,
    normalize_ohlcv_columns,
    smart_round_price,
)

NUMBER_OF_SIGNALS = 10
OUTPUT_DIR_ENV_VAR = "M_AD_OUTPUT_DIR"
SL_TP_COLUMNS = [
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
_LIVE_PRICE_PROVIDER = MT5MarketDataProvider()


def find_best_signals(
    input_path: Path,
    output_path: Path,
    top_n: int = NUMBER_OF_SIGNALS,
    price_loader: Callable[..., pd.DataFrame] = load_symbol_data,
) -> pd.DataFrame:
    """Read signal rows and write the strongest buy/sell signals."""

    if top_n <= 0:
        raise ValueError("top_n must be greater than zero.")
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    signals = pd.read_csv(input_path)
    required_columns = {"ticker", "direction", "signal_strength"}
    missing = required_columns - set(signals.columns)
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")

    filtered = signals[
        signals["direction"].astype(str).str.lower().isin({"buy", "sell"})
    ].copy()
    filtered["signal_strength"] = pd.to_numeric(
        filtered["signal_strength"],
        errors="coerce",
    )
    filtered = filtered.dropna(subset=["signal_strength"])
    filtered = filtered.sort_values(
        "signal_strength",
        ascending=False,
        kind="mergesort",
    ).head(top_n)

    filtered = enrich_with_current_prices(filtered, price_loader=price_loader)
    filtered = enrich_with_sl_tp(filtered, price_loader=price_loader)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    filtered.to_csv(output_path, index=False)
    return filtered


def enrich_with_current_prices(
    signals: pd.DataFrame,
    price_loader: Callable[..., pd.DataFrame] = load_symbol_data,
) -> pd.DataFrame:
    """Ensure selected rows have a current_price column."""

    enriched = signals.copy()
    if "current_price" not in enriched.columns:
        enriched.insert(loc=1, column="current_price", value=None)

    for index, row in enriched.iterrows():
        existing_price = _to_float(row.get("current_price"))
        current_price = get_current_price(
            str(row["ticker"]),
            price_loader=price_loader,
            side=str(row.get("direction", "")),
        )
        if current_price is None or current_price <= 0:
            current_price = existing_price
        enriched.at[index, "current_price"] = current_price

    current_price = enriched.pop("current_price")
    enriched.insert(loc=1, column="current_price", value=current_price)
    return enriched


def enrich_with_sl_tp(
    signals: pd.DataFrame,
    price_loader: Callable[..., pd.DataFrame] = load_symbol_data,
) -> pd.DataFrame:
    """Ensure selected rows have ATR-based SL/TP columns."""

    enriched = signals.copy()
    for column in SL_TP_COLUMNS:
        if column not in enriched.columns:
            enriched[column] = None

    for index, row in enriched.iterrows():
        current_price = _to_float(row.get("current_price"))
        atr_1d = _to_float(row.get("atr_1d"))
        if atr_1d is None or atr_1d <= 0:
            atr_1d = get_daily_atr(
                str(row["ticker"]),
                price_loader=price_loader,
            )

        sl_tp = calculate_daily_sl_tp(
            current_price=current_price,
            direction=str(row.get("direction", "")),
            signal_strength=_to_float(row.get("signal_strength")) or 0.0,
            atr_1d=atr_1d,
        )
        for column, value in sl_tp.items():
            enriched.at[index, column] = value

    return enriched


def get_current_price(
    ticker: str,
    price_loader: Callable[..., pd.DataFrame] = load_symbol_data,
    side: str | None = None,
) -> float | None:
    """Return the latest available current price for a ticker.

    MT5 tick data is preferred because candle closes can remain unchanged until
    the next candle completes. If a live tick is unavailable, the function falls
    back to recent candle closes so CSV generation can continue.
    """

    try:
        live_price = _LIVE_PRICE_PROVIDER.get_current_price(ticker, side=side)
        return smart_round_price(float(live_price))
    except Exception:
        pass

    now = datetime.now(timezone.utc)
    attempts = [
        ("1h", now - timedelta(days=10)),
        ("1d", now - timedelta(days=45)),
    ]
    for timeframe, begin_time in attempts:
        try:
            data = price_loader(
                symbol=ticker,
                timeframe=timeframe,
                begin_time=begin_time,
                end_time=now,
                provider="fallback",
            )
            close_column = _find_column(data, "close")
            if close_column is None or data.empty:
                continue
            close_values = pd.to_numeric(data[close_column], errors="coerce").dropna()
            if close_values.empty:
                continue
            return smart_round_price(float(close_values.iloc[-1]))
        except Exception:
            continue
    return None


def get_daily_atr(
    ticker: str,
    price_loader: Callable[..., pd.DataFrame] = load_symbol_data,
) -> float | None:
    """Return latest ATR14 from recent daily candles, or None on failure."""

    now = datetime.now(timezone.utc)
    try:
        data = price_loader(
            symbol=ticker,
            timeframe="1d",
            begin_time=now - timedelta(days=800),
            end_time=now,
            provider="fallback",
        )
        normalized = normalize_ohlcv_columns(data)
        if len(normalized) < 14:
            return None
        atr = calculate_atr14(normalized)
        value = pd.to_numeric(atr, errors="coerce").dropna()
        if value.empty:
            return None
        return float(value.iloc[-1])
    except Exception:
        return None


def default_output_path() -> Path:
    """Return best_signals_YYYYMMDD.csv for the current UTC date."""

    date_text = datetime.now(timezone.utc).strftime("%Y%m%d")
    return default_output_dir() / f"best_signals_{date_text}.csv"


def default_input_path() -> Path:
    """Return signals.csv from M_AD_OUTPUT_DIR or the current directory."""

    return default_output_dir() / "signals.csv"


def default_output_dir() -> Path:
    """Return the configured output directory for generated CSV files."""

    return Path(os.getenv(OUTPUT_DIR_ENV_VAR, ".")).expanduser()


def main() -> None:
    """Parse command-line arguments and write the best-signal CSV."""

    parser = argparse.ArgumentParser(
        description="Find the strongest buy/sell signals from signals.csv.",
    )
    parser.add_argument(
        "--input",
        default=None,
        help="Input CSV path. Defaults to signals.csv or M_AD_OUTPUT_DIR/signals.csv.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV path. Defaults to best_signals_YYYYMMDD.csv.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=NUMBER_OF_SIGNALS,
        help="Number of rows to save.",
    )
    args = parser.parse_args()

    input_path = Path(args.input) if args.input else default_input_path()
    output_path = Path(args.output) if args.output else default_output_path()
    result = find_best_signals(input_path, output_path, top_n=args.top)
    print(f"Saved {len(result)} rows to {output_path}")


def _find_column(data: pd.DataFrame, wanted: str) -> str | None:
    wanted_normalized = wanted.lower()
    for column in data.columns:
        if str(column).lower() == wanted_normalized:
            return str(column)
    return None


def _to_float(value: object) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(numeric):
        return None
    return numeric


if __name__ == "__main__":
    main()
