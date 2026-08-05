# T2_OPERATOR_PASTE_SHEET — kalshi_crypto_v2 forward observer deploy

Read-only research collector as a systemd service on `tc-prod-vm` (paper/observation, **no order path**,
additive-only). **The agent is read-only and executed nothing on prod; you run every block yourself.**

**No `sudo`, ever.** All root operations (placing files into `trading_corp/`, installing the unit into
`/etc`, `systemctl` enable, rollback) run through **`az vm run-command invoke ... RunShellScript`**, which
executes as **root** on the VM. DB writes inside it use `runuser -u azureuser` so the live DB stays
azureuser-owned. The only non-root step (scp to home) is plain SSH. You paste **only short runners**
(`powershell -ep bypass -f .\t2_N.ps1`); the real commands live inside the runner files. Runners
validated: PS 5.1 parses, pure ASCII, no `sudo` command anywhere.

**Prereqs:** be `az` logged-in with run-command rights on the VM (`az account show` should succeed), and
have SSH to `azureuser@trading.jacksumner.com`. The runners target `RG-SHARED-PROD` / `tc-prod-vm` — if
your resource-group or VM name differs, edit the `$rg` / `$vm` lines at the top of each runner first.

### Agent pre-verification (DONE — no drift)
The 3 files match the recorded **LF** md5s exactly (local copies are LF):
| file | md5 (LF) |
|---|---|
| `trading_corp/agents/strategies/kalshi_crypto_v2_observer.py` | `dba46374b23a74fe9eaa333be61744cd` |
| `scripts/migrate_kcv2_tables.py` | `7a2dd43e46be0c57382a838f6b223b64` |
| `infra/systemd/trading-corp-kcv2-observer.service` | `bf0014618895921790c6423f4fbd2255` |

### Cred mechanism — DECIDED: Key Vault via the VM **managed identity** (no secret on disk)
Unit + `load_creds` already do this; **step 1 proves it** before we rely on it. If step 1's KV read
fails, use **Appendix B** (`t2_B_envfile.ps1`). Note: each `az run-command` returns its output as a
message field that the runner prints; it is **tail-truncated**, so the pass/fail verdicts are printed at
the **end** of each block (`=== DEPLOY_OK ===`, `UNIT_MD5_MATCH`, `KV_OK True True`, `ROLLBACK_DONE`).

---

## One-time: open a local PowerShell and go to the runner folder (paste this one line)

```
cd "C:\Users\AA Incorporado\cc-2026-08-04-wt\research\kalshi_crypto_v2\t2_deploy"
```

## 1. Precheck + managed-identity cred pre-flight (az run-command, root)

```
powershell -ep bypass -f .\t2_1_checks.ps1
```
- **OK:** kcv2-tables list is **empty**, service line says **not found**, and the last line is
  **`KV_OK True True`**.
- **STOP-if:** any `kcv2_*` table listed or the service already exists → partly deployed; report to me.
  If the KV line is not `KV_OK True True` (error / `False`) → grant the VM identity **Key Vault Secrets
  User** on `kv-tc-vtwbowt3wtkpy` and re-run, or use **Appendix B**. If `az` itself errors (login /
  resource-not-found) → fix `az login` or the `$rg`/`$vm` names.

## 2. Copy the 3 files to the VM home (plain scp, no sudo, no az)

```
powershell -ep bypass -f .\t2_2_scp.ps1
```
- **OK:** three `100%` transfer lines.
- **STOP-if:** `Permission denied` / `No such file` → report.

## 3. Deploy: place files + md5-gate + migrate + install unit + md5-gate + enable (az run-command, root)

```
powershell -ep bypass -f .\t2_3_deploy.ps1
```
Self-gated: an md5 mismatch aborts before migrating / before enabling.
- **OK:** two **`MATCH`** lines → `created this run: ['kcv2_heartbeat', 'kcv2_index_ticks',
  'kcv2_quotes', 'kcv2_signals']` → **`UNIT_MD5_MATCH`** → `Active: active (running)` →
  **`=== DEPLOY_OK ===`**.
- **STOP-if:** any **`MISMATCH`** / **`STOP:`** / **`UNIT_MD5_MISMATCH_STOP`** line, a Python traceback,
  or no `=== DEPLOY_OK ===` at the end → report to me (nothing was enabled if it aborted early).

## 4. Acceptance gate — heartbeat + quotes + signals + logs (az run-command, root; wait ~90 s)

```
powershell -ep bypass -f .\t2_4_verify.ps1
```
- **PASS:** heartbeat has a **new row ~every 30 s**, **`rows_index` = 4**, **`rows_quotes` > 0**,
  **`index_ws_connected` = 1**, **`alarm` = 0** on every row; `kcv2_quotes` COUNT > 0 with SUM ≈ COUNT;
  `kcv2_signals` carry a non-null `computed_bar_ts_ms`; logs show `WS connected` + cycle lines.
- **STOP-if:** any `alarm = 1`, heartbeat not advancing ~30 s, `index_ws_connected = 0`, or tracebacks /
  `KalshiAuthError` / `Key Vault fetch failed` in the logs → do not trust the corpus; roll back (step 9)
  and report if it doesn't self-clear in a few cycles.

**Deploy is complete when step 4 PASSES.** Then append the deploy_log block from `T2_DEPLOY_RUNBOOK.md`
to `runbooks/deploy_log.md` with the verified UTC timestamp.

## 9. Rollback (az run-command, root; any time)

```
powershell -ep bypass -f .\t2_9_rollback.ps1
```
Prints `ROLLBACK_DONE`. The 4 `kcv2_*` tables are additive + harmless and are left in place.

---

## Appendix A — what each `kcv2_*` table is
- `kcv2_index_ticks` — raw cfbenchmarks index + trailing-60s TWAP per asset.
- `kcv2_quotes` — near-money both-sided Kalshi quotes (KX*15M + hourly), raw dollars, `band_pct` +
  `sum_to_1_ok` guard.
- `kcv2_signals` — lifted SFP state per asset with `computed_bar_ts_ms`.
- `kcv2_heartbeat` — one row per ~30 s cycle with row counts + `alarm`.

## Appendix B — EnvironmentFile cred fallback (ONLY if step 1's KV read failed / you want explicit creds)

Run **before** step 3:
```
powershell -ep bypass -f .\t2_B_envfile.ps1
```
- `az` (your identity) fetches `KALSHI-KAREN-*`; the values are **streamed to the VM over `ssh` STDIN
  only** (never printed, never in shell history) into an azureuser-owned **600** file
  `/home/azureuser/.config/kcv2-kalshi.env` (no sudo). The `/etc` drop-in (no secret) is installed via
  `az run-command` (root) so the packaged unit stays at md5 `bf001461`.
- **OK:** `wc -l` = **2**, perms `600 azureuser`, then `DROPIN_DONE`. Then continue at step 3.
- **STOP-if:** `STOP: PEM has real newlines...` (the KV PEM isn't single-line `\n`-escaped) → use managed
  identity instead. Rollback (step 9) also removes the drop-in; delete the env file separately with a
  plain `ssh azureuser@trading.jacksumner.com "rm ~/.config/kcv2-kalshi.env"`.
