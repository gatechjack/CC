"""DIAGNOSTIC (read-only investigation): are the 2 test_pead_offhours_single_outcome
failures a stale TEST FIXTURE or a real CODE regression, and does the exactly-one-exit
property still hold on the CURRENT deployed pead_strategy.py (28eb62be)?

Runs against the UNMODIFIED deployed code (no snapshot guard, no edits). The ONLY change
vs the box test is the fake broker's quote() signature:
  box fixture:  async def quote(self, symbol)                # lacks 'strict'
  deployed call: await broker.quote(symbol, strict=True)     # Part-3 rename-defense
-> TypeError, swallowed by manage()'s `except Exception: continue`, so exit-eval is skipped.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from trading_corp.persistence import db
from trading_corp.persistence.db import init_db
from trading_corp.agents.strategies.pead_strategy import PEADStrategy, _Bar

SLUG = PEADStrategy.SLUG


class _Logger:
    def __init__(self):
        self.events: list = []

    def log_event(self, actor, kind, payload):
        self.events.append((actor, kind, payload))
        return len(self.events)

    def log_proposed_order(self, order):
        pass

    def kinds(self):
        return [k for _, k, _ in self.events]


class BrokerOld:
    """The box fixture verbatim: quote() has NO 'strict' -> incompatible with the
    Part-3 deployed call broker.quote(symbol, strict=True)."""
    def __init__(self, price):
        self.price = price

    async def snapshot(self):
        return SimpleNamespace(equity=5000.0)

    async def quote(self, symbol):
        return self.price


class BrokerNew:
    """Corrected fixture: quote accepts strict (matches the deployed manage() call)."""
    def __init__(self, price):
        self.price = price

    async def snapshot(self):
        return SimpleNamespace(equity=5000.0)

    async def quote(self, symbol, *, strict=False):
        return self.price


_EXTRA = {"entry_atr_14": 2.0, "post_earnings_swing_low": 90.0,
          "pre_earnings_close": 95.0, "earnings_gap_top": 105.0}


def _mk(url, logger):
    s = PEADStrategy(db_url=url, risk_agent=None, data_exec=None, logger_agent=logger,
                     earnings_provider=object(), strategies_yaml=Path("does-not-exist.yaml"),
                     execution_mode="paper")
    s._risk_ok = lambda order, equity: True
    return s


def _insert(url, *, order_id, symbol, opened_days_ago, extra):
    opened = (datetime.now(timezone.utc) - timedelta(days=opened_days_ago)).date().isoformat()
    db.insert_paper_trade_record({
        "order_id": order_id, "ts": opened, "strategy": SLUG, "division": SLUG,
        "symbol": symbol, "side": "buy", "qty": 1.0, "entry_reference_price": 100.0,
        "result": None, "extra_json": json.dumps(extra), "execution_mode": "paper",
    }, db_url=url)


def _row(url, oid):
    with db.connect(url) as c:
        r = c.execute("SELECT result, json_extract(extra_json,'$.drift_last_daily') AS marker "
                      "FROM paper_trade_record WHERE order_id=?", (oid,)).fetchone()
    return r["result"], r["marker"]


def _boom(*a, **k):
    raise AssertionError("_fetch_daily_bars must not be called")


def _run(strat, broker):
    return asyncio.run(strat.manage(broker))


# ── ROOT CAUSE: the stale fixture's quote() cannot accept strict=True ──────────
def test_rootcause_is_the_strict_kwarg(tmp_path):
    assert "strict" not in inspect.signature(BrokerOld.quote).parameters
    with pytest.raises(TypeError):
        asyncio.run(BrokerOld(100.0).quote("X", strict=True))
    # ...and that TypeError is swallowed -> manage() skips exit-eval -> NO deferred event
    url = f"sqlite:///{tmp_path / 't.db'}"
    init_db(url)
    lg = _Logger()
    s = _mk(url, lg)
    today = datetime.now(timezone.utc).date()
    _insert(url, order_id="o1", symbol="NWSA", opened_days_ago=200,
            extra=dict(_EXTRA, drift_last_daily=PEADStrategy._prev_weekday(today).isoformat()))
    s._fetch_daily_bars = _boom
    win = {"v": ("pre_open", False)}
    s._exit_window_state = lambda now, cfg, _w=win: _w["v"]
    exits, _ = _run(s, BrokerOld(100.0))
    assert exits == [] and lg.kinds() == []            # REPRODUCES the red (feature never reached)


# ── PROPERTY 1 (corrected broker): deferred is NOT terminal; exactly ONE exit ──
def test_property_deferred_not_terminal_exactly_one_exit(tmp_path):
    url = f"sqlite:///{tmp_path / 't.db'}"
    init_db(url)
    lg = _Logger()
    s = _mk(url, lg)
    today = datetime.now(timezone.utc).date()
    _insert(url, order_id="o1", symbol="NWSA", opened_days_ago=200,
            extra=dict(_EXTRA, drift_last_daily=PEADStrategy._prev_weekday(today).isoformat()))
    s._fetch_daily_bars = _boom
    win = {"v": ("pre_open", False)}
    s._exit_window_state = lambda now, cfg, _w=win: _w["v"]
    b = BrokerNew(100.0)

    exits, _ = _run(s, b)
    assert exits == [] and lg.kinds() == ["pead_exit_deferred"]
    assert lg.events[0][2]["rule"] == "time" and lg.events[0][2]["reason"] == "pre_open"
    assert _row(url, "o1")[0] is None

    exits, _ = _run(s, b)                               # 2nd pre-open tick: still deferred, still open
    assert exits == [] and lg.kinds() == ["pead_exit_deferred", "pead_exit_deferred"]
    assert _row(url, "o1")[0] is None

    win["v"] = ("session", True)
    exits, _ = _run(s, b)                               # open: places EXACTLY ONE real sell
    assert len(exits) == 1
    assert lg.kinds().count("pead_exit") == 1
    assert lg.kinds().count("pead_exit_deferred") == 2
    assert _row(url, "o1")[0] in ("win", "loss")

    exits, _ = _run(s, b)                               # closed -> no double-fire, no half-state
    assert exits == [] and lg.kinds().count("pead_exit") == 1


# ── PROPERTY 2 (corrected broker): drift marker not consumed pre-open; fires ONCE ─
def test_property_drift_not_consumed_pre_open_fires_once(tmp_path):
    url = f"sqlite:///{tmp_path / 't.db'}"
    init_db(url)
    lg = _Logger()
    s = _mk(url, lg)
    today = datetime.now(timezone.utc).date()
    yday = today - timedelta(days=1)
    _insert(url, order_id="o2", symbol="CENX", opened_days_ago=10, extra=dict(_EXTRA))
    calls = {"n": 0}

    def bars(sym, *a, **k):
        calls["n"] += 1
        return [_Bar(yday, 100.0, 101.0, 98.0, 99.0, 1000)]
    s._fetch_daily_bars = bars
    win = {"v": ("pre_open", False)}
    s._exit_window_state = lambda now, cfg, _w=win: _w["v"]
    b = BrokerNew(100.0)

    exits, _ = _run(s, b)                               # pre-open: drift NOT evaluated
    assert exits == [] and calls["n"] == 0
    assert _row(url, "o2") == (None, None) and lg.events == []

    win["v"] = ("session", True)
    exits, _ = _run(s, b)                               # open: drift fires exactly once
    assert len(exits) == 1 and calls["n"] == 1
    assert lg.kinds().count("pead_exit") == 1 and lg.events[-1][2]["rule"] == "drift"
    assert _row(url, "o2")[0] in ("win", "loss")
    assert _row(url, "o2")[1] == yday.isoformat()       # marker advanced exactly once

    exits, _ = _run(s, b)                               # closed -> no re-fire, no second fetch
    assert exits == [] and calls["n"] == 1 and lg.kinds().count("pead_exit") == 1
