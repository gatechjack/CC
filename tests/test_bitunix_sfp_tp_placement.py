"""Post-fill TP-leg placement for the live SFP division (the fix for the verified
stop-out-only blocker: place_order rests only the B1 stop, so a real /tpsl/
reduce-only TP leg must be placed after the entry fills).

Mocked against the PROD broker surface: ``place_tpsl_order`` (native /tpsl/),
``get_pending_positions`` (positionId resolution), and ``data_exec.safety_notifier``
(loud telegram). The worktree branch is OLDER than PROD on the broker surface
(no ``place_tpsl_order``, no ``BitunixUntrackedTpslOrder``); ``bitunix_bracket.py``
is synced from PROD (md5-identical) and the exception is injected here so the
observer's lazy imports resolve — exactly the PROD types, no behaviour faked.

Covers: TP placed full-qty at take_profit_price + extra_json records the id;
positionId-unresolved → SL-only + loud audit/telegram; place_tpsl_order raise →
fail-soft + alert + entry intact; min-leg-too-small → SL-only + alert; idempotent
"" → no double-count; untracked → flagged LOUD; and _place wiring (entry intact
on TP failure)."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

# ── Inject the PROD exception type into the (stale-worktree) module so the
#    observer's lazy `from ...bitunix_exceptions import BitunixUntrackedTpslOrder`
#    resolves. No-op on PROD (the real class is already present). ──────────────
import trading_corp.brokers.bitunix_exceptions as _exc
if not hasattr(_exc, "BitunixUntrackedTpslOrder"):
    class BitunixUntrackedTpslOrder(RuntimeError):
        def __init__(self, *, position_id=None, symbol=None, tp_price=None,
                     tp_qty=None, raw_response=None):
            super().__init__("untracked tpsl leg")
            self.position_id = position_id
            self.symbol = symbol
            self.tp_price = tp_price
            self.tp_qty = tp_qty
            self.raw_response = raw_response
    _exc.BitunixUntrackedTpslOrder = BitunixUntrackedTpslOrder
BitunixUntrackedTpslOrder = _exc.BitunixUntrackedTpslOrder

from trading_corp.persistence import db
from trading_corp.agents.divisions.bitunix_sfp_observer import (
    BitunixSfpConfig,
    BitunixSfpObserver,
    DIVISION,
)


# ── Fakes (extend the observer-test fakes with the PROD tpsl surface) ──────────
class FakeNotifier:
    def __init__(self):
        self.pushes = []

    async def push(self, text, *, audit_path="other", audit_context=None):
        self.pushes.append({"text": text, "audit_path": audit_path,
                            "audit_context": audit_context})
        return True


class FakeBroker:
    def __init__(self, *, positions=None, tpsl_result="tp-venue-1", tpsl_raises=None):
        self.paper = False
        self._positions = positions if positions is not None else [
            SimpleNamespace(symbol="BTCUSDT", qty=0.01,
                            extra={"positionId": "pos-123", "side": "LONG"})
        ]
        self._tpsl_result = tpsl_result
        self._tpsl_raises = tpsl_raises
        self.tpsl_calls = []

    async def snapshot(self):
        return SimpleNamespace(equity=1000.0)

    async def get_pending_positions(self):
        return list(self._positions)

    async def place_tpsl_order(self, *, symbol, position_id, tp_price, tp_qty,
                               tp_stop_type="MARK_PRICE", tp_order_type="LIMIT"):
        self.tpsl_calls.append({"symbol": symbol, "position_id": position_id,
                                "tp_price": tp_price, "tp_qty": tp_qty})
        if self._tpsl_raises is not None:
            raise self._tpsl_raises
        return self._tpsl_result


class FakeDataExec:
    def __init__(self, broker, notifier=None):
        self.brokers = {DIVISION: broker}
        self.safety_notifier = notifier


class FakeLogger:
    def __init__(self):
        self.events = []

    def log_event(self, *, actor, kind, payload):
        self.events.append((kind, payload))
        return 1

    def log_proposed_order(self, order):
        pass

    def kinds(self):
        return [k for k, _ in self.events]


def _mk(tmp_path, *, broker=None, notifier=None):
    db_url = f"sqlite:///{tmp_path.as_posix()}/t.db"
    db.init_db(db_url)
    broker = broker or FakeBroker()
    notifier = notifier or FakeNotifier()
    cfg = BitunixSfpConfig(
        enabled=True, auto_execute=True, execution_mode="live",
        symbols=("BTC/USDT.P",), risk_pct_real=0.005,
        risk_pct_considerable=0.005, leverage=2.0,
    )
    obs = BitunixSfpObserver(
        db_url=db_url, risk_agent=SimpleNamespace(), data_exec=FakeDataExec(broker, notifier),
        logger_agent=FakeLogger(), config=cfg, bar_caches={},
    )
    obs._yaml_auto_execute = lambda: True
    return obs, broker, notifier, db_url


def _order(order_id="ord-1", qty=0.01, tp=102.2):
    return SimpleNamespace(id=order_id, symbol="BTC/USDT.P", side="buy", qty=qty,
                           extra={"take_profit_price": tp, "stop_price": 98.9})


def _seed_entry_row(db_url, order):
    """Insert the entry row the observer would have written at fill time, so the
    inline _persist_tp UPDATE has a row to amend."""
    rec = {
        "order_id": order.id, "ts": "2026-06-26T00:00:00+00:00", "strategy": DIVISION,
        "division": DIVISION, "symbol": order.symbol, "side": order.side, "qty": order.qty,
        "tier": None, "source_signal": "sfp_real", "entry_reference_price": 100.0,
        "stop_price": 98.9, "tp_price": order.extra["take_profit_price"], "tp_r_multiple": 2.0,
        "expected_loss": None, "expected_gain": None, "rr_ratio": None,
        "max_hold_seconds": 604800, "result": None, "result_ts": None,
        "result_price": None, "actual_pnl_dollars": None, "actual_r_multiple": None,
        "bars_to_resolution": None,
        "extra_json": json.dumps({"execution_mode": "live", "broker_order_id": "fill-1",
                                  "take_profit_price": order.extra["take_profit_price"]}),
        "execution_mode": "live",
    }
    db.insert_paper_trade_record(rec, db_url=db_url)


def _extra(db_url, order_id):
    with db.connect(db_url) as conn:
        row = conn.execute("SELECT extra_json FROM paper_trade_record WHERE order_id=?",
                           (order_id,)).fetchone()
    return json.loads(row["extra_json"]) if row and row["extra_json"] else {}


# --------------------------------------------------------------------------- #
def test_tp_leg_placed_full_qty_and_recorded(tmp_path):
    obs, broker, notifier, db_url = _mk(tmp_path)
    order = _order(qty=0.01, tp=102.2)
    _seed_entry_row(db_url, order)
    asyncio.run(obs._place_tp_leg(order, "BTC/USDT.P"))
    # ONE leg, FULL qty, at take_profit_price, position-tied
    assert len(broker.tpsl_calls) == 1
    call = broker.tpsl_calls[0]
    assert call["symbol"] == "BTC/USDT.P" and call["position_id"] == "pos-123"
    assert abs(call["tp_price"] - 102.2) < 1e-9 and abs(call["tp_qty"] - 0.01) < 1e-9
    # extra_json records the venue id + bracket state; broker_order_id preserved
    e = _extra(db_url, order.id)
    assert e["bracket_tp_order_id"] == "tp-venue-1"
    assert e["bracket_position_id"] == "pos-123"
    assert abs(e["bracket_tp_qty"] - 0.01) < 1e-9 and abs(e["bracket_tp_price"] - 102.2) < 1e-9
    assert e["broker_order_id"] == "fill-1"
    assert "sfp_bracket_placed" in obs.logger_agent.kinds()
    assert notifier.pushes == []   # success → no alert


def test_positionid_unresolved_sl_only_loud(tmp_path):
    obs, broker, notifier, db_url = _mk(tmp_path, broker=FakeBroker(positions=[]))
    order = _order()
    _seed_entry_row(db_url, order)
    asyncio.run(obs._place_tp_leg(order, "BTC/USDT.P"))
    assert broker.tpsl_calls == []                       # no TP attempted
    assert "sfp_tp_unresolved_position" in obs.logger_agent.kinds()
    assert len(notifier.pushes) == 1                     # LOUD telegram
    e = _extra(db_url, order.id)
    assert "bracket_tp_order_id" not in e                # entry row untouched (SL-only)
    assert e["broker_order_id"] == "fill-1"              # entry intact


def test_place_tpsl_raise_fail_soft_entry_intact(tmp_path):
    broker = FakeBroker(tpsl_raises=RuntimeError("venue 500"))
    obs, broker, notifier, db_url = _mk(tmp_path, broker=broker)
    order = _order()
    _seed_entry_row(db_url, order)
    # Must NOT raise (fail-soft) — the filled entry stands.
    asyncio.run(obs._place_tp_leg(order, "BTC/USDT.P"))
    assert len(broker.tpsl_calls) == 1                   # attempted once
    assert "sfp_tp_place_failed" in obs.logger_agent.kinds()
    assert len(notifier.pushes) == 1
    e = _extra(db_url, order.id)
    assert "bracket_tp_order_id" not in e and e["broker_order_id"] == "fill-1"


def test_untracked_tpsl_flagged_loud(tmp_path):
    exc = BitunixUntrackedTpslOrder(position_id="pos-123", symbol="BTCUSDT",
                                    tp_price=102.2, tp_qty=0.01, raw_response="[]")
    obs, broker, notifier, db_url = _mk(tmp_path, broker=FakeBroker(tpsl_raises=exc))
    order = _order()
    _seed_entry_row(db_url, order)
    asyncio.run(obs._place_tp_leg(order, "BTC/USDT.P"))
    assert "sfp_tp_untracked" in obs.logger_agent.kinds()  # never swallowed
    assert len(notifier.pushes) == 1
    e = _extra(db_url, order.id)
    assert "bracket_tp_order_id" not in e and e["broker_order_id"] == "fill-1"


def test_min_leg_too_small_sl_only(tmp_path):
    # The VENUE position qty (not the requested qty) drives the leg; make it sub-min.
    broker = FakeBroker(positions=[SimpleNamespace(
        symbol="BTCUSDT", qty=0.00005, extra={"positionId": "pos-123", "side": "LONG"})])
    obs, broker, notifier, db_url = _mk(tmp_path, broker=broker)
    order = _order(qty=0.00005)                           # < 0.0001 BTC min leg (Board 2026-07-06)
    _seed_entry_row(db_url, order)
    asyncio.run(obs._place_tp_leg(order, "BTC/USDT.P"))
    assert broker.tpsl_calls == []                       # no sub-min leg placed
    assert "sfp_tp_skipped_submin" in obs.logger_agent.kinds()
    assert len(notifier.pushes) == 1
    assert "bracket_tp_order_id" not in _extra(db_url, order.id)


def test_idempotent_empty_id_no_double_count(tmp_path):
    obs, broker, notifier, db_url = _mk(tmp_path, broker=FakeBroker(tpsl_result=""))
    order = _order()
    _seed_entry_row(db_url, order)
    asyncio.run(obs._place_tp_leg(order, "BTC/USDT.P"))
    assert len(broker.tpsl_calls) == 1                   # called ONCE, no retry loop
    # persisted with the empty id (not lost); sfp_bracket_placed emitted
    e = _extra(db_url, order.id)
    assert e["bracket_tp_order_id"] == "" and e["bracket_position_id"] == "pos-123"
    assert "sfp_bracket_placed" in obs.logger_agent.kinds()
    assert notifier.pushes == []                         # idempotent dup is not a failure


def test_unsupported_broker_sl_only(tmp_path):
    # Broker lacking place_tpsl_order (e.g. paper) → SL-only + loud, never crashes.
    class NoTpsl:
        paper = False
    obs, _b, notifier, db_url = _mk(tmp_path, broker=NoTpsl())
    order = _order()
    _seed_entry_row(db_url, order)
    asyncio.run(obs._place_tp_leg(order, "BTC/USDT.P"))
    assert "sfp_tp_unsupported" in obs.logger_agent.kinds()
    assert len(notifier.pushes) == 1
