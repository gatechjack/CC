# Bidirectional Regime-Aware SFP — Implementation Plan

## Context
`bitunix_sfp` is today **long-only, fixed-side** — near-inert in the current bear
(pivot-50 fires ~5×/46d) and negative-expectancy when it does. Research (regime spikes)
points to **regime-aware side selection** (long-UP / short-DOWN / both-RANGE, never
counter-trend). We are treating the preliminary edge as a working signal and building the
real dataset by **going live**. This deploy makes the division regime-aware bidirectional
across **all 4 coins** with a **uniform base config** (no per-coin personalization —
insufficient n), learn-account sizing, **plus** the two real deliverables: a
**regime-stamped research log** (the months-long catalog) and a **regime-flip watch**
(catches the missing bull live). Detector stays byte-identical (`91fd7672`); all
regime/side logic lives in the observer. **GROSS metrics only** (operator handles fees).
Build read-only on throwaway worktree `cc-sfp-deploy-wt` (branch
`sfp-bidirectional-deploy-2026-07-01`); operator runs every prod write/restart.

## Base & fork calls (locked)
- Base **a9fb8c6b / 188794ad** (prod==main==`79cbbef`; drift is **Kalshi-K5 only**, the
  `bitunix_sfp` block is byte-unchanged). Code known-good: detector `91fd7672`, observer
  `8a916526`, broker `4b00dea2`.
- A: build on new base. B: SFP leverage in **strategies.yaml scalar** = 10.0 (NOT futures
  TIER_SIZING). C: full bidirectional. D: regime **engine-native** from the live bar cache.

## ⚠ Two dead-config traps (both confirmed; both matter)
1. **Leverage**: SFP reads `strategies.yaml leverage:` (observer `L493 config.leverage` →
   broker `_ensure_leverage` L1457). `TIER_SIZING` is **futures-only, never imported by the
   SFP observer**. → set `leverage: 10.0` in the yaml block; do NOT touch futures TIER_SIZING.
2. **Cache size**: `bar_cache_max_bars` (yaml/observer L128/L184) is **parsed but never
   consumed** — dead. The real 15m cache size is hardcoded `main.py:409 max_bars=160`.
   → engine-native EMA-200 requires **`main.py:409` 160→260** (feeds all four 15m caches).
   This realizes the operator's intent ("raise the cache to ≥240") via the *actual* knob.

## Critical files
- `trading_corp/agents/divisions/bitunix_sfp_observer.py` — regime, side-gate, short path,
  research-log write, flip-watch (most changes).
- `main.py` — `L409` cache 160→260; existing SFP wiring L676-691, boot prime L1930-1933.
- `trading_corp/agents/divisions/bitunix_position_reconciler.py` — research-log EXIT update.
- `trading_corp/web/sfp_cockpit_view.py` + `web/templates/sfp_cockpit/_state_board.html` — flip chip.
- `config/strategies.yaml` — SFP block superset.
- Reuse: `trading_corp/agents/strategies/_ta_helpers.py` `ema_series()` L49.
- UNCHANGED (assert md5): `trading_corp/agents/strategies/bitunix_sfp.py` `91fd7672`.

---

## Section 1 — Regime component (engine-native)
- New `BitunixSfpObserver._compute_regime(wire) -> "up"|"down"|"range"|None`, reads
  `self.bar_caches[wire].bars` (15m closes) — same cache the detector feeds from.
- **Cache**: `main.py:409` `max_bars 160→260`. Boot REST prime (`main.py:1932` `refresh`,
  `limit=260`) loads it in one shot; regime valid at boot **iff the venue returns ≥232** —
  verify via `last_refresh_count`/`status()` the same way `main.py:392-396` does for HTF caches.
- **Warmup gate**: return `None` when `len(closes) < 232` → side-gate SKIPs + audits
  `sfp_skip_regime_warmup` (fail-safe: never fire without a regime).
- **Formula (PARITY-LOCKED to research `ema200_pos_slope`)**: `em = ema_series(closes,200)`;
  `rising = em[-1] > em[-33]` (32-bar); UP = `close>em[-1] & rising`, DOWN =
  `close<em[-1] & falling`, else RANGE; last CLOSED 15m bar (k=1). Use `_ta_helpers.ema_series`.
  (NOT `linregress_slope` — the label must match the researched sign definition so backtest
  results transfer.)
- Perf/memory: 260 SfpBar × 4 coins ≈ ~100KB; O(260) recompute per sparse signal — negligible.

## Section 2 — Side-gate
- In `_handle_signal` after equity/geometry (~L438-463), before `ProposedOrder` (L472):
  `regime = _compute_regime(wire)`; `None` → skip. Side comes from which detector fired
  (long=real, short=reflected). Gate: long iff regime∈{up,range}; short iff regime∈{down,range};
  else skip+audit `sfp_skip_counter_trend`. Additive — the long path is preserved for up/range.

## Section 3 — SHORT build (deploy's core risk)
- **Detection**: feed **reflected** bars (`2M−price`, H/L swap) to a SECOND set of the
  **unchanged** `SfpModeBDetector` — add `_detectors_b_short[wire]` mirroring L224-251; the
  detector file stays byte-identical. A "long on reflected" == a short SFP on real bars.
  - **M2 stability (design + risk)**: FIX a per-symbol `M2` at detector init, constant for the
    detector's lifetime, used for BOTH 15m & 3m of that symbol. Never recompute as bars arrive
    (would corrupt swing/pivot state). Reflected prices may go negative (price>2M) — fine for
    comparisons; assert no positivity assumption in the fed path.
- **Geometry**: NEW observer helper `_geometry_short(entry_ref, swept_high)`: `stop =
  swept_high + buf·entry` (ABOVE), `r=stop−entry`, `tp = entry − tp_r·r` (BELOW). Do NOT touch
  vendored `compute_geometry` (long-only, stays byte-identical). `swept_high = M2 −
  sig.swept_low`; `entry_ref` = the REAL bar close at the signal's BOS ts (look up by ts).
- **Observer hardcoded-buy → side-derived**: L465 guard `"sell"`; L467 audit; L475 `side="sell"`;
  L478 rationale "short"; L482-483 stop/tp from `_geometry_short`.
- **Venue first-short (broker/reconciler already side-safe — confirmed):** broker
  `_build_order_body` passes `order.side` through and attaches `slPrice` gated only on
  `sl_px>0` with **no trigger-direction field** (venue infers from position side); TP `/tpsl/`
  leg is `positionId`-based; OCO is venue-managed on `positionId` (side-agnostic); reconciler
  auto-book is already `pnl=(entry−vwap) if sell`, `classify_result`/`exit_kind` side-branched
  → realized_R correct for shorts, **no change**. **The ONE unexercised behavior = a short's
  `slPrice` triggering on price RISING (venue-inferred, never done live)** → first-short A/B.
- One-way mode: RANGE both-sides could contend long vs short; the one-position-per-coin
  one-open invariant + the per-(symbol,side) guard prevent simultaneous opposite positions.

## Section 4 — Config superset (`strategies.yaml` bitunix_sfp block, base a9fb8c6b)
- `symbols`: add `SOL/USDT.P`, `XRP/USDT.P`. `symbol_modes`: add SOL+XRP `{bos_tf:3m, arm:trading}`.
- `side: long` → regime-aware (new key `side: regime`; observer `from_dict` L147 parses).
- `risk_pct_real 0.10→0.05`; `risk_pct_considerable 0.20→0.10`; `leverage 25.0→10.0`.
- Do NOT bump `bar_cache_max_bars` (dead) — cache change is `main.py:409` (Section 1).
- Diff gate: additive hunks touch ONLY the bitunix_sfp block; assert no other division changed.
- Ships WITH the observer+main.py code (sizing is load-once → the restart applies it).

## Section 5 — Research-log table (Piece 2, isolated)
- New `bitunix_sfp_research_log` (own DDL). Fail-soft write path mirroring `_ensure_watch_schema`
  (L844-854) + `_persist_tp` (L719-747, UPDATE-by-order_id). **Never touches `paper_trade_record`.**
- **ENTRY**: after `_place` L584 (`order.extra` + `fill.order_id` in scope) — INSERT (ts, coin,
  side, regime_label, regime_engine='15m_ema200_slope', rr_target=2.0, entry_px, stop_px,
  target_px, sfp_sweep_px, bos_confirm_px, sfp_mode, bos_tf, htf readings, order_id). Fail-soft.
- **HTF readings (logged-not-acted)**: 1H/4H/1D EMA200+slope + strength. **1D EMA-200 is
  infeasible from the 15m engine cache → compute the research-log HTF readings from
  `bitunix_bar_history` at log time** (out-of-band is acceptable HERE — it's diagnostic logging,
  NOT the trading regime; the engine-native constraint applies to the regime that PICKS SIDE).
  Fail-soft; NULL if unavailable.
- **EXIT**: reconciler after `_autobook_missing_close_real` UPDATE (L987) AND the estimate path
  (`_autobook_missing_close` L689) — UPDATE by order_id: exit_px, realized_R=`r_mult`,
  closing_leg=`exit_kind`, duration=`now−r["ts"]`. Fail-soft.
- Re-analyzable by (coin × regime × side × rr_target).

## Section 6 — Flip-watch (Piece 3, read-only)
- Reuse `_compute_regime(wire)`. Attach in `_process_symbol_b` after L402 (post-15m-close, per
  symbol; Mode B is the live path — both symbols `bos_tf:3m` → `run_loop_master`). Track
  `self._last_regime[wire]`; on change → INSERT `bitunix_sfp_regime_flip` (ts, coin, old, new,
  ema200, slope) + audit + cockpit. No trading logic. Highest value = any coin → UP.
- **Cockpit**: `sfp_cockpit_view.py` `_coin_state` L523-564 → `card["regime"]=_regime_state(...)`
  (fail-soft, mirror the `card["armed"]` stub L554-563); template `_state_board.html` `coincard`
  macro (badges L44-46 / monitoring L85-97); HTMX partial `/sfp/partials/state-board` L619-623.
  FLAG: web files may be prod-newer → drift-gate the cockpit hunk.

## Section 7 — Deploy sequencing & gates (operator runs writes/restart; staged .ps1)
Touched: `main.py`, `bitunix_sfp_observer.py`, `bitunix_position_reconciler.py`,
`sfp_cockpit_view.py` + `_state_board.html`, `config/strategies.yaml`. Detector UNCHANGED.
- **preflight.ps1**: parity (git main==origin; per-file prod md5 == staged base), drift-gate
  (snapshot prod md5s; abort if changed since staging), RH-pickle freshness (>~20h → refresh
  first, operator 2FA), SFP-flat via reconciler `match_count==0` (NOT `position WHERE qty!=0`;
  bitunix_futures may hold independently — isolated, does not block).
- **apply.ps1**: re-drift-gate → LF-blob install (`tr -d '\r'` + scp; md5-gate installed==target
  LF) → config-diff gate 1 (ONLY bitunix_sfp block changed) → assert detector md5 `91fd7672`.
- **restart.ps1**: re-check SFP flat + pickle fresh → ONE flat-guarded engine-level restart →
  both divisions reconcile post-boot.
- **boot-smoke.ps1 (gate 3)**: 0 tracebacks; both divisions reconcile clean; SFP armed on all 4
  coins; regime live (len≥232, label for all 4); research-log table exists; flip-watch emitting;
  cache depth ≥232 confirmed.
- Operator-paste: `powershell -ep bypass -f .\NAME.ps1` (one line ≤100 chars, ASCII-only .ps1,
  stdin-stream not ssh-arg).

## Section 8 — Test/parity plan (before arming live)
- Detector byte-identical: assert `bitunix_sfp.py` md5 `91fd7672` (LF).
- Regime parity: `_compute_regime` == research `regime_filter.regime_series('ema200_pos_slope')`
  label-for-label on the same 15m bars.
- Short-detection parity: reflected-bar detection reproduces the research `short_sfp_sweep`
  fires (ts + swept levels) on the same data.
- Short-geometry offline proof: `_geometry_short` → stop ABOVE, tp BELOW, r_unit>0, no sign
  error — BEFORE any real sell.
- Research-log round-trip (entry insert + exit update on a simulated fill); assert
  `paper_trade_record` untouched (isolation).
- Flip-watch: synthetic regime series → rows only at transitions.
- Full SFP observer suite == baseline + new tests, 0 regressions.

## First-live A/B verification (explicit gate)
First real fill per side: TP rests at venue with real `/tpsl/` id; OCO closes + B1 auto-cancels
(no orphan); auto-book at 2R; research-log row with correct regime stamp. **Watch a maiden SHORT
and a RANGE both-sides case hardest** (the short's slPrice trigger direction is the one
venue-unexercised behavior).

## Build order (piece-by-piece; each committed incrementally, GROSS-only, no prod writes)
0. (on approval) commit this plan as `deploy_bidirectional_sfp/IMPLEMENTATION_PLAN.md`.
1. Regime component + `main.py:409` cache + parity test.
2. Reflected short detectors + `_geometry_short` + offline proofs.
3. Side-gate wiring.
4. Research-log table + entry/exit write paths.
5. Flip-watch + cockpit chip.
6. Config superset.
7. RUNBOOK + 4 `.ps1` runners + full suite green.
Operator authorizes each piece before the next; nothing pushed/applied without your word.
