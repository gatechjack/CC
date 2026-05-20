# BitUnix audit-integrity reconciliation — TP fills vs recorded result

**Window:** 2026-05-17 05:14 UTC (trade-plan v2 flip) → 2026-05-20 05:00 UTC.
**Source:** read-only `sqlite3` against prod `/home/azureuser/trading_corp/data/trading_corp.db` via `az vm run-command invoke`; local read of `trading_corp/agents/paper_trade_replay.py` (md5-verified equal to prod earlier in deploy_log).
**Trigger:** observed contradiction. User reported 2 trades on 2026-05-19 progressing to TP2 (one to TP3). Prior review (`reports/bitunix_paper_data_review_2026-05-20.md`) reported only 2 v2-era trades total (both 2026-05-18, both -1.0R losses) and `position_sl_update = 0`. Both statements cannot be true.

---

## TL;DR — the prior reports are wrong about "no silent failures"

There is a **silent audit-logging failure** in the v2 multi-leg lifecycle. The audit trail records both 5/18 v2 trades as clean −1.0R full SL losses with `filled_legs: []`. Bar-history data on prod shows **Trade #1 actually hit TP1 on the entry bar itself and TP2 six minutes later** before reverting to SL — a partial-win outcome under Option C arithmetic worth ~+0.625R, NOT a −1.0R loss. The system never observed those TP fills, never advanced the SL, never wrote a single `position_sl_update` audit (0 such rows exist in the entire database, ever, for any trade).

Trade #2 is genuinely a loss — TP1 was missed by $3.97 and was never touched.

**Material delta on Trade #1: ~1.625R difference between recorded (−1.0R) and actual price action (+0.625R).** The prior funnel review's "infra healthy / no silent failures" framing is invalidated.

---

## Part 1 — True v2-era trade count

There are **exactly 2 v2-era trades.** Both happened on 2026-05-18. Both are tagged `tp_plan_version="v2"`. There are **zero** trades on 2026-05-19 (verified: `paper_trade_record` cross-division 5/19 query returned 0 rows).

The "5/19 observation" was the same 2 trades. Both were **open across the date boundary**:

| order_id | entered (UTC) | resolved (UTC) | open for |
|---|---|---|---|
| `35aa49c9-bb62-4084-865f-5d839515cd81` | 2026-05-18 16:24:02 | 2026-05-19 05:44:00 | 13 h 20 m |
| `a467e316-8889-4969-96d6-466865cb8046` | 2026-05-18 18:30:05 | 2026-05-19 07:50:00 | 13 h 20 m |

The prior review correctly counted 2 v2 trades. The `tp_plan_version='v2'` filter was *not* the bug — both trades carry that tag, both were found, both were resolved as losses. The bug is one level deeper.

---

## Part 2 — Reconciling TP progression against price truth

### Trade #1 — `35aa49c9` (sell @ 76,407.4, mc_b_sell_circle STANDARD)

Plan (from extra_json):
- SL 76,610.9 (swing-based, R = 203.5)
- TP1 76,269.87 (target_r 0.676, fraction 0.25, action `move_to_breakeven`)
- TP2 76,203.90 (target_r 1.000, fraction 0.50, action `move_to_tp1`)
- TP3 75,898.64 (target_r 2.500, fraction 0.25, action `trail_atr`)

Recorded result: `loss`, `actual_r_multiple: -1.0`, `result_price: 76,610.9037`, `result_ts: 2026-05-19 05:44:00`, **`filled_legs: []`**, `current_sl: 76,610.9037` (unchanged), `bars_to_resolution: 1`.

**Reality (from `bitunix_bar_history` 3m bars, 267 bars walked between entry and resolution):**

| Event | Price truth |
|---|---|
| TP1 (76,269.87) | **Hit on the entry bar itself** — 5/18 16:24 3m bar low = 76,255.0. Then 14 bars over the window with low ≤ TP1. |
| TP2 (76,203.90) | **Hit on the second 3m bar** — 5/18 16:27 low = 76,182.4. 6 bars with low ≤ TP2 across the window. |
| TP3 (75,898.64) | **Never hit.** Min low across window = 76,107.7 ($209 above TP3). |
| Entry SL (76,610.9) | First violation at 5/18 17:15 (high 76,665.2). |

If the v2 lifecycle had worked, Trade #1 should have been:
1. 5/18 16:24:00 — entry @ 76,407.4 (sell). Same bar low touches TP1 → fill 25% @ 76,269.87 (or earlier on 1m bar within), SL → entry 76,407.4 (BE).
2. 5/18 16:27:00 — next bar low 76,182.4 touches TP2 → fill 50% @ 76,203.90, SL → TP1 76,269.87 (locks in min 0.75R on the trade).
3. 5/18 17:15:00 — bar high 76,665.2 violates SL 76,269.87 → close remaining 25% at SL = TP1 price.
4. Expected realized R per Option C arithmetic: `0.25 × 0.676 + 0.50 × 1.00 + 0.25 × 0.676 ≈ +0.838R` (using the plan's actual `target_r` values, which are slightly elevated above the nominal 0.5/1.0/2.5 due to fee-floor scaling).

**Recorded as −1.0R loss. Delta = ~1.84R missed.**

### Trade #2 — `a467e316` (sell @ 76,319.1, mc_a_blood_diamond STANDARD)

Plan:
- SL 76,466.1 (atr_fallback, R = 147.0)
- TP1 76,181.73, TP2 76,172.09, TP3 75,951.58

Recorded result: `loss`, `actual_r_multiple: -1.0`, `result_price: 76,466.1085`, `result_ts: 2026-05-19 07:50:00`, **`filled_legs: []`**, `current_sl: 76,466.1085` (unchanged), `bars_to_resolution: 1`.

**Reality:**
- TP1 (76,181.73) **never hit** — minimum low across window = 76,185.7 (missed by $3.97).
- TP2 and TP3 also never hit (TP1 is the closest tier).
- SL hit at 5/18 19:00 (bar high 76,794.2).

**Trade #2 is a genuine loss.** No TPs filled in price action; the recorded result is correct (modulo the same `bars_to_resolution=1` artifact, which doesn't affect the win/loss verdict for this trade).

---

## Part 3 — Per-transition pass / fail

| Trade | Transition expected | Recorded? | Verdict |
|---|---|---|---|
| #1 `35aa49c9` | TP1 fill (5/18 16:24, low ≤ 76,269.87) | **NO** | **silent miss** |
| #1 | SL → entry on TP1 fill | **NO** | silent miss (cascades from above) |
| #1 | TP2 fill (5/18 16:27, low ≤ 76,203.90) | **NO** | **silent miss** |
| #1 | SL → TP1 on TP2 fill | **NO** | silent miss |
| #1 | TP3 fill | n/a — TP3 not actually hit in price action | (not a bug) |
| #1 | Final close at SL (which should have been TP1 price 76,269.87) | recorded as full original-SL hit at 76,610.9 | **wrong exit price** |
| #2 `a467e316` | TP1 fill | not expected — price truth shows no TP1 hit | (correct null) |
| #2 | SL hit | YES, recorded at 76,466.1 | **correct** |

`position_sl_update` audits across the entire DB, all-time: **0 rows**. Not "0 since the v2 flip" — **0 ever** for any trade on any division. The reconciler module ships with `_log_position_sl_update` wired, but no producer path has ever successfully written one.

---

## Bug class — where the failure lives

Routing condition at `trading_corp/agents/paper_trade_replay.py:745-749`:
```python
is_v2 = (
    row.division == "bitunix_futures"
    and bool(extra.get("tp_plan"))
    and extra.get("tp_plan_version") == "v2"
)
if is_v2:
    verdict = _classify_v2_multi_leg(row, bars, extra)
```

Routing matches both 5/18 trades — verified — so `_classify_v2_multi_leg` IS being called.

The classifier body (`paper_trade_replay.py:401-583`) walks `bars` in order. For each bar: check SL hit; check leg fills; on SL-hit return immediately with `bars_to_resolution = idx + 1`.

**Both trades resolved with `bars_to_resolution = 1`.** That means the very first bar walked at the resolving replay-tick had `high >= current_sl`. But:
- For Trade #1, the first 1m bar from `since_ms = entry_ts_ms (5/18 16:24)` should have high ≈ 76,419–76,482 (well below SL 76,610.9). The first bar should NOT have triggered SL.
- The same logic applies to Trade #2.

So either (a) the fetcher is not returning bars from `since_ms` onward as expected, (b) prior replay-ticks silently lost or never persisted the early-bar progress, or (c) some other path-condition in the classifier short-circuits before bars are walked. Diagnosing the exact root cause is out of scope per the task; the *audit-integrity* finding is decisive without it.

What I *can* state from the data:
- Audit `position_sl_update` count, all-time, all divisions: **0**.
- `filled_legs` on both v2 trades: **[]**.
- `current_sl` on both: **unchanged from original SL**.
- `bars_to_resolution`: **1** for both.
- `result_ts`: **5/19** for both (13h+ after entry).
- BTC bar history during the trade-#1 open window unambiguously shows price visiting both TP1 and TP2 before SL violation.

These facts are mutually inconsistent only if the v2 classifier is not walking the bars where TP fills happened. Most likely: the `_default_router_fetcher` → `_bitunix_kline_fetcher` path is returning the wrong slice (empty, single-bar, or starting too late) at the moment the replay-tick that finally resolves the trade runs. The audit-emission code itself (`_emit_audit` → `_log_audit`) cannot be exercised because `filled_legs` never grows.

This is a single bug class — *the v2 lifecycle path has never executed a TP fill in production* — manifesting as **(a)** wrong recorded outcomes on trades that traversed TP levels, **(b)** zero `position_sl_update` audits, **(c)** `bars_to_resolution=1` regardless of actual time-in-trade.

---

## Implications for prior reports

### `reports/bitunix_paper_data_review_2026-05-20.md` (commit `504c992`)

This report stated:
> **The infrastructure works.** Audit chain is intact end-to-end. … No silent failures.

This is **wrong**. The v2 trade-replay path is a silent audit failure. The "0 SL lifecycle events; consistent with `decide_sl_action()` being correctly idempotent" line is also wrong — the 0 count reflects the bug, not a quiet reconciler. The report needs a correction notice.

### `reports/bitunix_confound_and_fee_floor_2026-05-20.md` (commit `f6559ff`)

This report's Step 2 conclusion ("fee floor is functioning, not over-killing") stands — the fee-floor diagnosis is independent of the lifecycle bug. But the closing statement:
> Paper-mode v2 is functioning.

is wrong in a meaningful sub-component (the lifecycle classifier). The fee-floor reasoning is unaffected; the closing framing needs correction. The "watch-items" section already named "if a sustained bull regime arrives and PREMIUM-buy count stays at zero" — add to that list: "if any v2 trade resolves with filled_legs != []" — that would be the first positive evidence the v2 lifecycle has ever fired in prod.

Both reports should get a correction notice referencing this audit-integrity finding.

---

## What I did NOT do in this task

Per the read-only scope:
- I did not modify the v2 classifier, the fetcher, or any config.
- I did not run the v2 classifier locally with the bar data to confirm the predicted +0.838R outcome on Trade #1 (would need a fixture harness; out of scope for diagnosis).
- I did not pull 1m bars from the BitUnix kline endpoint directly to verify the fetcher returns the expected slice. The 3m bars from `bitunix_bar_history` are sufficient to prove the price action happened; whether the 1m fetcher sees it is a separate question that bears on root-cause but not on the audit-integrity verdict.
- I did not look at `paper_trade_record` columns beyond what the audit asks (no PnL accounting walkthrough).

These belong to a follow-up "fix the bug" task with explicit code-change scope.

---

## Operational consequences

1. **Every v2 trade outcome in the audit log is suspect.** With n=2 closed v2 trades so far, the impact is small (~1.84R missed on Trade #1; Trade #2 is genuinely correct). But the rate at which v2 trades accumulate will determine how badly the shadow-data record diverges from reality as time passes.
2. **The 60-day paper-cutover decision will be made on wrong data** if the bug persists. The v1.1 cutover memo's `[1.14, 2.63] PF prior` assumes the shadow data reports real outcomes. It does not.
3. **The dashboard's Trade Plan v2 panel** displays this same flawed audit data. Operators reading the dashboard see "0/2, 100% loss rate" — they're seeing the bug, not reality.
4. **`auto_execute: false` remains correctly set** — even if it weren't, the system has not yet reported a real positive-EV signal that would warrant the flip discussion. The bug doesn't expose capital.

## Recommended next task (separate, with explicit code-change scope)

Investigate the v2 replay classifier's bar-source path. Concretely:
1. Add a one-off debug log to `_classify_v2_multi_leg` that records `len(bars)`, `bars[0]` timestamp, and `current_sl` at the moment of SL-hit return. Re-run on the two existing trades' extra_json (need to add a CLI replay tool, or just inspect via a test fixture).
2. Verify the BitUnix kline endpoint returns the expected slice when queried with `startTime = 5/18 16:24:02` and `interval=1m`.
3. Confirm whether previous still-open ticks were correctly persisting `filled_legs` and `current_sl` updates — `_persist_extra_json` path at line 771 only fires if `verdict.extra_json_updates` is populated and `delta` is non-None.
4. Once root cause is identified: write a regression test that loads a synthetic `paper_trade_record` row mimicking Trade #1's plan + the actual 1m bars from 5/18 16:24-17:15, asserts the classifier returns `result='win'`, `actual_r_multiple > 0`, and emits ≥ 2 `position_sl_update` audits.
5. After the fix lands, audit any in-flight v2 trades (none exist currently — both are closed) and consider whether to re-tag the 2 closed trades with a `audit_corrected=true` flag or just memo the known-wrong outcome.

---

## Artifacts

- Query scripts: `tmp/bitunix_tp_audit_reconcile.sh`, `tmp/bitunix_tp_audit_part1.sh` through `..part6_routing.sh`.
- Prior reports referenced: `reports/bitunix_paper_data_review_2026-05-20.md`, `reports/bitunix_confound_and_fee_floor_2026-05-20.md`.

## Honest assessment

I wrote two prior reports that both concluded "infra is healthy." Both were wrong in the same way: I confirmed audit *self-consistency* (rows are there, fields are populated, counts add up) but did not verify *audit-vs-reality* alignment for the v2 lifecycle layer. The discipline carryover memory `verify-before-narrating` warned me about exactly this failure mode: "user trusts diffs, not narration. Run the verifying query before asserting facts you didn't directly observe."

The user's chart observation is the verifying signal I should have sought before writing "no silent failures." Future audit-integrity claims need cross-checks against an independent source (here: the bar-history table, which the audit pipeline doesn't write to itself).
