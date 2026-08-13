"""PART 1 correctness proof for the off-hours exit gate: a pre-open DEFERRAL is
NOT a terminal exit, and a position resolves to EXACTLY ONE exit outcome.

Drives the real PEADStrategy.manage() against a temp sqlite ledger in PAPER mode
(so _place_or_paper records without a live broker) across the window states:
  pre_open -> pre_open -> session -> session
and asserts on the DB `result` column + the captured audit events.

Window CLASSIFICATION (clock->state) is covered by test_pead_offhours_gate.py;
here we inject the state to prove the OUTCOME per state. _risk_ok is stubbed True
(the risk gate is unchanged and orthogonal to the single-outcome property).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from trading_corp.persistence import db
from trading_corp.persistence.db import init_db
from trading_corp.agents.strategies.pead_strategy import PEADStrategy, _Bar

SLUG = PEADStrategy.SLUG


class _FakeLogger:
    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    def log_event(self, actor, kind, payload):
        self.events.append((actor, kind, payload))
        return len(self.events)

    def log_proposed_order(self, order):   # only hit on risk reject (not here)
        pass

    def kinds(self) -> list[str]:
        return [k for _, k, _ in self.events]


class _FakeBroker:
    def __init__(self, price: float):
        self.price = price

    async def snapshot(self):
        return SimpleNamespace(equity=5000.0)

    async def quote(self, symbol):
        return self.price


def _mk_strat(url, logger):
    strat = PEADStrategy(
        db_url=url, risk_agent=None, data_exec=None, logger_agent=logger,
        earnings_provider=object(), strategies_yaml=Path("does-not-exist.yaml"),
        execution_mode="paper",
    )
    strat._risk_ok = lambda order, equity: True    # risk gate is orthogonal + unchanged
    return strat


def _insert_open_row(url, *, order_id, symbol, opened_days_ago, extra):
    opened = (datetime.now(timezone.utc) - timedelta(days=opened_days_ago)).date().isoformat()
    db.insert_paper_trade_record({
        "order_id": order_id, "ts": opened, "strategy": SLUG, "division": SLUG,
        "symbol": symbol, "side": "buy", "qty": 1.0, "entry_reference_price": 100.0,
        "result": None, "extra_json": json.dumps(extra), "execution_mode": "paper",
    }, db_url=url)


def _row(url, order_id):
    with db.connect(url) as conn:
        r = conn.execute(
            "SELECT result, json_extract(extra_json,'$.drift_last_daily') AS marker "
            "FROM paper_trade_record WHERE order_id=?", (order_id,)).fetchone()
    return r["result"], r["marker"]


# Primitives: entry 100, ATR 2 -> stop_level 95; gap_top 105, pre_close 95 -> gap 10,
# drift_dead_level 100. Quote 100 => stop pressure 0 (never fires on these tests).
_EXTRA = {"entry_atr_14": 2.0, "post_earnings_swing_low": 90.0,
          "pre_earnings_close": 95.0, "earnings_gap_top": 105.0}


def _run(strat, broker):
    return asyncio.run(strat.manage(broker))


# ── time-rule name: defers pre-open (x2), places ONCE at the open ──────────────
def test_deferred_is_not_terminal_then_exactly_one_exit_at_open(tmp_path):
    url = f"sqlite:///{tmp_path / 't.db'}"
    init_db(url)
    logger = _FakeLogger()
    strat = _mk_strat(url, logger)

    today = datetime.now(timezone.utc).date()
    extra = dict(_EXTRA, drift_last_daily=PEADStrategy._prev_weekday(today).isoformat())
    _insert_open_row(url, order_id="o1", symbol="NWSA", opened_days_ago=200, extra=extra)

    # drift must NOT be fetched in this test (marker == prev weekday skips it); prove it.
    def _boom(*a, **k):
        raise AssertionError("_fetch_daily_bars must not be called (marker guard)")
    strat._fetch_daily_bars = _boom

    win = {"v": ("pre_open", False)}
    strat._exit_window_state = lambda now, cfg, _w=win: _w["v"]
    broker = _FakeBroker(price=100.0)   # >= stop_level 95 -> stop never fires; TIME fires (held>>60)

    # tick 1 — pre-open: defer, no terminal
    exits, _ = _run(strat, broker)
    assert exits == []
    assert logger.kinds() == ["pead_exit_deferred"]
    assert logger.events[0][2]["rule"] == "time" and logger.events[0][2]["reason"] == "pre_open"
    assert _row(url, "o1")[0] is None                  # result still NULL — NOT closed

    # tick 2 — still pre-open: another (harmless) deferred row, still no terminal
    exits, _ = _run(strat, broker)
    assert exits == []
    assert logger.kinds() == ["pead_exit_deferred", "pead_exit_deferred"]
    assert _row(url, "o1")[0] is None

    # tick 3 — session: places the REAL sell -> exactly one terminal exit
    win["v"] = ("session", True)
    exits, _ = _run(strat, broker)
    assert len(exits) == 1
    assert logger.kinds().count("pead_exit") == 1      # ONE real exit event
    assert logger.kinds().count("pead_exit_deferred") == 2   # deferred count unchanged
    assert _row(url, "o1")[0] in ("win", "loss")       # row now closed

    # tick 4 — session again: row is closed -> book empty -> no second exit
    exits, _ = _run(strat, broker)
    assert exits == []
    assert logger.kinds().count("pead_exit") == 1      # STILL exactly one — no double-count


# ── drift name: marker NOT consumed pre-open; fires exactly ONCE at the open ───
def test_drift_marker_not_consumed_pre_open_fires_once_at_open(tmp_path):
    url = f"sqlite:///{tmp_path / 't.db'}"
    init_db(url)
    logger = _FakeLogger()
    strat = _mk_strat(url, logger)

    today = datetime.now(timezone.utc).date()
    # no drift_last_daily marker -> the daily-bar fetch is eligible when placement is allowed.
    _insert_open_row(url, order_id="o2", symbol="CENX", opened_days_ago=10, extra=dict(_EXTRA))

    calls = {"n": 0}
    yday = today - timedelta(days=1)

    def _fake_bars(symbol, *a, **k):
        calls["n"] += 1
        # one completed post-entry bar whose close crosses drift_dead_level (100)
        return [_Bar(yday, 100.0, 101.0, 98.0, 99.0, 1_000)]
    strat._fetch_daily_bars = _fake_bars

    win = {"v": ("pre_open", False)}
    strat._exit_window_state = lambda now, cfg, _w=win: _w["v"]
    broker = _FakeBroker(price=100.0)   # stop never fires; held<60 so TIME never fires; only DRIFT can

    # tick 1 — pre-open: drift is NOT evaluated -> no fetch, no marker write, no exit
    exits, _ = _run(strat, broker)
    assert exits == []
    assert calls["n"] == 0                              # marker/fetch NOT consumed pre-open
    result, marker = _row(url, "o2")
    assert result is None and marker is None
    assert logger.events == []                          # pure-drift name: nothing pre-open

    # tick 2 — session: drift evaluated once -> fetch once, marker set, ONE exit
    win["v"] = ("session", True)
    exits, _ = _run(strat, broker)
    assert len(exits) == 1
    assert calls["n"] == 1
    assert logger.kinds().count("pead_exit") == 1
    assert logger.events[-1][2]["rule"] == "drift"
    result, marker = _row(url, "o2")
    assert result in ("win", "loss")
    assert marker == yday.isoformat()                   # marker advanced exactly once

    # tick 3 — session again: row closed -> no re-fire, no second fetch
    exits, _ = _run(strat, broker)
    assert exits == []
    assert calls["n"] == 1
    assert logger.kinds().count("pead_exit") == 1       # drift fired exactly once, not twice
