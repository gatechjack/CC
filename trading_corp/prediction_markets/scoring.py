"""Prediction Markets -- ANALYZE SCORING (2026-09-12). The DETERMINISTIC promotion-judge that turns a whale's
record into a TIER + a sortable number + the trust-flagged dimensions the Sonnet narrator reasons over.

WHY DETERMINISTIC (not an LLM 'skill'): the SCORE must be stored, sortable, auditable and reproducible -- an LLM
cannot BE the score. So the reasoning framework lives here as tested code; the LLM writes only the one-sentence
verdict (analyze.narrate), reasoning over the numbers this module hands it with their trust-flags pre-attached.

★★ THE ONE TRAP THAT FOOLS EVERY READER: /closed-positions (-> pm_closed_position) DROPS held-to-worthless losses,
WALLET-DEPENDENTLY (SDTrading ~94%). `n_excluded=0` (the S3A quarantine) does NOT mean honest -- the dropped losers
NEVER ENTER our table, so the omission is INVISIBLE in our own data. That is why `omission_pct` is first-class with
an UNKNOWN state, why an UNGROUNDED whale can never reach PROMOTE, and why the equity-curve metrics
(Sortino/Sharpe/Calmar/drawdown/smoothness) are BANNED from the score (they INVERT the ranking -- a whale that drops
100% of its losses reads as a $0-drawdown, 100%-win-rate god; see drawdown_tell, kept as a LABELLED-FICTION SMELL).

Ruled by Jack 2026-09-12 (reports/prediction_markets/ANALYZE_UPGRADE_RESEARCH_2026-09-12.md S8-S9).
PURE (stdlib only): the caller (analyze) hands in the rows + the grounding + the journal copy figures.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── tiers (named for the ACTION Jack takes, not the whale's state -- Fork 2) ──────────────────────────────
TIER_INSUFFICIENT = "INSUFFICIENT_DATA"   # a GATE, not a low rank: not enough honest data to judge
TIER_PROMOTE = "PROMOTE"                   # clears every honest bar AND is grounded
TIER_WATCH = "WATCH"                       # an edge with a material caveat, OR ungrounded-but-promising
TIER_PASS = "PASS"                         # no honest edge

# ── thresholds. ★★ ALL INVENTED (Jack 2026-09-12): reasonable STARTING values, NOT data-derived. Nobody has
# evidence justifying 30 over 25 or 0.90 over 0.85. Recorded WITH provenance ON PURPOSE -- a threshold whose
# rationale is written can be tuned; one without it calcifies into folklore (the _SETTLED_LOOKBACK_SEC=160-days
# lesson: no rationale + squashed history -> an untouchable number that cost a full investigation to cut). Tune
# each against real promotion outcomes; do not treat any as sacred. ──
MIN_N_HONEST = 30           # INVENTED: below this many honest decided positions the record is too thin to judge.
DOM_EXTREME_NET = 0.60      # INVENTED: net-profit share above which the "record" IS one position -> the GATE.
DOM_HIGH_NET = 0.30         # INVENTED: net-profit share above which the record is concentration-caveated -> < PROMOTE.
COVERAGE_FLOOR = 0.90       # INVENTED (matches analyze.LOSS_COVERAGE_FLOOR): grounded coverage below which the
                            # win-rate is only a FLOOR (older losers lie beyond the /activity window) -> gate.
TWO_SIDED_HIGH = 0.40       # INVENTED: two-sided share above which the ROI is an upper bound (hedger/market-maker).
CHALK_HI = 0.85            # avg winning price at/above which the profile is favorite-farming (matches stats.CHALK_HI).
PASS_ROI = 0.0             # NOT invented: cost-ROI at/below zero is no edge, by definition.

SKILL_VERSION = "score-1"   # bump on ANY change to this module's thresholds/logic OR analyze's prompt -> cache miss.


@dataclass(frozen=True)
class Dominance:
    largest_pnl: float          # signed P&L of the single largest-|P&L| position
    largest_title: str
    gross_share: float | None   # |largest| / SUM|P&L| -- OVER-states on our feed (dropped losers would enlarge denom)
    net_share: float | None     # largest WIN / net P&L -- UNDER-states on our feed (honest is worse); >1.0 == one


def single_trade_dominance(pnls, titles=None) -> Dominance:
    """The share of a whale's P&L carried by its single largest position -- the beginner's-luck-vs-expert tell that
    needs NO equity curve. `pnls` = per-position realized P&L (signed). Returns BOTH shares because they bound
    DIFFERENTLY under the loss omission: gross_share is a conservative OVER-estimate (flags more concentration);
    net_share is an UNDER-estimate (the honest value is worse) and can EXCEED 1.0 when one win exceeds the whole net
    (the record is that one position). None shares when there is nothing to divide."""
    if not pnls:
        return Dominance(0.0, "", None, None)
    im = max(range(len(pnls)), key=lambda i: abs(pnls[i]))
    largest = float(pnls[im])
    title = str((titles[im] if titles and im < len(titles) else "") or "")
    gross = sum(abs(float(p)) for p in pnls)
    net = sum(float(p) for p in pnls)
    max_win = max((float(p) for p in pnls if p > 0), default=0.0)
    gross_share = (abs(largest) / gross) if gross > 0 else None
    net_share = (max_win / net) if net > 0 else None      # None when net<=0 (dominance is meaningless on a net loser)
    return Dominance(largest, title, gross_share, net_share)


@dataclass(frozen=True)
class DrawdownTell:
    """★ LABELLED FICTION -- NOT a risk figure and NEVER a score input. The max peak-to-trough of the
    resolved_ts-ordered cumulative realized P&L, over the (loss-OMITTED) pm_closed_position series. Kept ONLY as a
    SMELL: '$0 drawdown across 7,323 wins' is the single most legible tell that a whale is dropping its losses.
    The lower this is relative to a huge win count, the LESS trustworthy the whale -- the exact inverse of a real
    drawdown, which is why it must never rank."""
    max_drawdown: float
    n_wins: int
    n_losses: int


def drawdown_tell(pnls, wons) -> DrawdownTell:
    """Compute the labelled-fiction drawdown + the W/L context that makes it legible. `pnls` ordered by resolved_ts;
    `wons` parallel (1/0/None)."""
    cum = peak = mdd = 0.0
    for p in pnls:
        cum += float(p)
        if cum > peak:
            peak = cum
        if peak - cum > mdd:
            mdd = peak - cum
    w = sum(1 for x in wons if x == 1)
    l = sum(1 for x in wons if x == 0)
    return DrawdownTell(mdd, w, l)


@dataclass(frozen=True)
class HonestWindowedReturn:
    """Phase A (Jack ruling 3: ships WITH grounding, same /activity fetch). The whale's honest cost-ROI over the
    /activity window = /closed-positions UNION the recovered A_only held-to-resolution positions, $-accurate. It
    answers a DIFFERENT question from the grounded win-rate: the win-rate says whether they WIN, this says whether
    winning PAID. WINDOWED (the /activity ceiling) -> `coverage_pct` bounds it, and windowed is arguably RIGHT for a
    copy decision (recent behaviour predicts future copying; style drift). None roi when there is no cost basis."""
    honest_cost: float
    honest_pnl: float
    honest_roi: float | None
    n_positions: int          # closed + A_only decisions priced


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def honest_windowed_roi(activity_rows, closed_rows, resolutions: dict) -> HonestWindowedReturn:
    """Cost-based ROI over the honest windowed set. `activity_rows`/`closed_rows`/`resolutions` are the SAME rows
    loss_grounding.ground_losses consumes (so this rides the one grounding fetch). Closed rows contribute
    cost=total_bought*avg_price, pnl=realized_pnl. A_only held-to-resolution rows (in /activity, ABSENT from closed)
    contribute cost=net USDC committed, pnl = (won ? net_long_contracts - cost : -cost) -- i.e. a held-to-worthless
    loser loses its whole committed cost (the F-1 loss /closed-positions dropped). Pure; mirrors the
    loss_grounding held/won conventions."""
    # aggregate size + usdc per (cid, oi) from TRADE activity
    agg: dict = {}
    for a in activity_rows:
        if str(getattr(a, "type", "") or "").upper() != "TRADE":
            continue
        cid = getattr(a, "condition_id", None)
        oi = getattr(a, "outcome_index", None)
        if not cid or oi is None:
            continue
        try:
            oi = int(oi)
        except (TypeError, ValueError):
            continue
        e = agg.setdefault((cid, int(oi)), {"buy_sz": 0.0, "sell_sz": 0.0, "buy_usd": 0.0, "sell_usd": 0.0})
        side = str(getattr(a, "side", "") or "").upper()
        sz, usd = _f(getattr(a, "size", 0.0)), _f(getattr(a, "usdc_size", 0.0))
        if side == "BUY":
            e["buy_sz"] += sz; e["buy_usd"] += usd
        elif side == "SELL":
            e["sell_sz"] += sz; e["sell_usd"] += usd
    closed_keys = set()
    for c in closed_rows:
        cid = getattr(c, "condition_id", None)
        oi = getattr(c, "outcome_index", None)
        if cid is not None and oi is not None:
            try:
                closed_keys.add((cid, int(oi)))
            except (TypeError, ValueError):
                pass
    cost = pnl = 0.0
    n = 0
    # closed side: cost = total_bought * avg_price (the ruled ROI denominator); pnl = realized_pnl
    for c in closed_rows:
        cb = _f(getattr(c, "total_bought", 0.0)) * _f(getattr(c, "avg_price", 0.0))
        if cb <= 0:
            continue
        cost += cb; pnl += _f(getattr(c, "realized_pnl", 0.0)); n += 1
    # A_only side: held-to-resolution decisions in /activity, ABSENT from closed
    for (cid, oi), e in agg.items():
        if (cid, oi) in closed_keys:
            continue
        net_sz = e["buy_sz"] - e["sell_sz"]
        if net_sz <= max(0.5, 0.01 * e["buy_sz"]):         # not materially held (matches loss_grounding _HELD floor)
            continue
        r = resolutions.get(cid) or {}
        if str(r.get("status") or "").lower() != "resolved":
            continue
        committed = e["buy_usd"] - e["sell_usd"]           # net USDC still in the held position = its cost basis
        if committed <= 0:
            continue
        try:
            won = int(r.get("winning_outcome_index")) == oi
        except (TypeError, ValueError):
            won = False
        cost += committed
        pnl += (net_sz - committed) if won else (-committed)   # win: net_sz shares pay $1 each; loss: lose the cost
        n += 1
    roi = (pnl / cost) if cost > 0 else None
    return HonestWindowedReturn(cost, pnl, roi, n)


@dataclass(frozen=True)
class WhaleScore:
    wallet: str
    category: str
    tier: str
    reason: str                 # the SINGLE decisive factor, one short phrase (feeds the narrator + the store)
    sort_roi: float | None      # cost-based ROI -- the sort number; NEVER shown alone (always beside the below)
    n_resolved: int             # raw scoreable rows in pm_closed_position
    n_honest: int               # grounded decided count if grounded, else n_resolved (flagged via `grounded`)
    grounded: bool              # was loss-grounding applied? (False -> omission UNKNOWN -> cannot PROMOTE)
    omission_pct: float | None  # a_only_losses/honest_losses; None == UNKNOWN (ungrounded)
    coverage_pct: float | None  # how far back the /activity window reached (bounds the omission)
    omission_floor: bool        # the omission is a LOWER bound (windowed/low-coverage)
    honest_roi: float | None    # Phase A: the whale's honest WINDOWED cost-ROI (closed + recovered A_only $); None ungrounded
    dominance_net: float | None
    dominance_gross: float | None
    largest_pnl: float
    largest_title: str
    two_sided_pct: float | None
    avg_win_price: float | None
    chalk: bool
    dd_tell: float              # labelled fiction (see DrawdownTell)
    dd_wins: int
    dd_losses: int
    copy_fills: int             # REAL fills on this whale in our journal (display only, never a gate -- Ruling 2)
    copy_pnl: float | None      # realized copy P&L if computable, else None (thin sample -> display, not decide)
    skill_version: str = SKILL_VERSION
    flags: tuple = field(default_factory=tuple)


def build_score(*, wallet: str, category: str, pnls, wons, titles,
                sort_roi: float | None, n_resolved: int, two_sided_pct: float | None,
                avg_win_price: float | None, one_sided_roi: float | None,
                grounding=None, honest=None, copy_fills: int = 0, copy_pnl: float | None = None) -> WhaleScore:
    """Assemble the deterministic WhaleScore from the pieces the analyze orchestrator already computes. `pnls`/`wons`/
    `titles` are the per-position scoreable rows (resolved_ts-ordered) for dominance + the drawdown-tell. `grounding`
    is a loss_grounding.LossGrounding (or None -> UNGROUNDED -> omission UNKNOWN -> cannot PROMOTE). `honest` is a
    HonestWindowedReturn (Phase A, ships with grounding). `copy_*` are DISPLAY-ONLY (Ruling 2: never a gate)."""
    dom = single_trade_dominance(pnls, titles)
    ddt = drawdown_tell(pnls, wons)
    chalk = avg_win_price is not None and avg_win_price >= CHALK_HI
    grounded = grounding is not None
    omission_pct = getattr(grounding, "loss_omission_pct", None) if grounded else None
    coverage_pct = getattr(grounding, "coverage_pct", None) if grounded else None
    omission_floor = bool(getattr(grounding, "activity_truncated", False)) if grounded else False
    if grounded:
        hw, hl = getattr(grounding, "honest_wins", 0) or 0, getattr(grounding, "honest_losses", 0) or 0
        n_honest = int(hw) + int(hl)
    else:
        n_honest = int(n_resolved)
    honest_roi = getattr(honest, "honest_roi", None) if honest is not None else None
    tier, reason = assign_tier(n_honest=n_honest, dominance_net=dom.net_share, grounded=grounded,
                               coverage_pct=coverage_pct, sort_roi=sort_roi, honest_roi=honest_roi, chalk=chalk,
                               two_sided_pct=two_sided_pct, one_sided_roi=one_sided_roi)
    return WhaleScore(
        wallet=(wallet or "").lower(), category=category, tier=tier, reason=reason, sort_roi=sort_roi,
        n_resolved=int(n_resolved), n_honest=n_honest, grounded=grounded, omission_pct=omission_pct,
        coverage_pct=coverage_pct, omission_floor=omission_floor, honest_roi=honest_roi,
        dominance_net=dom.net_share, dominance_gross=dom.gross_share, largest_pnl=dom.largest_pnl,
        largest_title=dom.largest_title[:60], two_sided_pct=two_sided_pct, avg_win_price=avg_win_price, chalk=chalk,
        dd_tell=ddt.max_drawdown, dd_wins=ddt.n_wins, dd_losses=ddt.n_losses,
        copy_fills=int(copy_fills), copy_pnl=copy_pnl)


def assign_tier(*, n_honest: int, dominance_net: float | None, grounded: bool, coverage_pct: float | None,
                sort_roi: float | None, honest_roi: float | None, chalk: bool,
                two_sided_pct: float | None, one_sided_roi: float | None) -> tuple[str, str]:
    """(tier, one-phrase reason). Top-down; first match wins. See ANALYZE_UPGRADE_RESEARCH S9a. Ruling 2: real copy
    P&L is NEVER a gate here (12 fills is noise) -- it is display-only. Ungrounded -> cannot PROMOTE (omission UNKNOWN)."""
    # 1. INSUFFICIENT-DATA GATE (not a low rank -- 'watch, not yet judgeable')
    if n_honest < MIN_N_HONEST:
        return TIER_INSUFFICIENT, "n_honest=%d < %d (too thin to judge)" % (n_honest, MIN_N_HONEST)
    if dominance_net is not None and dominance_net > DOM_EXTREME_NET:
        return TIER_INSUFFICIENT, "one position is %.0f%% of net -- a bet, not a track record" % (100 * dominance_net)
    if grounded and coverage_pct is not None and coverage_pct < COVERAGE_FLOOR:
        return TIER_INSUFFICIENT, "grounding coverage %.0f%% < %.0f%% -- win-rate is only a floor" % (
            100 * coverage_pct, 100 * COVERAGE_FLOOR)
    # 2. PASS -- no honest edge
    if sort_roi is None or sort_roi <= PASS_ROI:
        return TIER_PASS, "no cost-ROI edge"
    if grounded and honest_roi is not None and honest_roi <= PASS_ROI:
        return TIER_PASS, "honest windowed ROI %.0f%% -- the edge vanishes once dropped losses are added" % (100 * honest_roi)
    if chalk and sort_roi < 0.10:
        return TIER_PASS, "favorite-farming (chalk) with a negligible %.0f%% edge" % (100 * sort_roi)
    # 3. PROMOTE -- grounded AND every honest bar cleared
    if grounded:
        two_sided_ok = (two_sided_pct is None or two_sided_pct < TWO_SIDED_HIGH
                        or (one_sided_roi is not None and one_sided_roi > 0))
        if (honest_roi is not None and honest_roi > PASS_ROI
                and (dominance_net is None or dominance_net < DOM_HIGH_NET)
                and two_sided_ok and not chalk):
            return TIER_PROMOTE, "honest ROI %.0f%%, diversified, edge survives grounding" % (100 * honest_roi)
    # 4. WATCH -- an edge with a caveat, OR ungrounded-but-promising
    if not grounded:
        return TIER_WATCH, "would PROMOTE if it grounds clean -- omission UNKNOWN, run grounding"
    if dominance_net is not None and dominance_net >= DOM_HIGH_NET:
        return TIER_WATCH, "concentrated -- %.0f%% of net from one position" % (100 * dominance_net)
    if two_sided_pct is not None and two_sided_pct >= TWO_SIDED_HIGH:
        return TIER_WATCH, "hedger/market-maker (%.0f%% two-sided) -- ROI is an upper bound" % (100 * two_sided_pct)
    if chalk:
        return TIER_WATCH, "favorite-farming profile with a real but low-edge return"
    return TIER_WATCH, "an edge with a caveat"
