"""MT5 order execution for generated trade-plan CSV files.

This module is the only place where broker-side order requests are built and
sent.  The surrounding scheduler can keep generating CSV files even when MT5
rejects an order or the bridge is unavailable.
"""

from __future__ import annotations

import hashlib
import json
import math
import queue
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any

import pandas as pd

from execution_ledger import ExecutionLedger
from investment_adviser.exceptions import DataProviderError
from investment_adviser.providers.mt5 import MT5BaseProvider
from runtime_paths import execution_ledger_path
from scheduler_config import (
    ALLOW_LIVE_TRADING,
    APP_ORDER_COMMENT_PREFIX,
    AUTO_TRADE_ENABLED,
    EXECUTION_LEDGER_FILE,
    MT5_STRATEGY_MAGIC,
    MT5_TEST_MAGIC,
    MT5_TEST_SYMBOL,
    OUTPUT_DIR,
    TARGET_TRADE_NOTIONAL_EUR,
    TEST_TRADE_HOLD_SECONDS,
    TEST_TRADE_NOTIONAL_EUR,
)
from scheduler_logging import LOGGER, timed_task

FINAL_STATUSES = {"opened", "opened_partial", "closed", "failed", "skipped"}
OPEN_STATUSES = {"opened", "opened_partial"}
SUCCESS_RETCODES = {0, 10008, 10009, 10010}
TRANSIENT_RETCODES = {10004, 10012, 10020, 10021}
TEST_TRADE_STATUS_FILE = "test_trade_status.json"
TEST_TRADE_OPERATION_TIMEOUT_SECONDS = 30
TEST_TRADE_OPEN_VERIFY_SECONDS = 15
TEST_TRADE_CLOSE_VERIFY_SECONDS = 20
TEST_TRADE_WATCHDOG_EXTRA_SECONDS = 180


@dataclass(frozen=True)
class ExecutionSettings:
    """Runtime settings for normal MT5 strategy execution."""

    auto_trade_enabled: bool = AUTO_TRADE_ENABLED
    allow_live_trading: bool = ALLOW_LIVE_TRADING
    target_notional_eur: float = TARGET_TRADE_NOTIONAL_EUR
    strategy_magic: int = MT5_STRATEGY_MAGIC
    test_magic: int = MT5_TEST_MAGIC
    test_symbol: str = MT5_TEST_SYMBOL
    test_notional_eur: float = TEST_TRADE_NOTIONAL_EUR
    test_hold_seconds: int = TEST_TRADE_HOLD_SECONDS
    max_deviation_points: int = 20
    comment_prefix: str = APP_ORDER_COMMENT_PREFIX


@dataclass(frozen=True)
class TradePlanOrder:
    """One executable row from a trade-plan CSV file."""

    plan_id: str
    source_trade_plan: str
    row_index: int
    symbol: str
    direction: str
    stop_loss: float
    take_profit: float
    session_group: str
    entry_time_local: str
    close_time_local: str


@dataclass(frozen=True)
class VolumeCalculation:
    """Result of converting a EUR notional target to an MT5 lot volume."""

    volume: float
    requested_eur_notional: float
    estimated_actual_exposure_eur: float
    per_lot_exposure_eur: float


@dataclass(frozen=True)
class ExecutionOutcome:
    """Result of an execution or close attempt."""

    plan_id: str
    symbol: str
    status: str
    message: str
    order_ticket: str | None = None
    deal_ticket: str | None = None
    position_ticket: str | None = None
    actual_volume: float | None = None
    price: float | None = None


class ExecutionError(RuntimeError):
    """Controlled order-execution failure."""


class OperationTimeoutError(ExecutionError):
    """Raised when an MT5/RPyC operation does not finish in time."""


class TestTradeStatusStore:
    """Small JSON status store for console status and crash recovery hints."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (OUTPUT_DIR / TEST_TRADE_STATUS_FILE)

    def write(self, **values: Any) -> None:
        """Persist test-trade state without credentials."""

        payload = self.read()
        payload.update(values)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def read(self) -> dict[str, Any]:
        """Read the last known status, returning idle on missing/corrupt JSON."""

        if not self.path.exists():
            return {
                "state": "idle",
                "last_stage": "",
                "failure_reason": "",
            }
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {
                "state": "idle",
                "last_stage": "",
                "failure_reason": "status file unreadable",
            }
        return data if isinstance(data, dict) else {"state": "idle"}


class MT5TradeExecutor(MT5BaseProvider):
    """Open and close bot-owned MT5 positions from Trade Plan rows."""

    def __init__(
        self,
        settings: ExecutionSettings | None = None,
        ledger: ExecutionLedger | None = None,
        ledger_path: Path | None = None,
        client_factory: Any | None = None,
    ) -> None:
        super().__init__(client_factory=client_factory)
        self.settings = settings or ExecutionSettings()
        self.ledger = ledger or ExecutionLedger(
            ledger_path or execution_ledger_path(OUTPUT_DIR, EXECUTION_LEDGER_FILE)
        )

    def execute_trade_plan_file(self, trade_plan_path: Path) -> list[ExecutionOutcome]:
        """Attempt to execute each valid row in a saved trade-plan CSV."""

        if not self.settings.auto_trade_enabled:
            LOGGER.info("EXECUTION_SKIPPED auto_trade_enabled=false path=%s", trade_plan_path)
            return []
        orders = load_trade_plan_orders(trade_plan_path)
        outcomes: list[ExecutionOutcome] = []
        for order in orders:
            try:
                outcomes.append(self.execute_order(order))
            except Exception as exc:
                LOGGER.exception(
                    "EXECUTION_FAILED plan_id=%s symbol=%s",
                    order.plan_id,
                    order.symbol,
                )
                self.ledger.mark_failed(order.plan_id, str(exc))
                outcomes.append(
                    ExecutionOutcome(
                        plan_id=order.plan_id,
                        symbol=order.symbol,
                        status="failed",
                        message=str(exc),
                    )
                )
        return outcomes

    def execute_order(self, order: TradePlanOrder) -> ExecutionOutcome:
        """Open one MT5 position for a Trade Plan row."""

        LOGGER.info(
            "EXECUTION_VALIDATION_STARTED plan_id=%s symbol=%s direction=%s",
            order.plan_id,
            order.symbol,
            order.direction,
        )
        existing = self.ledger.get(order.plan_id)
        if existing is not None and str(existing.get("execution_status")) in FINAL_STATUSES:
            LOGGER.info(
                "EXECUTION_SKIPPED plan_id=%s symbol=%s reason=ledger_status_%s",
                order.plan_id,
                order.symbol,
                existing.get("execution_status"),
            )
            return ExecutionOutcome(
                plan_id=order.plan_id,
                symbol=order.symbol,
                status="skipped",
                message=f"already {existing.get('execution_status')}",
                order_ticket=_ticket_text(existing.get("mt5_order_ticket")),
                deal_ticket=_ticket_text(existing.get("mt5_deal_ticket")),
                position_ticket=_ticket_text(existing.get("mt5_position_ticket")),
            )

        self.ledger.upsert_pending(
            {
                "plan_id": order.plan_id,
                "source_trade_plan": order.source_trade_plan,
                "symbol": order.symbol,
                "direction": order.direction,
                "planned_sl": order.stop_loss,
                "planned_tp": order.take_profit,
                "requested_eur_notional": self.settings.target_notional_eur,
                "magic_number": self.settings.strategy_magic,
                "comment": self.order_comment(order.plan_id, test=False),
            }
        )

        client = self._get_client()
        self.validate_environment(client, require_demo=not self.settings.allow_live_trading)
        symbol_name = resolve_symbol_name(client, order.symbol)
        select_symbol(client, symbol_name)
        if existing is not None:
            reconciled_position = find_position_for_plan(
                client,
                symbol_name=symbol_name,
                magic=self.settings.strategy_magic,
                plan_id=order.plan_id,
                position_ticket=_ticket_text(existing.get("mt5_position_ticket")),
            )
            if reconciled_position is not None:
                actual_volume = _numeric(get_value(reconciled_position, "volume")) or 0.0
                self.ledger.mark_opened(
                    order.plan_id,
                    status="opened",
                    actual_volume=actual_volume,
                    opening_price=_numeric(existing.get("opening_price")) or 0.0,
                    position_ticket=_ticket_text(get_value(reconciled_position, "ticket")),
                    requested_volume=_numeric(existing.get("requested_volume")),
                    estimated_actual_exposure=_numeric(
                        existing.get("estimated_actual_exposure")
                    ),
                )
                LOGGER.info(
                    "POSITION_RECONCILED plan_id=%s symbol=%s position=%s",
                    order.plan_id,
                    symbol_name,
                    get_value(reconciled_position, "ticket"),
                )
                return ExecutionOutcome(
                    plan_id=order.plan_id,
                    symbol=symbol_name,
                    status="opened",
                    message="reconciled existing bot-owned position",
                    position_ticket=_ticket_text(get_value(reconciled_position, "ticket")),
                    actual_volume=actual_volume,
                )
            reason = (
                "Previous execution attempt is uncertain and no matching open "
                "position was found; skipped to prevent duplicate order."
            )
            self.ledger.mark_failed(order.plan_id, reason)
            LOGGER.info(
                "EXECUTION_SKIPPED plan_id=%s symbol=%s reason=uncertain_previous_attempt",
                order.plan_id,
                symbol_name,
            )
            return ExecutionOutcome(
                plan_id=order.plan_id,
                symbol=symbol_name,
                status="failed",
                message=reason,
            )
        symbol_info = require_symbol_info(client, symbol_name)
        self.validate_symbol_trade_mode(symbol_info)
        if has_unrelated_position_conflict(client, symbol_name, self.settings.strategy_magic):
            raise ExecutionError(
                f"Manual or unrelated position already exists for {symbol_name}; "
                "skipping to avoid ownership conflict."
            )

        tick = require_tick(client, symbol_name)
        price = select_tick_price(tick, order.direction)
        stop_loss, take_profit = normalize_and_validate_sl_tp(
            symbol_info=symbol_info,
            direction=order.direction,
            price=price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
        )
        conversion_rate = currency_to_eur_rate(
            client,
            quote_currency(symbol_info, symbol_name),
        )
        volume_calc = calculate_volume_for_eur_notional(
            requested_eur_notional=self.settings.target_notional_eur,
            price=price,
            contract_size=contract_size(symbol_info),
            quote_to_eur_rate=conversion_rate,
            volume_min=volume_min(symbol_info),
            volume_max=volume_max(symbol_info),
            volume_step=volume_step(symbol_info),
        )
        request = build_market_order_request(
            client=client,
            symbol=symbol_name,
            direction=order.direction,
            volume=volume_calc.volume,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            magic=self.settings.strategy_magic,
            comment=self.order_comment(order.plan_id, test=False),
            filling_mode=choose_filling_mode(client, symbol_info),
            deviation=self.settings.max_deviation_points,
        )
        self.ledger.update(
            order.plan_id,
            requested_volume=volume_calc.volume,
            estimated_actual_exposure=volume_calc.estimated_actual_exposure_eur,
        )
        LOGGER.info(
            "ORDER_SEND_STARTED plan_id=%s symbol=%s volume=%s price=%s sl=%s tp=%s",
            order.plan_id,
            symbol_name,
            volume_calc.volume,
            price,
            stop_loss,
            take_profit,
        )
        request, result = self.send_checked_order_with_filling_fallback(
            client=client,
            request=request,
            symbol_info=symbol_info,
            order=order,
            action="open",
        )

        actual_volume = _numeric(get_result_value(result, "volume")) or volume_calc.volume
        actual_price = _numeric(get_result_value(result, "price")) or price
        status = "opened_partial" if actual_volume + 1e-12 < volume_calc.volume else "opened"
        position_ticket = reconcile_position_ticket(
            client,
            symbol_name=symbol_name,
            magic=self.settings.strategy_magic,
            plan_id=order.plan_id,
            order_result=result,
        )
        order_ticket = _ticket_text(get_result_value(result, "order"))
        deal_ticket = _ticket_text(get_result_value(result, "deal"))
        self.ledger.mark_opened(
            order.plan_id,
            status=status,
            actual_volume=actual_volume,
            opening_price=actual_price,
            order_ticket=order_ticket,
            deal_ticket=deal_ticket,
            position_ticket=position_ticket,
            requested_volume=volume_calc.volume,
            estimated_actual_exposure=volume_calc.estimated_actual_exposure_eur,
        )
        LOGGER.info(
            "POSITION_OPENED plan_id=%s symbol=%s status=%s order=%s deal=%s "
            "position=%s requested_eur=%s estimated_eur=%s actual_volume=%s",
            order.plan_id,
            symbol_name,
            status,
            order_ticket,
            deal_ticket,
            position_ticket,
            self.settings.target_notional_eur,
            volume_calc.estimated_actual_exposure_eur,
            actual_volume,
        )
        return ExecutionOutcome(
            plan_id=order.plan_id,
            symbol=symbol_name,
            status=status,
            message=describe_mt5_result(client, result),
            order_ticket=order_ticket,
            deal_ticket=deal_ticket,
            position_ticket=position_ticket,
            actual_volume=actual_volume,
            price=actual_price,
        )

    def close_trade_plan_file(self, trade_plan_path: Path) -> list[ExecutionOutcome]:
        """Close any open bot-owned positions from a trade-plan CSV."""

        outcomes: list[ExecutionOutcome] = []
        for order in load_trade_plan_orders(trade_plan_path):
            try:
                outcomes.append(self.close_order(order))
            except Exception as exc:
                LOGGER.exception(
                    "EXECUTION_FAILED close plan_id=%s symbol=%s",
                    order.plan_id,
                    order.symbol,
                )
                outcomes.append(
                    ExecutionOutcome(
                        plan_id=order.plan_id,
                        symbol=order.symbol,
                        status="failed",
                        message=str(exc),
                    )
                )
        return outcomes

    def close_order(self, order: TradePlanOrder) -> ExecutionOutcome:
        """Close one still-open bot-owned MT5 position at market."""

        record = self.ledger.get(order.plan_id)
        if record is None or str(record.get("execution_status")) not in OPEN_STATUSES:
            return ExecutionOutcome(
                plan_id=order.plan_id,
                symbol=order.symbol,
                status="skipped",
                message="no open ledger position",
            )

        client = self._get_client()
        self.validate_environment(client, require_demo=not self.settings.allow_live_trading)
        symbol_name = resolve_symbol_name(client, order.symbol)
        position = find_position_for_plan(
            client,
            symbol_name=symbol_name,
            magic=self.settings.strategy_magic,
            plan_id=order.plan_id,
            position_ticket=_ticket_text(record.get("mt5_position_ticket")),
        )
        if position is None:
            self.ledger.mark_closed(
                order.plan_id,
                closing_price=None,
                closing_deal=None,
                closing_reason="position_already_closed_or_missing",
            )
            LOGGER.info(
                "POSITION_ALREADY_CLOSED plan_id=%s symbol=%s",
                order.plan_id,
                symbol_name,
            )
            return ExecutionOutcome(
                plan_id=order.plan_id,
                symbol=symbol_name,
                status="closed",
                message="position already closed or missing",
            )

        LOGGER.info("SESSION_CLOSE_STARTED plan_id=%s symbol=%s", order.plan_id, symbol_name)
        side = "sell" if order.direction == "buy" else "buy"
        tick = require_tick(client, symbol_name)
        price = select_tick_price(tick, side)
        symbol_info = require_symbol_info(client, symbol_name)
        close_request = build_close_request(
            client=client,
            symbol=symbol_name,
            side=side,
            volume=_numeric(get_value(position, "volume")) or _numeric(record.get("actual_volume")) or 0.0,
            price=price,
            position_ticket=_ticket_text(get_value(position, "ticket"))
            or _ticket_text(record.get("mt5_position_ticket")),
            magic=self.settings.strategy_magic,
            comment=self.order_comment(order.plan_id, test=False),
            filling_mode=choose_filling_mode(client, symbol_info),
            deviation=self.settings.max_deviation_points,
        )
        if close_request["volume"] <= 0:
            raise ExecutionError(f"Invalid close volume for {symbol_name}.")
        close_request, result = self.send_checked_order_with_filling_fallback(
            client=client,
            request=close_request,
            symbol_info=symbol_info,
            order=order,
            action="close",
        )

        closing_price = _numeric(get_result_value(result, "price")) or price
        closing_deal = _ticket_text(get_result_value(result, "deal"))
        self.ledger.mark_closed(
            order.plan_id,
            closing_price=closing_price,
            closing_deal=closing_deal,
            closing_reason="session_close",
            realised_profit_loss=_numeric(get_result_value(result, "profit")),
            commission=_numeric(get_result_value(result, "commission")),
            swap=_numeric(get_result_value(result, "swap")),
        )
        LOGGER.info(
            "POSITION_CLOSED plan_id=%s symbol=%s deal=%s price=%s",
            order.plan_id,
            symbol_name,
            closing_deal,
            closing_price,
        )
        return ExecutionOutcome(
            plan_id=order.plan_id,
            symbol=symbol_name,
            status="closed",
            message=describe_mt5_result(client, result),
            deal_ticket=closing_deal,
            price=closing_price,
        )

    def order_check(
        self,
        client: Any,
        request: dict[str, Any],
        order: TradePlanOrder,
    ) -> None:
        """Run MT5 order_check and raise on broker validation failure."""

        if not hasattr(client, "order_check"):
            LOGGER.info("ORDER_CHECK_SKIPPED plan_id=%s reason=not_supported", order.plan_id)
            return
        result = self.order_check_result(client, request, order)
        if result is None:
            return
        if not is_success_retcode(client, result, include_check_success=True):
            raise ExecutionError(f"order_check failed: {describe_mt5_result(client, result)}")
        LOGGER.info("ORDER_CHECK_PASSED plan_id=%s symbol=%s", order.plan_id, order.symbol)

    def order_check_result(
        self,
        client: Any,
        request: dict[str, Any],
        order: TradePlanOrder,
    ) -> Any | None:
        """Run MT5 order_check and return the raw result when supported."""

        if not hasattr(client, "order_check"):
            LOGGER.info("ORDER_CHECK_SKIPPED plan_id=%s reason=not_supported", order.plan_id)
            return None
        result = client.order_check(request)
        LOGGER.info(
            "ORDER_CHECK_RESULT plan_id=%s symbol=%s result=%s",
            order.plan_id,
            request.get("symbol"),
            describe_mt5_result(client, result),
        )
        return result

    def send_checked_order_with_filling_fallback(
        self,
        *,
        client: Any,
        request: dict[str, Any],
        symbol_info: Any,
        order: TradePlanOrder,
        action: str,
    ) -> tuple[dict[str, Any], Any]:
        """Send a checked order, retrying only unsupported MT5 filling modes."""

        last_result: Any | None = None
        last_check: Any | None = None
        for filling_mode in candidate_filling_modes(client, symbol_info, request.get("type_filling")):
            attempt = dict(request)
            attempt["type_filling"] = filling_mode
            LOGGER.info(
                "ORDER_REQUEST plan_id=%s action=%s request=%s",
                order.plan_id,
                action,
                safe_request_log(attempt),
            )
            check_result = self.order_check_result(client, attempt, order)
            last_check = check_result
            if check_result is not None and not is_success_retcode(
                client,
                check_result,
                include_check_success=True,
            ):
                if is_unsupported_filling_retcode(client, check_result):
                    LOGGER.info(
                        "ORDER_FILLING_RETRY plan_id=%s action=%s type_filling=%s "
                        "reason=unsupported_order_check",
                        order.plan_id,
                        action,
                        filling_mode,
                    )
                    continue
                raise ExecutionError(
                    f"{action} order_check failed: {describe_mt5_result(client, check_result)}"
                )

            result = send_order(client, attempt)
            last_result = result
            if is_success_retcode(client, result):
                return attempt, result
            if is_unsupported_filling_retcode(client, result):
                LOGGER.info(
                    "ORDER_FILLING_RETRY plan_id=%s action=%s type_filling=%s "
                    "reason=unsupported_order_send",
                    order.plan_id,
                    action,
                    filling_mode,
                )
                continue
            raise ExecutionError(
                f"{action} order_send failed: {describe_mt5_result(client, result)}"
            )
        raise ExecutionError(
            f"{action} order_send failed for all filling modes: "
            f"{describe_mt5_result(client, last_result or last_check)}"
        )

    def validate_environment(self, client: Any, require_demo: bool) -> None:
        """Validate connection, account, and live-account safety settings."""

        terminal = call_optional(client, "terminal_info")
        account = call_optional(client, "account_info")
        if account is None:
            raise ExecutionError("MT5 account_info() is not available.")
        if terminal is not None:
            connected = get_value(terminal, "connected")
            if connected is False:
                raise ExecutionError("MT5 terminal is not connected.")
            if get_value(terminal, "trade_allowed") is False:
                raise ExecutionError("MT5 terminal trading is disabled.")
        if get_value(account, "trade_allowed") is False:
            raise ExecutionError("MT5 account trading is disabled.")
        if get_value(account, "trade_expert") is False:
            raise ExecutionError("MT5 expert/API trading is disabled.")
        if require_demo and not is_demo_account(client, account):
            raise ExecutionError("MT5 demo account is required; live trading is disabled.")

    @staticmethod
    def validate_symbol_trade_mode(symbol_info: Any) -> None:
        """Reject symbols where MT5 says trading is disabled."""

        trade_mode = _numeric(get_value(symbol_info, "trade_mode"))
        if trade_mode == 0:
            raise ExecutionError("Symbol trading mode is disabled.")

    def order_comment(self, plan_id: str, test: bool) -> str:
        """Return a short MT5 order comment containing bot and plan identity."""

        kind = "T" if test else "S"
        return f"{self.settings.comment_prefix}:{kind}:{plan_id[:16]}"[:31]


class TestTradeManager:
    """Run one non-blocking BTCUSD demo test trade at a time."""

    def __init__(
        self,
        settings: ExecutionSettings | None = None,
        ledger: ExecutionLedger | None = None,
        client_factory: Any | None = None,
        status_store: TestTradeStatusStore | None = None,
    ) -> None:
        self.settings = settings or ExecutionSettings()
        self.ledger = ledger or ExecutionLedger(
            execution_ledger_path(OUTPUT_DIR, EXECUTION_LEDGER_FILE)
        )
        self.client_factory = client_factory
        self._lock = threading.Lock()
        self._active = False
        self._worker: threading.Thread | None = None
        self._started_at: datetime | None = None
        self._state = "idle"
        self._last_stage = ""
        self._failure_reason = ""
        self._position_ticket: str | None = None
        self._plan_id: str | None = None
        self.status_store = status_store or TestTradeStatusStore()
        self.status_store.write(state="idle", last_stage="", failure_reason="")

    def start(self) -> bool:
        """Start the test trade in a daemon thread if none is active."""

        with self._lock:
            self._repair_stale_state_locked()
            if self._is_active_locked():
                LOGGER.info(
                    "TEST_TRADE_FAILED reason=already_running state=%s stage=%s",
                    self._state,
                    self._last_stage,
                )
                return False
            self._state = "starting"
            self._last_stage = "TEST_TRADE_WORKER_STARTING"
            self._failure_reason = ""
            self._started_at = datetime.now(timezone.utc)
            self._write_status_locked()
        LOGGER.info("TEST_TRADE_WORKER_STARTING")
        thread = threading.Thread(
            target=self._run_and_release,
            name="mad-test-trade",
            daemon=True,
        )
        try:
            thread.start()
        except Exception as exc:
            LOGGER.exception("TEST_TRADE_FAILED stage=TEST_TRADE_WORKER_STARTING")
            with self._lock:
                self._active = False
                self._worker = None
                self._state = "failed"
                self._failure_reason = str(exc)
                self._write_status_locked()
            return False
        with self._lock:
            self._worker = thread
            self._active = True
            self._last_stage = "TEST_TRADE_WORKER_STARTED"
            self._write_status_locked()
        LOGGER.info("TEST_TRADE_WORKER_STARTED thread=%s", thread.name)
        return True

    def _run_and_release(self) -> None:
        try:
            self.run_blocking()
        except Exception as exc:
            LOGGER.exception(
                "TEST_TRADE_FAILED stage=%s reason=%s",
                self._last_stage or "unknown",
                exc,
            )
            with self._lock:
                self._state = "failed"
                self._failure_reason = str(exc)
                self._write_status_locked()
        finally:
            with self._lock:
                self._active = False
                self._worker = None
                if self._state not in {"completed", "failed"}:
                    self._state = "failed"
                    self._failure_reason = self._failure_reason or "worker exited unexpectedly"
                self._write_status_locked()
            LOGGER.info("TEST_TRADE_STATE_CLEARED")

    def status(self) -> dict[str, Any]:
        """Return current test-trade status for console output."""

        with self._lock:
            self._repair_stale_state_locked()
            status = self.status_store.read()
            status.update(
                {
                    "state": self._state,
                    "last_stage": self._last_stage,
                    "failure_reason": self._failure_reason,
                    "active": self._is_active_locked(),
                    "position_ticket": self._position_ticket,
                    "plan_id": self._plan_id,
                }
            )
            return status

    def run_blocking(self) -> ExecutionOutcome:
        """Open a small BTCUSD demo position and close it after the hold time."""

        LOGGER.info("TEST_TRADE_REQUESTED")
        self._set_stage("TEST_TRADE_WORKER_ENTERED", "starting")
        executor = MT5TradeExecutor(
            settings=self.settings,
            ledger=self.ledger,
            client_factory=self.client_factory,
        )
        client: Any | None = None
        symbol_name = ""
        position_ticket: str | None = None
        plan_id = ""
        comment = ""
        opened = False
        try:
            self._set_stage("TEST_TRADE_MT5_CONNECTION_CHECK_STARTED", "starting")
            client = self._mt5_call(
                "TEST_TRADE_MT5_CONNECTION_CHECK_STARTED",
                executor._get_client,
            )
            self._set_stage("TEST_TRADE_MT5_CONNECTION_CONFIRMED", "starting")
            self._set_stage("TEST_TRADE_ACCOUNT_CHECK_STARTED", "validating")
            terminal = self._mt5_call(
                "TEST_TRADE_TERMINAL_INFO",
                lambda: call_optional(client, "terminal_info"),
            )
            account = self._mt5_call(
                "TEST_TRADE_ACCOUNT_INFO",
                lambda: call_optional(client, "account_info"),
            )
            self._validate_test_environment(client, terminal, account)
        except Exception as exc:
            self._record_failure(self._last_stage or "startup", str(exc))
            raise
        if account is None or not is_demo_account(client, account):
            LOGGER.error("TEST_TRADE_FAILED reason=test_trade is allowed only on an MT5 demo account")
            print("test_trade is allowed only on an MT5 demo account", flush=True)
            raise ExecutionError("test_trade is allowed only on an MT5 demo account")
        LOGGER.info(
            "DEMO_ACCOUNT_CONFIRMED trade_mode=%s login=%s server=%s",
            get_value(account, "trade_mode"),
            get_value(account, "login"),
            get_value(account, "server"),
        )
        self._set_stage("DEMO_ACCOUNT_CONFIRMED", "validating")

        self._set_stage("TEST_TRADE_SYMBOL_RESOLUTION_STARTED", "resolving_symbol")
        symbol_name, symbol_info = self._resolve_test_symbol(client)
        self._set_stage("TEST_TRADE_SYMBOL_RESOLVED", "resolving_symbol", symbol=symbol_name)
        self._set_stage("TEST_TRADE_SYMBOL_SELECT_STARTED", "validating", symbol=symbol_name)
        self._mt5_call(
            "TEST_TRADE_SYMBOL_SELECT_STARTED",
            lambda: select_symbol(client, symbol_name),
        )
        self._set_stage("TEST_TRADE_SYMBOL_SELECTED", "validating", symbol=symbol_name)
        self._set_stage("TEST_TRADE_TICK_REQUEST_STARTED", "validating", symbol=symbol_name)
        tick = self._mt5_call(
            "TEST_TRADE_TICK_REQUEST_STARTED",
            lambda: require_tick(client, symbol_name),
        )
        price = select_tick_price(tick, "buy")
        LOGGER.info(
            "TEST_TRADE_TICK_RECEIVED symbol=%s bid=%s ask=%s last=%s",
            symbol_name,
            get_value(tick, "bid"),
            get_value(tick, "ask"),
            get_value(tick, "last"),
        )
        self._set_stage("TEST_TRADE_TICK_RECEIVED", "validating", symbol=symbol_name)
        self._set_stage("TEST_TRADE_VOLUME_CALCULATION_STARTED", "validating", symbol=symbol_name)
        conversion_rate = self._mt5_call(
            "TEST_TRADE_CURRENCY_CONVERSION",
            lambda: currency_to_eur_rate(client, quote_currency(symbol_info, symbol_name)),
        )
        volume_calc = calculate_volume_for_eur_notional(
            requested_eur_notional=self.settings.test_notional_eur,
            price=price,
            contract_size=contract_size(symbol_info),
            quote_to_eur_rate=conversion_rate,
            volume_min=volume_min(symbol_info),
            volume_max=volume_max(symbol_info),
            volume_step=volume_step(symbol_info),
        )
        LOGGER.info(
            "TEST_TRADE_VOLUME_CALCULATED symbol=%s requested_eur=%s volume=%s "
            "estimated_eur=%s per_lot_eur=%s",
            symbol_name,
            volume_calc.requested_eur_notional,
            volume_calc.volume,
            volume_calc.estimated_actual_exposure_eur,
            volume_calc.per_lot_exposure_eur,
        )
        self._set_stage("TEST_TRADE_VOLUME_CALCULATED", "validating", symbol=symbol_name)
        plan_id = deterministic_plan_id(
            Path("test_trade"),
            0,
            {
                "ticker": symbol_name,
                "direction": "buy",
                "entry_time_local": datetime.now(timezone.utc).isoformat(),
            },
        )
        self._plan_id = plan_id
        comment = executor.order_comment(plan_id, test=True)
        self.ledger.upsert_pending(
            {
                "plan_id": plan_id,
                "source_trade_plan": "test_trade",
                "symbol": symbol_name,
                "direction": "buy",
                "planned_sl": None,
                "planned_tp": None,
                "requested_eur_notional": self.settings.test_notional_eur,
                "requested_volume": volume_calc.volume,
                "estimated_actual_exposure": volume_calc.estimated_actual_exposure_eur,
                "magic_number": self.settings.test_magic,
                "comment": comment,
            }
        )
        request = build_market_order_request(
            client=client,
            symbol=symbol_name,
            direction="buy",
            volume=volume_calc.volume,
            price=price,
            stop_loss=0.0,
            take_profit=0.0,
            magic=self.settings.test_magic,
            comment=comment,
            filling_mode=choose_filling_mode(client, symbol_info),
            deviation=executor.settings.max_deviation_points,
        )
        self._set_stage("TEST_TRADE_ORDER_CHECK_STARTED", "opening", symbol=symbol_name)
        self._set_stage("TEST_TRADE_ORDER_SEND_STARTED", "opening", symbol=symbol_name)
        request, check_result, open_result = self._send_request_with_filling_fallback(
            client=client,
            request=request,
            symbol_info=symbol_info,
            request_log_stage="TEST_TRADE_ORDER_REQUEST",
            check_log_stage="TEST_TRADE_ORDER_CHECK_RESULT",
            send_log_stage="TEST_TRADE_ORDER_SEND_RESULT",
        )
        self._set_stage("TEST_POSITION_OPEN_VERIFICATION_STARTED", "opening", symbol=symbol_name)
        position = self._verify_position_open(
            client,
            symbol_name=symbol_name,
            plan_id=plan_id,
            order_result=open_result,
        )
        position_ticket = _ticket_text(get_value(position, "ticket"))
        self._position_ticket = position_ticket
        actual_volume = _numeric(get_value(position, "volume")) or volume_calc.volume
        opening_price = (
            _numeric(get_value(position, "price_open"))
            or _numeric(get_result_value(open_result, "price"))
            or price
        )
        self.ledger.mark_opened(
            plan_id,
            status="opened",
            actual_volume=actual_volume,
            opening_price=opening_price,
            order_ticket=_ticket_text(get_result_value(open_result, "order")),
            deal_ticket=_ticket_text(get_result_value(open_result, "deal")),
            position_ticket=position_ticket,
            requested_volume=volume_calc.volume,
            estimated_actual_exposure=volume_calc.estimated_actual_exposure_eur,
        )
        opened = True
        LOGGER.info(
            "TEST_POSITION_OPENED symbol=%s order=%s deal=%s position=%s volume=%s price=%s",
            symbol_name,
            get_result_value(open_result, "order"),
            get_result_value(open_result, "deal"),
            position_ticket,
            actual_volume,
            opening_price,
        )
        print("test trade opened successfully", flush=True)
        try:
            self._set_stage("TEST_TRADE_HOLD_STARTED", "open_and_waiting", symbol=symbol_name)
            _sleep_without_busy_wait(self.settings.test_hold_seconds)
            self._set_stage("TEST_TRADE_HOLD_COMPLETED", "open_and_waiting", symbol=symbol_name)
        finally:
            close_outcome = self._close_verified_test_position(
                client=client,
                symbol_name=symbol_name,
                position_ticket=position_ticket,
                magic=self.settings.test_magic,
                comment=comment,
            )
            opened = False
            self.ledger.mark_closed(
                plan_id,
                closing_price=close_outcome.price,
                closing_deal=close_outcome.deal_ticket,
                closing_reason="test_trade_complete",
            )
        self._set_stage("TEST_TRADE_HISTORY_RECONCILED", "closing", symbol=symbol_name)
        self._reconcile_history(client, position_ticket=position_ticket, plan_id=plan_id)
        self._set_stage("TEST_TRADE_COMPLETED", "completed", symbol=symbol_name)
        print("test trade closed successfully", flush=True)
        return ExecutionOutcome(
            plan_id=plan_id,
            symbol=symbol_name,
            status="closed",
            message="test trade opened and closed successfully",
            order_ticket=_ticket_text(get_result_value(open_result, "order")),
            deal_ticket=_ticket_text(get_result_value(open_result, "deal")),
            position_ticket=position_ticket,
            actual_volume=actual_volume,
            price=opening_price,
        )

    def _validate_test_environment(self, client: Any, terminal: Any, account: Any) -> None:
        if account is None or not is_demo_account(client, account):
            LOGGER.error(
                "TEST_TRADE_FAILED stage=TEST_TRADE_ACCOUNT_CHECK_STARTED "
                "reason=test_trade is allowed only on an MT5 demo account"
            )
            print("test_trade is allowed only on an MT5 demo account", flush=True)
            raise ExecutionError("test_trade is allowed only on an MT5 demo account")
        if terminal is not None:
            if get_value(terminal, "connected") is False:
                raise ExecutionError("MT5 terminal is not connected.")
            if get_value(terminal, "trade_allowed") is False:
                raise ExecutionError("MT5 terminal trading is disabled.")
        if get_value(account, "trade_allowed") is False:
            raise ExecutionError("MT5 account trading is disabled.")
        if get_value(account, "trade_expert") is False:
            raise ExecutionError("MT5 expert/API trading is disabled.")

    def _send_request_with_filling_fallback(
        self,
        *,
        client: Any,
        request: dict[str, Any],
        symbol_info: Any,
        request_log_stage: str,
        check_log_stage: str,
        send_log_stage: str,
    ) -> tuple[dict[str, Any], Any | None, Any]:
        """Try supported filling modes until MT5 accepts one or fails definitively."""

        last_result: Any | None = None
        last_check: Any | None = None
        for filling_mode in candidate_filling_modes(client, symbol_info, request.get("type_filling")):
            attempt = dict(request)
            attempt["type_filling"] = filling_mode
            LOGGER.info("%s %s", request_log_stage, safe_request_log(attempt))
            check_result = None
            if hasattr(client, "order_check"):
                check_result = self._mt5_call(
                    check_log_stage,
                    lambda attempt=attempt: client.order_check(attempt),
                )
            last_check = check_result
            LOGGER.info(
                "%s %s",
                check_log_stage,
                describe_mt5_result(client, check_result)
                if check_result is not None
                else "not_supported",
            )
            if check_result is not None and not is_success_retcode(
                client,
                check_result,
                include_check_success=True,
            ):
                if is_unsupported_filling_retcode(client, check_result):
                    continue
                raise ExecutionError(
                    f"test_trade order_check failed: {describe_mt5_result(client, check_result)}"
                )
            result = self._mt5_call(
                send_log_stage,
                lambda attempt=attempt: send_order(client, attempt),
            )
            last_result = result
            LOGGER.info("%s %s", send_log_stage, describe_mt5_result(client, result))
            if is_success_retcode(client, result):
                return attempt, check_result, result
            if is_unsupported_filling_retcode(client, result):
                continue
            raise ExecutionError(
                f"test_trade order_send failed: {describe_mt5_result(client, result)}"
            )
        raise ExecutionError(
            "test_trade order_send failed for all filling modes: "
            f"{describe_mt5_result(client, last_result or last_check)}"
        )

    def _resolve_test_symbol(self, client: Any) -> tuple[str, Any]:
        exact_candidates = []
        for candidate in [self.settings.test_symbol, "BTCUSD", "BTCEUR", "GBTC"]:
            if candidate not in exact_candidates:
                exact_candidates.append(candidate)
        oversized: list[str] = []
        for candidate in exact_candidates:
            try:
                info = self._mt5_call(
                    f"TEST_TRADE_SYMBOL_INFO_{candidate}",
                    lambda candidate=candidate: require_symbol_info(client, candidate),
                    timeout_seconds=10,
                )
                min_exposure = self._candidate_min_exposure_eur(client, candidate, info)
            except Exception as exc:
                LOGGER.info(
                    "TEST_TRADE_SYMBOL_CANDIDATE_SKIPPED name=%s reason=%s",
                    candidate,
                    exc,
                )
                continue
            LOGGER.info(
                "TEST_TRADE_SYMBOL_CANDIDATE name=%s match=exact description=%s "
                "trade_mode=%s contract_size=%s volume_min=%s volume_max=%s "
                "volume_step=%s currency_base=%s currency_profit=%s "
                "minimum_exposure_eur=%.2f",
                candidate,
                get_value(info, "description"),
                get_value(info, "trade_mode"),
                get_value(info, "trade_contract_size"),
                get_value(info, "volume_min"),
                get_value(info, "volume_max"),
                get_value(info, "volume_step"),
                get_value(info, "currency_base"),
                get_value(info, "currency_profit"),
                min_exposure,
            )
            if min_exposure <= self.settings.test_notional_eur:
                LOGGER.info(
                    "TEST_TRADE_SYMBOL_RESOLVED symbol=%s reason=min_exposure_within_cap",
                    candidate,
                )
                return candidate, info
            oversized.append(f"{candidate}=EUR {min_exposure:.2f}")

        symbols = self._mt5_call(
            "TEST_TRADE_SYMBOL_RESOLUTION_STARTED",
            lambda: symbols_get(client),
        )
        requested_key = normalize_symbol_name(self.settings.test_symbol)
        selected: tuple[int, str] | None = None
        for symbol in symbols:
            name = str(get_value(symbol, "name") or "").strip()
            if not name:
                continue
            name_key = normalize_symbol_name(name)
            score = 0
            if name == self.settings.test_symbol:
                score = 100
            elif name_key == requested_key:
                score = 95
            elif name_key.startswith(requested_key):
                score = 90
            elif "BTCUSD" in name_key:
                score = 85
            if score <= 0:
                continue
            LOGGER.info(
                "TEST_TRADE_SYMBOL_CANDIDATE name=%s score=%s match=name",
                name,
                score,
            )
            if selected is None or score > selected[0]:
                selected = (score, name)
            if score >= 95:
                break
        if selected is None:
            for symbol in symbols:
                name = str(get_value(symbol, "name") or "").strip()
                if not name:
                    continue
                description = str(get_value(symbol, "description") or get_value(symbol, "path") or "")
                combined_key = normalize_symbol_name(f"{name} {description}")
                if not (
                    ("BTC" in combined_key or "BITCOIN" in combined_key)
                    and ("USD" in combined_key or "DOLLAR" in combined_key)
                ):
                    continue
                LOGGER.info(
                    "TEST_TRADE_SYMBOL_CANDIDATE name=%s score=70 match=description description=%s",
                    name,
                    description,
                )
                selected = (70, name)
                break
        if selected is None:
            raise ExecutionError(
                "No suitable BTCUSD/Bitcoin USD symbol found in MT5. "
                f"Oversized exact candidates: {', '.join(oversized)}"
            )
        symbol_name = selected[1]
        info = self._mt5_call(
            "TEST_TRADE_SYMBOL_INFO",
            lambda: require_symbol_info(client, symbol_name),
        )
        min_exposure = self._candidate_min_exposure_eur(client, symbol_name, info)
        if min_exposure > self.settings.test_notional_eur:
            raise ExecutionError(
                f"Selected Bitcoin symbol {symbol_name} minimum exposure "
                f"EUR {min_exposure:.2f} exceeds requested EUR "
                f"{self.settings.test_notional_eur:.2f}."
            )
        LOGGER.info(
            "TEST_TRADE_SYMBOL_RESOLVED symbol=%s description=%s trade_mode=%s "
            "contract_size=%s min=%s max=%s step=%s digits=%s point=%s "
            "currency_base=%s currency_profit=%s",
            symbol_name,
            get_value(info, "description"),
            get_value(info, "trade_mode"),
            get_value(info, "trade_contract_size"),
            get_value(info, "volume_min"),
            get_value(info, "volume_max"),
            get_value(info, "volume_step"),
            get_value(info, "digits"),
            get_value(info, "point"),
            get_value(info, "currency_base"),
            get_value(info, "currency_profit"),
        )
        return symbol_name, info

    def _candidate_min_exposure_eur(self, client: Any, symbol_name: str, info: Any) -> float:
        select_symbol(client, symbol_name)
        tick = require_tick(client, symbol_name)
        price = select_tick_price(tick, "buy")
        conversion_rate = currency_to_eur_rate(client, quote_currency(info, symbol_name))
        return price * contract_size(info) * volume_min(info) * conversion_rate

    def _verify_position_open(
        self,
        client: Any,
        *,
        symbol_name: str,
        plan_id: str,
        order_result: Any,
    ) -> Any:
        deadline = monotonic() + TEST_TRADE_OPEN_VERIFY_SECONDS
        candidate_tickets = {
            _ticket_text(get_result_value(order_result, key))
            for key in ("position", "order", "deal")
        }
        candidate_tickets.discard(None)
        while monotonic() < deadline:
            position = self._find_test_position(
                client,
                symbol_name=symbol_name,
                plan_id=plan_id,
                candidate_tickets=candidate_tickets,
            )
            if position is not None:
                return position
            threading.Event().wait(0.5)
        raise ExecutionError("Opened order was not visible in MT5 positions_get().")

    def _find_test_position(
        self,
        client: Any,
        *,
        symbol_name: str,
        plan_id: str,
        candidate_tickets: set[str | None] | None = None,
    ) -> Any | None:
        positions = self._mt5_call(
            "TEST_POSITION_QUERY",
            lambda: positions_get(client, symbol=symbol_name),
            timeout_seconds=10,
        )
        for position in positions:
            ticket = _ticket_text(get_value(position, "ticket"))
            magic = int(_numeric(get_value(position, "magic")) or -1)
            comment = str(get_value(position, "comment") or "")
            if candidate_tickets and ticket in candidate_tickets:
                return position
            if magic == self.settings.test_magic and plan_id[:16] in comment:
                return position
            if magic == self.settings.test_magic and not plan_id:
                return position
        return None

    def _close_verified_test_position(
        self,
        *,
        client: Any,
        symbol_name: str,
        position_ticket: str | None,
        magic: int,
        comment: str,
    ) -> ExecutionOutcome:
        self._set_stage("TEST_TRADE_CLOSE_STARTED", "closing", symbol=symbol_name)
        position = find_position_for_plan(
            client,
            symbol_name=symbol_name,
            magic=magic,
            plan_id="",
            position_ticket=position_ticket,
        )
        if position is None:
            raise ExecutionError(f"Test position {position_ticket} is not open.")
        side = "sell" if position_direction(client, position) == "buy" else "buy"
        tick = self._mt5_call(
            "TEST_TRADE_CLOSE_TICK_REQUEST",
            lambda: require_tick(client, symbol_name),
        )
        price = select_tick_price(tick, side)
        symbol_info = self._mt5_call(
            "TEST_TRADE_CLOSE_SYMBOL_INFO",
            lambda: require_symbol_info(client, symbol_name),
        )
        request = build_close_request(
            client=client,
            symbol=symbol_name,
            side=side,
            volume=_numeric(get_value(position, "volume")) or 0.0,
            price=price,
            position_ticket=position_ticket or _ticket_text(get_value(position, "ticket")),
            magic=magic,
            comment=comment,
            filling_mode=choose_filling_mode(client, symbol_info),
            deviation=self.settings.max_deviation_points,
        )
        request, close_check, close_result = self._send_request_with_filling_fallback(
            client=client,
            request=request,
            symbol_info=symbol_info,
            request_log_stage="TEST_TRADE_CLOSE_REQUEST",
            check_log_stage="TEST_TRADE_CLOSE_ORDER_CHECK_RESULT",
            send_log_stage="TEST_TRADE_CLOSE_ORDER_SEND_RESULT",
        )
        self._set_stage("TEST_POSITION_CLOSE_VERIFICATION_STARTED", "closing", symbol=symbol_name)
        self._verify_position_closed(
            client,
            symbol_name=symbol_name,
            position_ticket=position_ticket,
        )
        LOGGER.info(
            "TEST_POSITION_CLOSED symbol=%s order=%s deal=%s position=%s price=%s",
            symbol_name,
            get_result_value(close_result, "order"),
            get_result_value(close_result, "deal"),
            position_ticket,
            get_result_value(close_result, "price"),
        )
        return ExecutionOutcome(
            plan_id=self._plan_id or "",
            symbol=symbol_name,
            status="closed",
            message=describe_mt5_result(client, close_result),
            order_ticket=_ticket_text(get_result_value(close_result, "order")),
            deal_ticket=_ticket_text(get_result_value(close_result, "deal")),
            position_ticket=position_ticket,
            price=_numeric(get_result_value(close_result, "price")) or price,
        )

    def _verify_position_closed(
        self,
        client: Any,
        *,
        symbol_name: str,
        position_ticket: str | None,
    ) -> None:
        deadline = monotonic() + TEST_TRADE_CLOSE_VERIFY_SECONDS
        while monotonic() < deadline:
            positions = self._mt5_call(
                "TEST_POSITION_CLOSE_QUERY",
                lambda: positions_get(client, symbol=symbol_name),
                timeout_seconds=10,
            )
            still_open = [
                position
                for position in positions
                if int(_numeric(get_value(position, "magic")) or -1) == self.settings.test_magic
                and (
                    not position_ticket
                    or _ticket_text(get_value(position, "ticket")) == position_ticket
                )
            ]
            if not still_open:
                return
            threading.Event().wait(0.5)
        raise ExecutionError(f"Test position {position_ticket} remained open after close.")

    def _reconcile_history(self, client: Any, *, position_ticket: str | None, plan_id: str) -> None:
        if not hasattr(client, "history_deals_get"):
            LOGGER.info("TEST_TRADE_HISTORY_RECONCILED history_deals_get=not_supported")
            return

        matching: list[Any] = []
        if position_ticket:
            try:
                deals = self._mt5_call(
                    "TEST_TRADE_HISTORY_RECONCILE_POSITION",
                    lambda: client.history_deals_get(position=int(position_ticket)),
                    timeout_seconds=20,
                )
                matching.extend(list(deals or []))
            except Exception as exc:
                LOGGER.warning(
                    "TEST_TRADE_HISTORY_POSITION_QUERY_FAILED position=%s reason=%s",
                    position_ticket,
                    exc,
                )

        if not matching:
            start = datetime.now(timezone.utc) - timedelta(hours=2)
            end = datetime.now(timezone.utc) + timedelta(minutes=1)
            try:
                deals = self._mt5_call(
                    "TEST_TRADE_HISTORY_RECONCILE_RANGE",
                    lambda: client.history_deals_get(start, end),
                    timeout_seconds=20,
                )
            except Exception as exc:
                LOGGER.warning("TEST_TRADE_HISTORY_RANGE_QUERY_FAILED reason=%s", exc)
                deals = []
            for deal in list(deals or []):
                comment = str(get_value(deal, "comment") or "")
                magic = int(_numeric(get_value(deal, "magic")) or -1)
                position_id = _ticket_text(get_value(deal, "position_id"))
                if (
                    magic == self.settings.test_magic
                    or plan_id[:16] in comment
                    or position_id == position_ticket
                ):
                    matching.append(deal)

        unique_matching = []
        seen_tickets: set[str] = set()
        for deal in matching:
            ticket = _ticket_text(get_value(deal, "ticket")) or repr(deal)
            if ticket in seen_tickets:
                continue
            seen_tickets.add(ticket)
            unique_matching.append(deal)

        profit = sum(_numeric(get_value(deal, "profit")) or 0.0 for deal in unique_matching)
        commission = sum(_numeric(get_value(deal, "commission")) or 0.0 for deal in unique_matching)
        swap = sum(_numeric(get_value(deal, "swap")) or 0.0 for deal in unique_matching)
        if unique_matching:
            self.ledger.update(
                plan_id,
                realised_profit_loss=profit,
                commission=commission,
                swap=swap,
            )
        for deal in unique_matching:
            comment = str(get_value(deal, "comment") or "")
            LOGGER.info(
                "TEST_TRADE_HISTORY_DEAL ticket=%s order=%s position=%s entry=%s "
                "type=%s volume=%s price=%s profit=%s comment=%s",
                get_value(deal, "ticket"),
                get_value(deal, "order"),
                get_value(deal, "position_id"),
                get_value(deal, "entry"),
                get_value(deal, "type"),
                get_value(deal, "volume"),
                get_value(deal, "price"),
                get_value(deal, "profit"),
                comment,
            )
        LOGGER.info(
            "TEST_TRADE_HISTORY_RECONCILED matching_deals=%s position=%s profit=%s",
            len(unique_matching),
            position_ticket,
            profit,
        )

    def _mt5_call(
        self,
        stage: str,
        func: Any,
        timeout_seconds: int | None = None,
    ) -> Any:
        return call_with_timeout(
            stage,
            func,
            timeout_seconds=timeout_seconds or TEST_TRADE_OPERATION_TIMEOUT_SECONDS,
        )

    def _set_stage(self, stage: str, state: str, **extra: Any) -> None:
        with self._lock:
            self._state = state
            self._last_stage = stage
            if state != "failed":
                self._failure_reason = ""
            self._write_status_locked(extra)
        LOGGER.info("%s%s", stage, _format_stage_extra(extra))

    def _record_failure(self, stage: str, reason: str) -> None:
        if self._plan_id:
            try:
                self.ledger.mark_failed(self._plan_id, reason)
            except Exception:
                LOGGER.exception("TEST_TRADE_LEDGER_FAILURE_MARK_FAILED plan_id=%s", self._plan_id)
        with self._lock:
            self._state = "failed"
            self._last_stage = stage
            self._failure_reason = reason
            self._write_status_locked()
        LOGGER.error("TEST_TRADE_FAILED stage=%s reason=%s", stage, reason)

    def _write_status_locked(self, extra: dict[str, Any] | None = None) -> None:
        self.status_store.write(
            state=self._state,
            last_stage=self._last_stage,
            failure_reason=self._failure_reason,
            worker_alive=bool(self._worker and self._worker.is_alive()),
            started_at=self._started_at.isoformat() if self._started_at else None,
            plan_id=self._plan_id,
            position_ticket=self._position_ticket,
            **(extra or {}),
        )

    def _is_active_locked(self) -> bool:
        return bool(self._active and self._worker is not None and self._worker.is_alive())

    def _repair_stale_state_locked(self) -> None:
        if self._active and (self._worker is None or not self._worker.is_alive()):
            self._active = False
            if self._state not in {"completed", "failed"}:
                self._state = "failed"
                self._failure_reason = "stale active state without live worker"
            self._write_status_locked()
            LOGGER.info("TEST_TRADE_STATE_CLEARED reason=stale_worker")
            return
        if not self._active or self._started_at is None:
            return
        max_seconds = self.settings.test_hold_seconds + TEST_TRADE_WATCHDOG_EXTRA_SECONDS
        age = (datetime.now(timezone.utc) - self._started_at).total_seconds()
        if age > max_seconds:
            self._active = False
            self._state = "failed"
            self._failure_reason = f"watchdog timeout after {age:.0f}s"
            self._write_status_locked()
            LOGGER.error(
                "TEST_TRADE_FAILED stage=%s reason=watchdog_timeout age=%.0fs",
                self._last_stage,
                age,
            )


def execute_trade_plan_file_safely(trade_plan_path: Path) -> list[ExecutionOutcome]:
    """Execute a trade-plan file and convert all failures to logged outcomes."""

    with timed_task("execute_trade_plan_file", path=trade_plan_path):
        try:
            return MT5TradeExecutor(
                ledger_path=ledger_path_for_trade_plan(trade_plan_path),
            ).execute_trade_plan_file(trade_plan_path)
        except Exception as exc:
            LOGGER.exception("EXECUTION_FAILED path=%s", trade_plan_path)
            print(f"WARNING: trade execution failed for {trade_plan_path.name}: {exc}", flush=True)
            return []


def close_trade_plan_file_safely(trade_plan_path: Path) -> list[ExecutionOutcome]:
    """Close positions for a trade-plan file and log failures without raising."""

    with timed_task("close_trade_plan_file", path=trade_plan_path):
        try:
            return MT5TradeExecutor(
                ledger_path=ledger_path_for_trade_plan(trade_plan_path),
            ).close_trade_plan_file(trade_plan_path)
        except Exception as exc:
            LOGGER.exception("SESSION_CLOSE_FAILED path=%s", trade_plan_path)
            print(f"WARNING: MT5 close failed for {trade_plan_path.name}: {exc}", flush=True)
            return []


def ledger_path_for_trade_plan(trade_plan_path: Path) -> Path:
    """Return the runtime-root ledger path associated with a Trade Plan file."""

    plan_path = Path(trade_plan_path)
    root = plan_path.parent.parent if plan_path.parent.name else OUTPUT_DIR
    return execution_ledger_path(root, EXECUTION_LEDGER_FILE)


def start_test_trade_safely(manager: TestTradeManager | None = None) -> bool:
    """Start the non-blocking demo test trade command."""

    active_manager = manager or default_test_trade_manager()
    started = active_manager.start()
    if not started:
        print("test_trade is already running", flush=True)
        LOGGER.info("TEST_TRADE_FAILED reason=already_running")
    else:
        print("test_trade started; follow the log for execution details", flush=True)
    return started


def default_test_trade_manager() -> TestTradeManager:
    """Return the lazily-created process-wide test-trade manager."""

    global _DEFAULT_TEST_TRADE_MANAGER
    if _DEFAULT_TEST_TRADE_MANAGER is None:
        _DEFAULT_TEST_TRADE_MANAGER = TestTradeManager()
    return _DEFAULT_TEST_TRADE_MANAGER


def test_trade_status() -> dict[str, Any]:
    """Return the current process-wide test-trade status."""

    return default_test_trade_manager().status()


def recover_stale_test_trades_safely() -> None:
    """Best-effort startup recovery for bot-owned test positions."""

    try:
        executor = MT5TradeExecutor()
        client = executor._get_client()
        account = call_optional(client, "account_info")
        if account is None or not is_demo_account(client, account):
            LOGGER.info("TEST_TRADE_RECOVERY skipped: account is not confirmed demo")
            return
        for position in positions_get(client) or []:
            if int(_numeric(get_value(position, "magic")) or -1) != MT5_TEST_MAGIC:
                continue
            LOGGER.info(
                "TEST_TRADE_RECOVERY symbol=%s position=%s",
                get_value(position, "symbol"),
                get_value(position, "ticket"),
            )
            close_position_by_ticket(
                client=client,
                symbol_name=str(get_value(position, "symbol")),
                position_ticket=_ticket_text(get_value(position, "ticket")),
                magic=MT5_TEST_MAGIC,
                comment=f"{APP_ORDER_COMMENT_PREFIX}:T:recovery",
                deviation=20,
            )
    except Exception as exc:
        LOGGER.info("TEST_TRADE_RECOVERY skipped: %s", exc)


def call_with_timeout(
    stage: str,
    func: Any,
    timeout_seconds: int = TEST_TRADE_OPERATION_TIMEOUT_SECONDS,
) -> Any:
    """Run one operation with a bounded wait and propagate/log failures."""

    result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def runner() -> None:
        try:
            result_queue.put((True, func()))
        except BaseException as exc:  # noqa: BLE001 - preserve worker exception.
            result_queue.put((False, exc))

    thread = threading.Thread(target=runner, name=f"{stage}-timeout", daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        raise OperationTimeoutError(
            f"{stage} timed out after {timeout_seconds} seconds."
        )
    ok, value = result_queue.get()
    if ok:
        return value
    raise value


def safe_request_log(request: dict[str, Any]) -> dict[str, Any]:
    """Return an order request copy safe for logs."""

    return {
        key: value
        for key, value in request.items()
        if key.lower() not in {"password", "login", "server"}
    }


def _format_stage_extra(extra: dict[str, Any]) -> str:
    if not extra:
        return ""
    return " " + " ".join(f"{key}={value}" for key, value in extra.items())


def load_trade_plan_orders(trade_plan_path: Path) -> list[TradePlanOrder]:
    """Read executable orders from a trade-plan CSV file."""

    if not trade_plan_path.exists():
        raise FileNotFoundError(trade_plan_path)
    data = pd.read_csv(trade_plan_path)
    orders: list[TradePlanOrder] = []
    for index, row in data.iterrows():
        direction = str(row.get("direction") or row.get("direction of trading") or "").lower().strip()
        if direction not in {"buy", "sell", "long", "short"}:
            continue
        direction = "buy" if direction in {"buy", "long"} else "sell"
        symbol = str(row.get("ticker") or row.get("Ticker name") or "").strip()
        stop_loss = _numeric(row.get("stop_loss") or row.get("Stop Loss level"))
        take_profit = _numeric(row.get("take_profit") or row.get("Take Profit level"))
        if not symbol or stop_loss is None or take_profit is None:
            continue
        plan_id = deterministic_plan_id(trade_plan_path, int(index), row)
        orders.append(
            TradePlanOrder(
                plan_id=plan_id,
                source_trade_plan=str(trade_plan_path),
                row_index=int(index),
                symbol=symbol,
                direction=direction,
                stop_loss=stop_loss,
                take_profit=take_profit,
                session_group=str(row.get("session_group") or ""),
                entry_time_local=str(row.get("entry_time_local") or ""),
                close_time_local=str(row.get("close_time_local") or ""),
            )
        )
    return orders


def deterministic_plan_id(trade_plan_path: Path, row_index: int, row: Any) -> str:
    """Build a restart-stable identifier for one Trade Plan row."""

    values = [
        trade_plan_path.name,
        str(row_index),
        str(get_row_value(row, "ticker") or get_row_value(row, "Ticker name") or ""),
        str(get_row_value(row, "direction") or get_row_value(row, "direction of trading") or ""),
        str(get_row_value(row, "entry_time_local") or ""),
        str(get_row_value(row, "close_time_local") or ""),
        str(get_row_value(row, "stop_loss") or get_row_value(row, "Stop Loss level") or ""),
        str(get_row_value(row, "take_profit") or get_row_value(row, "Take Profit level") or ""),
    ]
    raw = "|".join(values).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def calculate_volume_for_eur_notional(
    *,
    requested_eur_notional: float,
    price: float,
    contract_size: float,
    quote_to_eur_rate: float,
    volume_min: float,
    volume_max: float,
    volume_step: float,
) -> VolumeCalculation:
    """Convert requested EUR exposure to a broker-valid MT5 volume.

    The returned volume is rounded down to the nearest broker step.  If the
    minimum lot would exceed the requested notional exposure, an ExecutionError
    is raised instead of opening an oversized trade.
    """

    inputs = [
        requested_eur_notional,
        price,
        contract_size,
        quote_to_eur_rate,
        volume_min,
        volume_max,
        volume_step,
    ]
    if any(value is None or not math.isfinite(float(value)) or float(value) <= 0 for value in inputs):
        raise ExecutionError("Cannot calculate volume from invalid symbol or price inputs.")

    per_lot_exposure = price * contract_size * quote_to_eur_rate
    raw_volume = requested_eur_notional / per_lot_exposure
    rounded_volume = round_down_to_step(raw_volume, volume_step)
    if rounded_volume < volume_min:
        min_exposure = volume_min * per_lot_exposure
        raise ExecutionError(
            "Minimum permitted volume is too large for requested exposure: "
            f"min_volume={volume_min}, minimum_exposure_eur={min_exposure:.2f}, "
            f"requested_eur={requested_eur_notional:.2f}."
        )
    if rounded_volume > volume_max:
        rounded_volume = round_down_to_step(volume_max, volume_step)
    estimated_exposure = rounded_volume * per_lot_exposure
    if estimated_exposure - requested_eur_notional > max(1e-8, requested_eur_notional * 1e-9):
        raise ExecutionError(
            "Normalised volume would exceed requested exposure; trade skipped."
        )
    return VolumeCalculation(
        volume=rounded_volume,
        requested_eur_notional=requested_eur_notional,
        estimated_actual_exposure_eur=estimated_exposure,
        per_lot_exposure_eur=per_lot_exposure,
    )


def round_down_to_step(value: float, step: float) -> float:
    """Round value down to the nearest positive step."""

    if step <= 0:
        raise ExecutionError("Volume step must be positive.")
    precision = max(0, min(8, int(abs(math.log10(step))) + 2 if step < 1 else 2))
    units = math.floor((value + 1e-12) / step)
    return round(units * step, precision)


def normalize_and_validate_sl_tp(
    *,
    symbol_info: Any,
    direction: str,
    price: float,
    stop_loss: float,
    take_profit: float,
) -> tuple[float, float]:
    """Normalize and validate SL/TP against price and broker stop distance."""

    digits = int(_numeric(get_value(symbol_info, "digits")) or 5)
    point = _numeric(get_value(symbol_info, "point")) or 10 ** (-digits)
    stop_points = max(
        _numeric(get_value(symbol_info, "trade_stops_level")) or 0.0,
        _numeric(get_value(symbol_info, "trade_freeze_level")) or 0.0,
    )
    min_distance = stop_points * point
    sl = round(float(stop_loss), digits)
    tp = round(float(take_profit), digits)
    if direction == "buy":
        if not sl < price < tp:
            raise ExecutionError("Invalid buy SL/TP: expected SL < price < TP.")
        sl_distance = price - sl
        tp_distance = tp - price
    else:
        if not tp < price < sl:
            raise ExecutionError("Invalid sell SL/TP: expected TP < price < SL.")
        sl_distance = sl - price
        tp_distance = price - tp
    if sl_distance + 1e-12 < min_distance:
        raise ExecutionError("Stop Loss is closer than broker stop/freeze distance.")
    if tp_distance + 1e-12 < min_distance:
        raise ExecutionError("Take Profit is closer than broker stop/freeze distance.")
    return sl, tp


def build_market_order_request(
    *,
    client: Any,
    symbol: str,
    direction: str,
    volume: float,
    price: float,
    stop_loss: float,
    take_profit: float,
    magic: int,
    comment: str,
    filling_mode: int,
    deviation: int,
) -> dict[str, Any]:
    """Build an MT5 market order request."""

    request = {
        "action": mt5_constant(client, "TRADE_ACTION_DEAL", 1),
        "symbol": symbol,
        "volume": volume,
        "type": mt5_constant(client, "ORDER_TYPE_BUY", 0)
        if direction == "buy"
        else mt5_constant(client, "ORDER_TYPE_SELL", 1),
        "price": price,
        "deviation": deviation,
        "magic": magic,
        "comment": comment,
        "type_time": mt5_constant(client, "ORDER_TIME_GTC", 0),
        "type_filling": filling_mode,
    }
    if stop_loss > 0:
        request["sl"] = stop_loss
    if take_profit > 0:
        request["tp"] = take_profit
    return request


def build_close_request(
    *,
    client: Any,
    symbol: str,
    side: str,
    volume: float,
    price: float,
    position_ticket: str | None,
    magic: int,
    comment: str,
    filling_mode: int,
    deviation: int,
) -> dict[str, Any]:
    """Build an MT5 close request for a specific bot-owned position."""

    request = build_market_order_request(
        client=client,
        symbol=symbol,
        direction=side,
        volume=volume,
        price=price,
        stop_loss=0.0,
        take_profit=0.0,
        magic=magic,
        comment=comment,
        filling_mode=filling_mode,
        deviation=deviation,
    )
    if position_ticket:
        try:
            request["position"] = int(position_ticket)
        except ValueError:
            request["position"] = position_ticket
    return request


def close_position_by_ticket(
    *,
    client: Any,
    symbol_name: str,
    position_ticket: str | None,
    magic: int,
    comment: str,
    deviation: int,
) -> ExecutionOutcome:
    """Close one position identified by ticket and magic number."""

    position = find_position_for_plan(
        client,
        symbol_name=symbol_name,
        magic=magic,
        plan_id="",
        position_ticket=position_ticket,
    )
    if position is None:
        return ExecutionOutcome("", symbol_name, "closed", "position already closed")
    side = "sell" if position_direction(client, position) == "buy" else "buy"
    price = select_tick_price(require_tick(client, symbol_name), side)
    symbol_info = require_symbol_info(client, symbol_name)
    request = build_close_request(
        client=client,
        symbol=symbol_name,
        side=side,
        volume=_numeric(get_value(position, "volume")) or 0.0,
        price=price,
        position_ticket=position_ticket or _ticket_text(get_value(position, "ticket")),
        magic=magic,
        comment=comment,
        filling_mode=choose_filling_mode(client, symbol_info),
        deviation=deviation,
    )
    result = None
    for filling_mode in candidate_filling_modes(client, symbol_info, request.get("type_filling")):
        attempt = dict(request)
        attempt["type_filling"] = filling_mode
        result = send_order(client, attempt)
        if is_success_retcode(client, result):
            request = attempt
            break
        if is_unsupported_filling_retcode(client, result):
            LOGGER.info(
                "RECOVERY_CLOSE_FILLING_RETRY symbol=%s position=%s type_filling=%s",
                symbol_name,
                position_ticket,
                filling_mode,
            )
            continue
        raise ExecutionError(f"close failed: {describe_mt5_result(client, result)}")
    if result is None or not is_success_retcode(client, result):
        raise ExecutionError(f"close failed: {describe_mt5_result(client, result)}")
    return ExecutionOutcome(
        plan_id="",
        symbol=symbol_name,
        status="closed",
        message=describe_mt5_result(client, result),
        deal_ticket=_ticket_text(get_result_value(result, "deal")),
        price=_numeric(get_result_value(result, "price")) or price,
    )


def choose_filling_mode(client: Any, symbol_info: Any) -> int:
    """Choose a filling policy supported by the symbol."""

    order_fok = mt5_constant(client, "ORDER_FILLING_FOK", 0)
    order_ioc = mt5_constant(client, "ORDER_FILLING_IOC", 1)
    order_return = mt5_constant(client, "ORDER_FILLING_RETURN", 2)
    raw_mode = int(_numeric(get_value(symbol_info, "filling_mode")) or order_return)
    if raw_mode in {order_fok, order_ioc, order_return}:
        return raw_mode
    if raw_mode & 1:
        return order_fok
    if raw_mode & 2:
        return order_ioc
    return order_return


def candidate_filling_modes(
    client: Any,
    symbol_info: Any,
    preferred: Any | None = None,
) -> list[int]:
    """Return unique filling modes to try, starting with the preferred mode."""

    modes = [
        int(preferred) if preferred is not None else choose_filling_mode(client, symbol_info),
        mt5_constant(client, "ORDER_FILLING_FOK", 0),
        mt5_constant(client, "ORDER_FILLING_IOC", 1),
        mt5_constant(client, "ORDER_FILLING_RETURN", 2),
    ]
    result: list[int] = []
    for mode in modes:
        if mode not in result:
            result.append(mode)
    return result


def is_unsupported_filling_retcode(client: Any, result: Any) -> bool:
    """Return True when MT5 says the chosen filling mode is unsupported."""

    retcode = get_result_value(result, "retcode")
    try:
        numeric = int(retcode)
    except (TypeError, ValueError):
        return False
    unsupported = {
        10030,
        mt5_constant(client, "TRADE_RETCODE_INVALID_FILL", 10030),
    }
    return numeric in unsupported


def currency_to_eur_rate(client: Any, currency: str) -> float:
    """Return the current conversion rate from a currency into EUR."""

    normalized = str(currency or "").upper().strip()
    if normalized == "EUR":
        return 1.0
    if len(normalized) != 3:
        raise ExecutionError(f"Cannot infer EUR conversion for currency '{currency}'.")

    direct = f"{normalized}EUR"
    inverse = f"EUR{normalized}"
    direct_rate = _conversion_rate_from_exact_symbol(client, direct, inverse=False)
    if direct_rate is not None:
        return direct_rate
    inverse_rate = _conversion_rate_from_exact_symbol(client, inverse, inverse=True)
    if inverse_rate is not None:
        return inverse_rate

    symbols = {normalize_symbol_name(get_value(item, "name")): get_value(item, "name") for item in symbols_get(client)}
    if normalize_symbol_name(direct) in symbols:
        symbol_name = str(symbols[normalize_symbol_name(direct)])
        select_symbol(client, symbol_name)
        return select_tick_price(require_tick(client, symbol_name), None)
    if normalize_symbol_name(inverse) in symbols:
        symbol_name = str(symbols[normalize_symbol_name(inverse)])
        select_symbol(client, symbol_name)
        rate = select_tick_price(require_tick(client, symbol_name), None)
        if rate <= 0:
            raise ExecutionError(f"Invalid conversion rate for {symbol_name}.")
        return 1.0 / rate
    raise ExecutionError(f"No reliable MT5 conversion symbol for {normalized}->EUR.")


def _conversion_rate_from_exact_symbol(
    client: Any,
    symbol_name: str,
    inverse: bool,
) -> float | None:
    """Try an exact conversion symbol without scanning the whole broker list."""

    if not hasattr(client, "symbol_info"):
        return None
    try:
        info = client.symbol_info(symbol_name)
    except Exception:
        return None
    if info is None:
        return None
    try:
        select_symbol(client, symbol_name)
        rate = select_tick_price(require_tick(client, symbol_name), None)
    except Exception:
        return None
    if rate <= 0:
        return None
    return 1.0 / rate if inverse else rate


def resolve_symbol_name(client: Any, requested_symbol: str) -> str:
    """Resolve exact or normalized broker symbol names, including suffixes."""

    requested = str(requested_symbol).strip()
    requested_key = normalize_symbol_name(requested)
    names = [str(get_value(item, "name") or "").strip() for item in symbols_get(client)]
    names = [name for name in names if name]
    for name in names:
        if name == requested:
            return name
    for name in names:
        if name.upper() == requested.upper():
            return name
    for name in names:
        if normalize_symbol_name(name) == requested_key:
            return name
    for name in names:
        if normalize_symbol_name(name).startswith(requested_key):
            return name
    raise ExecutionError(f"Symbol {requested_symbol} was not found in MT5.")


def select_symbol(client: Any, symbol_name: str) -> None:
    """Select a symbol in Market Watch."""

    if hasattr(client, "symbol_select") and client.symbol_select(symbol_name, True) is False:
        raise ExecutionError(f"MT5 could not select symbol {symbol_name}.")


def require_symbol_info(client: Any, symbol_name: str) -> Any:
    """Return symbol_info or raise a controlled error."""

    if not hasattr(client, "symbol_info"):
        raise ExecutionError("MT5 client does not expose symbol_info().")
    info = client.symbol_info(symbol_name)
    if info is None:
        raise ExecutionError(f"MT5 returned no symbol_info for {symbol_name}.")
    return info


def require_tick(client: Any, symbol_name: str) -> Any:
    """Return a recent usable tick or raise a controlled error."""

    if not hasattr(client, "symbol_info_tick"):
        raise ExecutionError("MT5 client does not expose symbol_info_tick().")
    tick = client.symbol_info_tick(symbol_name)
    if tick is None:
        raise ExecutionError(f"MT5 returned no tick for {symbol_name}.")
    if select_tick_price(tick, None) <= 0:
        raise ExecutionError(f"MT5 tick for {symbol_name} has no usable price.")
    return tick


def select_tick_price(tick: Any, side: str | None) -> float:
    """Select ask for buy, bid for sell, or midpoint/last otherwise."""

    bid = _numeric(get_value(tick, "bid"))
    ask = _numeric(get_value(tick, "ask"))
    last = _numeric(get_value(tick, "last"))
    midpoint = (bid + ask) / 2.0 if bid and ask else None
    normalized_side = str(side or "").lower()
    if normalized_side in {"buy", "long"}:
        candidates = [ask, last, midpoint, bid]
    elif normalized_side in {"sell", "short"}:
        candidates = [bid, last, midpoint, ask]
    else:
        candidates = [last, midpoint, bid, ask]
    for candidate in candidates:
        if candidate is not None and candidate > 0:
            return float(candidate)
    raise ExecutionError("No usable bid, ask, last, or midpoint price.")


def has_unrelated_position_conflict(client: Any, symbol_name: str, magic: int) -> bool:
    """Return True if a same-symbol position is not clearly bot-owned."""

    for position in positions_get(client, symbol=symbol_name) or []:
        position_magic = int(_numeric(get_value(position, "magic")) or -1)
        if position_magic != magic:
            return True
    return False


def find_position_for_plan(
    client: Any,
    *,
    symbol_name: str,
    magic: int,
    plan_id: str,
    position_ticket: str | None = None,
) -> Any | None:
    """Find a bot-owned open position using ticket, magic and comment."""

    for position in positions_get(client, symbol=symbol_name) or []:
        if position_ticket and _ticket_text(get_value(position, "ticket")) == position_ticket:
            return position
        position_magic = int(_numeric(get_value(position, "magic")) or -1)
        comment = str(get_value(position, "comment") or "")
        if position_magic == magic and (not plan_id or plan_id[:16] in comment):
            return position
    return None


def reconcile_position_ticket(
    client: Any,
    *,
    symbol_name: str,
    magic: int,
    plan_id: str,
    order_result: Any,
) -> str | None:
    """Return the best-known MT5 position ticket for a successful order."""

    for key in ("position", "position_id", "request_id", "order"):
        ticket = _ticket_text(get_result_value(order_result, key))
        if ticket:
            position = find_position_for_plan(
                client,
                symbol_name=symbol_name,
                magic=magic,
                plan_id=plan_id,
                position_ticket=ticket,
            )
            if position is not None:
                return _ticket_text(get_value(position, "ticket")) or ticket
    position = find_position_for_plan(
        client,
        symbol_name=symbol_name,
        magic=magic,
        plan_id=plan_id,
    )
    if position is not None:
        return _ticket_text(get_value(position, "ticket"))
    return _ticket_text(get_result_value(order_result, "order"))


def position_direction(client: Any, position: Any) -> str:
    """Infer position direction from MT5 type constants."""

    position_type = int(_numeric(get_value(position, "type")) or 0)
    buy_type = mt5_constant(client, "POSITION_TYPE_BUY", 0)
    return "buy" if position_type == buy_type else "sell"


def send_order(client: Any, request: dict[str, Any]) -> Any:
    """Send an MT5 order request and ensure a response object exists."""

    if not hasattr(client, "order_send"):
        raise ExecutionError("MT5 client does not expose order_send().")
    result = client.order_send(request)
    if result is None:
        raise ExecutionError(f"order_send returned None. last_error={last_error(client)}")
    LOGGER.info("ORDER_SEND_RESULT %s", describe_mt5_result(client, result))
    return result


def is_success_retcode(
    client: Any,
    result: Any,
    include_check_success: bool = False,
) -> bool:
    """Return True when an MT5 check/send result has a success retcode."""

    retcode = get_result_value(result, "retcode")
    if retcode is None:
        return False
    success_codes = set(SUCCESS_RETCODES)
    for name in [
        "TRADE_RETCODE_DONE",
        "TRADE_RETCODE_PLACED",
        "TRADE_RETCODE_DONE_PARTIAL",
    ]:
        success_codes.add(mt5_constant(client, name, -999999))
    if include_check_success:
        success_codes.add(0)
    try:
        return int(retcode) in success_codes
    except (TypeError, ValueError):
        return False


def describe_mt5_result(client: Any, result: Any) -> str:
    """Return a compact log-safe description of an MT5 result."""

    if result is None:
        return f"None last_error={last_error(client)}"
    values = {
        "retcode": get_result_value(result, "retcode"),
        "comment": get_result_value(result, "comment"),
        "order": get_result_value(result, "order"),
        "deal": get_result_value(result, "deal"),
        "volume": get_result_value(result, "volume"),
        "price": get_result_value(result, "price"),
        "request_id": get_result_value(result, "request_id"),
        "last_error": last_error(client),
    }
    return ", ".join(f"{key}={value}" for key, value in values.items() if value is not None)


def is_demo_account(client: Any, account: Any) -> bool:
    """Return True only when MT5 explicitly reports demo account mode."""

    demo_constant = getattr(client, "ACCOUNT_TRADE_MODE_DEMO", None)
    trade_mode = get_value(account, "trade_mode")
    if demo_constant is None or trade_mode is None:
        return False
    try:
        return int(trade_mode) == int(demo_constant)
    except (TypeError, ValueError):
        return False


def contract_size(symbol_info: Any) -> float:
    """Return symbol contract size."""

    size = _numeric(get_value(symbol_info, "trade_contract_size"))
    if size is None or size <= 0:
        raise ExecutionError("Symbol trade_contract_size is missing or invalid.")
    return size


def volume_min(symbol_info: Any) -> float:
    """Return minimum broker volume."""

    return _positive_symbol_float(symbol_info, "volume_min")


def volume_max(symbol_info: Any) -> float:
    """Return maximum broker volume."""

    return _positive_symbol_float(symbol_info, "volume_max")


def volume_step(symbol_info: Any) -> float:
    """Return broker volume step."""

    return _positive_symbol_float(symbol_info, "volume_step")


def quote_currency(symbol_info: Any, symbol_name: str) -> str:
    """Return the currency used for notional exposure conversion."""

    for key in ("currency_profit", "currency_margin", "currency_base"):
        value = str(get_value(symbol_info, key) or "").upper().strip()
        if len(value) == 3:
            return value
    normalized = normalize_symbol_name(symbol_name)
    if len(normalized) >= 6:
        return normalized[-3:]
    raise ExecutionError(f"Cannot infer quote/profit currency for {symbol_name}.")


def mt5_constant(client: Any, name: str, default: int) -> int:
    """Return an MT5 constant with a documented fallback for tests."""

    try:
        return int(getattr(client, name))
    except (AttributeError, TypeError, ValueError):
        return default


def symbols_get(client: Any) -> list[Any]:
    """Return MT5 symbols or raise a controlled error."""

    if not hasattr(client, "symbols_get"):
        raise ExecutionError("MT5 client does not expose symbols_get().")
    symbols = client.symbols_get()
    if not symbols:
        raise ExecutionError(f"MT5 returned no symbols. last_error={last_error(client)}")
    return list(symbols)


def positions_get(client: Any, **kwargs: Any) -> list[Any]:
    """Call positions_get with compatibility for fake clients."""

    if not hasattr(client, "positions_get"):
        return []
    try:
        positions = client.positions_get(**kwargs)
    except TypeError:
        positions = client.positions_get()
        if kwargs.get("symbol"):
            positions = [
                position
                for position in positions
                if str(get_value(position, "symbol")) == str(kwargs["symbol"])
            ]
    return list(positions or [])


def call_optional(client: Any, name: str) -> Any:
    """Call an optional MT5 method and return None when absent."""

    if not hasattr(client, name):
        return None
    return getattr(client, name)()


def last_error(client: Any) -> Any:
    """Return MT5 last_error without raising."""

    if not hasattr(client, "last_error"):
        return "not available"
    try:
        return client.last_error()
    except Exception:
        return "not available"


def get_result_value(result: Any, key: str) -> Any:
    """Read a field from MT5 result namedtuples, dicts, or plain objects."""

    return get_value(result, key)


def get_row_value(row: Any, key: str) -> Any:
    """Read a value from a pandas Series or mapping."""

    if isinstance(row, dict):
        return row.get(key)
    if hasattr(row, "get"):
        return row.get(key)
    return get_value(row, key)


def get_value(item: Any, key: str) -> Any:
    """Read a value from dicts, namedtuples, or objects."""

    if item is None:
        return None
    if isinstance(item, dict):
        return item.get(key)
    if hasattr(item, "_asdict"):
        return item._asdict().get(key)
    return getattr(item, key, None)


def normalize_symbol_name(value: Any) -> str:
    """Return uppercase alphanumeric symbol key."""

    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _numeric(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _positive_symbol_float(symbol_info: Any, key: str) -> float:
    value = _numeric(get_value(symbol_info, key))
    if value is None or value <= 0:
        raise ExecutionError(f"Symbol {key} is missing or invalid.")
    return value


def _ticket_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text or text == "0" or text.lower() == "nan":
        return None
    return text


def _sleep_without_busy_wait(seconds: int) -> None:
    deadline = monotonic() + max(0, seconds)
    while monotonic() < deadline:
        remaining = deadline - monotonic()
        threading.Event().wait(min(1.0, max(0.0, remaining)))


_DEFAULT_TEST_TRADE_MANAGER: TestTradeManager | None = None
