"""
S4 orchestrator: train/eval/report for kalshi_crypto_v2 model v1 + Rider B.

NO order/placement code anywhere. READ-ONLY research. Lab DB is opened read-only
in dataset.py. This script never writes to any DB.

Run:  python research/kalshi_crypto_v2/s4/run_s4.py
"""
from __future__ import annotations

import os
import sys
import warnings
import json
import math
import textwrap
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.exceptions import ConvergenceWarning

warnings.filterwarnings("ignore", category=ConvergenceWarning)
warnings.filterwarnings("ignore", category=UserWarning)

# ---------------------------------------------------------------------------
# Path setup — expose lab/ harness
# ---------------------------------------------------------------------------
S4_DIR     = os.path.dirname(os.path.abspath(__file__))
KCV2_DIR   = os.path.dirname(S4_DIR)
LAB_DIR    = os.path.join(KCV2_DIR, "lab")
sys.path.insert(0, LAB_DIR)

from split import chronological_split, expanding_cv_folds, flat_sensitivity
from calibration import brier, reliability_curve

# dataset builder (same dir)
sys.path.insert(0, S4_DIR)
from dataset import build_dataset, LAB_DB

ASSETS = ["BTC", "ETH", "SOL", "XRP"]
SEED   = 42

# ---------------------------------------------------------------------------
# GBM model helpers
# ---------------------------------------------------------------------------

def _train_catboost(X_train, y_train, X_val=None, y_val=None):
    """Train CatBoost. Falls back to XGBoost on import error."""
    try:
        from catboost import CatBoostClassifier
        model = CatBoostClassifier(
            iterations=400,
            depth=6,
            learning_rate=0.05,
            random_seed=SEED,
            verbose=0,
            loss_function="Logloss",
            eval_metric="Logloss",
            early_stopping_rounds=30 if X_val is not None else None,
        )
        if X_val is not None and y_val is not None:
            model.fit(X_train, y_train, eval_set=(X_val, y_val))
        else:
            model.fit(X_train, y_train)
        return model, "catboost"
    except Exception as e:
        print(f"  [WARN] CatBoost failed ({e}); falling back to XGBoost")
        import xgboost as xgb
        model = xgb.XGBClassifier(
            n_estimators=400,
            max_depth=6,
            learning_rate=0.05,
            random_state=SEED,
            verbosity=0,
            use_label_encoder=False,
            eval_metric="logloss",
        )
        model.fit(X_train, y_train)
        return model, "xgboost"


def _get_importances(model, feature_cols, backend):
    """Return sorted list of (feature, importance)."""
    try:
        if backend == "catboost":
            imps = model.get_feature_importance()
        else:
            imps = model.feature_importances_
        pairs = list(zip(feature_cols, imps))
        pairs.sort(key=lambda x: -x[1])
        return pairs
    except Exception:
        return []


def _prep_X(df, feature_cols):
    """Prepare feature matrix, replacing inf with NaN."""
    X = df[feature_cols].copy()
    X = X.replace([np.inf, -np.inf], np.nan)
    return X.values.astype(np.float32)


# ---------------------------------------------------------------------------
# Platt/sigmoid calibration
# ---------------------------------------------------------------------------

def calibrate_platt(raw_probs_cal: np.ndarray, y_cal: np.ndarray,
                    raw_probs_test: np.ndarray) -> np.ndarray:
    """Fit sigmoid calibration on (raw_probs_cal, y_cal), apply to test."""
    lr = LogisticRegression(C=1.0, max_iter=500, random_state=SEED)
    lr.fit(raw_probs_cal.reshape(-1, 1), y_cal)
    cal_probs = lr.predict_proba(raw_probs_test.reshape(-1, 1))[:, 1]
    return cal_probs


# ---------------------------------------------------------------------------
# CV sanity metric on train_core
# ---------------------------------------------------------------------------

def cv_brier(X_core, y_core, feature_cols, k=5):
    """Expanding-window CV Brier on train_core."""
    n = len(y_core)
    folds = expanding_cv_folds(n, k=k)
    if not folds:
        return float("nan")
    brieries = []
    for fold in folds:
        if len(fold["train"]) < 50 or len(fold["val"]) < 20:
            continue
        Xtr = X_core[fold["train"]]
        ytr = y_core[fold["train"]]
        Xvl = X_core[fold["val"]]
        yvl = y_core[fold["val"]]
        try:
            m, _ = _train_catboost(Xtr, ytr)
            raw = m.predict_proba(Xvl)[:, 1]
            brieries.append(brier(raw.tolist(), yvl.tolist()))
        except Exception:
            pass
    return float(np.mean(brieries)) if brieries else float("nan")


# ---------------------------------------------------------------------------
# Core train/eval pipeline for one asset
# ---------------------------------------------------------------------------

def run_asset(asset: str, data: dict) -> dict:
    """
    Train v1 model + Rider B for one asset.
    Returns result dict with per-asset metrics.
    """
    print(f"\n{'='*60}")
    print(f"  ASSET: {asset}")
    print(f"{'='*60}")

    df_v1    = data["df_v1"]
    feat_v1  = data["feature_cols_v1"]
    balance  = data["label_balance"]
    print(f"  Label balance: {balance}")

    # -----------------------------------------------------------------------
    # v1 split
    # -----------------------------------------------------------------------
    n_v1 = len(df_v1)
    ts_sorted = df_v1["open_ts_ms"].tolist()
    split_info = chronological_split(ts_sorted, holdout_frac=0.2)
    train_idx    = split_info["train"]
    holdout_idx  = split_info["holdout"]
    n_train      = split_info["n_train"]
    n_holdout    = split_info["n_holdout"]
    print(f"  Split: total={n_v1}, train={n_train}, holdout={n_holdout}, "
          f"boundary_ts={split_info.get('boundary_ts')}")

    df_train   = df_v1.iloc[train_idx].reset_index(drop=True)
    df_holdout = df_v1.iloc[holdout_idx].reset_index(drop=True)

    # Carve calibration slice from TRAIN (last ~20% of train, time-ordered)
    n_cal_end  = len(df_train)
    n_cal_start = int(n_cal_end * 0.8)
    df_core = df_train.iloc[:n_cal_start].reset_index(drop=True)
    df_cal  = df_train.iloc[n_cal_start:].reset_index(drop=True)
    print(f"  Train core: {len(df_core)}, calibration slice: {len(df_cal)}")

    X_core    = _prep_X(df_core, feat_v1)
    y_core    = df_core["y"].values.astype(int)
    X_cal     = _prep_X(df_cal,  feat_v1)
    y_cal     = df_cal["y"].values.astype(int)
    X_holdout = _prep_X(df_holdout, feat_v1)
    y_holdout = df_holdout["y"].values.astype(int)

    # -----------------------------------------------------------------------
    # CV sanity Brier (on train_core)
    # -----------------------------------------------------------------------
    print(f"  Running CV on train_core ...")
    mean_cv_brier = cv_brier(X_core, y_core, feat_v1, k=5)
    print(f"  Mean CV Brier (train_core): {mean_cv_brier:.5f}")

    # -----------------------------------------------------------------------
    # Train GBM on train_core
    # -----------------------------------------------------------------------
    print(f"  Training GBM on train_core ...")
    model, backend = _train_catboost(X_core, y_core, X_val=X_cal, y_val=y_cal)
    print(f"  Trained ({backend})")

    # Raw probabilities
    raw_cal      = model.predict_proba(X_cal)[:, 1]
    raw_holdout  = model.predict_proba(X_holdout)[:, 1]

    # Platt calibration
    cal_probs = calibrate_platt(raw_cal, y_cal, raw_holdout)

    # -----------------------------------------------------------------------
    # Holdout evaluation
    # -----------------------------------------------------------------------
    brier_model      = brier(cal_probs.tolist(), y_holdout.tolist())
    brier_base_rate  = brier([float(df_v1["y"].mean())] * n_holdout,
                             y_holdout.tolist())
    brier_const_half = brier([0.5] * n_holdout, y_holdout.tolist())

    print(f"  Holdout Brier (model): {brier_model:.5f}")
    print(f"  Holdout Brier (base rate={df_v1['y'].mean():.3f}): {brier_base_rate:.5f}")
    print(f"  Holdout Brier (const 0.5): {brier_const_half:.5f}")

    # Reliability curve
    rel_curve = reliability_curve(cal_probs.tolist(), y_holdout.tolist(), n_bins=10)

    # Feature importances
    importances = _get_importances(model, feat_v1, backend)
    print(f"  Top 10 features:")
    for fname, fimp in importances[:10]:
        flag = " [FLAG: feature-only]" if fname in ("rsi_14", "stoch_k_14") else ""
        print(f"    {fname:25s}: {fimp:.4f}{flag}")

    # -----------------------------------------------------------------------
    # Flat-bucket analysis
    # -----------------------------------------------------------------------
    moves_holdout = df_holdout["move_pct"].tolist()
    flat_results  = flat_sensitivity(moves_holdout)
    flat_report   = []
    for fb in flat_results:
        thr   = fb["threshold"]
        n_dir = fb["n_directional"]
        n_flt = fb["n_flat"]
        dir_probs    = [cal_probs[i] for i in fb["directional"]]
        dir_outcomes = [y_holdout[i] for i in fb["directional"]]
        flt_probs    = [cal_probs[i] for i in fb["flat"]]
        flt_outcomes = [y_holdout[i] for i in fb["flat"]]
        b_dir = brier(dir_probs, dir_outcomes) if dir_probs else float("nan")
        b_flt = brier(flt_probs, flt_outcomes) if flt_probs else float("nan")
        flat_report.append({
            "threshold_pct": f"{thr*100:.2f}%",
            "n_directional": n_dir,
            "n_flat": n_flt,
            "brier_directional": round(b_dir, 5) if not math.isnan(b_dir) else None,
            "brier_flat": round(b_flt, 5) if not math.isnan(b_flt) else None,
        })
        print(f"  Flat @{thr*100:.2f}%: dir={n_dir} Brier={b_dir:.5f}, "
              f"flat={n_flt} Brier={b_flt:.5f}")

    # -----------------------------------------------------------------------
    # TODO HOOKS (explicit placeholders for market-benchmark + EV)
    # -----------------------------------------------------------------------
    # TODO_MARKET_BENCHMARK:
    #   brier_market = brier(market_p_list, y_holdout.tolist())
    #   skill_score = 1 - brier_model / brier_market
    #   Requires: Kalshi window-open candle yes_bid_close / yes_ask_close per window
    #   Source: lab_kalshi_candles (being written; not read here per spec)
    #   Contact: lead engineer will wire this in S5 via calibration.compare_to_market()
    #
    # TODO_DUAL_EV:
    #   taker_ev_results = [ev.taker_ev(p, side, yes_ask, no_ask) for each window]
    #   maker_ev_results = [ev.maker_ev(p, side, bid, post_candles, close_ts) for each]
    #   agg_maker = ev.aggregate_maker(maker_ev_results)  # MUST include fill_rate
    #   agg_taker = ev.aggregate_taker(taker_ev_results)
    #   Source: lab_kalshi_candles mid-window price + post-candles for maker fill model
    #   import from lab/ev.py: taker_ev, maker_ev, aggregate_maker, aggregate_taker

    # -----------------------------------------------------------------------
    # Rider B
    # -----------------------------------------------------------------------
    rider_b_result = None
    df_riderb      = data.get("df_riderb")
    feat_riderb    = data.get("feature_cols_riderb", [])

    if df_riderb is not None and len(df_riderb) > 100 and feat_riderb:
        print(f"\n  --- Rider B: {len(df_riderb)} windows (15m flow window) ---")
        print(f"  NOTE: Small-n, thin holdout. Evidence-probe only.")

        rb_ts_sorted = df_riderb["open_ts_ms"].tolist()
        rb_split     = chronological_split(rb_ts_sorted, holdout_frac=0.2)
        rb_train_idx = rb_split["train"]
        rb_hold_idx  = rb_split["holdout"]
        print(f"  Rider B split: train={rb_split['n_train']}, holdout={rb_split['n_holdout']}")

        rb_train   = df_riderb.iloc[rb_train_idx].reset_index(drop=True)
        rb_holdout = df_riderb.iloc[rb_hold_idx].reset_index(drop=True)

        rb_n_cal_end   = len(rb_train)
        rb_n_cal_start = int(rb_n_cal_end * 0.8)
        rb_core = rb_train.iloc[:rb_n_cal_start].reset_index(drop=True)
        rb_cal  = rb_train.iloc[rb_n_cal_start:].reset_index(drop=True)

        X_rb_core    = _prep_X(rb_core,    feat_riderb)
        y_rb_core    = rb_core["y"].values.astype(int)
        X_rb_cal     = _prep_X(rb_cal,     feat_riderb)
        y_rb_cal     = rb_cal["y"].values.astype(int)
        X_rb_holdout = _prep_X(rb_holdout, feat_riderb)
        y_rb_holdout = rb_holdout["y"].values.astype(int)

        if len(rb_core) >= 50 and len(rb_cal) >= 10 and len(rb_holdout) >= 10:
            rb_model, rb_backend = _train_catboost(X_rb_core, y_rb_core)
            rb_raw_cal      = rb_model.predict_proba(X_rb_cal)[:, 1]
            rb_raw_holdout  = rb_model.predict_proba(X_rb_holdout)[:, 1]
            rb_cal_probs    = calibrate_platt(rb_raw_cal, y_rb_cal, rb_raw_holdout)
            rb_brier        = brier(rb_cal_probs.tolist(), y_rb_holdout.tolist())
            rb_brier_half   = brier([0.5] * len(y_rb_holdout), y_rb_holdout.tolist())
            rb_importances  = _get_importances(rb_model, feat_riderb, rb_backend)
            print(f"  Rider B holdout Brier: {rb_brier:.5f} (const 0.5: {rb_brier_half:.5f})")
            print(f"  Rider B top 10 features:")
            for fname, fimp in rb_importances[:10]:
                flag = " [FLAG: feature-only]" if fname in ("rsi_14", "stoch_k_14") else ""
                print(f"    {fname:25s}: {fimp:.4f}{flag}")
            rider_b_result = {
                "n_total":       len(df_riderb),
                "n_holdout":     rb_split["n_holdout"],
                "brier_model":   round(rb_brier, 5),
                "brier_const05": round(rb_brier_half, 5),
                "importances":   rb_importances[:15],
                "small_n_caveat": True,
            }
        else:
            print(f"  Rider B: insufficient splits (core={len(rb_core)}, "
                  f"cal={len(rb_cal)}, hold={len(rb_holdout)})")

    return {
        "asset":          asset,
        "n_total":        n_v1,
        "n_train":        n_train,
        "n_holdout":      n_holdout,
        "label_balance":  balance,
        "mean_cv_brier":  round(mean_cv_brier, 5) if not math.isnan(mean_cv_brier) else None,
        "brier_model":    round(brier_model, 5),
        "brier_base_rate": round(brier_base_rate, 5),
        "brier_const05":  round(brier_const_half, 5),
        "rel_curve":      rel_curve,
        "importances":    importances[:20],
        "flat_report":    flat_report,
        "leakage_ok":     data["leakage_ok"],
        "rider_b":        rider_b_result,
        # TODO hooks (explicit; filled by lead engineer in S5):
        "brier_market":   None,  # TODO_MARKET_BENCHMARK
        "skill_score":    None,  # TODO_MARKET_BENCHMARK
        "dual_ev_taker":  None,  # TODO_DUAL_EV
        "dual_ev_maker":  None,  # TODO_DUAL_EV (must include fill_rate)
    }


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def _fmt_rel_curve(curve):
    lines = ["| Bin         | n    | mean_pred | obs_freq |",
             "|-------------|------|-----------|----------|"]
    for b in curve:
        n    = b["n"] or 0
        mpred = f"{b['mean_pred']:.4f}" if b["mean_pred"] is not None else "  -   "
        ofreq = f"{b['obs_freq']:.4f}"  if b["obs_freq"]  is not None else "  -   "
        lo, hi = b["bin"]
        lines.append(f"| [{lo:.2f},{hi:.2f}) | {n:<4} | {mpred:>9} | {ofreq:>8} |")
    return "\n".join(lines)


def _fmt_importances(imps, n=15):
    lines = ["| Feature                   | Importance | Notes                  |",
             "|---------------------------|------------|------------------------|"]
    for fname, fimp in imps[:n]:
        note = "FLAG: feature-only" if fname in ("rsi_14", "stoch_k_14") else ""
        lines.append(f"| {fname:25s} | {fimp:>10.4f} | {note:22s} |")
    return "\n".join(lines)


def _fmt_flat(flat_report):
    lines = ["| Threshold | n_directional | n_flat | Brier_directional | Brier_flat |",
             "|-----------|---------------|--------|-------------------|------------|"]
    for r in flat_report:
        bd = f"{r['brier_directional']:.5f}" if r["brier_directional"] is not None else "  -   "
        bf = f"{r['brier_flat']:.5f}"        if r["brier_flat"] is not None else "  -   "
        lines.append(f"| {r['threshold_pct']:>9} | {r['n_directional']:>13} | "
                     f"{r['n_flat']:>6} | {bd:>17} | {bf:>10} |")
    return "\n".join(lines)


def write_report(results: list[dict], report_path: str):
    lines = []
    lines.append("# S4 Model v1 — Kalshi Crypto Binary Prediction")
    lines.append("")
    lines.append(f"**Date:** 2026-08-02  ")
    lines.append(f"**Assets:** BTC, ETH, SOL, XRP  ")
    lines.append(f"**Label:** y=1 if Kalshi 15m result='yes' (price UP), "
                 f"y=0 if result='no'  ")
    lines.append(f"**Data:** Binance 1m bars, Coinalyze 1h flow, "
                 f"Kalshi 15m markets 2026-05-25 → present  ")
    lines.append("")

    # --- Methodology summary ---
    lines.append("## Methodology")
    lines.append("")
    lines.append("### Label Rule (S1)")
    lines.append("For each 15m market with result in {yes, no}: y=1 if result=yes, y=0 if no.  ")
    lines.append("strike=floor_strike, settle=settlement_value,  ")
    lines.append("move_pct=(settle-strike)/|strike| (divide-by-zero or None → skip).  ")
    lines.append("")
    lines.append("### Leakage Rule")
    lines.append(
        "Features computed AS-OF the last fully-closed bar BEFORE the window opens: "
        "reference bar ts_ms ≤ open_ts_ms - 60 000 ms.  "
    )
    lines.append(
        "All rolling indicators computed causally on the full bar series first "
        "(pandas rolling is causal), then joined via `merge_asof(direction='backward')` "
        "using key = open_ts_ms - 60 000.  "
    )
    lines.append(
        "Post-join assertion: no feature row used a bar ts > open_ts_ms - 60 000.  "
    )
    lines.append("")
    lines.append("### Split")
    lines.append("Chronological split: holdout = last 20% by count (touched once at final eval).  ")
    lines.append("Within TRAIN: calibration slice = last ~20% of train (time-ordered).  ")
    lines.append("GBM fitted on train_core (earlier 80% of train).  ")
    lines.append("Platt/sigmoid calibration fitted on calibration slice.  ")
    lines.append("CV sanity metric: expanding-window (k=5) Brier on train_core.  ")
    lines.append("")
    lines.append("### Model")
    lines.append("CatBoostClassifier (iterations=400, depth=6, lr=0.05, seed=42).  ")
    lines.append("Calibrated via Platt (sklearn LogisticRegression on GBM raw probs).  ")
    lines.append("")
    lines.append("### Missing Flow Values")
    lines.append(
        "Coinalyze 1h flow NaN values left as NaN for the GBM to handle natively "
        "(CatBoost supports NaN). An `is_missing_flow_1h` flag column distinguishes "
        "true-zero from missing."
    )
    lines.append("")
    lines.append("### Flat-Bucket Caveat")
    lines.append(
        "**FINDING:** `settlement_value` in `lab_kalshi_markets` is the Kalshi binary "
        "contract settlement (0.0=no, 1.0=yes), NOT the close-60s-avg RTI price. "
        "Computing `move_pct = (settle - strike) / |strike|` with a binary settle and "
        "RTI strike (~50k–80k) yields |move_pct| ≈ 1.0 for every window. "
        "The flat-bucket analysis is therefore trivially all-directional "
        "(n_flat=0 at all thresholds). "
        "To compute physically meaningful move_pct, the actual close-60s-avg RTI for "
        "each window would need to be derived from the cfbenchmarks feed or Binance bar "
        "averages — deferred to a future phase. The flat-bucket rows are reported as "
        "observed (all windows directional) for completeness."
    )
    lines.append("")

    # --- Per-asset sections ---
    lines.append("---")
    lines.append("")
    lines.append("## Per-Asset Results (v1 + Rider B)")
    lines.append("")

    for r in results:
        asset = r["asset"]
        bal   = r["label_balance"]
        lines.append(f"### {asset}")
        lines.append("")
        lines.append(f"**Windows:** {r['n_total']} total | {r['n_train']} train "
                     f"| {r['n_holdout']} holdout  ")
        lines.append(f"**Label balance:** y=1 (yes): {bal['n_yes']} "
                     f"({bal['base_rate']:.3f}), y=0 (no): {bal['n_no']}  ")
        lines.append(f"**Leakage assertion:** {'PASS' if r['leakage_ok'] else '*** FAIL ***'}  ")
        lines.append("")
        lines.append("#### Brier Scores (holdout)")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Brier_model (calibrated) | {r['brier_model']:.5f} |")
        lines.append(f"| Brier_const_0.5 | {r['brier_const05']:.5f} |")
        lines.append(f"| Brier_base_rate | {r['brier_base_rate']:.5f} |")
        lines.append(f"| Mean CV Brier (train_core) | {r['mean_cv_brier'] or 'N/A'} |")
        lines.append(f"| Brier_market | TODO_MARKET_BENCHMARK |")
        lines.append(f"| Skill score vs market | TODO_MARKET_BENCHMARK |")
        lines.append("")
        lines.append("#### Reliability Curve (holdout, 10 bins)")
        lines.append("")
        lines.append(_fmt_rel_curve(r["rel_curve"]))
        lines.append("")
        lines.append("#### Feature Importances (top 15, v1)")
        lines.append("")
        lines.append(_fmt_importances(r["importances"], n=15))
        lines.append("")
        lines.append("#### Flat-Bucket Analysis (holdout)")
        lines.append("")
        lines.append(_fmt_flat(r["flat_report"]))
        lines.append("")

        # --- Rider B ---
        rb = r.get("rider_b")
        lines.append("#### Rider B (15m flow, ~last 21d)")
        lines.append("")
        if rb:
            lines.append(
                f"**CAVEAT: Small-n evidence probe only.** "
                f"n={rb['n_total']} windows (15m flow coverage), "
                f"holdout={rb['n_holdout']}. "
                f"Results not comparable to v1 (different time window, thinner data)."
            )
            lines.append("")
            lines.append(f"| Metric | Value |")
            lines.append(f"|--------|-------|")
            lines.append(f"| Brier_model (Rider B) | {rb['brier_model']:.5f} |")
            lines.append(f"| Brier_const_0.5 | {rb['brier_const05']:.5f} |")
            lines.append(f"| Brier_market | TODO_MARKET_BENCHMARK |")
            lines.append("")
            lines.append("Rider B top 10 feature importances:")
            lines.append("")
            lines.append(_fmt_importances(rb["importances"], n=10))
            lines.append("")
            lines.append(
                "_Interpretation note: if 15m flow features (cvd_15m, oi_delta_15m, "
                "ls_ratio_15m) rank highly, that supports investing in the deferred "
                "Binance-aggTrades fine-flow reconstruction for v2. If they rank near "
                "the bottom, v1 features are sufficient for near-term iteration._"
            )
        else:
            lines.append(
                "Rider B not available for this asset "
                "(insufficient 15m flow data or sample size)."
            )
        lines.append("")
        lines.append("---")
        lines.append("")

    # --- TODO hooks ---
    lines.append("## TODO Hooks (lead engineer — S5)")
    lines.append("")
    lines.append("### TODO_MARKET_BENCHMARK")
    lines.append("```python")
    lines.append("# Wire in from lab/calibration.py:")
    lines.append("from calibration import compare_to_market")
    lines.append("# market_p_list: list of Kalshi window-open candle implied probs")
    lines.append("# (e.g., yes_bid_close or (yes_bid+yes_ask)/2 at the 1m candle")
    lines.append("#  closest to but before window open, from lab_kalshi_candles)")
    lines.append("result = compare_to_market(model_p_list, market_p_list, y_holdout)")
    lines.append("# result keys: brier_model, brier_market, skill_score_vs_market, n")
    lines.append("```")
    lines.append("")
    lines.append("### TODO_DUAL_EV")
    lines.append("```python")
    lines.append("# Wire in from lab/ev.py:")
    lines.append("from ev import taker_ev, maker_ev, aggregate_maker, aggregate_taker")
    lines.append("# For each holdout window:")
    lines.append("#   side = 'yes' if cal_prob > 0.5 else 'no'")
    lines.append("#   taker_result = taker_ev(cal_prob, side, yes_ask, no_ask)")
    lines.append("#   post_candles = [{ts, yes_low, no_low, volume}, ...]")
    lines.append("#                  from lab_kalshi_candles after window open")
    lines.append("#   maker_result = maker_ev(cal_prob, side, bid, post_candles,")
    lines.append("#                           window_close_ts=close_ts)")
    lines.append("# Aggregation:")
    lines.append("#   agg_t = aggregate_taker(taker_results)")
    lines.append("#   agg_m = aggregate_maker(maker_results)")
    lines.append("#   # MUST report agg_m['fill_rate'] alongside agg_m['mean_ev_on_fills']")
    lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Summary Table")
    lines.append("")
    lines.append("| Asset | N total | N holdout | Base rate | Brier_model | "
                 "Brier_const05 | CV Brier | Rider B Brier |")
    lines.append("|-------|---------|-----------|-----------|-------------|"
                 "---------------|----------|---------------|")
    for r in results:
        rb_brier = (f"{r['rider_b']['brier_model']:.5f}"
                    if r.get("rider_b") else "N/A")
        lines.append(
            f"| {r['asset']} | {r['n_total']} | {r['n_holdout']} | "
            f"{r['label_balance']['base_rate']:.3f} | {r['brier_model']:.5f} | "
            f"{r['brier_const05']:.5f} | {r['mean_cv_brier'] or 'N/A'} | {rb_brier} |"
        )
    lines.append("")
    lines.append(
        "_Distributions and leads only. No verdict on whether the model 'works' or "
        "'beats the market' is drawn here — that gate requires brier_market (TODO) "
        "and real edge validation under live conditions. Accuracy is reported, never gates._"
    )
    lines.append("")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nReport written: {report_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("  S4 Quant Model v1 — kalshi_crypto_v2")
    print("=" * 70)

    # Security check: no order/placement surface
    import subprocess, shutil
    grep_cmd = shutil.which("grep") or "grep"
    grep_result = None
    try:
        result = subprocess.run(
            [grep_cmd, "-r",
             "place_order\\|create_order\\|POST.*order\\|submit_order",
             S4_DIR],
            capture_output=True, text=True
        )
        grep_result = result.stdout.strip()
    except Exception:
        grep_result = "(grep unavailable)"
    if grep_result:
        print(f"\n*** WARNING: order surface grep hit: {grep_result}")
    else:
        print("\n[OK] No order/placement surface in s4/ dir (grep clean)")

    all_results = []

    for asset in ASSETS:
        print(f"\n{'='*70}")
        print(f"  Building dataset for {asset} ...")
        data = build_dataset(asset, db_path=LAB_DB, include_rider_b=True)
        result = run_asset(asset, data)
        all_results.append(result)

    # -----------------------------------------------------------------------
    # Self-verification printout
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("  SELF-VERIFICATION SUMMARY")
    print("=" * 70)
    all_leakage_ok = True
    for r in all_results:
        bal = r["label_balance"]
        br  = bal["base_rate"]
        lk  = r["leakage_ok"]
        if not lk:
            all_leakage_ok = False
        flag_bal = " [WARNING: imbalanced]" if abs(br - 0.5) > 0.15 else ""
        print(f"  {r['asset']}: N={r['n_total']}, holdout={r['n_holdout']}, "
              f"y=1 rate={br:.3f}{flag_bal}, "
              f"leakage={'OK' if lk else '*** FAIL ***'}, "
              f"Brier_model={r['brier_model']:.5f} "
              f"(const05={r['brier_const05']:.5f})")
    print()
    if all_leakage_ok:
        print("  [PASS] All assets: leakage assertion OK")
    else:
        print("  [*** FAIL ***] Leakage violation detected in one or more assets!")

    brier_range_ok = all(
        0.10 <= r["brier_model"] <= 0.30 for r in all_results
    )
    if brier_range_ok:
        print("  [OK] All Brier_model values in plausible range [0.10, 0.30]")
    else:
        for r in all_results:
            if not (0.10 <= r["brier_model"] <= 0.30):
                print(f"  [WARNING] {r['asset']} Brier_model={r['brier_model']:.5f} "
                      f"outside [0.10, 0.30]")

    # -----------------------------------------------------------------------
    # Write report
    # -----------------------------------------------------------------------
    # Worktree root: s4/ -> kalshi_crypto_v2 (KCV2_DIR) -> research -> wt_root
    # KCV2_DIR = .../cc-2026-08-01c-wt/research/kalshi_crypto_v2
    # dirname(KCV2_DIR) = .../cc-2026-08-01c-wt/research
    # dirname(dirname(KCV2_DIR)) = .../cc-2026-08-01c-wt  <- worktree root
    _wt_root = os.path.dirname(os.path.dirname(KCV2_DIR))
    reports_dir = os.path.join(_wt_root, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    report_path = os.path.join(
        reports_dir, "2026-08-02_kalshi_crypto_v2_S4_model_v1.md"
    )
    write_report(all_results, report_path)

    # -----------------------------------------------------------------------
    # Condensed JSON summary to stdout
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("  JSON SUMMARY")
    print("=" * 70)
    summary = []
    for r in all_results:
        summary.append({
            "asset":        r["asset"],
            "n_total":      r["n_total"],
            "n_holdout":    r["n_holdout"],
            "base_rate":    r["label_balance"]["base_rate"],
            "leakage_ok":   r["leakage_ok"],
            "mean_cv_brier": r["mean_cv_brier"],
            "brier_model":  r["brier_model"],
            "brier_const05": r["brier_const05"],
            "brier_base_rate": r["brier_base_rate"],
            "brier_market": r["brier_market"],  # None = TODO
            "skill_score":  r["skill_score"],   # None = TODO
            "rider_b_brier": r["rider_b"]["brier_model"] if r.get("rider_b") else None,
            "rider_b_n":    r["rider_b"]["n_total"] if r.get("rider_b") else None,
        })
    print(json.dumps(summary, indent=2))

    print("\nDone.")


if __name__ == "__main__":
    main()
