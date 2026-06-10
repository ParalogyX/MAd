"""Extract the strongest buy/sell signals from signals.csv.

The script only reads and writes CSV files. It does not perform trading,
broker login, or order execution.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def find_best_signals(
    input_path: Path,
    output_path: Path,
    top_n: int = 5,
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

    filtered.to_csv(output_path, index=False)
    return filtered


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
    parser.add_argument("--top", type=int, default=5, help="Number of rows to save.")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else default_output_path()
    result = find_best_signals(input_path, output_path, top_n=args.top)
    print(f"Saved {len(result)} rows to {output_path}")


if __name__ == "__main__":
    main()
