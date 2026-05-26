# Polymarket watchlist PnL-aggregation fix — plan (Board-gated)

**Date:** 2026-05-26
**Scope:** Read-only planning. No prod write since the failed 16m44s run that exposed the gap.
**Predecessor:** `reports/2026-05-26_polymarket_clustering_fix_plan.md` (Option A: dedupe by `(cid, outcome_index)`, shipped 22:20 UTC, commit `a4558fc`).
**Decision asked of Board:** ratify the decision-aggregated PnL fix on top of the clustering fix; redeploy via the same mechanism.

## TL;DR

The clustering fix counted decisions correctly but kept per-fill PnL math. Under `(cid, outcome_index)` windowing only the most-recent fill survives in the window, so its `size` (the divisor for PnL contribution) carries `1/N` of the decision's true economic exposure. The first prod fire ran 16m44s clean and produced a roster of **53** — outside the predicted **97-172** band — because the `$5k` PnL floor was rejecting cluster-heavy whales whose realized PnL got artifactually deflated 3-30x. The list's WR column is right (clustering signal works, no 100%-WR plague) but the PnL column lies.

Fix: **for each `(cid, oi)` decision in the window, sum size across ALL fills on that decision and use the size-weighted average price.** Math identity: `(1 - weighted_avg) * total_size ≡ sum_i (1 - p_i) * s_i` and the loss-side mirror, so `compute_polymarket_stats`'s per-row formula on aggregated rows is byte-for-byte the same number as a per-fill walk. Count axis (n = distinct decisions) stays as shipped; value axis (PnL/AvgPx) gets fixed.

Empirical replay against the same 329-wallet cached corpus the prior plan used: **cohort survives = 136 wallets** under `n≥10 + WR≥0.62 + $5k PnL` — squarely inside the 97-172 band. No floor re-tuning needed. Promotion stays paused until the Board ratifies AND the fix lands AND the next fire is verified.

## What went wrong on the 00:44 UTC fire

Floors held: `min_resolved_buys=10`, `provisional_threshold=50`, `min_windowed_wr=0.62`, `min_windowed_pnl=5000.0`. Drop_reasons:

| Floor | Pre-fix run (2026-05-23) | This fire (2026-05-26 00:44 UTC) |
|---|---|---|
| n_floor (n<10) | 249 | 586 (+135% — stricter under decision-counting; expected) |
| recency_floor | 115 | 105 |
| wr_floor (WR<0.62) | 1013 | 1180 (+17% — honest WR is lower than inflated 100%s; expected) |
| pnl_floor (PnL<$5k) | 815 | 509 (drops because more wallets fail upstream floors first) |
| **PASS** | **197** | **53** |

The visible drop from 197 → 53 isn't `1180 + 509` doing 1.5x what they did before — those numbers actually went DOWN. The shortfall comes from the cohort *being concentrated upstream*: wallets that used to pass `n+WR` under fill-counting now fail `n` because their fills collapsed to <10 decisions, OR fail WR because their inflated 100% deflated to honest 0.40-0.60. Of the ~171 that survived `n+WR` honestly, the $5k PnL floor — applied to per-fill-survivor PnL math — rejected 118 of them as "low realized PnL." Those rejections are the artifact.

## The PnL artifact, demonstrated

Pre-fix (Runaround example):
- Window = 100 most-recent BUYs, concentrated in 13 distinct decisions
- compute_polymarket_stats walks 100 rows, each contributing `(1-price)*size if win else -price*size`
- $44,000 realized PnL (sum over all 100 fills' contributions)

Post-clustering-fix, pre-PnL-aggregation (today's bug):
- Window = 100 distinct `(cid, oi)` decisions — for Runaround, his full corpus = 130 decisions, so the 100 picked include 60W/40L
- Each window row is the SURVIVOR fill (most-recent) of its decision
- compute_polymarket_stats walks 100 rows; each contributes `(1-price)*size`, but `size` is just THAT survivor fill's size, not the cluster's total
- For a 29-fill Knicks-spread cluster, only 1 fill's PnL contribution counts → ~1/29 of the decision's true economic value

The PnL the dashboard reports is no longer "PnL across the last 100 distinct decisions" — it's "PnL on one fill per decision," which is neither honest nor a useful screening signal.

## Math identity (why aggregation is the right fix)

For a single decision `d` with fills `(p_1, s_1), …, (p_n, s_n)`:

- **Total size:** `S_d = Σ s_i`
- **Size-weighted avg price:** `wavg_d = (Σ p_i · s_i) / S_d`
- **Per-row PnL formula applied to aggregated row** = `(1 - wavg_d) · S_d` (win) or `-wavg_d · S_d` (loss)

Expand the win case:
```
(1 - wavg_d) · S_d
  = S_d - wavg_d · S_d
  = (Σ s_i) - (Σ p_i · s_i)
  = Σ (1 - p_i) · s_i   <- per-fill PnL sum
```

Identical. The loss case mirrors. So if we synthesize one row per decision with `size = S_d` and `price = wavg_d` and feed it through the existing `compute_polymarket_stats` formula, we get exactly the sum the per-fill walk would produce. Counting math stays decision-level; valuation math becomes decision-level too. The two halves of the screening pipeline align.

## Implementation

**One new helper, one call-site change.** No new public API, no schema change, no test reshape.

### New helper: `_aggregate_window_to_decisions(activity, window) → list[ActivityRow]`

For each `(cid, oi)` survivor row in `window`:
1. Find all BUY+TRADE fills on the same `(cid, oi)` across the full `activity` feed
2. Compute `total_size`, `total_usdc_size`, `weighted_avg_price = Σ(p·s) / Σs`
3. Emit one synthetic `ActivityRow` via `dataclasses.replace(survivor, size=total_size, usdc_size=total_usdc_size, price=weighted_avg_price)` — all other fields preserved (notably `timestamp`, so `window_days_span` math is unchanged)

Edge cases handled:
- `total_size == 0`: keep survivor's price (degenerate guard)
- Empty activity: fall back to survivor unmodified
- SELL / REDEEM / non-TRADE rows excluded from the per-decision sum (entries only — exits aren't part of the BUY-side cost basis)
- Hedge `(cid, 0)` and `(cid, 1)`: separate buckets, valued independently
- Single-fill decision: synthetic row is byte-equivalent to the survivor

### Call site: one line added after `_select_resolved_buys_window`

```python
window = _select_resolved_buys_window(activity, resolutions, window_size=window_size)
n_resolved = len(window)
if n_resolved < min_resolved_buys: ...

# NEW: collapse each (cid, oi) decision in the window to a single synthetic
# row carrying ALL fills' aggregate size + weighted-avg entry price.
window = _aggregate_window_to_decisions(activity, window)

stats, _ = compute_polymarket_stats(... activity_rows=window, ...)
```

Downstream — `compute_polymarket_stats`, `avg_entry_price`, `share_below_70`, `window_days_span`, `last_trade_iso` — all stay as-is. They now operate on aggregated rows automatically.

### What changes for the screening columns

| Column | Before fix | After fix |
|---|---|---|
| `window_size_n` | distinct decisions (shipped) | distinct decisions — unchanged |
| `wins`, `losses`, `win_rate` | per-row sum across survivors | unchanged (a decision's outcome is the same regardless of fill aggregation) |
| `realized_pnl_usdc` | sum across N survivor rows (broken) | sum across N decisions, each at full economic exposure |
| `avg_entry_price` | mean of survivor fill prices | mean of decision weighted-avg prices |
| `share_below_70` | share of survivors with px<0.70 | share of decisions whose weighted-avg px<0.70 |
| `window_days_span` | max ts − min ts of survivors | unchanged (timestamps preserved on aggregated rows) |
| `total_usdc_size` | sum across survivor rows | sum across all fills on windowed decisions |

`avg_entry_price` and `share_below_70` shift slightly — they're now per-decision weighted statistics, which is semantically cleaner. Not a regression, an upgrade.

## Tests

8 new unit tests + 1 new integration test, all passing locally (34 total in `tests/test_polymarket_watchlist_seed.py`).

**Unit tests (`_aggregate_window_to_decisions`):**

- 29 winning fills aggregate to one row whose `(1-price)*size` matches the per-fill PnL sum exactly
- 29 losing fills aggregate to one row whose `-price*size` matches the per-fill loss sum exactly
- Mixed-price fills compute the size-weighted average correctly (and `(1-wavg)*total_size` matches the per-fill sum to floating-point precision)
- Hedge case: `(cid, 0)` and `(cid, 1)` aggregate independently — each side's size and price computed only from its own fills
- Single-fill decision passes through unchanged (size + price = survivor's)
- Survivor's `timestamp` preserved (so `window_days_span` is not perturbed)
- Empty activity falls back to survivor as-is (defensive)
- SELL / REDEEM rows on the same `(cid, oi)` are NOT summed into the BUY-side PnL (entries only)

**Integration test:** a clustered whale with 50 distinct winning decisions × 5 fills each at price 0.5 size 200 produces `$25,000` realized PnL (50 × $500), clearing the $5k floor. Without aggregation that same whale would report `$5,000` exactly — borderline-fail on rounding and be artifactually dropped. With aggregation the $25k genuine exposure is correctly surfaced.

All previously-passing tests still pass.

## Empirical replay

Same cached 329-wallet activity+resolutions corpus the predecessor plan used. Re-ran the new code path through `_aggregate_window_to_decisions`.

### Test-trader spot-check (unchanged outcomes — they still correctly drop on WR/n, not on PnL artifact)

| Trader | n | WR | PnL (aggregated) | Floors passed | Verdict |
|---|---|---|---|---|---|
| Runaround | 100 | 0.6000 | $51,859 | n + PnL | DROPS on WR (0.60 < 0.62 floor) |
| weflyhigh | 25 | 0.5600 | $286,777 | n + PnL | DROPS on WR (0.56 < 0.62 floor) |
| surfandturf | 5 | 0.4000 | $286,832 | PnL | DROPS on n (5 < 10 floor) |
| Mosley1 | 20 | 0.5000 | $939,241 | n + PnL | DROPS on WR (0.50 < 0.62 floor) |

All 4 cluster-traders still correctly drop — for the right reasons (honest decision WR is below floor, or n below floor). Their PnL is now accurately reported (e.g., Mosley1 at $939k aggregated reflects his true cluster-level realized exposure across resolved decisions), but they're disqualified by the WR/n axis, not by artifactual PnL. **This is the right outcome and matches the predecessor plan's intent.**

### Cohort survival under all four floors

| Filter | Count |
|---|---|
| n ≥ 10 | 304 (matches plan's 301) |
| n ≥ 10 AND WR ≥ 0.62 | **168** (matches plan's predicted 171 within minor cache-completeness drift) |
| n ≥ 10 AND WR ≥ 0.62 AND PnL ≥ $5k (PROD GATES) | **136** ← lands inside 97-172 band |
| + non-provisional (n ≥ 50) | 69 |

The PnL floor still bites a meaningful fraction (168 → 136, ~19% additional cut), but those rejections are honest now — they're wallets whose decisions on resolved markets really did carry under $5k of cumulative economic exposure across all fills. That's a defensible floor working as designed; not the artifact rejecting good whales.

### Sample of restored top survivors (under PnL aggregation)

| Whale | n | WR | Aggregated PnL |
|---|---|---|---|
| Magamyman | 98 | 0.6735 | $1,005,203 |
| Macks22 | 100 | 0.8000 | $355,382 |
| 65765757 | 100 | 0.8800 | $237,979 |
| aekghas | 10 | 0.9000 | $232,734 (provisional) |
| martingaleking | 44 | 0.7273 | $199,423 (provisional) |
| NeverYES | 16 | 0.9375 | $191,678 (provisional) |

Magamyman tops the list — same as under the pre-fix run; consistent. The high-edge whales who were correctly on the old list are back; the 100% WR plague stays gone.

## What the deploy looks like (not yet executed)

Same Board-gated mechanism as the clustering fix.

1. Cut commits on branch `pm-watchlist-pnl-aggregation-fix` (off `main`). Two commits expected: code+tests, and plan+replay-artifact.
2. Push branch to origin.
3. Snapshot current prod seed file (md5 `6b4372b7d38393c4b38a9d9999521dd5` from the clustering-fix deploy) with backup tag `.pre-pnl-aggregation-fix-20260526`.
4. Transfer the new LF blob via gz+b64 chunked az run-command (file is small, ~5 chunks at worst).
5. Move into place, chown root:root, chmod 644, md5-verify.
6. Smoke import via prod venv (verify `_aggregate_window_to_decisions` importable; functional smoke: 3 fills on same `(cid, oi)` aggregate to size=3*size correctly).
7. **No manual seed run this time either.** Reasoning: the manual run that produced today's 53-row list was operator-requested to surface this exact issue earlier than Sunday. With the fix in place, we don't need to re-run before the Sunday fire — Sun 2026-05-31 ~13:00 UTC is the next natural fire and produces the corrected list cleanly. Alternative: a manual `systemctl start` post-deploy if the operator wants verification before Sunday. (Cost: another ~17m wall-clock + extension lock juggling.)
8. Deploy_log entry + memory updates.

Promotion remains PAUSED through this deploy. Unpause is gated on post-deploy verification (next seed fire produces a list in ~97-172 range, top-of-list contains expected real-edge whales, no artifactual drops).

## Rollback footprint

Snapshots already in place:
- Pre-clustering-fix code: `.pre-clustering-fix-20260526` (md5 `0f38a83e…`) — full revert restores pre-2026-05-26 behavior.
- Pre-clustering-fix watchlist slot: `/tmp/backup_watch_only_whales_pre_clustering_fix_20260526.json` (md5 `1fcee3ba…`, 329 rows) — restores the buggy-but-not-this-broken roster.

To roll back the PnL-aggregation fix specifically: `mv .pre-pnl-aggregation-fix-20260526 <current_path>` restores the clustering-fix-only state (today's 53-row behavior).

## Decision asked

Board, please ratify:

1. **Adopt the decision-aggregated PnL semantics** as described above (one new helper, one call-site line). No floor re-tuning bundled.
2. **Deploy via the same mechanism as the clustering fix** (az run-command, chunked gz+b64, post-deploy md5 verify + smoke import + functional smoke).
3. **Choice between deploy paths:**
   - **(a)** Deploy now, manual `systemctl start` to validate this week (catches any live-API edge case the cached replay missed)
   - **(b)** Deploy now, ride the Sun 2026-05-31 ~13:00 UTC weekly fire (no manual run; lower extension-lock juggling cost)
4. **Promotion remains PAUSED** through deploy and post-fire verification.

I have no strong recommendation between (3a) and (3b) — the cached replay matches the math identity exactly, so the empirical risk of a surprise is low. (3a) gives one more confirmation cycle at the cost of another extension-lock cycle.

Anti-recommendations (decisions NOT to make this round):

- Do not lower the $5k PnL floor as a workaround. The aggregation fix is the right answer; lowering the floor would calibrate against broken PnL and bake in the bug.
- Do not change `_select_resolved_buys_window` (the count axis is correct, shipped, and verified).
- Do not bundle staleness fixes, AvgPx semantic discussions, or anything else with this fix. Single-purpose deploy.
