"""3m trigger-event analysis for the BitUnix Phase 3 strategy.

For each Otter trigger event on the 3m timeframe, computes:

  1. Wick stats — Max Adverse Excursion (MAE) over next 1, 5, 10 bars,
     directly addressing the "signal fires but price drops first before
     taking off" concern.
  2. Stop survival — with our proposed structural stop
     (max(1.5×ATR, 0.3%×price)), does the trade live?
  3. Forward returns — close-to-close return at 5, 10, 20 bars in the
     signal's direction.
  4. Confluence + bias context — did 3m volume agree (CVD direction)?
     What was the 4h bias state at trigger time? 1D bias state?
  5. Tier classification — PREMIUM / STANDARD / WEAK / COUNTER / SKIP
     per the locked design (memory `trading_corp_bitunix_phase3_confluence_model`).
  6. Per-tier forward-return breakdown — does the tier ranking actually
     predict performance? Tiny-n today, but baseline grows weekly.

Re-runnable: each weekly data drop refreshes the numbers and shifts the
sample size upward. With 6 days of data the absolute conclusions are
noise; the SHAPE of the results (e.g., "WEAK has higher MAE than
PREMIUM" or vice versa) is the early signal.

Usage:
    python scripts/analyze_btc_scalping_3m.py [--db PATH]
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import statistics
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "btc_scalping.db"
HISTORY_CSV = REPO_ROOT / "data" / "scalping_3m_analysis_history.csv"

# Otter rare/decisive triggers on 3m. Ribbon crosses (`ribbon_buy_cross`,
# `ribbon_sell_cross`) are intentionally EXCLUDED — they fire ~50/day on
# 3m as price oscillates around the EMA stack and are too noisy to use
# as discrete entry signals. Reserved as a potential GATE filter.
# Per memory `trading_corp_otter_tuned_for_3m`, Otter is calibrated for 3m.
OTTER_TRIGGERS: dict[str, str] = {
    # marker name        side
    "otter_buy":               "bull",
    "otter_sell":              "bear",
    "super_buy_high":          "bull",
    "super_sell_high":         "bear",
    "super_buy_std":           "bull",
    "super_sell_std":          "bear",
    "top_signal":              "bear",  # "top" = bearish reversal trigger
    "bottom_signal":           "bull",
}

# Ribbon crosses preserved here for future analysis as a GATE (e.g.,
# "only enter when ribbon is on the trade's side") rather than a trigger.
OTTER_GATES: dict[str, str] = {
    "ribbon_buy_cross":        "bull",
    "ribbon_sell_cross":       "bear",
}

# Bias-state decay windows. Without decay, the bias machine never enters
# `neutral` (because divergence events fire frequently enough to keep it
# always latched), which collapses the STANDARD tier into PREMIUM/COUNTER.
# These windows are the right architectural fix from the 2026-05-10 analysis.
BIAS_DECAY_SECONDS: dict[str, int] = {
    "bars_4h":    24 * 3600,        # 24h half-life
    "bars_1d":     7 * 86400,       # 7-day half-life
}

# Bias-setters on higher TFs (validated by EDA 2026-05-10 — see
# `scripts/eda_btc_scalping_signals.py`). These set HTF bias state.
HTF_BIAS_SETTERS: dict[str, str] = {
    "stoch_bullish_divergence":  "bull",
    "stoch_bearish_divergence":  "bear",
    "rsi_bullish_divergence":    "bull",
    "rsi_bearish_divergence":    "bear",
    "wt_bullish_divergence":     "bull",
    "wt_bearish_divergence":     "bear",
    "wt_2nd_bullish_divergence": "bull",
    "wt_2nd_bearish_divergence": "bear",
    "bull_divergence":           "bull",
    "bear_divergence":           "bear",
}


def build_bias_events(con: sqlite3.Connection, table: str) -> list[tuple[int, str]]:
    """Return sorted list of (ts, side) bias-setter events from the table.

    Each event represents one bullish-or-bearish bias-setter firing.
    Decay logic applied at lookup time (`bias_at_decayed`).
    """
    bullish_cols = [c for c, s in HTF_BIAS_SETTERS.items() if s == "bull"]
    bearish_cols = [c for c, s in HTF_BIAS_SETTERS.items() if s == "bear"]
    existing = {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    bullish_cols = [c for c in bullish_cols if c in existing]
    bearish_cols = [c for c in bearish_cols if c in existing]

    bull_clause = " OR ".join(f'("{c}" IS NOT NULL AND "{c}" != 0)' for c in bullish_cols) or "0"
    bear_clause = " OR ".join(f'("{c}" IS NOT NULL AND "{c}" != 0)' for c in bearish_cols) or "0"

    rows = con.execute(
        f'SELECT ts, ({bull_clause}) AS is_bull, ({bear_clause}) AS is_bear '
        f'FROM "{table}" ORDER BY ts'
    ).fetchall()

    events: list[tuple[int, str]] = []
    for ts, is_bull, is_bear in rows:
        if is_bull and not is_bear:
            events.append((ts, "bull"))
        elif is_bear and not is_bull:
            events.append((ts, "bear"))
        # If both fire on same bar (rare), skip — ambiguous
    return events


def bias_at_decayed(events: list[tuple[int, str]], ts: int, decay_seconds: int) -> str:
    """Bias state at `ts` with time-decay applied.

    A bias-setter fired at `ev_ts` keeps that side "active" until
    `ev_ts + decay_seconds`. At query time, we find the latest active
    bull event and the latest active bear event:
      - both active: more recent one wins
      - only bull: bull
      - only bear: bear
      - neither: neutral (decayed)

    Newer same-side events refresh the bias (extend its expiry).
    """
    latest_bull_event_ts = -1
    latest_bear_event_ts = -1
    for ev_ts, ev_side in events:
        if ev_ts > ts:
            break
        if ev_side == "bull":
            if ev_ts > latest_bull_event_ts:
                latest_bull_event_ts = ev_ts
        else:
            if ev_ts > latest_bear_event_ts:
                latest_bear_event_ts = ev_ts

    bull_active = (latest_bull_event_ts >= 0
                   and ts - latest_bull_event_ts <= decay_seconds)
    bear_active = (latest_bear_event_ts >= 0
                   and ts - latest_bear_event_ts <= decay_seconds)
    if bull_active and bear_active:
        return "bull" if latest_bull_event_ts > latest_bear_event_ts else "bear"
    if bull_active:
        return "bull"
    if bear_active:
        return "bear"
    return "neutral"


def detect_3m_volume_confluence(
    con: sqlite3.Connection, ts: int, side: str, lookback_bars: int = 5
) -> bool:
    """Does 3m CVD direction over last N bars match the signal side?

    Bullish confluence: cvd_close[ts] > cvd_close[ts - N×180s].
    Bearish confluence: cvd_close[ts] < cvd_close[ts - N×180s].
    """
    row = con.execute(
        'SELECT cvd_close FROM bars_3m WHERE ts = ?', (ts,)
    ).fetchone()
    if not row or row[0] is None:
        return False
    cvd_now = row[0]

    past_ts = ts - lookback_bars * 180
    row = con.execute(
        'SELECT cvd_close FROM bars_3m WHERE ts <= ? ORDER BY ts DESC LIMIT 1', (past_ts,)
    ).fetchone()
    if not row or row[0] is None:
        return False
    cvd_past = row[0]

    if side == "bull":
        return cvd_now > cvd_past
    return cvd_now < cvd_past


def classify_tier(
    confluence_3m: bool, htf_4h_bias: str, htf_1d_bias: str, side: str
) -> str:
    """Apply the tier ladder from the locked confluence model."""
    aligned_4h = htf_4h_bias == side
    aligned_1d = htf_1d_bias == side
    contra_4h = htf_4h_bias != "neutral" and htf_4h_bias != side
    contra_1d = htf_1d_bias != "neutral" and htf_1d_bias != side

    if confluence_3m and aligned_4h and aligned_1d:
        return "PREMIUM"
    if confluence_3m and aligned_4h and not contra_1d:
        return "STANDARD"
    if not confluence_3m and aligned_4h and aligned_1d:
        return "WEAK"
    if confluence_3m and (contra_4h or contra_1d):
        return "COUNTER"
    return "SKIP"


def compute_wick_and_returns(
    bars_3m: list[tuple[int, float, float, float, float, float]],
    trigger_idx: int,
    side: str,
    horizons: tuple[int, ...] = (1, 5, 10, 20),
) -> dict:
    """For a trigger at bars_3m[trigger_idx], compute MAE/MFE/fwd_ret per horizon.

    bars_3m rows are (ts, open, high, low, close, atr). Side is "bull" or "bear".
    All percentages reported in the SIGNAL'S favorable direction
    (a bear signal with -2% raw return reports +2% favorable return).
    """
    sign = +1.0 if side == "bull" else -1.0
    entry = bars_3m[trigger_idx][4]  # close of trigger bar

    results: dict[int, dict] = {}
    for h in horizons:
        end = trigger_idx + h
        if end >= len(bars_3m):
            results[h] = None
            continue
        # MAE = max adverse move from entry over [t+1, t+h]
        # MFE = max favorable move from entry over [t+1, t+h]
        adverse_pct = 0.0
        favorable_pct = 0.0
        for j in range(trigger_idx + 1, end + 1):
            high = bars_3m[j][2]
            low = bars_3m[j][3]
            # Convert to favorable / adverse from entry, signed by side
            if side == "bull":
                fav = (high - entry) / entry
                adv = (low - entry) / entry
            else:
                fav = (entry - low) / entry
                adv = (entry - high) / entry
            if fav > favorable_pct:
                favorable_pct = fav
            if adv < adverse_pct:
                adverse_pct = adv
        # Signed close-to-close return at horizon h
        close_at_h = bars_3m[end][4]
        ret = sign * (close_at_h - entry) / entry
        results[h] = {
            "mae": adverse_pct,       # negative number, max adverse
            "mfe": favorable_pct,     # positive number, max favorable
            "ret": ret,               # signed in signal's direction
        }
    return results


def append_history_csv(history_path: Path, snapshot: dict) -> None:
    """Append one row to the analysis-history CSV. Builds time series of
    per-run summary metrics so we can chart how the model evolves as the
    3m table grows. Columns auto-extend as new metrics are added — missing
    columns in older rows show as blank.
    """
    history_path.parent.mkdir(parents=True, exist_ok=True)

    existing_rows: list[dict] = []
    existing_fields: list[str] = []
    if history_path.exists():
        with history_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            existing_fields = list(reader.fieldnames or [])
            existing_rows = list(reader)

    all_fields: list[str] = list(existing_fields)
    for k in snapshot.keys():
        if k not in all_fields:
            all_fields.append(k)

    with history_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_fields)
        writer.writeheader()
        for row in existing_rows:
            writer.writerow(row)
        writer.writerow({k: snapshot.get(k, "") for k in all_fields})


def analyze(db_path: Path, history_path: Path | None = HISTORY_CSV) -> None:
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    # Build HTF bias event lists once
    decay_4h = BIAS_DECAY_SECONDS["bars_4h"]
    decay_1d = BIAS_DECAY_SECONDS["bars_1d"]
    print("Building HTF bias event lists (with time-decay applied at lookup)...")
    print(f"  4h decay window: {decay_4h//3600}h    1D decay window: {decay_1d//86400}d")
    bias_events_4h = build_bias_events(con, "bars_4h")
    bias_events_1d = build_bias_events(con, "bars_1d")
    print(f"  4h bias-setter events: {len(bias_events_4h)}")
    print(f"  1D bias-setter events: {len(bias_events_1d)}")

    # Load 3m bars once with what we need
    print()
    print("Loading 3m bars...")
    bars_3m = cur.execute(
        'SELECT ts, open, high, low, close, atr FROM bars_3m ORDER BY ts'
    ).fetchall()
    print(f"  3m bars loaded: {len(bars_3m):,}")
    ts_to_idx = {row[0]: i for i, row in enumerate(bars_3m)}

    # Find existing trigger columns
    existing = {r[1] for r in cur.execute("PRAGMA table_info(bars_3m)").fetchall()}
    triggers_in_data = {c: s for c, s in OTTER_TRIGGERS.items() if c in existing}

    # Collect trigger events
    print()
    print("Detecting Otter triggers...")
    events: list[dict] = []
    for trigger_col, side in triggers_in_data.items():
        rows = cur.execute(
            f'SELECT ts FROM bars_3m WHERE "{trigger_col}" IS NOT NULL AND "{trigger_col}" != 0 ORDER BY ts'
        ).fetchall()
        for (ts,) in rows:
            idx = ts_to_idx.get(ts)
            if idx is None:
                continue
            events.append({
                "ts": ts,
                "trigger": trigger_col,
                "side": side,
                "idx": idx,
            })
    print(f"  total Otter trigger events: {len(events)}")

    if not events:
        print("\n(no Otter triggers fired in this 3m window — nothing to analyze)")
        return

    # Per-trigger frequency
    print()
    print("  trigger frequency:")
    by_trigger: dict[str, int] = {}
    for e in events:
        by_trigger[e["trigger"]] = by_trigger.get(e["trigger"], 0) + 1
    for c, n in sorted(by_trigger.items(), key=lambda x: -x[1]):
        side = triggers_in_data[c]
        print(f"    {c:<25s} ({side:<4s})  fires: {n}")

    # Compute features + classify each event
    print()
    print("Computing wick / return / tier per event...")
    for e in events:
        e["bias_4h"] = bias_at_decayed(bias_events_4h, e["ts"], decay_4h)
        e["bias_1d"] = bias_at_decayed(bias_events_1d, e["ts"], decay_1d)
        e["confluence_3m"] = detect_3m_volume_confluence(con, e["ts"], e["side"])
        e["tier"] = classify_tier(e["confluence_3m"], e["bias_4h"], e["bias_1d"], e["side"])
        e["forward"] = compute_wick_and_returns(bars_3m, e["idx"], e["side"])
        e["entry"] = bars_3m[e["idx"]][4]
        e["atr"] = bars_3m[e["idx"]][5]

    # Build snapshot dict for CSV history. Computed once; used by both
    # the on-screen rendering and the append-to-history step.
    snapshot: dict = {
        "run_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "3m_window_start": bars_3m[0][0],
        "3m_window_end": bars_3m[-1][0],
        "3m_days": round((bars_3m[-1][0] - bars_3m[0][0]) / 86400.0, 2),
        "3m_bars": len(bars_3m),
        "decay_4h_seconds": decay_4h,
        "decay_1d_seconds": decay_1d,
        "total_triggers": len(events),
        "bias_events_4h": len(bias_events_4h),
        "bias_events_1d": len(bias_events_1d),
    }
    for trig, n in by_trigger.items():
        snapshot[f"trig_{trig}"] = n

    # ─── Section: stop survival ────────────────────────────────────────────
    print()
    print("=" * 78)
    print("STOP SURVIVAL -- would the proposed structural stop have held?")
    print("=" * 78)
    print("  Stop = max(1.5 x ATR_3m, 0.3% x entry).  Survives if MAE in next")
    print("  K bars > stop_distance (i.e. price didn't move farther against us).")
    print()
    print(f"  {'horizon':<10s} {'n':>5s}  {'survived':>9s}  {'rate':>7s}  {'med_MAE%':>9s}  {'p75_MAE%':>9s}  {'p95_MAE%':>9s}")
    print("  " + "-" * 70)
    for h in (1, 5, 10, 20):
        rows_h = [e for e in events if e["forward"].get(h) and e["atr"] is not None]
        if not rows_h:
            continue
        n = len(rows_h)
        survived = 0
        maes = []
        for e in rows_h:
            stop_pct = max(1.5 * e["atr"] / e["entry"], 0.003)
            mae_pct = abs(e["forward"][h]["mae"])
            if mae_pct < stop_pct:
                survived += 1
            maes.append(mae_pct * 100)
        maes.sort()
        med = maes[n // 2]
        p75 = maes[int(n * 0.75)] if n >= 4 else maes[-1]
        p95 = maes[int(n * 0.95)] if n >= 20 else maes[-1]
        rate = 100.0 * survived / n
        snapshot[f"stop_survive_{h}b_n"] = n
        snapshot[f"stop_survive_{h}b_rate"] = round(rate, 2)
        snapshot[f"med_MAE_{h}b_pct"] = round(med, 4)
        snapshot[f"p75_MAE_{h}b_pct"] = round(p75, 4)
        print(f"  {h:>3d} bars  {n:>5d}  {survived:>9d}  {rate:>6.1f}%  "
              f"{med:>+8.3f}%  {p75:>+8.3f}%  {p95:>+8.3f}%")

    # ─── Section: tier distribution ────────────────────────────────────────
    print()
    print("=" * 78)
    print("TIER DISTRIBUTION — how triggers classified at decision time")
    print("=" * 78)
    by_tier: dict[str, list[dict]] = {}
    for e in events:
        by_tier.setdefault(e["tier"], []).append(e)
    print()
    print(f"  {'tier':<10s} {'count':>6s}  {'pct':>6s}")
    print("  " + "-" * 24)
    for tier in ("PREMIUM", "STANDARD", "WEAK", "COUNTER", "SKIP"):
        n = len(by_tier.get(tier, []))
        pct = 100.0 * n / len(events) if events else 0
        snapshot[f"tier_{tier}_n"] = n
        snapshot[f"tier_{tier}_pct"] = round(pct, 2)
        print(f"  {tier:<10s} {n:>6d}  {pct:>5.1f}%")

    # ─── Section: forward returns by tier ──────────────────────────────────
    print()
    print("=" * 78)
    print("FORWARD-RETURN BY TIER — does the tier ranking actually predict?")
    print("=" * 78)
    print("  Returns in the SIGNAL'S favorable direction.")
    print()
    print(f"  {'tier':<10s} {'h':>3s}  {'n':>4s}  {'mean%':>8s}  {'med%':>8s}  "
          f"{'mean_MAE%':>10s}  {'mean_MFE%':>10s}")
    print("  " + "-" * 70)
    for tier in ("PREMIUM", "STANDARD", "WEAK", "COUNTER"):
        evts = by_tier.get(tier, [])
        if not evts:
            print(f"  {tier:<10s}  (no events in this sample)")
            continue
        for h in (5, 10, 20):
            rows_h = [e for e in evts if e["forward"].get(h)]
            if not rows_h:
                continue
            rets = [e["forward"][h]["ret"] for e in rows_h]
            maes = [abs(e["forward"][h]["mae"]) for e in rows_h]
            mfes = [e["forward"][h]["mfe"] for e in rows_h]
            n = len(rets)
            mean = sum(rets) / n
            med = statistics.median(rets)
            mean_mae = sum(maes) / n
            mean_mfe = sum(mfes) / n
            snapshot[f"{tier}_{h}b_n"] = n
            snapshot[f"{tier}_{h}b_mean_pct"] = round(mean * 100, 4)
            snapshot[f"{tier}_{h}b_med_pct"] = round(med * 100, 4)
            print(f"  {tier:<10s} {h:>3d}  {n:>4d}  "
                  f"{mean*100:>+7.3f}%  {med*100:>+7.3f}%  "
                  f"{mean_mae*100:>+9.3f}%  {mean_mfe*100:>+9.3f}%")

    # ─── Section: confluence + bias breakdown ──────────────────────────────
    print()
    print("=" * 78)
    print("CONFLUENCE + BIAS — what the inputs to the tier classifier looked like")
    print("=" * 78)
    confluent = sum(1 for e in events if e["confluence_3m"])
    bias_4h_dist = {"bull": 0, "bear": 0, "neutral": 0}
    bias_1d_dist = {"bull": 0, "bear": 0, "neutral": 0}
    for e in events:
        bias_4h_dist[e["bias_4h"]] += 1
        bias_1d_dist[e["bias_1d"]] += 1
    print(f"  3m volume confluence (CVD direction agrees w/ side): "
          f"{confluent}/{len(events)} ({100.0*confluent/len(events):.1f}%)")
    print(f"  4h bias at trigger time:  bull={bias_4h_dist['bull']}  "
          f"bear={bias_4h_dist['bear']}  neutral={bias_4h_dist['neutral']}")
    print(f"  1D bias at trigger time:  bull={bias_1d_dist['bull']}  "
          f"bear={bias_1d_dist['bear']}  neutral={bias_1d_dist['neutral']}")
    snapshot["confluence_pct"] = round(100.0 * confluent / len(events), 2)
    snapshot["bias_4h_bull"] = bias_4h_dist["bull"]
    snapshot["bias_4h_bear"] = bias_4h_dist["bear"]
    snapshot["bias_4h_neutral"] = bias_4h_dist["neutral"]
    snapshot["bias_1d_bull"] = bias_1d_dist["bull"]
    snapshot["bias_1d_bear"] = bias_1d_dist["bear"]
    snapshot["bias_1d_neutral"] = bias_1d_dist["neutral"]

    # ─── Section: sample-size caveat ───────────────────────────────────────
    days = (bars_3m[-1][0] - bars_3m[0][0]) / 86400.0
    print()
    print("=" * 78)
    print(f"SAMPLE SIZE: {len(events)} trigger events over {days:.1f} days "
          f"({len(events)/days:.1f}/day avg).")
    print("=" * 78)
    print("  At this n, distribution shapes are interesting; absolute numbers")
    print("  are noise. Per-tier results are especially preliminary -- re-run")
    print("  weekly as the 3m table accumulates more bars.")

    # ─── CSV history append ────────────────────────────────────────────────
    if history_path is not None:
        append_history_csv(history_path, snapshot)
        print()
        print(f"Snapshot appended to {history_path.relative_to(REPO_ROOT)}")
        print("  Re-run weekly after each TV CSV ingest. Watch the time series")
        print("  for tier distribution shifts, stop-survival convergence, and")
        print("  per-tier mean-return separation.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--history", default=str(HISTORY_CSV),
                        help=f"Append-only CSV time-series of summary metrics "
                             f"(default {HISTORY_CSV}). Pass empty string to disable.")
    args = parser.parse_args(argv)
    history = Path(args.history) if args.history else None
    analyze(Path(args.db), history)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
