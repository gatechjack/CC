# Flat-contracts sizing DEPLOY (2026-08-31) — ledger

Ships the CAPABILITY (`sizing_mode='contracts'` + the `contracts` column, migration 014, execution/broker/db/web).
**Nothing sizes differently after this deploy** — every sub stays `sizing_mode='fixed'` until the SEPARATE caps write
flips it. The deploy ships the capability; the caps write turns it on. Branch `pm-shard-scope-2026-08-30` @ `bd09b21`.

## ★ THE ORDER IS LOAD-BEARING (not incidental) — migration LEADS
This deploy set spans components with **two different schema-tolerance levels**, so the migration must land first:
- **Engine path (`execution.sub_config_from_row`) is pre-014 TOLERANT** — it reads `contracts` via `_row_get`, which
  catches a missing-column `IndexError`/`KeyError` and returns None → `CONFIG_DEFAULTS['contracts']=5`. It would not
  break against a schema-13 row.
- **Web path (`subdivision.get_subdivision`) is pre-014 INTOLERANT** — it HARD-SELECTS `s.contracts`, so on a
  schema-13 DB (column absent) the query raises `OperationalError` and the `/live` sub-division page 500s.
**GENERAL RULE (worth keeping): when a deploy set mixes components with different schema tolerance, the migration
leads and the ordering is load-bearing — the intolerant reader must never run against the un-migrated schema.**
Here: **S1 migration 014 → S2 code → S3 restarts.** Reverse it and the migration is fine but the web page breaks.

## SEQUENCE (each its own authorization; runner + one-liner per step, halt for the board)
- **S1 — migration 014 to live (DB write, NO restart).** Gate-1 backup (path + sha256 + `integrity_check`, ABORT if
  not ok) → pre-state → apply via the ephemeral byte-verified branch scratch (`db.init_db` runs pending migrations)
  → verify schema 13→14, `contracts` column present w/ DEFAULT 5, existing counts unchanged, `pm_subdivision_order`
  still 1, latch still `count_ceiling`. **★ Confirms whether the existing kalshi_jack/mlb row lands `contracts=5`
  (DDL default fills existing rows) or NULL — matters because the caps write assumes a value is there.**
- **S2 — code (execution.py, kalshi_live.py, db.py, subdivision.py).** Explicit manifest, Gate-A incl. transitive
  imports, per-file backups, forced 0644 + perms assertion, **box==branch sha256 as the GATE** (not a grep marker —
  last turn's own correction). NO restart in S2.
- **S3 — restarts (both az-root).** ENGINE `restart_tc.ps1` (bitunix heads-up — activates execution.py + kalshi_live.py
  + db.py in the running engine) + **pm_web** `restart_pmweb.ps1` (activates subdivision.py on `/live`).
- **S4 — post-check (read-only).** engine + pm_web PIDs changed; boot-reconcile clean; **★ latch + order count
  UNTOUCHED** (terminal safe state); `/live` still renders the first trade; **`sizing_mode` still `'fixed'` on the
  row — nothing behaves differently yet.**

## STOP CRITERIA (any -> halt + report, do not proceed)
Schema landing anywhere but 14; the latch or `pm_subdivision_order` count moving; `sizing_mode` changing; `/live`
breaking; the engine not coming back; any division missing from the startup lines.

## Results — DEPLOY COMPLETE 2026-08-31, all GREEN
- **S1 (02:04Z):** Gate-1 backup `~/pm_mig014_backup_20260831T020430Z.db` integrity `ok`; schema **13→14**; `contracts`
  column `INTEGER NOT NULL DEFAULT '5'`; **existing kalshi_jack/mlb row landed `contracts=5`** (DDL default filled it,
  not NULL); sizing_mode still 'fixed'; order count 1; latch `count_ceiling`. No restart.
- **S2 (02:07Z):** 4 files placed 0644, **box==branch sha256 MATCH on all four**, Gate-A + py_compile + live-tree
  import OK. Backup `~/pm_sizing_code_backup_20260831T020713Z`. No restart.
- **S3:** Jack ran `restart_tc.ps1` (engine, bitunix bounce) + `restart_pmweb.ps1` (pm_web).
- **S4 (02:13-02:14Z):** ★ latch `count_ceiling` + order count 1 + effective_armed false UNTOUCHED; ★ sizing_mode
  still `'fixed'` (caps 1/2/2/2, caps write not run); engine 106773→**107937**, pm_web 103913→**108138** (both
  changed); **boot-reconcile reconciled=True latched=False** (02:14:05, clean; one self-healing index-build
  transient); schema 14; `/live` **200** renders the first trade + held table (get_subdivision's new `s.contracts`
  SELECT works on schema 14); tile "1 live trade"; ALL divisions back (bitunix connected $144.21/$128.97 + reconcilers
  clean, poly_kalshi WIRED, PM driver WIRED, PEAD/MACE/Kalshi-arb/scout/copy/Donchian/SFP online). **Nothing sizes
  differently — capability shipped, OFF until the caps write.**

*Then, each separately: the caps write (sizing_mode='contracts', contracts=5, max_orders 20 / per_order $5.50 /
daily $60 / max_open $60) → re-attach xifutloong3 → arm → R8.*
