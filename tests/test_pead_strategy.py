"""ADVERSARIAL verification suite for the `robinhood_pead` LIVE entry + exit engine
(`agents/strategies/pead_strategy.py`).

This is the gate before the strategy + division are committed: the engine places
REAL long-equity orders with `RiskAgent.evaluate` as the ONLY safety gate (no
HITL), so these tests are written to BREAK the engine, not to confirm it. They
pin the live-money invariants:

  #1 Each exit rule fires at the EXACT contract price the locked `pead_pressures`
     module defines — and NOT one tick before (boundary on each of stop/drift/
     guard/time).
  #2 When two rules cross 1.0 in the SAME manage tick, first-match precedence
     fires the right one, and the result-IS-NULL de-dup prevents a second sell.
  #3 Round-to-zero (the NORMAL path at ~$7/position): a name whose notional
     yields 0 whole shares is skipped and the NEXT ranked name fills.
  #4 The 6 locked extra_json keys survive a full JSON+DB round-trip and equal the
     engine's own `_build_primitives` output.
  #5 NO-HITL: the live path places through `data_exec.place`; `RiskAgent.evaluate`
     is the only gate (reject blocks placement, approve places with no other
     step); and PAPER never calls `data_exec.place` (the structural safety claim),
     gated by execution_mode==live AND yaml auto_execute.

All exits IMPORT the locked `pead_pressures` contract — the tests assert the
engine fires at the contract's own levels, so engine/dashboard/backtest agree by
construction.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import yaml

from trading_corp.agents.strategies import pead_pressures as pp
from trading_corp.agents.strategies.pead_strategy import PEADStrategy, _Bar
from trading_corp.persistence.db import connect, init_db
from trading_corp.web.pead_view import DIVISION, business_days, query_open_positions

# ── test doubles ─────────────────────────────────────────────────────────────


class FakeBroker:
    """Equity + per-symbol live quotes; quotes are mutable so a single seeded
    position can be walked across the contract boundary in one test."""

    paper = True

    def __init__(self, equity: float, quotes: dict[str, float] | None = None):
        self.equity = float(equity)
        self.quotes = dict(quotes or {})

    async def snapshot(self):
        return SimpleNamespace(equity=self.equity)

    async def quote(self, symbol):
        return self.quotes[symbol]


class FakeRisk:
    """Stand-in RiskAgent — the ONLY gate. Records every evaluate() call so the
    no-HITL test can prove the gate was consulted and that nothing else gates."""

    def __init__(self, verdict: str = "approve", *, new_qty=None, reason="ok"):
        self._verdict = verdict
        self._new_qty = new_qty
        self._reason = reason
        self.calls: list = []

    def evaluate(self, order, account, strategy_state, *a, **k):
        self.calls.append(order)
        return SimpleNamespace(verdict=self._verdict, reason=self._reason,
                               new_qty=self._new_qty)


class FakeDataExec:
    """Records place() calls. In PAPER mode the engine must NEVER reach here."""

    def __init__(self, price: float = 123.0):
        self._price = price
        self.placed: list = []

    async def place(self, order, division=None):
        self.placed.append((order, division))
        order.fill_price = self._price
        return SimpleNamespace(price=self._price)


class FakeLogger:
    def __init__(self):
        self.events: list = []
        self.proposed: list = []

    def log_event(self, slug, kind, payload):
        self.events.append((slug, kind, payload))

    def log_proposed_order(self, order):
        self.proposed.append(order)


# ── helpers ──────────────────────────────────────────────────────────────────


def _yaml(tmp_path, **over):
    block = {"auto_execute": False, "position_pct": 0.10,
             "max_concurrent_positions": 7, "manage_cadence_sec": 300}
    block.update(over)
    p = tmp_path / "strategies.yaml"
    p.write_text(yaml.safe_dump({"robinhood_pead": block}), encoding="utf-8")
    return p


def _strategy(db_url, yaml_path, *, risk, execmode="paper", data_exec=None,
              logger=None, provider=None):
    return PEADStrategy(
        db_url=db_url,
        risk_agent=risk,
        data_exec=data_exec or FakeDataExec(),
        logger_agent=logger or FakeLogger(),
        earnings_provider=provider or SimpleNamespace(),
        strategies_yaml=yaml_path,
        execution_mode=execmode,
    )


def _extra(*, atr, swing_low, pre_close, gap_top, next_earn=None, sue=3.0, name=None):
    return {
        "entry_atr_14": float(atr),
        "post_earnings_swing_low": float(swing_low),
        "pre_earnings_close": float(pre_close),
        "earnings_gap_top": float(gap_top),
        "entry_sue": float(sue),
        "next_earnings_date": next_earn.isoformat() if next_earn else None,
        "name": name,
    }


def _seed_open(url, *, order_id, symbol, qty, entry, extra, opened):
    with connect(url) as conn:
        conn.execute(
            "INSERT INTO paper_trade_record (order_id, ts, strategy, division, "
            "symbol, side, qty, entry_reference_price, extra_json, execution_mode) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (order_id, opened.isoformat(), DIVISION, DIVISION, symbol, "buy",
             float(qty), float(entry), json.dumps(extra), "paper"),
        )


def _row(url, order_id):
    with connect(url) as conn:
        return conn.execute(
            "SELECT * FROM paper_trade_record WHERE order_id=?", (order_id,)
        ).fetchone()


def _open_count(url):
    with connect(url) as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM paper_trade_record "
            "WHERE division=? AND result IS NULL", (DIVISION,),
        ).fetchone()["c"]


def _past_bdays(today: date, n: int) -> date:
    """Largest date d <= today with business_days(d, today) == n (monotone +1/step
    so it never overshoots)."""
    d = today
    while business_days(d, today) < n:
        d -= timedelta(days=1)
    return d


def _future_bdays(today: date, n: int) -> date:
    d = today
    while business_days(today, d) < n:
        d += timedelta(days=1)
    return d


def _gen_bars(today: date, count: int, base: float) -> list:
    """`count` weekday OHLC bars ending at the most-recent weekday <= today,
    mild uptrend, non-zero range (positive ATR)."""
    dates: list[date] = []
    dd = today
    while len(dates) < count:
        if dd.weekday() < 5:
            dates.append(dd)
        dd -= timedelta(days=1)
    dates.reverse()
    bars = []
    for i, dt in enumerate(dates):
        c = base * (1 + 0.001 * i)
        bars.append(_Bar(dt, c * 0.99, c * 1.02, c * 0.98, c, 1_000_000.0))
    return bars


# ═══ #1 — each rule fires at its contract price, and not before ══════════════


def test_stop_fires_at_contract_level_not_above(tmp_db, tmp_path):
    """stop_level = max(entry-2.5*ATR, swing_low). Quote just above = no fire;
    quote AT the level = stop fires and books at that price."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    # entry 100, atr 2 -> entry-2.5*atr = 95; swing 95 -> stop_level = 95.
    # gap_top 96 / pre 92 -> drift_dead = 94, so drift stays < 1 near 95 (isolates stop).
    extra = _extra(atr=2.0, swing_low=95.0, pre_close=92.0, gap_top=96.0)
    _seed_open(tmp_db, order_id="o1", symbol="AAA", qty=3, entry=100.0,
               extra=extra, opened=today)
    strat = _strategy(tmp_db, _yaml(tmp_path), risk=FakeRisk("approve"))

    brk = FakeBroker(1000.0, {"AAA": 95.5})            # one tick above stop_level
    exits, _ = asyncio.run(strat.manage(brk))
    assert exits == [] and _open_count(tmp_db) == 1     # MUST NOT fire above level

    brk.quotes["AAA"] = 95.0                            # exactly at stop_level
    exits, _ = asyncio.run(strat.manage(brk))
    assert [o.extra["exit_reason"] for o in exits] == ["stop"]
    r = _row(tmp_db, "o1")
    assert r["result"] == "loss" and r["result_price"] == 95.0
    assert json.loads(r["extra_json"])["exit_reason"] == "stop"


def test_drift_fires_at_giveback_level_not_above(tmp_db, tmp_path):
    """drift_dead = gap_top - 0.5*(gap_top-pre_close). Stop kept far away so drift
    is isolated. Quote above the level = no fire; AT it = drift fires (a winner)."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    # entry 100, atr 10 -> 75; swing 70 -> stop_level 70 (far). gap_top 110 / pre 100
    # -> gap 10, drift_dead = 105.
    extra = _extra(atr=10.0, swing_low=70.0, pre_close=100.0, gap_top=110.0)
    _seed_open(tmp_db, order_id="o1", symbol="BBB", qty=4, entry=100.0,
               extra=extra, opened=today)
    strat = _strategy(tmp_db, _yaml(tmp_path), risk=FakeRisk("approve"))

    brk = FakeBroker(1000.0, {"BBB": 105.5})
    exits, _ = asyncio.run(strat.manage(brk))
    assert exits == [] and _open_count(tmp_db) == 1

    brk.quotes["BBB"] = 105.0
    exits, _ = asyncio.run(strat.manage(brk))
    assert [o.extra["exit_reason"] for o in exits] == ["drift"]
    r = _row(tmp_db, "o1")
    assert r["result"] == "win" and r["result_price"] == 105.0   # exit > entry


def test_guard_fires_inside_lead_window_not_before(tmp_path):
    """guard fires when trading-days-to-next-earnings <= GUARD_LEAD_DAYS (2).
    Price held neutral so only guard can fire. d2n==3 -> no fire; d2n==2 -> fire."""
    today = datetime.now(timezone.utc).date()
    # neutral price: 108 -> stop 0 (above entry), drift 0.4 (<1), held 0.
    extra_common = dict(atr=10.0, swing_low=70.0, pre_close=100.0, gap_top=110.0)

    url_no = f"sqlite:///{tmp_path / 'guard_no.db'}"
    init_db(url_no)
    _seed_open(url_no, order_id="g", symbol="CCC", qty=2, entry=100.0,
               extra=_extra(**extra_common, next_earn=_future_bdays(today, 3)),
               opened=today)
    s_no = _strategy(url_no, _yaml(tmp_path), risk=FakeRisk("approve"))
    exits, _ = asyncio.run(s_no.manage(FakeBroker(1000.0, {"CCC": 108.0})))
    assert exits == [] and _open_count(url_no) == 1     # 3 td out: not yet

    url_fire = f"sqlite:///{tmp_path / 'guard_fire.db'}"
    init_db(url_fire)
    _seed_open(url_fire, order_id="g", symbol="CCC", qty=2, entry=100.0,
               extra=_extra(**extra_common, next_earn=_future_bdays(today, 2)),
               opened=today)
    s_fire = _strategy(url_fire, _yaml(tmp_path), risk=FakeRisk("approve"))
    exits, _ = asyncio.run(s_fire.manage(FakeBroker(1000.0, {"CCC": 108.0})))
    assert [o.extra["exit_reason"] for o in exits] == ["guard"]  # 2 td out: flatten


def test_time_fires_at_max_hold_not_before(tmp_path):
    """time fires at MAX_HOLD_TRADING_DAYS (60) held. Price neutral, no earnings
    date (guard skipped). held 59 -> no fire; held 60 -> time fires."""
    today = datetime.now(timezone.utc).date()
    extra = _extra(atr=10.0, swing_low=70.0, pre_close=100.0, gap_top=110.0)

    url_no = f"sqlite:///{tmp_path / 'time_no.db'}"
    init_db(url_no)
    _seed_open(url_no, order_id="t", symbol="DDD", qty=2, entry=100.0, extra=extra,
               opened=_past_bdays(today, 59))
    s_no = _strategy(url_no, _yaml(tmp_path), risk=FakeRisk("approve"))
    exits, _ = asyncio.run(s_no.manage(FakeBroker(1000.0, {"DDD": 108.0})))
    assert exits == [] and _open_count(url_no) == 1

    url_fire = f"sqlite:///{tmp_path / 'time_fire.db'}"
    init_db(url_fire)
    _seed_open(url_fire, order_id="t", symbol="DDD", qty=2, entry=100.0, extra=extra,
               opened=_past_bdays(today, 60))
    s_fire = _strategy(url_fire, _yaml(tmp_path), risk=FakeRisk("approve"))
    exits, _ = asyncio.run(s_fire.manage(FakeBroker(1000.0, {"DDD": 108.0})))
    assert [o.extra["exit_reason"] for o in exits] == ["time"]


# ═══ #2 — precedence + de-dup ════════════════════════════════════════════════


def test_two_rules_cross_precedence_and_dedup(tmp_db, tmp_path):
    """stop AND drift both reach 1.0 in one tick. First-match precedence fires
    stop (not drift); exactly one sell; and the result-IS-NULL de-dup means a
    second manage tick fires nothing (no double sell)."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    # entry 100, atr 4 -> 90; swing 95 -> stop_level 95. gap_top 110 / pre 100 ->
    # drift_dead 105. quote 80 drives BOTH stop and drift well past 1.0.
    extra = _extra(atr=4.0, swing_low=95.0, pre_close=100.0, gap_top=110.0)
    _seed_open(tmp_db, order_id="o1", symbol="EEE", qty=5, entry=100.0,
               extra=extra, opened=today)
    strat = _strategy(tmp_db, _yaml(tmp_path), risk=FakeRisk("approve"))
    brk = FakeBroker(1000.0, {"EEE": 80.0})

    exits, _ = asyncio.run(strat.manage(brk))
    assert len(exits) == 1                                   # not two sells
    assert exits[0].extra["exit_reason"] == "stop"           # stop wins precedence
    assert exits[0].id == "o1-exit-stop"
    assert _open_count(tmp_db) == 0                           # row closed

    exits2, _ = asyncio.run(strat.manage(brk))               # next tick
    assert exits2 == []                                      # de-dup: no second sell


# ═══ #3 + #4 — round-to-zero (next ranked fills) + extra_json round-trip ══════


def _scan_harness(monkeypatch, db_url, tmp_path, *, equity, prices):
    """Build a scan() ready strategy whose ranked wave is [AAA (expensive),
    BBB (cheap)] with canned daily bars, so the sizing/skip path is exercised
    deterministically. Returns (strategy, broker, bars_by, ann, nxt)."""
    init_db(db_url)
    today = datetime.now(timezone.utc).date()
    ann = _past_bdays(today, 1)            # announced 1 trading day ago (entry window)
    nxt = today + timedelta(days=30)
    bars_by = {sym: _gen_bars(today, 40, base) for sym, base in prices.items()}

    provider = SimpleNamespace(
        get_quarterly_eps=lambda sym: [
            SimpleNamespace(report_date=ann, actual_eps=1.0 + 0.1 * i) for i in range(8)
        ],
        get_company_facts=lambda sym: {"market_cap": 5e8, "sector": "technology"},
        get_next_earnings_date=lambda sym, asof=None: nxt,
    )
    strat = _strategy(db_url, _yaml(tmp_path, universe=["AAA", "BBB"]),
                      risk=FakeRisk("approve"), provider=provider)

    monkeypatch.setattr(
        "trading_corp.agents.strategies.pead_strategy.rank_wave",
        lambda eps_by, screens, **k: [
            SimpleNamespace(symbol="AAA", sue=3.0),    # ranked FIRST, round-to-zero
            SimpleNamespace(symbol="BBB", sue=2.5),    # ranked SECOND, must fill
        ],
    )
    monkeypatch.setattr(PEADStrategy, "_fetch_daily_bars",
                        staticmethod(lambda symbol, lookback_days=180: bars_by[symbol]))
    return strat, FakeBroker(equity, {}), bars_by, ann, nxt


def test_round_to_zero_skips_and_next_ranked_fills(tmp_db, tmp_path, monkeypatch):
    """At ~$7.50/position (equity 75 * 10%), the top-ranked $7k name rounds to 0
    shares and is skipped; the next ranked $5 name fills 1 share — the book still
    reaches a position. This is the NORMAL path, not an edge case."""
    strat, brk, _bars, _ann, _nxt = _scan_harness(
        monkeypatch, tmp_db, tmp_path, equity=75.0,
        prices={"AAA": 7000.0, "BBB": 5.0},
    )
    orders = asyncio.run(strat.scan(brk))
    assert [o.symbol for o in orders] == ["BBB"]            # AAA skipped, BBB filled
    rows = query_open_positions(tmp_db)
    assert {r["symbol"] for r in rows} == {"BBB"}
    assert rows[0]["qty"] == 1.0                           # floor(7.5 / ~5.2) == 1


def test_extra_json_round_trips_six_locked_keys(tmp_db, tmp_path, monkeypatch):
    """The 6 locked keys the dashboard + exit engine read survive JSON+DB and
    equal the engine's own _build_primitives output (no drift between writer and
    reader)."""
    strat, brk, bars_by, ann, nxt = _scan_harness(
        monkeypatch, tmp_db, tmp_path, equity=75.0,
        prices={"AAA": 7000.0, "BBB": 5.0},
    )
    asyncio.run(strat.scan(brk))
    row = query_open_positions(tmp_db)[0]
    extra = row["extra"]

    six = ("entry_atr_14", "post_earnings_swing_low", "pre_earnings_close",
           "earnings_gap_top", "next_earnings_date", "entry_sue")
    assert all(k in extra for k in six), f"missing keys: {set(six) - set(extra)}"

    # primitives_from_extra (the reader) reconstructs cleanly from the round-trip.
    prim = pp.primitives_from_extra(extra, row["entry_price"])
    assert prim is not None

    # and the persisted numbers equal what the engine computed at write time.
    recomputed = strat._build_primitives(bars_by["BBB"], ann, row["entry_price"])
    assert extra["entry_atr_14"] == recomputed["entry_atr_14"]
    assert extra["post_earnings_swing_low"] == recomputed["post_earnings_swing_low"]
    assert extra["pre_earnings_close"] == recomputed["pre_earnings_close"]
    assert extra["earnings_gap_top"] == recomputed["earnings_gap_top"]
    assert extra["entry_sue"] == 2.5                       # BBB's ranked SUE
    assert extra["next_earnings_date"] == nxt.isoformat()


# ═══ #5 — NO-HITL: RiskAgent.evaluate is the only gate ═══════════════════════


def test_live_exit_places_through_data_exec_with_risk_as_only_gate(tmp_db, tmp_path):
    """LIVE (execution_mode=live AND auto_execute) — a fired rule routes through
    data_exec.place exactly once, with the risk gate consulted and NO approval
    step between approve and placement."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    extra = _extra(atr=2.0, swing_low=95.0, pre_close=92.0, gap_top=96.0)
    _seed_open(tmp_db, order_id="o1", symbol="AAA", qty=3, entry=100.0,
               extra=extra, opened=today)
    risk = FakeRisk("approve")
    de = FakeDataExec()
    strat = _strategy(tmp_db, _yaml(tmp_path, auto_execute=True), risk=risk,
                      execmode="live", data_exec=de)

    exits, _ = asyncio.run(strat.manage(FakeBroker(1000.0, {"AAA": 95.0})))
    assert len(exits) == 1 and exits[0].execution_mode == "live"
    assert len(de.placed) == 1                             # placed through data_exec
    assert de.placed[0][1] == "robinhood_pead"            # division-routed
    assert len(risk.calls) == 1                           # the gate WAS consulted


def test_risk_reject_blocks_placement(tmp_db, tmp_path):
    """The risk gate is the ONLY gate AND it is authoritative: a reject means the
    order is never placed (no HITL override path)."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    extra = _extra(atr=2.0, swing_low=95.0, pre_close=92.0, gap_top=96.0)
    _seed_open(tmp_db, order_id="o1", symbol="AAA", qty=3, entry=100.0,
               extra=extra, opened=today)
    de = FakeDataExec()
    logger = FakeLogger()
    strat = _strategy(tmp_db, _yaml(tmp_path, auto_execute=True),
                      risk=FakeRisk("reject", reason="cap"), execmode="live",
                      data_exec=de, logger=logger)

    exits, _ = asyncio.run(strat.manage(FakeBroker(1000.0, {"AAA": 95.0})))
    assert exits == []                                    # rejected -> not placed
    assert de.placed == []                                # never reached the broker
    assert _open_count(tmp_db) == 1                       # position left open
    assert any(getattr(o, "status", None) == "risk_rejected"
               for o in logger.proposed)


def test_paper_mode_never_calls_data_exec_place(tmp_db, tmp_path):
    """Structural safety claim: PAPER books the close but NEVER calls
    data_exec.place. Holds even with auto_execute on while execution_mode!=live."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    extra = _extra(atr=2.0, swing_low=95.0, pre_close=92.0, gap_top=96.0)
    _seed_open(tmp_db, order_id="o1", symbol="AAA", qty=3, entry=100.0,
               extra=extra, opened=today)
    de = FakeDataExec()
    strat = _strategy(tmp_db, _yaml(tmp_path, auto_execute=True), risk=FakeRisk(),
                      execmode="paper", data_exec=de)      # paper despite auto_execute

    exits, _ = asyncio.run(strat.manage(FakeBroker(1000.0, {"AAA": 95.0})))
    assert len(exits) == 1 and exits[0].execution_mode == "paper"
    assert de.placed == []                                # paper NEVER places
    assert _open_count(tmp_db) == 0                        # but the close IS booked


def test_live_requires_auto_execute_kill_switch(tmp_db, tmp_path):
    """execution_mode==live but auto_execute False (the Board kill-switch) keeps
    the engine on the paper path — no real placement."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    extra = _extra(atr=2.0, swing_low=95.0, pre_close=92.0, gap_top=96.0)
    _seed_open(tmp_db, order_id="o1", symbol="AAA", qty=3, entry=100.0,
               extra=extra, opened=today)
    de = FakeDataExec()
    strat = _strategy(tmp_db, _yaml(tmp_path, auto_execute=False), risk=FakeRisk(),
                      execmode="live", data_exec=de)

    exits, _ = asyncio.run(strat.manage(FakeBroker(1000.0, {"AAA": 95.0})))
    assert len(exits) == 1 and exits[0].execution_mode == "paper"
    assert de.placed == []                                # kill-switch held live OFF
