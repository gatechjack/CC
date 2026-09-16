# PHASE 7 RESUME HANDOFF — kcv2 DATABASE DROP (written 2026-09-14 for a COLD session)

You did not attend the session that wrote this. Read it top-to-bottom before touching anything.
Full phase detail is in the living tracking doc `reports/platform/LEGACY_PM_RETIREMENT_2026-09-12.md`
§19 (branch `legacy-pm-retire-2026-09-13`). This file is the "start here" pointer; that doc is the record.

**Resume timing:** AFTER MARKET CLOSE (Monday). The live PM division trades real money during games;
the DROP briefly blocks the engine's DB writes, so do it in a quiet window, and the drop-safety
"PM still placing" proof needs active play afterward to be meaningful.

---

## ★ DO THIS FIRST — THE GATE-A BLOCKER THAT STOPS YOU COLD
Gate A (Jack's ruling): an **off-device copy of the kcv2 archive must exist AND be verified readable
FROM WHERE IT LIVES before the drop.** Jack chose form **(b) a second PHYSICAL drive** — but **this
machine has ONE physical disk** (Samsung NVMe 953.9 GB, single volume `C:`, ~471 GB free). **There is
no valid target, so the session cannot reach the drop until one exists.** Options, in Jack's order:
1. **A UNC path on another machine (`\\host\share`)** — genuinely OFF-BOX, needs no hardware → the
   STRONGEST option and the one to pursue first.
2. A **USB / external drive** Jack attaches (copy to it, then hash-from-the-target).
3. **Azure blob** — best long-term (off-SITE: survives fire/theft/whole machine), but needs a storage
   account+container that DO NOT EXIST yet + auth + a new upload+read-back-verify runner. Off-SITE
   remains OPEN and worth doing properly later, when it is not the last gate before an irreversible step.
★ Verify the copy by **reading it back from the target** and hashing (`a4eef50f…` prod, `cff8a469…`
lab) — NOT from the source. A copy that wrote successfully can land corrupt and look fine from the sender.

Gate A's other two legs ARE already proven (do not redo): file integrity (byte+SHA256 == Phase 5) and
**restorability** (fresh restore today, all four counts + spot rows matched — see §19.2).

---

## THE EXACT REMAINING ORDER (do not re-derive; supersedes §17.8)
1. **Off-device copy** of BOTH archive files (prod `a4eef50f…`, lab `cff8a469…`), verified by reading
   back from the target. [BLOCKED until a target exists — see above.]
2. **Observer stop** — `trading-corp-kcv2-observer.service`, PID 679, writing ~130 quotes/30 s.
   **az-root → JACK'S ACTION** (agent writes+presents the runner, Jack runs it). ★ The box's NOPASSWD
   sudo is INERT — a root runner can report success having changed nothing. **Verify actual unit state
   independently after**, and confirm the kcv2_quotes row count is genuinely static (no other process
   picked up the writes), not merely that one unit is down. This goes before the delta so the delta is
   a fixed target, not a moving one.
3. **Delta-archive** everything written since the Phase-5 snapshot (the Phase-5 HWMs: quotes 12,034,120
   / signals 891,520 / index_ticks 445,756 / heartbeat 111,439). It is **LARGER than the +97,636 quotes
   measured on 2026-09-14** because the observer kept running after. Dump rows > HWM per table; VERIFY by
   restore-into-scratch, matching counts, spot-check content. **Copy the delta off-device too** — it is
   NOT covered by the Gate-A copy (it didn't exist when that copy was made).
4. **Full-DB backup** `cp data/trading_corp.db data/trading_corp.db.bak_pre_kcv2drop_<ts>` (~5.31 GB;
   29 G free is enough). Check PM health DURING the copy. Verify the backup is **RESTORABLE**, not merely
   that a file exists.
5. **DROP** the four kcv2_* tables — **enumerate from the schema, do not trust this list**; as of
   2026-09-14 they are `kcv2_heartbeat`, `kcv2_index_ticks`, `kcv2_quotes`, `kcv2_signals` (8 indexes drop
   with them). Jack-run DB write (reserved). The DROP takes a write lock for seconds-to-~1 min and the
   engine's writes block for that window; an in-flight order blocks then proceeds. Say when you take it.
6. **NO VACUUM** — Gate B, Option A (below).
7. **Prove the live division untouched** (below).

## GATE B — RULED: DROP ONLY, NO VACUUM (and what it defers)
Disk is 55 % used / 29 G free → no space pressure, so the ~3.15 GB reclaim buys nothing now; Options B/C
would cost an engine stop + PM disarm + a restart + a full Phase-6-style gate cycle to reclaim disk
nobody needs. ★ **THE FILE WILL STILL BE ~5.31 GB AFTER THE DROP**, with ~3.15 GB of free INTERNAL pages
(reused by future growth). **THE DATA IS RETIRED REGARDLESS.** A later agent must NOT read the unchanged
file size as a failed drop. **Measured evidence:** the engine holds the DB open (PIDs 397109 engine +
656 sfp), WAL mode, `freelist_count=0`, `auto_vacuum=0` → a full VACUUM needs the **ENGINE STOPPED**, not
merely PM-disarmed. VACUUM can fold into any future restart-for-another-reason at zero marginal cost.

## ★ ARM-COUNT TRUTH — THE PROOF OBLIGATION (it has confused a count once already)
**31** `agent_state` arm rows in **`trading_corp.db`** (the DROP TARGET: 1 `arm:global` + 15 kalshi_jack +
15 kalshi_karen, all armed / 0 latched / 0 trigger) vs **44** `pm_subdivision active=1` rows in
**`prediction_markets.db`** (jack 23 / karen 21 — a DIFFERENT database the drop never touches). Different
things, different DBs. **After the drop, prove THE 31 ROWS + the poly_kalshi persist-halt row
(`agent=strategy_state key=poly_kalshi_mlb {"halted":true,...}`) byte-identical — in `trading_corp.db`,
the DB actually operated on.** ARM-READ TRAPS: use `data/trading_corp.db` (not prediction_markets.db, not
the 0-byte `trading_corp.sqlite` decoy); the `agent_state` column is `agent`, NOT `actor`; read
`value_json` from persisted rows, never a status call; `agent_state` holds 21 agents — a wrong-agent
filter once missed the persist-halt row entirely. Runner: `cc/recon_arm_read_ro.ps1`.

## ROLLBACK REALITY — state it before authorising, not after
After the DROP, recovery is **NOT HOT — it needs an ENGINE STOP.** Routes: (a) restore the full-DB backup
`.bak_pre_kcv2drop` (cp back, engine stopped, ~minutes for 5.3 GB); (b) if that's lost, re-restore kcv2_*
from the archive (`gunzip -c … | sqlite3`, engine stopped, **~13 min** — measured by the 2026-09-14
restore) + the delta-archive. After the drop the corpus exists only in the full-DB backup + the local
archive + the delta-archive; **if all are lost the 3.15 GB corpus is gone forever** (hence Gate A).

## ★ PROOF OF LIFE AFTER THE DROP
A real `pm_subdivision_order` with `dry_run=0` placed AFTER the drop. ★ Fresh heartbeats and green arm
rows are EXACTLY what the 2026-09-04 failure looked like for 28 h — PM sat armed and not trading behind
nine green rows and it took a chance observation to find. **Liveness is not proof; a real placement is.**
Runner: `cc/recon_liveness_ro.ps1` (heartbeats + orders-by-dry_run + active subs).

## TONIGHT'S CLOSING STATE (2026-09-14 ~01:55Z) — the do-no-harm baseline to match
- **31** arm rows, all armed / **0 latched** / 0 trigger. poly_kalshi persist-halt present (redundant now).
- engine trading-corp **397094** (boot 00:25:13Z), pm_web **381803**, sfp **656** — all active, NRestarts 0.
- observer `trading-corp-kcv2-observer.service` **679** still active+writing (kcv2_quotes max_rowid 12,131,756).
- 32 heartbeats `state=evaluated`, fresh, 0 errors; 44 active subs (jack 23/karen 21); **390 open positions**.
- last order id **820 @ 00:28Z** (late-night quiet — evaluating, not placing; NOT a fault).
- schema head **23**; prod-live **8f35f254**; trading_corp.db **5,313,138,688 B**; 47 tables (43 after drop).
- Archive: `C:\Users\AA Incorporado\kcv2_archive_2026-09-13\` — `kcv2_prod_tables.sql.gz` 278,591,317 B
  `a4eef50f…`, `kcv2_lab.db.gz` 110,760,320 B `cff8a469…`. Fresh-restore scratch `cc/_p7_verify_scratch.db`.

## EVERYTHING STILL OPEN BEYOND PHASE 7
- **The deferred file-deletion closure problem — ✅ COMPUTED 2026-09-16 (session 8). See tracking-doc §20.**
  Tranche 1 = 49 files pure-deleted, BUILT + PROVEN LOCAL (branch `legacy-pm-untangle-2026-09-16` @ `ed6d9b83`,
  off prod-live `3dd15c10`, NOT pushed; py_compile 736/0, import-clean 0, §16.7 shared files byte-identical).
  Keepers confirmed (`_weather_math` ← `path_logger/logger.py:31`; `kalshi_crypto_v2_observer` ← PID 679) PLUS
  a closure surfaced **new** web/routes.py couplings (kalshi_copy_trader/polymarket_copy_trader/roster_split/
  polymarket_whale_analyst/polymarket_whale_audit_cache, all lazy in-handler → 500 at request time). The 13
  shared-blocked keepers + woven web/data.py dashboard need a Jack-gated graft pass (§20.4/§20.6). Deploy of
  tranche 1 = FF `git push origin legacy-pm-untangle-2026-09-16:prod-live` (after 16:00 ET; pure deletion, no restart).
- **The woven ~2,500 LOC `web/data.py` legacy PM dashboard** + its routes (deferred; legacy-data-only).
- **API cancellations:** Apify is the real paid one (legacy-only, was still billing until the timers +
  loop were disabled); Finnhub is a dead/free win; **Anthropic and Kalshi/Polymarket keys are SHARED —
  NEVER cancel them.**
- ~~**The Polymarket USDC drain — it must GATE key removal.**~~ **VOID 2026-09-16 (Jack): the Polymarket credentials TRANSFER to the live PM division → funds never stranded, no drain, keys are NEVER-CANCEL alongside Anthropic + Kalshi. See tracking-doc §3/§9/§10.**
- **The off-SITE blob copy** (Gate A's stronger long-term form).
- The redundant poly_kalshi persist-halt row (leave it) and the `strategies.yaml.block_bs` scratch (leave it).

## RESERVED FOR JACK (unchanged)
PUSH · RESTART · ARM/HALT/DISARM · any az-root write (incl. the observer stop) · THE DROP · deleting
anything. No ad-hoc ssh — validated `.ps1` runners only, PRESENTED AND WAITED ON. Agent-run az-root
writes are classifier-BLOCKED regardless of authorization → Jack runs them. Classifier block → STOP,
report verbatim. `az run-command` TRUNCATES stdout; multi-line `@'...'@` to `--scripts` returns EMPTY and
does NOT run — use single-line no-quote az + the RO ssh-STDIN channel for anything complex (see
[[command-paste-rule]]).
