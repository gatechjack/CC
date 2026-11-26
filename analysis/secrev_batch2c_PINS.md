# Batch 2c -- staged pins (branch `secrev-batch2c-2026-11-25`, base prod-live `110d735c`)

Resolution verified on a full prod-mirror PC scratch venv (python 3.12, 158 pkgs, `pip check` clean).
Each set is the **minimum consistent** change (every installed consumer's specifier re-checked by hand --
pip's `--dry-run` silently tolerates installed-but-unnamed conflicts, so a naive dry-run under-reports).

## Exact version pins

| set | package | from | to | note |
|---|---|---|---|---|
| 2c-1 | aiohttp | 3.13.5 | **3.14.5** | CVE-2026-69244/128124/128128; consumers ccxt/web3/anthropic/azure-core all satisfied |
| 2c-1 | yarl | 1.23.0 | **1.25.1** | forced by aiohttp>=3.14 (aiohttp needs yarl>=1.17,<2.0) |
| 2c-1 | fastapi | 0.136.1 | **0.165.0** | GHSA-f56c quadratic-param DoS (fix floor 0.163.2) |
| 2c-1 | starlette | 1.0.1 | **1.10.0** | fastapi 0.165 needs >=1.6; 1.10.0 >= 1.0.1 so BadHost KEV CVE-2026-48710 stays fixed; streamlit 1.57 OK (>=0.40) |
| 2c-1 | opentelemetry-api | (absent) | **1.45.2** | NEW transitive dep pulled by fastapi 0.165.0 |
| 2c-2 | cryptography | 47.0.0 | **50.0.2** | CVE-2026-69248 (x509 name-constraint); NOT reachable here, defense-in-depth |
| 2c-2 | msal | 1.36.0 | **1.39.0** | REQUIRED companion: msal 1.36.0 pins `cryptography<49`. 1.39.0 drops the cap |

Unchanged / verified-not-needed: azure-identity 1.25.3, azure-core 1.39.0, azure-keyvault-secrets 4.11.0
(azure-identity needs only `cryptography>=2.5`; accepts msal 1.39.0). web3 excluded (M3, not app-imported).

## requirements.txt (DONE on this branch)
`fastapi>=0.163.2` and `starlette==1.10.0` edited with secrev comments. The other 5 are transitive -> `.lock` only.

## requirements.lock -- regenerate at FOLD time (NOT edited here; do NOT leave stale version+old-hash)
The `.lock` is `--require-hashes` style. Bumping a version WITHOUT its matching sha256 breaks the lock, so it
must be regenerated with correct **Linux (manylinux cp312 x86_64) wheel + sdist** hashes, not the PC wheels.
Lines to update (current -> new):
  L15   aiohttp==3.13.5   -> 3.14.5
  L701  cryptography==47.0.0 -> 50.0.2
  L1035 fastapi==0.136.1  -> 0.165.0
  L1869 msal==1.36.0      -> 1.39.0
  L3438 starlette==1.0.1  -> 1.10.0
  L4022 yarl==1.23.0      -> 1.25.1
  + ADD a new `opentelemetry-api==1.45.2 \` block with its hashes.
Method (PC-side, no box touch): for each pkg,
  `pip download --only-binary=:all: --platform manylinux2014_x86_64 --python-version 312 --implementation cp \
     --no-deps <pkg>==<ver> -d <tmp>` then `pip hash <tmp>/<wheel>` (+ the sdist hash from PyPI). List ALL
  matching x86_64 cp312 wheel hashes + the sdist hash under each pin (pip accepts any one that matches).

## Deploy order (see 00_BATCH2C_SCOPING report item 7)
2c-1 first (safe, strong fastapi test coverage). 2c-2 after it is stable (isolated -- msal touches the Key
Vault auth path which has NO PC test coverage; verify the Key Vault fetch burst live post-restart).
Both restart all 4 app services (shared venv). deploy scripts: `secrev_batch2c1_deploy.sh`,
`secrev_batch2c2_deploy.sh`; rollback: `secrev_batch2c_rollback.sh <backup_dir>`. DEPLOY+RESTART+FF reserved for Jack.
