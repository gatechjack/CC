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

import logging
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
    closed_wins: int              # what the CLOSED feed reports today
    closed_losses: int
    a_only_wins: int              # held+resolved in /activity, ABSENT from the closed feed (the dropped decisions)
    a_only_losses: int            # ★ THE MISSING LOSSES the closed feed omitted (activity-recovered + RL-recovered)
    honest_wins: int              # closed + a_only
    honest_losses: int
    loss_omission_pct: float | None   # a_only_losses / honest_losses -- the MEASURED size of the bias for THIS whale
    activity_truncated: bool      # /activity hit the ~5000-row ceiling -> the WIN-side recovery is a LOWER bound
    completeness: str             # human bound; '+rl' in the string marks the RL-supplemented loss basis
    n_activity_held_resolved: int  # held+resolved decisions found in the /activity window (RL rows NOT counted here)
    # ── the coverage BEHIND the omission % (so a display cannot flatten two different claims into one number) ──
    n_closed: int = 0                  # closed-feed decisions in this (wallet,category) slice = the coverage denominator
    coverage_pct: float | None = None  # |closed re-found in the /activity window| / |closed|: how much of the whale's closed
                                       # era the window actually reached. '94% omission @ 96% coverage' and '@ 31% coverage'
                                       # are DIFFERENT claims -- a windowed low-coverage omission is a FLOOR, not a measurement.
                                       # None when there are no closed decisions to cover. With the RL supplement this bounds
                                       # only the WIN-side recovery; the loss side is point-in-time complete (loss_basis).
    # ── RL SUPPLEMENT (2026-11-19, Jack ruling): /v2/positions?status=REDEEMABLE_LOST unioned into the loss side ──
    rl_added_losses: int = 0          # gamma-confirmed RL losses NOT found by the /activity window and NOT in closed
    rl_false_losses: int = 0          # telemetry: RL rows gamma says WON (dropped; measured 0/6,383 on 2026-11-19)
    loss_side_complete: bool = False  # RL walk AND closed walk both reached their terminal page -> the loss set is
                                      # point-in-time complete regardless of the /activity window. (Docs: inactive
                                      # markets stay excluded even with include_archived -> a lower bound in principle.)
    loss_basis: str = "activity"      # 'activity' (legacy) | 'activity+rl' -- consumers use this to stop treating low
                                      # /activity coverage as a LOSS-side floor once RL supplies the losses directly


def _rl_key(r):
    """(condition_id, outcome_index) for a /v2/positions PositionRow (outcomeIndex rides in `.extra`)."""
    cid = getattr(r, "condition_id", None)
    oi = _int_or_none((getattr(r, "extra", None) or {}).get("outcomeIndex"))
    if oi is None:
        oi = _int_or_none(getattr(r, "outcome_index", None))
    return (cid, oi) if (cid and oi is not None) else None


def rl_decisions(rl_rows, resolutions: dict) -> tuple[dict, int]:
    """{(condition_id, outcome_index): {resolved, won, held}} for the GAMMA-CONFIRMED LOST rows of a
    REDEEMABLE_LOST book, plus the count of FALSE LOSSES (RL rows gamma says WON -- dropped, never counted).
    Gamma stays the resolution authority (R3): a row whose market gamma calls unresolved/void/not_found is
    dropped too -- grounding may only ADD losses it can prove (the bias-down rule). Measured 2026-11-19:
    0 false losses in 6,383 RL rows across 26 pairs, but the referee is kept on purpose."""
    out: dict = {}
    false_losses = 0
    for r in rl_rows or []:
        k = _rl_key(r)
        if k is None:
            continue
        res = resolutions.get(k[0]) or {}
        if str(res.get("status") or "").lower() != "resolved":
            continue
        win = _int_or_none(res.get("winning_outcome_index"))
        if win is not None and k[1] == win:
            false_losses += 1
            continue
        out[k] = {"resolved": True, "won": False, "held": True}
    return out, false_losses


def ground_losses(activity_rows, closed_rows, resolutions: dict, *, activity_truncated: bool,
                  rl_rows=None, loss_side_complete: bool = False) -> LossGrounding:
    """Reconcile the /activity held-to-resolution set (unioned with the gamma-confirmed REDEEMABLE_LOST set
    when `rl_rows` is given -- the 2026-11-19 RL supplement) against the closed feed -> the HONEST win/loss
    counts + the A_only (dropped) losses/wins + a MEASURED completeness bound. HONEST = closed UNION A_only
    (A_only excludes keys already in closed, so no double-count; an RL key the activity window already found
    counts ONCE). Without RL, a TRUNCATED /activity window makes A_only a LOWER bound on both sides; with RL
    the LOSS side is point-in-time complete (losses sit in the open book until redeemed, and redeemed losers
    move to CLOSED -- measured 2026-11-19), so truncation remains only the WIN-side caveat."""
    ad = activity_decisions(activity_rows, resolutions)
    cd = closed_decisions(closed_rows)
    # coverage = fraction of the closed-feed decisions this /activity window actually reached (re-found). Without RL
    # it bounds the omission %; with RL it bounds only the WIN-side recovery (the loss side no longer depends on the
    # window). Computed off ALL activity decisions (any held/resolved status), since merely SEEING a closed cid's
    # trades in the window is what proves the window reached that far back.
    cd_keys = set(cd)
    coverage_pct = ((len(cd_keys & set(ad)) / len(cd_keys)) if cd_keys else None)
    aheld = {k: v for k, v in ad.items() if v["resolved"] and v["held"]}
    n_activity_held_resolved = len(aheld)
    rl_added = rl_false = 0
    if rl_rows is not None:
        rl_d, rl_false = rl_decisions(rl_rows, resolutions)
        rl_new = {k: v for k, v in rl_d.items() if k not in aheld}
        rl_added = sum(1 for k in rl_new if k not in cd)   # the losses RL alone contributed to A_only
        aheld.update(rl_new)                               # union BEFORE the A_only subtraction (the ruling)
    a_only = set(aheld) - set(cd)
    ao_w = sum(1 for k in a_only if aheld[k]["won"])
    ao_l = len(a_only) - ao_w
    cw = sum(1 for v in cd.values() if v["won"])
    cl = len(cd) - cw
    honest_w = cw + ao_w
    honest_l = cl + ao_l
    if rl_rows is not None:
        completeness = ("complete(loss-side: +rl & closed walked to exhaustion; activity windowed for wins)"
                        if loss_side_complete else
                        "windowed(+rl partial: a positions walk hit its page ceiling -- a_only losses are a lower bound)")
    else:
        completeness = ("windowed(activity truncated -- a_only losses are a lower bound)"
                        if activity_truncated else "complete(activity exhausted within window)")
    return LossGrounding(
        closed_wins=cw, closed_losses=cl, a_only_wins=ao_w, a_only_losses=ao_l,
        honest_wins=honest_w, honest_losses=honest_l,
        loss_omission_pct=((ao_l / honest_l) if honest_l > 0 else None),
        activity_truncated=bool(activity_truncated), completeness=completeness,
        n_activity_held_resolved=n_activity_held_resolved, n_closed=len(cd_keys), coverage_pct=coverage_pct,
        rl_added_losses=rl_added, rl_false_losses=rl_false,
        loss_side_complete=bool(rl_rows is not None and loss_side_complete),
        loss_basis=("activity+rl" if rl_rows is not None else "activity"))


# ── the async ORCHESTRATOR (Stage 5 R2a + RL supplement 2026-11-19) ──────────────────────────────────────────
# /activity depth stays at the v1-era 5000-row window (DELIBERATE: grounded win-side recovery must not shift
# silently; deepening is a separate decision). The CLOSED side is now walked LIVE TO EXHAUSTION (Jack ruling
# 2026-11-19): a stale stored closed basis paired with a current REDEEMABLE_LOST loss side would count recent
# losses while missing recent redeemed WINS, and the old closed_max=2000 cap made omission overcount on big
# redeemer wallets (redeemed losers beyond the cap read as "dropped"). REDEEMABLE_LOST (include_archived=true)
# supplies the held-to-zero losers the window never reaches. Category filtering stays DECOUPLED via the injected
# `category_of(row)->str`. The client is duck-typed: a client WITHOUT the 2026-11-19 methods (older fakes in
# tests) falls back to the legacy offset-paged closed fetch and no RL -- byte-identical legacy behaviour.
# The wallet-level fetch and the per-category grounding are SPLIT so a multi-category caller (the overnight
# batch runner) fetches each wallet's books ONCE and grounds every category from the same books.
async def fetch_wallet_grounding_books(client, wallet: str, *, activity_max_pages: int = 10,
                                       activity_limit: int = 500,
                                       closed_limit: int = 50, closed_max: int = 2000) -> dict:
    """ONE wallet-level fetch of everything grounding needs: the /activity window, the FULL live CLOSED book,
    and the REDEEMABLE_LOST book. Returns {'acts', 'activity_truncated', 'closed', 'closed_complete', 'rl',
    'rl_complete'}; 'rl' is None when the client has no RL surface (legacy fallback)."""
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
    if hasattr(client, "fetch_closed_positions_book"):
        book = await client.fetch_closed_positions_book(wallet)
        closed, closed_complete = list(book.rows), bool(book.complete)
    else:                                                     # legacy fake-client fallback: offset paging, capped
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
        closed_complete = False                               # the capped legacy path cannot assert exhaustion
    if hasattr(client, "fetch_redeemable_lost"):
        # A FAILED RL walk (exhausted retries, abandoned page) degrades to the legacy activity-only
        # grounding (rl=None -> loss_basis 'activity' -> the coverage gate applies as before) instead of
        # failing the whole wallet: bias down, never up (Board ruling 2026-11-19 #2). A PARTIAL walk
        # (page ceiling, complete=False) keeps its rows but loss_side_complete stays False.
        try:
            rb = await client.fetch_redeemable_lost(wallet)
            rl, rl_complete = list(rb.rows), bool(rb.complete)
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "loss_grounding: REDEEMABLE_LOST walk failed for %s -- grounding degrades to activity-only",
                wallet[:10])
            rl, rl_complete = None, False
    else:
        rl, rl_complete = None, False
    return {"acts": acts, "activity_truncated": trunc, "closed": closed,
            "closed_complete": closed_complete, "rl": rl, "rl_complete": rl_complete}


async def ground_category_from_books(client, books: dict, category: str, *, category_of):
    """Category-filter one wallet's fetched books, gamma-referee every cid, ground. Returns
    (LossGrounding, HonestWindowedReturn). The only network here is the gamma batch."""
    acts_cat = [a for a in books["acts"] if category_of(a) == category]
    closed_cat = [c for c in books["closed"] if category_of(c) == category]
    rl_all = books.get("rl")
    rl_cat = ([r for r in rl_all if category_of(r) == category] if rl_all is not None else None)
    cids = {getattr(x, "condition_id", None) for x in (acts_cat + closed_cat + (rl_cat or []))}
    cids = [c for c in cids if c]
    resolutions = (await client.fetch_market_resolutions(cids)) if cids else {}
    loss_side_complete = bool(rl_cat is not None and books.get("rl_complete") and books.get("closed_complete"))
    grounding = ground_losses(acts_cat, closed_cat, resolutions,
                              activity_truncated=books["activity_truncated"],
                              rl_rows=rl_cat, loss_side_complete=loss_side_complete)
    # Phase A (Jack ruling 3: ships WITH grounding, SAME fetch): the whale's honest WINDOWED cost-ROI (did winning
    # pay), $-accurate over the closed feed UNION the recovered held-to-worthless losers. The RL supplement's
    # gamma-confirmed LOST rows join the $ basis too (cost = entry cost, pnl = current_value - entry cost), so the
    # honest ROI and the grounded omission judge the SAME loss set. scoring stays pure; the gamma filter lives here.
    from . import scoring
    rl_lost_rows = None
    if rl_cat is not None:
        keys = rl_decisions(rl_cat, resolutions)[0]
        rl_lost_rows = [r for r in rl_cat if _rl_key(r) in keys]
    honest = scoring.honest_windowed_roi(acts_cat, closed_cat, resolutions, rl_rows=rl_lost_rows)
    return grounding, honest


async def fetch_and_ground_losses(client, wallet: str, category: str, *, category_of,
                                  activity_max_pages: int = 10, activity_limit: int = 500,
                                  closed_limit: int = 50, closed_max: int = 2000):
    """Fetch + category-filter + ground the loss set for (wallet, category). Returns (LossGrounding,
    HonestWindowedReturn). `category_of` maps an Activity/Closed/Position row to its category string (injected).
    Thin per-pair wrapper over fetch_wallet_grounding_books + ground_category_from_books (same signature as the
    pre-RL version; `closed_limit`/`closed_max` only govern the legacy fallback path)."""
    books = await fetch_wallet_grounding_books(client, wallet, activity_max_pages=activity_max_pages,
                                               activity_limit=activity_limit,
                                               closed_limit=closed_limit, closed_max=closed_max)
    return await ground_category_from_books(client, books, category, category_of=category_of)
