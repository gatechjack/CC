"""Bitunix SFP CONSTRUCT Cockpit — situational-awareness view for the LIVE construct.

Retargets the SFP cockpit from the old no-edge config (pivot50 / 2R) to the DEPLOYED construct
(two-candle SFP + fresh-inst + with-trend + 1h detect + 3R, all 4 coins, live 2026-07-10). This is
a DISPLAY layer only (HTMX fragment-poll) — it does NOT touch prod trading logic. Mounted at a NEW
route (/sfp/construct) so it can be reviewed side-by-side with the live /sfp cockpit before any cutover.

★ HONESTY DISCIPLINE (the whole point):
  - Every panel reads REAL engine data or shows an honest empty/accumulating state. NO fabricated
    numbers. Un-wired stages get a visible "pending emit-wire" badge.
  - ★KEY FINDING: the quality-gate FUNNEL is fully RECONSTRUCTABLE from EXISTING data
    (sfp_watch_state lifecycle + audit_event gate-skip kinds) — so NO new observer emit-counters are
    needed. This is a pure display layer; the trading engine is untouched.
  - RD (LuxAlgo Range Detector) + position-in-range are VIEW-COMPUTED from real bars and labelled as
    such (the construct GATES on 15m ema200, not RD — RD is a situational-awareness overlay only).
  - Live-vs-backtest verdict is GATED behind n>=30 fills; below that it shows "accumulating n/30 — not
    yet significant" (NEVER a divergence alarm on a handful of trades). At ~3 setups/wk that is ~10wk.

Wire-up (one line in trading_corp/web/routes.py register(app)):
    from trading_corp.web import sfp_construct_cockpit_view
    sfp_construct_cockpit_view.register(app)
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from trading_corp.persistence import db
# reuse the vetted TIER-C runtime truth + regime/bar helpers from the live cockpit
from trading_corp.web.sfp_cockpit_view import (
    DIVISION, SYMBOLS, runtime_badge, _live_count, _symbol_arm_map,
    _regime_state, _bar_strip, _latest_close, _week_start_iso, _utc_now,
    _STRAT_YAML, COCKPIT_STATS_SINCE, COCKPIT_STATS_SINCE_LABEL,
)

log = logging.getLogger(__name__)

# ── construct constants (retargeted) ───────────────────────────────────────
TP_R = 3.0                      # construct target (was 2.0)
BASELINE_AVG_R = 0.182          # in-sample pooled backtest benchmark (GROSS, Binance proxy)
# ★closed-trade performance reads paper_trade_record (the reconciler/server-side-stop close record —
# SAME source /sfp uses), NOT research_log.realized_r (which never populates on a live close). Scoped
# to the SHARED forward-scoreboard epoch (imported from sfp_cockpit_view = RD-gate go-live) so BOTH
# cockpits reset to the SAME T and stay in agreement. Pre-epoch closes remain in the DB, just uncounted.
CONSTRUCT_SINCE = COCKPIT_STATS_SINCE
SIG_N = 30                      # fills needed before a live-vs-backtest verdict is meaningful
EXPECTED_SETUPS_WK = 3          # ~157 booked/yr across 4 coins ≈ 3/wk (healthy, not stalled)
RD_LEN = 20                     # LuxAlgo Range Detector window (bars)
RD_MULT = 1.0                   # range = all last RD_LEN closes within RD_MULT*ATR of SMA
PIR_WIN = 96                    # position-in-range window (15m bars ≈ 24h)
GATE_TF = "15m"                 # RD/PIR/regime computed on 15m (= the construct's regime TF)


def _loads(s):
    if not s:
        return {}
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return {}


# ── L5 tight with-trend gate SOURCE (display-only; honest read of the LIVE config) ──
def _trend_mode_map() -> dict:
    """WIRE symbol -> live tight with-trend gate source ('ema200'|'rd'|'ps_trail30') from
    bitunix_sfp.trend_mode. DISPLAY-ONLY — this is exactly the map the engine hot-reads per signal, so
    the cockpit shows the gate that IS live. Absent/unreadable -> {} (every coin renders the ema200
    default = the inert state). Keys in the YAML may be display or wire form; normalized to wire
    (BTC->BTCUSDT)."""
    try:
        import yaml
        with open(_STRAT_YAML, encoding="utf-8") as f:
            raw = (yaml.safe_load(f) or {}).get(DIVISION) or {}
        out: dict = {}
        for key, val in (raw.get("trend_mode") or {}).items():
            k = str(key).upper()
            wire = SYMBOLS.get(str(key)) or (k if k.endswith("USDT") else f"{k.split('/')[0]}USDT")
            mode = str(val).lower()
            out[wire] = mode if mode in ("ema200", "rd", "ps_trail30") else "ema200"
        return out
    except Exception as e:                                    # noqa: BLE001
        log.warning("sfp_construct_cockpit: trend_mode read failed: %s", e)
        return {}


# ── PANEL 1 — REGIME (real ema200 gate) + RD + position-in-range (view-computed) ──
def _regime_panel(db_url: str, wire: str) -> dict:
    """Real 15m ema200 regime (the LIVE construct gate, from bitunix_sfp_regime_state) + a
    VIEW-COMPUTED LuxAlgo Range-Detector state and position-in-range from real bars. The RD/PIR are
    labelled view_computed=True — they are situational-awareness overlays, NOT the live gate."""
    reg = _regime_state(db_url, wire)                       # {label, to_up, last_flip_ts, ...} REAL
    with db.connect(db_url) as conn:
        rows = conn.execute(
            "SELECT close, high, low FROM bitunix_bar_history WHERE symbol=? AND timeframe=? "
            "ORDER BY ts_ms DESC LIMIT ?", (wire, GATE_TF, max(RD_LEN + 1, PIR_WIN)),
        ).fetchall()
    closes = [float(r["close"]) for r in reversed(rows)]
    highs = [float(r["high"]) for r in reversed(rows)]
    lows = [float(r["low"]) for r in reversed(rows)]
    rd_state, pir = None, None
    if len(closes) >= RD_LEN + 1:
        win = closes[-RD_LEN:]
        sma = sum(win) / RD_LEN
        # ATR(RD_LEN) Wilder-ish simple: mean true range over the window
        trs = []
        for i in range(len(closes) - RD_LEN, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        atr = sum(trs) / len(trs) if trs else 0.0
        band = RD_MULT * atr
        in_range = all(abs(c - sma) <= band for c in win) if band > 0 else False
        rd_state = "range" if in_range else "trend"
    if len(lows) >= 2:
        w_lo = min(lows[-PIR_WIN:]); w_hi = max(highs[-PIR_WIN:]); cur = closes[-1]
        span = (w_hi - w_lo) or 1.0
        pir = max(0, min(100, round(100 * (cur - w_lo) / span)))
    # L5: the LIVE tight with-trend gate SOURCE for this coin (ema200|rd|ps_trail30) — honest read of
    # the config the engine hot-reads. When 'rd' the RD break-state IS the live gate (not just a view
    # overlay); when 'ema200' or 'ps_trail30' the RD chip stays a view-computed situational overlay
    # (ps_trail30 gates on the DAILY PS trend, so RD is NOT the live gate — rd_is_live_gate stays False).
    gate = _trend_mode_map().get(wire, "ema200")
    return {
        "regime": reg["label"], "to_up": reg["to_up"], "last_flip_ts": reg.get("last_flip_ts"),
        "rd_state": rd_state, "rd_view_computed": True, "pir": pir, "tf": GATE_TF,
        "gate": gate, "rd_is_live_gate": (gate == "rd"),
        "has_bars": len(closes) >= RD_LEN + 1,
    }


# ── PANEL 2 — ★QUALITY-GATE FUNNEL (reconstructed from EXISTING data) ──
def _gate_funnel(db_url: str, wire: str, since_iso: str) -> dict:
    """★THE STAR PANEL — the construct's selectivity funnel, reconstructed from EXISTING engine data
    (sfp_watch_state lifecycle + audit_event gate-skip kinds). NO new observer counters needed.
    Stages (windowed since since_iso):
       raw two-candle SFP fired  = ARMED watches           (sfp_watch_state)
       -> BOS confirmed          = CONFIRMED watches        (sfp_watch_state)
       -> passed with-trend gate = confirmed - counter_trend skips   (audit_event)
       -> passed fresh-inst gate = passed_trend - not_fresh_inst/no_inst_source skips
       -> placed (live)          = would_have_placed + live_order_placed
    Each stage is REAL. If sfp_watch_state has no rows yet the arm/confirm stages show 0 (honest)."""
    root = wire.replace("USDT", "")
    # terminal audit kinds — each BOS-confirmed signal reaching _handle_signal emits EXACTLY ONE of
    # these, so their SUM = signals that reached handling (internally consistent, monotonic funnel).
    # with-trend rejections — includes the L5 RD-gate skips (sfp_skip_rd_range/no_data) AND the
    # ps_trail30-gate skips (sfp_skip_ps_counter/no_data) so an RD- or ps_trail30-gated coin's with-trend
    # drops are accounted for in the funnel exactly like the ema200 counter_trend skip.
    TREND_SKIPS = ("sfp_skip_counter_trend", "sfp_skip_regime_warmup",
                   "sfp_skip_side_disabled", "sfp_skip_invalid_geometry",
                   "sfp_skip_rd_range", "sfp_skip_rd_no_data",
                   "sfp_skip_ps_counter", "sfp_skip_ps_no_data")
    FRESH_SKIPS = ("sfp_skip_not_fresh_inst", "sfp_skip_no_inst_source", "sfp_skip_inst_error")
    # placement ATTEMPTS (one per confirmed signal). live_order_rejected + sfp_bracket_placed are
    # FOLLOW-ON events on an already-counted attempt → excluded from the terminal/reached sum.
    PLACED_KINDS = ("would_have_placed", "live_order_placed")
    POST_FRESH = ("sfp_skip_no_broker", "sfp_skip_no_equity", "sfp_skip_nonpositive_qty",
                  "sfp_concurrent_position_blocked", "sfp_skip_risk_error",
                  "sfp_drawdown_breach_block", "sfp_risk_rejected")
    terminal = TREND_SKIPS + FRESH_SKIPS + PLACED_KINDS + POST_FRESH
    with db.connect(db_url) as conn:
        armed = conn.execute(
            "SELECT COUNT(*) n FROM sfp_watch_state WHERE symbol LIKE ? AND armed_ts >= ?",
            (f"%{root}%", since_iso),
        ).fetchone()
        ac = conn.execute(
            "SELECT kind, COUNT(*) n FROM audit_event WHERE actor=? AND ts >= ? "
            "AND json_extract(payload_json,'$.symbol') LIKE ? GROUP BY kind",
            (DIVISION, since_iso, f"%{root}%"),
        ).fetchall()
        ac = {r["kind"]: int(r["n"]) for r in ac}
    raw = int(armed["n"] or 0)                        # raw two-candle fires = the since-epoch cohort
    # raw is watch-state (armed_ts-based); reached/trend/fresh/placed are audit_event (ts-based). Across
    # the reset epoch those two clocks can disagree — a watch armed PRE-epoch can emit its BOS/skip audit
    # POST-epoch, which would render a NON-MONOTONIC funnel (BOS confirmed > raw fires, the BTC "1 BOS /
    # 0 raw" artifact). The funnel is a strict subset chain by definition, so clamp each stage to <= the
    # previous. When raw==0 (no fires since the epoch) the whole funnel collapses to 0 = the clean slate.
    reached = min(sum(ac.get(k, 0) for k in terminal), raw)   # BOS-confirmed, capped at the fire cohort
    trend_sk = sum(ac.get(k, 0) for k in TREND_SKIPS)
    fresh_sk = sum(ac.get(k, 0) for k in FRESH_SKIPS)
    rejected = ac.get("live_order_rejected", 0)
    passed_trend = min(max(0, reached - trend_sk), reached)
    passed_fresh = min(max(0, passed_trend - fresh_sk), passed_trend)
    # FIX (2026-07-12, funnel-vs-equity discrepancy): a REAL placement passed EVERY gate, so it must NEVER
    # render as 0 placed. The clamp above subtracts with-trend/fresh-inst SKIP audits that come from OTHER
    # signals (NOT bounded to the raw armed-watch cohort); when those skips exceed the raw-capped `reached`,
    # the subtraction underflowed and buried a real placement (the BTC/SOL 2026-07-12 shorts: reached 2 -
    # trend-skips 3 -> 0). Floor `placed` at the authoritative placement-audit count (what paper_trade_record
    # — the equity panel's source — records), then lift the upstream stages to keep the funnel monotonic
    # (fired >= bos >= trend >= fresh >= placed). Only a real PLACEMENT lifts the chain (skips alone never
    # bump raw), so the "skips must not light the funnel" intent below is preserved.
    placed = sum(ac.get(k, 0) for k in PLACED_KINDS)
    passed_fresh = max(passed_fresh, placed)
    passed_trend = max(passed_trend, passed_fresh)
    reached = max(reached, passed_trend)
    raw = max(raw, reached)
    stages = [
        {"key": "fired", "label": "raw 2-candle SFP", "n": raw, "src": "sfp_watch_state"},
        {"key": "bos", "label": "BOS confirmed", "n": reached, "src": "audit_event"},
        {"key": "trend", "label": "passed with-trend", "n": passed_trend, "src": "audit_event"},
        {"key": "fresh", "label": "passed fresh-inst", "n": passed_fresh, "src": "audit_event"},
        {"key": "placed", "label": "placed (broker)", "n": placed, "src": "audit_event"},
    ]
    # has_data keys off the FIRE COHORT (raw), not raw-or-any-audit: a lone pre-epoch-armed watch's
    # post-epoch skip audit must NOT light the funnel — no fires since epoch => "no setups", clean slate.
    return {"stages": stages, "rejected": rejected, "counter_trend": trend_sk, "fresh_skip": fresh_sk,
            "has_data": raw > 0, "stats_since_label": COCKPIT_STATS_SINCE_LABEL}


# ── PANEL 3 — LIFECYCLE (MON→ARM→RES→TRD→CLS) from real sfp_watch_state ──
def _watch_lifecycle(db_url: str, wire: str) -> dict:
    """Real per-coin watch lifecycle from sfp_watch_state (replaces the old TIER-B mock). Shows the
    latest watch's status + the live ARMED/CONFIRMED counts. MON=no active watch, ARM=armed pending
    BOS, RES=confirmed/resolving, and closed states are TIMED_OUT/INVALIDATED."""
    root = wire.replace("USDT", "")
    # BOTH the current-state read and the armed/confirmed live COUNTS are scoped to the reset epoch, so the
    # lifecycle rail + "N armed · M confirmed" chip reflect only the RD-gated era (clean slate). A watch
    # armed before the epoch is pre-reset activity: it collapses the rail to MON and the counts to 0 (it
    # is NOT deleted — sfp_watch_state keeps every row; this is the same "count from T" filter).
    with db.connect(db_url) as conn:
        cur = conn.execute(
            "SELECT status, fired_bar_ts, swept_wick, bos_watch_level, armed_ts, status_ts "
            "FROM sfp_watch_state WHERE symbol LIKE ? AND armed_ts >= ? "
            "ORDER BY COALESCE(status_ts, armed_ts) DESC LIMIT 1",
            (f"%{root}%", COCKPIT_STATS_SINCE),
        ).fetchone()
        live = conn.execute(
            "SELECT SUM(status='ARMED') armed, SUM(status='CONFIRMED') confirmed "
            "FROM sfp_watch_state WHERE symbol LIKE ? AND armed_ts >= ?",
            (f"%{root}%", COCKPIT_STATS_SINCE),
        ).fetchone()
    if not cur:
        return {"has_data": False, "state": "MON", "armed_live": 0, "confirmed_live": 0}
    st = (cur["status"] or "").upper()
    stage = {"ARMED": "ARM", "CONFIRMED": "RES", "INVALIDATED": "CLS", "TIMED_OUT": "CLS"}.get(st, "MON")
    return {"has_data": True, "state": stage, "raw_status": st,
            "swept_wick": cur["swept_wick"], "bos_watch_level": cur["bos_watch_level"],
            "armed_ts": cur["armed_ts"], "status_ts": cur["status_ts"],
            "armed_live": int(live["armed"] or 0), "confirmed_live": int(live["confirmed"] or 0)}


# ── PANEL 4 — SETUPS-THIS-WEEK vs EXPECTED (healthy low-frequency framing) ──
def _setups_this_week(db_url: str) -> dict:
    """Count real armed setups this week (all coins) vs the ~3/wk backtest expectation. Reframes 'no
    setup' as quality-gated + EXPECTED, not stalled."""
    wk = max(_week_start_iso(), COCKPIT_STATS_SINCE)   # floored at the reset epoch (clean slate)
    with db.connect(db_url) as conn:
        raw = conn.execute(
            "SELECT COUNT(*) n FROM sfp_watch_state WHERE armed_ts >= ?", (wk,),
        ).fetchone()
        placed = conn.execute(
            "SELECT COUNT(*) n FROM audit_event WHERE actor=? AND ts >= ? "
            "AND kind IN ('would_have_placed','live_order_placed')", (DIVISION, wk),
        ).fetchone()
    # "setups" = fully-qualified (post-gate) placements — comparable to the ~3/wk booked expectation.
    # raw_fires = raw two-candle fires (armed watches) BEFORE the with-trend + fresh-inst gates.
    setups = int(placed["n"] or 0)
    raw_fires = int(raw["n"] or 0)
    return {"setups": setups, "raw_fires": raw_fires, "expected": EXPECTED_SETUPS_WK,
            "healthy": True, "note": "quality-gated — low frequency is the design, not a stall",
            "stats_since_label": COCKPIT_STATS_SINCE_LABEL}


# ── PANEL 5 — LIVE vs BACKTEST (gated behind n>=30) ──
def _live_vs_backtest(db_url: str) -> dict:
    """Construct realized-R vs the +0.182 backtest baseline — but the diverging/on-track VERDICT is
    GATED behind n>=30 fills. Below that: 'accumulating n/30 — not yet significant'. Reads
    paper_trade_record (the authoritative reconciler/server-side-stop close record — SAME source /sfp
    uses); research_log.realized_r never populates on a live close."""
    with db.connect(db_url) as conn:
        rows = conn.execute(
            "SELECT actual_r_multiple AS r FROM paper_trade_record "
            "WHERE division=? AND result IN ('win','loss') AND result_ts >= ? "
            "AND actual_r_multiple IS NOT NULL", (DIVISION, CONSTRUCT_SINCE),
        ).fetchall()
    rs = [float(r["r"]) for r in rows]
    n = len(rs)
    avg = round(sum(rs) / n, 3) if n else None
    if n < SIG_N:
        verdict, significant = "accumulating", False
    else:
        significant = True
        verdict = "on-track" if avg is not None and avg >= BASELINE_AVG_R * 0.5 else "diverging"
    return {"n": n, "need": SIG_N, "avg_r": avg, "baseline": BASELINE_AVG_R,
            "significant": significant, "verdict": verdict,
            "note": f"accumulating {n}/{SIG_N} — not yet significant" if not significant else "",
            "stats_since_label": COCKPIT_STATS_SINCE_LABEL}


# ── PANEL 6 — DIVISION EQUITY (construct cum-R, from paper_trade_record) ──
def _construct_equity(db_url: str) -> dict:
    """Cumulative realized-R for the construct from paper_trade_record (same authoritative close record
    /sfp uses), construct-scoped + time-ordered. Honest empty until the first fill closes."""
    with db.connect(db_url) as conn:
        rows = conn.execute(
            "SELECT actual_r_multiple AS r FROM paper_trade_record WHERE division=? "
            "AND result IN ('win','loss') AND result_ts >= ? AND actual_r_multiple IS NOT NULL "
            "ORDER BY result_ts ASC", (DIVISION, CONSTRUCT_SINCE),
        ).fetchall()
    cum, pts = 0.0, []
    for r in rows:
        cum += float(r["r"]); pts.append(round(cum, 3))
    return {"has_data": bool(pts), "n_closed": len(pts), "cum_r": round(cum, 2) if pts else None,
            "points": pts, "stats_since_label": COCKPIT_STATS_SINCE_LABEL}


# ── per-coin card + board ──
def _construct_coin(db_url: str, display: str, arm: str, div_live: bool) -> dict:
    wire = SYMBOLS[display]
    # funnel window = SINCE THE FORWARD-SCOREBOARD EPOCH (the 2026-07-13 net-basis boundary; matches the
    # shared COCKPIT_STATS_SINCE_LABEL + the lifecycle/live counts, which also key off the epoch). ★Was max(week_start, epoch): that
    # silently narrowed to the current Mon-week once a week rolled past the epoch — so on the first Monday
    # after go-live every coin with no new-week fire (SOL/ETH) blanked to "no setups this week" under a
    # "since epoch" label. The epoch IS the clean-slate reset; no second weekly floor is wanted here.
    since = COCKPIT_STATS_SINCE
    return {
        "symbol": display, "wire": wire,
        "is_live_coin": (arm == "trading") and div_live,
        "exec_tag": ("LIVE" if (arm == "trading" and div_live) else ("MONITOR" if arm == "watch" else "PAPER")),
        "regime": _regime_panel(db_url, wire),
        "funnel": _gate_funnel(db_url, wire, since),
        "lifecycle": _watch_lifecycle(db_url, wire),
        "price": _latest_close(db_url, wire),
        "bars": _bar_strip(db_url, wire),
    }


def register(app: FastAPI) -> None:
    templates = app.state.templates
    deps = app.state.deps
    db_url = deps.db_url

    async def _q(fn, *a):
        return await asyncio.to_thread(fn, *a)

    async def _board():
        arm = _symbol_arm_map()
        div_live = runtime_badge(deps).get("state") == "live"
        return [await _q(_construct_coin, db_url, d, arm.get(d, "watch"), div_live) for d in SYMBOLS]

    def _summary():
        return {
            "badge": runtime_badge(deps), "live_count": _live_count(deps),
            "setups": None, "lvb": None, "equity": None,
        }

    @app.get("/sfp/construct", response_class=HTMLResponse)
    async def construct_cockpit(request: Request):
        ctx = {
            "badge": runtime_badge(deps), "live_count": _live_count(deps),
            "coins": await _board(),
            "setups": await _q(_setups_this_week, db_url),
            "lvb": await _q(_live_vs_backtest, db_url),
            "equity": await _q(_construct_equity, db_url),
            "tp_r": TP_R, "baseline": BASELINE_AVG_R,
        }
        return templates.TemplateResponse(request, "sfp_construct_cockpit.html", ctx)

    @app.get("/sfp/construct/partials/board", response_class=HTMLResponse)
    async def construct_board(request: Request):
        return templates.TemplateResponse(
            request, "sfp_construct_cockpit/_board.html", {"coins": await _board()})

    @app.get("/sfp/construct/partials/summary", response_class=HTMLResponse)
    async def construct_summary(request: Request):
        return templates.TemplateResponse(
            request, "sfp_construct_cockpit/_summary.html",
            {"badge": runtime_badge(deps), "live_count": _live_count(deps),
             "setups": await _q(_setups_this_week, db_url),
             "lvb": await _q(_live_vs_backtest, db_url),
             "equity": await _q(_construct_equity, db_url),
             "tp_r": TP_R, "baseline": BASELINE_AVG_R})

    log.info("SFP CONSTRUCT cockpit routes registered at /sfp/construct")
