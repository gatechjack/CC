# ANALYZE UPGRADE — STAGED DEPLOY MANIFEST (2026-09-12)

**Status: STAGED. HALT AT THE DEPLOY.** Nothing below runs until Jack replies with an explicit
"board authorizes atomic execution: `powershell -ep bypass -f .\NAME.ps1`" for each step, one step at a time
(command-paste-rule). Runners are authored + validated from a FRESH read-only pre-flight at authorization time,
not pre-baked here (their drift-baseline shas must be captured live).

- **Branch:** `pm-analyze-upgrade-research-2026-09-12` — build HEAD **`b84bd61a`**
  (research `6153a8a9`/`9e38b5ab` → build `30677ea3`/`3ee7c7ef` → skeptic fixes `45b30293`/`b84bd61a`).
- **Base (== what the box runs today):** prod-live **`e02d73ea`** — pm_web (`prediction-markets-web`) **PID 372688**,
  engine (`trading-corp`) **PID 370246**, PM schema **head 22**.
- **Fold target after deploy:** prod-live `e02d73ea` → `b84bd61a` (a fast-forward; the branch is built off `e02d73ea`).
- **Deploy class:** **pm_web-only**, ONE `prediction-markets-web` restart, engine **NEVER touched**. + migration 023
  (additive-only) applied BEFORE the restart. Mirrors Deploy 11 (sizing / migration 022) exactly.

---

## 1. What ships

**Runtime graft set (5 files, scp+tar, drift-gated box == `e02d73ea`):**

| File | Change | Ownership |
|---|---|---|
| `trading_corp/prediction_markets/scoring.py` | **NEW** — the deterministic promotion-judge (tiers + dominance + honest windowed ROI) | pm_web-only |
| `trading_corp/prediction_markets/analyze.py` | Sonnet swap, score wiring, `_extract_model` (real-model post-check), `_store_whale_score` | pm_web-only |
| `trading_corp/prediction_markets/loss_grounding.py` | `fetch_and_ground_losses` returns `(grounding, honest)` (Phase A rides the one /activity fetch) | pm_web-only |
| `trading_corp/prediction_markets/web/app.py` | `_ground_losses` returns `(g, honest)`; `farm_analyze`/`_run_analyze` thread `honest` | pm_web-only |
| `trading_corp/prediction_markets/db.py` | `MIGRATION_023` = `pm_whale_score` + index; `(23, MIGRATION_023)`; `SCHEMA_HEAD → 23` | **engine-SHARED, additive-only** |

**NOT grafted** (not runtime): `tests/prediction_markets/test_scoring.py`, `test_analyze_score.py`,
`test_rung3_observability.py` (head-pin bump), the research doc, and THIS manifest. They travel via the git fold only.

**Migration 023** — `pm_whale_score(wallet, category, tier, reason, sort_roi, n_resolved, n_honest, grounded,
omission_pct, coverage_pct, omission_floor, honest_roi, dominance_net, dominance_gross, largest_pnl, largest_title,
two_sided_pct, avg_win_price, chalk, dd_tell, dd_wins, dd_losses, copy_fills, copy_pnl, skill_version, computed_ts,
PK(wallet,category))` + `ix_pm_whale_score_cat`. Pure `CREATE ... IF NOT EXISTS` → **idempotent, additive-only,
zero rows added to any existing table.** Applies via `db.init_db` (the pm_cli mechanism), NOT on restart.

---

## 2. ★ ENGINE-UNTOUCHED PROOF (why this is pm_web-only despite db.py being engine-shared)

1. The engine (`trading-corp`: live_driver / execution / main) imports **none** of scoring / analyze / loss_grounding
   — verified: **0 references in live_driver.py**; those three are imported only by `web/app.py` (pm_web) and
   `pm_cli.py` (crons). Grafting those 4 files cannot change engine behavior.
2. `db.py` **is** engine-shared, but `MIGRATION_023` is **additive-only** (a new table the engine never reads). The
   running engine holds the OLD `db.py` in memory and is **not restarted**, so it is unaffected now. On its next
   INDEPENDENT restart it loads the new `db.py`, sees version 23 already in `schema_version`, skips the migration →
   a non-event. **This is the identical property proven in Deploy 11 (migration 022): "engine reads head-N DB w/old
   db.py = non-event, 0 err."**

---

## 3. ★ THE SONNET SWAP IS LIVE ON DEPLOY (the key is wired)

The Anthropic key **is wired in the `prediction-markets-web` process today** (Jack 2026-09-12). Therefore:

- `is_llm_available()` returns **True** in prod → the **Sonnet** narrator RUNS on the first uncached Analyze. This is
  NOT inert; it is a real, billable call the moment the code is live.
- `PM_ANALYZE_SKILL_VERSION` bumped **"3" → "4"** → every whale's cached verdict is auto-invalidated → the **first
  Analyze after deploy is a guaranteed cache MISS → a real Sonnet call** (no `?force=1` needed).
- Cost: ~1.4k in / ~120 out ≈ **$0.006/call** at Sonnet $3/$15; the daily $20 cap is unchanged.
- ★ **The model string is now read from the RESPONSE, not the config** (`_extract_model` → `NarrationResult.model` →
  `rep.model` → the cached report). A silent Haiku fallback would surface as a Haiku model string and the post-check
  catches it. **The post-check reads `rep.model` back; it does NOT trust `PM_ANALYZE_MODEL`.**

---

## 4. Deploy sequence (each step gated on Jack's authorization phrase, in order)

| # | Step | Runner (authored at auth time) | Reversible? |
|---|---|---|---|
| 0 | **Pre-flight RO** | `pm_analyze_pre_ro.ps1` | read-only |
| 1 | **DB backup (gate)** | `pm_analyze_backup.ps1` | — |
| 2 | **Graft 5 files** (scp+tar, drift-gated) | `pm_analyze_graft.ps1` | yes (roll back all 5) |
| 3 | **Apply migration 023** (`db.init_db`) | `pm_analyze_migrate.ps1` | yes (restore db.py + DB from backup) |
| 4 | **ONE pm_web restart** | `pm_analyze_restart.ps1` (`az ... systemctl restart prediction-markets-web`) | yes (restore + restart) |
| 5 | **Post-check + LIVE SONNET PROOF** | `pm_analyze_post_ro.ps1` + a real Analyze click | read-only |
| 6 | **FF-push prod-live + tag + docs** | `pm_analyze_ff.ps1` | git only |

### Step 0 — Pre-flight RO (must all hold, else STOP)
- box PM schema **head == 22**; `pm_whale_score` does **not** yet exist.
- the 5 files' box CR-sha8 **== the `e02d73ea` baseline** (no drift — nobody deployed underneath us).
- engine **PID 370246** alive; pm_web **PID 372688** alive.
- **`ANTHROPIC_API_KEY` present in the `prediction-markets-web` process env** (else Sonnet silently no-ops → decide with Jack).
- record: `pm_analysis_cost` today (usd, n_calls); `pm_subdivision` contracts/sizing_mode map; a resolved-whale count (do-no-harm baseline).

### Step 1 — DB backup (gate)
- snapshot `/home/azureuser/trading_corp/data/prediction_markets.db` → sized backup. **Abort if backup missing or 0 bytes.**

### Step 2 — Graft (scp+tar; the standing multi-file channel, NOT base64-heredoc)
- stage the 5 files to `/tmp`, drift-gate box == baseline for **all 5**, `cp` into place, re-verify CR-sha8, `py_compile` all 5.
- **any mismatch → roll back ALL grafted files, leave the box CONSISTENT, do NOT migrate or restart.**

### Step 3 — Migration 023 (BEFORE the restart, using the grafted db.py)
- run `db.init_db(PMDB)` (identical to pm_cli's mechanism). Before/after snapshot proves:
  - **head 22 → 23**; `pm_whale_score` exists **and is EMPTY**.
  - `pm_subdivision.contracts` / `sizing_mode` **unchanged for every row**; `pm_account` /
    `pm_subdivision_attachment` / `pm_watchlist` **row counts unchanged** (do-no-harm; 023 adds 0 rows to any existing table).
- ★ **Self-renumber guard (now the DEFAULT for every PM migration):** the drift-check asserts box head == 22 before
  applying. If **Item B** landed a migration 023 first (box head already 23), **renumber the `(23, MIGRATION_023)`
  tuple → `(24, ...)`** in the grafted db.py before running init_db (024 = box-head+1, still contiguous; the body is
  idempotent so only the version watermark changes).
- **CLEAN → proceed. Any MISMATCH → restore db.py + restore the DB from the Step-1 backup, do NOT restart.**

### Step 4 — ONE pm_web restart
- `az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "systemctl restart prediction-markets-web"`.
- ★ **This targets `prediction-markets-web` ONLY. Using `restart_tc.ps1` / bouncing `trading-corp` here is a STOP —
  the engine (PID 370246) must not move.** pm_web gets a NEW pid; engine pid stays 370246.

### Step 5 — Post-check + the LIVE SONNET PROOF
- **box == prod-live `b84bd61a`** on the 5 files (three-way after the FF); schema **head == 23**; `pm_whale_score`
  present; **engine still PID 370246** (untouched); pm_web **NEW pid**.
- ★★ **LIVE PROOF — click Analyze on `0x684baa57c3`** (a whale Jack already knows). The skill_version "3"→"4" bump
  guarantees a cache MISS → a **real Sonnet call**. Then read the STORED report back and assert:
  1. **`rep.model == "claude-sonnet-4-6"`** — the ACTUAL response model (via `_extract_model`), NOT the config. A
     Haiku string here = a silent fallback = **STOP**.
  2. the `pm_analysis_cost` day ledger **incremented ~$0.006** (a real billable call happened; a $0 delta ⇒ no call).
  3. a `pm_whale_score` row for `(0x684baa57c3, <category>)` was **written**.
  4. the **new one-sentence template** renders (trust-flagged score block → ONE decisive sentence, no caveat-list).
  5. **Expected tier == WATCH, omission UNKNOWN** — Jack's mental model, the UNGROUNDED path. ⚠️ **Caveat:**
     `farm_analyze` auto-grounds on a cache miss. If grounding returns **0** in-category held-to-resolution decisions
     (fail-soft), the whale stays ungrounded → WATCH / omission UNKNOWN as expected. If grounding **succeeds** with
     held-resolved decisions, omission becomes **KNOWN** and the tier reflects the honest numbers (PROMOTE or PASS).
     **That is a FINDING to report to Jack, NOT a deploy failure** — the deploy's pass/fail is 1–4 (Sonnet ran, real
     model, cost, stored row, new template); the tier is a confirmation of Jack's model, reported either way.
- **do-no-harm:** engine journal **0 new errors** since the restart; subdivision contracts/config **unchanged**; **0 fills disturbed**.

### Step 6 — Fold, prove, ledger, FF (deploy is not complete until prod-live carries it)
- three-way prove box == prod-live == branch on the 5 files; FF prod-live `e02d73ea` → `b84bd61a`.
- **report the FF push command in THIS session.** Tag `pm-analyze-upgrade-deploy-2026-09-12`. Docs/ledger commit after.

---

## 5. STOP CONDITIONS (each aborts before the next irreversible step)

- **Pre-flight:** box head ≠ 22 (unless an intended Item-B 023 → take the renumber path); any of the 5 files' box
  CR-sha ≠ `e02d73ea` baseline (**drift → someone deployed underneath → reconcile FIRST, do not overwrite**);
  `ANTHROPIC_API_KEY` absent in the pm_web process; `pm_whale_score` already present.
- **Backup:** missing or 0 bytes → abort, no graft.
- **Graft:** any post-`cp` CR-sha8 mismatch or `py_compile` failure → **roll back all 5, do NOT migrate/restart.**
- **Migration:** head not 22→23 (or box-head+1 on the renumber path); `pm_whale_score` absent or non-empty; ANY
  existing config count or `contracts` value changed → **restore db.py + restore DB from backup, do NOT restart.**
- **Restart:** must be `prediction-markets-web` ONLY. Bouncing `trading-corp` is a STOP. Post-restart pm_web
  unhealthy / import error → restore the 5 files + restart pm_web; the engine was never touched, so it is unaffected.
- **Live proof:** `rep.model` ≠ `claude-sonnet-4-6` (silent Haiku fallback) OR narration empty (key not reaching the
  process) → **STOP and report.** The code deployed fine but the live-Sonnet premise failed → Jack decides
  (roll the model back to Haiku, or accept).
- **Cost:** if the day ledger is already near/over $20 → defer the proof call.

---

## 6. SEPARATE AUTHORIZATIONS (explicitly NOT in this deploy)

- ★ **The pm_web PROSPECTS LIST / display surface that reads `pm_whale_score`** (a sortable, tier-badged column
  ranking whales by tier then `sort_roi`) is a **SEPARATE pm_web authorization**. This deploy STORES the score and
  narrates the per-whale Analyze result (the existing Analyze surface, now Sonnet + the one-sentence template); it
  does **not** add the sortable Prospects display. `pm_whale_score` is written; a NULL/absent row means
  "never analyzed" (distinct from a PASS row) — the future list reads that.
- **Item B collision:** any concurrent migration claiming 023 → self-renumber to 024 at apply time (§4 Step 3).

---

## 7. Verification already banked (pre-deploy)

- Box-scratch (isolated `/tmp`, deployed code + engine UNTOUCHED): all 5 files `py_compile` OK; **SCHEMA_HEAD = 23**;
  **27/27 new tests pass** (scoring + analyze_score, incl. the partial-sale-winner regression and the
  `_extract_model` real-vs-fallback test); full `tests/prediction_markets` = the **same 21 pre-existing UI failures**
  (test_accounts_m2 / test_live_r3 / test_stage2_* — none touch scoring/analyze) → **0 new regressions**.
- Two adversarial skeptics: Skeptic-1's ONE MEDIUM (partial-sale winner honest-ROI inflation) **fixed** (gross cost
  basis on the A_only side, matching the closed convention) + regression-tested; Skeptic-2's LOW/cosmetic nits
  (stale Haiku/"not wired" comments) **retired**. No BLOCKER/HIGH outstanding.
- The `rep.model`-is-config gap Jack flagged is **closed** (`_extract_model` reads the real response model).

---

## 8. DEPLOYED LIVE 2026-09-13 — OUTCOME (every step board-authorized)

**origin/prod-live `e02d73ea` → this commit (FF); tag `pm-analyze-upgrade-deploy-2026-09-13`.** pm_web-only; engine
`trading-corp` **PID 370246 NEVER touched**; pm_web `prediction-markets-web` **372688 → 376953** (one restart).

Sequence as executed:
- **Step 0 pre-flight RO** (01:12Z): head 22, box == `e02d73ea` (5/5), key wired (183 billed calls, Haiku/skill-3
  baseline), engine 370246 / pm_web 372688, 44 subs recorded (atp jack=2 / karen=1).
- **Step 1 backup** (01:15Z): 4 files == `e02d73ea`; **393 MB** DB snapshot head-22 → restore point
  `/home/azureuser/pm_analyze_backup_20260913T011517Z`.
- **Step 2 graft** (01:22Z): 5/5 landed == `b84bd61a` (LF), py_compile + import-gate clean (zero engine/broker
  modules, `SCHEMA_HEAD=23`), no rollback.
- **Step 3 migration 023**: my manual apply **STOPPED on head==23** — a **pm_cli `paper-poll` cron (01:30Z) applied
  the grafted MIGRATION_023 itself** (PM migrations apply via `db.init_db` from crons). Verified clean vs the head-22
  snapshot: head 22→23, `pm_whale_score` created + EMPTY + indexed (**26 cols** per the DDL; the RO check's
  "expect 27" was a miscount — the 27th was the `PRIMARY KEY` clause), config **byte-identical** (44 subs incl atp
  2/1), counts unchanged, engine + pm_web unrestarted.
- **Step 4 restart** (01:42:59Z): pm_web 372688 → 376953; engine 370246 unchanged.
- **Step 5a health**: box == `b84bd61a` (5/5), schema 23, heartbeats 0–2s fresh.
- **Step 5b LIVE SONNET PROOF** (02:07Z): first Analyze on `0x684baa57c3/mlb` → **model=claude-sonnet-4-6 (the REAL
  response model, not config)**, cost $0.00275, skill_version 4, one-sentence verdict, `pm_whale_score` stored. The
  Sonnet swap is LIVE.

**First-Analyze FINDING (report either way — Jack):** `0x684baa57c3/mlb` came back **GROUNDED**, tier=**INSUFFICIENT_DATA**.
Our closed table shows **196 W / 17 L** (looks 92%, cost-ROI 88.5%), but grounding found **omission_pct=0.80** (80% of
losses dropped) at **coverage 36%** → the win-rate is only a floor → refused to judge. Honest windowed ROI still +47%,
diversified (dom 0.04), not chalk/hedger. **The loss-omission mirage caught in the wild on the most-copied whale
(copy_fills=222).**

**Do-no-harm:** engine PID 370246 unchanged throughout; `pm_subdivision` config byte-identical; PM live driver cycling.
**Pre-existing / out-of-scope:** legacy `kalshi_copy_trader` "Apify FEED DOWN" (engine legacy loop, not this deploy);
`db.py:connect()` sets WAL before a busy_timeout → a transient "database is locked" can hit `init_db` (one old
traceback in `pm_poll.log`; filed as a backlog nit).

**SEPARATE AUTHORIZATION (NOT shipped):** the pm_web Prospects/display surface that reads `pm_whale_score`.
