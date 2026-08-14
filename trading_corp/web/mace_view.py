"""Robinhood MACE cockpit — FastAPI routes for the /mace dashboard (plan §
Observability / Dashboard v1).

Wire-up (one line in trading_corp/web/routes.py `register(app)`):

    from trading_corp.web import mace_view
    mace_view.register(app)

Routes:

    GET /mace                    full cockpit page (shell)
    GET /mace/partials/rungs     open-rungs table fragment (htmx-polled ~30s)
    GET /mace/partials/halt      entry-halt tri-state pill (htmx-polled ~30s)
    POST /mace/halt              set the entry-halt latch (audit-before-state)
    POST /mace/arm               clear the entry-halt latch (audit-before-state)

DATA-READINESS DISCIPLINE (mirrors the SFP cockpit): MACE launches STANDBY with
zero open rungs. Every panel renders an HONEST-EMPTY state until real rows exist
— never a fabricated number. The hot states (standby / enabled / auto_execute)
and the frozen effective config come from `deps.mace_division` /
`deps.mace_manager.cfg`; the rungs / equity / IVR / calendar come from the DB via
a SHORT-LIVED `db.connect` per request (off the event-loop thread) — the view
NEVER touches the manager's persistent loop-thread connection, and NEVER calls
the broker (a GET must not place API load or hit RH). The live per-rung MARK /
distance-to-stop / distance-to-PT are computed by the manage LOOP, not the
dashboard, so they render "—" here (honest — the dashboard is a read model).

MACE is zero-HITL: there are NO approve/reject controls (CLAUDE.md's
web-app-is-the-HITL-surface rule is not engaged — MACE has no approval gates in
its order path). The ONE write surface is the entry-HALT button (Board-added
2026-08-13): a kill-switch latch in `agent_state (robinhood_mace, entry_halt)`
with auto_execute:false semantics — it halts NEW entries at the next
symbol/attempt boundary (an already-resting order completes its fill-or-cancel
cycle; HONEST latency, stated in the UI) and open-position management is
deliberately unaffected. The engine reads the latch fail-safe (absent/error =
NOT halted; auto_execute stays the primary kill).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import date, datetime, time as dtime, timedelta, timezone
from statistics import NormalDist
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from trading_corp.persistence import db
from trading_corp.utils.time import now_et, now_utc

_NORM = NormalDist()

log = logging.getLogger(__name__)

DIVISION = "robinhood_mace"
_MANAGED = ("open", "closing")


# ── hot runtime state (from deps; no DB) ─────────────────────────────────
def mace_badge(deps: Any) -> dict:
    """The ACTUAL runtime posture. LIVE only when the broker is real AND the
    division is active (enabled + not standby) AND auto_execute is on; otherwise a
    precise non-live reason. Never a static label."""
    division = getattr(deps, "mace_division", None)
    broker = None
    dx = getattr(deps, "data_exec", None)
    brokers = getattr(dx, "brokers", None) if dx is not None else None
    if isinstance(brokers, dict):
        broker = brokers.get(DIVISION)
    paper = bool(getattr(broker, "paper", True)) if broker is not None else True
    broker_present = broker is not None

    if division is None:
        return {"state": "unwired", "label": "NOT WIRED", "sub": "MACE division not constructed",
                "paper": paper, "broker_present": broker_present,
                "standby": True, "enabled": False, "auto_execute": False}
    try:
        standby = bool(division.standby)
        enabled = bool(division.enabled)
        auto = bool(division.auto_execute)
    except Exception:  # noqa: BLE001 — config read must never 500 the page
        standby, enabled, auto = True, False, False
    active = enabled and not standby and getattr(division, "has_manager", False)

    if not broker_present:
        state, label, sub = "unwired", "NO BROKER", "division not registered"
    elif not enabled:
        state, label, sub = "disabled", "DISABLED", "enabled=false"
    elif standby:
        state, label, sub = "standby", "STANDBY", "loops no-op until standby lifted"
    elif not auto:
        state, label, sub = "disarmed", "DISARMED", "auto_execute=false (exits still run)"
    elif paper:
        state, label, sub = "paper", "PAPER", "broker paper=True (simulated fills)"
    elif active and auto and not paper:
        state, label, sub = "live", "LIVE", "REAL CAPITAL"
    else:
        state, label, sub = "unknown", "UNKNOWN", "indeterminate"
    return {"state": state, "label": label, "sub": sub, "paper": paper,
            "broker_present": broker_present, "standby": standby,
            "enabled": enabled, "auto_execute": auto}


def _cfg(deps: Any):
    mgr = getattr(deps, "mace_manager", None)
    return getattr(mgr, "cfg", None) if mgr is not None else None


def _config_ctx(cfg) -> Optional[dict]:
    """Grouped effective-config rows from the FROZEN MaceConfig (not the YAML
    file) — the plan's "full effective config from the frozen object"."""
    if cfg is None:
        return None
    try:
        e, m, s, x, b = cfg.entry, cfg.management, cfg.sizing, cfg.execution, cfg.breakers
        groups = [
            ("account", [("account_number", cfg.account_number),
                         ("universe", ", ".join(cfg.universe)),
                         ("max_contracts", cfg.max_contracts),
                         ("acknowledge_foreign_positions", cfg.acknowledge_foreign_positions)]),
            ("entry", [("eval / cutoff ET", f"{e.eval_time_et} / {e.entry_cutoff_et}"),
                       ("DTE window", f"{e.dte_min}-{e.dte_max}"),
                       ("short delta", f"{e.short_delta_target} {list(e.short_delta_band)}"),
                       ("credit floor / width", e.credit_floor_pct_of_width),
                       ("risk band $/width", f"min {e.risk_band_min_per_width_usd}xW / max {e.risk_band_max_usd}"),
                       ("IVR floor", e.ivr_floor),
                       ("weekly new / max rungs", f"{e.weekly_new_rungs_per_symbol} / {e.max_rungs_per_symbol}"),
                       ("stop cooldown sessions", e.stop_cooldown_sessions)]),
            ("sizing", [("rung risk %", s.rung_risk_pct),
                        ("deployment target %", s.deployment_target_pct),
                        ("equity snapshot ET", s.equity_snapshot_time_et)]),
            ("management", [("interval sec", m.check_interval_sec),
                            ("window ET", f"{list(m.window_et)}"),
                            ("PT % of credit", m.pt_pct_of_credit),
                            ("stop multiple", m.stop_multiple),
                            ("time exit DTE / at", f"{m.time_exit_dte} / {m.time_exit_at_et}"),
                            ("exdiv guard sessions", m.exdiv_guard_sessions)]),
            ("execution", [("entry start offset", x.entry_start_offset_usd),
                           ("entry tick", x.entry_tick_usd),
                           ("entry wait / attempts", f"{x.entry_fill_wait_sec}s / {x.entry_max_attempts}"),
                           ("exit wait / attempts", f"{x.exit_fill_wait_sec}s / {x.exit_max_attempts}"),
                           ("exit ceiling xwidth", x.exit_hard_ceiling_mult_of_width)]),
            ("breakers (alert-only)", [("enforcement", b.breaker_enforcement),
                                       ("day / week loss %", f"{b.day_loss_pct} / {b.week_loss_pct}"),
                                       ("HWM soft / hard", f"{b.hwm_soft_pct} / {b.hwm_hard_pct}")]),
        ]
        symbols = [{"symbol": sym, "enabled": sc.enabled, "width": sc.width_dollars,
                    "exdiv_guard": sc.exdiv_guard,
                    "blackouts": ", ".join(sc.blackout_event_types) or "—",
                    "overflow_only": getattr(sc, "overflow_only", False)}
                   for sym, sc in cfg.symbols.items()]
        return {"config_hash": cfg.config_hash, "groups": groups, "symbols": symbols}
    except Exception as exc:  # noqa: BLE001 — never 500 on a config-shape surprise
        log.warning("mace_view: config context build failed: %s", exc)
        return None


# ── DB reads (SELECT-only, short-lived connection) ───────────────────────
def _strikes_from_legs(legs_json: str | None) -> str:
    try:
        legs = json.loads(legs_json) if legs_json else None
        if isinstance(legs, list) and len(legs) == 4:
            by = {}
            for leg in legs:
                t = str(leg.get("type") or "").lower()
                sd = str(leg.get("side") or "").lower()
                by[(t, sd)] = leg.get("strike")

            def g(t, sd):
                v = by.get((t, sd))
                return f"{float(v):g}" if v is not None else "?"
            return (f"{g('put','sell')}/{g('put','buy')}P "
                    f"{g('call','sell')}/{g('call','buy')}C")
    except Exception:  # noqa: BLE001
        pass
    return "—"


def _open_rungs(db_url: str) -> list[dict]:
    today = now_et().date()
    qmarks = ",".join("?" for _ in _MANAGED)
    with db.connect(db_url) as conn:
        try:
            rows = conn.execute(
                f"SELECT rung_id, symbol, status, expiry, legs_json, width_dollars, "
                f"contracts, credit_actual, max_risk_usd, pt_debit, entry_ts "
                f"FROM mace_rung WHERE status IN ({qmarks}) "
                f"ORDER BY symbol, expiry", _MANAGED).fetchall()
        except Exception:  # noqa: BLE001 — table absent (fresh DB) -> honest empty
            return []
    out = []
    for r in rows:
        try:
            dte = (date.fromisoformat(r["expiry"]) - today).days
        except Exception:  # noqa: BLE001
            dte = None
        out.append({
            "rung_id": r["rung_id"], "symbol": r["symbol"], "status": r["status"],
            "expiry": r["expiry"], "dte": dte, "strikes": _strikes_from_legs(r["legs_json"]),
            "width": r["width_dollars"], "contracts": r["contracts"],
            "credit": r["credit_actual"], "max_risk": r["max_risk_usd"],
            "pt_debit": r["pt_debit"], "entry_ts": r["entry_ts"],
        })
    return out


def _recent_closed(db_url: str, limit: int = 10) -> list[dict]:
    with db.connect(db_url) as conn:
        try:
            rows = conn.execute(
                "SELECT symbol, expiry, contracts, exit_reason, exit_debit, "
                "realized_pnl, exit_ts FROM mace_rung WHERE status='closed' "
                "ORDER BY exit_ts DESC LIMIT ?", (limit,)).fetchall()
        except Exception:  # noqa: BLE001
            return []
    return [dict(r) for r in rows]


def _latest_equity(db_url: str) -> Optional[dict]:
    with db.connect(db_url) as conn:
        try:
            r = conn.execute(
                "SELECT snap_date, equity, cash, market_value FROM mace_equity_snapshot "
                "ORDER BY snap_date DESC LIMIT 1").fetchone()
        except Exception:  # noqa: BLE001
            return None
    return dict(r) if r is not None else None


def _latest_ivr(db_url: str) -> list[dict]:
    with db.connect(db_url) as conn:
        try:
            rows = conn.execute(
                "SELECT symbol, snap_date, atm_iv, ivr_tasty, source FROM mace_iv_history h "
                "WHERE snap_date = (SELECT MAX(snap_date) FROM mace_iv_history "
                "WHERE symbol = h.symbol) ORDER BY symbol").fetchall()
        except Exception:  # noqa: BLE001
            return []
    return [dict(r) for r in rows]


def _upcoming_events(db_url: str, days: int = 7) -> list[dict]:
    today = now_et().date()
    with db.connect(db_url) as conn:
        try:
            rows = conn.execute(
                "SELECT event_type, symbol_scope, event_date, source FROM economic_event "
                "WHERE event_date >= ? ORDER BY event_date LIMIT 40",
                (today.isoformat(),)).fetchall()
        except Exception:  # noqa: BLE001
            return []
    out = []
    for r in rows:
        try:
            delta = (date.fromisoformat(r["event_date"]) - today).days
        except Exception:  # noqa: BLE001
            continue
        if 0 <= delta <= days:
            d = dict(r)
            d["in_days"] = delta
            out.append(d)
    return out


# ── live per-rung state + derived analytics (UI rebuild 2026-08-14) ──────
# EVERYTHING here is SELECT-only and broker-free. Live mark/spot come from
# mace_rung_live (written by the manage loop); when a tick is missed the row is
# flagged `stale` and the template renders an honest "as of {ts}" — never a
# fabricated number. All derived math (P&L, gauges, POP, payoff inputs) is pure
# arithmetic over stored rung fields + cfg, so it is unit-testable in isolation.
_MKT_OPEN_ET = dtime(9, 30)
_MKT_CLOSE_ET = dtime(16, 0)


def _parse_iso(ts: str | None) -> Optional[datetime]:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _fmt_strikes(lp, sp, sc, lc) -> str:
    def g(v):
        return f"{float(v):g}" if v is not None else "?"
    return f"{g(sp)}/{g(lp)}P {g(sc)}/{g(lc)}C"


def _structured_strikes(legs_json: str | None) -> Optional[dict]:
    try:
        legs = json.loads(legs_json) if legs_json else None
        if isinstance(legs, list) and len(legs) == 4:
            by = {}
            for leg in legs:
                by[(str(leg.get("type") or "").lower(),
                    str(leg.get("side") or "").lower())] = leg.get("strike")

            def g(t, sd):
                v = by.get((t, sd))
                return float(v) if v is not None else None
            return {"sp": g("put", "sell"), "lp": g("put", "buy"),
                    "sc": g("call", "sell"), "lc": g("call", "buy")}
    except Exception:  # noqa: BLE001
        pass
    return None


def _pop(spot, iv, t, be_low, be_high) -> Optional[float]:
    """Probability of profit for a short condor: P(BE_low <= S_T <= BE_high) under
    the SAME zero-rate lognormal the payoff T+0 uses (r=div=0). None when spot/iv
    are unavailable (honest — never a guessed number)."""
    if not (spot and iv and t) or iv <= 0 or t <= 0 or spot <= 0:
        return None
    sd = iv * math.sqrt(t)
    if sd <= 0:
        return None

    def p_below(k):
        return _NORM.cdf((math.log(k / spot) + 0.5 * iv * iv * t) / sd)
    try:
        return max(0.0, min(1.0, p_below(be_high) - p_below(be_low)))
    except (ValueError, ZeroDivisionError):
        return None


def _live_state_map(db_url: str) -> dict:
    """{rung_id: {'mark','spot','ts'}} from mace_rung_live (SELECT-only)."""
    with db.connect(db_url) as conn:
        try:
            rows = conn.execute(
                "SELECT rung_id, mark, spot, ts FROM mace_rung_live").fetchall()
        except Exception:  # noqa: BLE001 — table absent (pre-migration) -> no live state
            return {}
    return {r["rung_id"]: {"mark": r["mark"], "spot": r["spot"], "ts": r["ts"]}
            for r in rows}


def _iv_daily_map(db_url: str) -> dict:
    """{symbol: {'atm_iv','snap_date'}} latest per symbol (A4 corpus)."""
    return {r["symbol"]: {"atm_iv": r["atm_iv"], "snap_date": r["snap_date"]}
            for r in _latest_ivr(db_url)}


def _enrich_one(r: dict, live: Optional[dict], iv_daily: dict, pt_pct: float,
                stop_mult: float, time_exit_dte: int, stale_after: float,
                today: date, now: datetime) -> dict:
    sk = _structured_strikes(r.get("legs_json")) or {}
    sp, lp, sc, lc = sk.get("sp"), sk.get("lp"), sk.get("sc"), sk.get("lc")
    credit = r.get("credit_actual")
    contracts = r.get("contracts") or 1
    width = r.get("width_dollars")
    pt_debit = r.get("pt_debit")
    try:
        exp = date.fromisoformat(r["expiry"])
        dte = (exp - today).days
    except Exception:  # noqa: BLE001
        exp, dte = None, None
    entry_dt = _parse_iso(r.get("entry_ts"))
    time_exit_date = (exp - timedelta(days=time_exit_dte)) if exp else None

    # IV: durable entry IV (A3) preferred, else fresh daily IV (A4), labeled.
    entry_iv = r.get("entry_atm_iv")
    daily = iv_daily.get(r["symbol"]) or {}
    if entry_iv is not None:
        iv, iv_source, iv_asof = entry_iv, "entry", None
    elif daily.get("atm_iv") is not None:
        iv, iv_source, iv_asof = daily["atm_iv"], "daily", daily.get("snap_date")
    else:
        iv, iv_source, iv_asof = None, None, None

    # live state (Track A) + staleness
    mark = spot = mark_ts = None
    age_sec = None
    stale = True
    if live:
        mark, spot, mark_ts = live.get("mark"), live.get("spot"), live.get("ts")
        ts_dt = _parse_iso(mark_ts)
        if ts_dt is not None:
            age_sec = (now - ts_dt).total_seconds()
            stale = age_sec > stale_after

    stop_level = (stop_mult * credit) if credit is not None else None
    pnl = pnl_pct = dist_pt = dist_stop = pt_prog = stop_prog = None
    if mark is not None and credit is not None:
        pnl = (credit - mark) * 100.0 * contracts
        pnl_pct = ((credit - mark) / credit * 100.0) if credit else None
        if pt_debit is not None and (credit - pt_debit):
            pt_prog = max(0.0, min(1.0, (credit - mark) / (credit - pt_debit)))
            dist_pt = (mark - pt_debit) * 100.0 * contracts
        if stop_level is not None and (stop_level - credit):
            stop_prog = max(0.0, min(1.0, (mark - credit) / (stop_level - credit)))
            dist_stop = (stop_level - mark) * 100.0 * contracts

    max_profit = (credit * 100.0 * contracts) if credit is not None else None
    max_loss = r.get("max_risk_usd")
    if max_loss is None and width is not None and credit is not None:
        max_loss = (width - credit) * 100.0 * contracts
    be_low = (sp - credit) if (sp is not None and credit is not None) else None
    be_high = (sc + credit) if (sc is not None and credit is not None) else None
    t = (dte / 365.0) if dte is not None else None
    pop = (_pop(spot, iv, t, be_low, be_high)
           if (be_low is not None and be_high is not None) else None)

    rail = None
    if None not in (lp, sp, sc, lc):
        pad = 8.0
        lo = lp - pad
        hi = lc + pad
        rng = (hi - lo) or 1.0

        def _pct(v):
            return max(0.0, min(100.0, (v - lo) / rng * 100.0))
        rail = {"lp": _pct(lp), "sp": _pct(sp), "sc": _pct(sc), "lc": _pct(lc),
                "spot": (_pct(spot) if spot is not None else None)}

    life = None
    if entry_dt is not None and exp is not None:
        e0 = entry_dt.date()
        span = (exp - e0).days or 1

        def _lpct(d):
            return max(0.0, min(100.0, (d - e0).days / span * 100.0))
        life = {"today": _lpct(today),
                "time_exit": (_lpct(time_exit_date) if time_exit_date else None)}

    # payoff data island for the client canvas (strikes+credit+iv+t+spot).
    payoff = {"sp": sp, "lp": lp, "sc": sc, "lc": lc, "credit": credit,
              "iv": iv, "t": t, "spot": spot,
              "lo": (lp - 18.0 if lp is not None else None),
              "hi": (lc + 18.0 if lc is not None else None)}

    return {
        "rung_id": r["rung_id"], "symbol": r["symbol"], "status": r["status"],
        "sp": sp, "lp": lp, "sc": sc, "lc": lc,
        "strikes_label": _fmt_strikes(lp, sp, sc, lc),
        "credit": credit, "contracts": contracts, "width": width,
        "pt_debit": pt_debit, "stop_level": stop_level,
        "max_profit": max_profit, "max_loss": max_loss,
        "be_low": be_low, "be_high": be_high, "pop": pop,
        "iv": iv, "iv_source": iv_source, "iv_asof": iv_asof,
        "dte": dte, "expiry": r["expiry"],
        "entry_date": (entry_dt.date().isoformat() if entry_dt else None),
        "time_exit_date": (time_exit_date.isoformat() if time_exit_date else None),
        "mark": mark, "spot": spot, "mark_ts": mark_ts,
        "age_sec": age_sec, "stale": stale,
        "pnl": pnl, "pnl_pct": pnl_pct,
        "dist_pt": dist_pt, "dist_stop": dist_stop,
        "pt_prog": pt_prog, "stop_prog": stop_prog,
        "rail": rail, "life": life, "payoff": payoff,
    }


def _enriched_rungs(deps: Any, db_url: str) -> list[dict]:
    """Open/closing rungs joined with mace_rung_live + daily IV + cfg, carrying
    all per-rung analytics the cockpit renders. Broker-free."""
    cfg = _cfg(deps)
    m = getattr(cfg, "management", None)
    pt_pct = getattr(m, "pt_pct_of_credit", 0.5) if m else 0.5
    stop_mult = getattr(m, "stop_multiple", 2.0) if m else 2.0
    time_exit_dte = getattr(m, "time_exit_dte", 21) if m else 21
    interval = getattr(m, "check_interval_sec", 300) if m else 300
    stale_after = max(600.0, interval * 2.0)
    live = _live_state_map(db_url)
    iv_daily = _iv_daily_map(db_url)
    today = now_et().date()
    now = now_utc()
    with db.connect(db_url) as conn:
        try:
            rows = conn.execute(
                "SELECT rung_id, symbol, status, expiry, legs_json, width_dollars, "
                "contracts, credit_actual, max_risk_usd, pt_debit, entry_ts, "
                "entry_atm_iv FROM mace_rung WHERE status IN ('open','closing') "
                "ORDER BY symbol, expiry").fetchall()
        except Exception:  # noqa: BLE001 — table absent -> honest empty
            return []
    return [_enrich_one(dict(r), live.get(r["rung_id"]), iv_daily, pt_pct,
                        stop_mult, time_exit_dte, stale_after, today, now)
            for r in rows]


def _managed_symbols(db_url: str) -> set:
    with db.connect(db_url) as conn:
        try:
            return {x[0] for x in conn.execute(
                "SELECT DISTINCT symbol FROM mace_rung "
                "WHERE status IN ('open','closing')")}
        except Exception:  # noqa: BLE001
            return set()


def _ivr_for_view(deps: Any, db_url: str) -> list[dict]:
    """G3: IVR rows ordered actives-first (cfg.universe order), then any retired
    symbol still holding open rungs (e.g. SPY), and DROP leaked non-universe /
    non-managed symbols. Each row tagged role + below-floor."""
    cfg = _cfg(deps)
    rows = {r["symbol"]: dict(r) for r in _latest_ivr(db_url)}
    universe = list(getattr(cfg, "universe", []) or []) if cfg else []
    e = getattr(cfg, "entry", None)
    floor = getattr(e, "ivr_floor", None) if e else None
    managed = _managed_symbols(db_url)
    out: list[dict] = []
    seen: set = set()
    for sym in universe:                        # actives first, universe order
        if sym in rows and sym not in seen:
            out.append({**rows[sym], "role": "active"})
            seen.add(sym)
    for sym in sorted(managed - seen):          # retired-but-managed (SPY)
        if sym in rows:
            out.append({**rows[sym], "role": "managed"})
            seen.add(sym)
    for r in out:
        iv = r.get("ivr_tasty")
        r["below_floor"] = (iv is not None and floor is not None and iv < floor)
        r["floor"] = floor
    return out


def _equity_ctx(db_url: str) -> dict:
    """G4: latest snapshot + HWM (MAX equity) + a curve series for the sparkline,
    all from mace_equity_snapshot (broker-free)."""
    latest = _latest_equity(db_url)
    with db.connect(db_url) as conn:
        try:
            hwm_row = conn.execute(
                "SELECT MAX(equity) AS hwm FROM mace_equity_snapshot").fetchone()
            curve = conn.execute(
                "SELECT snap_date, equity FROM mace_equity_snapshot "
                "ORDER BY snap_date DESC LIMIT 30").fetchall()
        except Exception:  # noqa: BLE001
            return {"latest": latest, "hwm": None, "curve": []}
    hwm = hwm_row["hwm"] if hwm_row else None
    series = [{"snap_date": c["snap_date"], "equity": c["equity"]}
              for c in reversed(curve)]
    return {"latest": latest, "hwm": hwm, "curve": series}


def _session_ctx(request: Request, deps: Any) -> dict:
    """G5: session label = market phase (from now_et) + engine uptime
    (app.state.live_since_utc) + build sha. Uptime is None when unknown (honest —
    never fabricated); reuses the app-wide live-since, not a bare clock."""
    et = now_et()
    tt = et.time()
    if et.weekday() >= 5:
        phase = "weekend"
    elif tt < _MKT_OPEN_ET:
        phase = "pre-market"
    elif tt < _MKT_CLOSE_ET:
        phase = "open"
    else:
        phase = "after-hours"
    state = getattr(getattr(request, "app", None), "state", None)
    uptime = None
    live_since = getattr(state, "live_since_utc", None)
    if live_since is not None:
        try:
            mins = int((now_utc() - live_since).total_seconds() // 60)
            uptime = f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m"
        except Exception:  # noqa: BLE001
            uptime = None
    cfg = _cfg(deps)
    e = getattr(cfg, "entry", None)
    return {"date": et.date().isoformat(), "phase": phase, "uptime": uptime,
            "git_sha": getattr(state, "git_sha", None),
            "eval_time": (getattr(e, "eval_time_et", None) if e else None)}


_MACE_ERROR_KINDS = (
    "mace_entry_exception", "mace_entry_partial", "mace_entry_unconfirmed",
    "mace_manage_error", "mace_chain_error", "mace_ivr_outage",
    "mace_live_state_error", "mace_iv_snapshot_error",
)


def _recent_audits(db_url: str, limit: int = 40) -> list[dict]:
    """MACE audit_event rows for the activity feed + audit trail (SELECT-only).
    actor 'robinhood_mace' (engine) or 'mace_operations' (UI halt/arm)."""
    with db.connect(db_url) as conn:
        try:
            rows = conn.execute(
                "SELECT ts, actor, kind, payload_json FROM audit_event "
                "WHERE actor IN ('robinhood_mace','mace_operations') "
                "ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        except Exception:  # noqa: BLE001
            return []
    out = []
    for r in rows:
        try:
            payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
        except Exception:  # noqa: BLE001
            payload = {}
        out.append({"ts": r["ts"], "actor": r["actor"], "kind": r["kind"],
                    "payload": payload})
    return out


def _breakers_ctx(deps: Any, db_url: str, equity: Optional[dict]) -> dict:
    """Breaker status panel — ONLY breaker-facts derivable from the ledger +
    audit_event (broker-free). Feed-staleness (quote heartbeat) has NO broker-free
    source and is omitted honestly; 'errors' surfaces recent MACE error audits
    (the closest broker-free proxy for the mock's ORDER REJECTS)."""
    cfg = _cfg(deps)
    e = getattr(cfg, "entry", None)
    brk = getattr(cfg, "breakers", None)
    today = now_et().date().isoformat()
    hour_ago = (now_utc() - timedelta(hours=1)).isoformat()
    eq = (equity or {}).get("latest") or {}
    equity_val = eq.get("equity")
    rows: list[dict] = []
    with db.connect(db_url) as conn:
        try:
            open_n = conn.execute(
                "SELECT COUNT(*) FROM mace_rung WHERE status IN ('open','closing')"
            ).fetchone()[0]
            open_risk = conn.execute(
                "SELECT COALESCE(SUM(max_risk_usd),0) FROM mace_rung "
                "WHERE status IN ('open','closing')").fetchone()[0]
            day_pnl = conn.execute(
                "SELECT COALESCE(SUM(realized_pnl),0) FROM mace_rung "
                "WHERE status='closed' AND substr(exit_ts,1,10)=?", (today,)
            ).fetchone()[0]
            closed = conn.execute(
                "SELECT realized_pnl FROM mace_rung WHERE status='closed' "
                "AND realized_pnl IS NOT NULL ORDER BY exit_ts DESC LIMIT 20"
            ).fetchall()
            errs = conn.execute(
                "SELECT COUNT(*) FROM audit_event WHERE actor='robinhood_mace' "
                "AND ts>=? AND kind IN (%s)" % ",".join("?" * len(_MACE_ERROR_KINDS)),
                (hour_ago, *_MACE_ERROR_KINDS)).fetchone()[0]
        except Exception:  # noqa: BLE001
            return {"rows": [], "omitted": ["feed_staleness"]}
    consec = 0
    for c in closed:
        if (c["realized_pnl"] or 0) < 0:
            consec += 1
        else:
            break
    max_rungs = getattr(e, "max_rungs_per_symbol", None) if e else None
    universe_n = len(getattr(cfg, "universe", []) or []) if cfg else 0
    cap = (max_rungs * universe_n) if (max_rungs and universe_n) else None
    day_loss_pct = getattr(brk, "day_loss_pct", None) if brk else None
    day_limit = (-abs(day_loss_pct) * equity_val
                 if (day_loss_pct and equity_val) else None)
    bp_pct = (open_risk / equity_val * 100.0) if equity_val else None
    rows = [
        {"k": "DAILY P&L", "v": day_pnl, "limit": day_limit, "note": "realized today"},
        {"k": "OPEN RUNGS", "v": open_n, "limit": cap, "note": "portfolio cap"},
        {"k": "BP UTILIZATION", "v_pct": bp_pct, "note": "open risk / settled equity"},
        {"k": "CONSEC LOSSES", "v": consec, "limit": 3, "note": "cool-down on trip"},
        {"k": "MACE ERRORS (1h)", "v": errs, "note": "engine error audits"},
    ]
    return {"rows": rows, "omitted": ["feed_staleness"]}


def _symbol_states(deps: Any, db_url: str) -> list[dict]:
    """Ticker strip (G6) — per-symbol STATE chips (active / managing / off), NO
    live price (broker-free read path). Derived from cfg.universe + enabled +
    open-rung presence + the IVR floor gate."""
    cfg = _cfg(deps)
    if cfg is None:
        return []
    universe = set(getattr(cfg, "universe", []) or [])
    managed = _managed_symbols(db_url)
    e = getattr(cfg, "entry", None)
    floor = getattr(e, "ivr_floor", None) if e else None
    ivr = {r["symbol"]: r.get("ivr_tasty") for r in _latest_ivr(db_url)}
    out = []
    for sym, sc in cfg.symbols.items():
        enabled = bool(getattr(sc, "enabled", False))
        has_rungs = sym in managed
        iv = ivr.get(sym)
        if sym in universe:
            below = (iv is not None and floor is not None and iv < floor)
            state = "blocked" if below else "active"
            label = "BLOCKED · ivr_floor" if below else "ELIGIBLE"
        elif has_rungs:
            state, label = "managing", "MANAGING · entries off"
        elif enabled:
            state, label = "active", "ELIGIBLE"
        else:
            state, label = "off", "OFF"
        out.append({"symbol": sym, "state": state, "label": label,
                    "enabled": enabled, "has_rungs": has_rungs})
    return out


# ── entry-halt latch (the ONE write surface) ─────────────────────────────
def _halt_ctx(deps: Any, db_url: str) -> dict:
    """Tri-state for the halt pill: HALTED (config) when auto_execute is off
    (the latch is moot — config is the stronger kill), HALTED (button) when the
    latch is set, else ARMED. Latch read errors render as unlatched here (read
    model only — the ENGINE's own fail-safe read is what governs behavior)."""
    badge = mace_badge(deps)
    latch = {"halted": False, "ts": None, "source": None}
    try:
        row = db.load_agent_state(DIVISION, "entry_halt", db_url=db_url)
        if row is not None and isinstance(row[0], dict):
            latch = {"halted": bool(row[0].get("halted")), "ts": row[0].get("ts"),
                     "source": row[0].get("source")}
    except Exception:  # noqa: BLE001 — a latch read must never 500 the pill
        log.exception("mace_view: entry_halt latch read failed")
    if not badge["auto_execute"]:
        state = "halted_config"
    elif latch["halted"]:
        state = "halted_button"
    else:
        state = "armed"
    return {"halt": {"state": state, "latch": latch,
                     "auto_execute": badge["auto_execute"]}}


def register(app: FastAPI) -> None:
    templates = app.state.templates
    deps = app.state.deps
    db_url = deps.db_url

    async def _q(fn, *a):
        return await asyncio.to_thread(fn, *a)

    async def _rungs_ctx():
        return {"rungs": await _q(_enriched_rungs, deps, db_url)}

    @app.get("/mace", response_class=HTMLResponse)
    async def mace_cockpit(request: Request):
        cfg = _cfg(deps)
        equity = await _q(_equity_ctx, db_url)
        ctx = {
            "badge": mace_badge(deps),
            "config": _config_ctx(cfg),
            "session": _session_ctx(request, deps),
            "equity": equity,
            "ivr": await _q(_ivr_for_view, deps, db_url),
            "symbols": await _q(_symbol_states, deps, db_url),
            "events": await _q(_upcoming_events, db_url, 7),
            "closed": await _q(_recent_closed, db_url, 10),
            "audits": await _q(_recent_audits, db_url, 40),
            "breakers": await _q(_breakers_ctx, deps, db_url, equity),
            "mode": getattr(deps, "mode", "PAPER"),
            **(await _q(_halt_ctx, deps, db_url)),
            **(await _rungs_ctx()),
        }
        return templates.TemplateResponse(request, "mace_live.html", ctx)

    @app.get("/mace/partials/rungs", response_class=HTMLResponse)
    async def mace_rungs(request: Request):
        return templates.TemplateResponse(
            request, "partials/mace_live_sections.html", await _rungs_ctx())

    async def _halt_partial(request: Request):
        return templates.TemplateResponse(
            request, "partials/mace_halt.html", await _q(_halt_ctx, deps, db_url))

    def _set_latch(halted: bool, kind: str) -> None:
        """AUDIT BEFORE STATE (CLAUDE.md #2): the durable mace_operations event
        lands before the latch flips. Telemetry failure must never block the
        halt itself (engine convention), so the audit is guarded — but it is
        always ATTEMPTED first."""
        ts = now_utc().isoformat(timespec="seconds")
        payload = {"division": DIVISION, "halted": halted, "ts": ts,
                   "source": "dashboard_button"}
        try:
            if deps.logger_agent is not None:
                deps.logger_agent.log_event("mace_operations", kind, payload)
        except Exception:  # noqa: BLE001
            log.exception("mace_view: %s audit failed (latch still applied)", kind)
        db.set_agent_state(DIVISION, "entry_halt",
                           {"halted": halted, "ts": ts, "source": "dashboard_button"},
                           db_url=db_url)
        log.info("%s: latch halted=%s", kind, halted)

    @app.get("/mace/partials/halt", response_class=HTMLResponse)
    async def mace_halt_state(request: Request):
        return await _halt_partial(request)

    @app.post("/mace/halt", response_class=HTMLResponse)
    async def mace_halt(request: Request):
        """Halt NEW entries (auto_execute:false semantics — manage/exits keep
        running; a resting order completes its ≤fill-wait cancel cycle)."""
        await asyncio.to_thread(_set_latch, True, "mace_ui_halt")
        return await _halt_partial(request)

    @app.post("/mace/arm", response_class=HTMLResponse)
    async def mace_arm(request: Request):
        """Clear the entry-halt latch (entries resume at the next eval slot)."""
        await asyncio.to_thread(_set_latch, False, "mace_ui_arm")
        return await _halt_partial(request)
