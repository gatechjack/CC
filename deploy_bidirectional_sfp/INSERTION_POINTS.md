# Bidirectional SFP Deploy — Insertion Points & Design (SHOW-BEFORE-WRITE)

*2026-07-01. Read-only investigation on throwaway worktree `cc-sfp-deploy-wt` @ 79cbbef.
NOTHING written to the live path yet. Awaiting operator confirmation on the flagged forks.*

## Gates (all read-only, before any prod write)
- git `main == origin == 79cbbef` (task expected a73044e — moved via the concurrent Kalshi
  agent, as anticipated; a73044e is an ancestor).
- Code files match known-good EXACTLY (prod == main): detector `91fd7672`, observer
  `8a916526`, broker `4b00dea2`.
- Config files DRIFTED from the task's stated known-good, but prod == main and the drift is
  **Kalshi-only**: `strategies.yaml 1ec7832b→a9fb8c6b`, `divisions.yaml b2ac87cf→188794ad`.
  Diff `a73044e..79cbbef` = `7d87f1b` (kill-paper, already in 1ec7832b) + `79cbbef`
  (kalshi K5 live-flip). The **bitunix_sfp block is byte-unchanged** (BTC+ETH, side long, 2R,
  0.10/0.20/lev25). → base is coherent; needs operator OK to build on a9fb8c6b/188794ad.
- Engine: PID 39646, NRestarts 0, active, booted 2026-07-01 14:08:58 UTC.
- Local worktree observer md5 `b29213ff` = CRLF checkout of the LF blob `8a916526`
  (`tr -d '\r'` will restore 8a916526) — confirms the ship-LF-blobs discipline.

## ⚠ Anomaly: leverage location (task note is wrong for SFP)
`TIER_SIZING` (bitunix_futures_observer.py L208) is used ONLY by the **futures** observer —
`bitunix_sfp_observer.py` never imports it. For SFP, venue leverage flows:
`strategies.yaml leverage:` → `BitunixSfpConfig.from_dict` L182 → observer L493
`"leverage": self.config.leverage` → broker `_ensure_leverage` L1457 → `change_leverage` API.
**→ Set SFP leverage in strategies.yaml (leverage: 10.0). Do NOT touch futures TIER_SIZING**
(would not affect SFP and would wrongly change the futures division).

## PIECE 1 — insertion points (bitunix_sfp_observer.py, 910 lines)
- **Config `BitunixSfpConfig` (L108–187):** `side="long"` (L123/179) → regime-aware mode; add
  regime params (engine, slope window). `from_dict` (L147) parse them. Sizing scalars L124–126
  (`risk_pct_real/considerable/leverage`) come straight from yaml — set 0.05/0.10/10.0 there.
- **Detector setup (L224–251):** today per symbol `_detectors_b[wire]=[REAL,CONSIDERABLE]` on
  REAL bars. ADD a parallel `_detectors_b_short[wire]` fed REFLECTED bars (the research
  reflection: `2M − price`, H/L swap) → "long on reflected" = SHORT SFP. New state dict.
- **Bar feeding (`_warm_start_b` L281; live loop ~L354–400):** feed reflected 15m+3m bars to the
  short detectors (same M2 for 15m & 3m per symbol — reflection-midpoint stability = a design
  point to lock).
- **`_handle_signal` (L422–500) — the core gate insertion:**
  - REGIME + SIDE GATE go **after equity/geometry (~L438–463), before the ProposedOrder (L472)**:
    compute 15m EMA200+slope regime for the symbol; long signal kept only if regime∈{UP,RANGE},
    short only if regime∈{DOWN,RANGE}; never counter-trend → else skip+audit.
  - SHORT geometry: `compute_geometry` (vendored, L429) is long-only (stop below). Add an observer
    `_geometry_short(entry_ref, swept_high)` (stop ABOVE = swept_high+buf, tp BELOW) — the detector
    file stays byte-identical (91fd7672).
  - L465 concurrent guard `_has_open_live_same_side(sym,"buy")` → "sell" for shorts.
  - L472–499 `ProposedOrder(side="buy",…)` → `side="sell"`, stop_price above, take_profit below,
    short rationale; L493 leverage unchanged (yaml=10).
- **Regime data source:** EMA200 needs ≥232 15m bars; `bar_cache_max_bars=160` (L128) is too small.
  → compute regime from **`bitunix_bar_history`** (exists on prod, months of 15m) — NOT the cache.
- **Config change (strategies.yaml bitunix_sfp block):** add SOL/XRP to `symbols` + `symbol_modes`
  (arm:trading); `side: long` → regime-aware; `risk_pct_real 0.05` / `risk_pct_considerable 0.10`
  / `leverage 10.0`. (Surgical superset on prod content a9fb8c6b; SFP block only.)

## PIECE 2 — research-log table (proposed schema; SEPARATE from paper_trade_record)
New table `bitunix_sfp_research_log`, own fail-soft write path (try/except, never raises into the
order path), scoped `division='bitunix_sfp'`, symbol-keyed. Entry-insert + exit-update.
```
CREATE TABLE IF NOT EXISTS bitunix_sfp_research_log (
  id INTEGER PRIMARY KEY,
  division TEXT NOT NULL,            -- 'bitunix_sfp'
  coin TEXT NOT NULL, side TEXT NOT NULL,          -- symbol; long|short
  regime_label TEXT, regime_engine TEXT,           -- UP|RANGE|DOWN ; '15m_ema200_slope'
  rr_target REAL,                                  -- 2.0
  entry_ts TEXT, entry_px REAL, stop_px REAL, target_px REAL,
  sfp_sweep_px REAL, bos_confirm_px REAL, sfp_mode TEXT, bos_tf TEXT,
  htf_1h_ema200 REAL, htf_1h_slope REAL, htf_4h_ema200 REAL, htf_4h_slope REAL,
  htf_1d_ema200 REAL, htf_1d_slope REAL, htf_strength_score REAL,   -- LOGGED, not acted on
  exit_ts TEXT, exit_px REAL, realized_r REAL, closing_leg TEXT, duration_sec INTEGER,
  order_id TEXT,                                   -- link to live record (join, no schema change)
  extra_json TEXT
);
-- re-analyzable by (coin × regime × side × rr_target).
```

## PIECE 3 — regime-flip watch (proposed attach point)
Reuse Piece-1's per-symbol regime computation: track `self._last_regime[wire]`; on change emit a
row to `bitunix_sfp_regime_flip` (ts, coin, old→new) + an audit event / cockpit field. Read-only,
no trading logic. Highest-value event = any coin flipping to UP (the missing bull). Depends on
Piece 1's regime method (single source of truth).

## Forks needing operator confirmation BEFORE I write
1. Build on the drifted-but-coherent base a9fb8c6b/188794ad (Kalshi-only drift, SFP block clean)?
2. Leverage in strategies.yaml (=10), NOT futures TIER_SIZING — confirm (contradicts task note)?
3. Regime from `bitunix_bar_history` (not the 160-bar cache) — confirm?
4. GO on the live short-side build (reflected-detector shorts + un-reflected sell geometry +
   first-ever SFP sell order at the venue) — this is the load-bearing new logic; confirm scope.
```
