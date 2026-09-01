# Engine bundle DEPLOYED LIVE — 2026-09-01 (opposed-memory + M3-writer + migration 016)

Deployed to the box by a file-by-file box-is-truth graft (NOT a ledger/branch advance — prod-live is deliberately
NOT advanced). Engine restarted once (all divisions). Order path never touched.

## What shipped
- **opposed-memory** (`execution.py`, `live_driver.py`) — the deliberate bound on the opposing-guard fee loop.
- **M3-writer** (`shard_snapshot.py` [new], `shard_snapshot_task.py` [new], `main.py` boot wiring) — the 5-min
  per-account shard-balance snapshot timer.
- **migration 016** (`db.py`) — `pm_shard_balance_snapshot` table (schema 15 → 16).
- `shard_balance.py` was already on the box (import-closure dep satisfied) → NOT redeployed.

## The 4-step deploy (each HALT-authorized)
1. **Migration leads** (`pm_bundle_step1_migrate`): Gate-1 backup (consistent DB snapshot via sqlite backup API +
   db.py copy) → precondition schema==15 → deploy db.py (sha `f85ce8714c71`) → `init_db` applied only 016 → schema 16,
   table present, **engine PID unchanged (no restart)**.
2. **Files + graft** (`pm_bundle_step2_files`): execution/live_driver/shard_snapshot/shard_snapshot_task deployed
   (all sha-verified) + `main.py` **grafted** (the box main.py had diverged only by the absence of my 35-line M3
   block; the LF patch applied +35/−0, sha `cc733a17989d`, py_compile OK) + **Gate-A import closure green**.
   ★ Line-ending trap: the box main.py is LF; the git-generated patch was CRLF (autocrlf) → normalize both to LF.
   (A `grep -c $'\r'` false-positive briefly suggested CRLF on the box; `cat -A`/`file` corrected it —
   grep-is-not-a-state-check.)
3. **Guarded restart** (`pm_bundle_step3_restart`): atomic — checked in-flight orders and restarted in the SAME root
   shell only if none (in_flight=0). **PID 132470 → 139938, active/running.**
4. **Post-check** (read-only): all green (below).

## Verification (all green)
- Schema 16 live; `pm_shard_balance_snapshot` present.
- Arm **UNTOUCHED, latched=false** on global + jack-mlb (effective_armed=true) ⇒ **boot_reconcile clean**; R8 resumes.
- **Every division back incl. bitunix** (bitunix_futures/sfp live, PEAD, MACE 4 loops, PMCC, poly_kalshi_mlb, …);
  the only errors are pre-existing + unrelated (fidelity playwright browser → paper fallback; BTC earnings 404).
- **M3-writer PROVEN end-to-end** — first tick wrote `kalshi_karen`: total **$462.83**, per-shard
  `{0:$25.01, 3:$437.83}`, `has_breakdown=True` (the first shard-aware read of Karen's account — the number the
  total-balance figure was hiding). `kalshi_jack` fail-softed on a transient `Server disconnected` and retries next
  tick (fail-soft design working under real conditions).
- **Opposed-memory ACTIVE** — opposing-guard skipping the pre-existing contested pair (`0x19a016da…`) with
  **`opposed_closes=0`** every cycle: no re-close, no fee churn (the flicker bug is fixed on live).
- **NO-branch (sign convention)** — the journal currently holds two NO-leg positions, each 5 contracts (journal
  signs NO negative → **−5**): `KXMLBSPREAD-…SEABOS-BOS2` (away-side spread) + `KXMLBTOTAL-…SDCIN-10` (Under).
  boot_reconcile matched them (no latch = venue agreed). These are the KNOWN NO positions (my deploy never touched
  the order path — not a new NO fill, not the NO-leg STOP). The engine's boot_reconcile is silent-on-clean, so the
  literal venue `−5.00` was not logged; available on request via a read-only R7.g-style keyed venue read.

## ★ FILED: `live_driver.py:639` logging footgun (fixed in branch; rides the NEXT engine restart)
`log.info("pm_live_driver cycle: %s", summ)` where `summ` is a non-empty dict → stdlib logging treats the lone dict
as a `%`-MAPPING → `TypeError: not all arguments converted` → **the cycle-summary line is EATEN, exactly on active
cycles** (placed/errors truthy). Pre-existing (not introduced by this deploy), but live. **Fixed** →
`log.info(..., str(summ))`. Not worth a dedicated restart (logging-only); it ships with the next engine window.
Lens recorded: [[a-log-call-can-silently-fail-to-emit]].

## ★★ M4 CAVEAT — carry forward: Karen's half of M4 is proven by TEST ONLY until she exists in Authelia
Authelia (read-only investigation, 2026-09-01): file backend (`/etc/authelia/users_database.yml`), **`watch:false`**,
and **`access_control` gates BOTH consoles by `subject: 'user:jack'` with `default_policy: deny`** — so today ONLY
jack can authenticate to predictions/trading. Adding Karen requires (a) a users-db entry, (b) an `access_control`
rule `subject: 'user:karen'` for predictions.jacksumner.com, (c) an authelia restart (watch off + ACL is main-config).
All three are authelia-owned (az-root, Jack's). **Until Karen exists, "M4 verified" covers JACK ONLY** — his side
(admin sees all accounts, the gates admit him, a non-admin POST is 403) is verifiable on live without her; Karen's
scoping (she sees only her account; her POST is 403) is proven by `test_m4_gates.py` (TEST), never yet by live
observation. Do NOT read "M4 verified" as covering both users.

## Backups (box; do NOT restore onto live without care)
`data/prediction_markets.db.bak_mig016_20260901T160113Z` (pre-016) + `db.py.bak_bundle_20260901T160113Z` +
`execution.py/live_driver.py/main.py.bak_bundle_20260901T160621Z`.

## Branch
`pm-multiaccount-2026-09-01`; the live_driver:639 fix is a NEW commit on top (post-deploy) — it makes the branch
live_driver.py DIVERGE from the box by exactly that one-line fix, which rides the next engine deploy.
