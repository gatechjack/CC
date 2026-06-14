# Bitunix combined deploy+restart — batch plan (PREPARED; NOT executed)

**Prepared 2026-06-14 — read-only + draft only. NO deploy, NO restart, NO prod write, NO query execution this session.** All steps below are **operator-gated**. Disclosure per `82fda13` at the bottom.

The P1 reconciler fix needs a restart to load, and a restart bounces the whole live engine — so any OTHER ready-and-reviewed Bitunix code is batched into the **one** deploy+restart.

---

## 1. Deploy-batch recommendation

### IN the batch (CODE — needs the restart to take effect)
| item | files (live engine) | branch / state |
|---|---|---|
| **P1 reconciler fix** | `trading_corp/brokers/bitunix.py`, `trading_corp/agents/divisions/bitunix_position_reconciler.py` | `bitunix-p1-reconciler-fix-2026-06-14` (`8b78da8`) — **reviewed, ready**; 14 new tests + full gate zero-new-regressions |
| **kalshi-sports-arb disable** ("turn off `kdis.sh`") | _PENDING — being prepared in another session_ | **fold its reviewed file(s) into the same md5-gate/backup/atomic-mv + the single restart.** AWAITING its file list + reviewed status before the batch is final. Likely a `config/strategies.yaml` enable-flag flip and/or an observer/script change — confirm from that session. |

### NOT a deploy (DOCS / ANALYSIS — merge to main, no restart)
Confirmed by three-dot diff (`git diff main...<branch>`): none touch `trading_corp/` live code — only `BACKLOG.md`, `reports/`, `runbooks/deploy_log.md`, and **repo-root analysis harnesses** (`fgharness.py`, `etharness.py`, `introspect_dbs.py`, `q*.sh/out`) which run locally, not in the engine:
- `bitunix-fee-gate-analysis-2026-06-14`
- `bitunix-entry-timing-analysis-2026-06-14`
- `bitunix-first-fill-closeout-2026-06-14`
- `bitunix-backtest-infra-inventory-2026-06-14`

(`bitunix-redeem-cap-backtest-tooling-2026-06-14` is already **MERGED** + is a local backtest script — no prod deploy. `bitunix-backlog-yfinance-removal-2026-06-14` already MERGED.)

### HOLD
None. **P1 is the only unmerged live-engine CODE ready now** (confirmed — matches the default expectation), plus the pending kalshi item from the other session.

---

## 2. Deploy + restart plan (operator-gated; nothing executed here)

### 2a. Pre-deploy md5 gate — prod is at the clean base (verified read-only 2026-06-14)
Prod files **already equal the P1 base** (`32e7fb4`), and `main` never touched these two files since that base (drift check empty) → the single-file replace base→target is clean, no rebase.

| file (prod: `/home/azureuser/trading_corp/<path>`) | prod NOW (LF md5) | = base `32e7fb4` | → TARGET `8b78da8` (LF md5) |
|---|---|---|---|
| `trading_corp/brokers/bitunix.py` | `8a81b30e74a5a38e60752e0c88de8d9e` | ✓ match | `64d857246a0879c4378e5b3a4185874e` |
| `trading_corp/agents/divisions/bitunix_position_reconciler.py` | `bcefc1c0b95a784c35d8e236f86748ed` | ✓ match | `64f33e76934e754c76437e6ce7d7d290` |

> md5s are **LF-normalized git-blob** values (comparable to prod's LF files). The Windows working-tree files are CRLF — the apply script `tr -d '\r'` normalizes before gating.

### 2b. Deploy sequence (per file — see `deploy_apply_p1.sh`)
Per the engineering philosophy (backup → md5 gate → atomic mv), for each `(staged_new_file, prod_path, expected_base_md5, target_md5)`:
1. **Stage** the LF-normalized new file on prod (operator scp; the script `tr -d '\r'` normalizes).
2. **Gate A** — prod current md5 == `expected_base_md5` (ABORT on mismatch = unexpected prod drift).
3. **Gate B** — staged md5 == `target_md5` (the new code is exactly what we reviewed).
4. **py_compile** the staged file.
5. **Backup** — `cp -n <prod_path> <prod_path>.bak-pre-p1reconciler-2026-06-14` (naming per convention: `.bak-pre-b1-2026-06-10`, `.bak-pre-d1-2026-06-11`, `.bak-pre-hitl-2026-06-13`).
6. **Atomic mv** — copy staged → `<prod_path>.new.$$` (same dir/fs) → `mv` over `<prod_path>` → `chown azureuser`.
7. **Re-verify** prod md5 == `target_md5`.
→ When the kalshi item lands, add its `(file, path, base_md5, target_md5)` row to the script's `FILE_SPECS` and re-run the gate; deploy in the same pass.

### 2c. Side-label confirmation (the one residual P1 grounding item — read-only)
The P1 side fix treats `SELL/SHORT`→short, `BUY/LONG`→long, and **fail-louds (warns + treats as long) on any other label**. The exact BitUnix position-side label is `"SELL"` by strong inference, not a captured string (the closed position can't be re-read without a signed call). At/after deploy, confirm the real label by EITHER:
- reading the BitUnix UI/position payload `side` value for any open position, OR
- watching the first post-deploy live position: it either **matches cleanly** (label ∈ SELL/BUY → handled) or emits `WARNING ... unrecognized side label '<X>'` in the journal (a third convention → extend `_SHORT_POSITION_SIDE_LABELS`/`_LONG_POSITION_SIDE_LABELS` by one line + re-deploy).

The fix is safe either way; this just closes the inference.

### 2d. Restart — AFTER deploy, clears the latched halt (NOT instead of)
`systemctl restart trading-corp` (NOPASSWD). The restart:
- loads new `bitunix.py` + `reconciler.py` (+ the kalshi change),
- **re-inits `BitunixBroker` → `_halt_new_orders` resets to `False`** (bitunix.py:320) — clears the `position_state_reconciler_divergence` latch,
- the next reconciler tick now matches the position cleanly → halt stays clear.

⚠️ **Restart ALONE (without the deploy) is NOT enough** — it clears the halt, but the next live fill re-triggers the same false divergence and re-latches. Deploy the fix FIRST, then restart.

### 2e. Post-restart verification (read-only)
1. `systemctl status` — new PID, `NRestarts=0`, active; ExecStart still `--live --brokers bitunix`.
2. Prod md5 of the 2 files == TARGET; fresh `.pyc` mtime > proc start.
3. Audit: bitunix `position_state_reconciled` (CLEAN) rows appear and **`position_state_divergence_detected` STOPS** (the ~60s false-divergence is gone). If a position is open it shows in `matches`; if flat, no divergence.
4. Not halted: no new `position_state_reconciler_divergence` rows post-restart; a subsequent real fill places (not refused). `execution_mode=live`, broker `paper=False` unchanged.
5. Confirm the side-label step (2c) resolves on the first live position.

---

## 3. P2 — book the unbooked first-fill exit (DRAFT — operator runs, do NOT execute here)

The first live fill's row is **unbooked** (`result IS NULL`) because the server-side stop closed it outside the bot's place-then-record path. Book it manually with the operator-supplied authoritative numbers. Schema read from prod (`result` domain `'win'|'loss'|'open'|'expired'` → stop-out = `'loss'`); columns mirror `_record_exit_outcome`'s `UPDATE` + the `extra_json` `exit_fee_usd` merge.

**Row identity (confirmed read-only):** `order_id = '6741f62f-d950-4356-8deb-578f603f8db0'` (= venue/broker_order_id), the SHORT BTC/USDT.P fill, entry 18:24:08, `mode=live`, all `result_*` NULL, `entry_fee_usd` 0.005094248 present, `exit_fee_usd` NULL.

### Step 1 — confirming SELECT (run FIRST; must return EXACTLY 1 row) — `runbooks/2026-06-14_p2_confirm_select.sql`
```sql
SELECT order_id, ts, side, qty, entry_reference_price, stop_price,
       result, result_price, actual_pnl_dollars,
       json_extract(extra_json,'$.execution_mode') AS mode,
       json_extract(extra_json,'$.entry_fee_usd')  AS entry_fee,
       json_extract(extra_json,'$.exit_fee_usd')   AS exit_fee
FROM paper_trade_record
WHERE order_id = '6741f62f-d950-4356-8deb-578f603f8db0'
  AND result IS NULL;
-- EXPECT exactly 1 row (result NULL, mode 'live', exit_fee NULL).
-- 0 rows ⇒ already booked → do NOT run the UPDATE.
```

### Step 2 — the booking UPDATE (single targeted statement) — `runbooks/2026-06-14_p2_book_update.sql`
```sql
UPDATE paper_trade_record
SET result             = 'loss',
    result_ts          = '2026-06-14T19:12:00+00:00',  -- ~stop-out; CONFIRM exact broker fill ts (bracketed 19:11:46–19:12:46 by the reconciler)
    result_price       = 63778.62,                     -- ⚠ DERIVED from PnL; CONFIRM exact exit fill (see DISCREPANCY note)
    actual_pnl_dollars = -0.04880000,                  -- operator-supplied GROSS price PnL
    actual_r_multiple  = -0.39906,                     -- DERIVED = actual_pnl_dollars / max_dollar_risk(0.12228668); CONFIRM
    bars_to_resolution = NULL,                         -- server-side broker close (not a bar-walk resolution)
    extra_json = json_set(extra_json,
                   '$.exit_fee_usd',     0.00510400,   -- operator-supplied
                   '$.result_source',    'operator_manual_booking',
                   '$.exit_method',      'server_side_sl_B1',
                   '$.net_realized_usd', -0.0590)       -- = pnl - entry_fee - exit_fee
WHERE order_id = '6741f62f-d950-4356-8deb-578f603f8db0'
  AND result IS NULL;                                  -- idempotency guard: books only if still unbooked
-- EXPECT "1 row(s) modified". Re-run Step 1 after → result='loss', exit_fee 0.005104.
```

**Discipline:** run on a writable connection (NOT `-readonly`); single targeted UPDATE; the `AND result IS NULL` guard makes it idempotent (won't double-book). Confirm Step 1 shows exactly 1 row BEFORE running Step 2.

### ⚠ DISCREPANCY to resolve before booking `result_price`
Operator PnL −0.04880000 implies an exit fill ≈ **63778.62** (= entry fill 63678.1 + 100.52). That is **below the SL trigger 63805.3397** — a clean stop-at-trigger would be ≈ **−0.0618** (exit 63805.34), not −0.0488. So either the MARK_PRICE stop filled ~26.7 pts better than its trigger, or the −0.0488 is on a different basis. **Operator: confirm the exact exit fill price from the BitUnix trade history and set `result_price` to it** (the −0.0488 PnL is taken as authoritative; `result_price`/`actual_r_multiple` are derived from it and flagged). `actual_pnl_dollars` is GROSS (fees are separate in `extra_json`), matching the schema/`_record_exit_outcome` convention.

---

## Disclosure (82fda13)
Agent: read-only SSH (`sqlite3 -readonly` schema + row, `md5sum`/`stat`/`ls`/`ps` file reads) + local git/source review + local file writes (this plan, the apply script, the two SQL drafts) + local git commit on this branch. **No deploy, no restart, no prod write, no query execution, no signed/public-API call.** Branch `bitunix-p1-deploy-prep-2026-06-14`, UNMERGED — for operator review and execution.
