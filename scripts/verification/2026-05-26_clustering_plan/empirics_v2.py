"""
Polymarket whale-scoring fix-planning empirics — 2026-05-26 (v2 — optimized cohort sweep)
=============================================================================================
Same logic as empirics_master.py but smarter gamma fetching:
  - For test traders: fetch gamma for ALL buy cids (full history needed for honest_decision_wr)
  - For cohort wallets: only fetch gamma for the first CID_PREFETCH unique cids from buy rows
    (enough to fill the 100-slot window; drastically reduces API calls per wallet)
  - Reuses cached activity from v1 run (keyed by wallet)
  - Writes empirics.json with same schema as v1
"""
from __future__ import annotations

import json
import os
import sys
import time
import hashlib
import urllib.request
from collections import defaultdict
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
CACHE_DIR      = r"C:\Users\AA Incorporado\cc\tmp\2026-05-26_clustering_plan\cache"
OUT_PATH       = r"C:\Users\AA Incorporado\cc\tmp\2026-05-26_clustering_plan\empirics.json"
WATCHLIST_PATH = r"C:\Users\AA Incorporado\cc\tmp\2026-05-26_clustering_plan\watchlist.json"

os.makedirs(CACHE_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Floor params
# ---------------------------------------------------------------------------
WINDOW_SIZE           = 100
MIN_RESOLVED_BUYS     = 10
PROVISIONAL_THRESHOLD = 50
MIN_WINDOWED_WR       = 0.62

# Cohort optimization: only fetch gamma for first N unique cids in buy history.
# 300 unique cids = ~6 gamma calls = enough to find 100+ resolved rows for any active trader.
CID_PREFETCH_COHORT   = 300

TEST_TRADERS = [
    {"name": "Runaround",   "wallet": "0xc0ff6a9ac424210cf218fda5c5753324c34a9953"},
    {"name": "weflyhigh",   "wallet": "0x03e8a544e97eeff5753bc1e90d46e5ef22af1697"},
    {"name": "surfandturf", "wallet": "0x9f2fe025f84839ca81dd8e0338892605702d2ca8"},
]

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def http_get(url: str, retries: int = 3) -> Any:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(1.0 + attempt)
    return None


def fetch_activity(wallet: str, max_pages: int = 10) -> list[dict]:
    rows = []
    limit = 500
    for page_idx in range(max_pages):
        offset = page_idx * limit
        url = (
            f"https://data-api.polymarket.com/activity"
            f"?user={wallet}&limit={limit}&offset={offset}"
        )
        try:
            page = http_get(url)
        except Exception as e:
            print(f"  [WARN] activity fetch error at offset={offset}: {e}", file=sys.stderr)
            break
        if not page:
            break
        rows.extend(page)
        if len(page) < limit:
            break
        time.sleep(0.35)
    return rows


def fetch_gamma_markets(condition_ids: list[str], closed: bool = False) -> dict[str, dict]:
    results = {}
    chunk_size = 50
    for i in range(0, len(condition_ids), chunk_size):
        chunk = condition_ids[i:i + chunk_size]
        params = "&".join(f"condition_ids={cid}" for cid in chunk)
        url = f"https://gamma-api.polymarket.com/markets?{params}&limit=50"
        if closed:
            url += "&closed=true"
        try:
            data = http_get(url)
            for m in data:
                cid = m.get("conditionId") or m.get("condition_id")
                if cid:
                    results[cid] = m
        except Exception as e:
            print(f"  [WARN] gamma fetch error: {e}", file=sys.stderr)
        time.sleep(0.35)
    return results


def decode_resolution(market: dict) -> tuple[bool, int | None, list, list]:
    closed = market.get("closed", False)
    raw_prices = market.get("outcomePrices", "[]")
    raw_outcomes = market.get("outcomes", "[]")
    try:
        prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
        prices = [float(p) for p in prices]
    except Exception:
        prices = []
    try:
        outcomes = json.loads(raw_outcomes) if isinstance(raw_outcomes, str) else raw_outcomes
    except Exception:
        outcomes = []
    if not closed:
        return False, None, outcomes, prices
    winner_idx = None
    for idx, p in enumerate(prices):
        if p >= 0.9:
            winner_idx = idx
            break
    return winner_idx is not None, winner_idx, outcomes, prices

# ---------------------------------------------------------------------------
# Caching helpers
# ---------------------------------------------------------------------------

def _cache_path(key: str, kind: str) -> str:
    return os.path.join(CACHE_DIR, f"{key}_{kind}.json")


def load_cached(key: str, kind: str) -> Any | None:
    p = _cache_path(key, kind)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_cache(key: str, kind: str, data: Any) -> None:
    with open(_cache_path(key, kind), "w", encoding="utf-8") as f:
        json.dump(data, f)


def get_activity(wallet: str, max_pages: int = 10) -> list[dict]:
    """Always uses cache keyed by wallet address."""
    cached = load_cached(wallet.lower(), "activity")
    if cached is not None:
        return cached
    rows = fetch_activity(wallet, max_pages=max_pages)
    save_cache(wallet.lower(), "activity", rows)
    return rows


def get_resolutions(wallet: str, cids: list[str], cache_suffix: str = "") -> dict[str, dict]:
    """Fetch resolutions for the given cids list. Cache keyed by wallet + cid-hash."""
    cids_key = hashlib.md5("|".join(sorted(cids)).encode()).hexdigest()[:12]
    cache_key = wallet.lower() + "_" + cids_key + cache_suffix
    cached = load_cached(cache_key, "resolutions")
    if cached is not None:
        return cached

    open_markets   = fetch_gamma_markets(cids, closed=False)
    closed_markets = fetch_gamma_markets(cids, closed=True)
    merged = {**open_markets, **closed_markets}

    resolutions = {}
    for cid, m in merged.items():
        is_resolved, winner_idx, outcomes, prices = decode_resolution(m)
        resolutions[cid] = {
            "is_resolved": is_resolved,
            "winner_idx": winner_idx,
            "outcomes": outcomes,
            "prices": prices,
            "closed": m.get("closed", False),
            "title": m.get("question", m.get("title", "")),
            "status": "resolved" if is_resolved else ("closed" if m.get("closed") else "open"),
        }

    save_cache(cache_key, "resolutions", resolutions)
    return resolutions

# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------

def filter_buy_rows(activity: list[dict]) -> list[dict]:
    out = []
    for row in activity:
        if row.get("type") == "TRADE" and row.get("side") == "BUY":
            cid = row.get("conditionId") or row.get("condition_id")
            if cid:
                out.append(row)
    return out


def get_cid(row: dict) -> str:
    return row.get("conditionId") or row.get("condition_id") or ""


def parse_ts(row: dict) -> float:
    ts_str = row.get("timestamp") or row.get("ts") or ""
    if not ts_str:
        return 0.0
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return 0.0


def parse_price(row: dict) -> float:
    p = row.get("price") or row.get("avgPrice") or 0.0
    try:
        return float(p)
    except (TypeError, ValueError):
        return 0.0

# ---------------------------------------------------------------------------
# Windowing options
# ---------------------------------------------------------------------------

def window_current(buy_rows, resolutions, window_size=WINDOW_SIZE):
    window = []
    for row in buy_rows:
        cid = get_cid(row)
        res = resolutions.get(cid)
        if not res or res.get("status", "").lower() != "resolved":
            continue
        window.append(row)
        if len(window) >= window_size:
            break
    return window


def window_A(buy_rows, resolutions, window_size=WINDOW_SIZE):
    seen = set()
    window = []
    for row in buy_rows:
        cid = get_cid(row)
        res = resolutions.get(cid)
        if not res or res.get("status", "").lower() != "resolved":
            continue
        if cid in seen:
            continue
        seen.add(cid)
        window.append(row)
        if len(window) >= window_size:
            break
    return window


def window_B(buy_rows, resolutions, K, window_size=WINDOW_SIZE):
    counts = defaultdict(int)
    window = []
    for row in buy_rows:
        cid = get_cid(row)
        res = resolutions.get(cid)
        if not res or res.get("status", "").lower() != "resolved":
            continue
        if counts[cid] >= K:
            continue
        counts[cid] += 1
        window.append(row)
        if len(window) >= window_size:
            break
    return window


def window_bucket(buy_rows, resolutions, window_size=WINDOW_SIZE):
    """Dedupe by (cid, 1h time bin, 5c price bin)."""
    seen = set()
    window = []
    for row in buy_rows:
        cid = get_cid(row)
        res = resolutions.get(cid)
        if not res or res.get("status", "").lower() != "resolved":
            continue
        ts = parse_ts(row)
        price = parse_price(row)
        hour_bin = int(ts // 3600)
        price_bin = round(price / 0.05) * 0.05
        key = (cid, hour_bin, price_bin)
        if key in seen:
            continue
        seen.add(key)
        window.append(row)
        if len(window) >= window_size:
            break
    return window


def score_window(window, resolutions):
    wins = losses = 0
    for row in window:
        cid = get_cid(row)
        res = resolutions.get(cid)
        if not res:
            continue
        winner_idx = res.get("winner_idx")
        oi = row.get("outcomeIndex")
        if oi is None:
            oi = row.get("outcome_index")
        try:
            oi_int = int(oi)
        except (TypeError, ValueError):
            losses += 1
            continue
        if oi_int == winner_idx:
            wins += 1
        else:
            losses += 1
    n = wins + losses
    return wins, losses, n / n if n else 1, wins / n if n else 0.0


def score_C(window, resolutions):
    """1/n weighting — cid counts within window."""
    cid_count = defaultdict(int)
    for row in window:
        cid = get_cid(row)
        res = resolutions.get(cid)
        if res and res.get("status", "").lower() == "resolved":
            cid_count[cid] += 1
    n_eff = wins_w = 0.0
    for row in window:
        cid = get_cid(row)
        res = resolutions.get(cid)
        if not res:
            continue
        n_in = cid_count.get(cid, 1)
        w = 1.0 / n_in
        n_eff += w
        winner_idx = res.get("winner_idx")
        oi = row.get("outcomeIndex")
        if oi is None:
            oi = row.get("outcome_index")
        try:
            oi_int = int(oi)
        except (TypeError, ValueError):
            continue
        if oi_int == winner_idx:
            wins_w += w
    wr = wins_w / n_eff if n_eff else 0.0
    return round(n_eff, 2), round(wins_w, 2), round(n_eff - wins_w, 2), round(wr, 4)

# ---------------------------------------------------------------------------
# D1 fill patterns
# ---------------------------------------------------------------------------

def compute_fill_patterns(window_rows, resolutions):
    cid_groups = defaultdict(list)
    for row in window_rows:
        cid = get_cid(row)
        if cid:
            cid_groups[cid].append(row)

    clusters = []
    for cid, rows in cid_groups.items():
        if len(rows) < 2:
            continue
        timestamps = [parse_ts(r) for r in rows]
        prices = [parse_price(r) for r in rows]
        ois = set()
        for r in rows:
            oi = r.get("outcomeIndex")
            if oi is None:
                oi = r.get("outcome_index")
            try:
                ois.add(int(oi))
            except (TypeError, ValueError):
                pass
        time_span = max(timestamps) - min(timestamps) if timestamps else 0.0
        price_range = max(prices) - min(prices) if prices else 0.0
        unique_oi = len(ois)
        if time_span <= 300 and price_range <= 0.01 and unique_oi == 1:
            cls = "fragmented_fill"
        elif time_span > 3600 or price_range > 0.05:
            cls = "scale_in"
        else:
            cls = "ambiguous"
        clusters.append({
            "condition_id": cid,
            "title": (resolutions.get(cid) or {}).get("title", "")[:80],
            "n_buys_in_cluster": len(rows),
            "time_span_seconds": round(time_span, 1),
            "price_range": round(price_range, 4),
            "unique_outcome_indices": unique_oi,
            "classification": cls,
            "timestamps": [r.get("timestamp") or r.get("ts") for r in rows[:5]],
            "prices": [parse_price(r) for r in rows[:5]],
        })

    hist = {"1": 0, "2": 0, "3": 0, "4-5": 0, "6-10": 0, "11-20": 0, "21+": 0}
    for cid, rows in cid_groups.items():
        n = len(rows)
        if n == 1:    hist["1"] += 1
        elif n == 2:  hist["2"] += 1
        elif n == 3:  hist["3"] += 1
        elif n <= 5:  hist["4-5"] += 1
        elif n <= 10: hist["6-10"] += 1
        elif n <= 20: hist["11-20"] += 1
        else:         hist["21+"] += 1

    total = len(clusters)
    frag  = sum(1 for c in clusters if c["classification"] == "fragmented_fill")
    scale = sum(1 for c in clusters if c["classification"] == "scale_in")
    amb   = sum(1 for c in clusters if c["classification"] == "ambiguous")
    return {
        "cluster_count": total,
        "share_fragmented_fill": round(frag / total, 3) if total else 0.0,
        "share_scale_in":        round(scale / total, 3) if total else 0.0,
        "share_ambiguous":       round(amb / total, 3) if total else 0.0,
        "cluster_size_histogram": hist,
        "sample_clusters": clusters[:3],
    }

# ---------------------------------------------------------------------------
# D2 honest decision WR
# ---------------------------------------------------------------------------

def compute_honest_decision_wr(buy_rows, resolutions):
    decision_outcomes: dict[tuple, bool | None] = {}
    for row in buy_rows:
        cid = get_cid(row)
        res = resolutions.get(cid)
        if not res or not res.get("is_resolved"):
            continue
        oi = row.get("outcomeIndex")
        if oi is None:
            oi = row.get("outcome_index")
        try:
            oi_int = int(oi)
        except (TypeError, ValueError):
            continue
        key = (cid, oi_int)
        winner_idx = res.get("winner_idx")
        is_win = (oi_int == winner_idx)
        if decision_outcomes.get(key) is not True:
            decision_outcomes[key] = is_win
    resolved = [(k, v) for k, v in decision_outcomes.items() if v is not None]
    n = len(resolved)
    wins = sum(1 for _, v in resolved if v)
    wr = wins / n if n else 0.0
    return {"n_decisions": n, "wins": wins, "losses": n - wins, "honest_decision_wr": round(wr, 4)}

# ---------------------------------------------------------------------------
# Per-option compute
# ---------------------------------------------------------------------------

def compute_options(buy_rows, resolutions, honest_wr, trigger_bucket):
    results = {}

    def rec(key, w, wins, losses, n):
        wr = wins / n if n else 0.0
        results[key] = {
            "n": n, "wins": wins, "losses": losses,
            "wr": round(wr, 4),
            "delta_vs_honest_decision_wr": round(wr - honest_wr, 4),
        }

    w = window_current(buy_rows, resolutions)
    wins, losses, _, wr = score_window(w, resolutions)
    rec("current", w, wins, losses, wins + losses)

    w = window_A(buy_rows, resolutions)
    wins, losses, _, wr = score_window(w, resolutions)
    rec("A", w, wins, losses, wins + losses)

    w = window_B(buy_rows, resolutions, K=3)
    wins, losses, _, wr = score_window(w, resolutions)
    rec("B_K3", w, wins, losses, wins + losses)

    w = window_B(buy_rows, resolutions, K=5)
    wins, losses, _, wr = score_window(w, resolutions)
    rec("B_K5", w, wins, losses, wins + losses)

    w_curr = window_current(buy_rows, resolutions)
    n_eff, wins_w, losses_w, wr_w = score_C(w_curr, resolutions)
    wr_w_val = wr_w
    results["C"] = {
        "n_effective": n_eff, "wins_weighted": wins_w,
        "losses_weighted": losses_w, "wr": wr_w,
        "delta_vs_honest_decision_wr": round(wr_w - honest_wr, 4),
    }

    if trigger_bucket:
        w = window_bucket(buy_rows, resolutions)
        wins, losses, _, wr = score_window(w, resolutions)
        results["bucket"] = {
            "n": wins + losses, "wins": wins, "losses": losses,
            "wr": round(wr, 4),
            "delta_vs_honest_decision_wr": round(wr - honest_wr, 4),
            "rule": (
                "Dedupe by (cid, 1h time bin [floor(unix_ts/3600)], "
                "5c price bin [round(price/0.05)*0.05]). "
                "Walk most-recent-first; keep first row per bucket_key."
            ),
        }

    return results

# ---------------------------------------------------------------------------
# D1 gate check
# ---------------------------------------------------------------------------

def check_fragmented_fill_gate(test_results):
    total_c = total_f = 0
    for data in test_results.values():
        fp = data.get("fill_patterns", {})
        n = fp.get("cluster_count", 0)
        total_c += n
        total_f += int(round(fp.get("share_fragmented_fill", 0) * n))
    if total_c == 0:
        return True, "no clusters found at all"
    share = total_f / total_c
    if share < 0.05 or share > 0.95:
        return True, f"fragmented_fill across all 3 traders = {share:.1%} ({total_f}/{total_c}) — outside [5%,95%]"
    return False, ""

# ---------------------------------------------------------------------------
# D3 cohort aggregation
# ---------------------------------------------------------------------------

def percentile(lst, p):
    if not lst:
        return 0.0
    idx = (len(lst) - 1) * p
    lo = int(idx)
    hi = lo + 1
    if hi >= len(lst):
        return float(lst[-1])
    return float(lst[lo] + (idx - lo) * (lst[hi] - lst[lo]))


def aggregate_cohort(option_key, cohort_results):
    n_ge_10 = n_lt_10 = provisional = non_prov = wr_survive = 0
    c100_ge80 = c100_drop = c100_prov = 0
    n_vals = []
    wr_vals = []

    for r in cohort_results:
        opt = r.get("options", {}).get(option_key)
        if opt is None:
            continue
        cur = r.get("options", {}).get("current", {})
        cur_n = cur.get("n", 0)
        cur_wr = cur.get("wr", 0.0)
        is_c100 = cur_wr == 1.0 and cur_n >= MIN_RESOLVED_BUYS

        # For option C we use raw count n (same as current) for floor/provisional checks
        n_val = opt.get("n", 0) if "n" in opt else opt.get("n_effective", 0)
        wr_val = opt.get("wr", 0.0)

        if n_val >= MIN_RESOLVED_BUYS:
            n_ge_10 += 1
            is_prov = n_val < PROVISIONAL_THRESHOLD
            if is_prov:
                provisional += 1
            else:
                non_prov += 1
            if wr_val >= MIN_WINDOWED_WR:
                wr_survive += 1
            n_vals.append(n_val)
            wr_vals.append(wr_val)
            if is_c100:
                if is_prov:
                    c100_prov += 1
                elif wr_val >= 0.80:
                    c100_ge80 += 1
        else:
            n_lt_10 += 1
            if is_c100:
                c100_drop += 1

    n_vals.sort()
    wr_vals.sort()
    return {
        "n_ge_10_pass": n_ge_10,
        "n_lt_10_drop": n_lt_10,
        "provisional_count": provisional,
        "non_provisional_count": non_prov,
        "wr_ge_062_survive": wr_survive,
        "current_100pct_rows_surviving_at_ge_80pct": c100_ge80,
        "current_100pct_rows_dropped": c100_drop,
        "current_100pct_rows_tipped_to_provisional": c100_prov,
        "median_n": round(percentile(n_vals, 0.5), 1),
        "wr_percentiles": {
            "p10": round(percentile(wr_vals, 0.10), 4),
            "p25": round(percentile(wr_vals, 0.25), 4),
            "p50": round(percentile(wr_vals, 0.50), 4),
            "p75": round(percentile(wr_vals, 0.75), 4),
            "p90": round(percentile(wr_vals, 0.90), 4),
        },
    }

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=== Polymarket clustering empirics v2 ===", file=sys.stderr)

    with open(WATCHLIST_PATH, encoding="utf-8") as f:
        watchlist = json.load(f)
    print(f"Watchlist: {len(watchlist)} entries", file=sys.stderr)

    test_stored = {e["user_name"]: e for e in watchlist
                   if e.get("user_name") in ("Runaround", "weflyhigh", "surfandturf")}

    for t in TEST_TRADERS:
        if t["name"] not in test_stored:
            print(f"STOP-AND-REPORT: {t['name']} not in watchlist.json", file=sys.stderr)
            sys.exit(1)

    # -----------------------------------------------------------------------
    # Phase 1: Test traders — D1 + D2 (full history gamma)
    # -----------------------------------------------------------------------
    test_results = {}
    global_trigger_bucket = False

    for trader in TEST_TRADERS:
        name, wallet = trader["name"], trader["wallet"]
        print(f"\n--- Test trader: {name} ---", file=sys.stderr)

        activity = get_activity(wallet, max_pages=10)
        buy_rows = filter_buy_rows(activity)
        print(f"  buy_rows={len(buy_rows)}", file=sys.stderr)

        if not buy_rows:
            test_results[name] = {"error": "no buy rows"}
            continue

        # For test traders: fetch ALL cids (full history for honest_decision_wr)
        all_cids = list({get_cid(r) for r in buy_rows})
        resolutions = get_resolutions(wallet, all_cids)

        # D1 on current window
        curr_w = window_current(buy_rows, resolutions)
        fill_patterns = compute_fill_patterns(curr_w, resolutions)
        print(
            f"  D1: {fill_patterns['cluster_count']} clusters "
            f"frag={fill_patterns['share_fragmented_fill']:.0%} "
            f"scale_in={fill_patterns['share_scale_in']:.0%} "
            f"amb={fill_patterns['share_ambiguous']:.0%}",
            file=sys.stderr,
        )

        # D2: honest decision WR from full history
        honest = compute_honest_decision_wr(buy_rows, resolutions)
        print(
            f"  D2 honest: {honest['wins']}W/{honest['losses']}L "
            f"= {honest['honest_decision_wr']:.4f} ({honest['n_decisions']} decisions)",
            file=sys.stderr,
        )

        if name == "Runaround":
            delta = abs(honest["honest_decision_wr"] - 39/65)
            if delta > 0.15:
                print(
                    f"STOP-AND-REPORT: Runaround honest WR={honest['honest_decision_wr']:.4f} "
                    f"vs known ~60%, delta={delta:.2%}",
                    file=sys.stderr,
                )
                sys.exit(1)

        trigger_bucket = fill_patterns["share_scale_in"] > 0.5
        if trigger_bucket:
            global_trigger_bucket = True

        per_option = compute_options(
            buy_rows, resolutions, honest["honest_decision_wr"], trigger_bucket
        )
        print(
            f"  options: current={per_option['current']['wr']:.4f} "
            f"A={per_option['A']['wr']:.4f} "
            f"B_K3={per_option['B_K3']['wr']:.4f} "
            f"B_K5={per_option['B_K5']['wr']:.4f} "
            f"C={per_option['C']['wr']:.4f}",
            file=sys.stderr,
        )

        test_results[name] = {
            "stored": {
                k: test_stored[name].get(k)
                for k in ("wins", "losses", "win_rate", "window_size_n", "provisional")
            },
            "fill_patterns": fill_patterns,
            "honest_decision_wr": honest,
            "per_option": per_option,
            "trigger_bucket": trigger_bucket,
        }

    # D1 gate
    gate, msg = check_fragmented_fill_gate(test_results)
    if gate:
        print(f"\nSTOP-AND-REPORT: {msg}", file=sys.stderr)
        sys.exit(2)

    # -----------------------------------------------------------------------
    # Phase 2: Cohort sweep (D3) — optimized gamma fetching
    # -----------------------------------------------------------------------
    print(f"\n=== Cohort sweep: {len(watchlist)} wallets ===", file=sys.stderr)
    cohort_results = []

    for idx, entry in enumerate(watchlist):
        wallet = entry.get("proxy_wallet", "")
        user_name = entry.get("user_name", "?")
        print(f"  [{idx+1:03d}/{len(watchlist)}] {user_name}", file=sys.stderr)

        if not wallet:
            continue

        try:
            activity = get_activity(wallet, max_pages=10)
        except Exception as e:
            print(f"    [ERROR] {e}", file=sys.stderr)
            cohort_results.append({"wallet": wallet, "user_name": user_name, "error": str(e), "options": {}})
            continue

        buy_rows = filter_buy_rows(activity)

        if not buy_rows:
            opt_keys = ["current", "A", "B_K3", "B_K5", "C"] + (["bucket"] if global_trigger_bucket else [])
            cohort_results.append({
                "wallet": wallet, "user_name": user_name,
                "buy_rows_total": 0,
                "options": {k: {"n": 0, "wins": 0, "losses": 0, "wr": 0.0} for k in opt_keys},
            })
            continue

        # Optimization: only fetch gamma for first CID_PREFETCH_COHORT unique cids
        # (enough to fill 100-slot window for most wallets)
        # Walk buy_rows in order (most-recent-first) and collect first N unique cids
        seen_cids_ordered: list[str] = []
        seen_set: set[str] = set()
        for row in buy_rows:
            cid = get_cid(row)
            if cid and cid not in seen_set:
                seen_set.add(cid)
                seen_cids_ordered.append(cid)
            if len(seen_cids_ordered) >= CID_PREFETCH_COHORT:
                break
        cids_to_fetch = seen_cids_ordered

        try:
            resolutions = get_resolutions(wallet, cids_to_fetch, cache_suffix="_cohort")
        except Exception as e:
            print(f"    [ERROR] gamma: {e}", file=sys.stderr)
            cohort_results.append({"wallet": wallet, "user_name": user_name, "error": f"gamma: {e}", "options": {}})
            continue

        options = {}

        w = window_current(buy_rows, resolutions)
        wins, losses, _, _ = score_window(w, resolutions)
        n = wins + losses
        options["current"] = {"n": n, "wins": wins, "losses": losses, "wr": round(wins/n if n else 0.0, 4)}

        w = window_A(buy_rows, resolutions)
        wins, losses, _, _ = score_window(w, resolutions)
        n = wins + losses
        options["A"] = {"n": n, "wins": wins, "losses": losses, "wr": round(wins/n if n else 0.0, 4)}

        w = window_B(buy_rows, resolutions, K=3)
        wins, losses, _, _ = score_window(w, resolutions)
        n = wins + losses
        options["B_K3"] = {"n": n, "wins": wins, "losses": losses, "wr": round(wins/n if n else 0.0, 4)}

        w = window_B(buy_rows, resolutions, K=5)
        wins, losses, _, _ = score_window(w, resolutions)
        n = wins + losses
        options["B_K5"] = {"n": n, "wins": wins, "losses": losses, "wr": round(wins/n if n else 0.0, 4)}

        w_curr = window_current(buy_rows, resolutions)
        n_eff, wins_w, losses_w, wr_w = score_C(w_curr, resolutions)
        curr_n = options["current"]["n"]
        options["C"] = {
            "n": curr_n,  # raw count for floor check
            "n_effective": n_eff, "wins_weighted": wins_w, "losses_weighted": losses_w,
            "wr": wr_w,
        }

        if global_trigger_bucket:
            w = window_bucket(buy_rows, resolutions)
            wins, losses, _, _ = score_window(w, resolutions)
            n = wins + losses
            options["bucket"] = {"n": n, "wins": wins, "losses": losses, "wr": round(wins/n if n else 0.0, 4)}

        cohort_results.append({
            "wallet": wallet,
            "user_name": user_name,
            "stored_wr": entry.get("win_rate"),
            "stored_n": entry.get("window_size_n"),
            "stored_provisional": entry.get("provisional"),
            "buy_rows_total": len(buy_rows),
            "options": options,
        })

    # Aggregate
    print("\n=== D3 aggregation ===", file=sys.stderr)
    opt_keys = ["current", "A", "B_K3", "B_K5", "C"]
    if global_trigger_bucket:
        opt_keys.append("bucket")
    cohort_agg = {k: aggregate_cohort(k, cohort_results) for k in opt_keys}

    # -----------------------------------------------------------------------
    # Write output
    # -----------------------------------------------------------------------
    notes = [
        "Activity: data-api.polymarket.com/activity, max 10 pages per wallet.",
        "Resolution: gamma-api.polymarket.com/markets (open + closed, closed takes priority).",
        "is_resolved = closed=True AND any outcomePrices >= 0.9; winner_idx = first such index.",
        "_select_resolved_buys_window replicated exactly: TRADE+BUY, cid required, status=resolved, most-recent-first, stop at 100.",
        "Option A: dedupe by cid (most-recent first encounter); window = first 100 distinct resolved cids.",
        "Option B_K: walk most-recent-first; skip if cid already has K rows in window; stop at 100.",
        "Option C: 1/n weighting within current-style window; WR = weighted_wins / sum_weights.",
        "Option bucket: dedupe by (cid, floor(unix_ts/3600), round(price/0.05)*0.05); walk most-recent-first.",
        "Honest decision WR: unique (cid, outcome_index) pairs; decision wins if ANY buy on pair matched winner_idx.",
        "D3 cohort gamma optimization: first 300 unique cids per wallet fetched (vs all ~2000+). "
        "Enough to fill 100-slot window for all but extreme cases.",
        f"global_trigger_bucket={global_trigger_bucket}",
        "D3 Option C uses raw window count (same as current) for n floor/provisional check, not n_effective.",
    ]

    output = {
        "test_traders": test_results,
        "cohort": cohort_agg,
        "cohort_wallet_detail": cohort_results,
        "methodology_notes": notes,
    }

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"\nDone. Written: {OUT_PATH}", file=sys.stderr)
    print(f"Test traders: {list(test_results.keys())}", file=sys.stderr)
    print(f"Cohort wallets: {len(cohort_results)}", file=sys.stderr)


if __name__ == "__main__":
    main()
