"""Iron Condor live trades view — debugging surface for real-time monitoring.

Five query functions plus a TTL-cached Greeks fetcher. The web route at
`/telemetry/iron_condor` (see `web/routes.py`) calls these. Optimised
for information density, not aesthetics — the user is reading this to
catch bad behaviour as it happens, not to admire the layout.

Sections (mirrors the user spec):
  1. open_positions_detail(broker, db_url)
  2. recent_activity(db_url, limit=50)
  3. pending_combos_view(registry)
  4. todays_scan_results(broker, db_url, universe, day)
  5. strategy_health(broker, db_url, ic_division, ic_strategy, registry, batcher)
  6. recent_closed_combos(db_url, limit=10)

Aggregations (win rate, expectancy, mean P&L) live in `ic_telemetry.py`
— they're explicitly NOT in scope here; this surface is per-position
debug detail. The CLI covers the rollups.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from trading_corp.persistence import db
from trading_corp.utils.iv import calc_atm_iv, calc_iv_rank

log = logging.getLogger(__name__)


STRATEGY_SLUG = "robinhood_joint_iron_condor"
DIVISION_SLUG = "robinhood_joint"

# Module-level TTL cache for live Greeks lookups. Sized small (one entry
# per open option_id), keyed by option_id, value = (greeks_dict, ts).
# 60s TTL aligns with the user's "cache aggressively so we're not
# hammering the broker on every page refresh" constraint.
_GREEKS_TTL_SEC = 60.0
_greeks_cache: dict[str, tuple[dict, float]] = {}
_greeks_lock = asyncio.Lock()


async def _cached_get_option_greeks(
    broker: Any, option_id: str,
) -> dict[str, float | None]:
    """Wrap `broker.get_option_greeks` with a 60s in-memory cache.

    Returns the all-None shape on any failure so callers can render a
    "—" without short-circuiting the rest of the section.
    """
    if not option_id or broker is None:
        return _empty_greeks()
    now = time.monotonic()
    async with _greeks_lock:
        cached = _greeks_cache.get(option_id)
        if cached is not None:
            greeks, ts = cached
            if now - ts < _GREEKS_TTL_SEC:
                return greeks
    try:
        greeks = await broker.get_option_greeks(option_id)
    except Exception as e:
        log.warning("ic_live_view: get_option_greeks(%s) failed: %s",
                    option_id, e)
        greeks = _empty_greeks()
    async with _greeks_lock:
        _greeks_cache[option_id] = (greeks, time.monotonic())
    return greeks


def _empty_greeks() -> dict[str, float | None]:
    return {"delta": None, "gamma": None, "theta": None,
            "vega": None, "iv": None, "mark_price": None}


# ---------------------------------------------------------------------------
# Section 1: open positions detail (live)
# ---------------------------------------------------------------------------


async def open_positions_detail(
    *,
    broker: Any,
    db_url: str = "sqlite:///data/trading_corp.db",
    strategy_slug: str = STRATEGY_SLUG,
) -> list[dict]:
    """For each open IC in `agent_state.open_ics`, fetch live Greeks
    and compute current state + approaching-trigger distances.

    Returns one dict per open IC; empty list when there are none. The
    template iterates this without further transformation.
    """
    rec = db.load_agent_state(strategy_slug, "state", db_url=db_url)
    if rec is None:
        return []
    state, _ts = rec
    if not isinstance(state, dict):
        return []
    open_ics = state.get("open_ics") or {}
    cb = state.get("circuit_breaker") or {}

    out: list[dict] = []
    for combo_id, ic in open_ics.items():
        try:
            view = await _build_open_position_view(
                broker, combo_id, ic, cb,
            )
        except Exception as e:
            log.exception(
                "ic_live_view: open_positions_detail failed for combo %s",
                combo_id,
            )
            view = {
                "combo_id": combo_id,
                "short_id": combo_id[:8],
                "error": str(e),
            }
        out.append(view)
    out.sort(key=lambda v: v.get("opened_ts") or "", reverse=True)
    return out


async def _build_open_position_view(
    broker: Any, combo_id: str, ic: dict, cb: dict,
) -> dict:
    symbol = ic.get("symbol") or "?"
    expiry = ic.get("expiration") or ""
    credit_at_entry = float(ic.get("credit_at_entry") or 0)
    wing_width = float(ic.get("wing_width") or 0)
    contracts = int(ic.get("contracts") or 0)
    adj_count = int(ic.get("adjustment_count") or 0)
    opened_ts = ic.get("opened_ts") or ""
    ivr_entry = ic.get("ivr_at_entry")

    today = datetime.now(timezone.utc).date()
    try:
        dte_remaining = max(0, (date.fromisoformat(expiry) - today).days)
    except (ValueError, TypeError):
        dte_remaining = None

    # Live Greeks per leg (4 legs).
    leg_roles = ("short_put", "long_put", "short_call", "long_call")
    leg_greeks: dict[str, dict] = {}
    for role in leg_roles:
        opt_id = ic.get(f"{role}_option_id")
        leg_greeks[role] = await _cached_get_option_greeks(broker, opt_id)

    # Spot via broker.quote.
    spot: float | None = None
    if broker is not None:
        try:
            q = await broker.quote(symbol)
            spot = float(q) if q else None
        except Exception:
            spot = None

    # Current close cost (per share) = sum(short marks) - sum(long marks).
    def _mark(role):
        return leg_greeks[role].get("mark_price")
    sp_m = _mark("short_put")
    lp_m = _mark("long_put")
    sc_m = _mark("short_call")
    lc_m = _mark("long_call")
    close_cost = None
    current_combo_mark = None
    if None not in (sp_m, lp_m, sc_m, lc_m):
        close_cost = float(sc_m) + float(sp_m) - float(lc_m) - float(lp_m)
        current_combo_mark = credit_at_entry - close_cost   # MTM per share

    pnl_per_share = current_combo_mark
    pnl_dollars = (pnl_per_share * 100.0 * contracts) if pnl_per_share is not None else None
    pnl_pct_of_credit = (
        (pnl_per_share / credit_at_entry * 100.0)
        if (pnl_per_share is not None and credit_at_entry > 0)
        else None
    )
    max_loss_per_share = wing_width - credit_at_entry
    pnl_pct_of_max_loss = (
        (pnl_per_share / max_loss_per_share * -100.0)   # negative → fraction of max loss realized
        if (pnl_per_share is not None and max_loss_per_share > 0)
        else None
    )

    # Tested side: replicate the strategy's identification logic.
    sc_entry = ic.get("short_call_delta_at_entry")
    sp_entry = ic.get("short_put_delta_at_entry")
    sc_cur = leg_greeks["short_call"].get("delta")
    sp_cur = leg_greeks["short_put"].get("delta")
    tested_side = _identify_tested_side_for_view(
        sc_entry=sc_entry, sp_entry=sp_entry,
        sc_cur=sc_cur, sp_cur=sp_cur,
        band=0.05,
    )

    # Cadence the Position Manager would use for this IC.
    cadence = _cadence_for_deltas(sc_cur, sp_cur)

    # Approaching triggers.
    profit_target_per_share = credit_at_entry * 0.50
    distance_to_profit = (
        profit_target_per_share - pnl_per_share
        if pnl_per_share is not None else None
    )
    hard_stop_per_share = credit_at_entry * 2.0
    distance_to_hard_stop = (
        pnl_per_share - (-hard_stop_per_share)
        if pnl_per_share is not None else None
    )

    dte_to_21 = (dte_remaining - 21) if dte_remaining is not None else None
    dte_to_7 = (dte_remaining - 7) if dte_remaining is not None else None

    # Ex-div: only show if we have a calendar reference. We accept it
    # being unavailable for v1 (strategy itself reads it; this view
    # surfaces the same lookup).
    ex_div_view = _ex_div_view_for_symbol(symbol)
    short_call_above_ex_div_delta = (
        sc_cur is not None and abs(float(sc_cur)) > 0.25
    )

    # Circuit-breaker paused_until: applied to all positions, but the
    # countdown is a per-strategy state so we surface it on each row.
    paused_until = cb.get("paused_until")
    days_until_resume = None
    if paused_until:
        try:
            dt = datetime.fromisoformat(paused_until)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            delta = (dt - datetime.now(timezone.utc)).total_seconds()
            days_until_resume = max(0.0, delta / 86400.0)
        except (TypeError, ValueError):
            days_until_resume = None

    # Distance from spot for the two short strikes.
    short_call_strike = ic.get("short_call_strike")
    short_put_strike = ic.get("short_put_strike")
    sc_dist_dollars = (
        spot - short_call_strike
        if (spot is not None and short_call_strike is not None) else None
    )
    sp_dist_dollars = (
        spot - short_put_strike
        if (spot is not None and short_put_strike is not None) else None
    )
    sc_dist_pct = (
        sc_dist_dollars / spot * 100.0
        if (sc_dist_dollars is not None and spot) else None
    )
    sp_dist_pct = (
        sp_dist_dollars / spot * 100.0
        if (sp_dist_dollars is not None and spot) else None
    )

    # Session P&L contribution.
    session_start_mark = ic.get("session_start_mark")
    session_start_date = ic.get("session_start_date")
    session_pnl_per_share = (
        current_combo_mark - session_start_mark
        if (current_combo_mark is not None and session_start_mark is not None)
        else None
    )
    session_pnl_dollars = (
        session_pnl_per_share * 100.0 * contracts
        if session_pnl_per_share is not None else None
    )

    # Leg breakdown for the entry-context block.
    legs = []
    for role in leg_roles:
        gk = leg_greeks[role]
        legs.append({
            "role": role,
            "strike": ic.get(f"{role}_strike"),
            "option_type": "put" if "put" in role else "call",
            "side": "buy" if "long" in role else "sell",
            "position_effect": "open",
            "entry_delta": ic.get(f"{role}_delta_at_entry"),
            "current_delta": gk.get("delta"),
            "current_mark": gk.get("mark_price"),
            "current_iv": gk.get("iv"),
            "option_id": ic.get(f"{role}_option_id"),
        })

    return {
        "combo_id": combo_id,
        "short_id": combo_id[:8],
        "symbol": symbol,
        "opened_ts": opened_ts,
        "expiration": expiry,
        "dte_remaining": dte_remaining,
        "adjustment_count": adj_count,
        "ivr_at_entry": ivr_entry,
        "credit_at_entry_per_share": credit_at_entry,
        "credit_at_entry_dollars": credit_at_entry * 100.0 * contracts,
        "wing_width": wing_width,
        "contracts": contracts,
        "legs": legs,
        "spot": spot,
        "short_call_strike": short_call_strike,
        "short_call_distance_dollars": sc_dist_dollars,
        "short_call_distance_pct": sc_dist_pct,
        "short_call_current_delta": sc_cur,
        "short_put_strike": short_put_strike,
        "short_put_distance_dollars": sp_dist_dollars,
        "short_put_distance_pct": sp_dist_pct,
        "short_put_current_delta": sp_cur,
        "current_combo_mark_per_share": current_combo_mark,
        "current_close_cost_per_share": close_cost,
        "pnl_per_share": pnl_per_share,
        "pnl_dollars": pnl_dollars,
        "pnl_pct_of_credit": pnl_pct_of_credit,
        "pnl_pct_of_max_loss": pnl_pct_of_max_loss,
        "tested_side": tested_side,
        "position_manager_cadence_sec": cadence,
        "distance_to_profit_per_share": distance_to_profit,
        "distance_to_hard_stop_per_share": distance_to_hard_stop,
        "dte_distance_to_21": dte_to_21,
        "dte_distance_to_7": dte_to_7,
        "ex_div_next": ex_div_view.get("next_ex_date"),
        "ex_div_trading_days_until": ex_div_view.get("trading_days_until"),
        "ex_div_short_call_at_risk": short_call_above_ex_div_delta,
        "days_until_resume": days_until_resume,
        "session_start_mark": session_start_mark,
        "session_start_date": session_start_date,
        "session_pnl_per_share": session_pnl_per_share,
        "session_pnl_dollars": session_pnl_dollars,
    }


def _identify_tested_side_for_view(
    *, sc_entry, sp_entry, sc_cur, sp_cur, band: float,
) -> str:
    """Mirrors `RobinhoodJointIronCondorAgent._identify_tested_side`.

    Replicated here so the live view shows the same answer the strategy
    sees, even when the strategy hasn't run yet this tick.
    """
    if None in (sc_entry, sp_entry, sc_cur, sp_cur):
        return "neither"
    sc_e, sp_e = float(sc_entry), float(sp_entry)
    sc_c, sp_c = float(sc_cur), float(sp_cur)
    call_quiet = abs(sc_c - sc_e) < band
    put_quiet = abs(sp_c - sp_e) < band
    if call_quiet and put_quiet:
        return "neither"
    call_drift = abs(sc_c) - abs(sc_e)
    put_drift = abs(sp_c) - abs(sp_e)
    if call_drift > 0 and put_drift > 0:
        return "call" if abs(sc_c) >= abs(sp_c) else "put"
    if call_drift > 0:
        return "call"
    if put_drift > 0:
        return "put"
    return "neither"


def _cadence_for_deltas(sc_cur, sp_cur) -> int:
    """Reproduces the cadence logic from the strategy's `_compute_cadence`."""
    deltas = []
    for v in (sc_cur, sp_cur):
        if v is None:
            continue
        deltas.append(abs(float(v)))
    if not deltas:
        return 1800
    worst = max(deltas)
    if worst >= 0.30:
        return 300
    if worst >= 0.25:
        return 900
    return 1800


def _ex_div_view_for_symbol(symbol: str) -> dict:
    try:
        from trading_corp.data.ex_dividend_calendar import ExDividendCalendar
        cal = ExDividendCalendar.load()
        nxt = cal.next_ex_date(symbol)
        if nxt is None:
            return {"next_ex_date": None, "trading_days_until": None}
        today = datetime.now(timezone.utc).date()
        # Counting business days approximately (no holiday subtraction
        # — same convention as `is_within_window` in step-4 module).
        days = (nxt - today).days
        if days < 0:
            return {"next_ex_date": nxt.isoformat(), "trading_days_until": None}
        from trading_corp.data.ex_dividend_calendar import _business_days_between
        td = _business_days_between(today, nxt)
        return {"next_ex_date": nxt.isoformat(), "trading_days_until": td}
    except Exception as e:
        log.debug("ic_live_view: ex_div lookup for %s failed: %s", symbol, e)
        return {"next_ex_date": None, "trading_days_until": None}


# ---------------------------------------------------------------------------
# Section 2: recent activity feed
# ---------------------------------------------------------------------------


def recent_activity(
    *,
    db_url: str = "sqlite:///data/trading_corp.db",
    limit: int = 50,
    strategy_slug: str = STRATEGY_SLUG,
) -> list[dict]:
    """Last N audit_event rows whose actor matches the IC strategy OR
    whose payload tags it.

    The orchestration emits rows under several actors:
      - `robinhood_joint_iron_condor` (strategy module direct writes,
        e.g. `ic_lifecycle_closed`)
      - `data_exec` (combo_filled / combo_unfilled / dry_run_skip_combo)
      - `pending_combo_registry` (board_combo_approved / board_combo_rejected)
    To capture all of them, we filter on `payload_json LIKE %strategy%`
    in addition to the actor-name match. Best-effort; reads the latest
    `limit` rows from the union.
    """
    with db.connect(db_url) as conn:
        rows = conn.execute(
            """
            SELECT ts, actor, kind, payload_json
            FROM audit_event
            WHERE actor = ? OR payload_json LIKE ?
            ORDER BY ts DESC
            LIMIT ?
            """,
            (strategy_slug, f"%{strategy_slug}%", int(limit)),
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        try:
            payload = json.loads(r["payload_json"])
        except (TypeError, ValueError):
            payload = {"_raw": r["payload_json"]}
        if not isinstance(payload, dict):
            payload = {"_raw": payload}
        out.append({
            "ts": r["ts"],
            "actor": r["actor"],
            "kind": r["kind"],
            "severity": payload.get("severity") or payload.get("audit_severity"),
            "combo_id": payload.get("combo_id"),
            "symbol": payload.get("symbol") or payload.get("underlying"),
            "payload": payload,
        })
    return out


# ---------------------------------------------------------------------------
# Section 3: pending approvals
# ---------------------------------------------------------------------------


def pending_combos_view(
    *,
    registry: Any,
    batcher: Any = None,
) -> dict:
    """Wraps `PendingComboRegistry.list_pending()` into a view-ready
    shape. If `batcher` is provided, includes its pending count too.
    """
    out: dict[str, Any] = {
        "entries": [],
        "telegram_queue": {
            "pending_count": 0,
            "oldest_age_sec": None,
        },
    }
    if registry is None:
        return out
    now = datetime.now(timezone.utc)
    entries: list[dict] = []
    for e in registry.list_pending():
        age_sec = (now - e.added_at).total_seconds()
        legs = []
        for o in e.orders:
            ex = o.extra or {}
            legs.append({
                "role": ex.get("combo_role"),
                "side": o.side,
                "strike": ex.get("strike"),
                "option_type": ex.get("option_type"),
                "position_effect": ex.get("position_effect"),
                "limit_price": o.limit_price,
                "expiration": ex.get("expiration"),
            })
        entries.append({
            "combo_id": e.combo_id,
            "short_id": e.combo_id[:8],
            "intent": e.intent,
            "symbol": e.underlying,
            "direction": e.direction,
            "net_limit_price": e.net_limit_price,
            "contracts": int(e.orders[0].qty) if e.orders else None,
            "added_at": e.added_at.isoformat(),
            "age_sec": age_sec,
            "legs": legs,
            "detail_url": f"/approvals/combos/{e.combo_id}",
        })
    entries.sort(key=lambda x: x["age_sec"], reverse=True)
    out["entries"] = entries

    if batcher is not None:
        try:
            out["telegram_queue"]["pending_count"] = batcher.pending_count
        except Exception:
            pass

    return out


# ---------------------------------------------------------------------------
# Section 4: today's scan results
# ---------------------------------------------------------------------------


async def todays_scan_results(
    *,
    broker: Any,
    db_url: str = "sqlite:///data/trading_corp.db",
    universe: list[str] | None = None,
    day: date | None = None,
    strategy_slug: str = STRATEGY_SLUG,
) -> list[dict]:
    """Per-symbol view of today's scan outcomes.

    For each symbol in `universe`: scan-filter status today (from
    agent_state.scan_telemetry), live IVR, ATM IV, term-structure spread
    (front-month minus 60-90 DTE ATM IV). Network-bound; cache aware
    via `calc_iv_rank` / `calc_atm_iv` (yfinance-backed, end-of-day).
    """
    if universe is None:
        universe = ["SPY", "QQQ", "IWM", "GLD", "TLT"]
    if day is None:
        day = datetime.now(timezone.utc).date()

    # Pull scan_telemetry for today.
    rec = db.load_agent_state(strategy_slug, "state", db_url=db_url)
    state = rec[0] if rec else {}
    scan_telemetry = (state or {}).get("scan_telemetry", {})
    today_telemetry = scan_telemetry.get(day.isoformat(), {})

    # Pull today's combo_proposed events per symbol (best-effort).
    start_ts = datetime(day.year, day.month, day.day, tzinfo=timezone.utc).isoformat()
    end_ts = (datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
              + timedelta(days=1)).isoformat()
    with db.connect(db_url) as conn:
        rows = conn.execute(
            "SELECT kind, payload_json FROM audit_event "
            "WHERE ts >= ? AND ts < ? AND actor = ?",
            (start_ts, end_ts, strategy_slug),
        ).fetchall()
    proposed_by_symbol: dict[str, int] = {}
    rejected_by_symbol: dict[str, int] = {}
    for r in rows:
        try:
            p = json.loads(r["payload_json"])
        except (TypeError, ValueError):
            continue
        sym = (p.get("underlying") or p.get("symbol") or "").upper()
        if r["kind"] == "combo_proposed":
            proposed_by_symbol[sym] = proposed_by_symbol.get(sym, 0) + 1
        elif r["kind"] == "combo_rejected_by_risk":
            rejected_by_symbol[sym] = rejected_by_symbol.get(sym, 0) + 1

    out: list[dict] = []
    for symbol in universe:
        sym_telemetry = today_telemetry.get(symbol, {})
        scanned = symbol in today_telemetry or symbol in proposed_by_symbol
        # Filter reasons (if any).
        by_reason = (sym_telemetry.get("by_reason") or {})
        filter_total = int(sym_telemetry.get("total", 0))

        ivr = None
        atm_iv_front = None
        atm_iv_back = None
        try:
            ivr_decimal = await calc_iv_rank(symbol)
            ivr = round(ivr_decimal * 100.0, 1) if ivr_decimal is not None else None
        except Exception:
            ivr = None
        try:
            atm_iv_front = await calc_atm_iv(symbol, target_dte=45, tolerance_days=7)
        except Exception:
            atm_iv_front = None
        try:
            atm_iv_back = await calc_atm_iv(symbol, target_dte=75, tolerance_days=15)
        except Exception:
            atm_iv_back = None
        term_spread = (
            atm_iv_front - atm_iv_back
            if (atm_iv_front is not None and atm_iv_back is not None) else None
        )

        out.append({
            "symbol": symbol,
            "scanned": scanned,
            "filtered_total": filter_total,
            "filter_reasons": by_reason,
            "combos_proposed": proposed_by_symbol.get(symbol, 0),
            "combos_rejected_by_risk": rejected_by_symbol.get(symbol, 0),
            "current_ivr_pct": ivr,
            "current_atm_iv_45dte": atm_iv_front,
            "current_atm_iv_75dte": atm_iv_back,
            "term_structure_spread": term_spread,
        })
    return out


# ---------------------------------------------------------------------------
# Section 5: strategy health
# ---------------------------------------------------------------------------


def strategy_health(
    *,
    ic_strategy: Any,
    ic_division: Any,
    pending_combo_registry: Any,
    telegram_batcher: Any,
    db_url: str = "sqlite:///data/trading_corp.db",
    strategy_slug: str = STRATEGY_SLUG,
) -> dict:
    """Strategy-level health snapshot.

    Pulled from a mix of config (strategy.enabled / auto_execute),
    agent_state (circuit breaker, last-write timestamp), audit_event
    (last manage tick), live market data (VIX, macro halt), and a
    cross-check between `position` rows and `agent_state.open_ics` to
    catch state-consistency bugs from step 7/9/11.
    """
    out: dict[str, Any] = {}

    if ic_strategy is not None:
        try:
            out["enabled"] = bool(ic_strategy.enabled)
            out["auto_execute"] = bool(ic_strategy.auto_execute)
        except Exception:
            out["enabled"] = None
            out["auto_execute"] = None
    else:
        out["enabled"] = None
        out["auto_execute"] = None

    # Agent state pull.
    rec = db.load_agent_state(strategy_slug, "state", db_url=db_url)
    state, agent_state_updated_at = (rec or (None, None))
    state = state if isinstance(state, dict) else {}
    cb = state.get("circuit_breaker") or {}
    open_ics = state.get("open_ics") or {}
    scan_telemetry = state.get("scan_telemetry") or {}

    paused_until = cb.get("paused_until")
    drawdown_hwm = cb.get("drawdown_hwm")
    out["circuit_breaker"] = {
        "consecutive_losses": int(cb.get("consecutive_losses") or 0),
        "drawdown_hwm": drawdown_hwm,
        "paused_until": paused_until,
        "is_paused": _is_paused(paused_until),
    }

    # Heartbeat timestamps.
    out["agent_state_updated_at"] = (
        agent_state_updated_at.isoformat()
        if isinstance(agent_state_updated_at, datetime) else None
    )
    out["last_scan_telemetry_day"] = (
        max(scan_telemetry.keys()) if scan_telemetry else None
    )

    # Last manage tick — approximate via the most recent audit_event
    # whose actor is the strategy slug OR data_exec.
    try:
        with db.connect(db_url) as conn:
            row = conn.execute(
                "SELECT ts FROM audit_event "
                "WHERE actor IN (?, 'data_exec') "
                "AND (payload_json LIKE ? OR actor = ?) "
                "ORDER BY ts DESC LIMIT 1",
                (strategy_slug, f"%{strategy_slug}%", strategy_slug),
            ).fetchone()
        out["last_manage_audit_ts"] = row["ts"] if row else None
    except Exception:
        out["last_manage_audit_ts"] = None

    # Live VIX + macro halt.
    try:
        from trading_corp.utils.market_data import get_vix
        vix = get_vix()
        out["vix"] = vix
        out["vix_gate_firing"] = bool(vix is not None and vix > 30.0)
    except Exception:
        out["vix"] = None
        out["vix_gate_firing"] = None

    macro_info: dict[str, Any] = {"in_halt_window": False, "next_event": None}
    try:
        from trading_corp.data.macro_calendar import MacroCalendar
        cal = MacroCalendar.load()
        now = datetime.now(timezone.utc)
        hit, ev = cal.is_within_halt_window(
            now, window_minutes=7200, impact_levels=("high",),
        )
        macro_info["in_halt_window"] = bool(hit)
        if ev is not None:
            macro_info["next_event"] = {
                "name": ev.name, "ts": ev.ts.isoformat(),
                "impact": ev.impact,
            }
    except Exception:
        pass
    out["macro_halt"] = macro_info

    # Cross-check position rows vs open_ics.
    open_ic_count_in_state = len(open_ics)
    distinct_open_combos_in_db = 0
    try:
        with db.connect(db_url) as conn:
            row = conn.execute(
                """
                SELECT COUNT(DISTINCT json_extract(extra_json, '$.combo_id')) AS n
                FROM position
                WHERE json_extract(extra_json, '$.is_combo_leg') = 1
                  AND json_extract(extra_json, '$.strategy') = ?
                  AND json_extract(extra_json, '$.position_effect') = 'open'
                  AND json_extract(extra_json, '$.combo_id') NOT IN (
                    SELECT json_extract(extra_json, '$.combo_id')
                    FROM position
                    WHERE json_extract(extra_json, '$.is_combo_leg') = 1
                      AND json_extract(extra_json, '$.position_effect') = 'close'
                  )
                """,
                (strategy_slug,),
            ).fetchone()
        distinct_open_combos_in_db = int(row["n"] or 0) if row else 0
    except Exception:
        distinct_open_combos_in_db = -1   # signal "couldn't check"

    out["state_consistency"] = {
        "open_ics_in_agent_state": open_ic_count_in_state,
        "distinct_open_combos_in_position_table": distinct_open_combos_in_db,
        "agrees": (
            distinct_open_combos_in_db >= 0
            and distinct_open_combos_in_db == open_ic_count_in_state
        ),
    }

    # Wired references (lets the operator see whether the orchestration
    # pieces are even attached).
    out["wiring"] = {
        "ic_strategy_attached": ic_strategy is not None,
        "ic_division_attached": ic_division is not None,
        "pending_combo_registry_attached": pending_combo_registry is not None,
        "telegram_batcher_attached": telegram_batcher is not None,
        "division_has_strategy": (
            getattr(ic_division, "has_strategy", False)
            if ic_division is not None else None
        ),
    }
    return out


def _is_paused(paused_until: Any) -> bool:
    if not paused_until:
        return False
    try:
        dt = datetime.fromisoformat(paused_until)
    except (TypeError, ValueError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) < dt


# ---------------------------------------------------------------------------
# Section 6: last 10 closed combos
# ---------------------------------------------------------------------------


def recent_closed_combos(
    *,
    db_url: str = "sqlite:///data/trading_corp.db",
    limit: int = 10,
    strategy_slug: str = STRATEGY_SLUG,
) -> list[dict]:
    """Last N `ic_lifecycle_closed` audit events."""
    with db.connect(db_url) as conn:
        rows = conn.execute(
            """
            SELECT ts, payload_json FROM audit_event
            WHERE kind = 'ic_lifecycle_closed' AND actor = ?
            ORDER BY ts DESC LIMIT ?
            """,
            (strategy_slug, int(limit)),
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        try:
            p = json.loads(r["payload_json"])
        except (TypeError, ValueError):
            continue
        credit = float(p.get("credit_at_entry") or 0)
        realized_dollars = float(p.get("realized_pnl_dollars") or 0)
        realized_per_share = float(p.get("realized_pnl_per_share") or 0)
        pct_of_credit = (
            (realized_per_share / credit * 100.0) if credit > 0 else None
        )
        out.append({
            "closed_ts": r["ts"],
            "combo_id": p.get("combo_id"),
            "short_id": (p.get("combo_id") or "")[:8],
            "symbol": p.get("symbol"),
            "dte_at_entry": p.get("dte_at_entry"),
            "ivr_at_entry": p.get("ivr_at_entry"),
            "credit_at_entry": credit,
            "realized_pnl_dollars": realized_dollars,
            "realized_pnl_per_share": realized_per_share,
            "realized_pct_of_credit": pct_of_credit,
            "close_kind": p.get("close_kind"),
            "adjustment_count": int(p.get("adjustment_count") or 0),
            "contracts": p.get("contracts"),
        })
    return out
