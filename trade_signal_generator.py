"""Lightweight scheduled signal generation loop.

The script waits for either:
- a scheduled weekday run at 08:45 local time, or
- the console command configured by TRIGGER_COMMAND.

It runs the existing analysis scripts and writes a compact CSV for the current
day. It does not place trades, connect to broker execution APIs, size
positions, or send orders.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR_ENV_VAR = "M_AD_OUTPUT_DIR"
OUTPUT_DIR = Path(os.getenv(OUTPUT_DIR_ENV_VAR, str(PROJECT_ROOT))).expanduser()
SCHEDULED_TIME = time(hour=8, minute=45)
BEST_SIGNALS_PATTERN = "best_signals_*.csv"
TRIGGER_COMMAND = "now"
EXIT_COMMAND = "stop"


def console_listener(commands: queue.Queue[str]) -> None:
    """Read console input without keeping the main scheduler busy."""

    while True:
        try:
            command = input().strip()
        except EOFError:
            return
        commands.put(command)


def next_weekday_run(now: datetime) -> datetime:
    """Return the next Monday-Friday 08:45 local run time."""

    candidate = now.replace(
        hour=SCHEDULED_TIME.hour,
        minute=SCHEDULED_TIME.minute,
        second=0,
        microsecond=0,
    )
    if now.weekday() < 5 and now < candidate:
        return candidate

    candidate = candidate + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate = candidate + timedelta(days=1)
    return candidate


def run_signal_pipeline() -> Path:
    """Run find_signal.py and csv_analysis.py, then create today's CSV."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now()
    print("Starting signal generation...", flush=True)

    find_signal_script = existing_script_path("find_signals.py", "find_signal.py")
    csv_analysis_script = existing_script_path("csv_analysis.py")
    signals_path = OUTPUT_DIR / "signals.csv"
    best_signals_path = OUTPUT_DIR / best_signals_filename()

    subprocess.run(
        [sys.executable, str(find_signal_script), "--output", str(signals_path)],
        cwd=PROJECT_ROOT,
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(csv_analysis_script),
            "--input",
            str(signals_path),
            "--output",
            str(best_signals_path),
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )

    if not best_signals_path.exists():
        best_signals_path = find_latest_best_signals_file(started_at)
    output_path = create_today_signals_csv(best_signals_path)
    print(f"Created {output_path.name}", flush=True)
    return output_path


def existing_script_path(*names: str) -> Path:
    """Return the first existing script path from a list of candidate names."""

    for name in names:
        path = PROJECT_ROOT / name
        if path.exists():
            return path
    raise FileNotFoundError(f"None of these scripts exist: {', '.join(names)}")


def find_latest_best_signals_file(started_at: datetime) -> Path:
    """Find the newest best_signals_*.csv created or updated by this run."""

    candidates = sorted(
        OUTPUT_DIR.glob(BEST_SIGNALS_PATTERN),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError("csv_analysis.py did not create best_signals_*.csv")

    started_timestamp = started_at.timestamp()
    fresh_candidates = [
        path for path in candidates if path.stat().st_mtime >= started_timestamp
    ]
    return fresh_candidates[0] if fresh_candidates else candidates[0]


def best_signals_filename(now: datetime | None = None) -> str:
    """Return the dated best-signals filename used by csv_analysis.py."""

    timestamp = now or datetime.now(timezone.utc)
    return f"best_signals_{timestamp.astimezone(timezone.utc):%Y%m%d}.csv"


def create_today_signals_csv(best_signals_path: Path) -> Path:
    """Create compact today_signals_DDMMYYYY_H-MM.csv from best signals."""

    best_signals = pd.read_csv(best_signals_path)
    required_columns = {
        "ticker",
        "current_price",
        "direction",
        "stop_loss",
        "take_profit",
    }
    missing = required_columns - set(best_signals.columns)
    if missing:
        raise ValueError(
            f"{best_signals_path.name} is missing columns: "
            f"{', '.join(sorted(missing))}"
        )

    tradable = best_signals[
        best_signals["direction"].astype(str).str.lower().isin({"buy", "sell"})
    ].copy()

    compact = pd.DataFrame(
        {
            "Ticker name": tradable["ticker"],
            "current price": tradable["current_price"],
            "direction of trading": tradable["direction"].astype(str).str.lower(),
            "Stop Loss level": tradable["stop_loss"],
            "Take Profit level": tradable["take_profit"],
        }
    )

    output_path = today_output_path(datetime.now())
    compact.to_csv(output_path, index=False)
    return output_path


def today_output_path(now: datetime) -> Path:
    """Return a Windows-safe today_signals filename.

    The requested H:MM form contains ":", which is not allowed in Windows
    filenames, so the time separator is written as "-".
    """

    date_part = now.strftime("%d%m%Y")
    time_part = f"{now.hour}-{now.minute:02d}"
    return OUTPUT_DIR / f"today_signals_{date_part}_{time_part}.csv"


def main() -> None:
    """Run the scheduler loop and listen for console commands."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    commands: queue.Queue[str] = queue.Queue()
    listener = threading.Thread(
        target=console_listener,
        args=(commands,),
        daemon=True,
    )
    listener.start()

    next_run = next_weekday_run(datetime.now())
    print(
        "trade_signal_generator.py is running. "
        f"Type '{TRIGGER_COMMAND}' to run immediately.",
        flush=True,
    )
    print(f"Type '{EXIT_COMMAND}' to stop.", flush=True)
    print(f"CSV output directory: {OUTPUT_DIR}", flush=True)
    print(f"Next scheduled run: {next_run}", flush=True)

    while True:
        now = datetime.now()
        wait_seconds = max(0.0, (next_run - now).total_seconds())
        timeout = min(wait_seconds, 60.0)

        try:
            command = commands.get(timeout=timeout)
        except queue.Empty:
            command = None

        if command is not None:
            normalized_command = command.lower()
            if normalized_command == EXIT_COMMAND.lower():
                print("Stopping trade_signal_generator.py.", flush=True)
                return
            if normalized_command == TRIGGER_COMMAND.lower():
                try:
                    run_signal_pipeline()
                except Exception as exc:
                    print(f"ERROR: {exc}", flush=True)
            else:
                print("unknown command", flush=True)

        if datetime.now() >= next_run:
            try:
                run_signal_pipeline()
            except Exception as exc:
                print(f"ERROR: {exc}", flush=True)
            next_run = next_weekday_run(datetime.now())
            print(f"Next scheduled run: {next_run}", flush=True)


if __name__ == "__main__":
    main()
