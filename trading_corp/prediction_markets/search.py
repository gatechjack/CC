"""Stage 4 SEARCH -- whale DISCOVERY -> prospects (candidate write). RUNG 1: the PURE CORE.

Two pieces, both pure + offline (no DB, no network) so they are fully unit-testable and
adversarially reviewable in isolation -- the two places Jack pointed the review at:

  1. THE INCREMENTAL-BACKFILL WATERMARK (`page_new_rows`). `/closed-positions` has NO time
     filter (only limit/offset; `polymarket_data_api_client.py:526`), so "incremental" = page
     NEWEST-FIRST and STOP at the wallet's watermark (MAX stored resolved_ts). That is correct
     ONLY IF the pages really are newest-first. This module does NOT assume it -- it ASSERTS it and
     RAISES `OutOfOrderPage` on any violation. A watermark that stops early on an unsorted page
     would SKIP trades, and a silently skipped trade is this platform's worst failure class.
     The assertion has TWO scopes, both required, because an early stop is safe only under GLOBAL
     (across-page) descending order -- an intra-page check alone leaves a page-seam skip open:
       - intra-page: `rows` must be non-increasing by resolution ts (always checked in incremental mode);
       - inter-page: the caller threads the previous page's minimum ts as `prev_min_ts`, and the first
         row of this page must not exceed it (checked when `prev_min_ts` is given), so pages 0..N are
         provably globally-descending and the stop is inductively sound over what we FETCHED.
     Residual inter-page obligation the caller (rung 2) MUST honor, documented here so it is not lost:
     the stop halts BEFORE the next (unfetched) seam is checked, so incremental mode is enabled ONLY
     after the box-verify proves `/closed-positions` is newest-first ACROSS offset (not just on page 0),
     OR the loop fetches one extra "confirm-horizon" page past the first stop. And the watermark MUST be
     a true lower-or-equal bound on unseen resolutions (derive `MAX(resolved_ts)` over exactly the rows a
     full re-page would store; a too-HIGH watermark silently skips -> sanity-bound `wm <= now` + force a
     periodic full re-page). `OutOfOrderPage` is RECOVERABLE: rung 2 catches it BEFORE any generic
     `except`, and falls back to full-paging that wallet IN THE SAME RUN. (STAGE4_SEARCH_PLAN §3, §4.)

  2. THE CANDIDATE SELECTION + RANK (`select_candidates`). Jack's ruled filter:
       - N >= `min_resolved` (50) resolved-in-category, WITH a fallback: a category yielding FEWER
         THAN `thin_sample_target` (10) qualifiers takes its TOP 10 by cost-ROI regardless of the
         floor, each sub-floor row flagged THIN_SAMPLE (Q1).
       - RECENCY: 30 days, via the OPEN-POSITION PROXY (has an open position in the category) OR a
         settlement within the window (Q2). Recency is a GATE that the thin-sample fallback still
         respects -- the fallback relaxes the EVIDENCE floor (N), never the currently-active gate, so
         a dormant whale never surfaces (documented; a one-line change if Jack rules otherwise).
       - Category ALLOWLIST (Q4): only the 15 ruled-in categories yield candidates; ingest stays
         all-categories (R5), exclusion is at SELECTION only.
       - RANK on cost-ROI, NEVER win% (win% is loss-omission-biased UP; F-1). win_rate is carried for
         DISPLAY only.
     Returns ALL that pass (Q3: no top-K cap) except in a fallback category, which caps at the
     thin-sample target. Nothing dropped is dropped SILENTLY -- `SelectionResult.excluded` counts every
     reason, so the run summary can state what did not surface and why.

RUNG 1 is BUILD + BOX-SCRATCH ONLY: no candidate write, no leaderboard/backfill run, no deploy. The
orchestration that calls these (discovery, backfill, candidate write, the /farm screen) lands in later
rungs, each its own authorization. Independent of R7 (order path untouched).

Spec: reports/prediction_markets/STAGE4_SEARCH_PLAN_2026-08-29.md (RULINGS + RUNG LADDER).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Iterable, NamedTuple

# ── Q4: the category ALLOWLIST (a code CONSTANT, Jack's ruled mechanism). The 15 ruled-in categories.
# SELECTION filters to these BEFORE writing candidates; a discovered position in an excluded category is
# still backfilled (R5: ingest is all-categories) but never surfaces as a prospect. Excluded by omission:
# cbb, fifwc, nascar, unknown. This is the SINGLE edit point -- cbb re-admits here after its probe
# (PM_REQUIREMENTS R2), no table, no migration. Every entry is a canonical category emitted by
# category.derive_category_from_slug / category.TAG_SLUG_TO_CATEGORY (verified against both maps).
CATEGORY_ALLOWLIST: frozenset[str] = frozenset({
    "mlb", "nba", "nfl", "nhl", "wnba", "cfb",
    "epl", "ucl", "soccer",
    "atp", "wta", "tennis",
    "cs2", "golf", "ufc", "fed",
})

# ── ruled selection defaults (STAGE4 Q1/Q2). Overridable per run; recorded on pm_search_run.
DEFAULT_MIN_RESOLVED_FLOOR = 50   # Q1: N>=50 resolved-in-category
DEFAULT_RECENCY_DAYS = 30         # Q2: 30-day recency window
DEFAULT_THIN_TARGET = 10          # Q1 fallback: a category with < this many qualifiers takes its TOP 10
_DAY_SECONDS = 86_400


# ════════════════════════════════════════════════════════════════════════════════════════════════
# 1. INCREMENTAL-BACKFILL WATERMARK
# ════════════════════════════════════════════════════════════════════════════════════════════════

class OutOfOrderPage(RuntimeError):
    """A `/closed-positions` page was NOT newest-first by resolution timestamp.

    Raised by `page_new_rows` in INCREMENTAL mode when a row's resolution ts is NEWER than a row
    before it -- which means stop-at-watermark could skip a newer trade sitting later in pagination.
    The caller MUST treat this as "the endpoint is not recency-sorted" and fall back to full-paging
    (offset 0..end) for the wallet. NEVER swallow it into a silent early stop.
    """


def _resolution_ts(row: Any) -> int:
    """Read a row's resolution timestamp as a non-negative int.

    Accepts either a mapped record dict (`resolved_ts`) or a raw `ClosedPositionRow`
    (`.timestamp`, which ingest maps to `resolved_ts`; `ingest.py:143`). Missing / non-numeric /
    non-positive -> 0, the "unreadable ts" sentinel: a 0-ts row sorts to the end of a newest-first
    page (so it can never HIDE a real-ts row behind it), and because its true recency is UNKNOWN,
    incremental mode treats it as AMBIGUOUS -> RE-INCLUDES it in new_rows (idempotent upsert) rather
    than silently dropping it -- "unreadable" must never mean "assumed old and skipped" (`page_new_rows`).
    """
    for attr in ("resolved_ts", "timestamp"):
        v = row.get(attr) if isinstance(row, dict) else getattr(row, attr, None)
        if v is None:
            continue
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        return iv if iv > 0 else 0
    return 0


class PageDecision(NamedTuple):
    new_rows: list      # rows to upsert (resolution ts >= watermark; boundary re-included, idempotent)
    stop: bool          # True => paging can halt (a below-watermark row proves the rest is already stored)


def page_new_rows(
    rows: Iterable[Any],
    *,
    watermark_ts: int | None,
    backfill_complete: bool,
    prev_min_ts: int | None = None,
    ts_of: Callable[[Any], int] = _resolution_ts,
) -> PageDecision:
    """Decide, for ONE `/closed-positions` page, which rows are NEW and whether paging may STOP.

    Two modes:

    * FULL mode -- `not backfill_complete` OR `watermark_ts` is None/<=0. There is no trustworthy
      watermark (the wallet has never been fully backfilled, or nothing is stored yet), so take the
      WHOLE page and never stop on a watermark. Order is IRRELEVANT here (we keep every row), so no
      ordering assertion is imposed -- the paging loop terminates on a short/empty page instead. This
      is exactly today's behaviour (`ingest.backfill_wallet` always full-pages).

    * INCREMENTAL mode -- `backfill_complete` AND a positive `watermark_ts`. Now we intend to STOP
      early once we cross into already-stored history, which is only sound on a newest-first STREAM:
        - ASSERT order (raise `OutOfOrderPage` otherwise -- the loud refusal that prevents a silent
          skip). The check is INTRA-page (this page non-increasing) AND, when the caller threads
          `prev_min_ts` (the previous page's minimum ts), the SEAM (`first row of this page <=
          prev_min_ts`) -- so pages 0..N are provably globally-descending, which is what makes the
          stop inductively sound over what we have fetched. On page 0 pass `prev_min_ts=None`.
        - NEW rows = those with `ts >= watermark_ts`, PLUS any `ts == 0` "unreadable ts" row (its
          recency is unknown -> re-include and upsert, never silently drop). The boundary `==
          watermark_ts` is RE-INCLUDED so a market co-resolved in the same second as the watermark is
          upserted again, never skipped -- upsert is idempotent, so re-fetch is free and skip unthinkable.
        - STOP = the page contains ANY row with `0 < ts < watermark_ts` (a real below-watermark
          resolution; descending then guarantees every subsequent FETCHED row is older -> already stored).

    Returns `PageDecision(new_rows, stop)`. Prefers RE-FETCH over SKIP at every ambiguity. See the
    module docstring for the residual inter-page obligation the caller must honor (the stop halts before
    the next, unfetched seam is checked -> rung 2 needs proven-global-sort or a confirm-horizon page).
    """
    rows = list(rows or [])
    incremental = bool(backfill_complete) and watermark_ts is not None and int(watermark_ts) > 0
    if not incremental:
        return PageDecision(new_rows=rows, stop=False)
    wm = int(watermark_ts)
    _assert_descending(rows, ts_of, prev_min_ts=prev_min_ts)
    new_rows = [r for r in rows if ts_of(r) >= wm or ts_of(r) == 0]
    stop = any(0 < ts_of(r) < wm for r in rows)
    return PageDecision(new_rows=new_rows, stop=stop)


def _assert_descending(rows: list, ts_of: Callable[[Any], int], *, prev_min_ts: int | None = None) -> None:
    """Raise `OutOfOrderPage` unless `rows` is non-increasing by `ts_of` (newest-first).

    `prev_min_ts` seeds the comparison with the PREVIOUS page's minimum ts, so the very first row of
    this page is checked against the page seam (inter-page order), not just row-to-row within the page.
    Non-strict (`>` raises, `==` allowed): two legs of a two-sided market -- or markets co-resolved in
    the same second -- share a ts and may legitimately straddle a page boundary.
    """
    prev: int | None = prev_min_ts
    for i, r in enumerate(rows):
        t = ts_of(r)
        if prev is not None and t > prev:
            where = "page seam" if i == 0 and prev_min_ts is not None else f"row {i}"
            raise OutOfOrderPage(
                f"/closed-positions not newest-first at {where}: resolution_ts={t} > "
                f"previous resolution_ts={prev}. Stop-at-watermark could skip a newer trade "
                f"later in pagination; refusing (caller must fall back to full-paging)."
            )
        prev = t


# ════════════════════════════════════════════════════════════════════════════════════════════════
# 2. CANDIDATE SELECTION + RANK
# ════════════════════════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class WalletCategoryStat:
    """One (wallet, category) row as read from pm_category_stats + the injected recency signal.

    `roi` is COST-ROI (pm_category_stats.roi = net_realized_pnl / cost_basis; db.py:156). It is the ONLY
    ranking metric. `win_rate` is carried for DISPLAY and is NEVER used to rank (F-1: /closed-positions
    under-reports held losses, biasing win% UP). `has_open_position` is the recency open-position proxy
    (Q2), injected from pm_open_position by the caller.
    """
    wallet: str
    category: str
    n_resolved: int
    roi: float | None
    last_resolved_ts: int | None = None
    has_open_position: bool = False
    win_rate: float | None = None
    user_name: str | None = None


@dataclass(frozen=True)
class Candidate:
    """A selected prospect. Written (later rung) as pm_watchlist(status='candidate', active=1)."""
    wallet: str
    category: str
    roi: float
    n_resolved: int
    thin_sample: bool       # surfaced via the <target-qualifiers fallback despite n_resolved < min_resolved
    recent_reason: str      # 'open_position' | 'settled_recent' -- which half of the recency gate passed
    rank_in_category: int   # 1-based, by cost-ROI desc (ties: n_resolved desc, then wallet asc)
    win_rate: float | None = None   # DISPLAY ONLY -- never a ranking input
    user_name: str | None = None


class SelectionResult(NamedTuple):
    candidates: list[Candidate]
    excluded: dict           # reason -> count (never silent): see reasons below
    thin_sample_categories: list[str]   # categories that fell back to the top-`target` rule


# excluded-reason keys (stable; the run summary reports each so nothing drops without a count). Every
# eligible (allowlisted + recent + roi-computable) row that is NOT written as a candidate lands under
# exactly ONE of BELOW_MIN_RESOLVED / BELOW_FALLBACK_CAP -- so discovered - candidates == sum(excluded),
# and the run summary can state precisely what did not surface and why. (Selection is a screen, not a
# silent gap.)
EX_CATEGORY_NOT_ALLOWED = "category_not_allowed"   # Q4 allowlist reject
EX_NOT_RECENT = "not_recent"                        # failed the 30d / open-position recency gate
EX_NO_COST_ROI = "no_cost_roi"                      # roi is None (cost_basis<=0) -> not rankable by the ruled metric
EX_BELOW_MIN_RESOLVED = "below_min_resolved"        # recent + in-category but n_resolved < floor in a NON-fallback category
EX_BELOW_FALLBACK_CAP = "below_fallback_cap"        # eligible + recent but ranked out of a fallback category's top-`target`


def _is_recent(stat: WalletCategoryStat, *, now_ts: int, window_days: int) -> tuple[bool, str | None]:
    """Recency gate (Q2). Open position in the category => active NOW (the proxy); else a settlement
    within the window. Returns (is_recent, reason). Open-position wins the label when both hold."""
    if stat.has_open_position:
        return True, "open_position"
    lt = stat.last_resolved_ts
    if lt is not None and int(lt) > 0 and int(lt) >= now_ts - window_days * _DAY_SECONDS:
        return True, "settled_recent"
    return False, None


def _rank_key(stat: WalletCategoryStat):
    """Deterministic cost-ROI-desc ordering key. roi is guaranteed non-None here (eligible pool filters
    None out). Ties break by MORE evidence (n_resolved desc) then wallet asc -- NEVER win%."""
    return (-float(stat.roi), -int(stat.n_resolved), stat.wallet)


def select_candidates(
    stats: Iterable[WalletCategoryStat],
    *,
    now_ts: int,
    min_resolved: int = DEFAULT_MIN_RESOLVED_FLOOR,
    recency_window_days: int = DEFAULT_RECENCY_DAYS,
    thin_sample_target: int = DEFAULT_THIN_TARGET,
    allowlist: frozenset[str] = CATEGORY_ALLOWLIST,
) -> SelectionResult:
    """Apply Jack's ruled selection filter to per-(wallet,category) stats -> ranked candidates.

    Per category (allowlisted + recent + cost-ROI-computable rows only):
      * qualifiers = eligible rows with n_resolved >= min_resolved.
      * >= thin_sample_target qualifiers  -> keep ALL qualifiers (Q3: no top-K cap), none flagged.
      * <  thin_sample_target qualifiers  -> keep the TOP `thin_sample_target` of the eligible pool by
        cost-ROI (Q1 fallback), flagging each row with n_resolved < min_resolved as thin_sample; the
        eligible-but-ranked-out remainder is counted under EX_BELOW_FALLBACK_CAP (never silent).

    Ranking is cost-ROI desc (ties: n_resolved desc, wallet asc). Recency is a GATE the fallback still
    respects. Returns a `SelectionResult` (candidates sorted by category asc, then rank asc) plus an
    excluded-reason tally and the list of categories that fell back.

    PRECONDITION: `stats` is unique per (wallet, category) -- it is read from pm_category_stats, whose PK
    is (wallet, category), so the caller (later rung) never passes duplicates. This function does NOT
    dedup; a duplicated (wallet, category) would emit two candidate rows (a later pm_watchlist write,
    PK (wallet, category), would then conflict) -- so the uniqueness lives at the SQL source, by design.
    """
    excluded: dict = {
        EX_CATEGORY_NOT_ALLOWED: 0,
        EX_NOT_RECENT: 0,
        EX_NO_COST_ROI: 0,
        EX_BELOW_MIN_RESOLVED: 0,
        EX_BELOW_FALLBACK_CAP: 0,
    }
    # eligible pool per category, carrying the recency reason alongside each stat
    by_cat: dict[str, list[tuple[WalletCategoryStat, str]]] = {}
    for s in stats:
        cat = (s.category or "").strip().lower()
        if cat not in allowlist:
            excluded[EX_CATEGORY_NOT_ALLOWED] += 1
            continue
        recent, reason = _is_recent(s, now_ts=now_ts, window_days=recency_window_days)
        if not recent:
            excluded[EX_NOT_RECENT] += 1
            continue
        if s.roi is None or not math.isfinite(s.roi):
            # not rankable by the ruled metric: None (cost_basis<=0) OR a non-finite NaN/inf (out of
            # db.py contract, but a NaN key would silently scramble the sort -> treat it like None)
            excluded[EX_NO_COST_ROI] += 1
            continue
        by_cat.setdefault(cat, []).append((s, reason))

    candidates: list[Candidate] = []
    thin_cats: list[str] = []
    for cat in sorted(by_cat):
        pool = sorted(by_cat[cat], key=lambda pr: _rank_key(pr[0]))   # cost-ROI desc, deterministic
        qualifiers = [pr for pr in pool if int(pr[0].n_resolved) >= min_resolved]
        if len(qualifiers) >= thin_sample_target:
            chosen = qualifiers
            # sub-floor recent rows exist but the category has enough qualifiers -> the N floor drops them
            excluded[EX_BELOW_MIN_RESOLVED] += (len(pool) - len(qualifiers))
        else:
            thin_cats.append(cat)
            chosen = pool[:thin_sample_target]
            excluded[EX_BELOW_FALLBACK_CAP] += max(0, len(pool) - len(chosen))
        for rank, (s, reason) in enumerate(chosen, start=1):
            candidates.append(Candidate(
                wallet=s.wallet,
                category=cat,
                roi=float(s.roi),
                n_resolved=int(s.n_resolved),
                thin_sample=(int(s.n_resolved) < min_resolved),
                recent_reason=reason,
                rank_in_category=rank,
                win_rate=s.win_rate,
                user_name=s.user_name,
            ))
    return SelectionResult(candidates=candidates, excluded=excluded, thin_sample_categories=thin_cats)


# ── the F-1 loss-omission caveat -- the exact on-screen label the prospect screen (later rung) MUST show
# beside any Search-produced list. Named here so the string has ONE definition and the web layer imports it
# rather than re-wording the bias. (STAGE4_SEARCH_PLAN §6; PM_REQUIREMENTS F-1.)
LOSS_OMISSION_CAVEAT = (
    "Win rates are over-stated: the completed-trades API under-reports held losses, wallet-dependently. "
    "This list is a SCREEN ranked on cost-ROI (never win%); Analyze is the promotion judge."
)
