"""
S4 dataset builder: feature/label builder for kalshi_crypto_v2 model v1.

LEAKAGE RULE (critical):
  Features for a window are computed AS-OF the last fully-closed bar BEFORE the
  window opens: reference bar ts_ms <= open_ts_ms - 60000.
  All rolling indicators are computed causally on the full bar series first
  (pandas rolling is causal), THEN joined via merge_asof(direction='backward')
  using key = open_ts_ms - 60000.
  Post-join assertion: no feature row used a bar ts > open_ts_ms - 60000.

NO order/placement code anywhere in this file. READ-ONLY research only.
"""
from __future__ import annotations

import os
import sys
import sqlite3
import warnings
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# DB path
# ---------------------------------------------------------------------------
_LAB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAB_DB = os.path.join(_LAB_DIR, "lab", "kcv2_lab.db")


def _ro_conn(db_path: str = LAB_DB) -> sqlite3.Connection:
    """Open the lab DB read-only. Never write."""
    conn = sqlite3.connect("file:" + db_path + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# 1. Label builder (S1 rule)
# ---------------------------------------------------------------------------

def load_labels(asset: str, db_path: str = LAB_DB) -> pd.DataFrame:
    """
    Load S1 labels for an asset from lab_kalshi_markets.
    Returns DataFrame with columns:
      open_ts_ms, close_ts_ms, strike, settle, y, move_pct, market_ticker
    Skips rows where result not in {'yes','no'} or move_pct guard fails.
    """
    conn = _ro_conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT market_ticker, open_ts, close_ts, floor_strike, settlement_value, result
            FROM lab_kalshi_markets
            WHERE kind='15m' AND asset=? AND result IN ('yes','no')
            ORDER BY open_ts
            """,
            (asset,),
        ).fetchall()
    finally:
        conn.close()

    records = []
    for r in rows:
        strike = r["floor_strike"]
        settle = r["settlement_value"]
        if strike is None or settle is None:
            continue
        if strike == 0:
            continue
        # NOTE on move_pct: settlement_value in lab_kalshi_markets is the Kalshi binary
        # contract settlement (0.0=no, 1.0=yes), NOT the close-60s-avg RTI price.
        # Computing (settle - strike)/strike gives ≈ (0 or 1 - ~60000)/60000 ≈ ±1.0 —
        # a meaningless quantity physically. The actual close-60s-avg RTI is NOT stored
        # in this table (it would need to be derived from the cfbenchmarks feed or
        # back-computed from Binance bars). The spec's flat_sensitivity call is therefore
        # vacuously all-directional here: every window passes any |move|>0.02% threshold
        # because |move_pct| ≈ 1.0 >> 0.001. This is flagged in the report.
        # We compute move_pct as specified (for the record) but the flat_sensitivity
        # result is trivially all-directional.
        move_pct = (settle - strike) / abs(strike)
        y = 1 if r["result"] == "yes" else 0
        records.append(
            {
                "market_ticker": r["market_ticker"],
                "open_ts": r["open_ts"],          # seconds
                "close_ts": r["close_ts"],         # seconds
                "open_ts_ms": r["open_ts"] * 1000,
                "close_ts_ms": r["close_ts"] * 1000,
                "strike": strike,
                "settle": settle,
                "move_pct": move_pct,
                "y": y,
            }
        )

    df = pd.DataFrame(records).sort_values("open_ts_ms").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# 2. Bar feature builder (Binance 1m)
# ---------------------------------------------------------------------------

def _wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI (exponential smoothing, causal)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    alpha = 1.0 / period
    avg_gain = gain.ewm(alpha=alpha, adjust=False).mean()
    avg_loss = loss.ewm(alpha=alpha, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def build_bar_features(asset: str, db_path: str = LAB_DB) -> pd.DataFrame:
    """
    Compute all 1m-bar-derived features causally on the full Binance bar series.
    Returns a DataFrame indexed by ts_ms (the bar close timestamp).

    Features:
      ret_1m, ret_5m, ret_15m
      vwap_dist (60-bar rolling VWAP)
      rsi_14   [FLAG: causal feature-only; forward-looking in a live deployment
                 sense only if period is not strictly lagged — here it is causal]
      stoch_k_14 [FLAG: feature-only]
      rv_day, rv_week, rv_month (HAR RVs via 5m log returns)
      ema_trend (+1/0/-1 from 1h resampled close)
      hour_of_day, day_of_week (from ts_ms UTC)
    """
    conn = _ro_conn(db_path)
    try:
        df = pd.read_sql_query(
            "SELECT ts_ms, open, high, low, close, volume FROM lab_bars_binance "
            "WHERE asset=? ORDER BY ts_ms",
            conn,
            params=(asset,),
        )
    finally:
        conn.close()

    if df.empty:
        raise ValueError(f"No Binance bars for asset={asset}")

    df = df.sort_values("ts_ms").reset_index(drop=True)
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # --- returns ---
    df["ret_1m"] = close.pct_change(1)
    df["ret_5m"] = close.pct_change(5)
    df["ret_15m"] = close.pct_change(15)

    # --- rolling VWAP (60-bar) ---
    dollar_vol = close * volume
    roll_dollar = dollar_vol.rolling(60, min_periods=1).sum()
    roll_vol = volume.rolling(60, min_periods=1).sum()
    vwap_60 = roll_dollar / roll_vol.replace(0, np.nan)
    df["vwap_dist"] = (close - vwap_60) / vwap_60.replace(0, np.nan)

    # --- RSI 14 (Wilder, causal) [FLAG: feature-only] ---
    df["rsi_14"] = _wilder_rsi(close, 14)

    # --- Stochastic %K 14 [FLAG: feature-only] ---
    low_14 = low.rolling(14, min_periods=1).min()
    high_14 = high.rolling(14, min_periods=1).max()
    denom = (high_14 - low_14).replace(0, np.nan)
    df["stoch_k_14"] = 100.0 * (close - low_14) / denom

    # --- HAR realized vols (5m log returns, trailing 1d/7d/30d) ---
    # Resample to 5m bars (close of each 5m)
    df_idx = df.set_index(pd.to_datetime(df["ts_ms"], unit="ms", utc=True))
    close_5m = df_idx["close"].resample("5min").last().dropna()
    log_ret_5m = np.log(close_5m / close_5m.shift(1))

    # RVs as sqrt of sum of squared 5-min returns over trailing windows
    # 1d  = 288 bars of 5m,  7d = 2016,  30d = 8640
    # NOTE: resample labels bins on the LEFT edge; a 5m bin [T,T+5m) is labeled T
    # but its value uses data up to T+5m. shift(1) => at any 1m bar we only ever
    # see the last FULLY-COMPLETED 5m bin (no intra-bin look-ahead). ffill is then
    # strictly causal.
    rv_day_5m   = np.sqrt(log_ret_5m.pow(2).rolling(288,   min_periods=10).sum()).shift(1)
    rv_week_5m  = np.sqrt(log_ret_5m.pow(2).rolling(2016,  min_periods=50).sum()).shift(1)
    rv_month_5m = np.sqrt(log_ret_5m.pow(2).rolling(8640,  min_periods=100).sum()).shift(1)

    # Reindex back to 1m frequency (forward-fill: causal)
    rv_d = rv_day_5m.reindex(df_idx.index, method="ffill")
    rv_w = rv_week_5m.reindex(df_idx.index, method="ffill")
    rv_m = rv_month_5m.reindex(df_idx.index, method="ffill")
    df["rv_day"]   = rv_d.values
    df["rv_week"]  = rv_w.values
    df["rv_month"] = rv_m.values

    # --- EMA trend from 1h bars ---
    close_1h = df_idx["close"].resample("1h").last().dropna()
    ema9  = close_1h.ewm(span=9,  adjust=False).mean()
    ema21 = close_1h.ewm(span=21, adjust=False).mean()
    ema55 = close_1h.ewm(span=55, adjust=False).mean()
    trend_1h = pd.Series(0, index=close_1h.index, dtype=np.int8)
    trend_1h[( ema9 > ema21) & (ema21 > ema55)] =  1
    trend_1h[(ema9 < ema21) & (ema21 < ema55)] = -1
    # shift(1): a 1h bin labeled T (left edge) closes at T+1h; use only the last
    # COMPLETED hourly bin so a window inside the hour never sees its own close.
    ema_trend_1m = trend_1h.shift(1).reindex(df_idx.index, method="ffill")
    df["ema_trend"] = ema_trend_1m.values

    # --- Time features ---
    ts_dt = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
    df["hour_of_day"] = ts_dt.dt.hour
    df["day_of_week"] = ts_dt.dt.dayofweek

    df = df.set_index("ts_ms")
    feat_cols = [
        "ret_1m", "ret_5m", "ret_15m", "vwap_dist",
        "rsi_14", "stoch_k_14",
        "rv_day", "rv_week", "rv_month",
        "ema_trend", "hour_of_day", "day_of_week",
    ]
    return df[feat_cols].copy()


# ---------------------------------------------------------------------------
# 3. Flow feature builder (Coinalyze 1h, causal as-of)
# ---------------------------------------------------------------------------

def build_flow_features_1h(asset: str, db_path: str = LAB_DB) -> pd.DataFrame:
    """
    Compute flow features from Coinalyze interval='1hour' bars.
    Returns DataFrame indexed by ts_ms.

    Features:
      cvd_1h        = 2*buy_vol - vol
      oi_delta_1h   = oi_c[t] - oi_c[t-1]
      funding       = funding_c[t]
      funding_chg   = funding_c[t] - funding_c[t-1]
      liq_1h        = liq_long + liq_short
      ls_ratio      = ls_r[t]

    Missing values: left as NaN for GBM to handle natively.
    An is_missing_flow flag column is added to allow the model to distinguish
    true-zero from missing.
    """
    conn = _ro_conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT ts_ms, metric, value FROM lab_coinalyze
            WHERE asset=? AND interval='1hour'
            ORDER BY ts_ms
            """,
            (asset,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        # Return empty frame; caller will produce all-NaN flow columns
        return pd.DataFrame(
            columns=["cvd_1h", "oi_delta_1h", "funding", "funding_chg",
                     "liq_1h", "ls_ratio", "is_missing_flow_1h"]
        )

    raw = pd.DataFrame(rows, columns=["ts_ms", "metric", "value"])
    pivot = raw.pivot_table(index="ts_ms", columns="metric", values="value",
                            aggfunc="last").sort_index()

    out = pd.DataFrame(index=pivot.index)

    # CVD
    if "buy_vol" in pivot.columns and "vol" in pivot.columns:
        out["cvd_1h"] = 2 * pivot["buy_vol"] - pivot["vol"]
    else:
        out["cvd_1h"] = np.nan

    # OI delta
    if "oi_c" in pivot.columns:
        out["oi_delta_1h"] = pivot["oi_c"].diff()
    else:
        out["oi_delta_1h"] = np.nan

    # Funding
    if "funding_c" in pivot.columns:
        out["funding"] = pivot["funding_c"]
        out["funding_chg"] = pivot["funding_c"].diff()
    else:
        out["funding"] = np.nan
        out["funding_chg"] = np.nan

    # Liquidations
    liq_long  = pivot.get("liq_long",  pd.Series(dtype=float))
    liq_short = pivot.get("liq_short", pd.Series(dtype=float))
    if not liq_long.empty and not liq_short.empty:
        out["liq_1h"] = liq_long.reindex(out.index).add(
            liq_short.reindex(out.index), fill_value=0.0
        )
    else:
        out["liq_1h"] = 0.0

    # LS ratio
    if "ls_r" in pivot.columns:
        out["ls_ratio"] = pivot["ls_r"]
    else:
        out["ls_ratio"] = np.nan

    out["is_missing_flow_1h"] = 0  # rows present = not missing
    out.index.name = "ts_ms"
    return out


def build_flow_features_15m(asset: str, db_path: str = LAB_DB) -> pd.DataFrame:
    """
    Rider B: Coinalyze interval='15min' flow features.
    Returns DataFrame indexed by ts_ms.
    Features: cvd_15m, oi_delta_15m, ls_ratio_15m, is_missing_flow_15m
    """
    conn = _ro_conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT ts_ms, metric, value FROM lab_coinalyze
            WHERE asset=? AND interval='15min'
            ORDER BY ts_ms
            """,
            (asset,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return pd.DataFrame(
            columns=["cvd_15m", "oi_delta_15m", "ls_ratio_15m", "is_missing_flow_15m"]
        )

    raw = pd.DataFrame(rows, columns=["ts_ms", "metric", "value"])
    pivot = raw.pivot_table(index="ts_ms", columns="metric", values="value",
                            aggfunc="last").sort_index()

    out = pd.DataFrame(index=pivot.index)

    if "buy_vol" in pivot.columns and "vol" in pivot.columns:
        out["cvd_15m"] = 2 * pivot["buy_vol"] - pivot["vol"]
    else:
        out["cvd_15m"] = np.nan

    if "oi_c" in pivot.columns:
        out["oi_delta_15m"] = pivot["oi_c"].diff()
    else:
        out["oi_delta_15m"] = np.nan

    if "ls_r" in pivot.columns:
        out["ls_ratio_15m"] = pivot["ls_r"]
    else:
        out["ls_ratio_15m"] = np.nan

    out["is_missing_flow_15m"] = 0
    out.index.name = "ts_ms"
    return out


# ---------------------------------------------------------------------------
# 4. Cross-asset BTC features (for ETH/SOL/XRP)
# ---------------------------------------------------------------------------

def build_btc_features(db_path: str = LAB_DB) -> pd.DataFrame:
    """
    BTC lagged returns and 1h CVD for use in cross-asset feature set.
    Returns DataFrame indexed by ts_ms.
    Columns: btc_ret_1m, btc_ret_5m, btc_ret_15m, btc_cvd_1h
    """
    # BTC bar returns
    conn = _ro_conn(db_path)
    try:
        df = pd.read_sql_query(
            "SELECT ts_ms, close, volume FROM lab_bars_binance "
            "WHERE asset='BTC' ORDER BY ts_ms",
            conn,
        )
    finally:
        conn.close()

    df = df.sort_values("ts_ms").reset_index(drop=True)
    close = df["close"]
    df["btc_ret_1m"]  = close.pct_change(1)
    df["btc_ret_5m"]  = close.pct_change(5)
    df["btc_ret_15m"] = close.pct_change(15)
    btc_bar = df.set_index("ts_ms")[["btc_ret_1m", "btc_ret_5m", "btc_ret_15m"]]

    # BTC 1h CVD
    flow_btc = build_flow_features_1h("BTC", db_path)
    btc_cvd_1h = flow_btc[["cvd_1h"]].rename(columns={"cvd_1h": "btc_cvd_1h"})

    return btc_bar, btc_cvd_1h


# ---------------------------------------------------------------------------
# 5. Main assembler: build full dataset for one asset
# ---------------------------------------------------------------------------

def build_dataset(
    asset: str,
    db_path: str = LAB_DB,
    include_rider_b: bool = False,
) -> dict:
    """
    Build the full dataset for an asset:
      - Labels (y, move_pct, etc.)
      - Bar features (Binance 1m, causally computed)
      - Flow features (Coinalyze 1h, as-of)
      - Cross-asset BTC features (ETH/SOL/XRP only)
      - Rider B features (15m flow, if include_rider_b=True)

    Returns dict with keys:
      'df_v1': pd.DataFrame for model v1 (all windows with sufficient data)
      'df_riderb': pd.DataFrame for Rider B (windows in 15m flow coverage period)
      'feature_cols_v1': list of feature column names
      'feature_cols_riderb': list of feature column names (Rider B)
      'label_balance': dict
      'leakage_ok': bool (assertion result)
    """
    print(f"\n[build_dataset] asset={asset} ...")

    # --- Labels ---
    labels = load_labels(asset, db_path)
    print(f"  labels raw: {len(labels)} windows, y=1: {labels['y'].sum()} "
          f"({labels['y'].mean():.3f})")

    # join key: last bar ts <= open_ts_ms - 60s
    labels["join_key"] = labels["open_ts_ms"] - 60_000

    # --- Bar features ---
    bar_feats = build_bar_features(asset, db_path)
    bar_feats_reset = bar_feats.reset_index()  # ts_ms column

    # --- Flow features 1h ---
    flow_1h = build_flow_features_1h(asset, db_path)
    flow_1h_reset = flow_1h.reset_index() if not flow_1h.empty else pd.DataFrame()

    # --- Cross-asset BTC (for non-BTC assets) ---
    if asset != "BTC":
        btc_bar, btc_cvd = build_btc_features(db_path)
        btc_bar_reset = btc_bar.reset_index()
        btc_cvd_reset = btc_cvd.reset_index() if not btc_cvd.empty else pd.DataFrame()
        has_cross = True
    else:
        has_cross = False

    # --- Rider B: 15m flow ---
    if include_rider_b:
        flow_15m = build_flow_features_15m(asset, db_path)
        flow_15m_reset = flow_15m.reset_index() if not flow_15m.empty else pd.DataFrame()
    else:
        flow_15m_reset = pd.DataFrame()

    # -----------------------------------------------------------------------
    # As-of join: bar features (1m) -- join backward on join_key
    # -----------------------------------------------------------------------
    labels_sorted = labels.sort_values("join_key").reset_index(drop=True)
    bar_sorted = bar_feats_reset.sort_values("ts_ms").reset_index(drop=True)

    merged = pd.merge_asof(
        labels_sorted,
        bar_sorted,
        left_on="join_key",
        right_on="ts_ms",
        direction="backward",
    )
    # Rename bar ts_ms to avoid collision
    merged = merged.rename(columns={"ts_ms": "bar_ts_ms_used"})

    # --- Leakage assertion ---
    mask_valid = merged["bar_ts_ms_used"].notna()
    if mask_valid.any():
        max_bar_used = merged.loc[mask_valid, "bar_ts_ms_used"].max()
        min_join_key = merged.loc[mask_valid, "join_key"].min()
        leakage_violation = (
            merged.loc[mask_valid, "bar_ts_ms_used"] > merged.loc[mask_valid, "join_key"]
        ).any()
        leakage_ok = not leakage_violation
        if not leakage_ok:
            n_viol = (merged.loc[mask_valid, "bar_ts_ms_used"] > merged.loc[mask_valid, "join_key"]).sum()
            print(f"  *** LEAKAGE VIOLATION: {n_viol} rows used future bar! ***")
        else:
            print(f"  leakage assertion OK: max bar_ts_used={max_bar_used}, "
                  f"min join_key={min_join_key}")
    else:
        leakage_ok = True
        print("  warning: no bar matched (all NaN bar_ts_ms_used)")

    # --- As-of join: 1h flow ---
    if not flow_1h_reset.empty:
        flow_1h_sorted = flow_1h_reset.sort_values("ts_ms").reset_index(drop=True)
        merged = pd.merge_asof(
            merged.sort_values("join_key"),
            flow_1h_sorted,
            left_on="join_key",
            right_on="ts_ms",
            direction="backward",
        ).rename(columns={"ts_ms": "flow_1h_ts_used"})
        # Mark missing flow
        merged["is_missing_flow_1h"] = merged["is_missing_flow_1h"].fillna(1).astype(int)
        # Fill flow NaN with 0 for missing, leave non-missing NaNs for GBM
        flow_cols_1h = ["cvd_1h", "oi_delta_1h", "funding", "funding_chg",
                        "liq_1h", "ls_ratio"]
        for c in flow_cols_1h:
            if c not in merged.columns:
                merged[c] = np.nan
    else:
        for c in ["cvd_1h", "oi_delta_1h", "funding", "funding_chg",
                  "liq_1h", "ls_ratio", "is_missing_flow_1h"]:
            merged[c] = np.nan
        merged["is_missing_flow_1h"] = 1
        merged["flow_1h_ts_used"] = np.nan

    # --- As-of join: cross-asset BTC ---
    if has_cross:
        btc_bar_sorted = btc_bar_reset.sort_values("ts_ms").reset_index(drop=True)
        merged = pd.merge_asof(
            merged.sort_values("join_key"),
            btc_bar_sorted,
            left_on="join_key",
            right_on="ts_ms",
            direction="backward",
        ).rename(columns={"ts_ms": "btc_bar_ts_used"})

        if not btc_cvd_reset.empty:
            btc_cvd_s = btc_cvd_reset.sort_values("ts_ms").reset_index(drop=True)
            merged = pd.merge_asof(
                merged.sort_values("join_key"),
                btc_cvd_s,
                left_on="join_key",
                right_on="ts_ms",
                direction="backward",
            ).rename(columns={"ts_ms": "btc_cvd_ts_used"})
        else:
            merged["btc_cvd_1h"] = np.nan
            merged["btc_cvd_ts_used"] = np.nan
    else:
        for c in ["btc_ret_1m", "btc_ret_5m", "btc_ret_15m", "btc_cvd_1h"]:
            merged[c] = 0.0  # zero for BTC itself (not applicable)

    merged = merged.sort_values("open_ts_ms").reset_index(drop=True)

    # --- Drop rows with no bar match (windows outside bar coverage) ---
    n_before = len(merged)
    merged = merged[merged["bar_ts_ms_used"].notna()].reset_index(drop=True)
    n_after = len(merged)
    if n_before > n_after:
        print(f"  dropped {n_before - n_after} windows with no bar match")

    # -----------------------------------------------------------------------
    # v1 feature columns
    # -----------------------------------------------------------------------
    v1_bar_feats = [
        "ret_1m", "ret_5m", "ret_15m", "vwap_dist",
        "rsi_14", "stoch_k_14",           # FLAG: feature-only (causal but noted)
        "rv_day", "rv_week", "rv_month",
        "ema_trend", "hour_of_day", "day_of_week",
    ]
    v1_flow_feats = [
        "cvd_1h", "oi_delta_1h", "funding", "funding_chg",
        "liq_1h", "ls_ratio", "is_missing_flow_1h",
    ]
    v1_cross_feats = [] if asset == "BTC" else [
        "btc_ret_1m", "btc_ret_5m", "btc_ret_15m", "btc_cvd_1h",
    ]
    feature_cols_v1 = v1_bar_feats + v1_flow_feats + v1_cross_feats

    # Ensure all feature columns exist
    for c in feature_cols_v1:
        if c not in merged.columns:
            merged[c] = np.nan

    print(f"  v1 dataset: {len(merged)} windows, "
          f"y=1: {merged['y'].sum()} ({merged['y'].mean():.3f}), "
          f"features: {len(feature_cols_v1)}")

    df_v1 = merged.copy()

    # -----------------------------------------------------------------------
    # Rider B: restrict to 15m flow coverage window
    # -----------------------------------------------------------------------
    df_riderb = pd.DataFrame()
    feature_cols_riderb = []

    if include_rider_b and not flow_15m_reset.empty:
        min_15m_ts = flow_15m_reset["ts_ms"].min()  # ms
        rider_mask = merged["open_ts_ms"] >= (min_15m_ts - 60_000)
        df_rb_base = merged[rider_mask].copy().reset_index(drop=True)

        flow_15m_sorted = flow_15m_reset.sort_values("ts_ms").reset_index(drop=True)
        df_rb_base = pd.merge_asof(
            df_rb_base.sort_values("join_key"),
            flow_15m_sorted,
            left_on="join_key",
            right_on="ts_ms",
            direction="backward",
        ).rename(columns={"ts_ms": "flow_15m_ts_used"})

        df_rb_base["is_missing_flow_15m"] = df_rb_base["is_missing_flow_15m"].fillna(1).astype(int)
        df_rb_base = df_rb_base.sort_values("open_ts_ms").reset_index(drop=True)

        rider_extra = ["cvd_15m", "oi_delta_15m", "ls_ratio_15m", "is_missing_flow_15m"]
        for c in rider_extra:
            if c not in df_rb_base.columns:
                df_rb_base[c] = np.nan

        feature_cols_riderb = feature_cols_v1 + rider_extra
        df_riderb = df_rb_base
        print(f"  rider-B dataset: {len(df_riderb)} windows "
              f"(15m flow from {min_15m_ts}), "
              f"y=1: {df_riderb['y'].mean():.3f}")
    elif include_rider_b:
        print(f"  rider-B: no 15m flow data found for {asset}")

    # Label balance
    label_balance = {
        "n_total": len(df_v1),
        "n_yes": int(df_v1["y"].sum()),
        "n_no": int((df_v1["y"] == 0).sum()),
        "base_rate": float(df_v1["y"].mean()),
    }

    return {
        "df_v1": df_v1,
        "df_riderb": df_riderb,
        "feature_cols_v1": feature_cols_v1,
        "feature_cols_riderb": feature_cols_riderb,
        "label_balance": label_balance,
        "leakage_ok": leakage_ok,
    }


if __name__ == "__main__":
    for asset in ["BTC", "ETH", "SOL", "XRP"]:
        res = build_dataset(asset, include_rider_b=True)
        print(f"  label_balance: {res['label_balance']}")
        print(f"  leakage_ok: {res['leakage_ok']}")
