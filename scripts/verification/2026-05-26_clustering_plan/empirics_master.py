"""
Polymarket whale-scoring fix-planning empirics — 2026-05-26
===========================================================
Deliverables:
  D1 — Fill-pattern analysis (cluster classification per test trader)
  D2 — Full-history ground-truth + per-option windowed WR for test traders
  D3 — Cohort impact across all 329 watchlist wallets

Output: C:\\Users\\AA Incorporado\\cc\\tmp\\2026-05-26_clustering_plan\\empirics.json

Read-only — NO production code changes.
"""
from __future__ import annotations

import json
import os
import sys
import time
import math
import hashlib
import urllib.request
import urllib.parse
from collections import defaultdict
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
CACHE_DIR = r"C:\Users\AA Incorporado\cc\tmp\2026-05-26_clustering_plan\cache"
OUT_PATH   = r"C:\Users\AA Incorporado\cc\tmp\2026-05-26_clustering_plan\empirics.json"
WATCHLIST_PATH = r"C:\Users\AA Incorporado\cc\tmp\2026-05-26_clustering_plan\watchlist.json"

os.makedirs(CACHE_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Floor params (immutable per brief)
# ---------------------------------------------------------------------------
WINDOW_SIZE          = 100
MIN_RESOLVED_BUYS    = 10
PROVISIONAL_THRESHOLD = 50
MIN_WINDOWED_WR      = 0.62

# ---------------------------------------------------------------------------
# Test traders
# ---------------------------------------------------------------------------
TEST_TRADERS = [
    {"name": "Runaround",   "wallet": "0xc0ff6a9ac424210cf218fda5c5753324c34a9953"},
    {"name": "weflyhigh",   "wallet": "0x03e8a544e97eeff5753bc1e90d46e5ef22af1697"},
    {"name": "surfandturf", "wallet": "0x9f2fe025f84839ca81dd8e0338892605702d2ca8"},
]

# ---------------------------------------------------------------------------
# HTTP helpers (reused from verify_wr.py)
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
    """Fetch all activity rows for a wallet, up to max_pages * 500 rows."""
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
            print(f"  [WARN] activity fetch failed at offset={offset} for {wallet[:10]}: {e}", file=sys.stderr)
            break
        if not page:
            break
        rows.extend(page)
        if len(page) < limit:
            break
        time.sleep(0.35)
    return rows


def fetch_gamma_markets(condition_ids: list[str], closed: bool = False) -> dict[str, dict]:
    """Fetch markets from gamma API. Returns dict keyed by conditionId."""
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
    """
    Replicate _decode_resolution from seed script / verify_wr.py.
    Returns (is_resolved, winner_idx, outcomes, prices).
    """
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
    is_resolved = winner_idx is not None
    return is_resolved, winner_idx, outcomes, prices


# ---------------------------------------------------------------------------
# Caching helpers
# ---------------------------------------------------------------------------

def _cache_path(wallet: str, kind: str) -> str:
    h = wallet.lower()
    return os.path.join(CACHE_DIR, f"{h}_{kind}.json")


def load_cached(wallet: str, kind: str) -> Any | None:
    p = _cache_path(wallet, kind)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_cache(wallet: str, kind: str, data: Any) -> None:
    p = _cache_path(wallet, kind)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f)


def get_activity(wallet: str, max_pages: int = 10) -> list[dict]:
    cached = load_cached(wallet, "activity")
    if cached is not None:
        return cached
    rows = fetch_activity(wallet, max_pages=max_pages)
    save_cache(wallet, "activity", rows)
    return rows


def get_resolutions(wallet: str, buy_rows: list[dict]) -> dict[str, dict]:
    """Fetch + cache resolutions for all condition_ids in buy_rows.
    Replicates the seed script's pattern: fetch open then closed, merge."""
    cids = list({
        (row.get("conditionId") or row.get("condition_id"))
        for row in buy_rows
        if (row.get("conditionId") or row.get("condition_id"))
    })
    if not cids:
        return {}

    # Cache keyed by a hash of sorted cids to avoid repeated fetches
    cids_key = hashlib.md5("|".join(sorted(cids)).encode()).hexdigest()[:12]
    cached = load_cached(wallet + "_" + cids_key, "resolutions")
    if cached is not None:
        return cached

    open_markets   = fetch_gamma_markets(cids, closed=False)
    closed_markets = fetch_gamma_markets(cids, closed=True)
    merged = {**open_markets, **closed_markets}  # closed takes priority

    # Build resolution dict matching seed script's format
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

    save_cache(wallet + "_" + cids_key, "resolutions", resolutions)
    return resolutions


# ---------------------------------------------------------------------------
# Windowing helpers — replicate _select_resolved_buys_window EXACTLY
# ---------------------------------------------------------------------------

def filter_buy_rows(activity: list[dict]) -> list[dict]:
    """Keep only TRADE+BUY rows with a condition_id, preserving order (most-recent-first)."""
    out = []
    for row in activity:
        t = row.get("type", "")
        s = row.get("side", "")
        cid = row.get("conditionId") or row.get("condition_id")
        if t == "TRADE" and s == "BUY" and cid:
            out.append(row)
    return out


def window_current(buy_rows: list[dict], resolutions: dict, window_size: int = WINDOW_SIZE) -> list[dict]:
    """Bug-replication: each BUY row = one sample (the current prod behavior)."""
    window = []
    for row in buy_rows:
        cid = row.get("conditionId") or row.get("condition_id")
        res = resolutions.get(cid)
        if not res:
            continue
        if (res.get("status") or "").lower() != "resolved":
            continue
        window.append(row)
        if len(window) >= window_size:
            break
    return window


def window_option_A(buy_rows: list[dict], resolutions: dict, window_size: int = WINDOW_SIZE) -> list[dict]:
    """Option A: dedupe by condition_id — keep only the most-recent BUY per cid,
    then take the most-recent window_size distinct cids."""
    seen_cids: set[str] = set()
    window = []
    for row in buy_rows:
        cid = row.get("conditionId") or row.get("condition_id")
        res = resolutions.get(cid)
        if not res:
            continue
        if (res.get("status") or "").lower() != "resolved":
            continue
        if cid in seen_cids:
            continue
        seen_cids.add(cid)
        window.append(row)
        if len(window) >= window_size:
            break
    return window


def window_option_B(buy_rows: list[dict], resolutions: dict, K: int, window_size: int = WINDOW_SIZE) -> list[dict]:
    """Option B_K: walk most-recent-first; skip if already K BUYs from this cid in window."""
    cid_count: dict[str, int] = defaultdict(int)
    window = []
    for row in buy_rows:
        cid = row.get("conditionId") or row.get("condition_id")
        res = resolutions.get(cid)
        if not res:
            continue
        if (res.get("status") or "").lower() != "resolved":
            continue
        if cid_count[cid] >= K:
            continue
        cid_count[cid] += 1
        window.append(row)
        if len(window) >= window_size:
            break
    return window


def score_window(window: list[dict], resolutions: dict) -> tuple[int, int, float]:
    """Score a window of BUY rows → (wins, losses, wr). Uses decode_resolution logic."""
    wins = 0
    losses = 0
    for row in window:
        cid = row.get("conditionId") or row.get("condition_id")
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
    wr = wins / n if n > 0 else 0.0
    return wins, losses, wr


def score_window_C(window: list[dict], resolutions: dict) -> tuple[float, float, float, float]:
    """Option C: 1/n weighting per cid cluster.
    Returns (n_effective, wins_weighted, losses_weighted, wr_weighted)."""
    # Count occurrences per cid in this window first
    cid_count: dict[str, int] = defaultdict(int)
    for row in window:
        cid = row.get("conditionId") or row.get("condition_id")
        res = resolutions.get(cid)
        if not res:
            continue
        if (res.get("status") or "").lower() != "resolved":
            continue
        cid_count[cid] += 1

    n_eff = 0.0
    wins_w = 0.0
    for row in window:
        cid = row.get("conditionId") or row.get("condition_id")
        res = resolutions.get(cid)
        if not res:
            continue
        n_in_window = cid_count.get(cid, 1)
        weight = 1.0 / n_in_window
        n_eff += weight
        winner_idx = res.get("winner_idx")
        oi = row.get("outcomeIndex")
        if oi is None:
            oi = row.get("outcome_index")
        try:
            oi_int = int(oi)
        except (TypeError, ValueError):
            continue
        if oi_int == winner_idx:
            wins_w += weight

    wr_w = wins_w / n_eff if n_eff > 0 else 0.0
    losses_w = n_eff - wins_w
    return n_eff, wins_w, losses_w, wr_w


def window_option_bucket(buy_rows: list[dict], resolutions: dict, window_size: int = WINDOW_SIZE) -> list[dict]:
    """Option bucket: dedupe by (cid, 1-hour time bucket, 5-cent price bucket).
    Rule: for each row, compute bucket_key = (cid, floor(ts_unix/3600), round(price/0.05)*0.05).
    Keep only the first (most-recent) row per bucket_key encountered walking most-recent-first."""
    seen_buckets: set[tuple] = set()
    window = []
    for row in buy_rows:
        cid = row.get("conditionId") or row.get("condition_id")
        res = resolutions.get(cid)
        if not res:
            continue
        if (res.get("status") or "").lower() != "resolved":
            continue
        # Parse timestamp
        ts_str = row.get("timestamp") or row.get("ts") or ""
        try:
            # Polymarket returns ISO strings like "2026-05-01T12:34:56Z"
            from datetime import datetime, timezone
            ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            ts_unix = ts_dt.timestamp()
        except Exception:
            ts_unix = 0.0
        # Parse price
        price_raw = row.get("price") or row.get("avgPrice") or 0.0
        try:
            price_f = float(price_raw)
        except (TypeError, ValueError):
            price_f = 0.0
        hour_bin = int(ts_unix // 3600)
        price_bin = round(price_f / 0.05) * 0.05
        bucket_key = (cid, hour_bin, price_bin)
        if bucket_key in seen_buckets:
            continue
        seen_buckets.add(bucket_key)
        window.append(row)
        if len(window) >= window_size:
            break
    return window


# ---------------------------------------------------------------------------
# D1: Fill-pattern analysis
# ---------------------------------------------------------------------------

def classify_cluster(
    buys: list[dict],
    time_span_seconds: float,
    price_range: float,
    unique_oi: int,
) -> str:
    if time_span_seconds <= 300 and price_range <= 0.01 and unique_oi == 1:
        return "fragmented_fill"
    if time_span_seconds > 3600 or price_range > 0.05:
        return "scale_in"
    return "ambiguous"


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


def compute_fill_patterns(window_rows: list[dict], resolutions: dict) -> dict:
    """Compute D1 fill-pattern analysis for a windowed set of BUY rows."""
    # Group by condition_id
    cid_groups: dict[str, list[dict]] = defaultdict(list)
    for row in window_rows:
        cid = row.get("conditionId") or row.get("condition_id")
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
        unique_oi_count = len(ois)

        classification = classify_cluster(rows, time_span, price_range, unique_oi_count)
        clusters.append({
            "condition_id": cid,
            "title": (resolutions.get(cid) or {}).get("title", "")[:80],
            "n_buys_in_cluster": len(rows),
            "time_span_seconds": round(time_span, 1),
            "price_range": round(price_range, 4),
            "unique_outcome_indices": unique_oi_count,
            "classification": classification,
            "timestamps": [r.get("timestamp") or r.get("ts") for r in rows[:5]],
            "prices": [parse_price(r) for r in rows[:5]],
        })

    if not clusters:
        total = 0
        frag = scale = amb = 0
    else:
        total = len(clusters)
        frag  = sum(1 for c in clusters if c["classification"] == "fragmented_fill")
        scale = sum(1 for c in clusters if c["classification"] == "scale_in")
        amb   = sum(1 for c in clusters if c["classification"] == "ambiguous")

    # Histogram of cluster sizes
    hist_buckets = {"1": 0, "2": 0, "3": 0, "4-5": 0, "6-10": 0, "11-20": 0, "21+": 0}
    for cid, rows in cid_groups.items():
        n = len(rows)
        if n == 1:
            hist_buckets["1"] += 1
        elif n == 2:
            hist_buckets["2"] += 1
        elif n == 3:
            hist_buckets["3"] += 1
        elif n <= 5:
            hist_buckets["4-5"] += 1
        elif n <= 10:
            hist_buckets["6-10"] += 1
        elif n <= 20:
            hist_buckets["11-20"] += 1
        else:
            hist_buckets["21+"] += 1

    return {
        "cluster_count": total,
        "share_fragmented_fill": round(frag / total, 3) if total else 0,
        "share_scale_in": round(scale / total, 3) if total else 0,
        "share_ambiguous": round(amb / total, 3) if total else 0,
        "cluster_size_histogram": hist_buckets,
        "sample_clusters": clusters[:3],
    }


# ---------------------------------------------------------------------------
# D2: Full-history ground-truth + per-option windowed WR
# ---------------------------------------------------------------------------

def compute_honest_decision_wr(
    buy_rows: list[dict],
    resolutions: dict,
) -> dict:
    """
    Walk full history of resolved BUYs.
    Decision = unique (condition_id, outcome_index).
    A decision wins if any BUY on that (cid, oi) matched the winner.
    """
    decision_outcomes: dict[tuple, bool | None] = {}  # (cid, oi) -> won?
    for row in buy_rows:
        cid = row.get("conditionId") or row.get("condition_id")
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
        # A decision wins if ANY buy on it won (set to True if ever True)
        existing = decision_outcomes.get(key)
        if existing is True:
            pass  # already a winner
        else:
            decision_outcomes[key] = is_win

    resolved_decisions = [(k, v) for k, v in decision_outcomes.items() if v is not None]
    n = len(resolved_decisions)
    wins = sum(1 for _, v in resolved_decisions if v)
    losses = n - wins
    wr = wins / n if n > 0 else 0.0
    return {
        "n_decisions": n,
        "wins": wins,
        "losses": losses,
        "honest_decision_wr": round(wr, 4),
    }


def compute_all_options(
    buy_rows: list[dict],
    resolutions: dict,
    honest_wr: float,
    trigger_bucket: bool,
) -> dict:
    """Compute windowed stats under all fix options."""
    results = {}

    # Current (buggy)
    w = window_current(buy_rows, resolutions)
    wins, losses, wr = score_window(w, resolutions)
    results["current"] = {
        "n": len(w),
        "wins": wins,
        "losses": losses,
        "wr": round(wr, 4),
        "delta_vs_honest_decision_wr": round(wr - honest_wr, 4),
    }

    # Option A
    w = window_option_A(buy_rows, resolutions)
    wins, losses, wr = score_window(w, resolutions)
    results["A"] = {
        "n": len(w),
        "wins": wins,
        "losses": losses,
        "wr": round(wr, 4),
        "delta_vs_honest_decision_wr": round(wr - honest_wr, 4),
    }

    # Option B_K3
    w = window_option_B(buy_rows, resolutions, K=3)
    wins, losses, wr = score_window(w, resolutions)
    results["B_K3"] = {
        "n": len(w),
        "wins": wins,
        "losses": losses,
        "wr": round(wr, 4),
        "delta_vs_honest_decision_wr": round(wr - honest_wr, 4),
    }

    # Option B_K5
    w = window_option_B(buy_rows, resolutions, K=5)
    wins, losses, wr = score_window(w, resolutions)
    results["B_K5"] = {
        "n": len(w),
        "wins": wins,
        "losses": losses,
        "wr": round(wr, 4),
        "delta_vs_honest_decision_wr": round(wr - honest_wr, 4),
    }

    # Option C (weighted)
    w = window_current(buy_rows, resolutions)  # same window as current
    n_eff, wins_w, losses_w, wr_w = score_window_C(w, resolutions)
    results["C"] = {
        "n_effective": round(n_eff, 2),
        "wins_weighted": round(wins_w, 2),
        "losses_weighted": round(losses_w, 2),
        "wr": round(wr_w, 4),
        "delta_vs_honest_decision_wr": round(wr_w - honest_wr, 4),
    }

    # Option bucket (only if triggered by D1)
    if trigger_bucket:
        w = window_option_bucket(buy_rows, resolutions)
        wins, losses, wr = score_window(w, resolutions)
        results["bucket"] = {
            "n": len(w),
            "wins": wins,
            "losses": losses,
            "wr": round(wr, 4),
            "delta_vs_honest_decision_wr": round(wr - honest_wr, 4),
            "rule": (
                "Dedupe by (cid, 1-hour time bin [floor(unix_ts/3600)], "
                "5-cent price bin [round(price/0.05)*0.05]). "
                "Walk most-recent-first; keep first row per bucket_key."
            ),
        }

    return results


# ---------------------------------------------------------------------------
# D1 stop-and-report gate check
# ---------------------------------------------------------------------------

def check_fragmented_fill_gate(test_results: dict) -> tuple[bool, str]:
    """
    Gate: if fragmented_fill share is <5% or >95% across all 3 traders combined,
    stop and surface the issue.
    """
    total_clusters = 0
    total_frag = 0
    for trader_name, data in test_results.items():
        fp = data.get("fill_patterns", {})
        n = fp.get("cluster_count", 0)
        frag_share = fp.get("share_fragmented_fill", 0)
        total_clusters += n
        total_frag += int(round(frag_share * n))
    if total_clusters == 0:
        return False, "no clusters found at all — check D1 methodology"
    overall_share = total_frag / total_clusters
    if overall_share < 0.05 or overall_share > 0.95:
        return True, (
            f"fragmented_fill share across all 3 traders = {overall_share:.1%} "
            f"({total_frag}/{total_clusters}) — outside [5%, 95%] gate. "
            f"Check classification thresholds."
        )
    return False, ""


# ---------------------------------------------------------------------------
# D3: Cohort sweep
# ---------------------------------------------------------------------------

def compute_cohort_option_stats(option_key: str, all_results: list[dict]) -> dict:
    """Aggregate cohort metrics for one option."""
    n_ge_10_pass = 0
    n_lt_10_drop = 0
    provisional_count = 0
    non_provisional_count = 0
    wr_ge_062_survive = 0
    current_100pct_rows_surviving_at_ge_80pct = 0
    current_100pct_rows_dropped = 0
    current_100pct_rows_tipped_to_provisional = 0

    n_values = []
    wr_values = []

    for r in all_results:
        opt = r.get("options", {}).get(option_key)
        if opt is None:
            continue
        current_opt = r.get("options", {}).get("current", {})
        is_current_100pct = current_opt.get("wr", 0) == 1.0 and current_opt.get("n", 0) >= MIN_RESOLVED_BUYS

        # Determine n for this option
        n_key = "n" if "n" in opt else "n_effective"
        n_val = opt.get(n_key, 0)
        wr_val = opt.get("wr", 0.0)

        passes_floor = n_val >= MIN_RESOLVED_BUYS
        if passes_floor:
            n_ge_10_pass += 1
            is_prov = n_val < PROVISIONAL_THRESHOLD
            if is_prov:
                provisional_count += 1
            else:
                non_provisional_count += 1
            if wr_val >= MIN_WINDOWED_WR:
                wr_ge_062_survive += 1
            n_values.append(n_val)
            wr_values.append(wr_val)
        else:
            n_lt_10_drop += 1

        # For current 100% rows: track what each option does to them
        if is_current_100pct:
            if not passes_floor:
                current_100pct_rows_dropped += 1
            elif n_val < PROVISIONAL_THRESHOLD:
                current_100pct_rows_tipped_to_provisional += 1
            elif wr_val >= 0.80:
                current_100pct_rows_surviving_at_ge_80pct += 1

    # Compute percentiles
    n_values.sort()
    wr_values.sort()

    def percentile(lst: list, p: float) -> float:
        if not lst:
            return 0.0
        idx = (len(lst) - 1) * p
        lo = int(idx)
        hi = lo + 1
        if hi >= len(lst):
            return float(lst[-1])
        return float(lst[lo] + (idx - lo) * (lst[hi] - lst[lo]))

    return {
        "n_ge_10_pass": n_ge_10_pass,
        "n_lt_10_drop": n_lt_10_drop,
        "provisional_count": provisional_count,
        "non_provisional_count": non_provisional_count,
        "wr_ge_062_survive": wr_ge_062_survive,
        "current_100pct_rows_surviving_at_ge_80pct": current_100pct_rows_surviving_at_ge_80pct,
        "current_100pct_rows_dropped": current_100pct_rows_dropped,
        "current_100pct_rows_tipped_to_provisional": current_100pct_rows_tipped_to_provisional,
        "median_n": round(percentile(n_values, 0.5), 1),
        "wr_percentiles": {
            "p10": round(percentile(wr_values, 0.10), 4),
            "p25": round(percentile(wr_values, 0.25), 4),
            "p50": round(percentile(wr_values, 0.50), 4),
            "p75": round(percentile(wr_values, 0.75), 4),
            "p90": round(percentile(wr_values, 0.90), 4),
        },
    }


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def main():
    print("=== Polymarket clustering empirics 2026-05-26 ===", file=sys.stderr)

    # ------------------------------------------------------------------
    # Phase 1: Load watchlist
    # ------------------------------------------------------------------
    with open(WATCHLIST_PATH, encoding="utf-8") as f:
        watchlist = json.load(f)
    print(f"Loaded watchlist: {len(watchlist)} entries", file=sys.stderr)

    # Determine test trader stored values
    test_trader_stored = {}
    for entry in watchlist:
        n = entry.get("user_name")
        if n in ("Runaround", "weflyhigh", "surfandturf"):
            test_trader_stored[n] = entry

    # Check all 3 found
    for t in TEST_TRADERS:
        if t["name"] not in test_trader_stored:
            print(
                f"STOP-AND-REPORT: {t['name']} not found in watchlist.json",
                file=sys.stderr
            )
            sys.exit(1)

    # ------------------------------------------------------------------
    # Phase 2: Test traders — D1 + D2
    # ------------------------------------------------------------------
    test_results = {}

    for trader in TEST_TRADERS:
        name = trader["name"]
        wallet = trader["wallet"]
        print(f"\n--- Processing test trader: {name} ({wallet[:12]}...) ---", file=sys.stderr)

        # Fetch full activity (max 10 pages = up to 5000 rows)
        activity = get_activity(wallet, max_pages=10)
        print(f"  Activity rows: {len(activity)}", file=sys.stderr)

        buy_rows = filter_buy_rows(activity)
        print(f"  BUY rows: {len(buy_rows)}", file=sys.stderr)

        if not buy_rows:
            print(f"  [WARN] No BUY rows for {name}", file=sys.stderr)
            test_results[name] = {"error": "no buy rows"}
            continue

        # Fetch resolutions
        resolutions = get_resolutions(wallet, buy_rows)
        print(f"  Resolved markets fetched: {sum(1 for r in resolutions.values() if r.get('is_resolved'))}", file=sys.stderr)

        # D1: Fill-pattern analysis on the current (buggy) window
        current_window = window_current(buy_rows, resolutions)
        fill_patterns = compute_fill_patterns(current_window, resolutions)
        print(
            f"  D1: {fill_patterns['cluster_count']} multi-buy clusters in window; "
            f"frag={fill_patterns['share_fragmented_fill']:.0%} "
            f"scale_in={fill_patterns['share_scale_in']:.0%} "
            f"amb={fill_patterns['share_ambiguous']:.0%}",
            file=sys.stderr,
        )

        # D2: Full-history honest_decision_wr
        honest = compute_honest_decision_wr(buy_rows, resolutions)
        print(
            f"  D2 honest: {honest['wins']}W/{honest['losses']}L "
            f"= {honest['honest_decision_wr']:.4f} WR ({honest['n_decisions']} decisions)",
            file=sys.stderr,
        )

        # Runaround sanity check
        if name == "Runaround":
            known_wr_approx = 39 / 65  # ~60%
            delta = abs(honest["honest_decision_wr"] - known_wr_approx)
            if delta > 0.15:
                print(
                    f"STOP-AND-REPORT: Runaround honest WR={honest['honest_decision_wr']:.4f} "
                    f"diverges from known ~60% by {delta:.2%}. "
                    f"Methodology gap suspected.",
                    file=sys.stderr,
                )
                sys.exit(1)

        # D1 gate: trigger bucket option if clusters are predominantly scale_in
        trigger_bucket = fill_patterns["share_scale_in"] > 0.5

        # D2: per-option windowed stats
        per_option = compute_all_options(
            buy_rows, resolutions,
            honest["honest_decision_wr"],
            trigger_bucket=trigger_bucket,
        )
        print(
            f"  Per-option WRs: current={per_option['current']['wr']:.4f} "
            f"A={per_option['A']['wr']:.4f} "
            f"B_K3={per_option['B_K3']['wr']:.4f} "
            f"B_K5={per_option['B_K5']['wr']:.4f} "
            f"C={per_option['C']['wr']:.4f}",
            file=sys.stderr,
        )

        test_results[name] = {
            "stored": {
                "wins": test_trader_stored[name].get("wins"),
                "losses": test_trader_stored[name].get("losses"),
                "win_rate": test_trader_stored[name].get("win_rate"),
                "window_size_n": test_trader_stored[name].get("window_size_n"),
                "provisional": test_trader_stored[name].get("provisional"),
            },
            "fill_patterns": fill_patterns,
            "honest_decision_wr": honest,
            "per_option": per_option,
            "trigger_bucket": trigger_bucket,
        }

    # ------------------------------------------------------------------
    # D1 stop-and-report gate
    # ------------------------------------------------------------------
    gate_triggered, gate_msg = check_fragmented_fill_gate(test_results)
    if gate_triggered:
        print(f"\nSTOP-AND-REPORT: {gate_msg}", file=sys.stderr)
        sys.exit(2)

    # Determine if bucket option applies globally
    global_trigger_bucket = any(
        v.get("trigger_bucket", False) for v in test_results.values()
    )

    # ------------------------------------------------------------------
    # Phase 3: Cohort sweep (D3) — all 329 wallets
    # ------------------------------------------------------------------
    print(f"\n=== Phase 3: Cohort sweep ({len(watchlist)} wallets) ===", file=sys.stderr)
    cohort_wallet_results = []

    for idx, entry in enumerate(watchlist):
        wallet = entry.get("proxy_wallet", "")
        user_name = entry.get("user_name", "?")
        print(
            f"  [{idx+1:03d}/{len(watchlist)}] {user_name} ({wallet[:12]}...)",
            file=sys.stderr,
        )

        if not wallet:
            print(f"    [SKIP] no wallet", file=sys.stderr)
            continue

        try:
            activity = get_activity(wallet, max_pages=10)
        except Exception as e:
            print(f"    [ERROR] activity fetch: {e}", file=sys.stderr)
            cohort_wallet_results.append({
                "wallet": wallet,
                "user_name": user_name,
                "error": str(e),
                "options": {},
            })
            continue

        buy_rows = filter_buy_rows(activity)

        if not buy_rows:
            cohort_wallet_results.append({
                "wallet": wallet,
                "user_name": user_name,
                "buy_rows_total": 0,
                "options": {
                    opt: {"n": 0, "wins": 0, "losses": 0, "wr": 0.0}
                    for opt in (["current", "A", "B_K3", "B_K5", "C"] + (["bucket"] if global_trigger_bucket else []))
                },
            })
            continue

        try:
            resolutions = get_resolutions(wallet, buy_rows)
        except Exception as e:
            print(f"    [ERROR] resolutions fetch: {e}", file=sys.stderr)
            cohort_wallet_results.append({
                "wallet": wallet,
                "user_name": user_name,
                "error": f"resolutions: {e}",
                "options": {},
            })
            continue

        # Compute per-option stats (no honest_decision_wr for cohort — too expensive per wallet)
        options = {}

        w = window_current(buy_rows, resolutions)
        wins, losses, wr = score_window(w, resolutions)
        options["current"] = {"n": len(w), "wins": wins, "losses": losses, "wr": round(wr, 4)}

        w = window_option_A(buy_rows, resolutions)
        wins, losses, wr = score_window(w, resolutions)
        options["A"] = {"n": len(w), "wins": wins, "losses": losses, "wr": round(wr, 4)}

        w = window_option_B(buy_rows, resolutions, K=3)
        wins, losses, wr = score_window(w, resolutions)
        options["B_K3"] = {"n": len(w), "wins": wins, "losses": losses, "wr": round(wr, 4)}

        w = window_option_B(buy_rows, resolutions, K=5)
        wins, losses, wr = score_window(w, resolutions)
        options["B_K5"] = {"n": len(w), "wins": wins, "losses": losses, "wr": round(wr, 4)}

        # Option C uses same window as current
        w_curr = window_current(buy_rows, resolutions)
        n_eff, wins_w, losses_w, wr_w = score_window_C(w_curr, resolutions)
        options["C"] = {
            "n": options["current"]["n"],          # raw count (for floor check)
            "n_effective": round(n_eff, 2),
            "wins_weighted": round(wins_w, 2),
            "losses_weighted": round(losses_w, 2),
            "wr": round(wr_w, 4),
        }

        if global_trigger_bucket:
            w = window_option_bucket(buy_rows, resolutions)
            wins, losses, wr = score_window(w, resolutions)
            options["bucket"] = {"n": len(w), "wins": wins, "losses": losses, "wr": round(wr, 4)}

        cohort_wallet_results.append({
            "wallet": wallet,
            "user_name": user_name,
            "stored_wr": entry.get("win_rate"),
            "stored_n": entry.get("window_size_n"),
            "stored_provisional": entry.get("provisional"),
            "buy_rows_total": len(buy_rows),
            "options": options,
        })

    # ------------------------------------------------------------------
    # D3 aggregates
    # ------------------------------------------------------------------
    print("\n=== Computing D3 aggregates ===", file=sys.stderr)
    option_keys = ["current", "A", "B_K3", "B_K5", "C"]
    if global_trigger_bucket:
        option_keys.append("bucket")

    cohort_agg = {}
    for opt_key in option_keys:
        cohort_agg[opt_key] = compute_cohort_option_stats(opt_key, cohort_wallet_results)

    # ------------------------------------------------------------------
    # Assemble final output
    # ------------------------------------------------------------------
    # Determine methodology_notes
    methodology_notes = [
        "Activity fetched via data-api.polymarket.com/activity (max 10 pages = 5000 rows per wallet).",
        "Resolution status determined via gamma-api.polymarket.com/markets (open + closed fetches merged; closed takes priority for outcomePrices).",
        "is_resolved = closed=True AND any outcomePrices element >= 0.9.",
        "winner_idx = first index where outcomePrices >= 0.9.",
        "_select_resolved_buys_window replicated exactly: TRADE+BUY, condition_id required, status=resolved, most-recent-first, stop at window_size=100.",
        "Option A: dedupe by cid — first occurrence (most-recent) per cid kept; window = first 100 distinct resolved cids.",
        "Option B_K: walk most-recent-first; skip row if cid already has K rows in window; stop at 100.",
        "Option C: 1/n weighting — compute cid counts within the current-style 100-row window; each row weighted 1/count_for_its_cid; WR = weighted_wins / sum_weights.",
        "Option bucket: dedupe by (cid, 1-hour time bin, 5-cent price bin); rule documented in bucket.rule field.",
        "Honest decision WR: unique (cid, outcome_index) pairs from full history; decision wins if ANY BUY on that pair matched winner_idx.",
        "D3 Option C n floor check uses raw window count (same as current), not n_effective, for comparability of floor/provisional gates.",
        "All data cached to disk keyed by wallet; re-runs use cache without re-fetching.",
        f"global_trigger_bucket={global_trigger_bucket} (True if any test trader had >50% scale_in clusters).",
    ]

    output = {
        "test_traders": test_results,
        "cohort": cohort_agg,
        "cohort_wallet_detail": cohort_wallet_results,
        "methodology_notes": methodology_notes,
    }

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"\n=== Done. Output written to {OUT_PATH} ===", file=sys.stderr)
    print(f"Test traders processed: {list(test_results.keys())}", file=sys.stderr)
    print(f"Cohort wallets processed: {len(cohort_wallet_results)}", file=sys.stderr)


if __name__ == "__main__":
    main()
