"""Phase 2 (2026-07-31): 4x/day ET judgment scheduler + Telegram digest/delta/
heartbeat/split. Covers the pure helpers (split, delta thresholds, digest builders),
the calendar-aware slot selection (full-day / half-day / weekend / missed), the
marker idempotency of _judgment_tick, a dry-run compose_slot_digest (no live send,
places nothing), and the judgment_pass vs scan() PARITY guard for the duplicated
LLM+compose+store orchestration.
"""
from __future__ import annotations

from datetime import datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from trading_corp.agents.divisions.pmcc_robinhood import (
    PMCCAgent, PMCCPosition, PMCCAnalysis,
    judgment_delta, build_full_digest, build_delta_digest, format_digest_line,
    holding_snapshot, digest_row,
)
from trading_corp.comms.telegram_bot import TelegramChannel
from trading_corp.main import (
    _due_judgment_slot, _judgment_schedule_cfg, _judgment_tick, _execute_judgment_slot,
)

ET = ZoneInfo("America/New_York")
THRESH = {"target_delta_shift": 0.05, "target_dte_shift": 2,
          "net_move_dollars": 0.10, "net_move_pct": 0.20}


# --------------------------------------------------------------------------- #
# push_split / line splitter
# --------------------------------------------------------------------------- #

def test_split_on_lines_never_drops_and_bounds_each_chunk():
    text = "\n".join(f"L{i:04d} " + "x" * 60 for i in range(400))   # ~26k chars
    chunks = TelegramChannel.split_message_on_lines(text, max_chars=1000)
    assert len(chunks) > 1
    assert all(len(c) <= 1000 for c in chunks)          # each chunk bounded
    assert "\n".join(chunks) == text                    # NO line dropped/reordered


def test_split_single_overlong_line_kept_whole():
    long_line = "Z" * 5000
    text = f"short\n{long_line}\nafter"
    chunks = TelegramChannel.split_message_on_lines(text, max_chars=3900)
    assert long_line in chunks                           # over-long line NOT split mid-line
    assert "\n".join(chunks) == text


class _BufferChannel:
    """Captures push() calls instead of sending (the dry-run Telegram sink)."""
    def __init__(self, fail_on=None):
        self.pushes: list[str] = []
        self._fail_on = fail_on
    async def push(self, text, *, audit_path="other", audit_context=None, chat_id=None):
        self.pushes.append(text)
        return not (self._fail_on is not None and self._fail_on in text)

_BufferChannel.push_split = TelegramChannel.push_split
_BufferChannel.split_message_on_lines = staticmethod(TelegramChannel.split_message_on_lines)


@pytest.mark.asyncio
async def test_push_split_sends_each_chunk_sequentially():
    ch = _BufferChannel()
    text = "\n".join(f"line {i} " + "y" * 50 for i in range(200))
    ok = await ch.push_split(text, max_chars=1000)
    assert ok is True
    assert len(ch.pushes) >= 2                           # split into multiple sends
    assert "\n".join(ch.pushes) == text                  # full content delivered


@pytest.mark.asyncio
async def test_push_split_returns_false_but_still_sends_all_on_failure():
    ch = _BufferChannel(fail_on="line 150")
    text = "\n".join(f"line {i} " + "y" * 50 for i in range(200))
    ok = await ch.push_split(text, max_chars=1000)
    assert ok is False                                   # a chunk failed
    assert "\n".join(ch.pushes) == text                  # nothing dropped regardless


def _entities_balanced(chunk: str) -> bool:
    """Telegram legacy-Markdown sanity for ONE chunk of analyze_portfolio output:
    bold ** paired and italic _ (after removing **) paired. Because push_split cuts
    ONLY on line boundaries and analyze_portfolio balances every entity within its
    own line, each chunk stays balanced -> no bold/italic split across a chunk edge."""
    if chunk.count("**") % 2 != 0:
        return False
    return chunk.replace("**", "").count("_") % 2 == 0


@pytest.mark.asyncio
async def test_scan_markdown_digest_splits_no_drop_each_chunk_parses():
    """The manual /scan digest (analyze_portfolio Markdown) routed through push_split:
    a >4000-char roster splits with NO holding dropped, each chunk within the limit,
    and every chunk keeps its Markdown entities intact (whole-line chunking)."""
    # analyze_portfolio-shaped lines: bold symbol header + italic rationale (the two
    # entity kinds it emits), one entity per line, no underscores in content.
    holdings = [
        f"**SYM{i:03d}** @ $12.34\n"
        f"  ROLL ({70 + i % 30}% conf) - roll the short up and out for a credit\n"
        f"  _healthy leap, short decaying nicely, take the next cycle_"
        for i in range(120)
    ]
    digest = "📊 **PMCC Portfolio Analysis**\n" + "\n".join(holdings)
    assert len(digest) > 4000                            # would truncate under old push()
    ch = _BufferChannel()
    ok = await ch.push_split(digest, max_chars=3900)
    assert ok is True and len(ch.pushes) >= 2
    assert "\n".join(ch.pushes) == digest                # boundaries are EXACTLY on \n
    assert all(len(c) <= 3900 for c in ch.pushes)        # each chunk within limit
    assert all(_entities_balanced(c) for c in ch.pushes)  # no entity split mid-chunk
    orig_lines = set(digest.split("\n"))
    for c in ch.pushes:                                  # whole-line chunking (no partial line)
        assert all(ln in orig_lines for ln in c.split("\n"))
    joined = "\n".join(ch.pushes)
    for i in range(120):                                 # no holding dropped
        assert f"SYM{i:03d}" in joined


# --------------------------------------------------------------------------- #
# digest formatting
# --------------------------------------------------------------------------- #

def test_format_digest_line_full_pricing():
    row = {"symbol": "RKLB", "action": "roll_short", "confidence": 0.82,
           "urgency": "routine",
           "estimate": {"debit": 0.12, "credit": 0.38, "net": 0.26, "open_strike": 185}}
    assert format_digest_line(row) == (
        "RKLB - ROLL - BTC $0.12 / STO $0.38 - net +$0.26 - new 185C - 82%"
    )


def test_format_digest_line_hold_omits_pricing():
    row = {"symbol": "TSLA", "action": "hold", "confidence": 0.6,
           "urgency": "routine", "estimate": None}
    line = format_digest_line(row)
    assert line == "TSLA - HOLD - 60%"
    assert "BTC" not in line


def test_format_digest_line_urgent_carries_deeplink():
    row = {"symbol": "IREN", "action": "close_short", "confidence": 0.9,
           "urgency": "urgent", "estimate": None}
    line = format_digest_line(row)
    assert "/division/robinhood_pmcc?pair=IREN" in line


def test_build_full_digest_one_line_per_holding():
    rows = [
        {"symbol": "AAA", "action": "hold", "confidence": 0.5, "urgency": "routine", "estimate": None},
        {"symbol": "BBB", "action": "roll_short", "confidence": 0.7, "urgency": "routine",
         "estimate": {"debit": 0.1, "credit": 0.3, "net": 0.2, "open_strike": 50}},
    ]
    out = build_full_digest("HEADER", rows)
    lines = out.split("\n")
    assert lines[0] == "HEADER"
    assert len(lines) == 3                                # header + 2 holdings
    assert lines[1].startswith("AAA -") and lines[2].startswith("BBB -")


def test_build_delta_digest_heartbeat_when_nothing_material():
    assert build_delta_digest("14:00", "11:00", [], []) == (
        "14:00 - scan ran - no changes since 11:00"
    )


def test_build_delta_digest_lists_material_and_closed():
    row = {"symbol": "AAA", "action": "roll_short", "confidence": 0.7,
           "urgency": "routine", "estimate": None}
    out = build_delta_digest("14:00", "11:00", [(row, ["action hold->roll_short"])], ["ZZZ"])
    assert "changes since 11:00" in out
    assert "AAA -" in out and "action hold->roll_short" in out
    assert "- ZZZ closed" in out


# --------------------------------------------------------------------------- #
# judgment_delta thresholds
# --------------------------------------------------------------------------- #

def _snap(action="hold", mid=0.30, dte=7, net=0.20, conf=0.5, warnings=None):
    return {"action": action, "mid_delta": mid, "target_dte": dte, "net": net,
            "confidence": conf, "warnings": warnings or [], "urgency": "routine"}


def test_delta_added_when_prior_none():
    d = judgment_delta(None, _snap(), THRESH)
    assert d["material"] and d["reasons"] == ["added"]


def test_delta_action_flip_material():
    assert judgment_delta(_snap(action="hold"), _snap(action="roll_short"), THRESH)["material"]


def test_delta_confidence_only_not_material():
    d = judgment_delta(_snap(conf=0.4), _snap(conf=0.95), THRESH)
    assert d["material"] is False and d["reasons"] == []


def test_delta_target_delta_shift_boundary():
    assert not judgment_delta(_snap(mid=0.30), _snap(mid=0.34), THRESH)["material"]   # 0.04 < 0.05
    assert judgment_delta(_snap(mid=0.30), _snap(mid=0.35), THRESH)["material"]       # 0.05 >=


def test_delta_target_dte_shift_boundary():
    assert not judgment_delta(_snap(dte=7), _snap(dte=8), THRESH)["material"]         # 1 < 2
    assert judgment_delta(_snap(dte=7), _snap(dte=9), THRESH)["material"]             # 2 >=


def test_delta_net_move_dollars():
    # Case where BOTH the $ and % rules miss -> not material.
    assert not judgment_delta(_snap(net=1.00), _snap(net=1.08), THRESH)["material"]   # 0.08<0.10 and 8%<20%
    # $ rule fires (>= 0.10) even though only 11% of prior.
    assert judgment_delta(_snap(net=1.00), _snap(net=1.11), THRESH)["material"]       # 0.11 >= 0.10


def test_delta_net_move_pct_even_if_small_dollars():
    # 0.03 move on a 0.10 prior net = 30% >= 20% -> material even though < $0.10
    assert judgment_delta(_snap(net=0.10), _snap(net=0.13), THRESH)["material"]


def test_delta_new_earnings_flag_material():
    prior = _snap(warnings=[])
    new = _snap(warnings=["earnings in 3 days"])
    assert judgment_delta(prior, new, THRESH)["material"]


def test_holding_snapshot_reads_band_and_net():
    dec = {"status": "roll_short", "target_delta_low": 0.2, "target_delta_high": 0.4,
           "target_dte": 7, "confidence": 0.8, "urgency": "routine", "warnings": []}
    snap = holding_snapshot(dec, {"net": 0.25})
    assert snap["action"] == "roll_short" and snap["mid_delta"] == pytest.approx(0.30)
    assert snap["net"] == 0.25 and snap["target_dte"] == 7


# --------------------------------------------------------------------------- #
# _due_judgment_slot — calendar-aware selection (correction A)
# --------------------------------------------------------------------------- #

class _Cal:
    def __init__(self, ch, cm): self.ch, self.cm = ch, cm
    def close_time_et(self, when):
        d = when.date()
        return datetime(d.year, d.month, d.day, self.ch, self.cm, tzinfo=ET)

CFG = _judgment_schedule_cfg({})
FRI = (2026, 7, 31)      # Friday
SAT = (2026, 8, 1)       # Saturday


def _slot_at(h, m, cal=None, day=FRI):
    cal = cal or _Cal(16, 0)
    now = datetime(day[0], day[1], day[2], h, m, tzinfo=ET)
    d = _due_judgment_slot(now, cal, CFG)
    return d["id"] if d else None


def test_full_day_all_four_slots():
    assert _slot_at(9, 50) == "0945"
    assert _slot_at(11, 5) == "1100"
    assert _slot_at(14, 10) == "1400"
    assert _slot_at(15, 10) == "terminal"


def test_full_day_gap_and_missed_slot_are_none():
    assert _slot_at(12, 0) is None            # between slots
    assert _slot_at(10, 30) is None           # 0945 window (30m) passed -> MISSED, skipped
    assert _slot_at(16, 30) is None           # after close


def test_half_day_drops_afternoon_and_moves_terminal():
    half = _Cal(13, 0)
    assert _slot_at(9, 50, half) == "0945"    # morning full kept
    assert _slot_at(11, 5, half) == "1100"    # 11:00 still before close-margin
    assert _slot_at(14, 0, half) is None      # 14:00 >= close(13:00)-margin -> DROPPED
    assert _slot_at(12, 5, half) == "terminal"  # close-anchored -> ~12:00 on a 13:00 close


def test_weekend_no_slots():
    assert _slot_at(11, 5, day=SAT) is None


def test_closed_day_none():
    class _Closed:
        def close_time_et(self, when): return None
    assert _due_judgment_slot(datetime(*FRI, 11, 0, tzinfo=ET), _Closed(), CFG) is None


# --------------------------------------------------------------------------- #
# _judgment_tick — marker idempotency / missed / deferral / send-fail retry
# --------------------------------------------------------------------------- #

class _FakeDB:
    def __init__(self): self.store = {}
    def load_agent_state(self, agent, key, db_url=None):
        v = self.store.get((agent, key))
        return (v, None) if v is not None else None
    def set_agent_state(self, agent, key, value, db_url=None):
        self.store[(agent, key)] = value


async def _tick(db, now, on_slot, *, liveness=None, backstop=None, defer=None, cal=None):
    ch = _BufferChannel()
    return await _judgment_tick(
        now, cal or _Cal(16, 0), CFG, db=db, db_url="x", on_judgment_slot=on_slot,
        liveness_probe=liveness, backstop_time=backstop, defer_holder=defer or {"date": None},
        logger_agent=None, channel=ch,
    ), ch


@pytest.mark.asyncio
async def test_tick_marker_idempotency_no_double_send():
    db = _FakeDB()
    calls = {"n": 0}
    async def on_slot(due):
        calls["n"] += 1
        return True, "ok"
    now = datetime(*FRI, 11, 5, tzinfo=ET)
    r1, _ = await _tick(db, now, on_slot)
    assert r1["fired"] and r1["sent"] and calls["n"] == 1
    # second invocation of the SAME slot (e.g. mid-day restart) -> already sent, skip
    r2, _ = await _tick(db, now, on_slot)
    assert r2["fired"] is False and r2["reason"] == "already sent" and calls["n"] == 1


@pytest.mark.asyncio
async def test_tick_missed_slot_not_fired():
    db = _FakeDB()
    async def on_slot(due): return True, "ok"
    r, _ = await _tick(db, datetime(*FRI, 12, 0, tzinfo=ET), on_slot)   # in the gap
    assert r["fired"] is False and r["reason"] == "no slot due"


@pytest.mark.asyncio
async def test_tick_send_failure_allows_retry():
    db = _FakeDB()
    calls = {"n": 0}
    async def on_slot(due):
        calls["n"] += 1
        return False, "send failed"    # telegram_sent False
    now = datetime(*FRI, 11, 5, tzinfo=ET)
    r1, _ = await _tick(db, now, on_slot)
    assert r1["fired"] and r1["sent"] is False
    r2, _ = await _tick(db, now, on_slot)      # not "already sent" -> retries
    assert r2["fired"] and calls["n"] == 2


@pytest.mark.asyncio
async def test_tick_liveness_defer_does_not_run_slot():
    db = _FakeDB()
    calls = {"n": 0}
    async def on_slot(due):
        calls["n"] += 1
        return True, "ok"
    async def dead_probe():
        return False, "zero bid (opening rotation)"
    # 0945 full slot is liveness-gated; past backstop -> ONE defer notice, slot NOT run
    r, ch = await _tick(
        db, datetime(*FRI, 9, 55, tzinfo=ET), on_slot,
        liveness=dead_probe, backstop=time(9, 50),
    )
    assert r["fired"] is False and r.get("deferred") is True
    assert calls["n"] == 0
    assert any("deferred" in p for p in ch.pushes)


# --------------------------------------------------------------------------- #
# compose_slot_digest — dry run (no live send, places nothing)
# --------------------------------------------------------------------------- #

def _pos(symbol, short_strike=10.0, short_dte=5):
    return PMCCPosition(
        symbol=symbol, long_leg_expiry="2028-01-21", long_leg_strike=1.0,
        long_leg_delta=0.9, long_leg_dte=900, long_leg_qty=1.0, long_leg_avg_price=100.0,
        long_leg_symbol=f"{symbol} LEAP", short_leg_expiry="2026-08-07",
        short_leg_strike=short_strike, short_leg_dte=short_dte, short_leg_qty=-1.0,
        short_leg_mark=0.5, short_leg_symbol=f"{symbol} short",
    )


class _ComposeStub:
    _cfg: dict = {}
    def __init__(self, legs): self._legs = legs
    async def detect_existing_legs(self, broker): return self._legs

_ComposeStub.compose_slot_digest = PMCCAgent.compose_slot_digest


def _priced(sym, net, buildable=True):
    from trading_corp.web.pmcc_pricing import PricedRoll
    est = ({"debit": 0.10, "credit": 0.10 + net, "net": net, "open_strike": 50}
           if buildable else None)
    return PricedRoll(slug="robinhood_pmcc", symbol=sym, priced_at=0.0,
                      estimate=est, buildable=buildable)


@pytest.mark.asyncio
async def test_compose_full_digest_dry_run(monkeypatch):
    from trading_corp.web import pmcc_pricing
    from trading_corp.agents.divisions import _pmcc_status
    decisions = {
        "AAA": {"status": "roll_short", "confidence": 0.8, "urgency": "routine",
                "target_delta_low": 0.2, "target_delta_high": 0.4, "target_dte": 7, "warnings": []},
        "BBB": {"status": "hold", "confidence": 0.5, "urgency": "routine",
                "target_delta_low": None, "target_delta_high": None, "target_dte": None, "warnings": []},
    }
    async def fake_price(agent, broker, slug, sym, db_url, *, now=None):
        return _priced(sym, 0.26, buildable=(sym == "AAA"))
    monkeypatch.setattr(pmcc_pricing, "price_and_stash", fake_price)
    monkeypatch.setattr(_pmcc_status, "load_decision", lambda s, *, db_url: decisions.get(s))
    stub = _ComposeStub([_pos("AAA"), _pos("BBB")])
    text, snap = await stub.compose_slot_digest(
        object(), "db", kind="full", slot_label="09:45", prev_slot_label="prior",
        prior_decisions={}, prior_snapshot=None, thresholds=THRESH,
    )
    assert "AAA - ROLL" in text and "BBB - HOLD" in text
    assert "net +$0.26" in text                          # buildable pricing rendered
    assert snap["slot"] == "09:45" and set(snap["holdings"]) == {"AAA", "BBB"}


@pytest.mark.asyncio
async def test_compose_delta_material_and_heartbeat(monkeypatch):
    from trading_corp.web import pmcc_pricing
    from trading_corp.agents.divisions import _pmcc_status
    state = {"AAA": {"status": "hold", "confidence": 0.7, "urgency": "routine",
                     "target_delta_low": 0.2, "target_delta_high": 0.4, "target_dte": 7, "warnings": []}}
    async def fake_price(agent, broker, slug, sym, db_url, *, now=None):
        return _priced(sym, 0.20, buildable=False)
    monkeypatch.setattr(pmcc_pricing, "price_and_stash", fake_price)
    monkeypatch.setattr(_pmcc_status, "load_decision", lambda s, *, db_url: state.get(s))
    stub = _ComposeStub([_pos("AAA")])
    prior_snapshot = {"slot": "11:00", "holdings": {
        "AAA": {"action": "hold", "mid_delta": 0.30, "target_dte": 7, "net": 0.20,
                "confidence": 0.7, "warnings": []}}}
    # No change -> heartbeat
    text, _ = await stub.compose_slot_digest(
        object(), "db", kind="delta", slot_label="14:00", prev_slot_label="11:00",
        prior_decisions={"AAA": state["AAA"]}, prior_snapshot=prior_snapshot, thresholds=THRESH,
    )
    assert text == "14:00 - scan ran - no changes since 11:00"
    # Now flip the action -> material delta line
    state["AAA"]["status"] = "roll_short"
    text2, _ = await stub.compose_slot_digest(
        object(), "db", kind="delta", slot_label="14:00", prev_slot_label="11:00",
        prior_decisions={"AAA": {**state["AAA"], "status": "hold"}},
        prior_snapshot=prior_snapshot, thresholds=THRESH,
    )
    assert "changes since 11:00" in text2 and "AAA - ROLL" in text2


@pytest.mark.asyncio
async def test_execute_judgment_slot_dry_run_places_nothing(monkeypatch):
    """Full slot data path via a judgment-only `judge` (places NOTHING) + a buffer
    channel (no live send): capture prior -> judge -> fresh price -> digest -> buffer."""
    from trading_corp.web import pmcc_pricing
    from trading_corp.agents.divisions import _pmcc_status
    decisions = {"AAA": {"status": "roll_short", "confidence": 0.8, "urgency": "routine",
                 "target_delta_low": 0.2, "target_delta_high": 0.4, "target_dte": 7, "warnings": []}}
    async def fake_price(agent, broker, slug, sym, db_url, *, now=None):
        return _priced(sym, 0.26, buildable=True)
    monkeypatch.setattr(pmcc_pricing, "price_and_stash", fake_price)
    monkeypatch.setattr(_pmcc_status, "load_decision", lambda s, *, db_url: decisions.get(s))
    stub = _ComposeStub([_pos("AAA")])
    ch = _BufferChannel()
    judged = {"n": 0}
    async def judge():                        # judgment-only -> NO routing, places nothing
        judged["n"] += 1
    text, sent, snap = await _execute_judgment_slot(
        stub, object(), ch, "db", slot_id="0945", kind="full", label="09:45",
        prev_label="prior", thresholds=THRESH, prior_snapshot=None, judge=judge,
    )
    assert judged["n"] == 1 and sent is True
    assert ch.pushes and "AAA - ROLL" in "\n".join(ch.pushes)   # digest -> BUFFER, not live
    assert snap["slot"] == "09:45"


# --------------------------------------------------------------------------- #
# PARITY: judgment_pass() vs scan()'s judgment step store the SAME verdict
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_pmcc_judgment_parity(tmp_path, monkeypatch):
    """The duplicated LLM+compose+store orchestration in judgment_pass() must produce
    the SAME stored verdict (action + band + DTE + source) as scan()'s judgment step,
    so the two paths cannot silently drift."""
    from trading_corp.agents.divisions import pmcc_robinhood as P
    from trading_corp.agents.divisions import _pmcc_status
    import trading_corp.utils.market_data as md

    agent = P.PMCCAgent(db_url=f"sqlite:///{tmp_path.as_posix()}/parity.db")
    legs = [_pos("AAA", 10.0, 5), _pos("BBB", 20.0, 5)]     # non-0-DTE

    async def _legs(_b): return legs
    async def _prices(syms): return {s: 12.0 for s in syms}
    async def _universe(_b): return []
    def _analysis(sym):
        return PMCCAnalysis(symbol=sym, action="hold", confidence=0.7, urgency="routine",
                            summary="s", rationale="r", warnings=[], target_delta=0.30,
                            target_dte=7, target_delta_low=0.25, target_delta_high=0.35)
    async def _llm(pos, price, regime, vix=None): return _analysis(pos.symbol)

    monkeypatch.setattr(agent, "detect_existing_legs", _legs)
    monkeypatch.setattr(agent, "_fetch_prices", _prices)
    monkeypatch.setattr(agent, "get_universe", _universe)
    monkeypatch.setattr(agent, "_check_options_tier_once", lambda _b: None)
    monkeypatch.setattr(agent, "_llm_analyze_position", _llm)
    monkeypatch.setattr(md, "get_vix", lambda: None)

    captured: list = []
    def _rec(sym, **kw):
        captured.append((sym, kw.get("status"), kw.get("source"),
                         kw.get("target_delta_low"), kw.get("target_delta_high"),
                         kw.get("target_dte")))
        return True
    monkeypatch.setattr(_pmcc_status, "record_pmcc_decision", _rec)

    broker = SimpleNamespace(
        snapshot=lambda: _mk_snap(), quote=lambda s: _mk_quote(s),
    )

    # scan() stores the verdict BEFORE it builds orders; ignore any downstream
    # order-building error — the record calls we compare are already captured.
    try:
        await agent.scan(broker, regime="neutral")
    except Exception:
        pass
    scan_records = sorted(captured)
    captured.clear()

    await agent.judgment_pass(broker, regime="neutral")
    jp_records = sorted(captured)

    assert scan_records, "scan() recorded nothing"
    assert scan_records == jp_records


async def _mk_snap():
    return SimpleNamespace(equity=100000.0, positions=[])


async def _mk_quote(sym):
    return 12.0
