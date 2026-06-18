from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from execution_ledger import ExecutionLedger
from mt5_execution import (
    ExecutionError,
    ExecutionSettings,
    MT5TradeExecutor,
    TestTradeManager as MT5TestTradeManager,
    TestTradeStatusStore as MT5TestTradeStatusStore,
    build_market_order_request,
    calculate_volume_for_eur_notional,
    currency_to_eur_rate,
    deterministic_plan_id,
    load_trade_plan_orders,
    normalize_and_validate_sl_tp,
)


class FakeMT5Client:
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_DONE_PARTIAL = 10010
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    ACCOUNT_TRADE_MODE_REAL = 0
    ACCOUNT_TRADE_MODE_DEMO = 1

    def __init__(
        self,
        *,
        demo: bool = True,
        check_retcode: int = 0,
        send_retcode: int = 10009,
        unsupported_fill_modes: set[int] | None = None,
    ):
        self.demo = demo
        self.check_retcode = check_retcode
        self.send_retcode = send_retcode
        self.unsupported_fill_modes = unsupported_fill_modes or set()
        self.positions: list[SimpleNamespace] = []
        self.sent_requests: list[dict] = []
        self.next_ticket = 1000

    def initialize(self):
        return True

    def terminal_info(self):
        return SimpleNamespace(connected=True, trade_allowed=True)

    def account_info(self):
        trade_mode = self.ACCOUNT_TRADE_MODE_DEMO if self.demo else self.ACCOUNT_TRADE_MODE_REAL
        return SimpleNamespace(trade_mode=trade_mode, trade_allowed=True, trade_expert=True)

    def symbols_get(self):
        return [
            self._symbol("TEST", "EUR", 100.0, 0.01, 100.0, 0.01),
            self._symbol("BTCUSD", "USD", 1.0, 0.001, 10.0, 0.001),
            self._symbol("EURUSD", "USD", 100000.0, 0.01, 100.0, 0.01),
            self._symbol("USDEUR", "EUR", 100000.0, 0.01, 100.0, 0.01),
        ]

    def symbol_select(self, symbol, selected):
        return selected and any(item.name == symbol for item in self.symbols_get())

    def symbol_info(self, symbol):
        return next((item for item in self.symbols_get() if item.name == symbol), None)

    def symbol_info_tick(self, symbol):
        prices = {
            "TEST": (9.99, 10.0, 10.0),
            "BTCUSD": (49990.0, 50000.0, 50000.0),
            "EURUSD": (1.1, 1.1002, 1.1001),
            "USDEUR": (0.91, 0.92, 0.915),
        }
        bid, ask, last = prices[symbol]
        return SimpleNamespace(bid=bid, ask=ask, last=last)

    def positions_get(self, **kwargs):
        symbol = kwargs.get("symbol")
        if symbol:
            return [position for position in self.positions if position.symbol == symbol]
        return list(self.positions)

    def order_check(self, request):
        return SimpleNamespace(retcode=self.check_retcode, comment="check")

    def order_send(self, request):
        self.sent_requests.append(dict(request))
        if request.get("type_filling") in self.unsupported_fill_modes:
            return SimpleNamespace(retcode=10030, comment="Unsupported filling mode")
        if self.send_retcode != self.TRADE_RETCODE_DONE:
            return SimpleNamespace(retcode=self.send_retcode, comment="rejected")

        self.next_ticket += 1
        ticket = self.next_ticket
        if "position" in request:
            self.positions = [
                position
                for position in self.positions
                if str(position.ticket) != str(request["position"])
            ]
        else:
            self.positions.append(
                SimpleNamespace(
                    ticket=ticket,
                    symbol=request["symbol"],
                    type=request["type"],
                    volume=request["volume"],
                    magic=request["magic"],
                    comment=request["comment"],
                )
            )
        return SimpleNamespace(
            retcode=self.TRADE_RETCODE_DONE,
            comment="done",
            order=ticket,
            deal=ticket + 10000,
            position=ticket,
            volume=request["volume"],
            price=request["price"],
        )

    def last_error(self):
        return (0, "ok")

    @staticmethod
    def _symbol(name, currency_profit, contract_size, volume_min, volume_max, volume_step):
        return SimpleNamespace(
            name=name,
            trade_mode=1,
            currency_profit=currency_profit,
            trade_contract_size=contract_size,
            volume_min=volume_min,
            volume_max=volume_max,
            volume_step=volume_step,
            digits=5 if name.endswith("USD") else 2,
            point=0.00001 if name.endswith("USD") else 0.01,
            trade_stops_level=0,
            trade_freeze_level=0,
            filling_mode=1,
        )


def write_trade_plan(path: Path) -> None:
    pd.DataFrame(
        [
            {
                "ticker": "TEST",
                "direction": "buy",
                "stop_loss": 9.0,
                "take_profit": 11.0,
                "session_group": "unit",
                "entry_time_local": "2026-06-12 09:05",
                "close_time_local": "2026-06-12 21:45",
            }
        ]
    ).to_csv(path, index=False)


def test_volume_rounds_down_and_rejects_oversized_minimum():
    calc = calculate_volume_for_eur_notional(
        requested_eur_notional=1000,
        price=123,
        contract_size=1,
        quote_to_eur_rate=1,
        volume_min=0.01,
        volume_max=100,
        volume_step=0.1,
    )

    assert calc.volume == 8.1
    assert calc.estimated_actual_exposure_eur <= 1000

    with pytest.raises(ExecutionError, match="Minimum permitted volume"):
        calculate_volume_for_eur_notional(
            requested_eur_notional=50,
            price=1000,
            contract_size=1,
            quote_to_eur_rate=1,
            volume_min=0.1,
            volume_max=100,
            volume_step=0.1,
        )


def test_currency_conversion_direct_and_invalid():
    client = FakeMT5Client()

    assert currency_to_eur_rate(client, "USD") == pytest.approx(0.915)
    assert currency_to_eur_rate(client, "EUR") == 1.0

    with pytest.raises(ExecutionError):
        currency_to_eur_rate(client, "JPY")


def test_buy_sell_request_construction_and_sl_tp_validation():
    client = FakeMT5Client()

    buy_request = build_market_order_request(
        client=client,
        symbol="TEST",
        direction="buy",
        volume=1.0,
        price=10.0,
        stop_loss=9.0,
        take_profit=11.0,
        magic=123,
        comment="MAd:S:abc",
        filling_mode=client.ORDER_FILLING_FOK,
        deviation=20,
    )
    sell_request = build_market_order_request(
        client=client,
        symbol="TEST",
        direction="sell",
        volume=1.0,
        price=10.0,
        stop_loss=11.0,
        take_profit=9.0,
        magic=123,
        comment="MAd:S:def",
        filling_mode=client.ORDER_FILLING_FOK,
        deviation=20,
    )

    assert buy_request["type"] == client.ORDER_TYPE_BUY
    assert sell_request["type"] == client.ORDER_TYPE_SELL
    assert buy_request["sl"] == 9.0
    assert buy_request["tp"] == 11.0

    symbol_info = client.symbol_info("TEST")
    assert normalize_and_validate_sl_tp(
        symbol_info=symbol_info,
        direction="buy",
        price=10.0,
        stop_loss=9.0,
        take_profit=11.0,
    ) == (9.0, 11.0)
    with pytest.raises(ExecutionError, match="Invalid buy"):
        normalize_and_validate_sl_tp(
            symbol_info=symbol_info,
            direction="buy",
            price=10.0,
            stop_loss=10.5,
            take_profit=11.0,
        )


def test_execute_trade_plan_success_duplicate_prevention_and_close(tmp_path):
    path = tmp_path / "trade_plan_unit_2026-06-12_09-05.csv"
    write_trade_plan(path)
    fake_client = FakeMT5Client()
    ledger = ExecutionLedger(tmp_path / "ledger.sqlite3")
    executor = MT5TradeExecutor(
        settings=ExecutionSettings(target_notional_eur=1000, allow_live_trading=False),
        ledger=ledger,
        client_factory=lambda: fake_client,
    )

    first = executor.execute_trade_plan_file(path)
    second = executor.execute_trade_plan_file(path)

    assert first[0].status == "opened"
    assert second[0].status == "skipped"
    assert len([request for request in fake_client.sent_requests if "position" not in request]) == 1

    close_outcome = executor.close_trade_plan_file(path)

    assert close_outcome[0].status == "closed"
    assert len(fake_client.positions) == 0
    assert any("position" in request for request in fake_client.sent_requests)


def test_strategy_execution_retries_unsupported_filling_mode(tmp_path):
    path = tmp_path / "trade_plan_unit_2026-06-12_09-05.csv"
    write_trade_plan(path)
    fake_client = FakeMT5Client(unsupported_fill_modes={FakeMT5Client.ORDER_FILLING_IOC})
    executor = MT5TradeExecutor(
        settings=ExecutionSettings(target_notional_eur=1000, allow_live_trading=False),
        ledger=ExecutionLedger(tmp_path / "retry.sqlite3"),
        client_factory=lambda: fake_client,
    )

    open_outcome = executor.execute_trade_plan_file(path)
    close_outcome = executor.close_trade_plan_file(path)

    open_requests = [request for request in fake_client.sent_requests if "position" not in request]
    close_requests = [request for request in fake_client.sent_requests if "position" in request]
    assert open_outcome[0].status == "opened"
    assert close_outcome[0].status == "closed"
    assert [request["type_filling"] for request in open_requests[:2]] == [
        FakeMT5Client.ORDER_FILLING_IOC,
        FakeMT5Client.ORDER_FILLING_FOK,
    ]
    assert [request["type_filling"] for request in close_requests[:2]] == [
        FakeMT5Client.ORDER_FILLING_IOC,
        FakeMT5Client.ORDER_FILLING_FOK,
    ]
    assert len(fake_client.positions) == 0


def test_failed_order_check_and_live_account_refusal(tmp_path):
    path = tmp_path / "trade_plan_unit_2026-06-12_09-05.csv"
    write_trade_plan(path)
    order = load_trade_plan_orders(path)[0]

    failed_check_client = FakeMT5Client(check_retcode=10013)
    executor = MT5TradeExecutor(
        ledger=ExecutionLedger(tmp_path / "failed.sqlite3"),
        client_factory=lambda: failed_check_client,
    )
    with pytest.raises(ExecutionError, match="order_check failed"):
        executor.execute_order(order)

    live_client = FakeMT5Client(demo=False)
    live_executor = MT5TradeExecutor(
        settings=ExecutionSettings(allow_live_trading=False),
        ledger=ExecutionLedger(tmp_path / "live.sqlite3"),
        client_factory=lambda: live_client,
    )
    with pytest.raises(ExecutionError, match="demo account"):
        live_executor.execute_order(order)


def test_restart_reconciliation_does_not_send_duplicate_order(tmp_path):
    path = tmp_path / "trade_plan_unit_2026-06-12_09-05.csv"
    write_trade_plan(path)
    order = load_trade_plan_orders(path)[0]
    fake_client = FakeMT5Client()
    ledger = ExecutionLedger(tmp_path / "restart.sqlite3")
    ledger.upsert_pending(
        {
            "plan_id": order.plan_id,
            "source_trade_plan": str(path),
            "symbol": order.symbol,
            "direction": order.direction,
            "planned_sl": order.stop_loss,
            "planned_tp": order.take_profit,
            "requested_eur_notional": 1000,
            "requested_volume": 1.0,
            "magic_number": 26061801,
            "comment": f"MAd:S:{order.plan_id[:16]}",
        }
    )
    fake_client.positions.append(
        SimpleNamespace(
            ticket=333,
            symbol="TEST",
            type=fake_client.POSITION_TYPE_BUY,
            volume=1.0,
            magic=26061801,
            comment=f"MAd:S:{order.plan_id[:16]}",
        )
    )
    executor = MT5TradeExecutor(
        ledger=ledger,
        client_factory=lambda: fake_client,
    )

    outcome = executor.execute_order(order)

    assert outcome.message == "reconciled existing bot-owned position"
    assert fake_client.sent_requests == []


def test_manual_position_conflict_is_rejected(tmp_path):
    path = tmp_path / "trade_plan_unit_2026-06-12_09-05.csv"
    write_trade_plan(path)
    fake_client = FakeMT5Client()
    fake_client.positions.append(
        SimpleNamespace(
            ticket=777,
            symbol="TEST",
            type=fake_client.POSITION_TYPE_BUY,
            volume=1.0,
            magic=999,
            comment="manual",
        )
    )
    executor = MT5TradeExecutor(
        ledger=ExecutionLedger(tmp_path / "manual.sqlite3"),
        client_factory=lambda: fake_client,
    )

    outcome = executor.execute_trade_plan_file(path)[0]

    assert outcome.status == "failed"
    assert "unrelated position" in outcome.message


def test_test_trade_demo_sequence_and_second_command_rejection(tmp_path):
    fake_client = FakeMT5Client()
    manager = MT5TestTradeManager(
        settings=ExecutionSettings(test_hold_seconds=0, test_notional_eur=50),
        ledger=ExecutionLedger(tmp_path / "test.sqlite3"),
        client_factory=lambda: fake_client,
        status_store=MT5TestTradeStatusStore(tmp_path / "test_status.json"),
    )

    outcome = manager.run_blocking()

    assert outcome.status == "closed"
    assert len(fake_client.positions) == 0

    manager._active = True
    manager._worker = None
    assert manager.start() is True
    assert manager._worker is not None
    manager._worker.join(timeout=5)


def test_test_trade_rejects_live_worker(tmp_path):
    manager = MT5TestTradeManager(
        settings=ExecutionSettings(test_hold_seconds=0, test_notional_eur=50),
        ledger=ExecutionLedger(tmp_path / "test.sqlite3"),
        client_factory=lambda: FakeMT5Client(),
        status_store=MT5TestTradeStatusStore(tmp_path / "test_status.json"),
    )
    manager._active = True
    manager._worker = threading.current_thread()

    assert manager.start() is False


def test_test_trade_retries_unsupported_filling_mode(tmp_path):
    fake_client = FakeMT5Client(unsupported_fill_modes={FakeMT5Client.ORDER_FILLING_IOC})
    manager = MT5TestTradeManager(
        settings=ExecutionSettings(test_hold_seconds=0, test_notional_eur=50),
        ledger=ExecutionLedger(tmp_path / "test.sqlite3"),
        client_factory=lambda: fake_client,
        status_store=MT5TestTradeStatusStore(tmp_path / "test_status.json"),
    )

    outcome = manager.run_blocking()

    open_requests = [request for request in fake_client.sent_requests if "position" not in request]
    assert outcome.status == "closed"
    assert [request["type_filling"] for request in open_requests[:2]] == [
        FakeMT5Client.ORDER_FILLING_IOC,
        FakeMT5Client.ORDER_FILLING_FOK,
    ]


def test_deterministic_plan_id_is_stable(tmp_path):
    path = tmp_path / "trade_plan_unit.csv"
    row = {
        "ticker": "TEST",
        "direction": "buy",
        "entry_time_local": "2026-06-12 09:05",
        "close_time_local": "2026-06-12 21:45",
        "stop_loss": 9,
        "take_profit": 11,
    }

    assert deterministic_plan_id(path, 0, row) == deterministic_plan_id(path, 0, row)
