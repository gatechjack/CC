### FIRST USER MESSAGE (L4) ###
dangerously-skip-permissions


### LAST USER MESSAGE (L664) ###
Approve the plan with these modifications:

Pre-commit numeric thresholds for the backtest report before running it (define "acceptable comparison" precisely).
Add redeem_source: "immediate" | "deferred" to the confluence_gate_redeem audit payload.
Tighten CVD Factor 4 definition in the module docstring; resolve the bucket_minutes: 1 vs "3m bars in practice" ambiguity in the YAML.
Add boot-time warm-up ETA logging + dashboard banner.
Decide whether to split Phase D into D1 (logic) + D2 (dashboard) or to require a 24h observation period before considering the cutover complete. My preference is the split; defer to Board.
Decide whether to bootstrap 15m/5m caches from REST history at boot (separate work item; flag now).


### ExitPlanMode @ L658 (chars=23564) ###
# Plan — Replace BitUnix PA Validator with 5-Factor Confluence Gate

## Context

The BitUnix Futures division (3m scalping) currently uses a 3-validator `require_all=true` price-action gate (`evaluate_pa_validation` in `trading_corp/agents/strategies/bitunix_pa_validation.py`) running between the score engine and the HTF regime gate. The gate's `structure_alignment` validator is built on bucketed HH/LL comparisons that:

1. Use ambiguous swing detection that is lookback-sensitive
2. Operate on 4h (and tested-but-abandoned 1h) buckets while we trade on the 3m timeframe — wrong-scale filter
3. Flip inconsistently at bucket boundaries, causing missed-fire patterns documented in the 11/11 frozen-bucket prod observation and the [PA structure-TF backtest](../../cc/docs/memos/2026-05-18_pa_structure_backtest_results.md)

**Outcome we want:** Replace the HH/LL approach entirely with a deterministic 5-factor confirmation gate. Each factor contributes 0 or 1 point; trade fires when score ≥ 3-of-5. Deferred-fire mechanism preserved with refined exit semantics (4 exit conditions, 5 distinct audit kinds).

**Why this is a §4 change:** This is `bitunix_futures_observer.py` score→gate→risk path — CLAUDE.md §4 requires explicit Board approval before merging. PROJECT_CONTEXT.md §11 Backtester gate also applies: `reports/gate_backtest_2026-05-17.md` must show acceptable comparison vs the current PA gate before cutover.

## Architectural decisions (confirmed in plan-mode questions)

| Decision | Choice |
|---|---|
| Migration sequencing | **Atomic cutover in one PR.** No shadow-first. Tests + Backtester report green pre-commit. |
| Deferred-fire mechanism | **Keep.** 60s re-eval cadence preserved. Hard TTL added (15 min, tunable). |
| Exit conditions / audit kinds | **5 kinds:** `confluence_gate_decision` (every eval) + 4 exits: `_redeem` (passes), `_expired` (score decay), `_invalidated` (opposite-side; NEW kind, currently bundled), `_timeout` (hard TTL; NEW kind, no PA analog) |
| Backtest window | **2026-04-30 → 2026-05-17** (17 days, 1,796 alerts). Same window as commit `44a2395`. Caches already on disk. |
| Factor weighting | **Equal (binary 0/1 per factor) for v1.** Dynamic weighting / ML scoring explicitly out of scope. |
| Threshold | **min_gate_score=3** default, tunable in YAML. `gate_enabled=true` is emergency bypass. |
| CVD data source | **Tick-rule fallback** (close direction) — BitUnix has no aggressor-side trade stream. Log when fallback is in use; surface on dashboard; flag in audit row. |

## Phase A — Pure-function gate + TA helpers (no live wiring)

Build the 5-factor gate as a standalone, pure-function module. Old PA path keeps running.

### Files to create

**`trading_corp/agents/strategies/_ta_helpers.py`** — shared TA primitives:
- `sma(values, period)`, `sma_series(values, period)`
- `stdev(values, period)`
- `zscore(value, mean, stdev)`
- `linregress_slope(values)` — wrap `numpy.polyfit(range(n), values, 1)[0]` with guards
- `bollinger_band_width(values, period=20, n_stdev=2.0)`, `bb_width_series(...)`
- `percentile_rank(value, window)` — extends [`web/routes.py:2330-2343`](../../cc/trading_corp/web/routes.py)
- Re-exports `ema`, `_ema_series` (rename to `ema_series`), `atr` from [`bitunix_htf_regime.py:328-423`](../../cc/trading_corp/agents/strategies/bitunix_htf_regime.py)

**`trading_corp/agents/strategies/bitunix_confluence_gate.py`** — pure-function gate. Mirrors the shape of `bitunix_pa_validation.py`:
- `GateDecision` enum (PASS / REJECT / DISABLED)
- `ConfluenceGateConfig` frozen dataclass — `enabled`, `min_gate_score`, `gate_timeout_minutes`, per-factor sub-blocks (`ema_factor`, `vwap_factor`, `volatility_factor`, `cvd_factor`, `volume_z_factor`)
- `GateInputs` frozen dataclass — pre-computed factor inputs from caller
- `FactorResult` frozen dataclass — name + passed + detail dict (raw numbers for audit)
- `GateResult` frozen dataclass — decision, score, threshold, factors tuple, reason, `cvd_fallback_used`
- `evaluate_confluence_gate(*, inputs, config) → GateResult`
- Five private `_factor_*` functions, one per factor

**`tests/test_bitunix_confluence_gate.py`** — unit tests:
- 1 test per factor pass/fail (5 factors × ~3 cases each)
- 4 threshold tests: score=5, score=3, score=2, score=0
- `gate_disabled_returns_disabled`, `unknown_side_rejects`

**Tests green at end of Phase A:** new tests pass; all existing tests unchanged.

## Phase B — Bar cache wiring + price-context extension

### Files to modify

**`trading_corp/main.py:285-309`** — add two new `LiveBarCache` instances mirroring existing pattern:
```python
bitunix_15m_cache = LiveBarCache(symbol="BTCUSDT", timeframe="15m",
                                  venue="bitunix", max_bars=250)
bitunix_5m_cache  = LiveBarCache(symbol="BTCUSDT", timeframe="5m",
                                  venue="bitunix", max_bars=300)
```
Wire their `run_poll_loop` tasks alongside existing ones. Pass as ctor args to `BitunixFuturesObserver` (default `None`; observer doesn't consume yet).

**`trading_corp/data/bitunix_price_context.py`** — additive only:
- `prior_day_session_vwap(bars) → float | None` — extends the prior-day extraction pattern at [`bitunix_htf_context.py:363-373`](../../cc/trading_corp/data/bitunix_htf_context.py)
- `cvd_from_bars_tick_rule(bars, window_minutes) → tuple[float | None, bool]` — close-direction CVD slope; second tuple element always `True` (fallback flag)
- `build_gate_inputs(bar_3m, bar_5m, bar_15m, *, side, config) → GateInputs` — orchestrator that reads three caches and returns `GateInputs` ready for the gate

Do **NOT** modify `compute_price_context` yet — it still feeds the score engine independently of the gate.

### Files to create

**`tests/test_bitunix_gate_inputs.py`** — covers `prior_day_session_vwap`, `cvd_from_bars_tick_rule`, `build_gate_inputs` with full + empty caches.

### Files to modify (tests)

**`tests/test_boot_smoke.py`** — extend to assert 6 caches online (3m, 5m, 15m, 1h, 4h, 1d) and gate-config is parsed (in shadow / disabled state for now).

**Tests green at end of Phase B:** new tests + all existing + extended boot smoke. Live behavior unchanged.

## Phase C — Backtest harness extension + comparison run

### Files to modify

**`scripts/backtest_bitunix_confluence.py`** — add `--gate {pa_validation,five_factor,both}` flag (default `both`):
- For `five_factor` arm: at each alert ts, compute `GateInputs` from Coinbase OHLCV (resample to 5m + 15m using existing `_resample_to_*` helpers; add `_resample_to_5m` and `_resample_to_15m` if missing). Call `evaluate_confluence_gate`. Track gate-pass/fail per alert.
- Cross-tab outcomes: PA-pass × gate-pass × {fired, rejected, ...}
- Record per-factor pass rates + CVD-fallback usage rate
- Per-tier breakdown (PREMIUM / STANDARD / WEAK)

### Files to create

**`tests/test_backtest_bitunix_confluence_five_factor.py`** — at least 2 fixture tests:
- Synthetic bars where score=5 → expected fires count
- Synthetic bars where 3 EMAs misaligned → expected rejects count

**`reports/gate_backtest_2026-05-17.md`** — Backtester verdict (PROJECT_CONTEXT.md §11 deliverable):
- Window: 2026-04-30 → 2026-05-17 (17 days; 1,796 alerts)
- Methodology + caveats (Coinbase OHLCV vs BitUnix; CVD tick-rule fallback usage rate)
- Side-by-side comparison: win rate, profit factor, total trades, avg R-multiple, max DD
- 2×2 outcome cross-tab (PA-pass vs gate-pass)
- Per-factor pass-rate table
- Per-tier breakdown
- Recommendation: cutover / hold / iterate

**Tests green at end of Phase C:** new tests + all prior. Board reviews report. Atomic cutover commit BLOCKED until Board records approval in `runbooks/deploy_log.md` (planned entry).

## Phase D — Atomic cutover (single commit)

All file edits in one commit; full test suite green at the end.

### YAML config

**`config/strategies.yaml`** lines 1220-1231 (`bitunix_futures.pa_validation` block) — replace with `bitunix_futures.confluence_gate`:

```yaml
confluence_gate:
  enabled: true
  min_gate_score: 3              # 0..5; 3-of-5 default
  gate_timeout_minutes: 15       # deferred-fire hard TTL
  ema_factor:
    periods: [8, 21, 50]         # 15-min EMAs
    slope_lookback: 5            # linregress over last 5 EMA values
  vwap_factor:
    session_reset_hour_utc: 0
  volatility_factor:
    atr_period: 14               # 5m bars
    atr_sma_period: 50           # ATR > SMA(ATR, 50) for pass
    bb_period: 20
    bb_stdev: 2.0
    bb_pct_rank_window: 100
    bb_pct_rank_min_excluded_pct: 0.10   # reject if BB width in bottom 10%
  cvd_factor:
    slope_window_minutes: 15
    bucket_minutes: 1            # tick-rule uses 3m bars in practice
  volume_z_factor:
    period: 20                   # 3m bars
    min_z: 1.0
```

### Boot wiring

**`trading_corp/main.py:319-352`**:
- Delete PA import + `_pa_config = PAValidationConfig.from_dict(_bx_block)`
- Add `from trading_corp.agents.strategies.bitunix_confluence_gate import ConfluenceGateConfig`
- Add `_gate_config = ConfluenceGateConfig.from_dict(_bx_block)`
- Update observer ctor call: replace `pa_config=_pa_config` with `gate_config=_gate_config`; add `bar_cache_5m=bitunix_5m_cache`, `bar_cache_15m=bitunix_15m_cache`
- Update boot log line: print `gate_enabled` instead of `pa_enabled`

### Observer refactor

**`trading_corp/agents/divisions/bitunix_futures_observer.py`**:

Imports (lines 69-72) — replace PA import with:
```python
from trading_corp.agents.strategies.bitunix_confluence_gate import (
    ConfluenceGateConfig, GateDecision, evaluate_confluence_gate,
)
from trading_corp.data.bitunix_price_context import build_gate_inputs
```

Ctor (line 460, 498-501) — rename fields:
- `self.pa_config` → `self.gate_config: ConfluenceGateConfig | None`
- `self.bar_cache_5m`, `self.bar_cache_15m` (new ctor args)
- `_pending_pa_*` → `_pending_gate_*` (payload, side, cached_at_ts)

Helpers — rename + adapt:
- `_log_pa_validation` (727-768) → `_log_confluence_gate_decision`; audit kind `confluence_gate_decision`; payload includes `factors`, `score`, `threshold`, `cvd_fallback_used`
- `_clear_pending_pa` (770-779) → `_clear_pending_gate`
- `_log_pa_validation_redeem` (781-838) → `_log_confluence_gate_redeem`; audit kind `confluence_gate_redeem`
- `_backfill_redeem_order_id` (840-863) — kind string updated only
- `_log_pa_validation_expired` (865-912) — **split into TWO methods**:
  - `_log_confluence_gate_expired(reason='score_decay')` → audit kind `confluence_gate_expired`
  - `_log_confluence_gate_invalidated()` → audit kind `confluence_gate_invalidated` (opposite-side case)
- **NEW** `_log_confluence_gate_timeout()` → audit kind `confluence_gate_timeout`; written when cached payload ages past `gate_timeout_minutes`

Redeem loop (1005-1036):
- Rename `run_pa_redeem_loop` → `run_confluence_gate_redeem_loop`; keep 60s cadence
- On each tick: if `_pending_gate_payload is None` → continue. Else compute `delta_s`. If `delta_s > gate_timeout_minutes * 60`: write `_log_confluence_gate_timeout` + `_clear_pending_gate`. Else: invoke `_score_and_maybe_propose(payload, source="bar_tick_redeem")` (existing pattern).

Main gate invocation (1163-1196) — replace PA block with 5-factor block:
- Opposite-side check (mirrors current PA `opposite_side` logic): if `_pending_gate_side` is set AND ≠ current `side_str`, call `_log_confluence_gate_invalidated` + `_clear_pending_gate` BEFORE the gate call
- Build `gate_inputs = build_gate_inputs(...)` from three caches
- Call `evaluate_confluence_gate(inputs=gate_inputs, config=self.gate_config)`
- If `gate_result.cvd_fallback_used`: `log.info("...gate factor 4 used CVD tick-rule fallback...")`
- Write `_log_confluence_gate_decision` BEFORE branching (CLAUDE.md §1 audit-before-branch rule)
- If `htf_gate_mode='enforce'` AND `decision=REJECT`: cache payload (`_pending_gate_*`), emit `skipped_confluence_gate` score-decision audit, return
- If PASS / DISABLED: handle `redeem_metadata` block as today, calling `_log_confluence_gate_redeem` for bar-tick-redeem source

Score-decay branch (1135-1138): replace `_log_pa_validation_expired(reason='score_decay')` with `_log_confluence_gate_expired(reason='score_decay')`.

### Price-context cleanup

**`trading_corp/data/bitunix_price_context.py`**:
- Delete `_resample_to_4h` (108-140)
- Delete `higher_highs_lower_lows_4h` (143-165)
- Remove the call site in `compute_price_context` (~188) and its `hh4h`/`ll4h` fields on the return
- Keep `session_vwap`, `volume_above_20bar_avg`, `pct_change_in_window` (score engine consumes them independently)

### Dashboard

**`trading_corp/web/data.py`** — replace 3 view-builders:
- `build_bitunix_pending_pa_view` (1809-1899) → `build_bitunix_pending_confluence_view` — same shape + `seconds_until_timeout` countdown + `cvd_fallback_in_use` flag + `last_failed_factors` list
- `build_bitunix_pa_view` (1901-2045) → `build_bitunix_confluence_gate_view` — queries the 5 new audit kinds; counts include `by_factor_fail` dict + `cvd_fallback_pct`; rollups include `timeout_24h` + separated `expired_score_decay_24h` / `expired_opposite_side_24h`; recent-fires tables include `recent_timeouts`
- `build_bitunix_decision_flow_view` (2052-2200+) — replace `pa_validation_decision` query with `confluence_gate_decision`; replace `pa_validation_redeem` with `confluence_gate_redeem`; add a `confluence_gate_timeout` query so flow renderer can tag timed-out fires

**Templates:**
- **DELETE** `trading_corp/web/templates/partials/bitunix_pa_panel.html`
- **DELETE** `trading_corp/web/templates/partials/bitunix_pending_pa_panel.html`
- **CREATE** `trading_corp/web/templates/partials/bitunix_confluence_gate_panel.html` — modeled on `bitunix_pa_panel.html`; renders 5 factors checklist, score / threshold, 24h aggregates including timeout count, recent decisions / redeems / expired / timeouts tables
- **CREATE** `trading_corp/web/templates/partials/bitunix_pending_confluence_panel.html` — modeled on `bitunix_pending_pa_panel.html`; renders cached payload, side, seconds-waited, seconds-until-timeout countdown, last-failed factors
- **MODIFY** `trading_corp/web/templates/division.html` lines 220-226 — swap PA includes for new gate includes
- **MODIFY** `trading_corp/web/templates/partials/bitunix_decision_flow.html` lines 1-10 + body — replace PA section with confluence-gate section

### Tests

- **DELETE** `tests/test_bitunix_pa_validation.py` (entire file)
- **RENAME** `tests/test_bitunix_observer_pa_redeem.py` → `tests/test_bitunix_observer_gate_redeem.py`; rewrite assertions for 5 new audit kinds; add `test_gate_opposite_side_writes_confluence_gate_invalidated` and `test_gate_timeout_writes_confluence_gate_timeout`
- **MODIFY** `tests/test_bitunix_observer_pr3c_gates.py` — strip PA imports + assertions; keep HTF
- **MODIFY** `tests/test_boot_smoke.py` — assert 6 caches + `gate_enabled` in boot log
- **MODIFY** `tests/test_bitunix_view_builders.py` — update for new view-builder names + shapes

### File deletes

- `trading_corp/agents/strategies/bitunix_pa_validation.py`

### Memos archive

Move (don't delete) to preserve audit trail:
- `docs/memos/2026-05-18_pa_structure_tf_change.md` → `docs/memos/archive/`
- `docs/memos/2026-05-18_pa_structure_backtest_results.md` → `docs/memos/archive/`

`feedback_pa_gate_well_calibrated.md` in the memory dir (outside repo) — note in commit message that it'll be moved manually post-merge.

## Reuse map (file:line citations)

| Need | Existing helper | Action |
|---|---|---|
| EMA(8/21/50) | [`bitunix_htf_regime.py:328-341`](../../cc/trading_corp/agents/strategies/bitunix_htf_regime.py) — `ema()` | Re-export through `_ta_helpers` |
| EMA series for slope | [`bitunix_htf_regime.py:344-355`](../../cc/trading_corp/agents/strategies/bitunix_htf_regime.py) — `_ema_series()` | Re-export as `ema_series` |
| ATR(14) | [`bitunix_htf_regime.py:409-423`](../../cc/trading_corp/agents/strategies/bitunix_htf_regime.py) — `atr()` | Re-export |
| ATR from cache | [`live_bar_cache.py:225-257`](../../cc/trading_corp/data/live_bar_cache.py) — `LiveBarCache.get_atr` | Reuse for live path |
| Session VWAP | [`bitunix_price_context.py:46-73`](../../cc/trading_corp/data/bitunix_price_context.py) — `session_vwap()` | Reuse |
| Percentile helper | [`web/routes.py:2330-2343`](../../cc/trading_corp/web/routes.py) — `_percentile()` | Extend into `percentile_rank` |
| Prior-day extraction pattern | [`bitunix_htf_context.py:363-373`](../../cc/trading_corp/data/bitunix_htf_context.py) — `_prior_day_high_low` | Adapt for VWAP |
| Volume SMA pattern | [`bitunix_price_context.py:95-105`](../../cc/trading_corp/data/bitunix_price_context.py) — `volume_above_20bar_avg` | Reuse pattern for z-score |
| Backtest resample helpers | [`scripts/backtest_btc_accumulator.py:290-413`](../../cc/scripts/backtest_btc_accumulator.py) — `_resample_to_4h`, `_resample_to_1h`, `volume_above_20bar_avg_at`, `session_vwap_at` | Reuse; add `_resample_to_5m` + `_resample_to_15m` |

## Risk callouts

| Risk | Severity | Mitigation |
|---|---|---|
| Coinbase vs BitUnix data fidelity | Medium | Document in report. Same issue as PA arm — apples-to-apples relative comparison. |
| CVD tick-rule fallback fidelity | Medium-High | 3m close-direction is coarse. Log `cvd_fallback_used=True` on every eval; surface on dashboard; flag in audit. Future enhancement: WebSocket trade stream. |
| 15m + 5m cache warm-up | Medium | EMA50 on 15m needs ~50 bars (~12.5h). At fresh boot, factors with `None` inputs return `passed=False`; gate score is low; deferred-fire retries until caches warm. Document the warm-up window in deploy log. |
| Audit kind backward-compat | High | Old `pa_validation_*` kinds will no longer be written. **Pre-cutover: `Grep` for `pa_validation_decision`, `pa_validation_redeem`, `pa_validation_expired` outside deleted files. Update or note in deploy log.** Old rows stay readable for historical audit; only produce side changes. |
| Parallel sessions editing observer/main.py | Medium | Cutover commit may conflict. Coordinate timing in Board channel; rebase last-mile. |
| Config typo silent degradation | Low | YAML hot-reload has no validator (sharp edge). `ConfluenceGateConfig.from_dict` MUST `log.warning` on unknown keys. |
| Risk gate, schemas, auto_execute_caps | None | All unaltered. Gate runs BEFORE `RiskAgent.evaluate()`. No new tables. No `proposed_order` / `position` shape change. Paper-only division stays paper-only. |

## Verification plan

| Verification | Phase | Where |
|---|---|---|
| Unit tests per factor | A | `tests/test_bitunix_confluence_gate.py` |
| Threshold integration tests (5/3/2/0) | A | same |
| `build_gate_inputs` + prior-day VWAP + CVD tick-rule | B | `tests/test_bitunix_gate_inputs.py` |
| Boot smoke (6 caches + gate config logged) | B | `tests/test_boot_smoke.py` |
| Backtest 5-factor arm fixture tests | C | `tests/test_backtest_bitunix_confluence_five_factor.py` |
| Backtest comparison run | C | `reports/gate_backtest_2026-05-17.md` |
| Observer redeem-loop (pass / expire / invalidate / timeout) | D | `tests/test_bitunix_observer_gate_redeem.py` |
| Dashboard view-builders | D | `tests/test_bitunix_view_builders.py` |
| Dashboard render smoke (manual) | D | `curl localhost:.../division/bitunix_futures` after first paper-fire |
| Full pytest suite | end of D | `pytest tests/` |
| Backtester gate (PROJECT_CONTEXT.md §11) | pre-D-commit | Board reviews report; approval recorded in deploy log |
| Pre-cutover grep for old audit-kind consumers | pre-D-commit | `Grep -r "pa_validation_decision\\|pa_validation_redeem\\|pa_validation_expired"` outside files being deleted |

## Estimate

LOC delta: **+2,760 / −1,638 = +1,122 net**. Time: 27–36 hours focused work across multiple sessions, plus calendar time for Board approval between Phase C and Phase D.

## Critical files (canonical list)

| Path | Action |
|---|---|
| `trading_corp/agents/strategies/bitunix_confluence_gate.py` | **CREATE** |
| `trading_corp/agents/strategies/_ta_helpers.py` | **CREATE** |
| `trading_corp/agents/strategies/bitunix_pa_validation.py` | **DELETE** (Phase D) |
| `trading_corp/agents/divisions/bitunix_futures_observer.py` | **MODIFY** (Phase D atomic cutover) |
| `trading_corp/main.py` | **MODIFY** (Phase B caches + Phase D config) |
| `trading_corp/data/bitunix_price_context.py` | **MODIFY** (Phase B extend + Phase D strip 4h HH/LL) |
| `config/strategies.yaml` | **MODIFY** (Phase D YAML cutover) |
| `trading_corp/web/data.py` | **MODIFY** (Phase D 3 view-builders) |
| `trading_corp/web/templates/partials/bitunix_pa_panel.html` | **DELETE** (Phase D) |
| `trading_corp/web/templates/partials/bitunix_pending_pa_panel.html` | **DELETE** (Phase D) |
| `trading_corp/web/templates/partials/bitunix_confluence_gate_panel.html` | **CREATE** (Phase D) |
| `trading_corp/web/templates/partials/bitunix_pending_confluence_panel.html` | **CREATE** (Phase D) |
| `trading_corp/web/templates/partials/bitunix_decision_flow.html` | **MODIFY** (Phase D) |
| `trading_corp/web/templates/division.html` | **MODIFY** (Phase D, partial includes) |
| `scripts/backtest_bitunix_confluence.py` | **MODIFY** (Phase C) |
| `tests/test_bitunix_confluence_gate.py` | **CREATE** (Phase A) |
| `tests/test_bitunix_gate_inputs.py` | **CREATE** (Phase B) |
| `tests/test_bitunix_observer_pa_redeem.py` → `test_bitunix_observer_gate_redeem.py` | **RENAME + REWRITE** (Phase D) |
| `tests/test_bitunix_pa_validation.py` | **DELETE** (Phase D) |
| `tests/test_bitunix_observer_pr3c_gates.py` | **MODIFY** strip PA, keep HTF (Phase D) |
| `tests/test_boot_smoke.py` | **MODIFY** (Phase B extend, Phase D update) |
| `tests/test_bitunix_view_builders.py` | **MODIFY** (Phase D) |
| `tests/test_backtest_bitunix_confluence_five_factor.py` | **CREATE** (Phase C) |
| `reports/gate_backtest_2026-05-17.md` | **CREATE** (Phase C — Backtester deliverable) |
| `docs/memos/archive/2026-05-18_pa_structure_tf_change.md` | **MOVE** from `docs/memos/` (Phase D) |
| `docs/memos/archive/2026-05-18_pa_structure_backtest_results.md` | **MOVE** from `docs/memos/` (Phase D) |

## Pre-execution checks before each phase

- **Before Phase A:** `Grep` confirm `ema`, `_ema_series`, `atr` signatures in `bitunix_htf_regime.py` haven't drifted from what's quoted here.
- **Before Phase B:** `Read` `main.py:280-355` and check no parallel session has edited the cache-instantiation block.
- **Before Phase C:** confirm `data/historical_alerts/cache_alerts_20260430_20260517.json` is still on disk (left over from commit `44a2395`).
- **Before Phase D atomic cutover:**
  1. **Board approval recorded** for the gate cutover in `runbooks/deploy_log.md` (planned entry).
  2. **Backtester report shows acceptable comparison.** Stop and discuss if backtest shows the 5-factor gate is materially worse than PA on the 17-day window.
  3. **Grep for any orphan consumers of the old PA audit kinds** outside the files being deleted/modified.
  4. **Verify no parallel session is mid-edit on `bitunix_futures_observer.py`, `main.py`, `strategies.yaml`, `web/data.py`** — per the parallel-session-stop-and-discuss memory.
  5. **md5-diff** local files against prod before deploy to make sure the cutover targets are reachable via `az vm run-command invoke` patches (SSH still blocked).

## Out of scope (do not implement now)

- Order block / fair-value-gap detection
- Meta-labeling / ML scoring layer
- Dynamic factor weighting
- WebSocket aggressor-side CVD stream (CVD stays on tick-rule fallback for v1)
- 15m / 5m bar caches consumed by anyone other than the gate

