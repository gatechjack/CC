# BitUnix v2 lifecycle silent-logging bug — fix + reconciler

**Status:** SHIPPED to local main; NOT YET deployed to prod (per push gate). Bug is in `trading_corp/agents/paper_trade_replay.py::_bitunix_kline_fetcher`. The fixed code passes new + existing tests; the audit-vs-reality reconciler now correctly identifies the historical mis-record.

## Bug — confirmed root cause

The BitUnix Futures kline endpoint at `/api/v1/futures/market/kline` silently caps each response at **200 bars** regardless of the `limit` parameter, returning the newest bars within the requested `[startTime, endTime]` window in descending order. The legacy `_bitunix_kline_fetcher` treated "page returned fewer bars than requested" as end-of-data via `if len(page) < this_page: break`, exiting after one call.

For paper-mode v2 trades with `max_hold_seconds=86400` (24 h × 60 = 1440 1-min bars), the fetcher's one-shot request returned only **the newest 200 minutes** of the trade's life span — silently dropping ~85 % of the requested window. The v2 multi-leg classifier then walked a bar slice that did not overlap the trade's early/entry life, so TP1/TP2 fills that happened minutes after entry were never observed. The classifier saw only late bars where the original SL had long been violated and returned `loss` immediately on the first bar walked (`bars_to_resolution=1`).

**Probe evidence (2026-05-20, live BitUnix endpoint):**

```
GET /api/v1/futures/market/kline?symbol=BTCUSDT&interval=1m
    &startTime=1779121442000&endTime=1779181440000&limit=1000

→ code=0, msg='Success', rows_returned=200 (NOT 1000)
  newest bar (rows[0]) time = 1779181380000 = 5/19 09:03 UTC
  oldest returned (rows[-1])  = 1779169440000 = 5/19 05:44 UTC
```

The requested window was 5/18 16:24 → 5/19 09:04 (16.7 h). The server returned only 5/19 05:44 → 5/19 09:03 — exactly the newest 200 minutes. For Trade #1 (entered 5/18 16:24, resolved 5/19 05:44), every replay-tick saw a bar slice that started AT OR AFTER the trade's actual SL-violation timestamp, so the first bar walked always triggered the SL-hit return path.

## Trade-#1 / Trade-#2 asymmetry — explained without a second mechanism

Both trades had `bars_to_resolution=1` and `filled_legs=[]` — they were treated identically by the buggy fetcher. The asymmetry between recorded outcomes is reality-dependent, not bug-dependent:

- **Trade #1** had TP1+TP2 fills in the *missed early window* (5/18 16:24 → 5/19 05:43, where the buggy fetcher saw no bars). Reality says win +0.838R; recording says loss -1.0R. **Audit corrupted.**
- **Trade #2** had **no TP fills anywhere** in its life (TP1 missed by $3.97 — verified against bar history). The buggy slice happens to contain the SL-violation bar; the trade is a genuine SL hit regardless of window. **Audit happens to be correct.**

This is the cleanest available falsification of the alternative hypothesis ("the bug is more specific than 'always returns one bar'"). The bug is uniform; the consequence depends on whether reality had TP touches in the dropped window.

## Fix — minimal pagination correction

`trading_corp/agents/paper_trade_replay.py::_bitunix_kline_fetcher`:

- Slice the requested window into ≤200-bar sub-windows (matching the empirically-verified server cap).
- Iterate forward in time, advancing the cursor to each sub-window's `end_ms` regardless of how many bars came back. Gaps in the response do not imply end-of-data.
- Stop when cursor exceeds the requested total window end.

```python
SERVER_PAGE_CAP = 200
total_end_ms = since_ms + limit * tf_ms
cursor = since_ms
while cursor < total_end_ms:
    window_end = min(cursor + SERVER_PAGE_CAP * tf_ms, total_end_ms)
    # ... GET with startTime=cursor, endTime=window_end, limit=SERVER_PAGE_CAP
    # ... append page to out
    cursor = window_end
```

No other change to the lifecycle. The classifier (`_classify_v2_multi_leg`) was already correct — verified by `test_v2_buy_tp1_tp2_then_sl_at_tp1_floor_yields_0_75R` and the new `test_trade1_with_correct_bars_yields_partial_win`.

## Tests — failing-test-then-passing evidence

New test file: `tests/test_bitunix_kline_fetcher_pagination.py`. 6 tests, network-free (mocks `httpx.AsyncClient` with a `_FakeBitunixServer` that simulates the server's 200-bar cap + descending-newest-first ordering).

| Test | Pre-fix | Post-fix |
|---|---|---|
| `test_kline_fetcher_returns_full_window_when_server_caps_at_200` | **FAIL** (returned 200 bars; expected 1000) | **PASS** (returns 1000) |
| `test_kline_fetcher_short_window_uses_one_call` | pass | pass |
| `test_kline_fetcher_empty_server_response_returns_empty_list` | pass | pass |
| `test_trade1_reproduces_observed_bug_with_truncated_bars` | pass — reproduces the prod recorded outcome (loss / -1.0R / `filled_legs=[]` / `bars_to_resolution=1`) when classifier is fed the buggy single-bar slice | pass — bug repro is at the *classifier* layer with bad bars; classifier behavior is correct given that input |
| `test_trade1_with_correct_bars_yields_partial_win` | pass — confirms classifier produces TP1+TP2 fills + win when given proper bars | pass |
| `test_multi_tp_in_one_walk_yields_correct_sl_at_tp1_floor` (NEW) | pass | pass |

The full existing v2 lifecycle suite (`tests/test_paper_trade_replay.py`) — 27 tests — passes both before and after the fix. The fix only touches the fetcher's pagination.

The `test_multi_tp_in_one_walk_yields_correct_sl_at_tp1_floor` case explicitly covers the path that has never executed live: TP1 and TP2 filling within the same replay-tick walk. After the fix, the SL must end at TP1 floor (not stuck at entry, not double-moved) — assertion verified.

## Reconciler — durable safety net

New file: `scripts/audit_reality_reconciler.py`. For each closed v2 `paper_trade_record` row, the reconciler:

1. Pulls bars from `bitunix_bar_history` (3m) over the trade's `[ts, result_ts]` window — the persisted price-truth.
2. Calls `_classify_v2_multi_leg` with a fresh extra-state (`filled_legs=[]`, `current_sl=stop_price`) to simulate what a *non-buggy* replay would have produced.
3. Compares simulated vs recorded `result` + `actual_r_multiple`. Match-criteria: result string identical AND R within ±0.05 tolerance.
4. Reports per-trade MATCH or MISMATCH with discrepancy detail.

Exit code 0 if all match, 1 if any mismatch — wires into CI / cron as a gate.

### First-run output (against prod DB, 2026-05-20)

```
audit_reality_reconciler — 2 closed v2 trades scanned
matches: 1/2   mismatches: 1

✗ MISMATCH  35aa49c9-bb62-4084-865f-5d839515cd81  2026-05-18T16:24:02+00:00  sell
  recorded: result=loss R=-1.0
  simulated: result=win R=0.838 filled_legs=['tp1', 'tp2'] final_sl=76269.86667999999
  bars_walked: 266
  DISCREPANCY: result: recorded='loss' sim='win'; R: recorded=-1.0 sim=0.838 (delta=+1.8380); missed_legs: ['tp1', 'tp2']

✓ MATCH  a467e316-8889-4969-96d6-466865cb8046  2026-05-18T18:30:05+00:00  sell
  recorded: result=loss R=-1.0
  simulated: result=loss R=-1.0 filled_legs=[] final_sl=76466.10850649484
  bars_walked: 266
```

This is exactly the asymmetry predicted by the bug analysis.

## Data correction — closed trades re-tagged

Both v2 trades on prod were re-tagged with `audit_corrected=true` in `extra_json`, with the reconciler-verified corrected outcome recorded alongside. Original `result` + `actual_r_multiple` columns preserved for historical fidelity.

| order_id | recorded | corrected | delta |
|---|---|---|---|
| `35aa49c9-...` | `result=loss, R=-1.0` | `result=win, R=+0.838, filled_legs=['tp1','tp2'], current_sl=76,269.87` | **+1.838R** |
| `a467e316-...` | `result=loss, R=-1.0` | `result=loss, R=-1.0` (verified — TP1 missed by $3.97) | none |

## Re-stated v2-era record

Pre-correction: **0/2 wins, 100% loss rate** (both -1.0R).
Post-correction: **1/2 wins, ~50% (n=2 still uninformative)**. Sum R = +0.838 - 1.0 = -0.162R. Per-trade avg R = -0.08R.

Statistical reads on n=2 remain inadmissible — this is correctness, not performance.

## Decision summary

1. **Confirmed root cause:** BitUnix kline endpoint silently caps responses at 200 bars per call; the legacy `_bitunix_kline_fetcher` treated this as end-of-data, returning only the newest 200 minutes of any larger requested window. The v2 multi-leg classifier never saw the early bars where TP fills happened. Trade-#1 fails (TPs in dropped window); Trade-#2 succeeds (no TPs anywhere).
2. **Fix verified:** failing test reproduces bug pre-fix; same test passes post-fix; full v2 lifecycle suite (27 tests) and new pagination suite (6 tests) all pass; multi-TP-in-one-walk edge case covered and passes.
3. **Reconciler pass/fail:** 1/2 trades match recorded outcome; 1 mismatch correctly identified with R delta and missed legs. This is the durable check that would have caught the bug at trade-close time and that generalizes to unknown future bugs in the audit-vs-reality boundary.
4. **Corrected outcome for Trade #1:** `win / +0.838R`, filled_legs=[tp1, tp2], final SL at TP1 floor 76,269.87 (close at SL = +0.676R per leg-weighted Option C arithmetic). R delta = +1.838R vs recorded -1.0R.
5. **V2-era record re-stated:** 1 win / 1 loss, sum -0.162R. Not 0/2 -2.0R as previously reported.
6. **Correction notices: COMMITTED.** Headers prepended to both prior reports (`bitunix_paper_data_review_2026-05-20.md` from commit `504c992` and `bitunix_confound_and_fee_floor_2026-05-20.md` from commit `f6559ff`) pointing to this report and the audit-integrity finding. Push gate satisfied: the wrong "no silent failures" conclusion no longer sits unmarked on local main.

## Push gate

Local `main` is now ahead of `origin/main` by several commits. Push has been deliberately deferred per task instruction. Correction notices on both prior reports are now committed to local main alongside the fix. The push gate ("do not push until the correction notices are committed, so the wrong conclusion never sits unmarked on the shared branch") is satisfied.

The actual `git push` itself remains a separate user-driven action.

## Out of scope for this task (next-task candidates)

- **Deploy the fix to prod.** Requires the standard `az vm run-command` deploy procedure. `BitunixBroker.place_order` is still `NotImplementedError`, so this fix only changes paper-mode replay behavior — no live-capital effect. Recommended sequence: deploy with the `pre-v2-kline-fix-20260520-XXXX` backup tag pattern; verify via reconciler post-deploy.
- **Reconciler in CI/cron.** Wire `scripts/audit_reality_reconciler.py` into a daily check; alert on non-zero exit. Catches the next class of audit-vs-reality silent failure.
- **Dashboard surfacing of `audit_corrected` field.** When the Trade Plan v2 panel shows the closed-trade history, prefer `corrected_result`/`corrected_r_multiple` from `extra_json` when `audit_corrected=true`.
- **The `result_ts` puzzle.** Why is Trade #1's `result_ts` at 5/19 05:44 when the actual SL violation in price truth was at 5/18 17:15? Likely an artifact of replay-tick cadence (the trade was open across many ticks but the final resolving tick happened at 5/19 05:44 due to scheduling). Not a correctness issue post-fix but worth confirming once the fix is deployed.
