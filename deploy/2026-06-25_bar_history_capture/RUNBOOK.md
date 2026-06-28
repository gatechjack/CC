# Runbook — 4-coin `bitunix_bar_history` capture fix (2026-06-25)

Branch `bitunix-sfp-division-2026-06-25`. **STAGED, NOT deployed.** GT_Jack runs every prod step
(stop / migrate / apply / start / backfill); agent drives read-only SSH (FLAT check, boot smoke) and
will hand the paste-able `.ps1` upload runner + short per-step lines at execution time.

## What ships
Symbol-keys `bitunix_bar_history` so SOL/ETH/XRP stop colliding with BTC, drives the 6 missing capture
poll loops, and backfills the 3 coins. **Live-trading-safe** — the SFP detector reads the in-memory
cache, never this table (the SL-trail reconciler IS a table reader → fixed by the symbol filter).

## Package (this dir)
- `staged/` — 3 code files (md5-gated): `bitunix_bar_archiver.py` (copy), `bitunix_position_reconciler.py` (splice onto prod `8c3adcd1`), `main.py` (splice onto prod `2b504cbc`).
- `apply_capfix.sh` — Gate-A/Gate-B md5 + backup + atomic swap + py_compile (no restart/migration/backfill).
- `migrate_bar_history.sql` — offline transactional migration + backup table + verify.
- `backfill_capture.py` — one-time API-only REST backfill (SOL/ETH/XRP × 15m+3m, BTC-anchored depth).
- `recon.patch` / `main.patch` — the spliced hunks (provenance).

| file | method | Gate-A base | Gate-B target |
|---|---|---|---|
| `data/bitunix_bar_archiver.py` | copy (prod==main) | `f83a305f` | `53c2e64d` |
| `agents/divisions/bitunix_position_reconciler.py` | splice (sacred) | `8c3adcd1` | `3a23610c` |
| `main.py` | splice | `2b504cbc` | `82a01f83` |

Pre-deploy gates GREEN: full suite **28 = baseline, zero new**; 18 targeted (pr5 + new symbol test); py_compile clean. Reader audit: the only live table-reader is `_load_recent_bars` (fixed: `WHERE symbol=? AND timeframe=?` + per-position symbol).

## The 5 steps (engine STOPPED for 1–3; backfill 5 is live)
**1. PRE-FLIGHT + HALT.** (agent, read-only) confirm FLAT: `SELECT division,COUNT(*) FROM paper_trade_record WHERE result IS NULL GROUP BY 1` shows no bitunix; venue flat; PEAD has no open position (market closed). Then (operator):
`ssh azureuser@trading.jacksumner.com "sudo -n systemctl stop trading-corp"`

**2. BACKUP + MIGRATE (offline).** (operator) upload the package, then:
`cd ~/trading_corp && sqlite3 data/trading_corp.db < ~/capfix/migrate_bar_history.sql`
The script prints `pre_rows` (= N), then after the swap `post_rows / n_symbols / backup_rows`. **VERIFY post_rows == N AND n_symbols == 1.** If post_rows != N → STOP, restore `bitunix_bar_history_bak_20260625`.

**3. APPLY CODE (engine still stopped).** (operator) `bash ~/apply_capfix.sh` — Gate-A → backup `*.bak-pre-capfix-2026-06-25` → atomic swap → Gate-B → py_compile. Aborts before any write on a md5 mismatch.

**4. RESTART.** (operator) `ssh … "sudo -n systemctl start trading-corp"`. (agent, read-only) boot smoke: SFP loop online, reconciler clean, archiver now writing **symbol-tagged** rows (`SELECT symbol,timeframe,COUNT(*) FROM bitunix_bar_history GROUP BY 1,2` — BTC rows intact + new BTC rows tagged BTCUSDT), no tracebacks. BTC 15m/3m caches auto-prime (heal the halt gap). SFP detector unaffected.

**5. BACKFILL (live, API-only).** (operator) `cd ~/trading_corp && venv/bin/python ~/capfix/backfill_capture.py`. Prints per coin/TF: `BTC_target / fetched / inserted / total`. **REPORT actual depth achieved per coin/TF** (smaller pairs may have shorter Bitunix history — not uniform). Idempotent (INSERT OR IGNORE), db-lock-retry (live archiver also writes). Writes the TRADING db, not btc_scalping.db, not TV.

## Rollback
- Pre-restart: restore `*.bak-pre-capfix-2026-06-25` (3 code files); restore `bitunix_bar_history_bak_20260625` (rename back). Migration is transactional (a failed migrate auto-rolls-back).
- Post-restart: the code is additive/defensive; reverting the 3 files + restoring the table backup + restart returns to pre-state.

## Verification
- `bitunix_bar_history`: BTC unchanged all TFs (count == pre); post-backfill SOL/ETH/XRP at 15m+3m with reported depths; `COUNT(DISTINCT symbol)` = 4 after backfill.
- SL-trail reconciler reads BTC-only (symbol filter), boot log `timeframe=3m`, no mixed-coin bars.
- SFP live: loop online, BTC cache priming each bar, flat, reconciler clean — unchanged.
