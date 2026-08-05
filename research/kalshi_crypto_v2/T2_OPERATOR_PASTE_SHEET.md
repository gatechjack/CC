# T2_OPERATOR_PASTE_SHEET — kalshi_crypto_v2 forward observer deploy

Read-only research collector as a systemd service on `tc-prod-vm` (paper/observation, **no order
path**, additive-only — no existing file modified). **The agent is read-only and executed nothing on
prod; you run every block below yourself.**

### Agent pre-verification (requirement #2 — DONE, no drift)
The 3 package files at `158aaa5` match the runbook's recorded **LF** md5s exactly, and are byte-identical
at the `claude-2026-08-02` tip — **and your local working-tree copies are already LF** (so `scp`
transfers clean; the on-VM md5 will match directly). Recorded hashes:

| file | md5 (LF) |
|---|---|
| `trading_corp/agents/strategies/kalshi_crypto_v2_observer.py` | `dba46374b23a74fe9eaa333be61744cd` |
| `scripts/migrate_kcv2_tables.py` | `7a2dd43e46be0c57382a838f6b223b64` |
| `infra/systemd/trading-corp-kcv2-observer.service` | `bf0014618895921790c6423f4fbd2255` |

No drift → this sheet proceeds. (If a future re-verify ever disagrees, STOP and tell me.)

### Cred mechanism — DECIDED: **Key Vault via the VM managed identity** (simplest + safest)
The unit and `load_creds` (`_kalshi_auth.py`) are already built for it: with no `KALSHI_KAREN_*` env
set, the observer fetches `KALSHI-KAREN-API-KEY-ID` / `KALSHI-KAREN-PRIVATE-KEY-PEM` from
`kv-tc-vtwbowt3wtkpy` via `DefaultAzureCredential` and holds them **in memory only — never on disk**.
This beats the EnvironmentFile idea on every one of your criteria: **no secret ever lands on disk / repo
/ shell history**, no `az` fetch to run, no PEM line-ending fiddling, and the unit deploys **unmodified**
(md5 stays `bf001461`, so it passes the md5 gate). **Step 2 proves the managed identity can read the
secrets before we rely on it.** If step 2 fails, use **Appendix B** (the EnvironmentFile + `az` route you
described) as the fallback.

### Terminal legend + paste rule
- **[LOCAL PS]** = your local Windows PowerShell.
- **[SSH]** = inside the session you open in step 1 (`ssh azureuser@trading.jacksumner.com`).
- Each fenced line is **one logical line** — select the whole line and paste it; never hand-retype.

---

## 1. [SSH] Open the session + prod-state pre-check

```
ssh azureuser@trading.jacksumner.com
```
Then, inside that session:
```
cd /home/azureuser/trading_corp
sqlite3 -readonly data/trading_corp.db "SELECT name FROM sqlite_master WHERE name LIKE 'kcv2_%';"
systemctl status trading-corp-kcv2-observer 2>/dev/null | head -1
```
- **OK:** the SELECT prints **nothing** (no `kcv2_` tables yet) **and** the status line is empty or says
  `Unit trading-corp-kcv2-observer.service could not be found`.
- **STOP-if:** any `kcv2_*` table is listed, **or** the service already exists → it's partly deployed;
  stop and report to me.

## 2. [SSH] Managed-identity cred pre-flight (prints only booleans — no secret is shown)

```
venv/bin/python -c "from azure.identity import DefaultAzureCredential as D; from azure.keyvault.secrets import SecretClient as S; c=S('https://kv-tc-vtwbowt3wtkpy.vault.azure.net/', D()); print('KV_OK', bool(c.get_secret('KALSHI-KAREN-API-KEY-ID').value), bool(c.get_secret('KALSHI-KAREN-PRIVATE-KEY-PEM').value))"
```
- **OK:** prints exactly `KV_OK True True` → the VM identity can read both secrets; **no creds need to
  touch disk**. Continue to step 3.
- **STOP-if:** any error (`Forbidden`, `AuthenticationFailed`, `KeyVaultErrorException`, credential
  errors) or a `False` → the managed identity lacks access. Do **not** deploy with managed identity;
  either grant the VM identity **Key Vault Secrets User** on `kv-tc-vtwbowt3wtkpy` and re-run this, **or**
  switch to **Appendix B** (EnvironmentFile). Tell me which.

## 3. [LOCAL PS] Copy the 3 files to the VM home (files are LF; scp preserves)

```
cd "C:\Users\AA Incorporado\cc-2026-08-02-wt"
```
```
scp trading_corp/agents/strategies/kalshi_crypto_v2_observer.py scripts/migrate_kcv2_tables.py infra/systemd/trading-corp-kcv2-observer.service azureuser@trading.jacksumner.com:~/
```
- **OK:** three `100%` transfer lines, no errors.
- **STOP-if:** `Permission denied`, `No such file or directory`, or host-key prompt failures → stop and
  report. (If it's the first-ever connect and it asks to trust the host key, that's normal — accept it.)

## 4. [SSH] Place the two Python files + verify md5 == recorded (section b)

```
cd /home/azureuser/trading_corp
cp ~/kalshi_crypto_v2_observer.py trading_corp/agents/strategies/kalshi_crypto_v2_observer.py
cp ~/migrate_kcv2_tables.py scripts/migrate_kcv2_tables.py
md5sum trading_corp/agents/strategies/kalshi_crypto_v2_observer.py scripts/migrate_kcv2_tables.py
```
- **OK:** the two md5s are **exactly**:
  ```
  dba46374b23a74fe9eaa333be61744cd  trading_corp/agents/strategies/kalshi_crypto_v2_observer.py
  7a2dd43e46be0c57382a838f6b223b64  scripts/migrate_kcv2_tables.py
  ```
- **STOP-if:** either md5 differs → do **not** continue; report to me. (Root cause is almost always a
  line-ending change in transit; the fix is `sed -i 's/\r$//' <file>` then re-md5 — but stop and tell me
  first.)

## 5. [SSH] Run the migration — creates the 4 `kcv2_*` tables (section c)

```
venv/bin/python -X utf8 scripts/migrate_kcv2_tables.py data/trading_corp.db
```
- **OK:** prints `created this run: ['kcv2_heartbeat', 'kcv2_index_ticks', 'kcv2_quotes', 'kcv2_signals']`
  followed by `kcv2 tables present: [...4...]` and four `rows=0` lines. Confirm:
```
sqlite3 -readonly data/trading_corp.db "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'kcv2_%' ORDER BY name;"
```
  → lists exactly `kcv2_heartbeat`, `kcv2_index_ticks`, `kcv2_quotes`, `kcv2_signals` (4 rows).
- **STOP-if:** a Python traceback, or fewer than 4 tables listed → stop and report. (Re-running is safe —
  the DDL is `IF NOT EXISTS` — but investigate the error first.)

## 6. [SSH] Install the systemd unit + verify md5 (section d)

```
sudo cp ~/trading-corp-kcv2-observer.service /etc/systemd/system/trading-corp-kcv2-observer.service
md5sum /etc/systemd/system/trading-corp-kcv2-observer.service
```
- **OK:** md5 == `bf0014618895921790c6423f4fbd2255`.
- **STOP-if:** it differs → do **not** enable a drifted unit; report.

## 7. [SSH] Enable + start the service (section d cont.)

```
sudo systemctl daemon-reload
sudo systemctl enable --now trading-corp-kcv2-observer
sudo systemctl status trading-corp-kcv2-observer --no-pager | head -6
```
- **OK:** status shows `Active: active (running)`; `Loaded:` shows the unit `enabled`.
- **STOP-if:** `failed`, or `activating (auto-restart)` looping → go to step 8, then roll back (step 10)
  and report.

## 8. [SSH] Watch the logs (~1–2 min for the first cycles)

```
journalctl -u trading-corp-kcv2-observer -n 40 --no-pager
```
- **OK:** you see `cfbenchmarks WS connected` and lines like
  `kcv2 cycle N: idx=4 quotes=.. signals=.. ws=True alarm=0`.
- **STOP-if:** repeated tracebacks, `KalshiAuthError`, `Key Vault fetch failed`, or WS auth failures →
  roll back (step 10) and report. (A KV/auth error here means step 2 passed but the *service* identity
  differs — tell me; Appendix B is the fallback.)

## 9. [SSH] Heartbeat verification — the acceptance gate (section e)

Wait ~90 s after start (2–3 cycles), then:
```
sqlite3 -readonly data/trading_corp.db "SELECT cycle_id,rows_index,rows_quotes,rows_signals,index_ws_connected,alarm,datetime(ts_ms/1000,'unixepoch') AS t FROM kcv2_heartbeat ORDER BY id DESC LIMIT 5;"
sqlite3 -readonly data/trading_corp.db "SELECT COUNT(*), SUM(sum_to_1_ok) FROM kcv2_quotes;"
sqlite3 -readonly data/trading_corp.db "SELECT asset,state,computed_bar_ts_ms FROM kcv2_signals ORDER BY id DESC LIMIT 8;"
```
**PASS — good looks like:**
- `kcv2_heartbeat`: a **new row roughly every ~30 s** (the `t` timestamps step by ~30 s), **`rows_index`
  = 4** on every row, **`rows_quotes` > 0**, **`index_ws_connected` = 1**, and **`alarm` = 0** on every
  row.
- `kcv2_quotes`: `COUNT(*)` > 0 and `SUM(sum_to_1_ok)` ≈ `COUNT(*)` (essentially every quote passed the
  sum-to-1 guard).
- `kcv2_signals`: rows carry a non-null `computed_bar_ts_ms`.
- **STOP-if:** any `alarm = 1` (a category wrote 0 rows), the heartbeat is not advancing every ~30 s, or
  `index_ws_connected = 0` → **do not trust the corpus**; investigate WS auth / market fetch, and roll
  back (step 10) + report if it doesn't self-clear within a few cycles.

**Deploy is complete when step 9 PASSES.** (Optional good-hygiene: `rm ~/kalshi_crypto_v2_observer.py
~/migrate_kcv2_tables.py ~/trading-corp-kcv2-observer.service` to clear the staged copies from home.)

## 10. [SSH] Rollback (one sequence) (section f)

```
sudo systemctl disable --now trading-corp-kcv2-observer && sudo rm -f /etc/systemd/system/trading-corp-kcv2-observer.service && sudo systemctl daemon-reload
```
The 4 `kcv2_*` tables are additive + harmless — leave them. **Only** if you want them gone:
```
sqlite3 data/trading_corp.db "DROP TABLE IF EXISTS kcv2_index_ticks; DROP TABLE IF EXISTS kcv2_quotes; DROP TABLE IF EXISTS kcv2_signals; DROP TABLE IF EXISTS kcv2_heartbeat;"
```

## 11. [SSH] (only after a PASS) append the deploy_log entry

Append the block at the bottom of `research/kalshi_crypto_v2/T2_DEPLOY_RUNBOOK.md` to
`runbooks/deploy_log.md` (fill in the verified UTC timestamp from `date -u`). Not load-bearing for the
service; it's the record so the next session knows T2 is live.

---

## Appendix A — what each `kcv2_*` table is (reference)
- `kcv2_index_ticks` — raw cfbenchmarks index + trailing-60s TWAP per asset (BRTI/ETHUSD_RTI/SOLUSD_RTI/
  XRPUSD_RTI).
- `kcv2_quotes` — near-money both-sided Kalshi quotes (KX*15M + hourly ladder/dir), raw dollars, with
  `band_pct` (COND 1) and the `sum_to_1_ok` guard.
- `kcv2_signals` — lifted SFP state per asset with `computed_bar_ts_ms` (COND 2).
- `kcv2_heartbeat` — one row per ~30 s cycle with row counts + `alarm`.

## Appendix B — EnvironmentFile cred fallback (ONLY if step 2 failed, or you specifically want explicit creds)

This is the `az`-fetch + EnvironmentFile route. It puts the two secrets in a **root-only 600** file on the
VM and wires them in via a **drop-in** (so the unit file itself stays unmodified at md5 `bf001461`). The
`az | ssh sudo tee` pipe keeps each secret **out of your local terminal output and out of your shell
history** (the command text carries no secret; the value flows only through the pipe).

**B1. [SSH] create the root-only env file + dir:**
```
sudo install -d -m 750 -o root -g root /etc/trading-corp && sudo install -m 600 -o root -g root /dev/null /etc/trading-corp/kcv2-kalshi.env
```
**B2. [LOCAL PS] fetch each secret from KV and append it (never printed locally):**
```
az keyvault secret show --vault-name kv-tc-vtwbowt3wtkpy --name KALSHI-KAREN-API-KEY-ID --query value -o tsv | %{ "KALSHI_KAREN_API_KEY_ID=$_" } | ssh azureuser@trading.jacksumner.com "sudo tee -a /etc/trading-corp/kcv2-kalshi.env >/dev/null"
```
```
az keyvault secret show --vault-name kv-tc-vtwbowt3wtkpy --name KALSHI-KAREN-PRIVATE-KEY-PEM --query value -o tsv | %{ "KALSHI_KAREN_PRIVATE_KEY_PEM=$_" } | ssh azureuser@trading.jacksumner.com "sudo tee -a /etc/trading-corp/kcv2-kalshi.env >/dev/null"
```
- ⚠ **PEM must be one line.** `load_creds` un-escapes `\n` (`_kalshi_auth.py:38`), so this works **iff the
  KV secret stores the PEM single-line with literal `\n`**. Verify one line landed, not many:
  `[SSH] sudo awk 'END{print NR" lines"}' /etc/trading-corp/kcv2-kalshi.env` → expect **`2 lines`**. If it
  shows many lines, the KV PEM has real newlines → **stop and use managed identity instead** (it needs no
  escaping).
**B3. [SSH] add the drop-in so the unit reads the env file (unit md5 unchanged):**
```
sudo install -d -m 755 /etc/systemd/system/trading-corp-kcv2-observer.service.d && printf '[Service]\nEnvironmentFile=/etc/trading-corp/kcv2-kalshi.env\n' | sudo tee /etc/systemd/system/trading-corp-kcv2-observer.service.d/10-creds.conf >/dev/null && sudo systemctl daemon-reload
```
- Perms: `/etc/trading-corp/kcv2-kalshi.env` is **600 root:root** (only root reads it; systemd reads it as
  root before dropping the service to `azureuser`). It is **never** world-readable, never in the repo.
- Then continue at **step 7** (enable + start). The observer will use the env creds (env-override branch)
  instead of the managed identity. Rollback also removes the drop-in + env file:
  `sudo rm -rf /etc/systemd/system/trading-corp-kcv2-observer.service.d /etc/trading-corp/kcv2-kalshi.env`.
