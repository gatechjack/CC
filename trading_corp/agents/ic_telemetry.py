"""Telemetry queries for the Robinhood Joint Iron Condor strategy.

Pure-function surfaces that aggregate from three sources:

  - `position` table — leg rows tagged with `extra_json.combo_id` and
    `extra_json.ic_underlying_iv_rank_at_entry` (the IVR-at-entry stamp
    added on opening legs in step 13 plumbing).
  - `audit_event` table — `combo_filled` rows (per-combo slippage),
    `ic_lifecycle_closed` rows (per-combo realized P&L with the entry
    IVR, DTE, contracts, and adjustment_count snapshot taken at close).
  - `agent_state` table — `(robinhood_joint_iron_condor, state).
    value_json.scan_telemetry` daily counter of filtered-out symbols
    by reason.

All queries return dicts; the CLI tool in `scripts/ic_telemetry_cli.py`
prints formatted tables. The web app can consume the same dicts later
if a UI surface is added.

Date semantics: every query that takes a date range uses ISO-8601 UTC
strings inclusive of start, exclusive of end (`start <= ts < end`).
Pass `None` to either side to leave that bound open.
"""
from __future__ import annotations

import json
import logging
import statistics
from datetime import date, datetime, timezone
from typing import Any

from trading_corp.persistence import db

log = logging.getLogger(__name__)

STRATEGY_SLUG_DEFAULT = "robinhood_joint_iron_condor"
DIVISION_DEFAULT = "robinhood_joint"


# ---------------------------------------------------------------------------
# 1. Combo-grouped P&L report
# ---------------------------------------------------------------------------


def combo_pnl_report(
    *,
    strategy: str | None = STRATEGY_SLUG_DEFAULT,
    division: str | None = DIVISION_DEFAULT,
    start_ts: str | None = None,
    end_ts: str | None = None,
    db_url: str = "sqlite:///data/trading_corp.db",
) -> dict[str, Any]:
    """Combo-grouped realized P&L.

    Groups `position` rows by `extra_json.combo_id`, computes per-combo
    net cashflow, classifies each combo as realized (has both opens and
    closes) or still-open. Returns:

      {
        "combos":       [ {combo_id, symbol, net_pnl, leg_count,
                          open_legs, close_legs, status, first_ts, last_ts}, ... ],
        "summary": {
            "realized_count": int,
            "win_count":      int,
            "loss_count":     int,
            "win_rate":       float | None,
            "mean_win":       float | None,
            "mean_loss":      float | None,
            "expectancy":     float | None,
            "total_realized": float,
            "open_count":     int,
        },
      }

    `net_pnl` is in dollars (per-share × 100 × signed-qty already baked
    in by step-7's signed-qty convention: `-qty × avg_price` per leg).
    """
    sql = (
        "SELECT json_extract(extra_json, '$.combo_id')        AS combo_id, "
        "       json_extract(extra_json, '$.strategy')        AS strategy, "
        "       account                                       AS division, "
        "       json_extract(extra_json, '$.underlying')      AS symbol, "
        "       json_extract(extra_json, '$.position_effect') AS effect, "
        "       qty, avg_price, opened_ts "
        "FROM position "
        "WHERE json_extract(extra_json, '$.is_combo_leg') = 1 "
    )
    params: list[Any] = []
    if strategy is not None:
        sql += "AND json_extract(extra_json, '$.strategy') = ? "
        params.append(strategy)
    if division is not None:
        sql += "AND account = ? "
        params.append(division)
    if start_ts is not None:
        sql += "AND opened_ts >= ? "
        params.append(start_ts)
    if end_ts is not None:
        sql += "AND opened_ts < ? "
        params.append(end_ts)

    with db.connect(db_url) as conn:
        rows = conn.execute(sql, params).fetchall()

    # Aggregate per combo_id.
    per_combo: dict[str, dict] = {}
    for r in rows:
        cid = r["combo_id"]
        if cid is None:
            continue
        bucket = per_combo.setdefault(cid, {
            "combo_id": cid, "symbol": r["symbol"], "strategy": r["strategy"],
            "division": r["division"],
            "net_pnl": 0.0, "leg_count": 0,
            "open_legs": 0, "close_legs": 0,
            "first_ts": r["opened_ts"], "last_ts": r["opened_ts"],
        })
        # Step-7 signed-qty convention: buys=+qty, sells=-qty.
        # Cashflow per leg = -qty × avg_price × 100  (sells receive, buys pay).
        bucket["net_pnl"] += -float(r["qty"]) * float(r["avg_price"]) * 100.0
        bucket["leg_count"] += 1
        if r["effect"] == "open":
            bucket["open_legs"] += 1
        elif r["effect"] == "close":
            bucket["close_legs"] += 1
        if r["opened_ts"] < bucket["first_ts"]:
            bucket["first_ts"] = r["opened_ts"]
        if r["opened_ts"] > bucket["last_ts"]:
            bucket["last_ts"] = r["opened_ts"]

    combos = list(per_combo.values())
    for c in combos:
        if c["close_legs"] > 0 and c["open_legs"] > 0:
            c["status"] = "realized"
        elif c["open_legs"] > 0 and c["close_legs"] == 0:
            c["status"] = "open"
        else:
            c["status"] = "partial"

    realized = [c for c in combos if c["status"] == "realized"]
    wins = [c for c in realized if c["net_pnl"] > 0]
    losses = [c for c in realized if c["net_pnl"] < 0]
    open_count = sum(1 for c in combos if c["status"] == "open")

    wr = (len(wins) / (len(wins) + len(losses))) if (wins or losses) else None
    mw = statistics.mean(c["net_pnl"] for c in wins) if wins else None
    ml = statistics.mean(c["net_pnl"] for c in losses) if losses else None
    expectancy = (
        (wr * mw + (1 - wr) * ml)
        if (wr is not None and mw is not None and ml is not None)
        else None
    )

    combos.sort(key=lambda c: c["last_ts"], reverse=True)
    return {
        "combos": combos,
        "summary": {
            "realized_count": len(realized),
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": wr,
            "mean_win": mw,
            "mean_loss": ml,
            "expectancy": expectancy,
            "total_realized": sum(c["net_pnl"] for c in realized),
            "open_count": open_count,
        },
    }


# ---------------------------------------------------------------------------
# 2. Win-rate by IVR-at-entry bucket
# ---------------------------------------------------------------------------


_IVR_BUCKETS = [
    (30, 40, "30-40"),
    (40, 50, "40-50"),
    (50, 60, "50-60"),
    (60, 70, "60-70"),
    (70, 200, "70+"),
]


def win_rate_by_ivr(
    *,
    strategy: str | None = STRATEGY_SLUG_DEFAULT,
    start_ts: str | None = None,
    end_ts: str | None = None,
    db_url: str = "sqlite:///data/trading_corp.db",
) -> dict[str, Any]:
    """Bucket closed combos by IVR-at-entry and report win rate + P&L per bucket.

    Pulls from `ic_lifecycle_closed` audit rows emitted at close time
    (step 13 plumbing). For each bucket: count, win count, win rate,
    mean P&L (dollars), mean credit at entry (dollars/share).
    """
    sql = (
        "SELECT ts, payload_json FROM audit_event "
        "WHERE kind = 'ic_lifecycle_closed' "
    )
    params: list[Any] = []
    if strategy is not None:
        sql += "AND actor = ? "
        params.append(strategy)
    if start_ts is not None:
        sql += "AND ts >= ? "
        params.append(start_ts)
    if end_ts is not None:
        sql += "AND ts < ? "
        params.append(end_ts)
    sql += "ORDER BY ts ASC"

    with db.connect(db_url) as conn:
        rows = conn.execute(sql, params).fetchall()

    buckets: dict[str, list[dict]] = {label: [] for _, _, label in _IVR_BUCKETS}
    unbucketed: list[dict] = []
    for r in rows:
        try:
            p = json.loads(r["payload_json"])
        except (TypeError, ValueError):
            continue
        ivr = p.get("ivr_at_entry")
        if ivr is None:
            unbucketed.append(p)
            continue
        ivr_f = float(ivr)
        label = _label_for_ivr(ivr_f)
        buckets[label].append(p)

    out_buckets: list[dict] = []
    for low, high, label in _IVR_BUCKETS:
        entries = buckets[label]
        wins = [e for e in entries if (e.get("realized_pnl_dollars") or 0) > 0]
        losses = [e for e in entries if (e.get("realized_pnl_dollars") or 0) < 0]
        pnls = [float(e.get("realized_pnl_dollars") or 0) for e in entries]
        credits = [
            float(e.get("credit_at_entry") or 0)
            for e in entries if e.get("credit_at_entry") is not None
        ]
        wr = (len(wins) / (len(wins) + len(losses))) if (wins or losses) else None
        out_buckets.append({
            "label": label, "ivr_min": low, "ivr_max": high,
            "count": len(entries),
            "win_count": len(wins), "loss_count": len(losses),
            "win_rate": wr,
            "mean_pnl_dollars": statistics.mean(pnls) if pnls else None,
            "mean_credit_at_entry": statistics.mean(credits) if credits else None,
        })

    return {
        "buckets": out_buckets,
        "unbucketed_count": len(unbucketed),
        "total_closed": sum(b["count"] for b in out_buckets) + len(unbucketed),
    }


def _label_for_ivr(ivr: float) -> str:
    for low, high, label in _IVR_BUCKETS:
        if low <= ivr < high:
            return label
    return "70+"


# ---------------------------------------------------------------------------
# 3. Adjustment-outcome stats
# ---------------------------------------------------------------------------


def adjustment_outcome_stats(
    *,
    strategy: str | None = STRATEGY_SLUG_DEFAULT,
    start_ts: str | None = None,
    end_ts: str | None = None,
    db_url: str = "sqlite:///data/trading_corp.db",
) -> dict[str, Any]:
    """Compare P&L distribution for combos with vs without adjustments.

    Splits `ic_lifecycle_closed` rows into two buckets:
      - adjusted:  adjustment_count > 0
      - unadjusted: adjustment_count == 0

    Per-bucket stats: count, win rate, mean P&L, median P&L, stdev P&L.
    """
    sql = (
        "SELECT ts, payload_json FROM audit_event "
        "WHERE kind = 'ic_lifecycle_closed' "
    )
    params: list[Any] = []
    if strategy is not None:
        sql += "AND actor = ? "
        params.append(strategy)
    if start_ts is not None:
        sql += "AND ts >= ? "
        params.append(start_ts)
    if end_ts is not None:
        sql += "AND ts < ? "
        params.append(end_ts)

    with db.connect(db_url) as conn:
        rows = conn.execute(sql, params).fetchall()

    adjusted: list[float] = []
    unadjusted: list[float] = []
    for r in rows:
        try:
            p = json.loads(r["payload_json"])
        except (TypeError, ValueError):
            continue
        pnl = float(p.get("realized_pnl_dollars") or 0)
        adj_count = int(p.get("adjustment_count") or 0)
        (adjusted if adj_count > 0 else unadjusted).append(pnl)

    def _bucket(pnls: list[float]) -> dict:
        if not pnls:
            return {
                "count": 0, "win_count": 0, "loss_count": 0,
                "win_rate": None, "mean_pnl": None,
                "median_pnl": None, "stdev_pnl": None,
                "total_pnl": 0.0,
            }
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        return {
            "count": len(pnls),
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": (
                len(wins) / (len(wins) + len(losses))
                if (wins or losses) else None
            ),
            "mean_pnl": statistics.mean(pnls),
            "median_pnl": statistics.median(pnls),
            "stdev_pnl": statistics.stdev(pnls) if len(pnls) > 1 else 0.0,
            "total_pnl": sum(pnls),
        }

    return {
        "adjusted": _bucket(adjusted),
        "unadjusted": _bucket(unadjusted),
    }


# ---------------------------------------------------------------------------
# 4. Scan-filter counters (from agent_state.scan_telemetry)
# ---------------------------------------------------------------------------


def scan_filter_counters(
    *,
    strategy: str = STRATEGY_SLUG_DEFAULT,
    date_iso: str | None = None,
    db_url: str = "sqlite:///data/trading_corp.db",
) -> dict[str, Any]:
    """Return the scan_telemetry counter from agent_state.

    `date_iso` (e.g. "2026-05-15") restricts to a single day; None
    returns the entire history. Output shape mirrors the in-state
    structure (`{date: {symbol: {total, by_reason}}}`) plus a flat
    cross-day-and-symbol totals view at `["totals_by_reason"]`.
    """
    rec = db.load_agent_state(strategy, "state", db_url=db_url)
    if rec is None:
        return {"by_day": {}, "totals_by_reason": {}, "total_filtered": 0}
    state, _ts = rec
    if not isinstance(state, dict):
        return {"by_day": {}, "totals_by_reason": {}, "total_filtered": 0}
    telemetry = state.get("scan_telemetry") or {}

    if date_iso is not None:
        telemetry = {date_iso: telemetry.get(date_iso, {})}

    totals_by_reason: dict[str, int] = {}
    total_filtered = 0
    for day, by_symbol in telemetry.items():
        if not isinstance(by_symbol, dict):
            continue
        for sym, payload in by_symbol.items():
            if not isinstance(payload, dict):
                continue
            total_filtered += int(payload.get("total", 0))
            for reason, n in (payload.get("by_reason") or {}).items():
                totals_by_reason[reason] = totals_by_reason.get(reason, 0) + int(n)

    return {
        "by_day": telemetry,
        "totals_by_reason": totals_by_reason,
        "total_filtered": total_filtered,
    }


# ---------------------------------------------------------------------------
# 5. Combo slippage stats (from combo_filled audit)
# ---------------------------------------------------------------------------


def normalized_net_actual(net_actual: Any, direction: Any) -> float | None:
    """Direction-normalized `net_actual` for DISPLAY (credit +, debit −).

    `combo_filled.net_actual` is stored as a signed magnitude with `direction`
    the authoritative sign source. A pre-2026-07-24 leg-attribution bug (fixed in
    `robinhood.py`) could store it with a FLIPPED sign when Robinhood returned the
    combo legs reordered — e.g. the 2026-07-24 RKLB roll booked `-1.17` and OPEN
    `-0.26` for genuine credits. We take the magnitude and re-apply the sign the
    display convention implies, so a sign-flipped row renders correct. Pure /
    read-only — the audit row is NEVER mutated. None-safe.
    """
    if net_actual is None:
        return None
    try:
        mag = abs(float(net_actual))
    except (TypeError, ValueError):
        return None
    return -mag if direction == "debit" else mag


def slippage_vs_limit(net_actual: Any, net_limit: Any) -> float | None:
    """Favorable actual-vs-limit slippage recomputed from MAGNITUDES.

    The stored `actual_vs_limit_slippage_dollars` is `abs(actual - net_limit)`,
    which inflates when `net_actual`'s sign is flipped (RKLB's stored slippage is
    `abs(-1.17 - 1.14) = 2.31` vs the true `0.03`). Both operands are magnitudes,
    so the sign-flip-immune gap is `abs(|actual| - |net_limit|)`. None-safe.
    """
    if net_actual is None or net_limit is None:
        return None
    try:
        return abs(abs(float(net_actual)) - abs(float(net_limit)))
    except (TypeError, ValueError):
        return None


def combo_slippage_stats(
    *,
    strategy: str | None = STRATEGY_SLUG_DEFAULT,
    division: str | None = DIVISION_DEFAULT,
    start_ts: str | None = None,
    end_ts: str | None = None,
    db_url: str = "sqlite:///data/trading_corp.db",
) -> dict[str, Any]:
    """Distribution of actual-vs-limit slippage per filled combo.

    Pulls `combo_filled` audit rows emitted by `data_exec.place_combo`.
    `net_actual` and `slippage_dollars` are DIRECTION-NORMALIZED at read time
    (`normalized_net_actual` / `slippage_vs_limit`: credit positive, debit
    negative; slippage from magnitudes) so rows written by the pre-2026-07-24
    leg-attribution bug (a flipped `net_actual` sign) report the true credit and
    the true `abs(|actual| - |limit|)` gap. The stored audit row is never mutated.

    Returns:

      {
        "events": [ {combo_id, ts, direction, net_limit, net_actual,
                     slippage_dollars, intent}, ... ],
        "summary": {
            "n":             int,
            "mean_slippage": float | None,
            "median_slippage": float | None,
            "p90_slippage":  float | None,
            "max_slippage":  float | None,
            "total_slippage_realized": float,
        },
      }
    """
    sql = (
        "SELECT ts, payload_json FROM audit_event "
        "WHERE kind = 'combo_filled' "
    )
    params: list[Any] = []
    if start_ts is not None:
        sql += "AND ts >= ? "
        params.append(start_ts)
    if end_ts is not None:
        sql += "AND ts < ? "
        params.append(end_ts)
    sql += "ORDER BY ts ASC"

    with db.connect(db_url) as conn:
        rows = conn.execute(sql, params).fetchall()

    events: list[dict] = []
    for r in rows:
        try:
            p = json.loads(r["payload_json"])
        except (TypeError, ValueError):
            continue
        if strategy is not None and p.get("strategy") != strategy:
            continue
        if division is not None and p.get("division") != division:
            continue
        # Direction-normalized at read time (credit +, debit −; slippage from
        # magnitudes) so pre-2026-07-24 sign-flipped rows report the true values.
        # The stored audit row is untouched.
        slip = slippage_vs_limit(p.get("net_actual"), p.get("net_limit_price"))
        if slip is None:
            continue
        events.append({
            "ts": r["ts"],
            "combo_id": p.get("combo_id"),
            "direction": p.get("direction"),
            "net_limit": p.get("net_limit_price"),
            "net_actual": normalized_net_actual(p.get("net_actual"), p.get("direction")),
            "slippage_dollars": slip,
            "intent": (
                # The strategy module stamps combo_intent on every leg's
                # extra; place_combo carries it through into the audit
                # leg payload — best-effort lookup.
                (p.get("legs") or [{}])[0].get("position_effect") or "?"
            ),
        })

    slips = [e["slippage_dollars"] for e in events]
    out: dict[str, Any] = {"events": events}
    if slips:
        sorted_slips = sorted(slips)
        n = len(sorted_slips)
        p90_idx = max(0, int(0.9 * (n - 1)))
        out["summary"] = {
            "n": n,
            "mean_slippage": statistics.mean(slips),
            "median_slippage": statistics.median(slips),
            "p90_slippage": sorted_slips[p90_idx],
            "max_slippage": max(slips),
            "total_slippage_realized": sum(slips),
        }
    else:
        out["summary"] = {
            "n": 0, "mean_slippage": None, "median_slippage": None,
            "p90_slippage": None, "max_slippage": None,
            "total_slippage_realized": 0.0,
        }
    return out
