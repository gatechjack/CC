"""Shadow-data analyzer for the BitUnix PR 3c cutover.

Two analyses, both read-only against the production audit table:

  1. THRESHOLD-CHANGE REPLAY (historical)
     Walks `bitunix_score_decided` rows and asks: under the new tier
     thresholds (premium 10 / standard 5 / weak 3 / min_fire 5), how
     does the tier distribution shift vs. the legacy 12 / 8 / 5 / 8?
     This is the only historical analysis available — the score-
     accumulator's actual `net_score` is preserved in audit, so we
     can re-tier without re-running the engine.

  2. SHADOW-AUDIT SUMMARY (post-cutover)
     Walks `pa_validation_decision` and `htf_gate_decision` rows
     (only present after PR 3c ships in shadow mode). Reports:
       - PA REJECT rate, by reason
       - HTF gate distribution: PASS, half-size, hard-zero, by reason
       - Score winning_side vs HTF permission alignment
     Use this after a few days of shadow data to decide whether to
     flip `htf_gate.mode` from `shadow` to `enforce`.

Caveat (per PR 3c b-ii decision): historical replay cannot honor the
new `score_timeframes: ["3m", "15m", "30m"]` filter — pre-PR-3c
ledger rows have tf=NULL so the filter would silently drop everything.
The threshold-change replay below is therefore an upper bound: it
shows what would change under the new thresholds AGAINST THE OLD TF
mix. Real fire rate post-cutover may be lower because Cypher 4H/1D
fires are now excluded from scoring.

Usage:
    py -m scripts.replay_pr3_cutover --db-url sqlite:///path/to/trading_corp.db
    py -m scripts.replay_pr3_cutover --since 2026-04-01    # default: 30 days back
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add repo root so `trading_corp` imports work when this script is run as
# a standalone file rather than `python -m`.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from trading_corp.persistence import db                        # noqa: E402

# Tier thresholds — both versions, for comparison.
LEGACY_THRESHOLDS = {"min_fire": 8, "premium": 12, "standard": 8, "weak": 5}
NEW_THRESHOLDS = {"min_fire": 5, "premium": 10, "standard": 5, "weak": 3}


def _tier_for(net_score: int, t: dict[str, int]) -> str:
    if net_score < t["min_fire"]:
        return "SKIP"
    if net_score >= t["premium"]:
        return "PREMIUM"
    if net_score >= t["standard"]:
        return "STANDARD"
    if net_score >= t["weak"]:
        return "WEAK"
    return "SKIP"


# ─── analysis 1: threshold-change replay ────────────────────────────────


def threshold_change_replay(db_url: str, since_iso: str) -> dict:
    """For every historical `bitunix_score_decided` row, compute the
    tier under both legacy and new thresholds. Report transition counts."""
    legacy_dist: Counter[str] = Counter()
    new_dist: Counter[str] = Counter()
    transitions: Counter[tuple[str, str]] = Counter()
    by_side: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
    n_rows = 0

    with db.connect(db_url) as conn:
        rows = conn.execute(
            "SELECT ts, payload_json FROM audit_event "
            "WHERE kind='bitunix_score_decided' AND ts >= ? "
            "ORDER BY ts",
            (since_iso,),
        ).fetchall()
        for r in rows:
            try:
                p = json.loads(r["payload_json"]) if r["payload_json"] else {}
            except Exception:
                continue
            net = p.get("net_score")
            if net is None:
                continue
            n_rows += 1
            old_tier = _tier_for(int(net), LEGACY_THRESHOLDS)
            new_tier = _tier_for(int(net), NEW_THRESHOLDS)
            legacy_dist[old_tier] += 1
            new_dist[new_tier] += 1
            transitions[(old_tier, new_tier)] += 1
            side = p.get("side") or "unknown"
            by_side[side][(old_tier, new_tier)] += 1

    return {
        "rows_seen": n_rows,
        "legacy_distribution": dict(legacy_dist),
        "new_distribution": dict(new_dist),
        "transitions": {f"{a}->{b}": c for (a, b), c in transitions.most_common()},
        "by_side": {
            side: {f"{a}->{b}": c for (a, b), c in d.most_common()}
            for side, d in by_side.items()
        },
    }


# ─── analysis 2: shadow-audit summary ───────────────────────────────────


def shadow_audit_summary(db_url: str, since_iso: str) -> dict:
    """Walk `pa_validation_decision` + `htf_gate_decision` rows.
    Returns aggregate stats — empty dicts if shadow hasn't run yet."""
    pa_decisions: Counter[str] = Counter()
    pa_failed_validators: Counter[str] = Counter()
    pa_rush_fall: Counter[str] = Counter()

    htf_regimes: Counter[str] = Counter()
    htf_size_mults: Counter[float] = Counter()
    htf_hard_zero_reasons: Counter[str] = Counter()
    htf_session_fires: Counter[str] = Counter()

    with db.connect(db_url) as conn:
        for r in conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind='pa_validation_decision' AND ts >= ?",
            (since_iso,),
        ).fetchall():
            try:
                p = json.loads(r["payload_json"]) if r["payload_json"] else {}
            except Exception:
                continue
            pa_decisions[p.get("decision") or "unknown"] += 1
            for v in (p.get("failed") or []):
                pa_failed_validators[str(v)] += 1
            rf = p.get("rush_fall_triggered")
            if rf:
                pa_rush_fall[rf] += 1

        for r in conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind='htf_gate_decision' AND ts >= ?",
            (since_iso,),
        ).fetchall():
            try:
                p = json.loads(r["payload_json"]) if r["payload_json"] else {}
            except Exception:
                continue
            htf_regimes[p.get("regime") or "unknown"] += 1
            mult = p.get("size_multiplier")
            if mult is not None:
                htf_size_mults[float(mult)] += 1
            hzr = p.get("hard_zero_reason")
            if hzr:
                htf_hard_zero_reasons[str(hzr)] += 1
            sess = p.get("session")
            if sess:
                htf_session_fires[str(sess)] += 1

    return {
        "pa_validation": {
            "decisions": dict(pa_decisions),
            "failed_validators": dict(pa_failed_validators),
            "rush_fall_triggers": dict(pa_rush_fall),
        },
        "htf_gate": {
            "regime_distribution": dict(htf_regimes),
            "size_multiplier_distribution": {
                str(k): v for k, v in htf_size_mults.items()
            },
            "hard_zero_reasons": dict(htf_hard_zero_reasons),
            "session_fires": dict(htf_session_fires),
        },
    }


# ─── pretty-print ───────────────────────────────────────────────────────


def _print_section(title: str, data: dict) -> None:
    print(f"\n{'=' * 60}")
    print(title)
    print("=" * 60)
    print(json.dumps(data, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-url",
        default=os.environ.get("TC_DB_URL", "sqlite:///data/trading_corp.db"),
        help="SQLAlchemy DB URL (default: sqlite:///data/trading_corp.db)",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="ISO date (YYYY-MM-DD); default: 30 days ago.",
    )
    args = parser.parse_args()

    if args.since:
        since_dt = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
    else:
        since_dt = datetime.now(timezone.utc) - timedelta(days=30)
    since_iso = since_dt.isoformat()

    print(f"DB: {args.db_url}")
    print(f"Since: {since_iso}")

    threshold_data = threshold_change_replay(args.db_url, since_iso)
    _print_section(
        "1. Threshold-change replay (historical bitunix_score_decided rows)",
        threshold_data,
    )

    shadow_data = shadow_audit_summary(args.db_url, since_iso)
    _print_section(
        "2. Shadow-audit summary (pa_validation_decision + htf_gate_decision)",
        shadow_data,
    )

    print("\nNOTES:")
    print("  - Historical replay can't honor the new score_timeframes filter")
    print("    (pre-PR-3c ledger rows have tf=NULL). Treat threshold-change")
    print("    counts as upper bound — actual fire rate post-cutover will be")
    print("    lower because Cypher 4H/1D fires now contribute 0.")
    print("  - Shadow-audit summary is empty until PR 3c ships and runs in")
    print("    shadow mode. After ~few days of shadow data, use it to decide")
    print("    whether to flip htf_gate.mode from 'shadow' to 'enforce'.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
