"""Robinhood MACE cockpit — FastAPI routes for the /mace dashboard (plan §
Observability / Dashboard v1).

Wire-up (one line in trading_corp/web/routes.py `register(app)`):

    from trading_corp.web import mace_view
    mace_view.register(app)

Routes:

    GET /mace                    full cockpit page (shell)
    GET /mace/partials/rungs     open-rungs table fragment (htmx-polled ~30s)

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

MACE is zero-HITL: this cockpit is observability only. There are NO approve/
reject controls (CLAUDE.md's web-app-is-the-HITL-surface rule is not engaged —
MACE has no approval gates in its order path).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from trading_corp.persistence import db
from trading_corp.utils.time import now_et

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


def register(app: FastAPI) -> None:
    templates = app.state.templates
    deps = app.state.deps
    db_url = deps.db_url

    async def _q(fn, *a):
        return await asyncio.to_thread(fn, *a)

    async def _rungs_ctx():
        return {"rungs": await _q(_open_rungs, db_url)}

    @app.get("/mace", response_class=HTMLResponse)
    async def mace_cockpit(request: Request):
        cfg = _cfg(deps)
        ctx = {
            "badge": mace_badge(deps),
            "config": _config_ctx(cfg),
            "equity": await _q(_latest_equity, db_url),
            "ivr": await _q(_latest_ivr, db_url),
            "events": await _q(_upcoming_events, db_url, 7),
            "closed": await _q(_recent_closed, db_url, 10),
            "mode": getattr(deps, "mode", "PAPER"),
            **(await _rungs_ctx()),
        }
        return templates.TemplateResponse(request, "mace_live.html", ctx)

    @app.get("/mace/partials/rungs", response_class=HTMLResponse)
    async def mace_rungs(request: Request):
        return templates.TemplateResponse(
            request, "partials/mace_live_sections.html", await _rungs_ctx())
