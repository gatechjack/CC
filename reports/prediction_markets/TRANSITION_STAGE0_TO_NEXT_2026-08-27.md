# Prediction Markets — TRANSITION: Stage 0 → next code agent (2026-08-27)

**Read this first. Two things a fresh agent will otherwise get wrong are at the top.**

Branch: `prediction-markets-stage0-2026-08-26` @ **`5ce939b`** — **pushed to `origin`** (github.com/gatechjack/CC), branch under its own name (NOT prod-live, NOT main). `origin` tip == local `5ce939b` (ls-remote confirmed).

---

## (i) ⚠️ MIXED STATE — the single most important fact

**The live PM DB is at schema 8 (migration 008 applied 2026-08-27 02:15Z). The runtime code on the box is still the OLD pre-Stage-0 `db.py` / `paper.py` / `farm.py` / `stats.py`.** The `active`-flag gating exists ONLY on the branch, not on the box.

Therefore, all of the following are **CORRECT, not bugs**:
- **`/farm` renders exactly as it did before Stage 0** (byte-identical: 228,564 bytes both sides of the ALTER). The running old code does not reference `active` and ignores the three new columns.
- `pm_web` `/healthz` now reports `pm_db_schema_version: 8` — because healthz reads the DB, not the code. The page behaviour is still old-code behaviour.
- **Nothing auto-migrates unattended.** The runtime `db.py` knows only migrations 1–7, so its `init_db()` is a no-op on a schema-8 DB. Verified live: the 03:20 UTC cron `pm_cli refresh` ran clean on the migration path (see §Cron below).

The new columns (`active INTEGER DEFAULT 1`, `removal_reason TEXT`, `removal_ts INTEGER`) + index `ix_pm_watchlist_active` exist on live; all 114 `pm_watchlist` rows are `active=1`; **nothing has been deactivated** (`active=0` count is 0).

## (ii) WHERE THE THREE-RUNG DEPLOY LADDER STANDS

- **Rung 1 — DONE** (migration 008 applied to live). Execution record: `PM_REBUILD_PLAN_2026-08-26.md` §Stage-0.
- **Rung 2 — NOT AUTHORIZED and NOT PREPARED.** Deploy the gated code (`db.py`+`paper.py`+`farm.py`+`stats.py`) + the **only** pm_web restart in the ladder. No deploy artifact, no prod-live ledger commit exists yet.
- **Rung 3 — NOT AUTHORIZED.** The 22-row `active=0` write (cbb×3 / fifwc×8 / unknown×11), stamping `removal_ts`.
- **Rungs 2 and 3 each require Jack's separate, explicit authorization. Do NOT treat rung 1's completion as momentum.**

---

## Facts (each with its evidence)

### The branch — 5 commits, pushed
| SHA | Contains |
|---|---|
| `7cec332` | Migration 008 (`pm_watchlist.active`/`removal_reason`/`removal_ts` + index) + gate 6 consumers; `test_removal_gate.py` (12 BASIS tests). |
| `1c4327e` | Gate `stats.query_scoreboard` (7th consumer) + BASIS test; **PM_REQUIREMENTS.md** created + docstring pointer; plan deploy-ladder + migration renumber. |
| `01bb92a` | Prove `query_scoreboard` join is **pair-grain** (fan-out test); cron-window rung-1 note; R5 ingest-all-categories. |
| `aac882b` | Rung-1 pre-flight: `init_db` trigger enumeration, online-backup + rollback, `removal_ts` stamp, rung-2 governance. |
| `5ce939b` | **Rung-1 EXECUTION RECORD** (this migration). |

Base = `f4fb61d` (the CP3b-2 branch HEAD, Jack-confirmed). `main` (`2c8aa23`) and `origin/prod-live` (`95e78c4`) untouched.

### Rung-1 execution record (2026-08-27 02:15Z, fail-closed runner `pm_stage0_rung1.sh`)
- **Backup (rollback material, KEPT):** `~/pm_stage0_gate1_dbbackup_20260827T021526Z.db` — `PRAGMA integrity_check=ok`, schema-7 snapshot, 25,083,904 bytes, **sha256 `dfcb8ad78027b68826bed75c86e04022c744ef1a9bf3ff5ef6be1298b15820b5`**.
- **Applied via `init_db()` from a byte-verified scratch extract** (`db.py` sha256 == branch `76eb52b2…dbc93782`); runtime `db.py` NOT touched; scratch removed.
- **Byte-identical `/farm` proof:** 228,564 bytes before AND after → behaviour-neutral. pm_web PID **40483 NOT restarted**; engine PID **89366** unchanged.
- **Post:** schema 7→8; cols `active`/`removal_reason`/`removal_ts` (INTEGER/TEXT/INTEGER) + `ix_pm_watchlist_active` present; pm_watchlist 114 / active1 114 / active0 0 / removal_reason 0 / removal_ts 0; pm_paper_trade 102.

### The backup's LIMIT (do not misuse it)
It reverts **SCHEMA, not state.** Every PM write since 02:15Z — the 03:20 cron refresh rows, any `pm_analysis_cache` writes — is **absent** from it. **It is not a general restore point and gets staler daily.** Rollback via it loses all writes since 02:15Z.

### Rollback cost
Restore-from-backup requires **stopping pm_web first** (it holds live handles) — strictly **more disruptive than the ALTER it reverts**. SQLite on the box is **3.37.2** (≥3.35 → `DROP COLUMN` is available as a lighter, non-total alternative, but leaves `schema_version` at 8). Rollback is Jack's call, not an agent's initiative.

### Rung-2 outstanding blocker
The **prod-live artifact-ledger commit has NOT been authored.** It is a rung-2 deliverable and nothing exists yet. Standing governance requires advancing `prod-live` with a byte-verified box==branch commit recording the deployed **PM-ONLY** files (never `persistence/db.py` — that is the engine/MACE shared file).

### Hard constraint — do NOT move `origin/prod-live @ 95e78c4`
MACE forks from that tip. Rung 2 advances prod-live by a **NEW fast-forward commit only** — **no amend, no rebase, no force-move** — leaving `95e78c4` an ancestor.

### Where the requirements live
`reports/prediction_markets/PM_REQUIREMENTS.md`, referenced from `trading_corp/prediction_markets/__init__.py`'s docstring. Standing requirements:
- **R1** — the Stage-1 paper rollup MUST gate `active=1`.
- **R2** — Stage 4 needs a **CATEGORY-level** exclusion at candidate SELECTION; a row flag cannot stop a newly-discovered pair (the seed writer defaults `active=1`).
- **R5** — **ingest stays all-categories**; category exclusion is a presentation/selection concern, NEVER an ingest concern.
- Also R3 (resolution from gamma, never `/closed-positions`) and R4 (every displayed number needs a BASIS test).

### Migration renumber (008 consumed by Stage 0)
Stage 1 `pm_paper_category_stats` → **009**; Stage 4 `pm_search_run` → **010**; Stage 5 `loss_completeness` → **011**.

### Operating rules this session ran under
Report a SHA for every commit; **verify on the box, never narrate** ("a report without a commit SHA is not evidence"); **no live step without Jack's explicit per-step authorization**; never edit legacy; `poly_kalshi_mlb` and the trading engine are out of scope; box access is via validated `.ps1`/`.sh` runners Jack authorizes (fail-closed for any live write).

### Known box quirks
- The venv's **`pytest_ethereum` plugin is broken** (`eth_typing.ContractName` ImportError) and aborts pytest startup — any PM box-scratch must disable it (`-p no:pytest_ethereum`, discovered by entrypoint name). Test-tooling only; the engine uses `web3` fine.
- `az vm run-command` serializes and truncates stdout — the sanctioned path is `ssh azureuser` streaming a CR/BOM-stripped script; code deploys go via the root `az` channel.
- pm_web = `prediction-markets-web.service` on **`127.0.0.1:8081`** (loopback; behind Caddy+Authelia). PM DB = `~/trading_corp/data/prediction_markets.db`.

---

## OPEN / UNRESOLVED (honestly labelled)

1. **★ 03:20 refresh — one whale FAILED (surfaced at retirement, flagged for Jack's ruling).** The 2026-08-27 03:20Z cron reported `complete: 13, partial: 0` **plus one `failed`:** wallet `0x767a7964deeea63dddd0cba6db39503f328d8ac5` — `IntegrityError` **PK COLLISION** (1208 pulled → 1207 distinct PKs; 1 key would be silently collapsed by INSERT OR REPLACE). This is the **P1-era `_assert_no_pk_collision` guard** (migration 002) refusing to silently lose a row — **in the `pm_closed_position` ingest path, orthogonal to rung 1** (the ALTER touched `pm_watchlist`, not ingest; the runtime ingest code is unchanged). **NOT caused by Stage 0**, but it is a real recurring ingest anomaly (that whale is not refreshed this cycle). Same class as the P1 Kickstand7 collision. **Jack to rule whether it blocks rung 2** (my read: it is independent of the ladder, but it is "something off" so I am not declaring it non-blocking on my own).
2. **pm_web WAL-contention** at rung 1 was reasoned and **observed clean** (byte-identical `/farm`, no `SQLITE_BUSY`, no partial write) but **not measured under induced load**. Established from WAL semantics + `busy_timeout=5000`, not empirically stress-tested.
3. **Backup co-location:** the rung-1 backup sits on the **same disk** as the live DB (`~/`). Not off-host.
4. **Engine PID history 37596 → 89366 unexplained.** Snapshot: PID 89366 up since **2026-08-26 18:26:16Z**, `NRestarts=0`. So the change was a restart ~18:26 Aug 26; the *reason* was never established. The PID-history probe was authored (`cc\pm_pid_history_probe.ps1`, read-only) but **never run**.

## Deploy-log status
There is **no central `deploy_log.md`** on the box or in the repo (checked: `~/trading_corp/deploy_log.md` absent, `~/trading_corp/runbooks` absent; `~/trading_corp/deploy/` exists = per-deploy `RUNBOOK.md` convention, none for PM; PM deploys historically use `reports/prediction_markets/*_DEPLOY_COMPLETE.md`). **Rung 1 is recorded in the plan (execution record), memory, and this transition doc — but NOT in a box deploy runbook.** *Question for Jack: add a PM `DEPLOY_COMPLETE`-style entry / `deploy/` RUNBOOK for rung 1?* Not written unasked.

## Leave-it-running snapshot (2026-08-27T03:51:25Z, read-only, actual machine output)
- Engine `trading-corp.service`: MainPID **89366**, NRestarts 0, active/running, up since 2026-08-26 18:26:16Z.
- pm_web `prediction-markets-web.service`: MainPID **40483**, NRestarts 0, active/running; `/healthz` **200** (`schema_version: 8`), `/farm` **200**.
- Live PM DB: schema **8**; pm_watchlist **114** (active1 114 / active0 0 / removal_reason 0 / removal_ts 0); pm_paper_trade **102**; pm_closed_position **29,815**.
- Rung-1 backup present: 25,083,904 bytes, `~/pm_stage0_gate1_dbbackup_20260827T021526Z.db`.
- No PM scratch on the box (`/tmp/pm_stage0*` absent). Branch worktree clean, synced with origin.
