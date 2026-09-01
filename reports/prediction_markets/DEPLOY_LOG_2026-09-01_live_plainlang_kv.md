# DEPLOY LOG — 2026-09-01: /live plain-language + KEY_VAULT_URI (one bounce)

**LIVE on prod** (file-copy deploy; prod-live git ref NOT advanced, per the box-is-truth model). Branch
`pm-multiaccount-2026-09-01`. Two INDEPENDENT changes activated on ONE pm_web restart.

## Sequence
- **Step A** (`pm_multiacct_deploy.ps1`, board-authorized) — staged 5 source + 3 test files, file-by-file
  (git archive of ONLY those paths, never the tree → no cross-division revert). Box-is-truth pre-guard PASSED
  (box hashes == verified baselines), per-file backup `~/pm_multiacct_deploy_backup_20260901T053314Z`, install
  0644, post-hash NEW-OK, import-closure PASSED. **NO restart.**
- **Step B** (`pm_kv_fix.ps1`, Jack ran) — az-root unit edit (KEY_VAULT_URI) + daemon-reload + pm_web restart.
  Verdict line: `ANTHROPIC_API_KEY loaded from Key Vault (…) -- Analyze narration ENABLED` = **STATE 2**.
- **Step C** (`pm_multiacct_postcheck_ro.ps1`) — confirmed both, separately (below).

## Result (post-check, observed 2026-09-01T05:44Z)
- pm_web **133967** (was 124014, restarted) · **engine 132470 UNTOUCHED** · healthz ok, schema 15.
- **KV = STATE 2 (SUCCESS):** KEY_VAULT_URI active on the unit; startup log = narration ENABLED. (The
  post-check's `/proc` "ANTHROPIC_API_KEY absent" is a false-negative — it's set at RUNTIME via os.environ, so it
  never appears in `/proc/PID/environ` (static startup env); the startup log line is authoritative. Runner fixed.)
- **/live = LIVE:** all 5 deployed hashes MATCH the new branch code; `pm-ticker-raw` present; describe_market
  renders "San Diego Padres to win", "Cleveland Guardians to win", "Seattle Mariners to win"; raw tickers kept.

## Live file hashes (the code now running)
- market_describe.py `7b9b41e1…` (NEW) · subdivision.py `3dd62118…` · web/app.py `b3591789…` ·
  templates/pm_live_subdivision.html `9e00b356…` · static/pm.css `2bccdd24…`
- (This deploy also brought the /live template current: the box was on `f4c16d6` `99b06b4a…`, an ancestor; the
  branch's committed-but-previously-undeployed OPPOSED rendering from `2332de7` landed too — harmless, 0 opposed rows.)

## Gate-A false-negative — recorded lesson
The Step-A Gate-A pytest first FAILED on the pre-existing broken `web3.tools.pytest_ethereum` plugin
(`eth_typing.ContractName` ImportError) — a KNOWN box quirk (mace-box-scratch harness) that the runner did not
carry. Verified read-only that all 34 tests pass with `-p no:pytest_ethereum`; runner patched to the canonical
flag; the mitigation added to the runner-authoring rules ([[command-paste-rule]]) so future runners inherit it.
Corollary recorded: when a runner reports a gate failure, confirm it is REAL before restoring/aborting.

## Rollback (if ever needed)
Restore the 5 files from `~/pm_multiacct_deploy_backup_20260901T053314Z` (market_describe.py is NEW — delete it)
+ restart pm_web. The KV unit line is idempotent; to remove it, drop the `Environment=KEY_VAULT_URI=…` line +
daemon-reload + restart.
