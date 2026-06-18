"""Persistent SQLite ledger for MT5 order execution.

The ledger is intentionally separate from the existing signal and trade-plan
CSVs.  It stores bot-owned execution state so repeated scheduler runs,
application restarts, and command retries do not open duplicate positions.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LEDGER_COLUMNS = [
    "plan_id",
    "source_trade_plan",
    "symbol",
    "direction",
    "planned_sl",
    "planned_tp",
    "requested_eur_notional",
    "requested_volume",
    "actual_volume",
    "estimated_actual_exposure",
    "mt5_order_ticket",
    "mt5_deal_ticket",
    "mt5_position_ticket",
    "magic_number",
    "opening_timestamp",
    "opening_price",
    "execution_status",
    "failure_reason",
    "closing_timestamp",
    "closing_price",
    "closing_deal",
    "closing_reason",
    "realised_profit_loss",
    "commission",
    "swap",
    "comment",
    "created_at",
    "updated_at",
]


class ExecutionLedger:
    """SQLite-backed store for order execution lifecycle records."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.initialize()

    def initialize(self) -> None:
        """Create the execution table when it does not exist."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS executions (
                    plan_id TEXT PRIMARY KEY,
                    source_trade_plan TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    planned_sl REAL,
                    planned_tp REAL,
                    requested_eur_notional REAL,
                    requested_volume REAL,
                    actual_volume REAL,
                    estimated_actual_exposure REAL,
                    mt5_order_ticket TEXT,
                    mt5_deal_ticket TEXT,
                    mt5_position_ticket TEXT,
                    magic_number INTEGER,
                    opening_timestamp TEXT,
                    opening_price REAL,
                    execution_status TEXT NOT NULL,
                    failure_reason TEXT,
                    closing_timestamp TEXT,
                    closing_price REAL,
                    closing_deal TEXT,
                    closing_reason TEXT,
                    realised_profit_loss REAL,
                    commission REAL,
                    swap REAL,
                    comment TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def get(self, plan_id: str) -> dict[str, Any] | None:
        """Return one ledger row by plan ID."""

        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM executions WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
        return dict(row) if row else None

    def upsert_pending(self, values: dict[str, Any]) -> None:
        """Insert a pending row or update non-final planning fields."""

        now = utc_now_text()
        record = {column: values.get(column) for column in LEDGER_COLUMNS}
        record["created_at"] = record.get("created_at") or now
        record["updated_at"] = now
        record["execution_status"] = record.get("execution_status") or "pending"

        columns = LEDGER_COLUMNS
        placeholders = ", ".join(["?"] * len(columns))
        update_columns = [
            column
            for column in columns
            if column not in {"plan_id", "created_at", "execution_status"}
        ]
        update_clause = ", ".join(f"{column} = excluded.{column}" for column in update_columns)
        with self._lock, self._connect() as connection:
            connection.execute(
                f"""
                INSERT INTO executions ({", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(plan_id) DO UPDATE SET {update_clause}
                """,
                [record.get(column) for column in columns],
            )

    def mark_failed(self, plan_id: str, reason: str) -> None:
        """Store a controlled execution failure."""

        self.update(
            plan_id,
            execution_status="failed",
            failure_reason=reason[:1000],
        )

    def mark_opened(
        self,
        plan_id: str,
        *,
        status: str,
        actual_volume: float,
        opening_price: float,
        order_ticket: str | None = None,
        deal_ticket: str | None = None,
        position_ticket: str | None = None,
        requested_volume: float | None = None,
        estimated_actual_exposure: float | None = None,
    ) -> None:
        """Store successful or partial opening details."""

        updates: dict[str, Any] = {
            "execution_status": status,
            "actual_volume": actual_volume,
            "opening_price": opening_price,
            "opening_timestamp": utc_now_text(),
            "mt5_order_ticket": order_ticket,
            "mt5_deal_ticket": deal_ticket,
            "mt5_position_ticket": position_ticket,
            "failure_reason": None,
        }
        if requested_volume is not None:
            updates["requested_volume"] = requested_volume
        if estimated_actual_exposure is not None:
            updates["estimated_actual_exposure"] = estimated_actual_exposure
        self.update(plan_id, **updates)

    def mark_closed(
        self,
        plan_id: str,
        *,
        closing_price: float | None,
        closing_deal: str | None,
        closing_reason: str,
        realised_profit_loss: float | None = None,
        commission: float | None = None,
        swap: float | None = None,
    ) -> None:
        """Store closing details for an executed plan."""

        self.update(
            plan_id,
            execution_status="closed",
            closing_timestamp=utc_now_text(),
            closing_price=closing_price,
            closing_deal=closing_deal,
            closing_reason=closing_reason,
            realised_profit_loss=realised_profit_loss,
            commission=commission,
            swap=swap,
        )

    def update(self, plan_id: str, **values: Any) -> None:
        """Update selected columns for a row."""

        if not values:
            return
        cleaned = {
            column: value
            for column, value in values.items()
            if column in LEDGER_COLUMNS and column != "plan_id"
        }
        cleaned["updated_at"] = utc_now_text()
        assignments = ", ".join(f"{column} = ?" for column in cleaned)
        parameters = list(cleaned.values()) + [plan_id]
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE executions SET {assignments} WHERE plan_id = ?",
                parameters,
            )

    def list_open(self, magic_number: int | None = None) -> list[dict[str, Any]]:
        """Return rows that are expected to have an open broker position."""

        query = (
            "SELECT * FROM executions WHERE execution_status "
            "IN ('opened', 'opened_partial')"
        )
        parameters: tuple[Any, ...] = ()
        if magic_number is not None:
            query += " AND magic_number = ?"
            parameters = (magic_number,)
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection


def utc_now_text() -> str:
    """Return an ISO timestamp in UTC."""

    return datetime.now(timezone.utc).isoformat()
