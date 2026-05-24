#!/usr/bin/env python3
"""Phase 0 verdict aggregator for the Kalshi Sports Arbitrage observer.

Reads `kalshi_sports_arb_observation` rows from `audit_event`, joined
implicitly with `kalshi_sports_arb_scan` cycle summaries, and produces
per-hypothesis aggregates plus the mandatory caveats section the
Phase 0 plan requires (anti-false-KILL discipline per
[[kalshi-crypto-shelved]]).

Output verdict in {GO_PHASE_1_ODDS_API, GO_PHASE_1_NEEDS_PINNACLE,
INCONCLUSIVE_INSTRUMENT_TOO_WEAK, KILL}. The script reports the
numbers + caveats and proposes a verdict; the operator decides.

Read-only. Run early (against first-day rows, not seventh) to confirm
the aggregator works while there's still time to fix collection if a
field is missing.

Usage:
  python scripts/analyze_kalshi_sports_arb_observations.py --db data/trading_corp.db
  python scripts/analyze_kalshi_sports_arb_observations.py --db data/trading_corp.db --league MLB
  python scripts/analyze_kalshi_sports_arb_observations.py --db data/trading_corp.db --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict
from typing import Any


# Verdict thresholds -- heuristic; operator reads + decides
_INSUFFICIENT_N = 30                          # < 30 rows => INCONCLUSIVE
_A_ARB_HIT_GO = 0.05                          # >=5% of rows yield positive-EV A
_B_HIT_GO = 0.10                              # >=10% of B rows positive
_MEAN_EV_KILL_THRESHOLD_PER_ROW = -0.20       # mean EV <= -$0.20/row at $10 -> kill signal candidate


def _safe_get(d: dict, *path, default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
        if cur is None:
            return default
    return cur


def analyze(db_path: str, league_filter: str | None = None) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    obs_rows = conn.execute(
        "SELECT ts, payload_json FROM audit_event "
        "WHERE kind='kalshi_sports_arb_observation' ORDER BY ts ASC"
    ).fetchall()
    scan_rows = conn.execute(
        "SELECT ts, payload_json FROM audit_event "
        "WHERE kind='kalshi_sports_arb_scan' ORDER BY ts ASC"
    ).fetchall()
    unmapped_rows = conn.execute(
        "SELECT ts, payload_json FROM audit_event "
        "WHERE kind='kalshi_sports_arb_unmapped' ORDER BY ts ASC"
    ).fetchall()
    conn.close()

    # Per-league observation aggregates
    per_league: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "n_total": 0,
        "n_kalshi_quote_invalid": 0,
        "n_pinnacle_used": 0,
        "n_with_a_arb_evaluated": 0,
        "n_a_arb_positive_ev": 0,
        "n_a_arb_is_arb": 0,
        "a_arb_ev_dollars": [],
        "n_with_b_evaluated": 0,
        "n_b_positive_ev": 0,
        "b_ev_dollars": [],
        "sharp_books_used": defaultdict(int),
        "market_types_seen": defaultdict(int),
        "first_ts": None,
        "last_ts": None,
        "distinct_games": set(),
    })

    for row in obs_rows:
        ts = row["ts"]
        payload = json.loads(row["payload_json"])
        league = _safe_get(payload, "matching_key", "league", default="UNKNOWN")
        if league_filter and league != league_filter:
            continue
        a = per_league[league]
        a["n_total"] += 1
        if a["first_ts"] is None:
            a["first_ts"] = ts
        a["last_ts"] = ts
        # Distinct games key
        mk = payload.get("matching_key") or {}
        game_key = (
            mk.get("game_date_utc"), mk.get("team_home"), mk.get("team_away"),
        )
        a["distinct_games"].add(game_key)
        a["market_types_seen"][mk.get("market_type", "UNKNOWN")] += 1
        if payload.get("kalshi_quote_invalid"):
            a["n_kalshi_quote_invalid"] += 1
        if payload.get("pinnacle_used"):
            a["n_pinnacle_used"] += 1
        sbu = payload.get("sharp_book_used")
        if sbu:
            a["sharp_books_used"][sbu] += 1
        # A-arb
        best_a = payload.get("a_arb_best")
        if best_a is not None:
            a["n_with_a_arb_evaluated"] += 1
            ev = best_a.get("ev_dollars")
            if isinstance(ev, (int, float)):
                a["a_arb_ev_dollars"].append(ev)
                if ev > 0:
                    a["n_a_arb_positive_ev"] += 1
                if best_a.get("is_arb"):
                    a["n_a_arb_is_arb"] += 1
        # B
        b_ev = payload.get("b_ev_dollars")
        if isinstance(b_ev, (int, float)):
            a["n_with_b_evaluated"] += 1
            a["b_ev_dollars"].append(b_ev)
            if b_ev > 0:
                a["n_b_positive_ev"] += 1

    # Summarize cycle health
    cycle_summary = {
        "n_scan_cycles": len(scan_rows),
        "first_scan_ts": scan_rows[0]["ts"] if scan_rows else None,
        "last_scan_ts": scan_rows[-1]["ts"] if scan_rows else None,
        "n_unmapped_audits": len(unmapped_rows),
    }
    if scan_rows:
        last_scan_payload = json.loads(scan_rows[-1]["payload_json"])
        cycle_summary["last_scan_per_league"] = last_scan_payload.get("per_league") or {}
        cycle_summary["last_quota_remaining"] = last_scan_payload.get("odds_api_quota_remaining")
        cycle_summary["last_quota_used"] = last_scan_payload.get("odds_api_quota_used")

    # Convert defaultdicts + sets to plain types for JSON
    per_league_out: dict[str, dict] = {}
    for league, a in per_league.items():
        ev_a = a["a_arb_ev_dollars"]
        ev_b = a["b_ev_dollars"]
        per_league_out[league] = {
            "n_total": a["n_total"],
            "first_ts": a["first_ts"],
            "last_ts": a["last_ts"],
            "n_distinct_games": len(a["distinct_games"]),
            "market_types_seen": dict(a["market_types_seen"]),
            "n_kalshi_quote_invalid": a["n_kalshi_quote_invalid"],
            "n_pinnacle_used": a["n_pinnacle_used"],
            "pct_pinnacle_used": (
                round(a["n_pinnacle_used"] / a["n_total"], 4) if a["n_total"] else None
            ),
            "sharp_books_used": dict(a["sharp_books_used"]),
            "a_arb": _summarize_ev(ev_a),
            "a_arb_n_evaluated": a["n_with_a_arb_evaluated"],
            "a_arb_n_positive_ev": a["n_a_arb_positive_ev"],
            "a_arb_n_is_arb": a["n_a_arb_is_arb"],
            "b": _summarize_ev(ev_b),
            "b_n_evaluated": a["n_with_b_evaluated"],
            "b_n_positive_ev": a["n_b_positive_ev"],
        }

    # Propose verdict per league (operator confirms)
    verdicts: dict[str, dict] = {}
    for league, agg in per_league_out.items():
        verdicts[league] = _propose_verdict(agg)

    return {
        "cycle_summary": cycle_summary,
        "per_league": per_league_out,
        "verdicts": verdicts,
        "caveats": _mandatory_caveats(per_league_out),
    }


def _summarize_ev(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "median": None, "min": None, "max": None, "stdev": None}
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 4),
        "median": round(statistics.median(values), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "stdev": round(statistics.pstdev(values), 4) if len(values) > 1 else 0.0,
    }


def _propose_verdict(agg: dict[str, Any]) -> dict[str, Any]:
    n = agg["n_total"]
    if n < _INSUFFICIENT_N:
        return {
            "verdict": "INCONCLUSIVE_INSTRUMENT_TOO_WEAK",
            "reason": f"n={n} < {_INSUFFICIENT_N} threshold",
        }
    a_n_eval = agg["a_arb_n_evaluated"]
    a_pos = agg["a_arb_n_positive_ev"]
    a_pos_rate = a_pos / a_n_eval if a_n_eval else 0.0
    a_mean_ev = (agg["a_arb"] or {}).get("mean") or 0.0
    b_n_eval = agg["b_n_evaluated"]
    b_pos = agg["b_n_positive_ev"]
    b_pos_rate = b_pos / b_n_eval if b_n_eval else 0.0
    b_mean_ev = (agg["b"] or {}).get("mean") or 0.0
    pct_pin = agg.get("pct_pinnacle_used") or 0.0
    needs_pinnacle = pct_pin < 0.5 and b_pos_rate < _B_HIT_GO

    if a_pos_rate >= _A_ARB_HIT_GO:
        return {"verdict": "GO_PHASE_1_ODDS_API",
                "reason": f"A-arb positive-EV rate {a_pos_rate:.1%} >= {_A_ARB_HIT_GO:.0%} threshold"}
    if b_pos_rate >= _B_HIT_GO and pct_pin >= 0.5:
        return {"verdict": "GO_PHASE_1_ODDS_API",
                "reason": f"B positive-EV rate {b_pos_rate:.1%} >= {_B_HIT_GO:.0%}; Pinnacle in {pct_pin:.1%} of rows"}
    if needs_pinnacle:
        return {"verdict": "GO_PHASE_1_NEEDS_PINNACLE",
                "reason": f"Pinnacle present in only {pct_pin:.1%} of rows; B-test under soft-book proxy; cannot KILL"}
    if a_mean_ev < _MEAN_EV_KILL_THRESHOLD_PER_ROW and b_mean_ev < _MEAN_EV_KILL_THRESHOLD_PER_ROW:
        return {"verdict": "KILL_CANDIDATE",
                "reason": f"Both mean EVs below {_MEAN_EV_KILL_THRESHOLD_PER_ROW}; review caveats before confirming"}
    return {
        "verdict": "INCONCLUSIVE_INSTRUMENT_TOO_WEAK",
        "reason": "no hypothesis clears its GO threshold; not negative enough for KILL",
    }


def _mandatory_caveats(per_league: dict[str, dict]) -> list[str]:
    out = []
    out.append(
        "HOUR-SCALE ONLY -- observer polls every ~1h; sub-hour lead-lag edges "
        "(typical sportsbook horizon, often minutes) are structurally invisible."
    )
    out.append(
        "GAME-MARKETS ONLY -- Kalshi offers ML binaries for game markets (NBA/MLB); "
        "no full-game spread/total binary contracts as of 2026-05-23. Thinner / "
        "alternate-line / in-game / player-prop markets unobserved."
    )
    for league, agg in per_league.items():
        pct_pin = agg.get("pct_pinnacle_used") or 0.0
        if pct_pin < 0.5:
            out.append(
                f"SOFT-BOOK PROXY CAVEAT ({league}) -- Pinnacle present in only "
                f"{pct_pin:.1%} of rows; Hypothesis B for {league} was tested largely "
                f"against median(DK/FD/BetMGM) proxy, which follows the market rather "
                f"than leads it. A null B result under proxy is NOT a true sharp-book test."
            )
    if any(l == "MLB" for l in per_league):
        out.append(
            "MLB GRADING UNVERIFIED -- rain-shortened/official-game rule, pitcher-listed "
            "rule, and extra-innings handling between Kalshi vs DK/FD/BetMGM not yet "
            "audited. Hypothesis A live-action requires the deferred grading matrix."
        )
    return out


def _print_human(report: dict[str, Any]) -> None:
    cs = report["cycle_summary"]
    print("=" * 78)
    print("KALSHI SPORTS ARBITRAGE OBSERVER -- PHASE 0 VERDICT REPORT")
    print("=" * 78)
    print(f"Scan cycles seen: {cs['n_scan_cycles']}  "
          f"(first {cs['first_scan_ts']}, last {cs['last_scan_ts']})")
    print(f"Unmapped audit rows: {cs['n_unmapped_audits']}")
    if cs.get("last_quota_remaining") is not None:
        print(f"Last seen odds-api quota: remaining={cs['last_quota_remaining']}; "
              f"used={cs['last_quota_used']}")
    print()
    if not report["per_league"]:
        print("NO OBSERVATIONS YET.")
        if cs['n_scan_cycles'] == 0:
            print("Scan cycle has not fired. Confirm observer is enabled + running.")
        else:
            print("Scan cycles firing but emitting no observations; check `kalshi_sports_arb_unmapped` audits.")
        return
    for league, agg in report["per_league"].items():
        print(f"-- {league} --")
        print(f"  rows: {agg['n_total']}  distinct games: {agg['n_distinct_games']}  "
              f"first: {agg['first_ts']}  last: {agg['last_ts']}")
        print(f"  market types: {agg['market_types_seen']}")
        print(f"  kalshi_quote_invalid: {agg['n_kalshi_quote_invalid']}")
        print(f"  Pinnacle used in {agg['n_pinnacle_used']}/{agg['n_total']} "
              f"({(agg['pct_pinnacle_used'] or 0)*100:.1f}%); sharp_books_used={agg['sharp_books_used']}")
        print(f"  A-arb evaluated: {agg['a_arb_n_evaluated']}  positive-EV: "
              f"{agg['a_arb_n_positive_ev']}  guaranteed-arb: {agg['a_arb_n_is_arb']}")
        a = agg.get("a_arb") or {}
        if a.get("n"):
            print(f"    EV $: mean={a['mean']} median={a['median']} max={a['max']} min={a['min']}")
        print(f"  B-leadlag evaluated: {agg['b_n_evaluated']}  positive-EV: "
              f"{agg['b_n_positive_ev']}")
        b = agg.get("b") or {}
        if b.get("n"):
            print(f"    EV $: mean={b['mean']} median={b['median']} max={b['max']} min={b['min']}")
        v = report["verdicts"].get(league) or {}
        print(f"  PROPOSED VERDICT: {v.get('verdict')}  ({v.get('reason')})")
        print()
    print("MANDATORY CAVEATS:")
    for c in report["caveats"]:
        print(f"  - {c}")
    print()
    print("Operator: any GO or KILL is only valid in light of the caveats above.")
    print("If verdict is INCONCLUSIVE, review which caveat was load-bearing for")
    print("the inconclusiveness before deciding on a Phase 0.5 instrument fix.")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True, help="Path to SQLite DB containing audit_event")
    p.add_argument("--league", default=None, help="Filter to single league (e.g. MLB)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    report = analyze(args.db, league_filter=args.league)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
