"""Empirical replay — Option A via the modified prod code (not the empirics-v2 surrogate).

Reads cached activity + resolutions from `tmp/2026-05-26_clustering_plan/cache/`
and feeds them through the ACTUAL `_select_resolved_buys_window` shipped in
`trading_corp/scripts/seed_polymarket_watchlist_deep.py`. Verifies that the
shipped implementation reproduces the plan's empirical numbers within tolerance.

Plan numbers to reproduce (from reports/2026-05-26_polymarket_clustering_fix_plan.md):
  - Runaround: A → n=100 wins=60 losses=40 wr=0.6000
  - weflyhigh: A → n=24 wins=14 losses=10 wr=0.5833
  - surfandturf: A → n=4 wins=1 losses=3 wr=0.2500
  - Mosley1: stored 100/0; under A n=17 wr=0.4706 (from cohort top-10 table)
  - Clean list (n>=50 AND WR>=0.62): 225 (current) -> 97 (A)

Note: the plan used cid-only dedupe (empirics_v2.py window_A). The shipped code
uses (cid, outcome_index) dedupe per Board direction. For traders without
hedges these should agree; if a trader has split bets on both sides of a market,
the (cid, oi) implementation will produce a slightly higher n.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

# Make the trading_corp package importable.
REPO_ROOT = r"C:\Users\AA Incorporado\cc"
sys.path.insert(0, REPO_ROOT)

from trading_corp.data.polymarket_data_api_client import ActivityRow  # noqa: E402
from trading_corp.scripts.seed_polymarket_watchlist_deep import (  # noqa: E402
    _select_resolved_buys_window,
)

CACHE = r"C:\Users\AA Incorporado\cc\tmp\2026-05-26_clustering_plan\cache"
WATCHLIST = r"C:\Users\AA Incorporado\cc\tmp\2026-05-26_clustering_plan\watchlist.json"
OUT = r"C:\Users\AA Incorporado\cc\tmp\2026-05-26_clustering_plan\replay_via_prod_code.json"

MIN_RESOLVED_BUYS = 10
PROVISIONAL_THRESHOLD = 50
MIN_WINDOWED_WR = 0.62
WINDOW_SIZE = 100


def _activity_row_from_dict(d: dict) -> ActivityRow:
    """Reconstruct ActivityRow from the raw /activity JSON row that was cached."""
    return ActivityRow(
        proxy_wallet=d.get("proxyWallet") or d.get("proxy_wallet", ""),
        timestamp=int(d.get("timestamp") or 0),
        condition_id=d.get("conditionId") or d.get("condition_id") or "",
        type=d.get("type") or "",
        size=float(d.get("size") or 0.0),
        usdc_size=float(d.get("usdcSize") or d.get("usdc_size") or 0.0),
        transaction_hash=d.get("transactionHash") or "",
        price=float(d.get("price") or 0.0),
        asset=d.get("asset") or "",
        side=d.get("side") or "",
        outcome_index=int(d.get("outcomeIndex") if d.get("outcomeIndex") is not None
                          else d.get("outcome_index", -1)),
        title=d.get("title") or "",
        slug=d.get("slug") or "",
        event_slug=d.get("eventSlug") or "",
        outcome=d.get("outcome") or "",
        name=d.get("name") or "",
    )


def _load_cached(wallet: str, kind_suffix: str = "") -> Any:
    """Find the most-specific cache file for this wallet+kind. Returns None if absent."""
    target = f"{wallet.lower()}_activity.json" if kind_suffix == "activity" else None
    for fn in os.listdir(CACHE):
        if not fn.startswith(wallet.lower()):
            continue
        if kind_suffix == "activity" and fn.endswith("_activity.json"):
            with open(os.path.join(CACHE, fn), encoding="utf-8") as f:
                return json.load(f)
        if kind_suffix == "resolutions_cohort" and "_cohort_resolutions.json" in fn:
            with open(os.path.join(CACHE, fn), encoding="utf-8") as f:
                return json.load(f)
        if kind_suffix == "resolutions_test" and fn.endswith("_resolutions.json") and "cohort" not in fn:
            with open(os.path.join(CACHE, fn), encoding="utf-8") as f:
                return json.load(f)
    return None


def _resolutions_to_status_dict(raw: dict) -> dict:
    """Cache files store {'is_resolved', 'winner_idx', ...}; seed code expects
    'status' / 'winning_outcome_index' keys per `_select_resolved_buys_window`
    and `_is_win_for_buy`. Translate.
    """
    out: dict = {}
    for cid, r in raw.items():
        status = "resolved" if r.get("is_resolved") else (
            "closed" if r.get("closed") else "open"
        )
        out[cid] = {
            "status": status,
            "winning_outcome_index": r.get("winner_idx"),
            "outcomes": r.get("outcomes", []),
            "outcome_prices": r.get("prices", []),
            "closed": r.get("closed", False),
        }
    return out


def replay_wallet(wallet: str, user_name: str = "",
                  use_test_cache: bool = False) -> dict:
    """Run the shipped _select_resolved_buys_window against cached data."""
    activity_raw = _load_cached(wallet, "activity")
    if activity_raw is None:
        return {"wallet": wallet, "error": "activity_cache_missing"}
    res_raw = _load_cached(
        wallet, "resolutions_test" if use_test_cache else "resolutions_cohort",
    )
    if res_raw is None:
        return {"wallet": wallet, "error": "resolutions_cache_missing"}

    activity_rows = [_activity_row_from_dict(r) for r in activity_raw]
    resolutions = _resolutions_to_status_dict(res_raw)
    window = _select_resolved_buys_window(
        activity_rows, resolutions, window_size=WINDOW_SIZE,
    )

    # Score wins/losses per the existing _is_win_for_buy semantics.
    wins = losses = 0
    for a in window:
        res = resolutions.get(a.condition_id, {})
        wi = res.get("winning_outcome_index")
        if wi is None or a.outcome_index is None:
            losses += 1
            continue
        try:
            if int(wi) == int(a.outcome_index):
                wins += 1
            else:
                losses += 1
        except (TypeError, ValueError):
            losses += 1
    n = wins + losses
    wr = wins / n if n else 0.0
    return {
        "wallet": wallet, "user_name": user_name,
        "n": n, "wins": wins, "losses": losses, "wr": round(wr, 4),
        "provisional": n < PROVISIONAL_THRESHOLD,
        "passes_n_floor": n >= MIN_RESOLVED_BUYS,
        "passes_wr_floor": wr >= MIN_WINDOWED_WR,
    }


def main():
    with open(WATCHLIST, encoding="utf-8") as f:
        wl = json.load(f)

    target_traders = {
        "Runaround":   "0xc0ff6a9ac424210cf218fda5c5753324c34a9953",
        "weflyhigh":   "0x03e8a544e97eeff5753bc1e90d46e5ef22af1697",
        "surfandturf": "0x9f2fe025f84839ca81dd8e0338892605702d2ca8",
        "Mosley1":     "0x5bec79df9add70a3892041ab1a5516b60f53b215",
    }

    expected = {
        "Runaround":   {"n_min": 99, "n_max": 100, "wr_min": 0.59, "wr_max": 0.61},
        "weflyhigh":   {"n_min": 23, "n_max": 26, "wr_min": 0.56, "wr_max": 0.62},
        "surfandturf": {"n_min": 3,  "n_max": 6,  "wr_min": 0.20, "wr_max": 0.35},
        "Mosley1":     {"n_min": 15, "n_max": 20, "wr_min": 0.42, "wr_max": 0.55},
    }

    print("=== Test traders — replay vs plan expectations ===\n")
    print(f"{'Name':14}{'n':>5}{'wins':>6}{'losses':>8}{'wr':>9}    {'expect':18} verdict")
    test_results: dict[str, dict] = {}
    all_pass = True
    for name, wallet in target_traders.items():
        # use_test_cache=True picks up the all-cids resolution set when present
        use_test = name in ("Runaround", "weflyhigh", "surfandturf")
        r = replay_wallet(wallet, user_name=name, use_test_cache=use_test)
        test_results[name] = r
        if "error" in r:
            print(f"  {name:14} ERROR: {r['error']}")
            all_pass = False
            continue
        e = expected[name]
        n_ok = e["n_min"] <= r["n"] <= e["n_max"]
        wr_ok = e["wr_min"] <= r["wr"] <= e["wr_max"]
        verdict = "PASS" if (n_ok and wr_ok) else "FAIL"
        if not (n_ok and wr_ok):
            all_pass = False
        expect = f"n[{e['n_min']}-{e['n_max']}] wr[{e['wr_min']}-{e['wr_max']}]"
        print(f"  {name:14}{r['n']:>5}{r['wins']:>6}{r['losses']:>8}{r['wr']:>9.4f}    {expect:18} {verdict}")

    print("\n=== Cohort sweep (uses cohort-cached resolutions, first-300-cids slice) ===")
    cohort_results = []
    for entry in wl:
        wallet = entry.get("proxy_wallet", "")
        if not wallet:
            continue
        r = replay_wallet(wallet, user_name=entry.get("user_name", ""))
        cohort_results.append(r)
    print(f"  {len(cohort_results)} wallets replayed")

    pass_n = sum(1 for r in cohort_results if "n" in r and r["passes_n_floor"])
    drop_n = sum(1 for r in cohort_results if "n" in r and not r["passes_n_floor"])
    clean_A = sum(1 for r in cohort_results if r.get("n", 0) >= 50 and r.get("wr", 0) >= 0.62)
    print(f"  passes n>=10 floor: {pass_n}")
    print(f"  drops below n=10: {drop_n}")
    print(f"  clean list (n>=50 AND wr>=0.62): {clean_A}")

    # Plan's expectations
    print("\n=== Plan expectations ===")
    print("  test traders (above) within tolerance band")
    print("  cohort: clean A ~97 (plan number; (cid,oi) impl may differ slightly)")

    out = {
        "test_traders": test_results,
        "cohort_summary": {
            "total": len(cohort_results),
            "pass_n_floor": pass_n,
            "drop_n_floor": drop_n,
            "clean_n50_wr62": clean_A,
        },
        "cohort_detail": cohort_results,
        "verdict": "PASS" if all_pass else "FAIL",
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"\nWritten: {OUT}")
    if not all_pass:
        print("FAIL — at least one test trader fell outside the plan tolerance band.")
        sys.exit(1)
    print("PASS — all test traders within tolerance.")


if __name__ == "__main__":
    main()
