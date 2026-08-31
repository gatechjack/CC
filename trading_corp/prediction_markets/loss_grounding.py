"""Prediction Markets -- Stage 5 LOSS-GROUNDING: re-source a whale's LOSSES from /activity (held-to-resolution)
instead of trusting /closed-positions, which SYSTEMATICALLY OMITS held-to-worthless losses (wallet-dependently --
~63% dropped for evanng; the F-1 bias). Analyze counts losses from `pm_closed_position.won=0`, which inherits that
omission, so a whale's win-rate reads OVER-STATED. This module re-grounds the loss set PER PAIR (the ruled Stage-5
shape, F-1) and stamps a MEASURED completeness bound.

★ PORTS THE LOSS-VISIBILITY PROBE'S METHOD VERBATIM (`a_decisions` + the A_only set arithmetic): for each
(condition_id, outcome_index), accumulate BUY/SELL sizes from TRADE activity; a decision was HELD TO RESOLUTION iff
a MATERIAL net long remained (buy - sell > max(0.5, 1% of buy)); its resolved/won status comes from GAMMA (the
resolution authority, PM_REQUIREMENTS R3 -- NOT /closed-positions, which is a screening source with a measured bias).
`A_only` = held+resolved-in-activity MINUS present-in-/closed-positions = the losses (and wins) /closed-positions
dropped. The HONEST set = /closed-positions + A_only.

STRUCTURAL: PURE (stdlib only; no broker, no network, no DB). The caller (analyze) fetches /activity +
/closed-positions + gamma and hands the rows in, exactly like the driver injects its reads -- so the set arithmetic
is fully unit-testable and the API cost lives in one orchestration site.

Spec: reports/prediction_markets/PM_REBUILD_PLAN_2026-08-26.md Stage 5 + section F-1; the probe
`pm_loss_visibility_probe.sh`.
"""
from __future__ import annotations

from dataclasses import dataclass

# a decision "was HELD to resolution" (apples-to-apples with a settled loss -- NOT a round-trip that was sold out)
# iff the net long remaining exceeds a MATERIAL floor: 0.5 contracts OR 1% of the gross bought, whichever is larger.
# Verbatim from the probe (`held = (buy - sell) > max(0.5, 0.01*buy)`).
_HELD_ABS_FLOOR = 0.5
_HELD_FRAC = 0.01
# winner convention, identical on BOTH sides (the probe's conscious cross-source sanity check): cur_price >= 0.9.
_WON_THRESHOLD = 0.9


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def activity_decisions(activity_rows, resolutions: dict) -> dict:
    """{(condition_id, outcome_index): {resolved, won, held}} from TRADE activity + GAMMA resolutions. Aggregates
    BUY/SELL size per pair; `held` = a material net long remained at resolution; `resolved`/`won` from gamma
    (`resolutions[cid]` = {status, winning_outcome_index, ...} from fetch_market_resolutions). Ports the probe's
    a_decisions verbatim. Only TRADE rows with a condition_id + integer outcome_index are counted."""
    agg: dict = {}
    for a in activity_rows:
        if str(getattr(a, "type", "") or "").upper() != "TRADE":
            continue
        cid = getattr(a, "condition_id", None)
        if not cid:
            continue
        oi = _int_or_none(getattr(a, "outcome_index", None))
        if oi is None:
            continue
        e = agg.setdefault((cid, oi), {"buy": 0.0, "sell": 0.0})
        side = str(getattr(a, "side", "") or "").upper()
        if side == "BUY":
            e["buy"] += _f(getattr(a, "size", 0.0))
        elif side == "SELL":
            e["sell"] += _f(getattr(a, "size", 0.0))
    out: dict = {}
    for (cid, oi), e in agg.items():
        if e["buy"] <= 0.0:
            continue
        r = resolutions.get(cid) or {}
        resolved = str(r.get("status") or "").lower() == "resolved"
        win = _int_or_none(r.get("winning_outcome_index"))
        won = bool(resolved and win is not None and oi == win)
        held = (e["buy"] - e["sell"]) > max(_HELD_ABS_FLOOR, _HELD_FRAC * e["buy"])
        out[(cid, oi)] = {"resolved": resolved, "won": won, "held": held}
    return out


def closed_decisions(closed_rows) -> dict:
    """{(condition_id, outcome_index): {won}} from /closed-positions. Winner convention cur_price >= 0.9 (the same
    convention the ingest + the activity side use -- the conscious cross-source sanity check)."""
    out: dict = {}
    for c in closed_rows:
        cid = getattr(c, "condition_id", None)
        oi = _int_or_none(getattr(c, "outcome_index", None))
        if not cid or oi is None:
            continue
        out[(cid, oi)] = {"won": _f(getattr(c, "cur_price", 0.0)) >= _WON_THRESHOLD}
    return out


@dataclass(frozen=True)
class LossGrounding:
    closed_wins: int              # what /closed-positions (pm_closed_position) reports today
    closed_losses: int
    a_only_wins: int              # held+resolved in /activity, ABSENT from /closed-positions (the dropped decisions)
    a_only_losses: int            # ★ THE MISSING LOSSES /closed-positions omitted
    honest_wins: int              # closed + a_only
    honest_losses: int
    loss_omission_pct: float | None   # a_only_losses / honest_losses -- the MEASURED size of the bias for THIS whale
    activity_truncated: bool      # /activity hit the ~5000-row ceiling -> a_only is a LOWER bound
    completeness: str             # human bound: 'complete' | 'windowed(activity truncated -- a_only is a lower bound)'
    n_activity_held_resolved: int


def ground_losses(activity_rows, closed_rows, resolutions: dict, *, activity_truncated: bool) -> LossGrounding:
    """Reconcile the /activity held-to-resolution set against /closed-positions -> the HONEST win/loss counts + the
    A_only (dropped) losses/wins + a MEASURED completeness bound. HONEST = /closed-positions UNION A_only (A_only
    excludes keys already in /closed-positions, so no double-count). When /activity was TRUNCATED, A_only is a LOWER
    bound (more losses may lie beyond the 5000-row window) -> the completeness bound says so, and Analyze surfaces it
    rather than implying a precision it lacks."""
    ad = activity_decisions(activity_rows, resolutions)
    cd = closed_decisions(closed_rows)
    aheld = {k: v for k, v in ad.items() if v["resolved"] and v["held"]}
    a_only = set(aheld) - set(cd)
    ao_w = sum(1 for k in a_only if aheld[k]["won"])
    ao_l = len(a_only) - ao_w
    cw = sum(1 for v in cd.values() if v["won"])
    cl = len(cd) - cw
    honest_w = cw + ao_w
    honest_l = cl + ao_l
    denom = honest_w + honest_l
    completeness = ("windowed(activity truncated -- a_only losses are a lower bound)"
                    if activity_truncated else "complete(activity exhausted within window)")
    return LossGrounding(
        closed_wins=cw, closed_losses=cl, a_only_wins=ao_w, a_only_losses=ao_l,
        honest_wins=honest_w, honest_losses=honest_l,
        loss_omission_pct=((ao_l / honest_l) if honest_l > 0 else None),
        activity_truncated=bool(activity_truncated), completeness=completeness,
        n_activity_held_resolved=len(aheld))


# ── the async ORCHESTRATOR: page /activity + /closed-positions + gamma, category-filter, ground (Stage 5 R2a) ──
# Verbatim paging from the probe: /activity up to `activity_max_pages*activity_limit` (5000 default, TRUNCATED flag);
# /closed-positions up to `closed_max` (~the API's ~1500 cap). Category filtering is DECOUPLED from the slug->category
# derivation via an injected `category_of(row)->str` -- so this stays PURE-logic + testable with a fake client, and
# the derivation (names.py) lives at the ONE caller (analyze). The client is a PolymarketDataAPIClient (duck-typed:
# fetch_activity / fetch_closed_positions / fetch_market_resolutions). This is the ONE site with the API cost.
async def fetch_and_ground_losses(client, wallet: str, category: str, *, category_of,
                                  activity_max_pages: int = 10, activity_limit: int = 500,
                                  closed_limit: int = 50, closed_max: int = 2000):
    """Fetch + category-filter + ground the loss set for (wallet, category). Returns a LossGrounding. `category_of`
    maps an Activity/Closed row to its category string (injected). A_only is a LOWER bound when /activity truncated."""
    acts, trunc = [], False
    for pg in range(activity_max_pages):
        rows = await client.fetch_activity(wallet, limit=activity_limit, offset=pg * activity_limit)
        if not rows:
            break
        acts.extend(rows)
        if len(rows) < activity_limit:
            break
    else:
        trunc = True                                          # hit the page ceiling -> the ~5000-row window truncated
    closed, off = [], 0
    while True:
        rows = await client.fetch_closed_positions(wallet, limit=closed_limit, offset=off)
        if not rows:
            break
        closed.extend(rows)
        if len(rows) < closed_limit:
            break
        off += closed_limit
        if off >= closed_max:
            break
    acts_cat = [a for a in acts if category_of(a) == category]
    closed_cat = [c for c in closed if category_of(c) == category]
    cids = list({getattr(x, "condition_id", None) for x in (acts_cat + closed_cat) if getattr(x, "condition_id", None)})
    resolutions = (await client.fetch_market_resolutions(cids)) if cids else {}
    return ground_losses(acts_cat, closed_cat, resolutions, activity_truncated=trunc)
