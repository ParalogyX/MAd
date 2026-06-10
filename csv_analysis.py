"""Extract the strongest buy/sell signals from signals.csv.

The script only reads and writes CSV files. It does not perform trading,
broker login, or order execution.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from investment_adviser import load_symbol_data

NUMBER_OF_SIGNALS = 10

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

    filtered.insert(
        loc=1,
        column="current_price",
        value=[
            get_current_price(str(ticker), price_loader=price_loader)
            for ticker in filtered["ticker"]
        ],
    )

    filtered.to_csv(output_path, index=False)
    return filtered


def get_current_price(
    ticker: str,
    price_loader: Callable[..., pd.DataFrame] = load_symbol_data,
) -> float | None:
    """Return latest available close price for a ticker.

    Public providers do not guarantee a live quote endpoint for every symbol,
    so this uses the latest available close. It tries recent 1H candles first
    and falls back to recent 1D candles.
    """

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
            return round(float(close_values.iloc[-1]), 8)
        except Exception:
            continue
    return None


def default_output_path() -> Path:
    """Return best_signals_YYYYMMDD.csv for the current UTC date."""

    date_text = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Path(f"best_signals_{date_text}.csv")


def main() -> None:
    """Parse command-line arguments and write the best-signal CSV."""

    parser = argparse.ArgumentParser(
        description="Find the strongest buy/sell signals from signals.csv.",
    )
    parser.add_argument("--input", default="signals.csv", help="Input CSV path.")
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV path. Defaults to best_signals_YYYYMMDD.csv.",
    )
    parser.add_argument("--top", type=int, default=NUMBER_OF_SIGNALS, help="Number of rows to save.")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else default_output_path()
    result = find_best_signals(input_path, output_path, top_n=args.top)
    print(f"Saved {len(result)} rows to {output_path}")


def _find_column(data: pd.DataFrame, wanted: str) -> str | None:
    wanted_normalized = wanted.lower()
    for column in data.columns:
        if str(column).lower() == wanted_normalized:
            return str(column)
    return None


if __name__ == "__main__":
    main()
