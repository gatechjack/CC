"""K5·3 — kalshi copy loop wiring: live-arm gate, entry/exit write-back, and the
live placement handler. Fundless (fake data_exec / channel / logger; the agent runs
against a temp sqlite for its snapshot state)."""
from __future__ import annotations

import pytest

from trading_corp.brokers.base import Broker
from trading_corp.brokers.kalshi import KalshiBroker
from trading_corp.brokers.kalshi_live import KalshiLiveBroker, KalshiNoFill, OrderPlacementError
from trading_corp.persistence import db as _db
from trading_corp.persistence.models import FillEvent, ProposedOrder
from trading_corp.main import _handle_kalshi_copy_order_placement, _kalshi_is_live_armed


# ── fakes ────────────────────────────────────────────────────────────────────


class _PaperLikeBroker(Broker):
    name = "paper-like"
    paper = True

    async def connect(self):
        return None

    async def disconnect(self):
        return None

    async def snapshot(self):
        return None

    async def quote(self, symbol):
        return 0.0

    async def place_order(self, order):
        return None

    async def cancel_order(self, order_id):
        return False


class _Logger:
    def __init__(self):
        self.events = []

    def log_event(self, actor, kind, payload):
        self.events.append((kind, payload))

    def kinds(self):
        return [k for k, _ in self.events]


class _Chan:
    async def push(self, msg):
        return None


class _DataExec:
    def __init__(self, *, fill=None, exc=None):
        self._fill = fill
        self._exc = exc
        self.placed = []

    async def place(self, order, division=None):
        self.placed.append((order, division))
        if self._exc:
            raise self._exc
        return self._fill


@pytest.fixture
def agent_db(tmp_path):
    from trading_corp.agents.strategies.kalshi_copy_trader import KalshiCopyTraderAgent
    db_url = f"sqlite:///{tmp_path / 'k5loop.db'}"
    _db.init_db(db_url)
    yaml_path = tmp_path / "strategies.yaml"
    yaml_path.write_text("kalshi_copy_trader:\n  enabled: true\n  poll_interval_sec: 300\n")
    risk_path = tmp_path / "risk.yaml"
    risk_path.write_text("kalshi: {}\n")
    agent = KalshiCopyTraderAgent(strategies_yaml=yaml_path, risk_yaml=risk_path, db_url=db_url)
    return agent, db_url


def _seed_lot(db_url, *, whale="alice", ticker="KXBTC-T1", outcome="yes",
              copy_usd=2.0, entry_price=0.50):
    _db.set_agent_state(
        "kalshi_copy_trader", f"positions:{whale}",
        {ticker: {"contracts": 100, "pnl": 0.0, "first_seen_iso": "t0",
                  "our_side": outcome, "copy_size_usd": copy_usd, "entry_price": entry_price}},
        db_url=db_url,
    )


def _order(*, side="buy", outcome="yes", ticker="KXBTC-T1", whale="alice",
           copy_usd=2.0, limit_price=0.50, is_entry=True):
    return ProposedOrder(
        strategy="kalshi_copy_trader", symbol=f"{ticker}:{outcome}", side=side,
        qty=copy_usd, order_type="market", limit_price=limit_price,
        extra={"is_entry": is_entry, "outcome": outcome, "ticker": ticker,
               "whale_handle": whale, "copy_size_usd": copy_usd,
               "division": "kalshi_copy_trading"},
    )


def _fill(qty=4.0, price=0.49, fee=0.02, side="buy"):
    return FillEvent(order_id="O1", symbol="KXBTC-T1:yes", side=side, qty=qty,
                     price=price, ts="t", venue="kalshi", fee=fee)


# ── _kalshi_is_live_armed ────────────────────────────────────────────────────


def test_live_broker_is_armed():
    assert _kalshi_is_live_armed(KalshiLiveBroker(api_key_id="k", private_key_pem="pem")) is True


def test_readonly_broker_not_armed():
    assert _kalshi_is_live_armed(KalshiBroker(api_key_id="k", private_key_pem="pem")) is False


def test_paper_broker_not_armed():
    # The crux fix: a PaperExecutionBroker is a Broker but paper=True -> NOT armed.
    assert _kalshi_is_live_armed(_PaperLikeBroker()) is False


def test_none_not_armed():
    assert _kalshi_is_live_armed(None) is False


# ── write-back ───────────────────────────────────────────────────────────────


def test_record_entry_fill_overwrites_with_actual(agent_db):
    agent, db_url = agent_db
    _seed_lot(db_url, copy_usd=2.0, entry_price=0.50)
    agent.record_entry_fill(_order(), _fill(qty=4.0, price=0.49))
    snap = _db.load_agent_state("kalshi_copy_trader", "positions:alice", db_url=db_url)[0]
    rec = snap["KXBTC-T1"]
    assert rec["entry_price"] == pytest.approx(0.49)
    assert rec["copy_size_usd"] == pytest.approx(4.0 * 0.49)
    assert rec["actual_fill_qty"] == pytest.approx(4.0)


def test_discard_entry_removes_lot(agent_db):
    agent, db_url = agent_db
    _seed_lot(db_url)
    agent.discard_entry(_order())
    snap = _db.load_agent_state("kalshi_copy_trader", "positions:alice", db_url=db_url)[0]
    assert "KXBTC-T1" not in snap


def test_record_exit_fill_residual():
    from trading_corp.agents.strategies.kalshi_copy_trader import KalshiCopyTraderAgent
    a = KalshiCopyTraderAgent.__new__(KalshiCopyTraderAgent)   # no DB needed for the pure calc
    o = _order(side="sell", is_entry=False, copy_usd=2.0)
    assert a.record_exit_fill(o, None) == pytest.approx(2.0)              # no-fill -> full intended
    assert a.record_exit_fill(o, _fill(qty=2.0, price=0.50)) == pytest.approx(1.0)  # partial: 2 - 1
    assert a.record_exit_fill(o, _fill(qty=4.0, price=0.50)) == pytest.approx(0.0)  # full clear


# ── _handle_kalshi_copy_order_placement ──────────────────────────────────────


async def test_handler_entry_full_fill_records_and_logs(agent_db):
    agent, db_url = agent_db
    _seed_lot(db_url, copy_usd=2.0, entry_price=0.50)
    lg = _Logger()
    de = _DataExec(fill=_fill(qty=4.0, price=0.49))
    await _handle_kalshi_copy_order_placement(
        agent=agent, order=_order(), data_exec=de, logger_agent=lg,
        channel=_Chan(), base_payload={"strategy": "kalshi_copy_trader"},
    )
    assert "kalshi_copy_placed_live" in lg.kinds()
    snap = _db.load_agent_state("kalshi_copy_trader", "positions:alice", db_url=db_url)[0]
    assert snap["KXBTC-T1"]["entry_price"] == pytest.approx(0.49)


async def test_handler_entry_nofill_discards_and_skips(agent_db):
    agent, db_url = agent_db
    _seed_lot(db_url)
    lg = _Logger()
    de = _DataExec(exc=KalshiNoFill("no match"))
    await _handle_kalshi_copy_order_placement(
        agent=agent, order=_order(), data_exec=de, logger_agent=lg,
        channel=_Chan(), base_payload={},
    )
    assert "kalshi_copy_no_fill" in lg.kinds()
    snap = _db.load_agent_state("kalshi_copy_trader", "positions:alice", db_url=db_url)[0]
    assert "KXBTC-T1" not in snap   # optimistic lot discarded


async def test_handler_real_failure_propagates(agent_db):
    agent, _ = agent_db
    lg = _Logger()
    de = _DataExec(exc=OrderPlacementError("auth boom"))
    with pytest.raises(OrderPlacementError):
        await _handle_kalshi_copy_order_placement(
            agent=agent, order=_order(), data_exec=de, logger_agent=lg,
            channel=_Chan(), base_payload={},
        )


async def test_handler_exit_nofill_flags_residual(agent_db):
    agent, _ = agent_db
    lg = _Logger()
    de = _DataExec(exc=KalshiNoFill("no match"))
    await _handle_kalshi_copy_order_placement(
        agent=agent, order=_order(side="sell", is_entry=False, copy_usd=2.0),
        data_exec=de, logger_agent=lg, channel=_Chan(), base_payload={},
    )
    kinds = lg.kinds()
    assert "kalshi_copy_exit_residual" in kinds
    payload = dict(lg.events[0][1])
    assert payload["residual_usd"] == pytest.approx(2.0)
