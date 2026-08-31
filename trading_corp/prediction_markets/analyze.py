"""On-demand ANALYZE for a whale in ONE category (CP3b-2). A FORK of the legacy PCT analyzer, NOT a reuse.

Why a fork and not an import (Jack ruling 2026-08-25, Q6): the legacy analyzer lives in the ENGINE
(`trading_corp/agents/polymarket_whale_analyst.py` + `data/polymarket_whale_audit.py` +
`agents/research/polymarket_whale_audit_cache.py`) and serves the LIVE legacy PCT division. pm_web is
standalone (no engine imports) and the whole platform is zero-engine-edits -- so we COPY the narration
guardrails and the gate structure into the PM package. A live-division incident is possible ONLY if we
share/import/edit; forking makes that structurally impossible.

What changed vs the legacy (Q2/Q3 rulings):
  * DATA SOURCE = pm_closed_position ONLY (resolved positions), category-filtered `WHERE wallet=? AND
    category=?`. NO /activity, NO hybrid. The legacy walks /activity to exhaustion for fill-level analytics
    (clustering, sell-footprint, edge-profile, REDEEM-grounded realized PnL) -- pm_closed_position has NO
    fills (one row per resolved market), so ALL of that is DROPPED. CategoryConcentrationReport is NOT
    ported (category is the FILTER here, not a derived concentration metric). The verdict is LEANER:
    P&L / win-rate / cost-ROI over the settled positions, with the caveats the scoreboard already carries.
  * The deterministic numbers are recomputed FRESH from pm_closed_position through the ONE canonical
    scoreable predicate (`db.scoreable_where`) and the SAME formulas as `stats.rollup` -- so Analyze MATCHES
    the scoreboard when the weekly rollup is current, and TRANSPARENTLY shows fresher numbers (with a
    reconciliation note) when pm_closed_position has advanced past the last rollup. It never silently diverges.
  * COST cap = $20/day (`PM_ANALYZE_DAILY_CAP_USD`; Jack ruling -- the legacy code default is $1.00 and the
    P2_PLAN §7.4 doc wrongly said $2), enforced against ONE visible per-day counter in the PM DB
    (pm_analysis_cost). PM must NEVER write agent_state (the legacy's counter) -- isolation.
  * CACHE key = (wallet, category, skill_version) in pm_analysis_cache. `skill_version` REPLACES the legacy
    24h TTL as the ONLY invalidation axis. A cache HIT returns the stored report and spends NOTHING. Only a
    SUCCESSFUL verdict is cached -- reasoned-nulls recompute, so the moment the ANTHROPIC key is wired the
    next analyze narrates fresh instead of serving a stale "llm_unavailable".

Narration is SYNCHRONOUS here (chat.invoke, not ainvoke): pm_web runs every DB read through
`asyncio.to_thread(<sync fn>)`, so a sync narrator drops straight into that model (no nested event loop).

THE KEY IS NOT WIRED YET (e3, Jack's hands). is_llm_available() reads ANTHROPIC_API_KEY, which does NOT
resolve in the standalone pm_web process today (proven read-only 2026-08-25) -> narration returns the
`llm_unavailable` reasoned-null and the deterministic report renders without a verdict. Importable
langchain/azure libraries are CAPABILITY, not a working token -- do not read the presence of the library as
the key being reachable.

Spec: reports/prediction_markets/P2_PLAN.md §7.4 (amended in this commit); CP3b-2 rulings 2026-08-25.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, replace

from . import stats
from .db import SCOREABLE_PREDICATE_SQL, scoreable_where

log = logging.getLogger(__name__)

# ── forked constants (pinned in the PM package; NOT read from engine config/agents.yaml) ──────────────
PM_ANALYZE_MODEL = "claude-haiku-4-5-20251001"   # was get_model_for('polymarket_whale_analyst'); pinned so
                                                 # Analyze can't have its model swapped out from under it.
PM_ANALYZE_MAX_OUTPUT_TOKENS = 220
PM_ANALYZE_DAILY_CAP_USD = 20.00                 # Jack ruling 2026-08-25 (legacy code=$1.00; §7.4 doc said $2)
PM_ANALYZE_SKILL_VERSION = "3"                   # bump on ANY prompt/model/report-shape change -> cache miss
#   "1"->"2" (2026-08-31, Stage 5 R2b): PMAnalysisReport gained the loss-completeness fields (re-grounded loss set).
#   "2"->"3" (2026-08-31, Stage 5 R2c + prompt rung): the re-grounded loss set now FLOWS into the narrator prompt
#     (a top caveat tier + the honest win/loss lines), so the promotion-judge verdict itself reasons about the F-1
#     omission -- not just the printed table. Settle this at "3" BEFORE the Anthropic key is wired, so the first
#     PAID narration a wallet gets is the final-form one (key-last ordering, Jack 2026-08-31).
# Haiku price per 1M tokens -- forked from agents/research/cost.py (the 'claude-haiku-4-5-20251001' row).
_HAIKU_PRICE = {"input": 0.80, "output": 4.0}

# null-reason taxonomy. The FOUR LLM gates are preserved verbatim from the legacy narrator; `no_resolved_positions`
# is the additional DATA-level refusal (Jack: "refuses honestly at zero rows") -- distinct from the LLM being off.
NULL_DISABLED = "disabled_by_flag"
NULL_NO_DATA = "no_resolved_positions"
NULL_CAP = "daily_cap_hit"
NULL_UNAVAILABLE = "llm_unavailable"
NULL_ERROR = "llm_error"

# human-readable reasons (module-owned so the page and the CLI read the SAME words)
NULL_REASON_LABELS = {
    NULL_DISABLED: "narration disabled for this run",
    NULL_NO_DATA: "no resolved positions in this category to narrate",
    NULL_CAP: "daily analysis cost cap reached",
    NULL_UNAVAILABLE: "LLM narrator unavailable (no Anthropic key reachable from pm_web)",
    NULL_ERROR: "the narration call failed",
}


# ── report shape ─────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SampleRow:
    """One illustrative resolved position (largest by |realized_pnl|). Drill/colour only, never a rank."""
    title: str
    condition_id_short: str
    outcome: str | None
    outcome_index: int | None
    avg_price: float | None
    cost_basis: float | None
    realized_pnl: float | None
    won: int | None
    resolved_ts: int | None
    pnl_suspect: int


@dataclass(frozen=True)
class PMAnalysisReport:
    """The deterministic Analyze report for ONE (wallet, category). Numbers are fresh from
    pm_closed_position through the §3A scoreable predicate; caveats mirror the scoreboard. The verdict fields
    are filled by narrate() (or left as a reasoned-null)."""
    wallet: str
    category: str
    user_name: str | None
    backfill_complete: bool
    # counts (per-pair -- Q2: n-counts become per-pair with the category filter)
    n_total_rows: int          # every pm_closed_position row in the (wallet, category) slice
    n_resolved: int            # scoreable (pnl_suspect=0)
    n_excluded: int            # §3A quarantined
    n_anomaly: int             # §3A clause-(a) flag count (recorded, not excluded)
    wins: int
    losses: int
    # scoreable aggregates (same formulas as stats.rollup)
    win_rate: float | None
    net_realized_pnl: float
    total_bought: float        # NOTIONAL sum (NOT cost)
    cost_basis: float          # the ROI denominator
    roi: float | None          # cost-based (THE metric); None if cost_basis<=0
    roi_notional: float | None # net/total_bought -- NOT ranked, legacy comparison only
    avg_win_price: float | None
    chalk: bool                # avg_win_price >= 0.85
    contested: bool            # avg_win_price < 0.70
    # structure + data-quality caveats (fresh, mirroring the rollup)
    n_condition_ids: int | None
    two_sided_pct: float | None
    onesided_roi: float | None # UPPER BOUND (excludes hedged markets, survivorship-caveated)
    onesided_n: int | None
    data_quality: str | None   # 'contaminated' | None
    dq_count_pct: float
    dq_dollar_pct: float
    # thinness / data state
    data_state: str            # 'ok' (n>=min) | 'thin' (0<n<min) | 'empty' (n==0)
    all_quarantined: bool      # n_resolved==0 AND n_excluded>0 (the 4751346/nfl case)
    min_resolved: int          # the thin threshold, so 'thin' cites its cutoff
    # reconciliation vs the weekly rollup (never silent -- shipped reconcile() philosophy)
    rollup_n_resolved: int | None
    reconciled: bool
    recon_note: str | None
    # provenance
    generated_ts: int
    skill_version: str
    samples: tuple[SampleRow, ...] = ()
    # verdict (filled by narrate; a reasoned-null leaves verdict None + null_reason set)
    verdict: str | None = None
    null_reason: str | None = None
    model: str | None = None
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    served_from_cache: bool = False
    # ── Stage 5 LOSS-COMPLETENESS (re-grounded from /activity; None when not grounded) ──
    # The core wins/losses/win_rate above stay /closed-positions-based (rollup + scoreboard consistency -- Stage 5
    # does NOT re-plumb the platform rollup, F-1). These SEPARATE fields carry the HONEST re-grounded loss set +
    # the MEASURED bias, so Analyze tells the operator how honest its own input is per whale. Defaults keep the
    # dataclass backward-compatible (an ungrounded/offline analysis leaves them None/False).
    loss_grounded: bool = False           # True iff the loss set was re-grounded from /activity (the promotion judge)
    honest_wins: int | None = None        # /closed-positions UNION A_only
    honest_losses: int | None = None
    a_only_losses: int | None = None      # losses /closed-positions DROPPED (the F-1 omission, recovered)
    loss_omission_pct: float | None = None  # a_only_losses / honest_losses -- the MEASURED bias for THIS whale
    loss_completeness: str | None = None  # the measured bound ('complete...' | 'windowed(...lower bound)')

    @property
    def is_thin(self) -> bool:
        return self.data_state == "thin"

    @property
    def is_empty(self) -> bool:
        return self.data_state == "empty"

    @property
    def null_reason_label(self) -> str | None:
        return NULL_REASON_LABELS.get(self.null_reason) if self.null_reason else None


@dataclass(frozen=True)
class NarrationResult:
    narration: str | None
    null_reason: str | None
    cost_usd: float
    tokens_in: int
    tokens_out: int
    model: str | None


# ── deterministic report (pm_closed_position ONLY; reuses the ONE predicate + stats formulas) ─────────
def build_pm_analysis(conn, wallet: str, category: str, *, now_ts: int,
                      min_resolved: int | None = None,
                      skill_version: str = PM_ANALYZE_SKILL_VERSION,
                      loss_grounding=None) -> PMAnalysisReport:
    """Aggregate the (wallet, category) slice of pm_closed_position into the deterministic report. NO LLM,
    NO write. Every scoreable metric filters through `db.scoreable_where()` (the ONE §3A predicate) and uses
    the SAME formulas as `stats.rollup` -- see that function for the parity contract (roi cost-based, roi
    guarded on cost_basis>0, data_quality on EITHER count OR $-weighted fraction, etc.)."""
    wallet = (wallet or "").lower()
    if min_resolved is None:
        min_resolved = stats.DEFAULT_MIN_RESOLVED
    pred = SCOREABLE_PREDICATE_SQL

    core = conn.execute(
        "SELECT "
        f" SUM(CASE WHEN {pred} THEN 1 ELSE 0 END) AS n_resolved, "
        f" SUM(CASE WHEN {pred} AND won=1 THEN 1 ELSE 0 END) AS wins, "
        f" SUM(CASE WHEN {pred} AND won=0 THEN 1 ELSE 0 END) AS losses, "
        f" SUM(CASE WHEN {pred} THEN realized_pnl ELSE 0 END) AS net, "
        f" SUM(CASE WHEN {pred} THEN total_bought ELSE 0 END) AS tb, "
        f" SUM(CASE WHEN {pred} THEN cost_basis ELSE 0 END) AS cb, "
        f" AVG(CASE WHEN {pred} AND won=1 THEN avg_price END) AS avg_win_price, "
        " SUM(CASE WHEN pnl_suspect=1 THEN 1 ELSE 0 END) AS n_excluded, "
        " SUM(CASE WHEN pnl_anomaly=1 THEN 1 ELSE 0 END) AS n_anomaly, "
        " SUM(ABS(COALESCE(realized_pnl,0))) AS abs_all, "
        " SUM(CASE WHEN pnl_suspect=1 THEN ABS(COALESCE(realized_pnl,0)) ELSE 0 END) AS abs_excl, "
        " COUNT(*) AS n_total "
        "FROM pm_closed_position WHERE wallet=? AND category=?",
        (wallet, category)).fetchone()

    n_resolved = int(core["n_resolved"] or 0)
    wins = int(core["wins"] or 0)
    losses = int(core["losses"] or 0)
    n_excluded = int(core["n_excluded"] or 0)
    n_anomaly = int(core["n_anomaly"] or 0)
    n_total = int(core["n_total"] or 0)
    net = float(core["net"] or 0.0)
    tb = float(core["tb"] or 0.0)
    cb = float(core["cb"] or 0.0)
    avg_win_price = core["avg_win_price"]
    decided = wins + losses
    win_rate = (wins / decided) if decided > 0 else None
    roi = (net / cb) if cb > 0 else None                 # §13 dec 11: cost-based, guarded
    roi_notional = (net / tb) if tb > 0 else None
    abs_all = float(core["abs_all"] or 0.0)
    dq_count_pct = (n_excluded / n_total) if n_total > 0 else 0.0
    dq_dollar_pct = (float(core["abs_excl"] or 0.0) / abs_all) if abs_all > 0 else 0.0
    data_quality = ("contaminated"
                    if (dq_count_pct > stats.DATA_QUALITY_THRESHOLD or dq_dollar_pct > stats.DATA_QUALITY_THRESHOLD)
                    else None)
    chalk = avg_win_price is not None and avg_win_price >= stats.CHALK_HI
    contested = avg_win_price is not None and avg_win_price < stats.CONTESTED_LO

    # two-sided structure over ALL rows (mirrors stats.rollup's DISTINCT-outcome_index pass)
    tsr = conn.execute(
        "SELECT COUNT(*) AS n_cond, SUM(CASE WHEN n_out > 1 THEN 1 ELSE 0 END) AS n_two FROM "
        "(SELECT condition_id, COUNT(DISTINCT outcome_index) AS n_out FROM pm_closed_position "
        " WHERE wallet=? AND category=? GROUP BY condition_id)",
        (wallet, category)).fetchone()
    n_cond = int(tsr["n_cond"] or 0)
    two_sided_pct = (int(tsr["n_two"] or 0) / n_cond) if n_cond > 0 else None

    # one-sided directional slice (mirrors stats._rollup_onesided): scoreable rows on condition_ids the whale
    # held on a SINGLE outcome_index. UPPER BOUND -- excludes hedged markets, so it is optimistic (§13A(f)).
    osr = conn.execute(
        "SELECT SUM(1) AS n, SUM(CASE WHEN p.won=1 THEN 1 ELSE 0 END) AS wins, "
        " SUM(p.realized_pnl) AS net, SUM(p.cost_basis) AS cb "
        "FROM pm_closed_position p "
        "JOIN (SELECT condition_id FROM pm_closed_position WHERE wallet=? AND category=? "
        "      GROUP BY condition_id HAVING COUNT(DISTINCT outcome_index)=1) oc ON p.condition_id=oc.condition_id "
        "WHERE p.wallet=? AND p.category=? AND " + scoreable_where("p"),
        (wallet, category, wallet, category)).fetchone()
    onesided_n = int(osr["n"] or 0)
    os_cb = float(osr["cb"] or 0.0)
    onesided_roi = (float(osr["net"] or 0.0) / os_cb) if os_cb > 0 else None

    # illustrative rows -- largest by |realized_pnl| across the WHOLE slice (suspect rows marked, not hidden)
    samples = tuple(
        SampleRow(
            title=(s["title"] or "")[:70], condition_id_short=(s["condition_id"] or "")[:18],
            outcome=s["outcome"], outcome_index=s["outcome_index"], avg_price=s["avg_price"],
            cost_basis=s["cost_basis"], realized_pnl=s["realized_pnl"], won=s["won"],
            resolved_ts=s["resolved_ts"], pnl_suspect=int(s["pnl_suspect"] or 0))
        for s in conn.execute(
            "SELECT title, condition_id, outcome, outcome_index, avg_price, cost_basis, realized_pnl, won, "
            " resolved_ts, pnl_suspect FROM pm_closed_position WHERE wallet=? AND category=? "
            "ORDER BY ABS(COALESCE(realized_pnl,0)) DESC LIMIT 5",
            (wallet, category)).fetchall())

    # data-state (thinness travels with the verdict): empty (refuse honestly) / thin / ok
    if n_resolved == 0:
        data_state = "empty"
    elif n_resolved < min_resolved:
        data_state = "thin"
    else:
        data_state = "ok"
    all_quarantined = (n_resolved == 0 and n_excluded > 0)

    # whale identity + backfill gate
    w = conn.execute("SELECT user_name, backfill_complete FROM pm_whale WHERE wallet=?", (wallet,)).fetchone()
    user_name = (w["user_name"] if w else None) or None
    backfill_complete = bool(w["backfill_complete"]) if w else False

    # reconcile the FRESH scoreable count against the weekly rollup -- surface staleness, never silence it
    cs = conn.execute("SELECT n_resolved FROM pm_category_stats WHERE wallet=? AND category=?",
                      (wallet, category)).fetchone()
    rollup_n = int(cs["n_resolved"]) if (cs and cs["n_resolved"] is not None) else None
    if rollup_n is None or rollup_n == n_resolved:
        reconciled, recon_note = True, None
    else:
        reconciled = False
        recon_note = ("the weekly rollup (scoreboard) shows %d scoreable here; the live rows show %d -- the "
                      "rollup is stale (a refresh is pending). These numbers are fresh from the rows."
                      % (rollup_n, n_resolved))

    return PMAnalysisReport(
        wallet=wallet, category=category, user_name=user_name, backfill_complete=backfill_complete,
        n_total_rows=n_total, n_resolved=n_resolved, n_excluded=n_excluded, n_anomaly=n_anomaly,
        wins=wins, losses=losses, win_rate=win_rate, net_realized_pnl=net, total_bought=tb, cost_basis=cb,
        roi=roi, roi_notional=roi_notional, avg_win_price=avg_win_price, chalk=chalk, contested=contested,
        n_condition_ids=(n_cond or None), two_sided_pct=two_sided_pct, onesided_roi=onesided_roi,
        onesided_n=(onesided_n or None), data_quality=data_quality, dq_count_pct=dq_count_pct,
        dq_dollar_pct=dq_dollar_pct, data_state=data_state, all_quarantined=all_quarantined,
        min_resolved=min_resolved, rollup_n_resolved=rollup_n, reconciled=reconciled, recon_note=recon_note,
        generated_ts=int(now_ts), skill_version=skill_version, samples=samples,
        # Stage 5: carry the re-grounded loss set (the promotion judge) + the measured bias, WITHOUT touching the
        # /closed-positions-based wins/losses above (rollup + scoreboard consistency, F-1). None when not grounded.
        loss_grounded=(loss_grounding is not None),
        honest_wins=(loss_grounding.honest_wins if loss_grounding is not None else None),
        honest_losses=(loss_grounding.honest_losses if loss_grounding is not None else None),
        a_only_losses=(loss_grounding.a_only_losses if loss_grounding is not None else None),
        loss_omission_pct=(loss_grounding.loss_omission_pct if loss_grounding is not None else None),
        loss_completeness=(loss_grounding.completeness if loss_grounding is not None else None))


def analysis_flags(rep: PMAnalysisReport) -> list[str]:
    """Reuse the ONE scoreboard flag deriver so Analyze carries IDENTICAL tokens to the scoreboard/farm/CLI."""
    return stats.scoreboard_flags({
        "backfill_complete": rep.backfill_complete, "chalk": rep.chalk, "contested": rep.contested,
        "data_quality": rep.data_quality, "dq_count_pct": rep.dq_count_pct,
        "dq_dollar_pct": rep.dq_dollar_pct, "n_anomaly": rep.n_anomaly})


# ── (de)serialization for the cache ──────────────────────────────────────────────────────────────────
def report_to_json(rep: PMAnalysisReport) -> str:
    return json.dumps(asdict(rep))


def report_from_json(s: str) -> PMAnalysisReport:
    d = json.loads(s)
    d["samples"] = tuple(SampleRow(**sr) for sr in d.get("samples") or [])
    return PMAnalysisReport(**d)


# ── forked LLM helpers (langchain is a third-party lib, NOT an engine module -- forking the wrapper) ───
def is_llm_available() -> bool:
    """True iff langchain-anthropic is importable AND ANTHROPIC_API_KEY is set. Forked verbatim from
    agents/llm.is_llm_available. NOTE: an importable library is CAPABILITY, not a working token -- the key
    is not wired into pm_web yet (e3, Jack's hands), so this returns False in production today."""
    import os
    if not os.getenv("ANTHROPIC_API_KEY"):
        return False
    try:
        import langchain_anthropic  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False


def _build_chat_model(max_tokens: int = PM_ANALYZE_MAX_OUTPUT_TOKENS):
    """Forked from agents/llm.build_chat_model, pinned to Haiku (Analyze's only model). SYNC caller."""
    from langchain_anthropic import ChatAnthropic  # type: ignore
    return ChatAnthropic(model=PM_ANALYZE_MODEL, max_tokens=max_tokens, temperature=0.1)


def _extract_usage(resp: object) -> dict:
    """Anthropic-native usage off a langchain response, native block preferred (forked from the analyst)."""
    rm = getattr(resp, "response_metadata", None) or {}
    if isinstance(rm, dict):
        u = rm.get("usage")
        if isinstance(u, dict) and u:
            return u
    um = getattr(resp, "usage_metadata", None) or {}
    return um if isinstance(um, dict) else {}


def _cost_for_usage(usage: dict) -> float:
    """Haiku-only cost from a usage dict. Forked from agents/research/cost.cost_for_anthropic_usage with the
    Haiku price row pinned (cache-read billed 10% of input, cache-creation 125%)."""
    if not usage:
        return 0.0
    in_tok = int(usage.get("input_tokens") or 0) + int(usage.get("prompt_tokens") or 0)
    out_tok = int(usage.get("output_tokens") or 0) + int(usage.get("completion_tokens") or 0)
    cc = int(usage.get("cache_creation_input_tokens") or 0)
    cr = int(usage.get("cache_read_input_tokens") or 0)
    p = _HAIKU_PRICE
    return ((in_tok / 1_000_000.0) * p["input"] + (out_tok / 1_000_000.0) * p["output"]
            + (cc / 1_000_000.0) * p["input"] * 1.25 + (cr / 1_000_000.0) * p["input"] * 0.10)


_SYSTEM_PROMPT = """You narrate a Polymarket whale's RESOLVED-POSITION record in ONE market category, in \
2-4 plain-language sentences for a busy operator deciding whether the whale is worth copying.

The numbers are computed deterministically from the whale's SETTLED positions in this category only. You do \
NOT see individual fills, entry/exit timing, or partial sells -- only the settled outcome of each position. \
Do not speculate about anything you cannot see.

CRITICAL RULES:
- DO NOT perform arithmetic. Every number you cite must appear VERBATIM in the user message. If only a \
percentage is given, use it as written; never recompute or convert.
- Never override or soften a flag. If the data says CONTAMINATED, or the sample is thin, say so plainly.
- Describe; do not recommend. The operator decides whether to copy; you only characterize the record.
- Tone: factual, dispassionate, like a quant summarizing a screen. No hedging words unless the data is \
genuinely ambiguous.
- Lead with the most decision-relevant caveat, in this priority:
  1) LOSS SET MATERIALLY INCOMPLETE -- if a "Loss completeness" section is present AND it recovered \
held-to-worthless losses (a_only > 0), the win rate above is OVER-STATED: /closed-positions dropped real losses. \
Lead with the honest win/loss and the omission %, and say the copyable edge is smaller than the headline win rate \
implies. (If NO "Loss completeness" section is present, say NOTHING about this -- do not speculate about omission.)
  2) data quality CONTAMINATED -- the headline rests on a §3A-filtered subset
  3) thin sample (n_resolved below the stated threshold) -- too few settled positions to trust the rate
  4) two-sided share high -- the whale hedges / market-makes, so the one-sided ROI is an UPPER BOUND, not a \
copyable return
  5) CHALK (avg winning price >= 0.85) -- favorite-farming; a high win rate at these prices carries little edge
  6) CONTESTED (avg winning price < 0.70) -- contrarian entries
  7) a clean, adequately-sampled record if none of the above apply

Vocabulary cues (apply only when the condition holds):
- cost-based ROI is THE metric; notional ROI is shown for legacy comparison only -- never lead with it
- one-sided ROI is an UPPER BOUND (excludes hedged markets; an entry-time copier cannot pick the survivors)
- high two_sided_pct -> "hedges / market-makes"
- avg_win_price >= 0.85 -> "favorite-farming profile"; avg_win_price < 0.70 -> "contrarian profile"
- data_quality contaminated -> "the record rests on a filtered subset"
- Loss completeness present with a_only > 0 -> "the win rate is over-stated; ~X% of this whale's losses were \
omitted by the completed-trades API, so the honest record is <honest W/L>"; if it shows a LOWER BOUND (activity \
windowed), add "and that omission is a floor -- there may be more beyond the window"
- Loss completeness present with a_only = 0 -> "re-grounding confirms the loss set is complete -- the win rate is \
not inflated by the completed-trades omission"

Output: 2-4 sentences of prose. No bullets, no headings, no markdown."""


def _fmt_pct_signed(x) -> str:
    return "n/a" if x is None else "%+.1f%%" % (x * 100)


def _fmt_pct(x) -> str:
    return "n/a" if x is None else "%.0f%%" % (x * 100)


def _fmt_px(x) -> str:
    return "n/a" if x is None else "%.2f" % x


def _fmt_usd(x) -> str:
    return "n/a" if x is None else "%+.2f" % x


def _build_user_content(rep: PMAnalysisReport) -> str:
    """Serialize the deterministic report as a stable plaintext block. Every number the narrator may cite is
    PRE-FORMATTED here (percentages already computed) so the model never has to do arithmetic."""
    if rep.chalk:
        px_tag = "CHALK (favorite-farming)"
    elif rep.contested:
        px_tag = "CONTESTED (contrarian)"
    else:
        px_tag = "neither chalk nor contested"
    thin = " [THIN: below the %d-position threshold]" % rep.min_resolved if rep.is_thin else ""
    lines = [
        "Whale: %s (%s...)" % (rep.user_name or "<no display name>", rep.wallet[:10]),
        "Category: %s" % rep.category,
        "",
        "Resolved-position record (settled markets in this category only):",
        "  n_resolved (scoreable) = %d%s" % (rep.n_resolved, thin),
        "  n_excluded (quarantined, §3A) = %d   of %d total positions" % (rep.n_excluded, rep.n_total_rows),
        "  wins = %d   losses = %d   win_rate = %s" % (rep.wins, rep.losses, _fmt_pct(rep.win_rate)),
        "  net_realized_pnl = %s USDC" % _fmt_usd(rep.net_realized_pnl),
        "  cost_basis = %.2f USDC (the ROI denominator)" % rep.cost_basis,
        "  roi_cost_based = %s  <- THE metric" % _fmt_pct_signed(rep.roi),
        "  roi_notional = %s  (legacy comparison only, NOT the metric)" % _fmt_pct_signed(rep.roi_notional),
        "  avg_win_price = %s  [%s]" % (_fmt_px(rep.avg_win_price), px_tag),
        "",
        "Structure + data quality:",
        "  two_sided_pct = %s over %s condition_ids  (hedge / market-making tell)"
        % (_fmt_pct(rep.two_sided_pct), rep.n_condition_ids if rep.n_condition_ids is not None else "n/a"),
        "  one_sided_roi = %s (n=%s)  [UPPER BOUND -- excludes hedged markets]"
        % (_fmt_pct_signed(rep.onesided_roi), rep.onesided_n if rep.onesided_n is not None else "n/a"),
        "  data_quality = %s  (quarantined: %s of positions, %s of |PnL|)"
        % (rep.data_quality or "clean", _fmt_pct(rep.dq_count_pct), _fmt_pct(rep.dq_dollar_pct)),
        "  backfill_complete = %s" % ("yes" if rep.backfill_complete else "NO (partial history -- not ranked)"),
    ]
    # Stage 5 (R2c + prompt rung): the re-grounded loss set, PRE-FORMATTED so the narrator cites it verbatim (the
    # no-arithmetic rule). Present ONLY when the loss set was re-grounded from /activity -- when absent, the block is
    # omitted entirely and the system prompt tells the model to say nothing about omission (no speculation).
    if rep.loss_grounded:
        lines += [
            "",
            "Loss completeness (re-grounded from /activity, held-to-resolution -- corrects the /closed-positions "
            "under-reporting of held-to-worthless losses, the F-1 bias):",
            "  honest win/loss = %sW / %sL   (vs the %dW / %dL above, which is /closed-positions only)"
            % (rep.honest_wins if rep.honest_wins is not None else "n/a",
               rep.honest_losses if rep.honest_losses is not None else "n/a", rep.wins, rep.losses),
            "  held-to-worthless losses recovered (a_only) = %s"
            % (rep.a_only_losses if rep.a_only_losses is not None else "n/a"),
            "  loss omission = %s of honest losses were dropped by /closed-positions  (the measured bias for THIS whale)"
            % _fmt_pct(rep.loss_omission_pct),
            "  completeness = %s" % (rep.loss_completeness or "n/a"),
        ]
    if rep.samples:
        lines.append("")
        lines.append("Largest resolved positions by |PnL| (illustrative):")
        for s in rep.samples:
            outcome = "won" if s.won == 1 else ("lost" if s.won == 0 else "n/a")
            sus = " [quarantined]" if s.pnl_suspect else ""
            lines.append("  - %s | %s | entry %s | pnl %s%s"
                         % ((s.title or "<untitled>")[:50], outcome, _fmt_px(s.avg_price),
                            _fmt_usd(s.realized_pnl), sus))
    lines.append("")
    lines.append("Write 2-4 sentences.")
    return "\n".join(lines)


def narrate(rep: PMAnalysisReport, *, narrator_enabled: bool = True, chat: object | None = None,
            cap_hit: bool = False) -> NarrationResult:
    """The 4 legacy LLM gates + the data-refusal, in priority order. DB-free (cap_hit is passed in; the cost
    ledger is the orchestrator's job) so it is trivially testable with a fake `chat`.

    Gate order matters for the KEY-DEFERRED period: an 'empty' slice refuses on DATA (no_resolved_positions)
    before the LLM gates, because narrating zero numbers is meaningless whether or not the key is wired --
    so the zero-rows pair shows the honest data-refusal, not a misleading 'the LLM would have narrated'."""
    if not narrator_enabled:
        return NarrationResult(None, NULL_DISABLED, 0.0, 0, 0, None)
    if rep.data_state == "empty":                        # data refusal -- nothing to narrate
        return NarrationResult(None, NULL_NO_DATA, 0.0, 0, 0, None)
    if cap_hit:
        return NarrationResult(None, NULL_CAP, 0.0, 0, 0, None)
    if chat is None:
        if not is_llm_available():                       # key not wired -> this fires in production today
            return NarrationResult(None, NULL_UNAVAILABLE, 0.0, 0, 0, None)
        try:
            chat = _build_chat_model()
        except Exception as e:                            # langchain import / construction failure
            log.warning("pm analyze: build chat model failed: %s", e)
            return NarrationResult(None, NULL_UNAVAILABLE, 0.0, 0, 0, None)
    try:
        # LangChain (role, content) tuple shorthand -- ChatAnthropic accepts it, so we need NO
        # langchain_core.messages import here (keeps the fork's narrate decoupled; the injected-chat tests
        # never import langchain).
        resp = chat.invoke([("system", _SYSTEM_PROMPT),
                            ("human", _build_user_content(rep))])  # type: ignore[attr-defined]
        text = str(getattr(resp, "content", "") or "").strip()
        usage = _extract_usage(resp)
        ti = int(usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0) or 0)
        to = int(usage.get("output_tokens", 0) or usage.get("completion_tokens", 0) or 0)
        cost = _cost_for_usage(usage)
        if not text:                                      # empty content -> treat as error (no blank verdict)
            return NarrationResult(None, NULL_ERROR, cost, ti, to, PM_ANALYZE_MODEL)
        return NarrationResult(text, None, cost, ti, to, PM_ANALYZE_MODEL)
    except Exception as e:
        log.warning("pm analyze narration failed: %s", e)
        return NarrationResult(None, NULL_ERROR, 0.0, 0, 0, None)


# ── cost ledger (ONE visible per-UTC-day counter in the PM DB; NEVER agent_state) ─────────────────────
def _utc_day(now_ts: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(int(now_ts)))


def daily_cost(conn, day: str) -> tuple[float, int]:
    """(usd, n_calls) accumulated for a UTC day; (0.0, 0) if the day has no row yet. Read-only."""
    row = conn.execute("SELECT usd, n_calls FROM pm_analysis_cost WHERE day_utc=?", (day,)).fetchone()
    return (float(row["usd"] or 0.0), int(row["n_calls"] or 0)) if row else (0.0, 0)


def _cap_hit(conn, day: str, cap_usd: float) -> bool:
    return daily_cost(conn, day)[0] >= cap_usd


def _book_cost(conn, day: str, delta_usd: float, now_ts: int) -> None:
    conn.execute(
        "INSERT INTO pm_analysis_cost(day_utc, usd, n_calls, updated_ts) VALUES (?,?,1,?) "
        "ON CONFLICT(day_utc) DO UPDATE SET usd = usd + excluded.usd, n_calls = n_calls + 1, "
        "updated_ts = excluded.updated_ts",
        (day, float(delta_usd), int(now_ts)))


# ── cache (only successful verdicts are stored) ───────────────────────────────────────────────────────
def _cache_get(conn, wallet: str, category: str, skill_version: str) -> str | None:
    row = conn.execute(
        "SELECT report_json FROM pm_analysis_cache WHERE wallet=? AND category=? AND skill_version=?",
        (wallet, category, skill_version)).fetchone()
    return row["report_json"] if row else None


def _cache_put(conn, rep: PMAnalysisReport) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO pm_analysis_cache(wallet, category, skill_version, verdict, null_reason, "
        "report_json, model, cost_usd, tokens_in, tokens_out, n_resolved, created_ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (rep.wallet, rep.category, rep.skill_version, rep.verdict, rep.null_reason, report_to_json(rep),
         rep.model, rep.cost_usd, rep.tokens_in, rep.tokens_out, rep.n_resolved, rep.generated_ts))


def _cache_evict(conn, wallet: str, category: str, skill_version: str) -> None:
    conn.execute("DELETE FROM pm_analysis_cache WHERE wallet=? AND category=? AND skill_version=?",
                 (wallet, category, skill_version))


def is_cached(conn, wallet: str, category: str, skill_version: str = PM_ANALYZE_SKILL_VERSION) -> bool:
    """True iff a stored verdict exists for this (wallet, category, skill_version). Read-only; the analyze route
    peeks this to decide whether to pay for the /activity loss-grounding fetch -- a cache HIT skips it entirely."""
    return _cache_get(conn, (wallet or "").lower(), category, skill_version) is not None


# ── orchestration ─────────────────────────────────────────────────────────────────────────────────────
def analyze_whale(conn, wallet: str, category: str, *, now_ts: int, force: bool = False,
                  narrator_enabled: bool = True, chat: object | None = None,
                  skill_version: str = PM_ANALYZE_SKILL_VERSION,
                  min_resolved: int | None = None,
                  daily_cap_usd: float = PM_ANALYZE_DAILY_CAP_USD,
                  loss_grounding=None) -> PMAnalysisReport:
    """The button/CLI entrypoint. Cache-hit -> return stored, spend NOTHING. Miss/force -> build the
    deterministic report, narrate under the cap, book any spend, and cache ONLY a successful verdict.
    Writes pm_analysis_cache + pm_analysis_cost (both PM DB); NEVER agent_state, NEVER the legacy DB."""
    wallet = (wallet or "").lower()

    if not force:
        cached = _cache_get(conn, wallet, category, skill_version)
        if cached is not None:
            return replace(report_from_json(cached), served_from_cache=True)   # HIT: no narrate, no spend
    else:
        _cache_evict(conn, wallet, category, skill_version)                     # re-analyze: clear stale verdict

    rep = build_pm_analysis(conn, wallet, category, now_ts=now_ts, min_resolved=min_resolved,
                            skill_version=skill_version, loss_grounding=loss_grounding)

    day = _utc_day(now_ts)
    cap_hit = _cap_hit(conn, day, daily_cap_usd)
    nr = narrate(rep, narrator_enabled=narrator_enabled, chat=chat, cap_hit=cap_hit)
    if nr.cost_usd and nr.cost_usd > 0:                                         # an API call that cost money
        _book_cost(conn, day, nr.cost_usd, now_ts)

    rep = replace(rep, verdict=nr.narration, null_reason=nr.null_reason, model=nr.model,
                  cost_usd=nr.cost_usd, tokens_in=nr.tokens_in, tokens_out=nr.tokens_out,
                  served_from_cache=False)
    if nr.narration is not None:                                               # cache SUCCESS only
        _cache_put(conn, rep)
    if hasattr(conn, "commit"):
        conn.commit()
    return rep


__all__ = [
    "PMAnalysisReport", "SampleRow", "NarrationResult",
    "PM_ANALYZE_MODEL", "PM_ANALYZE_DAILY_CAP_USD", "PM_ANALYZE_SKILL_VERSION",
    "NULL_DISABLED", "NULL_NO_DATA", "NULL_CAP", "NULL_UNAVAILABLE", "NULL_ERROR", "NULL_REASON_LABELS",
    "build_pm_analysis", "analysis_flags", "narrate", "analyze_whale",
    "is_llm_available", "is_cached", "daily_cost", "report_to_json", "report_from_json",
]
