"""Bitunix SFP Cockpit — FastAPI routes + queries (data-readiness disciplined).

Wire-up (one line in trading_corp/web/routes.py `register(app)`):

    from trading_corp.web import sfp_cockpit_view
    sfp_cockpit_view.register(app)

Routes (all HTMX fragment-polled ~5s by the shell):

    GET /sfp                          full cockpit page (shell)
    GET /sfp/partials/header          TIER C badge + TIER A summary
    GET /sfp/partials/recon           TIER A win/avg-R  + TIER B bos-confirm
    GET /sfp/partials/state-board     TIER A bar-strips + TIER C position + TIER B armed-watch
    GET /sfp/partials/mode-split      TIER A (REAL vs CONSIDERABLE)
    GET /sfp/partials/near-miss       TIER B mock
    GET /sfp/partials/equity          TIER A equity / cum-R

★ DATA-READINESS DISCIPLINE — the whole point of this module:
  - TIER A  : reads real sources NOW. Renders an HONEST EMPTY state until rows
              exist (SFP just went live with ZERO closed trades → "—", never a
              fabricated number). Templates get `has_data: bool`.
  - TIER B  : NO real source yet (needs an observer watch-state emit). Served by
              `_mock_*()` functions, clearly named, and the template paints a
              dashed MOCK ribbon. A placeholder can NEVER read as live truth.
  - TIER C  : DANGEROUS. Every query is scoped STRICTLY to division='bitunix_sfp'
              — NEVER the shared bitunix account snapshot or corp-wide events
              (that cross-division bleed caused a false alarm). No position → an
              honest 'no SFP position' state, never borrowed from another division.
              The LIVE/PAPER badge reads ACTUAL runtime (broker.paper + auto_execute).

Every panel's context dict carries `tier` ("A"|"B"|"C") and (for B) `mock=True`
so the template renders the correct ribbon/badge.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from trading_corp.persistence import db

log = logging.getLogger(__name__)

DIVISION = "bitunix_sfp"
TP_R = 2.0                                   # fixed 2R target (build spec)
# Display symbol -> bitunix_bar_history.symbol (wire) key. Keep in one place.
SYMBOLS: dict[str, str] = {
    "BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT",
}
LIVE_SYMBOL = "BTC"                          # the only live-trading coin today
TF = "15m"
_STRAT_YAML = Path(__file__).resolve().parents[2] / "config" / "strategies.yaml"


# ──────────────────────────────────────────────────────────────────────────
# small helpers
# ──────────────────────────────────────────────────────────────────────────
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _day_start_iso() -> str:
    n = _utc_now()
    return n.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _week_start_iso() -> str:
    n = _utc_now()
    monday = (n - timedelta(days=n.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return monday.isoformat()


def _loads(s: Any) -> dict:
    if not s:
        return {}
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return {}


# ──────────────────────────────────────────────────────────────────────────
# TIER C — runtime LIVE/PAPER truth (NOT a static label; NOT record bookkeeping)
# ──────────────────────────────────────────────────────────────────────────
def _yaml_auto_execute() -> bool | None:
    """Fresh-read bitunix_sfp.auto_execute (the hot kill switch). None if unreadable."""
    try:
        import yaml
        with open(_STRAT_YAML, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return bool((raw.get(DIVISION) or {}).get("auto_execute", False))
    except Exception as e:                                    # noqa: BLE001
        log.warning("sfp_cockpit: auto_execute read failed: %s", e)
        return None


def runtime_badge(deps: Any) -> dict:
    """TIER C — the ACTUAL per-division runtime state, venue/runtime truth.

    Reads the live broker registered for division='bitunix_sfp' (its `paper`
    flag) AND the hot `auto_execute` switch. Renders LIVE only when the broker
    is real AND armed; otherwise a precise non-live reason. Never a static label.
    """
    broker = None
    dx = getattr(deps, "data_exec", None)
    brokers = getattr(dx, "brokers", None) if dx is not None else None
    if isinstance(brokers, dict):
        broker = brokers.get(DIVISION)
    paper = bool(getattr(broker, "paper", True)) if broker is not None else True
    broker_present = broker is not None
    auto = _yaml_auto_execute()
    armed = bool(auto)
    live = broker_present and (not paper) and armed
    if live:
        state, label, sub = "live", "LIVE", "REAL CAPITAL"
    elif not broker_present:
        state, label, sub = "unwired", "NO BROKER", "division not registered"
    elif paper:
        state, label, sub = "paper", "PAPER", "broker paper=True"
    elif not armed:
        state, label, sub = "disarmed", "DISARMED", "auto_execute=false"
    else:
        state, label, sub = "unknown", "UNKNOWN", "indeterminate"
    return {
        "tier": "C", "state": state, "label": label, "sub": sub,
        "paper": paper, "auto_execute": auto, "broker_present": broker_present,
    }


# ──────────────────────────────────────────────────────────────────────────
# TIER A — closed-trade metrics (HONEST EMPTY until rows exist)
# ──────────────────────────────────────────────────────────────────────────
def _closed_metrics(db_url: str) -> dict:
    """TIER A. Win@2R / avg-R / today / week / cum-R from CLOSED SFP trades.

    SQL (scoped to division='bitunix_sfp', resolved trades only):

        SELECT COUNT(*) n,
               SUM(result='win') wins,
               AVG(actual_r_multiple) avg_r,
               SUM(actual_r_multiple) cum_r
        FROM paper_trade_record
        WHERE division='bitunix_sfp' AND result IN ('win','loss');

    Reads EMPTY today (SFP has no closed trades) → has_data=False → honest "—".
    """
    with db.connect(db_url) as conn:
        agg = conn.execute(
            "SELECT COUNT(*) AS n, "
            "SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) AS wins, "
            "AVG(actual_r_multiple) AS avg_r, "
            "SUM(actual_r_multiple) AS cum_r "
            "FROM paper_trade_record "
            "WHERE division=? AND result IN ('win','loss')",
            (DIVISION,),
        ).fetchone()
        today_r = conn.execute(
            "SELECT SUM(actual_r_multiple) AS r FROM paper_trade_record "
            "WHERE division=? AND result IN ('win','loss') AND result_ts >= ?",
            (DIVISION, _day_start_iso()),
        ).fetchone()
        week_r = conn.execute(
            "SELECT SUM(actual_r_multiple) AS r FROM paper_trade_record "
            "WHERE division=? AND result IN ('win','loss') AND result_ts >= ?",
            (DIVISION, _week_start_iso()),
        ).fetchone()
    n = int(agg["n"] or 0)
    return {
        "tier": "A", "has_data": n > 0, "n": n,
        "win_pct": (round(100 * (agg["wins"] or 0) / n) if n else None),
        "avg_r": (round(agg["avg_r"], 2) if agg["avg_r"] is not None else None),
        "cum_r": (round(agg["cum_r"], 1) if agg["cum_r"] is not None else None),
        "today_r": (round(today_r["r"], 1) if today_r["r"] is not None else None),
        "week_r": (round(week_r["r"], 1) if week_r["r"] is not None else None),
    }


def _mode_split(db_url: str) -> dict:
    """TIER A. REAL vs CONSIDERABLE, tracked separately. Mode from
    extra_json.$.sfp_mode (the observer writes 'REAL'/'CONSIDERABLE').

        SELECT json_extract(extra_json,'$.sfp_mode') mode,
               COUNT(*) n, SUM(result='win') wins, AVG(actual_r_multiple) avg_r
        FROM paper_trade_record
        WHERE division='bitunix_sfp' AND result IN ('win','loss')
        GROUP BY mode;
    """
    out = {"REAL": {"n": 0, "win_pct": None, "avg_r": None},
           "CONSIDERABLE": {"n": 0, "win_pct": None, "avg_r": None}}
    with db.connect(db_url) as conn:
        rows = conn.execute(
            "SELECT json_extract(extra_json,'$.sfp_mode') AS mode, "
            "COUNT(*) AS n, "
            "SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) AS wins, "
            "AVG(actual_r_multiple) AS avg_r "
            "FROM paper_trade_record "
            "WHERE division=? AND result IN ('win','loss') GROUP BY mode",
            (DIVISION,),
        ).fetchall()
    for r in rows:
        mode = (r["mode"] or "").upper()
        if mode in out and r["n"]:
            out[mode] = {
                "n": int(r["n"]),
                "win_pct": round(100 * (r["wins"] or 0) / r["n"]),
                "avg_r": round(r["avg_r"], 2) if r["avg_r"] is not None else None,
            }
    has = out["REAL"]["n"] + out["CONSIDERABLE"]["n"] > 0
    return {"tier": "A", "has_data": has, "modes": out}


def _equity_curve(db_url: str) -> dict:
    """TIER A. Cumulative-R equity curve from closed SFP trades, time-ordered.

        SELECT result_ts, actual_r_multiple FROM paper_trade_record
        WHERE division='bitunix_sfp' AND result IN ('win','loss')
              AND actual_r_multiple IS NOT NULL
        ORDER BY result_ts ASC;
    """
    with db.connect(db_url) as conn:
        rows = conn.execute(
            "SELECT result_ts, actual_r_multiple AS r FROM paper_trade_record "
            "WHERE division=? AND result IN ('win','loss') "
            "AND actual_r_multiple IS NOT NULL ORDER BY result_ts ASC",
            (DIVISION,),
        ).fetchall()
    cum, pts = 0.0, []
    for r in rows:
        cum += float(r["r"])
        pts.append(round(cum, 3))
    line_d, area_d = _spark_paths(pts)
    return {"tier": "A", "has_data": bool(pts), "n_closed": len(pts),
            "cum_r": round(cum, 1) if pts else None, "points": pts,
            "line_d": line_d, "area_d": area_d}


def _spark_paths(pts: list[float], w: float = 340.0, h: float = 90.0) -> tuple[str, str]:
    """Build (line, area) SVG path strings for the equity sparkline. Empty -> ('','')."""
    if len(pts) < 2:
        return "", ""
    lo, hi = min(pts), max(pts)
    rng = (hi - lo) or 1.0
    pad = 6.0
    step = w / (len(pts) - 1)
    xy = [(round(i * step, 1), round(pad + (hi - p) / rng * (h - 2 * pad), 1))
          for i, p in enumerate(pts)]
    line = "M" + " L".join(f"{x},{y}" for x, y in xy)
    area = line + f" L{xy[-1][0]},{h} L{xy[0][0]},{h} Z"
    return line, area


def _bar_strip(db_url: str, wire: str, n: int = 40) -> list[dict]:
    """TIER A. Latest N 15m bars for ONE symbol (symbol-keyed — filter symbol).

        SELECT ts_ms, open, high, low, close FROM bitunix_bar_history
        WHERE symbol=? AND timeframe='15m' ORDER BY ts_ms DESC LIMIT ?;
    """
    with db.connect(db_url) as conn:
        rows = conn.execute(
            "SELECT ts_ms, open, high, low, close FROM bitunix_bar_history "
            "WHERE symbol=? AND timeframe=? ORDER BY ts_ms DESC LIMIT ?",
            (wire, TF, n),
        ).fetchall()
    return [{"ts_ms": r["ts_ms"], "o": r["open"], "h": r["high"],
             "l": r["low"], "c": r["close"]} for r in reversed(rows)]


def _latest_close(db_url: str, wire: str) -> float | None:
    with db.connect(db_url) as conn:
        r = conn.execute(
            "SELECT close FROM bitunix_bar_history WHERE symbol=? AND timeframe=? "
            "ORDER BY ts_ms DESC LIMIT 1", (wire, TF),
        ).fetchone()
    return float(r["close"]) if r else None


def _loop_heartbeat(db_url: str, wire: str) -> dict:
    """Loop heartbeat — best-effort PROXY (latest bar inserted_at age).

    NOTE: this is the bar-archiver's freshness, NOT a true SFP-loop tick. A real
    'loop last evaluated' needs an observer heartbeat emit (agent_state write per
    process_once). Marked proxy=True so the UI labels it honestly.
    """
    with db.connect(db_url) as conn:
        r = conn.execute(
            "SELECT inserted_at FROM bitunix_bar_history WHERE symbol=? AND timeframe=? "
            "ORDER BY ts_ms DESC LIMIT 1", (wire, TF),
        ).fetchone()
    if not r or not r["inserted_at"]:
        return {"proxy": True, "age_min": None, "stale": True}
    try:
        ts = datetime.fromisoformat(str(r["inserted_at"]).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_min = int((_utc_now() - ts).total_seconds() // 60)
    except Exception:                                        # noqa: BLE001
        return {"proxy": True, "age_min": None, "stale": True}
    return {"proxy": True, "age_min": age_min, "stale": age_min is None or age_min > 20}


# ──────────────────────────────────────────────────────────────────────────
# TIER C — open SFP position (STRICTLY division='bitunix_sfp'; no shared fallback)
# ──────────────────────────────────────────────────────────────────────────
def _open_sfp_position(db_url: str) -> dict | None:
    """TIER C. The open SFP position — scoped HARD to division='bitunix_sfp'.

        SELECT order_id, symbol, side, qty, entry_reference_price, stop_price,
               tp_price, tp_r_multiple, extra_json, ts, execution_mode
        FROM paper_trade_record
        WHERE division='bitunix_sfp' AND result IS NULL
        ORDER BY ts DESC LIMIT 1;

    NO fallback to the shared bitunix account snapshot or corp-wide events. If
    this returns None the caller renders an honest 'no SFP position' state.
    """
    with db.connect(db_url) as conn:
        r = conn.execute(
            "SELECT order_id, symbol, side, qty, entry_reference_price, stop_price, "
            "tp_price, tp_r_multiple, extra_json, ts, execution_mode "
            "FROM paper_trade_record "
            "WHERE division=? AND result IS NULL ORDER BY ts DESC LIMIT 1",
            (DIVISION,),
        ).fetchone()
    if not r:
        return None
    return {
        "order_id": r["order_id"], "symbol": r["symbol"], "side": r["side"],
        "qty": r["qty"], "entry": r["entry_reference_price"], "stop": r["stop_price"],
        "tp": r["tp_price"], "tp_r": r["tp_r_multiple"], "ts": r["ts"],
        "execution_mode": r["execution_mode"], "extra": _loads(r["extra_json"]),
    }


def _r_journey(db_url: str, pos: dict) -> dict:
    """TIER A. R-journey for the open SFP position. entry/stop from the row,
    target = entry + 2R, current = latest 15m close (symbol-keyed), MFE = running
    max((high-entry)/R) since entry. CSS handles the ~5s easing — no JS loop."""
    wire = pos["symbol"]
    # accept either wire ('BTCUSDT') or display ('BTC/USDT.P' -> map) symbols
    if wire not in SYMBOLS.values():
        wire = SYMBOLS.get(str(pos["symbol"]).split("/")[0].upper(), wire)
    entry = float(pos["entry"] or 0.0)
    stop = float(pos["stop"] or 0.0)
    r_unit = entry - stop                       # long: positive
    target = float(pos["tp"] or (entry + TP_R * r_unit))
    current = _latest_close(db_url, wire)
    out = {"tier": "A", "symbol": pos["symbol"], "entry": entry, "stop": stop,
           "target": target, "current": current, "r_unit": r_unit,
           "unreal_r": None, "mfe_r": None, "to_target_pct": None,
           "to_stop_pct": None, "marker_pos": None, "crossed_breakeven": None}
    if current is None or r_unit <= 0:
        return out
    out["unreal_r"] = round((current - entry) / r_unit, 2)
    out["crossed_breakeven"] = current >= entry
    out["to_target_pct"] = (round(100 * (current - entry) / (target - entry))
                            if target > entry else None)
    out["to_stop_pct"] = round(100 * (entry - current) / r_unit)
    # marker position 0..100 along stop->target axis (for spatial placement)
    span = target - stop
    out["marker_pos"] = max(0, min(100, round(100 * (current - stop) / span))) if span > 0 else None
    # MFE high-water mark since entry
    try:
        entry_ts_ms = int(_entry_ts_ms(pos))
        with db.connect(db_url) as conn:
            hi = conn.execute(
                "SELECT MAX(high) AS h FROM bitunix_bar_history "
                "WHERE symbol=? AND timeframe=? AND ts_ms >= ?",
                (wire, TF, entry_ts_ms),
            ).fetchone()
        if hi and hi["h"] is not None:
            out["mfe_r"] = round((float(hi["h"]) - entry) / r_unit, 2)
    except Exception as e:                                   # noqa: BLE001
        log.warning("sfp_cockpit: MFE compute failed: %s", e)
    return out


def _entry_ts_ms(pos: dict) -> int:
    """Entry bar ts in ms — from the row ts (ISO) for the MFE window."""
    ts = datetime.fromisoformat(str(pos["ts"]).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return int(ts.timestamp() * 1000)


def _tp_venue_chip(pos: dict) -> dict:
    """NEW. TP-@-venue chip from the position's extra_json.bracket_tp_order_id.
    Present → '✓ id <n>'. Empty on an OPEN position → '✗ TP MISSING' (loud red) —
    surfaces the exact blocker SFP just had (TP never placed at the venue)."""
    tp_id = (pos.get("extra") or {}).get("bracket_tp_order_id")
    if tp_id:
        return {"state": "ok", "text": f"✓ TP @ venue · id {tp_id}", "tp_order_id": tp_id}
    return {"state": "bad", "text": "✗ TP MISSING", "tp_order_id": None}


def _oco_health_chip(pos: dict) -> dict:
    """NEW. OCO/orphan health from record truth: an OPEN position should carry a
    venue stop (B1, always attached) AND a resting TP leg (bracket_tp_order_id).
      both present  → 'venue: 1 stop + 1 tp ✓'
      tp missing    → 'ORPHAN STOP' (red) — stop-only, the edge-inverting state.
    Authoritative orphan detection (a TP with no position, etc.) is the
    reconciler's signed-venue job; this is the record-side early-warning."""
    extra = pos.get("extra") or {}
    has_tp = bool(extra.get("bracket_tp_order_id"))
    has_stop = pos.get("stop") is not None        # B1 stop attached at entry
    if has_stop and has_tp:
        return {"state": "ok", "text": "OCO ✓ · 1 stop + 1 tp"}
    if has_stop and not has_tp:
        return {"state": "bad", "text": "ORPHAN STOP · no TP leg"}
    return {"state": "warn", "text": "OCO unknown"}


# ──────────────────────────────────────────────────────────────────────────
# TIER B — MOCK (no real source yet; needs observer watch-state emit)
# ──────────────────────────────────────────────────────────────────────────
# BLOCKED on observer watch-state emit: fired_bar_ts, mode, swept_level,
# swept_wick, bos_watch_level, status. Wire these to real reads when it ships.
def _mock_armed_watch(symbol: str) -> dict:
    """MOCK — per-coin SFP-armed watch overlay + countdown. Demo only."""
    return {
        "tier": "B", "mock": True, "symbol": symbol,
        "mode": "CONSIDERABLE", "swept_wick": 2409.2, "bos_watch_level": 2441.0,
        "expires_in": "06:49:01", "status": "ARMED",
    }


def _mock_near_miss() -> dict:
    """MOCK — near-miss list (armed setups that invalidated / timed out)."""
    return {
        "tier": "B", "mock": True, "convert_fail_pct": 58,
        "rows": [
            {"mode": "REAL", "symbol": "XRP", "status": "INVALIDATED", "level": "0.5120", "ago": "14m ago", "exec": "PAPER"},
            {"mode": "CONS", "symbol": "BTC", "status": "TIMED OUT", "level": "63,640", "ago": "2h ago", "exec": "PAPER"},
            {"mode": "REAL", "symbol": "SOL", "status": "INVALIDATED", "level": "136.90", "ago": "3h ago", "exec": "PAPER"},
            {"mode": "CONS", "symbol": "ETH", "status": "TIMED OUT", "level": "2,388.0", "ago": "5h ago", "exec": "PAPER"},
            {"mode": "REAL", "symbol": "BTC", "status": "INVALIDATED", "level": "62,910", "ago": "7h ago", "exec": "PAPER"},
        ],
    }


def _mock_bos_confirm() -> dict:
    """MOCK — BOS-confirm rate vs backtest band (needs the watch-state emit to
    count armed→confirmed)."""
    return {"tier": "B", "mock": True, "rate_pct": 31, "band": "27–65th", "status": "diverging"}


# ──────────────────────────────────────────────────────────────────────────
# context builders (compose per fragment)
# ──────────────────────────────────────────────────────────────────────────
_CH_W, _CH_H, _PAD = 460.0, 120.0, 10.0


def _chart_geom(bars: list[dict], levels: list | None = None,
                marker_price: float | None = None, marker_color: str = "#5b9dff") -> dict:
    """Scale OHLC bars + overlay levels into SVG coords (so Jinja stays dumb).
    levels = [(price, color, dash), ...]. Returns candles/lines/marker + viewbox."""
    levels = levels or []
    prices: list[float] = []
    for b in bars:
        prices += [b["h"], b["l"]]
    for (p, _c, _d) in levels:
        if p:
            prices.append(float(p))
    if marker_price:
        prices.append(float(marker_price))
    if not prices:
        return {"candles": [], "lines": [], "marker": None, "w": _CH_W, "h": _CH_H}
    lo, hi = min(prices), max(prices)
    rng = (hi - lo) or 1.0

    def y(p: float) -> float:
        return round(_PAD + (hi - float(p)) / rng * (_CH_H - 2 * _PAD), 1)

    n = len(bars) or 1
    step = (_CH_W - 2 * _PAD) / max(1, n)
    candles = []
    for i, b in enumerate(bars):
        x = round(_PAD + step * (i + 0.5), 1)
        candles.append({"x": x, "yh": y(b["h"]), "yl": y(b["l"]),
                        "yo": y(b["o"]), "yc": y(b["c"]), "up": b["c"] >= b["o"]})
    lines = [{"y": y(p), "color": c, "dash": d} for (p, c, d) in levels if p]
    marker = ({"x": round(_CH_W - _PAD, 1), "y": y(marker_price), "color": marker_color}
              if marker_price else None)
    return {"candles": candles, "lines": lines, "marker": marker, "w": _CH_W, "h": _CH_H}


def _coin_state(db_url: str, display: str, pos: dict | None) -> dict:
    """Per-coin card context. The LIVE coin with an open position gets the
    TIER-A R-journey + TIER-C chips; the rest get monitor-only bar strips. The
    armed-watch overlay (if any) is TIER B mock until the emit ships."""
    wire = SYMBOLS[display]
    is_live_coin = (display == LIVE_SYMBOL)
    has_pos = bool(pos) and (
        str(pos["symbol"]).upper().startswith(display) or pos["symbol"] == wire)
    card = {
        "symbol": display, "wire": wire, "is_live_coin": is_live_coin,
        "bars": _bar_strip(db_url, wire),
        "heartbeat": _loop_heartbeat(db_url, wire),
        "state": "in_trade" if has_pos else ("monitoring"),
        "exec_tag": "LIVE" if is_live_coin else "MONITOR",
    }
    if has_pos:
        rj = _r_journey(db_url, pos)                         # TIER A
        card["rj"] = rj
        card["tp_chip"] = _tp_venue_chip(pos)               # NEW
        card["oco_chip"] = _oco_health_chip(pos)            # NEW
        card["mode"] = (pos.get("extra") or {}).get("sfp_mode", "REAL")
        mk_color = "#34d399" if rj.get("crossed_breakeven") else "#5b9dff"
        card["chart"] = _chart_geom(
            card["bars"],
            [(rj["target"], "#34d399", "3 4"), (rj["entry"], "#f5b53d", "5 4"),
             (rj["stop"], "#fb5e6b", "3 4")],
            rj["current"], mk_color)
    else:
        armed = _mock_armed_watch(display) if display == "ETH" else None
        card["armed"] = armed
        levels = ([(armed["bos_watch_level"], "#5b9dff", "2 4"),
                   (armed["swept_wick"], "#f5b53d", "5 4")] if armed else [])
        card["chart"] = _chart_geom(card["bars"], levels)
    return card


# ──────────────────────────────────────────────────────────────────────────
# routes
# ──────────────────────────────────────────────────────────────────────────
def register(app: FastAPI) -> None:
    templates = app.state.templates
    deps = app.state.deps
    db_url = deps.db_url

    async def _q(fn, *a):
        return await asyncio.to_thread(fn, *a)

    @app.get("/sfp", response_class=HTMLResponse)
    async def sfp_cockpit(request: Request):
        # Initial full paint — fragments then HTMX-poll themselves every ~5s.
        ctx = {
            "badge": runtime_badge(deps),
            "metrics": await _q(_closed_metrics, db_url),
            # _recon.html (and its partial route) read top-level `bos` + `metrics`;
            # the full-page paint must provide `bos` top-level too (was nested in
            # an unused `recon` dict -> UndefinedError 'bos' on the full page).
            "bos": _mock_bos_confirm(),
            "modes": await _q(_mode_split, db_url),
            "equity": await _q(_equity_curve, db_url),
            "near_miss": _mock_near_miss(),
            "coins": await _build_board(),
        }
        return templates.TemplateResponse(request, "sfp_cockpit.html", ctx)

    async def _build_board() -> list[dict]:
        pos = await _q(_open_sfp_position, db_url)            # TIER C, scoped
        return [await _q(_coin_state, db_url, d, pos) for d in SYMBOLS]

    @app.get("/sfp/partials/header", response_class=HTMLResponse)
    async def sfp_header(request: Request):
        return templates.TemplateResponse(
            request, "sfp_cockpit/_header.html",
            {"badge": runtime_badge(deps), "metrics": await _q(_closed_metrics, db_url)},
        )

    @app.get("/sfp/partials/recon", response_class=HTMLResponse)
    async def sfp_recon(request: Request):
        m = await _q(_closed_metrics, db_url)
        return templates.TemplateResponse(
            request, "sfp_cockpit/_recon.html",
            {"metrics": m, "bos": _mock_bos_confirm()},     # A + B
        )

    @app.get("/sfp/partials/state-board", response_class=HTMLResponse)
    async def sfp_state_board(request: Request):
        return templates.TemplateResponse(
            request, "sfp_cockpit/_state_board.html", {"coins": await _build_board()},
        )

    @app.get("/sfp/partials/mode-split", response_class=HTMLResponse)
    async def sfp_mode_split(request: Request):
        return templates.TemplateResponse(
            request, "sfp_cockpit/_mode_split.html", {"modes": await _q(_mode_split, db_url)},
        )

    @app.get("/sfp/partials/near-miss", response_class=HTMLResponse)
    async def sfp_near_miss(request: Request):
        return templates.TemplateResponse(
            request, "sfp_cockpit/_near_miss.html", {"near_miss": _mock_near_miss()},
        )

    @app.get("/sfp/partials/equity", response_class=HTMLResponse)
    async def sfp_equity(request: Request):
        return templates.TemplateResponse(
            request, "sfp_cockpit/_equity.html", {"equity": await _q(_equity_curve, db_url)},
        )

    log.info("SFP cockpit routes registered at /sfp")
