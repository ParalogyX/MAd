"""Shared filesystem paths for generated runtime files."""

from __future__ import annotations

import os
from pathlib import Path

OUTPUT_DIR_ENV_VAR = "M_AD_OUTPUT_DIR"
LOG_DIR_ENV_VAR = "M_AD_LOG_DIR"
BEST_SIGNALS_DIR_ENV_VAR = "M_AD_BEST_SIGNALS_DIR"
TRADE_PLANS_DIR_ENV_VAR = "M_AD_TRADE_PLANS_DIR"
RESULTS_DIR_ENV_VAR = "M_AD_RESULTS_DIR"

LOG_DIR_NAME = "logs"
BEST_SIGNALS_DIR_NAME = "Best signals"
TRADE_PLANS_DIR_NAME = "Trade plans"
RESULTS_DIR_NAME = "Results"


def output_root(default: str | Path = ".") -> Path:
    """Return the root directory for editable runtime files."""

    return Path(os.getenv(OUTPUT_DIR_ENV_VAR, str(default))).expanduser()


def logs_dir(root: Path | None = None) -> Path:
    """Return the directory where dated log files are stored."""

    configured = os.getenv(LOG_DIR_ENV_VAR)
    if configured:
        return Path(configured).expanduser()
    return (root or output_root()) / LOG_DIR_NAME


def best_signals_dir(root: Path | None = None) -> Path:
    """Return the directory where best-signal CSV files are stored."""

    configured = os.getenv(BEST_SIGNALS_DIR_ENV_VAR)
    if configured:
        return Path(configured).expanduser()
    return (root or output_root()) / BEST_SIGNALS_DIR_NAME


def trade_plans_dir(root: Path | None = None) -> Path:
    """Return the directory where trade-plan CSV files are stored."""

    configured = os.getenv(TRADE_PLANS_DIR_ENV_VAR)
    if configured:
        return Path(configured).expanduser()
    return (root or output_root()) / TRADE_PLANS_DIR_NAME


def results_dir(root: Path | None = None) -> Path:
    """Return the directory where close-result CSV files are stored."""

    configured = os.getenv(RESULTS_DIR_ENV_VAR)
    if configured:
        return Path(configured).expanduser()
    return (root or output_root()) / RESULTS_DIR_NAME


def signals_path(root: Path | None = None) -> Path:
    """Return the default path for the intermediate signals.csv file."""

    return (root or output_root()) / "signals.csv"


def ensure_runtime_directories(root: Path | None = None) -> None:
    """Create all generated-output directories if they are missing."""

    base = root or output_root()
    base.mkdir(parents=True, exist_ok=True)
    logs_dir(base).mkdir(parents=True, exist_ok=True)
    best_signals_dir(base).mkdir(parents=True, exist_ok=True)
    trade_plans_dir(base).mkdir(parents=True, exist_ok=True)
    results_dir(base).mkdir(parents=True, exist_ok=True)
