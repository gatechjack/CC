"""Read-only diagnostic replay of the bitunix PA validation funnel.

Builds replay-based fire-rate estimates for the score <-> PA internal-
consistency options identified in the 2026-05-27 bitunix funnel
diagnostic. See reports/2026-05-27_bitunix_funnel_diagnostic.md
(forthcoming) for the source analysis.

This script does NOT touch live config, does NOT restart anything, and
does NOT write to the database (opens sqlite in read-only mode).

Tripwire constraint (memory bitunix-paper-clock): tightness options 3
(htf_regime.proximity_block_pct) and 4 (trade_plan.tp1_min_profit_
multiplier) are explicitly OUT OF SCOPE for this replay. They are
deferred to the 2026-06-19 midpoint tripwire per Board decision.
Replay covers ONLY score <-> PA internal-consistency options:

  Option 1: pa_validation.require_all true -> false (>=2 of 3 must pass)
  Option 5: scoring.tier_thresholds.standard 5 -> 7
  Option 6: scoring.min_score_to_fire 5 -> 7

Usage:
    python3 replay_pa_validation_alt.py --db /path/to/trading_corp.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path


ANCHOR_TS = "2026-05-23T15:52:00Z"

PA_VALIDATORS = ("vwap_alignment", "volume_confirmation", "structure_alignment")


def fetch_all_score_rows(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute(
        """
        SELECT id, ts, payload_json
        FROM audit_event
        WHERE ts >= ? AND kind = 'bitunix_score_decided'
        ORDER BY id
        """,
        (ANCHOR_TS,),
    )
    out: list[dict] = []
    for row_id, ts, payload in cur:
        try:
            p = json.loads(payload)
        except json.JSONDecodeError:
            continue
        p["_id"] = row_id
        p["_ts"] = ts
        out.append(p)
    return out


def fetch_pa_rows(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute(
        """
        SELECT id, ts, payload_json
        FROM audit_event
        WHERE ts >= ? AND kind = 'pa_validation_decision'
        ORDER BY id
        """,
        (ANCHOR_TS,),
    )
    out: list[dict] = []
    for row_id, ts, payload in cur:
        try:
            p = json.loads(payload)
        except json.JSONDecodeError:
            continue
        p["_id"] = row_id
        p["_ts"] = ts
        out.append(p)
    return out


def pair_pa_to_score(pa_rows: list[dict], score_rows: list[dict]) -> dict[int, dict]:
    """Map score_row_id -> matched pa_row (or None).

    Observer write order (verified from
    trading_corp/agents/divisions/bitunix_futures_observer.py:1268-1277):
    _log_pa_validation is called first, then _log_score_decision with
    outcome='skipped_pa_validation'. So PA row id < score row id when
    paired. Pairing strategy: for each score row with
    outcome=skipped_pa_validation, find the most recent PA row with
    matching (trigger_signal, trigger_source, side) and id < score_id.
    """
    pa_sorted = sorted(pa_rows, key=lambda r: r["_id"])
    by_id = {p["_id"]: p for p in pa_sorted}
    ids_sorted = [p["_id"] for p in pa_sorted]

    pairing: dict[int, dict] = {}
    for s in score_rows:
        if s.get("outcome") != "skipped_pa_validation":
            continue
        score_id = s["_id"]
        sig = s.get("trigger_signal")
        src = s.get("trigger_source")
        side = s.get("side")
        # Binary-ish scan backwards: walk pa_sorted ids descending starting
        # from the largest id < score_id. Small windows in practice.
        # Linear scan is fine at this scale (2.9k rows).
        match = None
        for pid in reversed(ids_sorted):
            if pid >= score_id:
                continue
            if score_id - pid > 200:
                break
            p = by_id[pid]
            if (
                p.get("trigger_signal") == sig
                and p.get("trigger_source") == src
                and p.get("score_side") == side
                and p.get("decision") == "reject"
            ):
                match = p
                break
        if match is not None:
            pairing[score_id] = match
    return pairing


def derive_tier(net_score: int, premium: int, standard: int, weak: int, min_fire: int) -> str:
    if net_score < min_fire:
        return "SKIP"
    if net_score >= premium:
        return "PREMIUM"
    if net_score >= standard:
        return "STANDARD"
    if net_score >= weak:
        return "WEAK"
    return "SKIP"


def summarize_outcomes(score_rows: list[dict]) -> Counter:
    return Counter(s.get("outcome") for s in score_rows)


def evaluate_option1(pa_rejects: list[dict], pairing: dict[int, dict]) -> dict:
    """Option 1: require_all=false (>=2 of 3 must pass).
    Re-evaluate each PA-rejected score row under the loosened rule.
    Would PASS if failed-count <= 1.
    """
    paired = []
    unpaired = []
    would_pass = []
    still_reject = []
    failed_count_dist: Counter = Counter()

    for s in pa_rejects:
        pa = pairing.get(s["_id"])
        if pa is None:
            unpaired.append(s)
            continue
        paired.append(s)
        failed = pa.get("failed") or []
        nfail = len(failed)
        failed_count_dist[nfail] += 1
        if nfail <= 1:
            would_pass.append((s, pa))
        else:
            still_reject.append((s, pa))

    by_tier = Counter(s["tier"] for s, _ in would_pass)
    by_side = Counter(s["side"] for s, _ in would_pass)
    return {
        "paired": len(paired),
        "unpaired": len(unpaired),
        "would_pass": len(would_pass),
        "still_reject": len(still_reject),
        "failed_count_dist": dict(failed_count_dist),
        "tier_counts": dict(by_tier),
        "side_counts": dict(by_side),
    }


def evaluate_options_5_6(all_scores: list[dict]) -> dict:
    """For options 5 and 6, the question is: how does tier re-derivation
    under tightened thresholds change the CANDIDATE SET that enters PA?

    These options cannot ADD fires; they only SHRINK the funnel.
    Reported: rows that survive each option on the score side.
    """
    o5_survive = 0   # tier under (premium=10, standard=7, weak=3, min_fire=5)
    o5_drop_from_std = 0
    o5_drop_from_premium = 0
    o6_survive = 0   # net_score >= 7
    o6_drop = 0

    # Of currently-fired/PA-pass rows, would they still survive?
    pa_passed_outcomes = {"skipped_htf_gate", "skipped_trade_plan", "placed",
                           "rejected_risk", "skipped_sizing", "skipped_daily_kill",
                           "skipped_no_equity", "skipped_no_price"}
    o5_pa_pass_survive = []
    o6_pa_pass_survive = []
    o5_placed_survive = []
    o6_placed_survive = []

    for s in all_scores:
        ns = s.get("net_score")
        if ns is None:
            continue
        cur_tier = s.get("tier")
        outcome = s.get("outcome")

        new_tier_5 = derive_tier(ns, premium=10, standard=7, weak=3, min_fire=5)
        new_tier_6 = derive_tier(ns, premium=10, standard=5, weak=3, min_fire=7)

        if new_tier_5 in ("PREMIUM", "STANDARD"):
            o5_survive += 1
        else:
            if cur_tier == "STANDARD":
                o5_drop_from_std += 1
            elif cur_tier == "PREMIUM":
                o5_drop_from_premium += 1

        if ns >= 7:
            o6_survive += 1
        else:
            o6_drop += 1

        if outcome in pa_passed_outcomes:
            if new_tier_5 in ("PREMIUM", "STANDARD"):
                o5_pa_pass_survive.append(s)
            if ns >= 7:
                o6_pa_pass_survive.append(s)

        if outcome == "placed":
            if new_tier_5 in ("PREMIUM", "STANDARD"):
                o5_placed_survive.append(s)
            if ns >= 7:
                o6_placed_survive.append(s)

    return {
        "option5_score_survive": o5_survive,
        "option5_drop_from_standard": o5_drop_from_std,
        "option5_drop_from_premium": o5_drop_from_premium,
        "option5_pa_pass_survive": len(o5_pa_pass_survive),
        "option5_placed_survive": len(o5_placed_survive),
        "option6_score_survive": o6_survive,
        "option6_drop": o6_drop,
        "option6_pa_pass_survive": len(o6_pa_pass_survive),
        "option6_placed_survive": len(o6_placed_survive),
    }


def analyze_all_three_failed_bucket(
    pa_rejects: list[dict], pairing: dict[int, dict]
) -> dict:
    """Characterize the bucket where ALL THREE PA validators failed.

    This is the diagnostic question: are score-engine fires that hit
    PREMIUM/STANDARD tier dominated by a few signal types (score over-
    generous), or do they reflect varied confluence the PA validators
    reject (PA too strict)?
    """
    bucket: list[dict] = []
    required = set(PA_VALIDATORS)
    for s in pa_rejects:
        pa = pairing.get(s["_id"])
        if pa is None:
            continue
        failed = set(pa.get("failed") or [])
        if failed == required:
            bucket.append(s)

    n = len(bucket)
    tier_counts = Counter(s["tier"] for s in bucket)
    side_counts = Counter(s["side"] for s in bucket)
    netscore_dist = Counter(s.get("net_score", 0) for s in bucket)

    sig_top3_count: Counter = Counter()
    sig_total_weight: Counter = Counter()
    dom_sig_count: Counter = Counter()
    dom_sig_with_supports: Counter = Counter()  # (dom_sig, count_of_supporting_sigs)

    # Concentration: how many rows are dominated by a single signal stack?
    stack_count: Counter = Counter()  # sorted tuple of all contributing sigs

    n_solo = 0           # exactly one contributing signal
    n_pair = 0           # exactly two
    n_three_plus = 0     # three or more

    for s in bucket:
        side = s.get("side")
        if side == "buy":
            contribs = s.get("buy_contributions")
        elif side == "sell":
            contribs = s.get("sell_contributions")
        else:
            contribs = None
        # The observer serializes contributions as list of [signal_name, weight]
        # pairs, not as a dict. Defensive: also handle dict shape.
        if isinstance(contribs, dict):
            items = [(k, v) for k, v in contribs.items()]
        elif isinstance(contribs, list):
            items = [(x[0], x[1]) for x in contribs if len(x) >= 2]
        else:
            items = []
        sorted_c = sorted(items, key=lambda kv: -kv[1])
        nsigs = len(sorted_c)
        if nsigs == 1:
            n_solo += 1
        elif nsigs == 2:
            n_pair += 1
        elif nsigs >= 3:
            n_three_plus += 1
        for sig, w in sorted_c[:3]:
            sig_top3_count[sig] += 1
            sig_total_weight[sig] += w
        if sorted_c:
            dom_sig_count[sorted_c[0][0]] += 1
            dom_sig_with_supports[(sorted_c[0][0], min(nsigs, 5))] += 1
        stack_key = tuple(sorted(sig for sig, _ in items))
        stack_count[stack_key] += 1

    # Concentration metrics
    if n:
        top1_pct = (dom_sig_count.most_common(1)[0][1] / n * 100) if dom_sig_count else 0.0
        top3_pct = (sum(x[1] for x in dom_sig_count.most_common(3)) / n * 100) if dom_sig_count else 0.0
        solo_pct = n_solo / n * 100
        pair_pct = n_pair / n * 100
        threeplus_pct = n_three_plus / n * 100
    else:
        top1_pct = top3_pct = solo_pct = pair_pct = threeplus_pct = 0.0

    return {
        "n": n,
        "tier_counts": dict(tier_counts),
        "side_counts": dict(side_counts),
        "netscore_dist": dict(sorted(netscore_dist.items())),
        "top_signals_by_top3_appearance": sig_top3_count.most_common(15),
        "top_signals_by_total_weight": sig_total_weight.most_common(15),
        "dominant_signal_counts": dom_sig_count.most_common(15),
        "top_full_stacks": stack_count.most_common(10),
        "stack_size": {"solo": n_solo, "pair": n_pair, "three_plus": n_three_plus},
        "concentration": {
            "top1_dominant_pct": top1_pct,
            "top3_dominant_pct": top3_pct,
            "solo_pct": solo_pct,
            "pair_pct": pair_pct,
            "three_plus_pct": threeplus_pct,
        },
    }


def render_report(
    n_all_scores: int,
    outcomes: Counter,
    n_pa_rejects: int,
    n_pa_rows_total: int,
    opt1: dict,
    opt56: dict,
    bucket: dict,
) -> str:
    lines: list[str] = []
    L = lines.append

    L("=" * 70)
    L("BITUNIX PA-VALIDATION ALT-CONFIG REPLAY")
    L("=" * 70)
    L(f"Anchor:                       {ANCHOR_TS}")
    L(f"Total score_decided rows:     {n_all_scores}")
    L(f"Total pa_validation rows:     {n_pa_rows_total}")
    L(f"PA-rejected score rows:       {n_pa_rejects}")
    L("")
    L("Outcome distribution:")
    for outcome, c in outcomes.most_common():
        L(f"  {outcome or '(none)':<28} {c}")
    L("")

    L("-" * 70)
    L("OPTION 1: pa_validation.require_all true -> false (>=2 of 3 must pass)")
    L("-" * 70)
    L(f"Paired PA-reject score rows:           {opt1['paired']}")
    L(f"Unpaired (excluded from option 1):     {opt1['unpaired']}")
    L(f"Failed-count distribution (PA rows):")
    for k in sorted(opt1["failed_count_dist"]):
        L(f"  {k} validator(s) failed: {opt1['failed_count_dist'][k]}")
    L("")
    L(f"Would PASS PA under option 1:          {opt1['would_pass']}")
    L(f"Would STILL REJECT (>=2 failed):       {opt1['still_reject']}")
    L(f"  by tier: {opt1['tier_counts']}")
    L(f"  by side: {opt1['side_counts']}")
    if opt1["paired"]:
        pass_pct = opt1["would_pass"] / opt1["paired"] * 100
        L(f"  PA-pass rate under option 1: {pass_pct:.1f}%")
    L("")
    L("Note: 'would PASS' means the trade reaches the HTF gate. Whether it")
    L("ultimately fires depends on HTF (historical 17/27=63.0% hard-zero)")
    L("and trade_plan (historical 7/10=70% STANDARD fee-floor rejection).")
    L("Upper-bound estimate for new placements under option 1 alone:")
    if opt1["paired"]:
        wp = opt1["would_pass"]
        est_after_htf = wp * (1 - 17 / 27)
        est_after_tp = est_after_htf * (1 - 7 / 10)
        L(f"  {wp} would-pass * (1 - 0.630 HTF hardzero) = {est_after_htf:.0f}")
        L(f"  {est_after_htf:.0f} * (1 - 0.700 trade_plan reject) = {est_after_tp:.0f}")
        L(f"  Estimated additional fires under option 1: ~{est_after_tp:.0f}")
        L("  Caveat: HTF/trade_plan rates were measured on PREMIUM-heavy survivors;")
        L("  STANDARD-heavy option-1 survivors may face worse trade_plan rejection.")
    L("")

    L("-" * 70)
    L("OPTION 5: scoring.tier_thresholds.standard 5 -> 7")
    L("-" * 70)
    L(f"All score rows whose tier survives (PREMIUM/STANDARD): {opt56['option5_score_survive']}")
    L(f"  dropped from STANDARD->WEAK/SKIP: {opt56['option5_drop_from_standard']}")
    L(f"  dropped from PREMIUM->* (unchanged since 10>=10): {opt56['option5_drop_from_premium']}")
    L(f"Currently-PA-pass rows that still survive on score side: {opt56['option5_pa_pass_survive']}")
    L(f"Currently-PLACED rows that still survive on score side:  {opt56['option5_placed_survive']}")
    L("")
    L("Effect: option 5 SHRINKS the candidate pool entering PA. It cannot")
    L("produce new fires on a PA-reject corpus. Diagnostic value: if many")
    L("currently-fired trades survive (high score), the engine's STANDARD")
    L("bracket isn't load-bearing for actual placements.")
    L("")

    L("-" * 70)
    L("OPTION 6: scoring.min_score_to_fire 5 -> 7")
    L("-" * 70)
    L(f"Rows with net_score >= 7 (survive option 6):  {opt56['option6_score_survive']}")
    L(f"Rows dropped (net_score < 7):                  {opt56['option6_drop']}")
    L(f"Currently-PA-pass rows that survive:           {opt56['option6_pa_pass_survive']}")
    L(f"Currently-PLACED rows that survive:            {opt56['option6_placed_survive']}")
    L("")

    L("=" * 70)
    L("ALL-THREE-FAILED PA BUCKET (the score<->PA disagreement bucket)")
    L("=" * 70)
    L(f"Bucket size: {bucket['n']}")
    L(f"Tier:        {bucket['tier_counts']}")
    L(f"Side:        {bucket['side_counts']}")
    L("")
    L("net_score distribution:")
    for ns, c in bucket["netscore_dist"].items():
        L(f"  net_score={ns:>3}: {c}")
    L("")
    L("Stack size (# contributing signals on winning side):")
    sz = bucket["stack_size"]
    L(f"  solo (1 sig):       {sz['solo']}  ({bucket['concentration']['solo_pct']:.1f}%)")
    L(f"  pair (2 sigs):      {sz['pair']}  ({bucket['concentration']['pair_pct']:.1f}%)")
    L(f"  three+ sigs:        {sz['three_plus']}  ({bucket['concentration']['three_plus_pct']:.1f}%)")
    L("")
    L(f"Top-1 dominant signal: {bucket['concentration']['top1_dominant_pct']:.1f}% of bucket")
    L(f"Top-3 dominant signals: {bucket['concentration']['top3_dominant_pct']:.1f}% of bucket")
    L("")
    L("Top 15 DOMINANT signals (#1 contributor per row):")
    for sig, c in bucket["dominant_signal_counts"]:
        pct = c / bucket["n"] * 100 if bucket["n"] else 0
        L(f"  {c:>5}  ({pct:5.1f}%)  {sig}")
    L("")
    L("Top 15 signals by TOP-3-CONTRIBUTOR appearance:")
    for sig, c in bucket["top_signals_by_top3_appearance"]:
        L(f"  {c:>5}  {sig}")
    L("")
    L("Top 15 signals by TOTAL weight summed across bucket:")
    for sig, w in bucket["top_signals_by_total_weight"]:
        L(f"  {w:>5}  {sig}")
    L("")
    L("Top 10 full signal STACKS (set of contributing sigs):")
    for stack, c in bucket["top_full_stacks"]:
        sigs = ",".join(stack) if stack else "(empty)"
        L(f"  {c:>5}  [{sigs}]")
    L("")

    L("=" * 70)
    L("HYPOTHESIS HINT (verdict requires human judgment)")
    L("=" * 70)
    top1 = bucket["concentration"]["top1_dominant_pct"]
    top3 = bucket["concentration"]["top3_dominant_pct"]
    solo = bucket["concentration"]["solo_pct"]
    if top1 >= 40 or solo >= 40:
        L("HINT: HIGH CONCENTRATION")
        L(f"  top-1 dominant = {top1:.1f}%, solo-stack = {solo:.1f}%")
        L("  -> Score engine appears over-generous: a small set of signal")
        L("     types is reaching tier threshold without supporting")
        L("     confluence that PA validators look for. Favor options 5/6.")
    elif top3 >= 70:
        L("HINT: MODERATE CONCENTRATION")
        L(f"  top-3 dominant = {top3:.1f}%")
        L("  -> Mixed signal: tightening score thresholds may help, but")
        L("     also consider loosening PA. Combined adjustment likely.")
    else:
        L("HINT: LOW CONCENTRATION")
        L(f"  top-1 dominant = {top1:.1f}%, top-3 = {top3:.1f}%")
        L("  -> Score engine is firing on varied stacks; PA validators")
        L("     are rejecting genuine confluence. Favor option 1.")
    L("")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="Path to trading_corp.db")
    ap.add_argument("--out", help="Optional output file (also prints to stdout)")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: db not found: {db_path}", file=sys.stderr)
        return 2

    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)

    all_scores = fetch_all_score_rows(conn)
    pa_rows = fetch_pa_rows(conn)
    outcomes = summarize_outcomes(all_scores)
    pa_rejects = [s for s in all_scores if s.get("outcome") == "skipped_pa_validation"]
    pairing = pair_pa_to_score(pa_rows, all_scores)

    opt1 = evaluate_option1(pa_rejects, pairing)
    opt56 = evaluate_options_5_6(all_scores)
    bucket = analyze_all_three_failed_bucket(pa_rejects, pairing)

    report = render_report(
        n_all_scores=len(all_scores),
        outcomes=outcomes,
        n_pa_rejects=len(pa_rejects),
        n_pa_rows_total=len(pa_rows),
        opt1=opt1,
        opt56=opt56,
        bucket=bucket,
    )
    print(report)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(main())
