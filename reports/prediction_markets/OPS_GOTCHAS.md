# Prediction Markets — Ops Gotchas (standing)

## ★ GOTCHA 1: root-owned artifacts vs the azureuser runtime (bit us on the first cron fire, 2026-08-23)

**Symptom:** the nightly cron (`20 3 * * *`, runs as **azureuser**) fired on time but died with
`sqlite3.OperationalError: attempt to write a readonly database` at `stats.py:89 rollup`.

**Root cause:** every box operation in the P1 build ran as **root** — because the sanctioned deploy channel
`az vm run-command invoke` executes as root (control-plane), not sudo. So every artifact root created —
here `data/prediction_markets.db` — was **root:root, mode 644**. The runtime user is **azureuser** (the cron,
and any future azureuser-run job), which then has read-only access to a 644 root file: it can pull data but
cannot write the rollup.

**Why it hid until the cron:** all my manual backfills/refreshes ALSO ran via `az run-command` = root, so they
wrote the root-owned DB fine. The mismatch only surfaced the first time the artifact was written by the actual
runtime user (azureuser), i.e. the cron.

**Fix applied 2026-08-23 (Board-authorized):** `chown azureuser:azureuser data/prediction_markets.db`
(+ `-wal`/`-shm` if present — absent here), then re-ran `refresh --cap 50000` **as azureuser** (`runuser -u
azureuser`) to prove it: `REFRESH_EXIT=0`, 12/12 complete, 0 failed/partial, pulled==stored, rollup updated_ts
advanced, ownership now azureuser:azureuser. The `data/` dir was already azureuser-owned; only the .db file blocked.

**STANDING RULE — this will bite again on ANY file P1 writes (logs, exports, new DBs, new migrations' sidecars):**
1. **Prove writes as the runtime user, not root.** After any change that touches a P1 artifact, verify the
   write path works as **azureuser** — e.g. `runuser -u azureuser -- bash -c '… pm_cli … '` — NOT as root.
   A root re-run silently "works" while leaving the azureuser cron broken. (This is exactly what would have
   masked the bug if I'd re-run the failed refresh as root.)
2. **Own artifacts as azureuser from creation.** Future runners that create/write PM files should run the
   mutating step via `runuser -u azureuser --` (or `sudo -u azureuser`) so the file is azureuser-owned at
   birth. If a root step is unavoidable, immediately `chown azureuser:azureuser <artifact>` afterward.
3. **The PM DB, its `-wal`/`-shm`, `pm_refresh.log`, and any export must all be azureuser-writable.** The
   `data/` dir is azureuser-owned (good); the risk is individual files created by a root process.
4. **Never chown broadly.** Touch only the specific PM artifact. `data/trading_corp.db` (legacy) is
   azureuser-owned and off-limits.

**Operational note (not a defect):** the nightly refresh takes ~15-18 min (full 12-wallet re-pull, ~28k rows,
rate-limited by the public data-api). It fires 03:20 UTC, finishes ~03:38, no overlap with other 03:xx jobs.
429 backoff + the per-wallet completeness gate make a throttled run safe (it just takes longer, or marks a
wallet PARTIAL and excludes it from ranking rather than corrupting).
