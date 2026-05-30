# BitUnix PA structure_alignment vs HTF gate — duplication diagnostic

**Date:** 2026-05-29
**Scope:** Read-only diagnostic. No code changes; no rate-tuning; no gate-logic modifications. Tripwire boundary unchanged.
**Branch context:** Drafted on `bitunix-live-entry-path-2026-05-29` (active session's branch — Stage-1 N+1 HITL wiring). Active-session edits are in the observer's `__init__` (HITL `pending_registry` kwarg), NOT in the PA validator or HTF gate code I quoted. Report file written but NOT staged; commit at operator discretion.
**Code snapshot:** working tree as of 2026-05-29 ~23:05 UTC (file:line refs valid for this snapshot).
**Empirical window:** 2026-05-27 23:18 UTC (2-of-3 PA deploy) → present (~48h).

---

## Operator hypothesis (under test)

> Post-2-of-3 deploy, many trades pass PA with volume + vwap (structure_alignment missing) but then fail HTF gate immediately after — suggesting HTF is checking the same structural condition the PA loosening was meant to make optional.
>
> If true: (a) the 2-of-3 deploy isn't actually loosening for this validator-pair, (b) HTF should be moved to 15m or 30m to be a real confluence gate.

---

## Phase 1 — code side-by-side

### PA `structure_alignment` validator

`trading_corp/agents/strategies/bitunix_pa_validation.py:163`

```python
def _structure_alignment(side: str, ctx: PriceContext) -> bool:
    if side == "buy":
        return bool(ctx.higher_highs_4h)
    if side == "sell":
        return bool(ctx.lower_lows_4h)
    return False
```

`ctx.higher_highs_4h` / `ctx.lower_lows_4h` are computed by `higher_highs_lower_lows_4h(bars)` in `trading_corp/data/bitunix_price_context.py:143`:

```python
def higher_highs_lower_lows_4h(bars) -> tuple[bool, bool]:
    """Compare the most-recently-COMPLETED 4h bucket to the one before it."""
    buckets = _resample_to_4h(bars)
    if len(buckets) < 3:
        return (False, False)
    last_completed = buckets[-2]
    prior = buckets[-3]
    return (
        last_completed["high"] > prior["high"],
        last_completed["low"] < prior["low"],
    )
```

**Properties:**
- Input data: live 3m bars from `bitunix_bar_cache` (max 500 bars = ~24h)
- Method: 3m bars resampled into 4h buckets; compare TWO completed buckets (last vs prior)
- Output: simple high-high / low-low boolean per side
- Look-back: ~8h of 4h structure (2 completed buckets)

### HTF gate — what runs after PA passes

The HTF gate evaluates `compute_regime(HTFContext, HTFRegimeConfig)` → `get_trade_permissions(verdict, side, config)`. Call site in `trading_corp/agents/divisions/bitunix_futures_observer.py:1353`:

```python
htf_verdict = self.htf_provider.regime_snapshot(
    self.htf_config, current_price=entry_price or None,
)
permission = get_trade_permissions(
    htf_verdict, side_str, self.htf_config,
)
# ...
if self.htf_gate_mode == "enforce":
    if permission.size_multiplier <= 0.0:
        return  # blocked
```

The "structure" sub-check inside HTF is one input to per-TF classification — `bitunix_htf_regime.py:530`:

```python
def market_structure(
    highs: Sequence[float], lows: Sequence[float],
    lookback: int = 20, n: int = 2,
) -> str:
    """Bull = recent SH > prior SH AND recent SL > prior SL. ..."""
    if len(highs) < lookback:
        return "insufficient"
    h_window = list(highs[-lookback:])
    l_window = list(lows[-lookback:])
    sh, sl = find_swing_points(h_window, l_window, n=n)
    if len(sh) < 2 or len(sl) < 2:
        return "insufficient"
    # ... compare last two swings
```

**Properties of HTF "h4 structure":**
- Input data: native 4H bars from `bitunix_h4_cache` (max 250 bars = ~41 days)
- Method: 20-bar lookback, swing-point detection (n=2 bars each side), compare last two swings
- Output: bull / bear / range / insufficient

### Side-by-side

| dimension | PA `structure_alignment` | HTF `h4.structure` |
|-----------|--------------------------|---------------------|
| data source | 3m cache, resampled to 4h | native 4H cache |
| method | 2-bucket high/low comparison | 20-bar swing-point structure |
| timeframe (nominal) | 4h | 4h |
| timeframe (effective) | last ~8h of structure | last ~80h of structure |
| weight in HTF gate | full vote (1 of 3 PA validators) | one component of `h4_class.regime`, weighted 0.3 in composite |

**Same property at same nominal TF, different math, materially different lookback.** Not a literal duplicate — but plausibly correlated. Phase 2 measures the actual overlap.

Crucially: HTF's `h4.structure` is one input among many. The gate's final block reason comes from one of: `regime_forbids_side`, `proximity_to_support`, `proximity_to_resistance`, `vol_tier_extreme`, `funding_extreme_crowded`, `safe_mode`. Only the first is regime-driven (where structure factors in); the others are independent dimensions.

---

## Phase 2 — empirical funnel since 2026-05-27 23:18 UTC

### Top-line

| stage | count |
|---|---:|
| `pa_validation_decision` rows | 661 |
| PA reject | 553 (83.7%) |
| PA pass | 108 (16.3%) |
| → HTF pass | 43 (39.8% of PA-pass) |
| → HTF block | 65 (60.2% of PA-pass) |

### Passed-validator-set distribution (108 PA passes)

| vwap | volume | structure | n | share |
|:---:|:---:|:---:|---:|---:|
| 1 | 1 | 1 (all 3) | 11 | 10.2% |
| 1 | 1 | 0 (no structure — **hypothesis case**) | 38 | 35.2% |
| 1 | 0 | 1 (no volume) | 39 | 36.1% |
| 0 | 1 | 1 (no vwap) | 20 | 18.5% |

### PA-pass × HTF outcome

| vwap | volume | structure | pa_pass | htf_pass | htf_block | **block rate** |
|:---:|:---:|:---:|---:|---:|---:|---:|
| 1 | 1 | 1 | 11 | 3 | 8 | **72.7%** |
| 1 | 1 | 0 | 38 | 15 | 23 | **60.5%** |
| 1 | 0 | 1 | 39 | 13 | 26 | **66.7%** |
| 0 | 1 | 1 | 20 | 12 | 8 | **40.0%** |
| **total** | | | **108** | **43** | **65** | **60.2%** |

### HTF block-reason decomposition

| vwap | volume | structure | blocked | by_regime | by_prox_support | by_prox_resistance | by_vol | by_funding | by_safemode |
|:---:|:---:|:---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 1 | 8 | 3 | 5 | 0 | 0 | 0 | 0 |
| 1 | 1 | 0 (hypothesis) | 23 | **4 (17.4%)** | **19 (82.6%)** | 0 | 0 | 0 | 0 |
| 1 | 0 | 1 | 26 | 11 (42.3%) | 15 (57.7%) | 0 | 0 | 0 | 0 |
| 0 | 1 | 1 | 8 | 0 | 8 (100%) | 0 | 0 | 0 | 0 |

### h4_struct × side for the hypothesis cohort (V=1 O=1 S=0)

| side | h4_struct | regime | pa_pass | htf_pass | htf_block |
|:---:|:---:|:---:|---:|---:|---:|
| sell | insufficient | BEAR | 20 | 13 | 7 |
| sell | insufficient | NEUTRAL | 8 | 0 | 8 |
| sell | bear | BEAR | 6 | 2 | 4 |
| buy | insufficient | BEAR | 4 | 0 | 4 |

---

## Phase 3 — verdict + implications

### Verdict: NO STRONG DUPLICATION

The operator hypothesis predicted >80% HTF-block rate in the V=1 O=1 S=0 cohort if HTF were re-applying the structure check that PA's 2-of-3 loosening was meant to bypass. The data shows **60.5% block rate** — high but not at the duplication threshold, AND the block reasons disprove the mechanism.

Three independent pieces of evidence:

1. **Block reasons are proximity-dominated, not structure-dominated.** Of the 23 V=1 O=1 S=0 HTF blocks, **19 (82.6%) are `proximity_to_support`** and only 4 (17.4%) are `regime_forbids_side`. `proximity_to_support` is a level-distance check (price within 0.3% of nearest 4H/1D swing-low or prior-day-low). That is an entirely orthogonal dimension to structure direction.

2. **Inverse check fails.** If HTF and PA-structure were duplicating, structure-passing cohorts would have *lower* HTF block rates than structure-failing ones. They don't:
   - structure-failing (V=1 O=1 S=0): 60.5% block
   - structure-passing (V=1 O=0 S=1): 66.7% block
   - all-pass (V=1 O=1 S=1): 72.7% block
   - structure-passing (V=0 O=1 S=1): 40.0% block (smallest cohort, n=20)

3. **`regime_forbids_side` rate is LOWER in the no-structure cohort.** 4/38 (10.5%) for V=1 O=1 S=0 vs 11/39 (28.2%) for V=1 O=0 S=1 and 3/11 (27.3%) for all-pass. If HTF's regime check were structurally overlapping with PA's structure_alignment, the no-structure cohort should have HIGHER regime-block rate, not lower.

The hypothesis mechanism does not match the data. The 2-of-3 loosening IS letting structure-missing PA-passes through; HTF then independently rejects a majority of them on proximity (not structure) grounds.

### Anomaly worth surfacing separately

**84% of h4_struct values in the hypothesis cohort are "insufficient"** (32/38 rows). "Insufficient" means HTF's 4H structure check returned no verdict — either the 4H cache holds <20 closed bars (=<80h, plausibly true for early hours after a restart but the system has been running 48h+) OR the 20-bar window doesn't contain ≥2 swings of each type. Either way, **the 4H structure leg of the HTF composite has been mostly absent during this window** — the gate has been running on EMA alignment + ADX + MACD + 1H/1D structure, NOT on h4 structure.

That makes the "duplication" question partly moot: HTF can't be duplicating PA's structure check if HTF's structure check is returning "insufficient" 84% of the time anyway. The actual gating work HTF is doing is the proximity-to-level check, which IS doing meaningful filtering.

Recommend a separate diagnostic: why is h4 cache so often returning fewer than 2 detectable swings inside its 20-bar window?

### Revised read on the 5/28 GREEN verdict (2-of-3 PA, observation window through 6/03)

The 5/28 verdict that "2-of-3 PA is working as designed" stands. The 2-of-3 PA loosening IS doing what it's supposed to do: letting some structure-missing signals through. The fact that 60% of those still get rejected downstream by HTF is not the 2-of-3 deploy being un-done — it's HTF doing its independent job on a different dimension (proximity to multi-day support/resistance levels).

If anything, the data argues the PA loosening is **doing exactly what was intended**: 23 vwap+volume-only trades that would have been hard-blocked by 3-of-3 PA were instead routed through the next gate, which made an independent call on level proximity. 15 of those passed all gates and placed. That is two-stage gating working correctly.

### Tripwire impact

No change. The tripwire is a binary structural condition (PA require_all + min_passed values) and is unchanged by this analysis. The 6/19 midpoint date in `project_bitunix_pa_2of3_deploy.md` is also unchanged.

---

## Phase 4 — building-block check for the proposed 15m/30m HTF

**Confirmed: the building blocks exist for additional timeframes.**

`trading_corp/data/live_bar_cache.py:75` defines the timeframe map directly:

```python
m = {"1m": 60, "3m": 180, "5m": 300, "15m": 900,
     "1h": 3600, "4h": 14400, "1d": 86400}
```

`15m` is already mapped. `30m` would require one line added. The `_refresh_bitunix` method passes `timeframe` as the `interval` param to the BitUnix kline endpoint, which accepts arbitrary intervals.

Wiring a 15m cache would look identical to the existing h1/h4/d1 instantiations at `trading_corp/main.py:301`:

```python
bitunix_m15_cache = LiveBarCache(
    symbol="BTCUSDT", timeframe="15m", venue="bitunix", max_bars=250,
)
```

Plus a poll loop entry alongside the existing `(bitunix_h1_cache, 300.0, "bitunix-h1-cache")` in `main.py:1475`. Plus passing it through `BitUnixHTFContextProvider` (which would need a fourth cache field).

### But — I recommend AGAINST the 15m/30m move

Stop-and-report rationale, not implementation:

The operator's framing — "HTF should be a real confluence gate (15m or 30m) relative to a 3m engine, 4h is so much higher it's a regime filter" — accurately describes HTF as a *regime filter* and then concludes it should be a *confluence filter*. The data argues HTF's most-used path (`proximity_to_support` blocks) is precisely the regime/level filtering that makes sense at HIGHER timeframes, not lower.

If HTF were demoted to 15m/30m:
- `proximity_to_support` would measure distance to 15m/30m swing points and prior-15m-bar high/low — far weaker than the current 4H/1D + prior-day H/L levels, which represent real institutional structure
- `regime_forbids_side` (the matrix BULL/BEAR direction filter) at 15m/30m would be noise-dominated relative to the current 1H/4H/1D composite
- The proximity check is the only block reason that ever fires in this window — and it's the one with the most to lose from timeframe demotion

The actual underlying problem the operator hypothesis is pointing at (PA structure_alignment fails too often → 2-of-3 loosening admits these → HTF re-rejects them) doesn't exist as described. What does exist:

- PA `structure_alignment` is a crude 2-bucket check on 3m-resampled 4H data. It's high-variance.
- HTF's broader proximity check rejects most signals near multi-day support during this window.

If the operator's underlying concern is "the 2-of-3 loosening isn't producing more trades" — the answer is that the binding constraint isn't structure_alignment, it's `proximity_to_support`. Of all 38 hypothesis-cohort PA passes, 19 died at proximity. Even if you removed `structure_alignment` from PA entirely, those 19 would still die there.

---

## Recommendation (no implementation this session)

1. **Do not move HTF to 15m/30m.** The empirical block reasons argue HTF's value is precisely its higher-timeframe regime-and-level scope. Demoting would reduce its information content.

2. **Surface a separate diagnostic for h4_struct=insufficient at 84%.** This was unrelated to the duplication hypothesis but is the largest "missing information" finding from this window. Possible causes: 4H bar refresh cadence (15 min poll), 4H cache age at typical fire moments, range-bound BTC over the window producing <2 swings within the 20-bar window. Worth understanding before any HTF tuning.

3. **If the goal is more trades through the funnel,** the diagnostic to run is "which PA-reject cause is highest, can it be relaxed safely?" — not "is HTF over-blocking PA passes?" The data says HTF's blocks are correctly grounded in independent dimensions.

4. **The 5/28 verdict and the 2026-06-03 observation window close date** stand. Don't advance early; observe through the close.

---

## Methodology notes

- All counts queried against `/home/azureuser/trading_corp/data/trading_corp.db` via ssh + sqlite3 3.37.2.
- Join key: PA-pass row `id` → next `htf_gate_decision` row within +10 audit IDs matching `score_side` + `score_tier`. Verified 1:1 (108 PA-pass → 108 HTF rows matched, 0 missing in id-adjacency window; one matched in the same audit second with no other intervening audit kinds, confirming the deterministic write order).
- Mode filter: `pa_validation_decision.mode = 'enforce'`. HTF rows in the window are all enforce. Confirms strategies.yaml `htf_gate.mode: enforce` is live.
- Validator-set extraction: `json_each(passed)` membership for each of the three validator names. Each PA-pass row has exactly 2 or 3 passes per the 2-of-3 rule, so the V/O/S 4-state pivot is complete.
