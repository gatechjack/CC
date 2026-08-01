# T2 kalshi_crypto_v2 observer — DEPLOY RUNBOOK (operator-run)

**What:** deploy a NEW read-only research collector as a systemd service on tc-prod-vm,
paper/observation. Additive only — no existing file modified, no order path, old `kalshi_crypto`
untouched. Agent is read-only; **the operator executes every write step below.**

**Branch:** `claude-2026-08-01b`. **Verified locally:** 2 clean 30s cycles (idx=4, quotes=66,
signals=8, alarm=0) against a temp DB; SFP ARMED state fired on real bars; guards + both conditions
(band_pct per row, computed_bar_ts_ms) confirmed.

## Files (3 new) — LF-normalized md5 to verify AFTER copy
| repo path | prod path | md5 (LF) |
|---|---|---|
| `trading_corp/agents/strategies/kalshi_crypto_v2_observer.py` | same under `/home/azureuser/trading_corp/` | `dba46374b23a74fe9eaa333be61744cd` |
| `scripts/migrate_kcv2_tables.py` | same | `7a2dd43e46be0c57382a838f6b223b64` |
| `infra/systemd/trading-corp-kcv2-observer.service` | `/etc/systemd/system/trading-corp-kcv2-observer.service` | `bf0014618895921790c6423f4fbd2255` |

## Pre-deploy (verify prod state)
```
cd /home/azureuser/trading_corp
sqlite3 -readonly data/trading_corp.db "SELECT name FROM sqlite_master WHERE name LIKE 'kcv2_%';"   # expect EMPTY
systemctl status trading-corp-kcv2-observer 2>/dev/null | head -1                                    # expect not-found
```

## Deploy
1. **Copy the 2 python files** into the repo tree on prod (git pull of this branch, or scp), then verify:
   ```
   md5sum trading_corp/agents/strategies/kalshi_crypto_v2_observer.py scripts/migrate_kcv2_tables.py
   ```
   Must equal the table above (prod files are LF).
2. **Read the migration DDL** (`scripts/migrate_kcv2_tables.py` top), then create the 4 tables:
   ```
   venv/bin/python -X utf8 scripts/migrate_kcv2_tables.py data/trading_corp.db
   ```
   Expect: `created this run: ['kcv2_heartbeat','kcv2_index_ticks','kcv2_quotes','kcv2_signals']`.
3. **Install the unit** (sudo — /etc is operator-owned):
   ```
   sudo cp infra/systemd/trading-corp-kcv2-observer.service /etc/systemd/system/
   md5sum /etc/systemd/system/trading-corp-kcv2-observer.service   # == table above
   sudo systemctl daemon-reload
   sudo systemctl enable --now trading-corp-kcv2-observer
   ```

## Verify (within ~1-2 min)
```
journalctl -u trading-corp-kcv2-observer -n 30 --no-pager    # expect "cfbenchmarks WS connected" + "kcv2 cycle N: idx=4 quotes=.. signals=.. ws=True alarm=0"
sqlite3 -readonly data/trading_corp.db "SELECT cycle_id,rows_index,rows_quotes,rows_signals,alarm FROM kcv2_heartbeat ORDER BY id DESC LIMIT 3;"
sqlite3 -readonly data/trading_corp.db "SELECT COUNT(*),SUM(sum_to_1_ok) FROM kcv2_quotes;"          # sum_to_1 guard populating
sqlite3 -readonly data/trading_corp.db "SELECT asset,state,computed_bar_ts_ms FROM kcv2_signals ORDER BY id DESC LIMIT 8;"
```
PASS = index=4/cycle, quotes>0, alarm=0, WS connected, signals carry computed_bar_ts_ms.
Any `alarm=1` (zero index or quotes) => investigate WS auth / market fetch before trusting the corpus.

## Rollback
```
sudo systemctl disable --now trading-corp-kcv2-observer
sudo rm -f /etc/systemd/system/trading-corp-kcv2-observer.service && sudo systemctl daemon-reload
# tables are additive + harmless; drop only if desired:
# sqlite3 data/trading_corp.db "DROP TABLE kcv2_index_ticks; DROP TABLE kcv2_quotes; DROP TABLE kcv2_signals; DROP TABLE kcv2_heartbeat;"
```

## deploy_log.md entry (append AFTER a verified deploy)
```
## <UTC ts> — kalshi_crypto_v2 READ-ONLY forward logger (T2) deployed (paper/observation)
**Branch:** claude-2026-08-01b
**Features shipped:** new systemd service trading-corp-kcv2-observer logging cfbenchmarks_value index
(BRTI/ETHUSD_RTI/SOLUSD_RTI/XRPUSD_RTI) + near-money both-sided Kalshi quotes (KX*15M + hourly
ladder/dir) + lifted SFP state, 30s cadence, to 4 new kcv2_* tables. Write-time sum-to-1 + heartbeat
+ zero-row alarm. Read-only (no order path); creds via KV managed identity.
**Notable code changes:** NEW trading_corp/agents/strategies/kalshi_crypto_v2_observer.py
(md5 dba46374b23a74fe9eaa333be61744cd); NEW scripts/migrate_kcv2_tables.py; NEW
infra/systemd/trading-corp-kcv2-observer.service. No existing file modified.
**Verify:** kcv2_heartbeat flowing alarm=0, WS connected, sum_to_1_ok populating.
```
