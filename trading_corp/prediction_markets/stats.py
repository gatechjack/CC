"""Rollup + ranking for Prediction Markets (P1).

pm_category_stats rollup + the two ranking routines (net_roi, recency_weighted) +
query_scoreboard. ALL of them read scoreable rows through the ONE canonical §3A predicate
(db.SCOREABLE_PREDICATE_SQL / scoreable_where) -- never re-derived here. Ranking reuses the
kalshi_whale_stats primitives (plan-designated) via a thin adapter; no engine import.

Spec: reports/prediction_markets/P1_PLAN.md §3A, §6, §7, §8.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict

from .db import SCOREABLE_PREDICATE_SQL, scoreable_where
from .category import classify_market_shape, NON_SINGLE_GAME_CATEGORIES
from trading_corp.data.kalshi_whale_stats import (
    _edge_factor,
    time_weighted_outcomes,
    wilson_lcb_95,
    wilson_lcb_95_weighted,
)

CHALK_HI = 0.85            # avg_win_price >= -> chalk (bets favorites)
CONTESTED_LO = 0.70       # avg_win_price <  -> contested-calls
DATA_QUALITY_THRESHOLD = 0.10   # §3A: > this fraction quarantined -> 'contaminated' (starting value)
DEFAULT_MIN_RESOLVED = 10
DEFAULT_HALF_LIFE_DAYS = 30.0

_STATS_COLS = [
    "wallet", "category", "n_resolved", "wins", "losses", "win_rate", "net_realized_pnl",
    "total_bought", "cost_basis", "roi", "roi_notional", "avg_bet", "avg_win_price", "last_resolved_ts",
    "n_excluded", "excluded_pnl", "n_anomaly", "dq_count_pct", "dq_dollar_pct", "data_quality",
    # migration 004 caveat analytics -- MUST stay in lock-step with the rollup SELECT (e5: INSERT OR
    # REPLACE resets any table column not listed here to its DEFAULT every run -> silent zeros forever).
    "n_condition_ids", "n_two_sided", "two_sided_pct", "n_single_game", "n_futures_like",
    "single_game_pct", "market_type_source", "updated_ts",
]

# migration-004 one-sided directional-slice companion (P2_PLAN §5.1); written by _rollup_onesided().
_ONESIDED_COLS = [
    "wallet", "category", "n_resolved", "wins", "losses", "win_rate", "net_realized_pnl",
    "total_bought", "cost_basis", "roi", "avg_bet", "avg_win_price", "last_resolved_ts",
    "is_upper_bound", "updated_ts",
]


def rollup(conn, *, now_ts: int, dq_threshold: float = DATA_QUALITY_THRESHOLD) -> int:
    """Aggregate pm_closed_position -> pm_category_stats over EVERY (wallet, category) that has
    any rows. Scoreable metrics use the §3A predicate; n_excluded/excluded_pnl summarize the
    quarantined remainder so the visibility columns are honest even for fully-quarantined
    categories (n_resolved=0, n_excluded>0)."""
    pred = SCOREABLE_PREDICATE_SQL   # the ONE definition
    # --- migration 004: two-sided structure over ALL rows (hedge/MM tell). n_condition_ids = distinct
    #     condition_ids; n_two_sided = those the whale held on >1 outcome_index. NOT scoreable-filtered
    #     (two-sidedness is structural regardless of quarantine). ---
    two_sided: dict[tuple, tuple] = {}
    for tr in conn.execute(
        "SELECT wallet, category, COUNT(*) AS n_cond, "
        " SUM(CASE WHEN n_out > 1 THEN 1 ELSE 0 END) AS n_two "
        "FROM (SELECT wallet, category, condition_id, COUNT(DISTINCT outcome_index) AS n_out "
        "      FROM pm_closed_position GROUP BY wallet, category, condition_id) "
        "GROUP BY wallet, category"
    ).fetchall():
        two_sided[(tr["wallet"], tr["category"])] = (tr["n_cond"] or 0, tr["n_two"] or 0)
    # --- migration 004: market-shape counts over ALL rows via the pure classifier (bias-down: ambiguous
    #     is NOT single-game). Per-row pass -- cheap regex, weekly rollup not query-time. ---
    shape: dict[tuple, list] = defaultdict(lambda: [0, 0])   # (wallet,category) -> [n_single_game, n_futures]
    for sr in conn.execute(
        "SELECT wallet, category, slug, event_slug, title FROM pm_closed_position"
    ).fetchall():
        cls = classify_market_shape(sr["slug"], sr["event_slug"], sr["title"])
        if cls == "single_game":
            shape[(sr["wallet"], sr["category"])][0] += 1
        elif cls == "futures":
            shape[(sr["wallet"], sr["category"])][1] += 1
    sql = (
        "SELECT wallet, category, "
        f" SUM(CASE WHEN {pred} THEN 1 ELSE 0 END) AS n_resolved, "
        f" SUM(CASE WHEN {pred} AND won=1 THEN 1 ELSE 0 END) AS wins, "
        f" SUM(CASE WHEN {pred} AND won=0 THEN 1 ELSE 0 END) AS losses, "
        f" SUM(CASE WHEN {pred} THEN realized_pnl ELSE 0 END) AS net, "
        f" SUM(CASE WHEN {pred} THEN total_bought ELSE 0 END) AS tb, "
        f" SUM(CASE WHEN {pred} THEN cost_basis ELSE 0 END) AS cost_basis, "
        f" AVG(CASE WHEN {pred} AND won=1 THEN avg_price END) AS avg_win_price, "
        f" MAX(CASE WHEN {pred} THEN resolved_ts END) AS last_ts, "
        " SUM(CASE WHEN pnl_suspect=1 THEN 1 ELSE 0 END) AS n_excluded, "
        " SUM(CASE WHEN pnl_suspect=1 THEN realized_pnl ELSE 0 END) AS excluded_pnl, "
        " SUM(CASE WHEN pnl_anomaly=1 THEN 1 ELSE 0 END) AS n_anomaly, "
        " SUM(ABS(realized_pnl)) AS abs_all, "
        " SUM(CASE WHEN pnl_suspect=1 THEN ABS(realized_pnl) ELSE 0 END) AS abs_excl "
        "FROM pm_closed_position GROUP BY wallet, category"
    )
    recs = []
    for r in conn.execute(sql).fetchall():
        n = r["n_resolved"] or 0
        wins = r["wins"] or 0
        losses = r["losses"] or 0
        tb = r["tb"] or 0.0
        cb = r["cost_basis"] or 0.0
        net = r["net"] or 0.0
        n_excl = r["n_excluded"] or 0
        n_anom = r["n_anomaly"] or 0
        decided = wins + losses
        total = n + n_excl
        win_rate = (wins / decided) if decided > 0 else None
        # §13 dec 11: RANKED roi is COST-based (net / SUM(cost_basis)); guard cb<=0 -> None (never /0).
        # A scoreable row can only reach here with cost_basis<=0 if total_bought>0 AND avg_price<=0/NULL
        # (a degenerate cost) -> the whole-sum guard keeps roi well-defined; roi_notional retained for
        # comparison to pre-fix / scout numbers (NOT ranked).
        roi = (net / cb) if cb > 0 else None
        roi_notional = (net / tb) if tb > 0 else None
        avg_bet = (cb / n) if n > 0 else None
        # data_quality is flagged on EITHER a count OR a $-weighted fraction of quarantined rows -- count
        # alone hides a few rows carrying large $ (Kickstand7 Fed: 3.6% count but 9% of $). Report both.
        abs_all = r["abs_all"] or 0.0
        dq_count = (n_excl / total) if total > 0 else 0.0
        dq_dollar = ((r["abs_excl"] or 0.0) / abs_all) if abs_all > 0 else 0.0
        dq = "contaminated" if (dq_count > dq_threshold or dq_dollar > dq_threshold) else None
        # migration 004 caveat columns (merged from the two ALL-rows passes above)
        wc = (r["wallet"], r["category"])
        n_cond, n_two = two_sided.get(wc, (0, 0))
        two_sided_pct = (n_two / n_cond) if n_cond > 0 else 0.0
        n_sg, n_fut = shape.get(wc, (0, 0))
        # single_game_pct: NULL for non-sports categories (OQ-2 -- Fed has no single-game notion), else
        # n_single_game / ALL rows (bias-down: ambiguous already excluded from n_single_game).
        if r["category"] in NON_SINGLE_GAME_CATEGORIES:
            single_game_pct = None
        else:
            single_game_pct = (n_sg / total) if total > 0 else None
        recs.append((r["wallet"], r["category"], n, wins, losses, win_rate, net, tb, cb, roi, roi_notional,
                     avg_bet, r["avg_win_price"], r["last_ts"], n_excl, r["excluded_pnl"] or 0.0,
                     n_anom, dq_count, dq_dollar, dq,
                     n_cond, n_two, two_sided_pct, n_sg, n_fut, single_game_pct, "slug_heuristic", now_ts))
    ph = ", ".join(["?"] * len(_STATS_COLS))
    conn.executemany(
        "INSERT OR REPLACE INTO pm_category_stats (%s) VALUES (%s)" % (", ".join(_STATS_COLS), ph),
        recs,
    )
    _rollup_onesided(conn, now_ts=now_ts)   # migration-004 companion: one-sided directional slice
    if hasattr(conn, "commit"):
        conn.commit()
    return len(recs)


def _rollup_onesided(conn, *, now_ts: int) -> int:
    """migration-004 companion: aggregate SCOREABLE rows on condition_ids the whale held on a SINGLE
    outcome_index (NOT two-sided) -> pm_category_onesided_stats. The copyable directional signal, but an
    UPPER BOUND (is_upper_bound=1): a position turns two-sided precisely when the first side sours, so
    excluding hedged markets is optimistic (survivorship-caveated, §13A(f)). One-sidedness is structural
    (COUNT(DISTINCT outcome_index)=1 over ALL rows); the aggregate is scoreable (the ONE §3A predicate)."""
    sql = (
        "SELECT p.wallet, p.category, "
        " SUM(1) AS n, "
        " SUM(CASE WHEN p.won=1 THEN 1 ELSE 0 END) AS wins, "
        " SUM(CASE WHEN p.won=0 THEN 1 ELSE 0 END) AS losses, "
        " SUM(p.realized_pnl) AS net, "
        " SUM(p.total_bought) AS tb, "
        " SUM(p.cost_basis) AS cb, "
        " AVG(CASE WHEN p.won=1 THEN p.avg_price END) AS avg_win_price, "
        " MAX(p.resolved_ts) AS last_ts "
        "FROM pm_closed_position p "
        "JOIN (SELECT wallet, category, condition_id FROM pm_closed_position "
        "      GROUP BY wallet, category, condition_id HAVING COUNT(DISTINCT outcome_index) = 1) oc "
        "  ON p.wallet=oc.wallet AND p.category=oc.category AND p.condition_id=oc.condition_id "
        "WHERE " + scoreable_where("p") + " "
        "GROUP BY p.wallet, p.category"
    )
    recs = []
    for r in conn.execute(sql).fetchall():
        n = r["n"] or 0
        wins = r["wins"] or 0
        losses = r["losses"] or 0
        cb = r["cb"] or 0.0
        net = r["net"] or 0.0
        decided = wins + losses
        win_rate = (wins / decided) if decided > 0 else None
        roi = (net / cb) if cb > 0 else None
        avg_bet = (cb / n) if n > 0 else None
        recs.append((r["wallet"], r["category"], n, wins, losses, win_rate, net, r["tb"] or 0.0, cb, roi,
                     avg_bet, r["avg_win_price"], r["last_ts"], 1, now_ts))
    ph = ", ".join(["?"] * len(_ONESIDED_COLS))
    conn.executemany(
        "INSERT OR REPLACE INTO pm_category_onesided_stats (%s) VALUES (%s)" % (", ".join(_ONESIDED_COLS), ph),
        recs,
    )
    return len(recs)


# ---- ranking adapter (thin wrapper over kalshi_whale_stats primitives) ----

def score_net_roi(wins: int, n_decided: int, roi: float | None) -> tuple[float, float, float]:
    """Routine B: wilson_lcb_95(wins, n) x _edge_factor(roi). Returns (score, wilson, edge)."""
    w = wilson_lcb_95(wins, n_decided)
    e = _edge_factor(roi or 0.0)
    return w * e, w, e


def score_recency_weighted(samples, *, now_ts: float, roi: float | None,
                           half_life_days: float = DEFAULT_HALF_LIFE_DAYS):
    """Routine A: time_weighted_outcomes(won, ts) -> wilson_lcb_95_weighted x _edge_factor(roi).
    Returns (score, wilson, edge, weighted_rate, n_eff)."""
    wr, n_eff = time_weighted_outcomes(samples, now_ts=now_ts, half_life_days=half_life_days)
    w = wilson_lcb_95_weighted(wr, n_eff)
    e = _edge_factor(roi or 0.0)
    return w * e, w, e, wr, n_eff


def compute_scores(conn, *, now_ts: int, min_resolved: int = DEFAULT_MIN_RESOLVED,
                   half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
                   recency_basis: str = "resolved_ts") -> int:
    """Compute both routines into pm_score_snapshot, over §3A scoreable rows only, for every
    (wallet, category) with n_resolved >= min_resolved. params_json records the exclusion +
    recency basis so each score is auditable."""
    # recency samples: (won, resolved_ts) over SCOREABLE rows only (the ONE predicate)
    samples: dict[tuple, list] = defaultdict(list)
    for r in conn.execute(
        "SELECT wallet, category, won, resolved_ts FROM pm_closed_position WHERE " + scoreable_where()
    ).fetchall():
        samples[(r["wallet"], r["category"])].append((bool(r["won"]), float(r["resolved_ts"] or 0)))

    # §13A(k): only COMPLETE-backfill wallets may be ranked. A PARTIAL/FAILED wallet (429-truncated or
    # cap-hit) has half a whale's history -> looks like a different whale -> gets NO score snapshot until
    # re-run to completion. pm_whale.backfill_complete is the gate.
    complete = {r[0] for r in conn.execute(
        "SELECT wallet FROM pm_whale WHERE backfill_complete = 1").fetchall()}
    snaps = []
    for s in conn.execute(
        "SELECT wallet, category, wins, losses, roi, n_resolved, n_excluded FROM pm_category_stats"
    ).fetchall():
        if s["wallet"] not in complete:      # PARTIAL/FAILED wallet -> not ranked
            continue
        if (s["n_resolved"] or 0) < min_resolved:
            continue
        key = (s["wallet"], s["category"])
        n_decided = (s["wins"] or 0) + (s["losses"] or 0)
        roi = s["roi"]
        n_excl = s["n_excluded"] or 0
        nr, w1, e1 = score_net_roi(s["wins"] or 0, n_decided, roi)
        snaps.append((s["wallet"], s["category"], "net_roi", nr, w1, e1,
                      json.dumps({"excludes_suspect": True, "n_excluded": n_excl,
                                  "min_resolved": min_resolved}), now_ts))
        rw, w2, e2, wr, n_eff = score_recency_weighted(
            samples.get(key, []), now_ts=now_ts, roi=roi, half_life_days=half_life_days)
        snaps.append((s["wallet"], s["category"], "recency_weighted", rw, w2, e2,
                      json.dumps({"excludes_suspect": True, "n_excluded": n_excl,
                                  "recency_basis": recency_basis, "half_life_days": half_life_days,
                                  "n_eff": round(n_eff, 3)}), now_ts))
    conn.executemany(
        "INSERT OR REPLACE INTO pm_score_snapshot "
        "(wallet, category, routine, score, wilson_lcb, edge_factor, params_json, computed_ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", snaps,
    )
    if hasattr(conn, "commit"):
        conn.commit()
    return len(snaps)


def query_scoreboard(conn, *, category: str | None = None, routine: str = "net_roi",
                     min_resolved: int = DEFAULT_MIN_RESOLVED) -> list[dict]:
    """Ranked read for the P2 farm UI / `report`. Joins pm_category_stats + the routine's
    pm_score_snapshot; filters n_resolved >= min_resolved; sorts by score desc. Adds chalk /
    contested / data_quality flags."""
    q = (
        "SELECT cs.*, ss.score AS score, ss.wilson_lcb AS wilson_lcb, "
        "ss.edge_factor AS edge_factor, ss.params_json AS params_json, "
        "COALESCE(w.backfill_complete, 0) AS backfill_complete, "
        # CP2 Phase-3 display name: a recognition label beside the wallet. NULL when a whale has no
        # user_name -- ingest NEVER populates it (/closed-positions carries no name); names.sync_user_names
        # copies roster labels in. On NULL the page renders the WALLET, never a blank/fabricated name.
        # Wallet stays the identity; the name is display-only (never keyed on).
        "w.user_name AS user_name, "
        # migration-004 one-sided directional slice (P2 CP2 render contract, deferred from CP1). LEFT JOIN
        # so a whale with NO one-sided slice still ranks -- the fields come back NULL and the page renders
        # honest-empty ("--"), never a fabricated 0. is_upper_bound is ALWAYS 1: the figure excludes hedged
        # markets so it is optimistic by construction (§13A(f)); it MUST render LABELED AS AN UPPER BOUND.
        "os.roi AS onesided_roi, os.n_resolved AS onesided_n, os.win_rate AS onesided_win_rate, "
        "os.avg_win_price AS onesided_avg_win_price, os.is_upper_bound AS onesided_is_upper_bound "
        "FROM pm_category_stats cs "
        "LEFT JOIN pm_score_snapshot ss "
        "  ON cs.wallet=ss.wallet AND cs.category=ss.category AND ss.routine=? "
        "LEFT JOIN pm_whale w ON cs.wallet = w.wallet "
        "LEFT JOIN pm_category_onesided_stats os "
        "  ON cs.wallet=os.wallet AND cs.category=os.category "
        # Stage-0 funnel gate (migration 008): a DEACTIVATED (wallet,category) pair (active=0 in pm_watchlist)
        # must show NOWHERE, including this ranker -- per F-4 query_scoreboard becomes the prospects section of
        # the category page (RULED 2026-08-26). LEFT JOIN so a pair ABSENT from pm_watchlist (a pure prospect)
        # still ranks (wl.active IS NULL); ONLY active=0 is excluded. Selects NO wl columns -> shape unchanged.
        "LEFT JOIN pm_watchlist wl ON cs.wallet=wl.wallet AND cs.category=wl.category "
        # COMPLETENESS gate (2026-08-29, Jack ruled): a PARTIAL/FAILED-backfill whale (backfill_complete != 1)
        # is ranked on TRUNCATED history and MUST be excluded. compute_scores already benches it (no
        # pm_score_snapshot) and scoreboard_flags has long CLAIMED "excluded from ranking" -- but this WHERE did
        # NOT enforce it (a FALSE statement in deployed code, read live by `pm_cli report`). Enforce it HERE, in
        # the ranker, so no caller can forget it -- mirroring the active gate. TWO GATES, INDEPENDENT + COMPOSED:
        #   active  (pm_watchlist LEFT JOIN): NULL => PERMISSIVE (a pure prospect not on the watchlist RANKS);
        #   complete(pm_whale     LEFT JOIN): NULL => RESTRICTIVE via COALESCE(...,0)=1 -- a stats row with NO
        #     pm_whale row (rollup keys off pm_closed_position) has UNKNOWN completeness and must NOT rank.
        # The asymmetry is intentional; both are AND-ed on DISTINCT joined tables, so neither gate defeats the
        # other and no NULL bypasses the OTHER gate. complete-but-inactive AND active-but-partial are BOTH
        # excluded (test_removal_gate::test_scoreboard_completeness_and_active_gates_compose).
        "WHERE cs.n_resolved >= ? AND (wl.active IS NULL OR wl.active = 1) "
        "AND COALESCE(w.backfill_complete, 0) = 1"
    )
    params: list = [routine, min_resolved]
    if category:
        q += " AND cs.category = ?"
        params.append(category)
    q += " ORDER BY (ss.score IS NULL), ss.score DESC, cs.roi DESC"
    out = []
    for r in conn.execute(q, params).fetchall():
        d = dict(r)
        awp = d.get("avg_win_price")
        d["chalk"] = (awp is not None and awp >= CHALK_HI)
        d["contested"] = (awp is not None and awp < CONTESTED_LO)
        out.append(d)
    return out


# ── scoreboard read + freshness helpers (P2 CP2 web layer; also usable by the CLI) ──────────────

def distinct_categories(conn) -> list[str]:
    """Categories present in pm_category_stats, for the scoreboard filter (sorted, non-NULL)."""
    return [r["category"] for r in conn.execute(
        "SELECT DISTINCT category FROM pm_category_stats WHERE category IS NOT NULL ORDER BY category"
    ).fetchall()]


def max_refresh_ts(conn) -> int | None:
    """MAX(pm_whale.last_refresh_ts) -- the data-freshness source for refresh_band(). None if never set
    (=> refresh_band renders RED/unknown, never hidden)."""
    row = conn.execute("SELECT MAX(last_refresh_ts) AS ts FROM pm_whale").fetchone()
    return int(row["ts"]) if row is not None and row["ts"] is not None else None


REFRESH_GREEN_MAX_DAYS = 8.0    # <= 8d green: within the weekly cycle + a day of grace
REFRESH_AMBER_MAX_DAYS = 15.0   # 8-15d amber: missed one weekly cycle; > 15d red: missed two+, or never refreshed


def refresh_band_state(ts: int | None, now_ts: int) -> dict:
    """Freshness band for the scoreboard stamp, calibrated to the WEEKLY refresh cadence. green <= 8d,
    amber 8-15d, red > 15d OR ts missing. Pure (now_ts is passed, never read here) so it is deterministic
    under test. A missing/zero ts is RED and labeled 'no refresh timestamp' -- honest-stale, NEVER hidden."""
    if ts is None or ts <= 0:
        return {"ts": None, "age_days": None, "band": "red", "iso": None, "note": "no refresh timestamp"}
    age_days = max(0.0, (now_ts - ts) / 86400.0)
    if age_days <= REFRESH_GREEN_MAX_DAYS:
        band = "green"
    elif age_days <= REFRESH_AMBER_MAX_DAYS:
        band = "amber"
    else:
        band = "red"
    return {"ts": int(ts), "age_days": age_days, "band": band,
            "iso": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts)), "note": None}


def _fmt(v, spec, default="-"):
    return format(v, spec) if isinstance(v, (int, float)) else default


def scoreboard_flags(row: dict) -> list[str]:
    """The ONE flag deriver -- consumed by BOTH format_report (CLI) and the web scoreboard, so the
    product page and the CLI can NEVER diverge on which flags a row carries (P2 Phase-2 parity bar #1).
    Tokens: INCOMPLETE-NOT-RANKED (§13A(k) partial/failed backfill), CHALK, CONTESTED,
    CONTAMINATED(cnt%/$%) (§3A data_quality), ANOM:n (§3A clause-a count)."""
    flags: list[str] = []
    if not row.get("backfill_complete"):
        # §13A(k): PARTIAL/FAILED backfill IS excluded from ranking -- now ENFORCED in query_scoreboard's WHERE
        # (2026-08-29). This flag is a DEFENSIVE BACKSTOP: a gated read never surfaces an incomplete row here,
        # but if a caller ever passes un-gated rows to scoreboard_flags, it is still marked (never silently ranked).
        flags.append("INCOMPLETE-NOT-RANKED")
    if row.get("chalk"):
        flags.append("CHALK")
    if row.get("contested"):
        flags.append("CONTESTED")
    if row.get("data_quality"):
        flags.append("%s(cnt%s/$%s)" % (
            str(row["data_quality"]).upper(),
            _fmt((row.get("dq_count_pct") or 0) * 100, ".0f") + "%",
            _fmt((row.get("dq_dollar_pct") or 0) * 100, ".0f") + "%"))
    if row.get("n_anomaly"):
        flags.append("ANOM:%d" % row["n_anomaly"])
    return flags


def format_report(board: list[dict], *, fmt: str = "table") -> str:
    """Render a query_scoreboard result as a text table or JSON (for `pm_cli report`)."""
    if fmt == "json":
        return json.dumps(board, default=str, indent=2)
    hdr = "%-14s %-8s %5s %6s %8s %8s %11s %8s  flags" % (
        "wallet", "cat", "n", "win%", "roiC%", "roiN%", "net_pnl", "score")
    lines = ["# roiC = COST-based ROI (net/cost_basis) = the RANKED metric; roiN = notional ROI "
             "(net/total_bought), NOT ranked, for legacy/scout comparison only (§13 dec 11)",
             hdr, "-" * len(hdr)]
    for r in board:
        flags = scoreboard_flags(r)   # THE shared deriver -- identical tokens on the web scoreboard (parity)
        wr = r.get("win_rate")
        roi = r.get("roi")                    # cost-based (RANKED)
        roin = r.get("roi_notional")          # notional (NOT ranked; legacy comparison)
        lines.append("%-14s %-8s %5s %6s %8s %8s %11s %8s  %s" % (
            str(r.get("wallet", ""))[:14], str(r.get("category", ""))[:8], r.get("n_resolved", "-"),
            _fmt(wr * 100 if isinstance(wr, (int, float)) else None, ".0f"),
            _fmt(roi * 100 if isinstance(roi, (int, float)) else None, "+.1f"),
            _fmt(roin * 100 if isinstance(roin, (int, float)) else None, "+.1f"),
            _fmt(r.get("net_realized_pnl"), "+.0f"),
            _fmt(r.get("score"), ".3f"),
            " ".join(flags)))
    return "\n".join(lines)
