"""Observer tests: ProposedOrder/extra_json, per-mode sizing, mandatory risk
gate, paper vs live fork, the per-(symbol,side) concurrent-position guard, the
sequential multi-symbol walk, and warm-start. Uses a real temp sqlite DB
(``db.init_db``) and lightweight fakes for risk/data_exec/broker/logger."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from trading_corp.persistence import db
from trading_corp.agents.divisions.bitunix_sfp_observer import (
    BitunixSfpConfig,
    BitunixSfpObserver,
    DIVISION,
)
from trading_corp.agents.strategies.bitunix_sfp import (
    MODE_CONSIDERABLE,
    MODE_REAL,
    SfpBar,
    SfpEntrySignal,
)


class FakeLogger:
    def __init__(self):
        self.events = []
        self.orders = []

    def log_event(self, *, actor, kind, payload):
        self.events.append((kind, payload))
        return 1

    def log_proposed_order(self, order):
        self.orders.append(order)

    def kinds(self):
        return [k for k, _ in self.events]


class FakeBroker:
    def __init__(self, equity=1000.0, raise_snap=False):
        self.equity = equity
        self.raise_snap = raise_snap
        self.paper = False

    async def snapshot(self):
        if self.raise_snap:
            raise RuntimeError("snapshot failed")
        return SimpleNamespace(equity=self.equity)


class FakeDataExec:
    def __init__(self, broker):
        self.brokers = {DIVISION: broker}
        self.placed = []

    async def place(self, order, division="default"):
        self.placed.append((order, division))
        return SimpleNamespace(
            order_id="fill-1", price=order.extra.get("entry_reference_price"), fee=0.0
        )


class FakeRisk:
    def __init__(self, verdict="approve", new_qty=None, flatten=False, reason=None):
        self.calls = 0
        self._v = verdict
        self._nq = new_qty
        self._flat = flatten
        self._reason = reason

    def evaluate(self, order, account, strat_state, *a, **kw):
        self.calls += 1
        return SimpleNamespace(
            verdict=self._v, new_qty=self._nq, flatten_account=self._flat, reason=self._reason
        )


def _mk(tmp_path, *, execution_mode="live", risk=None, broker=None,
        symbols=("BTC/USDT.P",), risk_pct_real=0.005, risk_pct_considerable=0.005):
    db_url = f"sqlite:///{tmp_path.as_posix()}/t.db"
    db.init_db(db_url)
    logger = FakeLogger()
    broker = broker or FakeBroker()
    de = FakeDataExec(broker)
    risk = risk or FakeRisk()
    cfg = BitunixSfpConfig(
        enabled=True, auto_execute=True, execution_mode=execution_mode,
        symbols=symbols, risk_pct_real=risk_pct_real,
        risk_pct_considerable=risk_pct_considerable, leverage=5.0,
    )
    obs = BitunixSfpObserver(
        db_url=db_url, risk_agent=risk, data_exec=de, logger_agent=logger,
        config=cfg, bar_caches={},
    )
    obs._yaml_auto_execute = lambda: True  # bypass strategies.yaml read in tests
    obs._yaml_side = lambda: "regime"      # bidirectional (side-gate else reads real yaml)
    # Seed the engine-native regime buffer -> 'up' (rising ramp) so the long tests
    # exercise real regime-gated fires (long allowed in up/range), not warmup-skips.
    for _w in obs._symbol_bos_tf:
        obs._regime_closes[_w] = [100.0 + i * 0.1 for i in range(801)]
    return obs, de, risk, logger, db_url


def _sig(mode=MODE_REAL, swept_low=99.0):
    return SfpEntrySignal(
        sfp_mode=mode, swept_low=swept_low, swept_swing_level=98.0,
        bos_ref_high=101.0, fire_bar_index=10, bos_bar_index=20,
        entry_bar_index=21, bos_bar_ts_ms=1_700_000_900_000,
    )


def _bar(close=100.0):
    return SfpBar(ts_ms=1_700_000_900_000, open=close, high=close + 1, low=close - 1, close=close)


def _rows(db_url):
    with db.connect(db_url) as conn:
        return conn.execute(
            "SELECT symbol, side, qty, stop_price, tp_price, tp_r_multiple, "
            "execution_mode, extra_json FROM paper_trade_record WHERE division = ?",
            (DIVISION,),
        ).fetchall()


# --------------------------------------------------------------------------- #
def test_proposed_order_fields_and_extra(tmp_path):
    obs, de, risk, logger, db_url = _mk(tmp_path)
    asyncio.run(obs._handle_signal("BTC/USDT.P", "BTCUSDT", _sig(), _bar(100.0)))
    assert len(de.placed) == 1
    order, division = de.placed[0]
    assert division == DIVISION
    assert order.strategy == DIVISION and order.symbol == "BTC/USDT.P"
    assert order.side == "buy" and order.order_type == "market"
    e = order.extra
    assert e["sfp_mode"] == MODE_REAL
    assert abs(e["stop_price"] - 98.9) < 1e-9          # 99 - 0.001*100
    assert abs(e["take_profit_price"] - 102.2) < 1e-9  # 100 + 2*(100-98.9)
    assert e["tp_r_multiple"] == 2.0
    assert e["swept_low"] == 99.0 and e["swept_swing_level"] == 98.0
    assert e["bos_ref_high"] == 101.0 and e["entry_reference_price"] == 100.0
    assert e["source_signal"] == "sfp_real" and e["reduce_only"] is False
    # Path-C live row written with the mode queryable via json_extract.
    rows = _rows(db_url)
    assert len(rows) == 1
    extra = json.loads(rows[0]["extra_json"])
    assert extra["sfp_mode"] == MODE_REAL and extra["execution_mode"] == "live"
    assert rows[0]["execution_mode"] == "live"
    with db.connect(db_url) as conn:
        n = conn.execute(
            "SELECT COUNT(*) c FROM paper_trade_record "
            "WHERE json_extract(extra_json,'$.sfp_mode') = 'REAL'"
        ).fetchone()["c"]
    assert n == 1


def test_per_mode_sizing_independent(tmp_path):
    # REAL 0.5%, CONS 1.0% → CONS qty == 2x REAL qty (same stop distance).
    # Separate DBs so the concurrent-position guard (which correctly blocks a
    # 2nd same-symbol+side entry) doesn't suppress the second placement.
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    obs_r, de_r, *_ = _mk(tmp_path / "a", risk_pct_real=0.005)
    obs_c, de_c, *_ = _mk(tmp_path / "b", risk_pct_considerable=0.010)
    asyncio.run(obs_r._handle_signal("BTC/USDT.P", "BTCUSDT", _sig(MODE_REAL), _bar(100.0)))
    asyncio.run(obs_c._handle_signal("BTC/USDT.P", "BTCUSDT", _sig(MODE_CONSIDERABLE), _bar(100.0)))
    qty_real = de_r.placed[0][0].qty
    qty_cons = de_c.placed[0][0].qty
    assert abs(qty_real - (1000 * 0.005 / 1.1)) < 1e-6
    assert abs(qty_cons - 2 * qty_real) < 1e-6


def test_risk_gate_always_called_and_reject_blocks(tmp_path):
    obs, de, risk, logger, _ = _mk(tmp_path, risk=FakeRisk(verdict="reject", reason="cap"))
    asyncio.run(obs._handle_signal("BTC/USDT.P", "BTCUSDT", _sig(), _bar(100.0)))
    assert risk.calls == 1            # the chokepoint was consulted
    assert de.placed == []            # reject → no placement
    assert "sfp_risk_rejected" in logger.kinds()


def test_resize_applies_new_qty(tmp_path):
    obs, de, risk, logger, _ = _mk(tmp_path, risk=FakeRisk(verdict="resize", new_qty=0.123))
    asyncio.run(obs._handle_signal("BTC/USDT.P", "BTCUSDT", _sig(), _bar(100.0)))
    assert len(de.placed) == 1 and de.placed[0][0].qty == 0.123


def test_drawdown_flatten_blocks(tmp_path):
    obs, de, risk, logger, _ = _mk(tmp_path, risk=FakeRisk(verdict="reject", flatten=True, reason="dd"))
    asyncio.run(obs._handle_signal("BTC/USDT.P", "BTCUSDT", _sig(), _bar(100.0)))
    assert de.placed == []
    assert "sfp_drawdown_breach_block" in logger.kinds()


def test_paper_fork_never_places(tmp_path):
    obs, de, risk, logger, db_url = _mk(tmp_path, execution_mode="paper")
    asyncio.run(obs._handle_signal("BTC/USDT.P", "BTCUSDT", _sig(), _bar(100.0)))
    assert de.placed == []                       # paper NEVER touches the broker
    assert "would_have_placed" in logger.kinds()
    rows = _rows(db_url)
    assert len(rows) == 1
    assert rows[0]["execution_mode"] == "paper"  # resolved 'paper' (no live tag)
    extra = json.loads(rows[0]["extra_json"])
    assert extra.get("execution_mode") != "live"


def test_live_fork_places_and_tags_path_c(tmp_path):
    obs, de, risk, logger, db_url = _mk(tmp_path, execution_mode="live")
    asyncio.run(obs._handle_signal("BTC/USDT.P", "BTCUSDT", _sig(), _bar(100.0)))
    assert len(de.placed) == 1
    rows = _rows(db_url)
    extra = json.loads(rows[0]["extra_json"])
    assert extra["execution_mode"] == "live" and extra["broker_order_id"] == "fill-1"


def test_concurrent_position_guard_blocks_same_side(tmp_path):
    obs, de, risk, logger, db_url = _mk(tmp_path)
    # pre-existing OPEN live SFP position on the same (symbol, side)
    rec = {
        "order_id": "pre-1", "ts": "2026-06-25T00:00:00+00:00", "strategy": DIVISION,
        "division": DIVISION, "symbol": "BTC/USDT.P", "side": "buy", "qty": 0.01,
        "tier": None, "source_signal": "sfp_real", "entry_reference_price": 100.0,
        "stop_price": 98.9, "tp_price": 102.2, "tp_r_multiple": 2.0,
        "expected_loss": None, "expected_gain": None, "rr_ratio": None,
        "max_hold_seconds": 604800, "result": None, "result_ts": None,
        "result_price": None, "actual_pnl_dollars": None, "actual_r_multiple": None,
        "bars_to_resolution": None, "extra_json": json.dumps({"execution_mode": "live"}),
        "execution_mode": "live",
    }
    db.insert_paper_trade_record(rec, db_url=db_url)
    asyncio.run(obs._handle_signal("BTC/USDT.P", "BTCUSDT", _sig(), _bar(100.0)))
    assert de.placed == []
    assert "sfp_concurrent_position_blocked" in logger.kinds()


def test_invalid_geometry_skips(tmp_path):
    obs, de, risk, logger, _ = _mk(tmp_path)
    # swept_low above entry → R <= 0 → SKIP, risk gate never reached
    asyncio.run(obs._handle_signal("BTC/USDT.P", "BTCUSDT", _sig(swept_low=101.0), _bar(100.0)))
    assert de.placed == [] and risk.calls == 0
    assert "sfp_skip_invalid_geometry" in logger.kinds()


def test_no_phantom_equity_on_snapshot_fail(tmp_path):
    obs, de, risk, logger, _ = _mk(tmp_path, broker=FakeBroker(raise_snap=True))
    asyncio.run(obs._handle_signal("BTC/USDT.P", "BTCUSDT", _sig(), _bar(100.0)))
    assert de.placed == [] and risk.calls == 0
    assert "sfp_skip_no_equity" in logger.kinds()


def test_multi_symbol_loop_walks_list(tmp_path):
    obs, de, risk, logger, _ = _mk(tmp_path, symbols=("BTC/USDT.P", "ETH/USDT.P"))

    class FakeCache:
        def __init__(self, base_ts):
            self.bars = [
                SimpleNamespace(ts_ms=base_ts + i * 900_000, open=100.0, high=101.0,
                                low=99.0, close=100.0, volume=1.0)
                for i in range(5)
            ]

        async def refresh(self):
            return len(self.bars)

    obs.bar_caches = {"BTCUSDT": FakeCache(1_700_000_000_000),
                      "ETHUSDT": FakeCache(1_800_000_000_000)}
    asyncio.run(obs.process_once())
    # both symbols' detectors were fed → last-processed ts advanced for each
    assert obs._last_ts["BTCUSDT"] == 1_700_000_000_000 + 4 * 900_000
    assert obs._last_ts["ETHUSDT"] == 1_800_000_000_000 + 4 * 900_000


def test_warm_start_sets_last_ts(tmp_path):
    obs, de, risk, logger, _ = _mk(tmp_path)

    class FakeCache:
        def __init__(self):
            self.bars = [
                SimpleNamespace(ts_ms=1_700_000_000_000 + i * 900_000, open=100.0,
                                high=101.0, low=99.0, close=100.0, volume=1.0)
                for i in range(120)
            ]

    obs.bar_caches = {"BTCUSDT": FakeCache()}
    obs.warm_start_from_cache()
    assert obs._last_ts["BTCUSDT"] == 1_700_000_000_000 + 119 * 900_000
