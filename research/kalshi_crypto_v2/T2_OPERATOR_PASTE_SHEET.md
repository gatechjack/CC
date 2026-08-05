# T2_OPERATOR_PASTE_SHEET — kalshi_crypto_v2 forward observer deploy

Read-only research collector as a systemd service on `tc-prod-vm` (paper/observation, **no order path**,
additive-only). **The agent is read-only and executed nothing on prod; you run every block yourself.**

**Command-paste-rule compliant:** you paste **only short, single-line runners** (`powershell -ep bypass
-f .\t2_N.ps1`). Each runner file holds the real command; remote bash is streamed over `ssh` **STDIN**
(`... | ssh $h "tr -d '\r'|bash"`) so quotes survive, and sudo steps use `ssh -t`. No long line, no
inline `ssh "..."`, ever leaves this file for you to paste. Runners are in `t2_deploy/` next to this
file (all validated: PS 5.1 parses, pure ASCII).

### Agent pre-verification (DONE — no drift)
The 3 files match the recorded **LF** md5s exactly and your local copies are LF, so `scp` transfers clean:
| file | md5 (LF) |
|---|---|
| `trading_corp/agents/strategies/kalshi_crypto_v2_observer.py` | `dba46374b23a74fe9eaa333be61744cd` |
| `scripts/migrate_kcv2_tables.py` | `7a2dd43e46be0c57382a838f6b223b64` |
| `infra/systemd/trading-corp-kcv2-observer.service` | `bf0014618895921790c6423f4fbd2255` |

### Cred mechanism — DECIDED: Key Vault via the VM **managed identity** (simplest + safest)
The unit + `load_creds` already do this: with no `KALSHI_KAREN_*` env set, the observer fetches
`KALSHI-KAREN-*` from `kv-tc-vtwbowt3wtkpy` via `DefaultAzureCredential`, in memory only — **no secret on
disk / repo / history**, and the unit deploys unmodified (md5 `bf001461`). **Step 1 proves it works
before we rely on it.** If step 1's KV read fails, use **Appendix B** (`t2_B_envfile.ps1`, the
EnvironmentFile + `az` fallback).

---

## One-time: open a local PowerShell and go to the runner folder (paste this one line)

```
cd "C:\Users\AA Incorporado\cc-2026-08-04-wt\research\kalshi_crypto_v2\t2_deploy"
```
Then run the steps below **in order**, reading OK / STOP-if after each. Sudo steps open a `ssh -t`
session and will prompt for your sudo password in the same window if it is not NOPASSWD.

## 1. Prod-state precheck + managed-identity cred pre-flight

```
powershell -ep bypass -f .\t2_1_checks.ps1
```
- **OK:** the kcv2-tables list is **empty**, the service line says **not found**, and the last line is
  **`KV_OK True True`**.
- **STOP-if:** any `kcv2_*` table listed, or the service already exists → partly deployed; report to me.
  If the last line is **not** `KV_OK True True` (an error or `False`) → managed identity can't read the
  secrets; either grant the VM identity **Key Vault Secrets User** on `kv-tc-vtwbowt3wtkpy` and re-run,
  or switch to **Appendix B**.

## 2. Copy the 3 files + verify md5 + run the migration

```
powershell -ep bypass -f .\t2_2_copy_migrate.ps1
```
(scp prints 3 `100%` lines, then md5 checks, then — only if both match — the migration.)
- **OK:** two **`MATCH`** lines, then `created this run: ['kcv2_heartbeat', 'kcv2_index_ticks',
  'kcv2_quotes', 'kcv2_signals']`, then the 4 table names listed.
- **STOP-if:** any **`MISMATCH`** line (the script prints `STOP` and does **not** migrate) → report to me.
  Or a Python traceback / fewer than 4 tables → report.

## 3. Install the systemd unit + verify its md5 (no start yet)

```
powershell -ep bypass -f .\t2_3_install.ps1
```
(enter your sudo password if prompted.)
- **OK:** prints the unit's md5 and then **`UNIT_MD5_MATCH`**.
- **STOP-if:** **`UNIT_MD5_MISMATCH_STOP`**, or the `sudo cp` errored → do **not** start; report to me.

## 4. Enable + start the service

```
powershell -ep bypass -f .\t2_4_start.ps1
```
- **OK:** status shows `Active: active (running)` and `Loaded: ... enabled`.
- **STOP-if:** `failed` or `activating (auto-restart)` looping → run step 5 (logs), then roll back
  (`t2_9`) and report.

## 5. Acceptance gate — heartbeat + quotes + signals + logs (wait ~90 s after step 4)

```
powershell -ep bypass -f .\t2_5_verify.ps1
```
- **PASS:** heartbeat has a **new row ~every 30 s**, **`rows_index` = 4**, **`rows_quotes` > 0**,
  **`index_ws_connected` = 1**, **`alarm` = 0** on every row; `kcv2_quotes` COUNT > 0 with SUM ≈ COUNT;
  `kcv2_signals` rows carry a non-null `computed_bar_ts_ms`; logs show `WS connected` + cycle lines.
- **STOP-if:** any `alarm = 1`, heartbeat not advancing ~30 s, `index_ws_connected = 0`, or tracebacks /
  `KalshiAuthError` / `Key Vault fetch failed` in the logs → **do not trust the corpus**; roll back
  (`t2_9`) and report if it doesn't self-clear in a few cycles.

**Deploy is complete when step 5 PASSES.** (Then append the deploy_log block from
`T2_DEPLOY_RUNBOOK.md` to `runbooks/deploy_log.md` with the verified UTC timestamp.)

## Rollback (any time)

```
powershell -ep bypass -f .\t2_9_rollback.ps1
```
Prints `ROLLBACK_DONE`. The 4 `kcv2_*` tables are additive + harmless and are left in place (drop them
manually only if you want them gone).

---

## Appendix A — what each `kcv2_*` table is
- `kcv2_index_ticks` — raw cfbenchmarks index + trailing-60s TWAP per asset.
- `kcv2_quotes` — near-money both-sided Kalshi quotes (KX*15M + hourly), raw dollars, `band_pct` +
  `sum_to_1_ok` guard.
- `kcv2_signals` — lifted SFP state per asset with `computed_bar_ts_ms`.
- `kcv2_heartbeat` — one row per ~30 s cycle with row counts + `alarm`.

## Appendix B — EnvironmentFile cred fallback (ONLY if step 1's KV read failed, or you want explicit creds)

Run this instead of relying on managed identity, **before** step 4:
```
powershell -ep bypass -f .\t2_B_envfile.ps1
```
What it does (secrets never printed, never in shell history — `az` output is captured to variables and
streamed to the VM over `ssh` STDIN only):
- fetches `KALSHI-KAREN-API-KEY-ID` / `KALSHI-KAREN-PRIVATE-KEY-PEM` from `kv-tc-vtwbowt3wtkpy`;
- writes them to **`/home/azureuser/.config/kcv2-kalshi.env` (chmod 600, azureuser-owned — no sudo, no
  secret in `/etc`)**; systemd reads it as root at start;
- adds a drop-in `.../trading-corp-kcv2-observer.service.d/10-creds.conf` with
  `EnvironmentFile=...kcv2-kalshi.env` (so the packaged unit's md5 stays `bf001461` — unmodified).
- **OK:** `wc -l` shows **2 lines**, perms `600 azureuser`, then `DROPIN_DONE`. Then continue at step 4.
- **STOP-if:** it prints `STOP: PEM has real newlines...` (the KV PEM isn't single-line `\n`-escaped) →
  an EnvironmentFile can't hold it; use managed identity instead. Rollback also removes these:
  `t2_9_rollback.ps1` deletes the drop-in; delete the env file with a separate `rm ~/.config/kcv2-kalshi.env`.
