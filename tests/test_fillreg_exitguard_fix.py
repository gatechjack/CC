"""#3 lock-resilient fill-registration + #5-B/#5-C exit-guard exemptions.

Bracket-redesign prerequisites (branch bitunix-fillreg-exitguard-fix-2026-06-16).

#3  — a BROKER-CONFIRMED fill must ALWAYS register; a transient 'database is
      locked' during persistence must NOT be mis-handled as live_order_rejected
      (the 2026-06-16 09:48-ET orphan). Two layers:
        A) logger.log_proposed_order retries a lock (never raises on a lock).
        B) data_exec.place: once the broker confirms the fill, NO persistence
           error converts it into a rejection — the fill is always returned.
      A GENUINE broker rejection still propagates (the distinction).
#5-B — the broker halt latch exempts reduce_only EXITS (entries stay blocked).
#5-C — the data_exec staleness gate exempts reduce_only EXITS (entries blocked).

All mocked / fundless.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3

import pytest

from trading_corp.agents import logger as logger_mod
from trading_corp.agents.data_exec import DataExecAgent
from trading_corp.agents.logger import LoggerAgent
from trading_corp.brokers import bitunix as bx
from trading_corp.brokers.base import AccountSnapshot
from trading_corp.brokers.bitunix import BitunixBroker
from trading_corp.brokers.bitunix_exceptions import BitunixStaleSnapshot
from trading_corp.persistence import db
from trading_corp.persistence.db import init_db
from trading_corp.persistence.models import FillEvent, ProposedOrder

# ── BitUnix REST endpoint paths (verbatim, for the #5-B broker-level tests) ──
P_PENDING_POS = "/api/v1/futures/position/get_pending_positions"
P_PLACE = "/api/v1/futures/trade/place_order"
P_ORDER_DETAIL = "/api/v1/futures/trade/get_order_detail"
P_HISTORY = "/api/v1/futures/trade/get_history_trades"


class FakeResp:
    def __init__(self, payload: dict):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class RecordingClient:
    """Stand-in for httpx.AsyncClient (per test_bitunix_broker_write.py)."""

    def __init__(self):
        self.calls: list[dict] = []
        self._responses: dict[str, list[dict]] = {}

    def queue(self, path: str, *payloads: dict):
        self._responses.setdefault(path, []).extend(payloads)

    def _resp(self, path: str) -> FakeResp:
        q = self._responses.get(path)
        if not q:
            return FakeResp({"code": 0, "msg": "Success", "data": {}})
        payload = q.pop(0) if len(q) > 1 else q[0]
        return FakeResp(payload)

    async def get(self, path, params=None, headers=None):
        self.calls.append({"method": "GET", "path": path})
        return self._resp(path)

    async def post(self, path, content=None, headers=None):
        self.calls.append({"method": "POST", "path": path})
        return self._resp(path)

    def posts_to(self, path):
        return [c for c in self.calls if c["method"] == "POST" and c["path"] == path]


def _make_broker() -> tuple[BitunixBroker, RecordingClient]:
    broker = BitunixBroker(api_key="k", api_secret="s")
    client = RecordingClient()
    broker._client = client  # type: ignore[assignment]
    broker._fill_poll_interval_s = 0.0
    return broker, client


def _queue_exit_fill(client: RecordingClient):
    """Happy-path responses for a reduce-only exit on an open ONE_WAY short."""
    client.queue(P_PENDING_POS,
                 {"code": 0, "data": [{"positionMode": "ONE_WAY", "side": "SELL"}]})
    client.queue(P_PLACE, {"code": 0, "data": {"orderId": "OIDx"}})
    client.queue(P_ORDER_DETAIL,
                 {"code": 0, "data": {"orderId": "OIDx", "status": "FILLED",
                                      "tradeQty": "0.001"}})
    client.queue(P_HISTORY,
                 {"code": 0, "data": {"tradeList": [
                     {"qty": "0.001", "price": "64000", "fee": "0.02"}]}})


def _entry_order() -> ProposedOrder:
    return ProposedOrder(
        strategy="bitunix_futures", symbol="BTC/USDT.P", side="buy",
        qty=0.001, order_type="market", extra={"leverage": 8},
    )


def _exit_order() -> ProposedOrder:
    return ProposedOrder(
        strategy="bitunix_futures", symbol="BTC/USDT.P", side="buy",
        qty=0.001, order_type="market", extra={"reduce_only": True},
    )


# ───────────────────────── #5-B (broker halt latch) ─────────────────────────

@pytest.mark.asyncio
async def test_halt_exempts_reduce_only_exit():
    """A reduce_only EXIT must be placeable even when _halt_new_orders is set."""
    broker, client = _make_broker()
    broker._halt_new_orders = True
    broker._halt_reason = "position_state_reconciler_divergence"
    _queue_exit_fill(client)

    fill = await broker.place_order(_exit_order())  # must NOT raise the halt

    assert fill.price == pytest.approx(64000.0)
    assert client.posts_to(P_PLACE), "exit order was not sent to the venue"


@pytest.mark.asyncio
async def test_halt_still_blocks_entry():
    """An ENTRY (reduce_only=False) stays blocked by the halt latch."""
    broker, client = _make_broker()
    broker._halt_new_orders = True
    broker._halt_reason = "position_state_reconciler_divergence"

    with pytest.raises(RuntimeError, match="halted"):
        await broker.place_order(_entry_order())

    assert client.posts_to(P_PLACE) == [], "halted entry must not reach the venue"


# ───────────────── data_exec-level fakes for #5-C + #3 ──────────────────────

class FakePushNotifier:
    def __init__(self, return_value: bool = True):
        self.calls: list[dict] = []
        self.return_value = return_value

    async def push(self, text, *, audit_path="other", audit_context=None):
        self.calls.append({"text": text, "audit_path": audit_path})
        return self.return_value


class FakeBitunixBroker:
    """Bitunix-shaped broker: a duck-typed `_assert_snapshot_fresh` that raises
    BitunixStaleSnapshot when `stale=True`, and a place_order that returns a
    fill (or a configured raise)."""
    name = "bitunix_futures"
    paper = False

    def __init__(self, *, stale: bool = False, place_raises: Exception | None = None):
        self._stale = stale
        self._place_raises = place_raises
        self._halt_new_orders = False
        self._halt_reason: str | None = None
        self.fresh_called = False
        self.place_called = False

    async def connect(self): pass
    async def disconnect(self): pass

    async def snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(account="bx", equity=1000.0, buying_power=1000.0,
                               cash=1000.0, positions=[])

    async def quote(self, symbol: str) -> float: return 0.0
    async def cancel_order(self, oid: str) -> bool: return False

    async def _assert_snapshot_fresh(self) -> None:
        self.fresh_called = True
        if self._stale:
            self._halt_new_orders = True
            self._halt_reason = "snapshot_stale:999s"
            raise BitunixStaleSnapshot(age_s=999.0, threshold_s=60.0)

    async def place_order(self, order) -> FillEvent:
        self.place_called = True
        if self._place_raises is not None:
            raise self._place_raises
        return FillEvent(
            order_id="venue-1", symbol=order.symbol, side=order.side,
            qty=float(order.qty), price=64000.0, ts="2026-06-16T21:00:00+00:00",
            venue="bitunix_futures",
        )


def _de(tmp_path) -> tuple[DataExecAgent, str]:
    url = f"sqlite:///{tmp_path / 'fillreg.db'}"
    init_db(url)
    de = DataExecAgent(LoggerAgent(db_url=url), safety_notifier=FakePushNotifier())
    return de, url


# ───────────────────────────── #5-C (staleness) ─────────────────────────────

@pytest.mark.asyncio
async def test_exit_skips_staleness_gate_when_stale(tmp_path):
    """A reduce_only EXIT closes even on a stale snapshot — the gate is skipped."""
    de, _ = _de(tmp_path)
    broker = FakeBitunixBroker(stale=True)
    de.register_broker("bitunix_futures", broker)

    fill = await de.place(_exit_order(), division="bitunix_futures")

    assert fill.price == pytest.approx(64000.0)
    assert broker.fresh_called is False, "staleness gate must be SKIPPED for exits"
    assert broker.place_called is True


@pytest.mark.asyncio
async def test_entry_blocked_by_staleness_gate_when_stale(tmp_path):
    """An ENTRY is still refused on a stale snapshot (gate not weakened)."""
    de, _ = _de(tmp_path)
    broker = FakeBitunixBroker(stale=True)
    de.register_broker("bitunix_futures", broker)

    with pytest.raises(BitunixStaleSnapshot):
        await de.place(_entry_order(), division="bitunix_futures")

    assert broker.fresh_called is True
    assert broker.place_called is False, "stale entry must not reach place_order"


# ────────────────────────────── #3 (data_exec) ──────────────────────────────

@pytest.mark.asyncio
async def test_confirmed_fill_survives_persistence_error(tmp_path):
    """Broker confirmed the fill; a persistence error must NOT convert it into a
    rejection — place() returns the fill (caller then registers it)."""
    de, _ = _de(tmp_path)
    broker = FakeBitunixBroker()
    de.register_broker("bitunix_futures", broker)
    # Simulate the post-fill persistence write blowing up.
    def _boom(_order):
        raise sqlite3.OperationalError("database is locked")
    de.logger.log_proposed_order = _boom  # type: ignore[assignment]

    order = _entry_order()
    fill = await de.place(order, division="bitunix_futures")  # must NOT raise

    assert fill.order_id == "venue-1"
    assert order.status == "filled"


@pytest.mark.asyncio
async def test_genuine_broker_rejection_propagates(tmp_path):
    """A real broker rejection (place_order raises) still propagates — so the
    caller correctly stamps live_order_rejected (the distinction)."""
    de, _ = _de(tmp_path)
    broker = FakeBitunixBroker(place_raises=RuntimeError("broker rejected: insufficient margin"))
    de.register_broker("bitunix_futures", broker)

    order = _entry_order()
    with pytest.raises(RuntimeError, match="broker rejected"):
        await de.place(order, division="bitunix_futures")

    assert order.status != "filled"


# ──────────────────────── #3 part A (logger retry) ──────────────────────────

def _proposed() -> ProposedOrder:
    return ProposedOrder(
        strategy="bitunix_futures", symbol="BTC/USDT.P", side="sell",
        qty=0.001, order_type="market", extra={"reduce_only": True},
    )


def test_log_proposed_order_retries_transient_lock(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'lp.db'}"
    init_db(url)
    la = LoggerAgent(db_url=url)
    monkeypatch.setattr(logger_mod, "_DB_LOCK_RETRY_DELAYS_SEC", (0.0, 0.0, 0.0))

    real_connect = logger_mod.db.connect
    state = {"n": 0}

    @contextlib.contextmanager
    def flaky(u):
        state["n"] += 1
        if state["n"] <= 2:
            raise sqlite3.OperationalError("database is locked")
        with real_connect(u) as conn:
            yield conn

    monkeypatch.setattr(logger_mod.db, "connect", flaky)
    order = _proposed()
    la.log_proposed_order(order)  # retries twice then succeeds — must NOT raise

    assert state["n"] == 3  # 2 locked attempts + 1 success
    # Verify the row actually landed (raw read path).
    path = db.resolve_db_path(url)
    conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id FROM proposed_order WHERE id = ?", (order.id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "proposed_order row must persist after lock retries"


def test_log_proposed_order_nonlock_error_propagates(tmp_path, monkeypatch):
    """A NON-lock OperationalError (genuine bug) still propagates — not masked."""
    url = f"sqlite:///{tmp_path / 'lp2.db'}"
    init_db(url)
    la = LoggerAgent(db_url=url)

    @contextlib.contextmanager
    def boom(u):
        raise sqlite3.OperationalError("no such table: proposed_order")
        yield  # pragma: no cover  (makes this a generator for @contextmanager)

    monkeypatch.setattr(logger_mod.db, "connect", boom)
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        la.log_proposed_order(_proposed())
