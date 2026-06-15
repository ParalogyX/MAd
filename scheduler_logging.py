"""Logging setup and timing helpers for scheduler scripts."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from runtime_paths import ensure_runtime_directories, logs_dir
from scheduler_config import LOG_PREFIX, LOG_RETENTION_DAYS, OUTPUT_DIR

LOGGER = logging.getLogger("trade_signal_generator")
LOGGER.addHandler(logging.NullHandler())
LOGGER.propagate = False

_LOGGING_CONFIGURED = False
_LOG_HANDLER: logging.FileHandler | None = None
_LOG_DATE: date | None = None


def setup_logging() -> Path:
    """Configure file logging for scheduler timings and operational events."""

    global _LOGGING_CONFIGURED
    ensure_runtime_directories(OUTPUT_DIR)
    if _LOGGING_CONFIGURED:
        return ensure_daily_logging()

    LOGGER.setLevel(logging.INFO)
    _LOGGING_CONFIGURED = True
    log_path = ensure_daily_logging()
    LOGGER.propagate = False
    LOGGER.info("Logging initialized at %s", log_path)
    return log_path


def ensure_daily_logging() -> Path:
    """Ensure the logger writes to today's dated log file."""

    global _LOG_HANDLER, _LOG_DATE
    today = datetime.now().date()
    log_path = log_file_path(today)
    if _LOG_HANDLER is not None and _LOG_DATE == today:
        return log_path

    log_path.parent.mkdir(parents=True, exist_ok=True)
    if _LOG_HANDLER is not None:
        LOGGER.removeHandler(_LOG_HANDLER)
        _LOG_HANDLER.close()

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    LOGGER.addHandler(handler)
    _LOG_HANDLER = handler
    _LOG_DATE = today
    cleanup_old_logs()
    return log_path


def log_file_path(log_date: date | None = None) -> Path:
    """Return the dated scheduler log path for a date."""

    selected_date = log_date or datetime.now().date()
    return logs_dir(OUTPUT_DIR) / f"{LOG_PREFIX}_{selected_date:%Y-%m-%d}.log"


def cleanup_old_logs(retention_days: int = LOG_RETENTION_DAYS) -> None:
    """Delete log files older than the retention window."""

    cutoff = datetime.now().timestamp() - retention_days * 24 * 60 * 60
    for path in logs_dir(OUTPUT_DIR).glob("*.log"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


@contextmanager
def timed_task(task_name: str, **context: Any):
    """Log task start, success/failure, and elapsed time."""

    if _LOGGING_CONFIGURED:
        ensure_daily_logging()
    context_text = _format_log_context(context)
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


def _format_log_context(context: dict[str, Any]) -> str:
    if not context:
        return ""
    parts = [f"{key}={value}" for key, value in context.items()]
    return " [" + ", ".join(parts) + "]"
