"""Signal-quality EDA for the BTC scalping research DB.

For each Cypher / Market-Cypher signal column on 1D and 4h timeframes,
compute the forward-return distribution at multiple horizons. Output is
a single ranked table per (timeframe, side) so we can quickly see which
signals carry real predictive edge vs which are decoration.

Skips Otter columns per memory `trading_corp_otter_tuned_for_3m`
(Otter is calibrated for 3m; its 1D/4h signal columns are near-empty
by design).

Usage:
    python scripts/eda_btc_scalping_signals.py [--db PATH] [--horizons 1,5,20]

A signal "fires" when its column is non-null and non-zero on a bar.
For each fire, we compute forward N-bar return:
    fwd_ret_N = (close[t+N] - close[t]) / close[t]

Hit rate is the % of fires where the forward return moves IN THE
SIGNAL'S DIRECTION (positive for bull signals, negative for bear).
Mean return is signed in the signal's direction (so a bear signal
with mean fwd return -2% is reported as +2.0% favorable mean).

Expectancy = (hit_rate as 0-1) * mean_favorable - (1 - hit_rate) * mean_unfavorable.
We rank by signed expectancy * sqrt(n) — gives weight to signals with
larger sample size, since a 70% hit rate on n=3 is meaningless.
"""
from __future__ import annotations

import argparse
import math
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "btc_scalping.db"

# Cypher / Vumanchu signal columns. Side annotation lets us compute
# hit rate in the correct direction. "neutral" = direction-ambiguous;
# we evaluate both directions and pick the better.
SIGNAL_CATALOG: dict[str, str] = {
    # Stack-based bias signals
    "bull_candle":              "bull",   # marker, may not be a signal
    "red_diamond":              "bear",
    "blood_diamond":            "bear",
    "blue_triangle":            "bull",
    "red_cross":                "bear",
    "yellow_cross":             "bull",
    # Circles
    "buy_circle":               "bull",
    "sell_circle":              "bear",
    "divergence_buy_circle":    "bull",
    "divergence_sell_circle":   "bear",
    "gold_buy_gold_circle":     "bull",
    # WT divergences (Vumanchu primary)
    "wt_bullish_divergence":    "bull",
    "wt_bearish_divergence":    "bear",
    "wt_2nd_bullish_divergence": "bull",
    "wt_2nd_bearish_divergence": "bear",
    # RSI / Stoch divergences
    "rsi_bullish_divergence":   "bull",
    "rsi_bearish_divergence":   "bear",
    "stoch_bullish_divergence": "bull",
    "stoch_bearish_divergence": "bear",
    # Generic divergence (separate indicator, possibly LazyBear)
    "bull_divergence":          "bull",
    "bear_divergence":          "bear",
    # CVD flips
    "cvd_flip_bullish":         "bull",
    "cvd_flip_bearish":         "bear",
}

# OTTER columns — explicitly excluded per memory. Listed here so future
# additions to SIGNAL_CATALOG don't accidentally include them on 1D/4h.
EXCLUDED_OTTER_ON_HIGH_TF = {
    "otter_buy", "otter_sell",
    "ribbon_buy_cross", "ribbon_sell_cross",
    "super_buy_high", "super_sell_high",
    "super_buy_std", "super_sell_std",
    "top_signal", "bottom_signal",
    "long_ema_signal", "short_ema_signal",
    "vpmo_glow", "vpmo", "money_flow_glow", "money_flow_signal",
}


def load_bars(con: sqlite3.Connection, table: str) -> list[tuple[int, float]]:
    """Return list of (ts, close) for the table, ordered by ts."""
    return con.execute(
        f'SELECT ts, close FROM "{table}" WHERE close IS NOT NULL ORDER BY ts'
    ).fetchall()


def signal_fires(con: sqlite3.Connection, table: str, col: str) -> set[int]:
    """Return set of ts where the signal fired (non-null, non-zero)."""
    rows = con.execute(
        f'SELECT ts FROM "{table}" WHERE "{col}" IS NOT NULL AND "{col}" != 0'
    ).fetchall()
    return {r[0] for r in rows}


def compute_signal_stats(
    bars: list[tuple[int, float]],
    fire_ts: set[int],
    side: str,
    horizons: list[int],
) -> dict[int, dict]:
    """For each horizon, compute n / hit_rate / mean_ret / median_ret.

    Returns dict[horizon] = {n, hit_rate, mean_signed_ret, median_signed_ret}.
    Returns are *signed in the signal's direction* — a bear signal with
    -3% mean raw return reports +3% mean signed return.
    """
    sign = +1.0 if side == "bull" else -1.0
    ts_index = {ts: i for i, (ts, _) in enumerate(bars)}
    closes = [c for _, c in bars]

    out: dict[int, dict] = {}
    for h in horizons:
        rets = []
        for ts in fire_ts:
            i = ts_index.get(ts)
            if i is None:
                continue
            j = i + h
            if j >= len(closes):
                continue
            raw = (closes[j] - closes[i]) / closes[i]
            rets.append(sign * raw)
        if not rets:
            out[h] = {"n": 0, "hit_rate": None, "mean": None, "median": None}
            continue
        rets_sorted = sorted(rets)
        n = len(rets)
        hits = sum(1 for r in rets if r > 0)
        mean = sum(rets) / n
        median = rets_sorted[n // 2] if n % 2 == 1 else 0.5 * (rets_sorted[n // 2 - 1] + rets_sorted[n // 2])
        out[h] = {
            "n": n,
            "hit_rate": hits / n,
            "mean": mean,
            "median": median,
        }
    return out


def expectancy_score(stats: dict, primary_horizon: int) -> float:
    """Sample-size-weighted score for ranking. NaN-safe."""
    s = stats.get(primary_horizon)
    if not s or s["n"] == 0 or s["mean"] is None:
        return float("-inf")
    return s["mean"] * math.sqrt(s["n"])


def render_table(rows: list[dict], horizons: list[int], primary_h: int) -> str:
    if not rows:
        return "  (no signals fired)"
    rows.sort(key=lambda r: -r["score"])

    cols_per_h = ["n", "hit", "mean", "med"]
    header = ["signal", "side"]
    for h in horizons:
        header += [f"n_{h}", f"hit_{h}", f"mean_{h}%", f"med_{h}%"]
    header += [f"score_h{primary_h}"]

    width_signal = max(len(r["signal"]) for r in rows + [{"signal": "signal"}])
    width_signal = max(width_signal, 28)

    out = []
    fmt_header = (
        f"  {'signal':<{width_signal}s}  {'side':<5s}  "
        + "  ".join(f"{f'n@{h}':>5s}  {f'hit@{h}':>7s}  {f'mean@{h}%':>9s}  {f'med@{h}%':>9s}" for h in horizons)
        + "    score"
    )
    out.append(fmt_header)
    out.append("  " + "-" * (len(fmt_header) - 2))

    for r in rows:
        line = f"  {r['signal']:<{width_signal}s}  {r['side']:<5s}"
        for h in horizons:
            s = r["stats"][h]
            if s["n"] == 0:
                line += f"  {'-':>5s}  {'-':>7s}  {'-':>9s}  {'-':>9s}"
            else:
                line += (
                    f"  {s['n']:>5d}  "
                    f"{s['hit_rate']*100:>6.1f}%  "
                    f"{s['mean']*100:>+8.3f}%  "
                    f"{s['median']*100:>+8.3f}%"
                )
        line += f"   {r['score']:>+7.3f}"
        out.append(line)
    return "\n".join(out)


def run_eda(db_path: Path, horizons: list[int]) -> None:
    primary_h = horizons[0] if 5 not in horizons else 5

    with sqlite3.connect(db_path) as con:
        for tf, table in [("1D", "bars_1d"), ("4h", "bars_4h")]:
            print()
            print("=" * 78)
            print(f"  TIMEFRAME: {tf}    table: {table}    "
                  f"horizons (bars forward): {horizons}    primary: {primary_h}")
            print("=" * 78)

            bars = load_bars(con, table)
            if not bars:
                print("  (no bars)")
                continue
            print(f"  total bars: {len(bars):,}    "
                  f"window: {bars[0][0]} -> {bars[-1][0]}    "
                  f"price: ${bars[0][1]:,.0f} -> ${bars[-1][1]:,.0f}")

            existing_cols = {
                r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()
            }

            rows = []
            for col, side in SIGNAL_CATALOG.items():
                if col in EXCLUDED_OTTER_ON_HIGH_TF:
                    continue
                if col not in existing_cols:
                    continue
                fires = signal_fires(con, table, col)
                if not fires:
                    continue
                stats = compute_signal_stats(bars, fires, side, horizons)
                score = expectancy_score(stats, primary_h)
                rows.append({
                    "signal": col,
                    "side": side,
                    "stats": stats,
                    "score": score,
                })

            print()
            print(render_table(rows, horizons, primary_h))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--horizons", default="5,20",
                        help="Comma-separated forward-bar horizons (default 5,20)")
    args = parser.parse_args(argv)

    horizons = [int(x.strip()) for x in args.horizons.split(",") if x.strip()]
    run_eda(Path(args.db), horizons)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
