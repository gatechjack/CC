# Reconciliation Exclusions & Non-Overlay Path Mappings

Canonical manifest for git ↔ prod drift sweeps. `prod-live` is the deploy base and single
source of truth (prod is not a git repo). Full sweeps compare each tracked file's
LF-normalized md5 against its deployed copy on prod. This manifest makes sweeps
**self-classifying**: apply the PATH MAPPINGS, auto-classify the EXCLUSIONS, and treat
everything else untracked-on-prod as REAL drift to reconcile.

## Exclusions — deployed-but-intentionally-untracked (NOT drift)

| prod path | class | reason |
|---|---|---|
| `data/kalshi_crypto_arb_cooldowns.yaml`, `data/kalshi_weather_arb_cooldowns.yaml` | runtime state | engine-written cooldown state — **never track** |
| `/home/azureuser/pead_earnings/earnings_watch.db`, `.db-wal`, `.db-shm` | runtime sqlite | watcher DB + WAL/SHM — **never track** |
| `/home/azureuser/pead_earnings/pead-earnings-watcher.service` (dir copy, md5 `b2157ffe`) | stale benign copy | stale Jul-20 copy; systemd runs the `/etc` unit (tracked at `pead_earnings/pead-earnings-watcher.service`). Fix opportunistically on a future PEAD deploy — **never as a standalone prod touch** |
| `config/Lets start Phase 1 — Plumbing now.txt` | scratch note | prod cleanup candidate (manual) |
| `deploy/2026-07-08_pmcc_lifecycle_fix/backfilled_ids.txt` | deploy artifact | prod cleanup candidate (manual) |

## Path mappings — non-overlay deploy targets

Most tracked files map `repo/<path>` → `/home/azureuser/trading_corp/<path>` (the overlay root).
These deploy OUTSIDE the overlay and must be mapped explicitly when sweeping:

| repo path | deployed to |
|---|---|
| `pead_earnings/*.py` | `/home/azureuser/pead_earnings/` |
| `pead_earnings/*.service`, `pead_earnings/*.timer` | `/etc/systemd/system/` (compare vs the **installed** units) |
| `card_assets/card_data.py` | `/home/azureuser/card_assets/` |

## Standing rule

Full sweeps must apply these mappings and auto-classify these exclusions; anything else untracked-on-prod is REAL drift.
