"""Synthesize a BitUnix-style signal ledger from `data/btc_scalping.db`.

The btc_scalping.db has every TradingView indicator the YAML names as a column
on bars_3m / bars_15m / bars_30m. For each (column, tf) we emit one
AlertEvent(ts, signal_name, tf) per bar where the column is non-null and
non-zero — exactly the shape `bitunix_signal_ledger` rows take post-PR-3c.

Mapping is locked to what `config/strategies.yaml bitunix_futures.scoring.factors`
defines. Columns with no factor equivalent (e.g. wt_*_divergence) are dropped.
Factors with no bar-column equivalent (mc_b_buy_dot / mc_b_sell_dot, pink_box_*)
are inert in replay — matches live behavior since pink_box is image-based and
the small "dot" momentum signal isn't separately exported.

Also exports `load_bars_1m`-style helpers used by the replay harness to walk
forward for SL/TP resolution. We use bars_3m as the resolution stream
(fine-grained enough for 30-min cooldown logic; the prior bitunix backtest used
1m Coinbase OHLCV but those bars aren't in our local DB).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "data" / "btc_scalping.db"


# Bar-column → YAML factor name. Side carried for sanity (matches YAML).
COL_TO_FACTOR: dict[str, tuple[str, str]] = {
    # ── Cypher A-panel ──
    "blood_diamond":          ("mc_a_blood_diamond",  "sell"),
    "red_diamond":            ("mc_a_red_diamond",    "sell"),
    "blue_triangle":          ("mc_a_bluetriangle",   "buy"),
    "red_cross":              ("mc_a_redx",           "sell"),
    "long_ema_signal":        ("mc_a_longema",        "buy"),
    "yellow_cross":           ("mc_a_yellow_x",       "buy"),
    # ── Cypher B-panel ──
    "gold_buy_gold_circle":   ("mc_b_gold_buy",       "buy"),
    "divergence_buy_circle":  ("mc_b_buy_circle_div", "buy"),
    "divergence_sell_circle": ("mc_b_sell_circle_div","sell"),
    "buy_circle":             ("mc_b_buy_circle",     "buy"),
    "sell_circle":            ("mc_b_sell_circle",    "sell"),
    # mc_b_buy_dot / mc_b_sell_dot — no column. Inert in replay (matches live).
    # ── Otter ──
    "otter_buy":              ("otter_buy",           "buy"),
    "otter_sell":             ("otter_sell",          "sell"),
    "top_signal":             ("money_bag_top",       "sell"),
    "bottom_signal":          ("money_bag_bottom",    "buy"),
    "super_buy_high":         ("water_buy_large",     "buy"),
    "super_sell_high":        ("water_sell_large",    "sell"),
    "super_buy_std":          ("water_buy_small",     "buy"),
    "super_sell_std":         ("water_sell_small",    "sell"),
    "bull_divergence":        ("spoon_bull",          "buy"),
    "bear_divergence":        ("spoon_bear",          "sell"),
    "cvd_flip_bullish":       ("cvd_bull_flip",       "buy"),
    "cvd_flip_bearish":       ("cvd_bear_flip",       "sell"),
    "ribbon_buy_cross":       ("bias_bull",           "buy"),
    "ribbon_sell_cross":      ("bias_bear",           "sell"),
    # pink_box_bull / pink_box_bear — image-based, not a TV alert. Inert.
}


@dataclass(frozen=True)
class SynthAlert:
    ts: datetime               # bar close timestamp, tz-aware UTC
    signal_name: str           # YAML factor name (lower-cased)
    tf: str                    # "3m" / "15m" / "30m"


def _is_truthy(v: float | int | None) -> bool:
    return v is not None and v != 0


def load_synth_ledger(
    db_path: Path = DB_PATH,
    tfs: tuple[str, ...] = ("3m", "15m", "30m"),
) -> list[SynthAlert]:
    """Read all bars for each TF, emit one alert per RISING-EDGE fire.

    Bar columns are STATE columns (column is non-zero for every bar the
    indicator's condition holds). TradingView alerts fire once on the bar
    where the condition becomes true — the standard pinescript pattern is
    `alertcondition(barstate.isconfirmed and not cond[1] and cond)`.

    We mirror that here: for each (column, tf), emit a SynthAlert only on
    the bar where the column transitions from falsy (NULL/0) to truthy
    (non-zero). That matches both the live webhook rate observed in
    `data/historical_alerts/cache_alerts_*.json` and the conceptual model
    in `trading_corp_bitunix_phase3_confluence_model.md`.

    Returns alerts sorted chronologically; ts is the bar-CLOSE timestamp
    (when the alert fires in TradingView).
    """
    con = sqlite3.connect(db_path)
    out: list[SynthAlert] = []
    tf_to_secs = {"3m": 180, "15m": 900, "30m": 1800}
    cols = list(COL_TO_FACTOR.keys())
    for tf in tfs:
        bar_secs = tf_to_secs[tf]
        sel = "ts, " + ", ".join(cols)
        rows = con.execute(f"SELECT {sel} FROM bars_{tf} ORDER BY ts ASC").fetchall()
        prev: list[float | int | None] = [None] * len(cols)
        for row in rows:
            bar_ts = row[0]
            close_dt = datetime.fromtimestamp(bar_ts + bar_secs, tz=timezone.utc)
            for i, col in enumerate(cols):
                cur = row[i + 1]
                if _is_truthy(cur) and not _is_truthy(prev[i]):
                    factor_name, _side = COL_TO_FACTOR[col]
                    out.append(SynthAlert(ts=close_dt, signal_name=factor_name, tf=tf))
                prev[i] = cur
    out.sort(key=lambda a: a.ts)
    con.close()
    return out


@dataclass(frozen=True)
class Bar3m:
    ts: int                    # unix-seconds, bar open
    open: float
    high: float
    low: float
    close: float
    atr: float                 # ATR(14) from the bar table


def load_bars_3m_for_resolution(
    db_path: Path = DB_PATH,
) -> list[Bar3m]:
    """Load 3m OHLCV + ATR(14) for walk-forward SL/TP resolution."""
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT ts, open, high, low, close, atr FROM bars_3m ORDER BY ts ASC",
    ).fetchall()
    con.close()
    return [Bar3m(ts=r[0], open=r[1], high=r[2], low=r[3], close=r[4],
                  atr=(r[5] or 0.0)) for r in rows]


if __name__ == "__main__":
    alerts = load_synth_ledger()
    print(f"synthesized {len(alerts)} alerts")
    print(f"first: {alerts[0]}")
    print(f"last:  {alerts[-1]}")
    from collections import Counter
    by_tf = Counter(a.tf for a in alerts)
    print(f"by tf: {dict(by_tf)}")
    by_sig_tf = Counter((a.signal_name, a.tf) for a in alerts)
    print()
    print(f"top 20 (signal, tf):")
    for (s, tf), n in by_sig_tf.most_common(20):
        print(f"  {s:30s} {tf:5s} {n}")
