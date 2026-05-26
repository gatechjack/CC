"""Empirical replay — Option A + PnL aggregation, using shipped+staged prod code.

Same scaffold as `replay_via_prod_code.py` but invokes the new
`_aggregate_window_to_decisions` and computes PnL with the production
formula across the aggregated rows. Verifies the cohort hits ~97-172 under
n>=10 + WR>=0.62 + $5k-PnL all together.

Hypothesis (from the PnL-aggregation plan): the aggregated-PnL identity
`(1-wavg)*total_size == sum_i (1-p_i)*s_i` means decision-PnL equals what
fill-counted PnL was. So with correct PnL, the cohort should match the plan's
171 wallets that pass n>=10 + WR>=0.62, mostly clearing the $5k floor that
they previously had no problem clearing under fill-counted economics.
"""
from __future__ import annotations

import json
import os
import sys

REPO_ROOT = r"C:\Users\AA Incorporado\cc"
sys.path.insert(0, REPO_ROOT)

from trading_corp.data.polymarket_data_api_client import ActivityRow  # noqa: E402
from trading_corp.scripts.seed_polymarket_watchlist_deep import (  # noqa: E402
    _aggregate_window_to_decisions,
    _select_resolved_buys_window,
)

CACHE = r"C:\Users\AA Incorporado\cc\tmp\2026-05-26_clustering_plan\cache"
WATCHLIST = r"C:\Users\AA Incorporado\cc\tmp\2026-05-26_clustering_plan\watchlist.json"
OUT = r"C:\Users\AA Incorporado\cc\tmp\2026-05-26_clustering_plan\replay_with_pnl_agg.json"

MIN_RESOLVED_BUYS = 10
PROVISIONAL_THRESHOLD = 50
MIN_WINDOWED_WR = 0.62
MIN_WINDOWED_PNL = 5000.0
WINDOW_SIZE = 100


def _activity_row_from_dict(d: dict) -> ActivityRow:
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


def _load_cached(wallet: str, kind: str):
    for fn in os.listdir(CACHE):
        if not fn.startswith(wallet.lower()):
            continue
        if kind == "activity" and fn.endswith("_activity.json"):
            with open(os.path.join(CACHE, fn), encoding="utf-8") as f:
                return json.load(f)
        if kind == "resolutions_cohort" and "_cohort_resolutions.json" in fn:
            with open(os.path.join(CACHE, fn), encoding="utf-8") as f:
                return json.load(f)
        if kind == "resolutions_test" and fn.endswith("_resolutions.json") and "cohort" not in fn:
            with open(os.path.join(CACHE, fn), encoding="utf-8") as f:
                return json.load(f)
    return None


def _resolutions_to_status_dict(raw: dict) -> dict:
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
    # Apply PnL-aggregation fix: collapse each (cid, oi) decision to one
    # synthetic row carrying ALL fills' aggregate size + weighted avg price.
    window = _aggregate_window_to_decisions(activity_rows, window)

    # Score wins/losses + PnL per the production compute_polymarket_stats math.
    wins = losses = 0
    total_pnl = 0.0
    prices = []
    for a in window:
        res = resolutions.get(a.condition_id, {})
        wi = res.get("winning_outcome_index")
        if wi is None or a.outcome_index is None:
            losses += 1
            continue
        try:
            is_win = int(wi) == int(a.outcome_index)
        except (TypeError, ValueError):
            losses += 1
            continue
        if is_win:
            wins += 1
        else:
            losses += 1
        per_contract_pnl = (1.0 - a.price) if is_win else -a.price
        total_pnl += per_contract_pnl * a.size
        prices.append(a.price)

    n = wins + losses
    wr = wins / n if n else 0.0
    avg_entry_price = sum(prices) / len(prices) if prices else 0.0
    share_below_70 = sum(1 for p in prices if p < 0.70) / len(prices) if prices else 0.0
    return {
        "wallet": wallet, "user_name": user_name,
        "n": n, "wins": wins, "losses": losses, "wr": round(wr, 4),
        "realized_pnl_usdc": round(total_pnl, 2),
        "avg_entry_price": round(avg_entry_price, 4),
        "share_below_70": round(share_below_70, 4),
        "provisional": n < PROVISIONAL_THRESHOLD,
        "passes_n_floor": n >= MIN_RESOLVED_BUYS,
        "passes_wr_floor": wr >= MIN_WINDOWED_WR,
        "passes_pnl_floor": total_pnl >= MIN_WINDOWED_PNL,
    }


def main():
    with open(WATCHLIST, encoding="utf-8") as f:
        wl = json.load(f)

    targets = {
        "Runaround":   "0xc0ff6a9ac424210cf218fda5c5753324c34a9953",
        "weflyhigh":   "0x03e8a544e97eeff5753bc1e90d46e5ef22af1697",
        "surfandturf": "0x9f2fe025f84839ca81dd8e0338892605702d2ca8",
        "Mosley1":     "0x5bec79df9add70a3892041ab1a5516b60f53b215",
    }

    print("=== Test traders — PnL-aggregated ===\n")
    test_results: dict[str, dict] = {}
    for name, wallet in targets.items():
        use_test = name in ("Runaround", "weflyhigh", "surfandturf")
        r = replay_wallet(wallet, user_name=name, use_test_cache=use_test)
        test_results[name] = r
        if "error" in r:
            print(f"  {name:14} ERROR: {r['error']}")
            continue
        # Each floor
        gates = []
        if r["passes_n_floor"]: gates.append("n>=10")
        if r["passes_wr_floor"]: gates.append("WR>=0.62")
        if r["passes_pnl_floor"]: gates.append("PnL>=5k")
        survives_all = r["passes_n_floor"] and r["passes_wr_floor"] and r["passes_pnl_floor"]
        verdict = "SURVIVES" if survives_all else "DROPS"
        print(f"  {name:14} n={r['n']:3d} wr={r['wr']:.4f} pnl=${r['realized_pnl_usdc']:>10,.0f} | passes: {','.join(gates) or 'none':25s} | {verdict}")

    print("\n=== Cohort sweep (329 wallets) ===")
    cohort = []
    for entry in wl:
        wallet = entry.get("proxy_wallet", "")
        if not wallet:
            continue
        r = replay_wallet(wallet, user_name=entry.get("user_name", ""))
        cohort.append(r)
    print(f"  {len(cohort)} wallets replayed")

    # Aggregate counts
    pass_n = sum(1 for r in cohort if r.get("passes_n_floor"))
    pass_wr = sum(1 for r in cohort if r.get("passes_n_floor") and r.get("passes_wr_floor"))
    pass_all = sum(1 for r in cohort if r.get("passes_n_floor") and r.get("passes_wr_floor") and r.get("passes_pnl_floor"))
    clean_nonprov = sum(1 for r in cohort if r.get("n", 0) >= PROVISIONAL_THRESHOLD and r.get("passes_wr_floor") and r.get("passes_pnl_floor"))
    print(f"  passes n>=10:                                 {pass_n}")
    print(f"  passes n>=10 + WR>=0.62:                      {pass_wr}")
    print(f"  passes ALL floors (n + WR + PnL):             {pass_all}")
    print(f"  + non-provisional (n>=50):                    {clean_nonprov}")

    # Compare to plan + earlier run
    print(f"\n=== Comparison ===")
    print(f"  Plan empirics (n>=10 + WR>=0.62, no PnL filter):                       171")
    print(f"  Plan empirics non-provisional (n>=50 + WR>=0.62, no PnL filter):       97")
    print(f"  Previous prod run (n>=10 + WR>=0.62 + $5k PnL, NO aggregation):        53  <-- bug")
    print(f"  THIS replay (n>=10 + WR>=0.62 + $5k PnL, WITH aggregation):            {pass_all}")
    print(f"  Expected band:                                                          97-172")

    verdict = "PASS" if 90 <= pass_all <= 180 else "OUT-OF-BAND"
    print(f"\n  Verdict: {verdict}")

    # Sample the dropped-then-restored whales — those that NEW passes but OLD failed
    # We don't have direct linkage to the old run, but we can sample a few high-PnL whales
    print(f"\n=== Top 10 by aggregated PnL ===")
    survived = [r for r in cohort if r.get("passes_n_floor") and r.get("passes_wr_floor") and r.get("passes_pnl_floor")]
    top = sorted(survived, key=lambda r: -r["realized_pnl_usdc"])[:10]
    for r in top:
        print(f"  {r['user_name'][:18]:18} n={r['n']:3d} wr={r['wr']:.4f} pnl=${r['realized_pnl_usdc']:>11,.0f}{'  PROV' if r['provisional'] else ''}")

    out = {
        "test_traders": test_results,
        "cohort_summary": {
            "total": len(cohort),
            "pass_n_floor": pass_n,
            "pass_n_AND_wr": pass_wr,
            "pass_ALL_floors": pass_all,
            "non_prov_pass_wr_AND_pnl": clean_nonprov,
        },
        "cohort_detail": cohort,
        "verdict": verdict,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"\nWritten: {OUT}")


if __name__ == "__main__":
    main()
