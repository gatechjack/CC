# Prediction Markets — Rebuild Plan (paper-lane-first)

**Date:** 2026-08-26 · **Branch context:** `prediction-markets-cp3b2-2026-08-25` @ `f4fb61d` · live schema **7** · `origin/prod-live 95e78c4`.
**Mode:** PLAN ONLY. No code, no commits, no branches, no migrations, no deploys, no box mutation. Built from `PM_STATE_REVIEW_2026-08-26.md` (accepted) + a read-only re-read of `paper.py` + the read-only Kalshi listing probe (§F-2, run 2026-08-26).
**How to read:** §A is the target (self-contained). **§B is the one thing wrong with the proposed sequence — read it first.** §C is the corrected stage plan. §D–§G are the required cross-cutting sections (deletions, decisions RULED, the truncation correction, anti-drift). Facts are labeled; **opinions/recommendations are in §I only.** Nothing is softened.

> **★ RULINGS LANDED 2026-08-26.** Jack ruled all five §F decisions, ran the read-only Kalshi listing probe, then **ruled the tile set: 15 categories IN.** This revision records them: **§B** carries the *gamma-is-the-resolution-authority* framing (not "route around a bug" — the correct source asserting itself). **§F** is now **DECISIONS RULED** with Jack's reasoning. **§F-2** carries the **MEASURED probe + the 15-IN ruling** (3 of the 15 are operator overrides of the probe output; the reasons are recorded). **§C Stage 0** carries the reversible removal of the 22 off-funnel pairs. **Stage 1** leads with the empty-list expectation. **Excluded categories are recorded with THREE DISTINCT states — never a flat "not copyable"** (not-probed / measured-dormant / structural). **Inclusion is ruled; the ticker inventory for nhl/ufc/fed is NOT measured** — three standalone probes stay on the books (§F-2-PROBES).

---

## §A. THE TARGET (Jack's requirements, verbatim — the spec)

> "There is a main Predictions Market Dashboard. This is where all the subdivisions (Account-Category) have pinned whales. Clicking on the Account-Category tile, takes me to the detailed (Account-Category) dashboard that shows live trades and stats for the live sub-division.
> On the main Predictions Market Dashboard, there is one other menu option: Farm League. When you click on the farm league main screen, there are the category only tiles. These are the Kalshi copyable Polymarket categories. When you click on a category, you get another detailed Dashboard for the category. On this page, you see the pinned whales in this category at the top section (with buttons: Demote and Promote and Analyze). The pinned whales do paper trades and clicking on a whale take you to a detailed page with all their paper trades and stats. On this page, you also see the farm league prospects in a bottom section (with buttons: Promote to watchlist). The farm league is dynamically populated. Move a whale to the watchlist is manually controlled by operator. The prospects do not paper trade, the only stats they have come from the completed trade api. Clicking on a prospect will take me to a detailed page of all their closed trades. Clicking on the analyze button will help the operator decide to promote (or not) to the watchlist to begin paper trading."

**THREE LISTS, THREE BASES (one whale-category pair can be on all three at once, showing different numbers on each):**
- **PROSPECT** (farm league, bottom section) → **completed trades only** (`pm_closed_position`). Does not paper-trade. Action: Promote-to-watchlist. *(Screen word: "Prospect"; code `status='candidate'`.)*
- **PINNED / WATCHLIST** (farm league, top section) → **our paper trades only** (`pm_paper_trade`). Actions: Demote, Promote, Analyze. *(Screen word: "Watchlist"; code `status='pinned'`.)*
- **LIVE** (attached to an Account-Category sub-division) → **live trades only** (P3 tables — not built). **P3, out of scope.**

**⭐ ANALYZE IS THE POINT.** Rough prospect stats are a screen; **Analyze is what decides promotion.** A defect in what Analyze is FED outranks any imprecise number on a screening list. Weight the plan toward Analyze's *input*, not its prose.

Source: `PLATFORM_VISION.md:14-25`, `P1_PLAN.md §2:25-36`. Full recovery + line cites: `PM_STATE_REVIEW_2026-08-26.md §0`. Canonical vocabulary map: §H (RULED §F-3).

---

## §B. THE SEQUENCE IS RIGHT — BUT STAGE 1 AS WRITTEN DOES NOT WORK. (loudest, first)

**The core reframe is correct:** the biased source is only platform-wide because the paper lane is incomplete; finish the paper lane and the bias quarantines to prospects (where Analyze judges). Paper-lane-first is the right order. **But the stated Stage-1 content is insufficient, and here is the fact that breaks it:**

**FACT (`paper.py:335-379`): the adjudicator determines a paper trade's win/loss by looking up a matching `pm_closed_position` row — the same loss-omitted source.**
- `:354-357` — on a `pending_adjudication` row it `SELECT won,realized_pnl … FROM pm_closed_position WHERE wallet=? AND condition_id=? AND outcome_index=?`.
- `:358-366` — **match → `status='closed'`, `won` copied from that row**, paper P&L booked.
- `:367-373` — **no match + past end_date+grace → `status='stale'`, `close_source='whale_exit'`, EXCLUDED from realized stats** (`:341`).

**Consequence:** a whale loss that `/closed-positions` OMITS (the measured bias — `PM_STATE_REVIEW §5`) produces **no `pm_closed_position` row → no match → the paper trade is booked `stale`, not `closed-lost` → it is dropped from the paper realized stats.** So **the paper lane inherits the exact same loss-omission**, and a paper rollup built on it would show the **same inflated win-rate** the completed lane does. Finishing the paper lane *as built* moves the bias from `pm_category_stats` to `pm_paper_category_stats` — it does not quarantine it. **That is worse than today:** today the pinned list is visibly borrowing from the wrong lane; after a naive Stage 1 it would have its own freshly-computed-looking table with the same distortion and nothing on screen saying so.

**THE FIX THAT MAKES THE REFRAME TRUE:** Stage 1 must **re-base the adjudicator on the resolution authority (gamma `/markets`), not on `pm_closed_position` row-presence.** On a vanished/pending paper trade, ask gamma "did this market resolve, and which outcome_index won?" → `won = (our outcome_index == winning_outcome_index)`; book paper P&L on our own `size_basis` (the existing `_paper_realized`, `paper.py:327-332`, is already correct and already independent of the whale's PnL). Only "market not resolved" → `stale` (a genuine pre-resolution whale exit). This makes the paper lane's loss-completeness **independent of the `/closed-positions` omission** — which is the whole point of the reframe. **Without this change, Stage 1 fails its own purpose.**

**★ FRAMING (Jack's ruling — record it THIS way, not as a workaround):** re-basing the adjudicator on gamma is **not** "routing around a bug in `/closed-positions`." **Whether a market resolved and which side won is a fact about the MARKET, not about any wallet's position records.** Gamma `/markets` **is** the resolution authority; `/closed-positions` was never the right source for resolution — it was *convenient*, and convenience became the system of record. Re-basing on gamma is **the correct source asserting itself**, which it always should have. This is the **same root cause the review named — convenience-as-system-of-record — surfacing in a THIRD place:** (1) the pinned rollup borrowing the completed lane, (2) the loss-omission baked into the completed foundation, (3) the adjudicator matching closed-position rows. All three are one mistake wearing three hats. Fixing the adjudicator's *source* is therefore not a patch; it is the platform finally reading resolution from where resolution actually lives.

**Second, smaller sequence issue:** **Promote-to-watchlist (Stage 3) is coupled to Search (Stage 4)** — pinning a *prospect* requires prospects to exist, and there are zero candidates until Search runs. So the prospect→pinned action is dormant until Stage 4. Promote/Demote on *pinned* whales are independent (92 pinned pairs remain after Stage 0). Recommendation: keep Stage 3 for the pinned-side actions + build Promote-to-watchlist's mechanics in Stage 3 but expect it to be exercisable only after Stage 4 (or fold that one action into Stage 4). Flagged, not fatal.

Everything else in the proposed order stands.

---

## §C. THE STAGES (corrected)

Migrations are **numbered on landing** — live is at schema 7. **Stage 0 has already BUILT migration 008** (`pm_watchlist.active`; branch `prediction-markets-stage0-2026-08-26` @ `7cec332`, box-scratch green, NOT yet applied to live), so the downstream numbers are now FIXED: **Stage 1 = 009, Stage 4 = 010, Stage 5 = 011.** If landing order still changes, the numbers follow the landing order, not this plan. Every stage carries the CP3a gate rhythm: **build → box-scratch (pytest actuals + render/verify against a WAL-safe copy of live) → report with SHA → HALT → Jack authorizes deploy separately** (code deploy + any pm_web restart on the root `az vm run-command` channel; poller/adjudicator/cron runs + any live-DB write are separate Jack-authorized acts).

### STAGE 0 — REMOVE THE 22 OFF-FUNNEL PAIRS (funnel hygiene; RULED)

**Ruling:** remove the pinned pairs in the three excluded categories — **cbb (3) + fifwc (8) + unknown (11) = 22 of 114.** The tile set covers the remaining **92** pairs across the 15 IN categories. **REMOVE = stop polling + stop paper-trading. It does NOT mean destroy their record.**

**Mechanism — BUILT + box-scratch GREEN 2026-08-26** (branch `prediction-markets-stage0-2026-08-26` @ `7cec332`; migration-on-copy proven, live untouched; NOT deployed). **ONE removal path for all 22:**
- **Migration 008 (BUILT):** add to `pm_watchlist` → `active INTEGER NOT NULL DEFAULT 1`, `removal_reason TEXT NULL`, `removal_ts INTEGER NULL`, + index `ix_pm_watchlist_active`. `status` semantics (`candidate`/`pinned`) unchanged.
- **"Remove from funnel" = `active=0` + `removal_reason` + `removal_ts`.** SEVEN consumer reads each add `AND active=1`: `poll_pinned` (the poller), the pinned-subset assertion, the seeded-pairs review, the farm tile / list / candidate-count reads, and `query_scoreboard` (the F-4 prospects ranker). **The adjudicator reads `pm_paper_trade`, NOT `pm_watchlist` — nothing to gate there; in-flight trades of a removed pair settle naturally.** An inactive pair is off-poll, off-paper — but **its `pm_watchlist` row AND all existing `pm_paper_trade` rows persist untouched** (history preserved).
- **Reversible by design:** `active=1` restores the pair to the funnel **in its prior status** (pinned stays pinned) with its record intact — nothing was deleted and the flag preserves status. cbb returns after one probe; fifwc returns next World Cup cycle — **both re-enter as themselves, not as new pairs.**
- **`unknown` uses the identical path** (`active=0`, `removal_reason='structural_slug_failure'`); it simply never gets flipped back — no separate mechanism (per the ruling). Its reversibility machinery exists but goes unused.
- **The 22, with the THREE DISTINCT reasons recorded per category** (§F-2): cbb(3) `removal_reason='pending_analysis_ncaab_not_probed'` · fifwc(8) `removal_reason='dormant_calendar_returns_next_wc'` · unknown(11) `removal_reason='structural_slug_failure'`.
- **Why an `active` flag, not `status='removed'` or a roster delete:** a flag **composes** with status (doesn't overload the candidate→pinned lifecycle) and makes reversal a single boolean that **restores the prior status** — directly serving "re-enter with whatever record they had." A `status='removed'` value would lose whether the pair was candidate or pinned; deleting `pm_roster` rows would force a re-seed on return. Both rejected for the reversibility requirement.

**Scope — NOT touched:** `pm_paper_trade` rows (NEVER deleted on removal); legacy; the completed/paper rollups' math; `poly_kalshi_mlb`.

**Dependencies:** none on other stages. **Deploy is a THREE-RUNG LADDER (below), NOT two steps** — migration → gated-code deploy → row write. The **one-time UPDATE of the 22 rows** (`active=0` + reasons) is **rung 3**, a separate Jack-authorized live-DB write, and it MUST come after the gated code is live (else it changes nothing on the box). Stage 0 can only fold into Stage 1's deploy once all three rungs complete.

**Migration: 008 (BUILT)** — the `active`/`removal_reason`/`removal_ts` columns + `ix_pm_watchlist_active`.

**Reuse / replace / delete:** REUSE `pm_watchlist`/`pm_roster`; ADD the `AND active=1` gate to the SEVEN consumer reads (poller, subset assertion, seeded-pairs review, farm tile/list/candidate-count, `query_scoreboard`). DELETE nothing (explicitly: no `pm_paper_trade` deletion; the adjudicator is untouched — it reads `pm_paper_trade`).

**Verification (BASIS tests):**
1. Remove a pair → assert it is off the poll set (poller skips it) AND no new paper trades accrue AND **its existing `pm_paper_trade` rows still exist** AND `removal_reason` is set.
2. **Reversibility:** flip `active=1` → assert it re-enters as **PINNED** (prior status) with its old paper rows intact, **not** as a new pair.
3. `unknown` → same path, `removal_reason='structural_slug_failure'`; documented as never-scheduled-to-return.
4. Count: after the removal write, active pinned pairs = **92** across the 15 IN categories (114 − 22).

**★ STAGE 0 DEPLOY SEQUENCE — THE THREE-RUNG LADDER (RECORDED 2026-08-26; do NOT collapse to two).**
An earlier report listed only two authorizations (migration, then the 22-row write) and OMITTED the deploy between them. That two-step order is UNSAFE: the `AND active=1` gates live only on the branch, not on the box. If the row write lands while the box still runs UNGATED readers, `active=0` changes nothing — the poller keeps polling the 22 and the tiles keep showing them. The safe order is three rungs, each verified, row write LAST:

- **Rung 1 — apply migration 008 to live via `init_db()` from an EPHEMERAL SCRATCH EXTRACT (the Gate-1 mechanism), NOT by deploying `db.py`.** This is exactly how CP3b-2 Gate 1 applied migration 007 (`CP3B_DEPLOY_COMPLETE.md:28`): byte-verify the extract `== <branch SHA>`, run its `init_db()` against the LIVE PM DB; the runtime `db.py` stays untouched until rung 2. 008 lands once, explicitly, under control.
  - **★ init_db auto-migrate — every trigger, and why the scratch extract closes the boundary:** PM `db.init_db()` auto-applies pending migrations and is called ONLY by `pm_cli` subcommands — `backfill`/`refresh` (`pm_cli.py:55`), `rollup` (:67), `paper-poll` (:129), `paper-adjudicate` (:140), `migrate-roster` (:157), `analyze` (:180). **pm_web does NOT call `init_db` on startup** (`web/app.py` builds `FastAPI(...)` with no startup/lifespan hook) → **the rung-2 pm_web restart is NOT a migration trigger.** `main.py:293`'s `init_db` is the ENGINE's (`persistence/db.py` on `trading_corp.db`) — a different function, never touches the PM DB (isolation). The ONLY UNATTENDED trigger is the 03:20 UTC cron (`pm_cli refresh`); all other `pm_cli` calls are manual. **Because rung 1 does NOT deploy `db.py`, the runtime keeps the OLD `db.py` (knows migrations 1–7) → its `init_db` is a harmless no-op on a schema-8 DB → nothing auto-migrates unattended.** Still, run rung 1 clear of 03:20 so the `ALTER`'s brief write-lock cannot collide with a cron-started refresh's writes.
  - **★ PRE-condition — online backup FIRST (do NOT invent a mechanism; use Gate 1's):** the SQLite **online-backup API** with a `mode=ro` source handle — `s=sqlite3.connect('file:<LIVE>?mode=ro',uri=True); d=sqlite3.connect(<BK>); s.backup(d)` — as `pm_cp3b2_gate1_probe.sh:82` did. Record in the rung-1 report: backup path `~/pm_stage0_gate1_dbbackup_<UTC>.db`, its **sha256**, and a **`PRAGMA integrity_check`** result. **Abort the migration if the backup or integrity check fails.**
  - **Verify:** live schema 7→8; `active` + `removal_reason` + `removal_ts` present; 114 rows `active=1` / 0 `active=0`; `pm_paper_trade` 102 unchanged; `/healthz` 200 and `/farm` still renders 200 on the OLD runtime code (every pm_web read of `pm_watchlist` is name-based via `sqlite3.Row`: `farm.*` explicit-column/scalar + `query_scoreboard` selects no `wl` columns; `pm_cli` reads only via gated `paper.*`; the engine does not read it).
  - **Rollback:** the box runs **SQLite 3.37.2** (≥ 3.35.0), so `ALTER TABLE pm_watchlist DROP COLUMN` is available — but the reliable TOTAL revert is **restore the pre-ALTER online backup** (a bare DROP COLUMN leaves `schema_version` at 8). To restore: **stop pm_web first** (it holds live read/write handles) and do it clear of the 03:20 cron; overwrite `prediction_markets.db` (delete any stale `-wal`/`-shm`) with the backup; confirm schema back to 7; restart pm_web. **Lost on restore:** any PM-DB writes since the backup (an analyze-cache write, a cron refresh's ingested rows) — for a rung-1-only rollback that window is the `ALTER` + any concurrent writes, so keep it short.
- **Rung 2 — deploy the gated code** (**5 PM-ONLY files:** `prediction_markets/db.py` + `paper.py` + `farm.py` + `stats.py` + `__init__.py`) and **restart pm_web**. By now 008 is applied (rung 1) so the gated `active=1` queries find the column; with all rows still `active=1`, behaviour is IDENTICAL (poller polls all 114; tiles show all) — behaviour-neutral, safe to verify before any data change. **Ships PM-ONLY files** — `db.py` here is `prediction_markets/db.py`, **NEVER `persistence/db.py`** (the engine/MACE shared file — do not ship). **★ `__init__.py` IS in the set (Ruling 2, 2026-08-27):** it carries the anti-drift docstring pointer to `PM_REQUIREMENTS.md` (behaviorally inert) — included so `box==branch` is clean across the whole PM package (no accepted-drift file) and the pointer actually reaches the box; the earlier "4 files" was stale text written before `__init__.py` changed. **Verify:** deployed files byte-match the branch (sha256 box==local, all 5); `/farm` + `/scoreboard` 200; gated queries execute against the live schema; poller dry-check still sees 114 pinned. **Full artifact list + sha256 + rung-2 verification plan: see `STAGE0_RUNG2_ARTIFACT_LEDGER.md` and the RUNG-2 VERIFICATION PLAN below.**
  - **★ Governance-A — prod-live artifact ledger (MANIFEST AUTHORED 2026-08-27; prod-live commit still a rung-2 deploy step):** standing deploy governance requires advancing the `prod-live` branch with a commit recording exactly the deployed artifacts, byte-verified box==branch (as CP3a / CP3b-2 did). That precedent is **inherently POST-deploy** (its standard is a fresh box re-hash), so it cannot be authored pre-deploy. **Ruling 1 (2026-08-27):** the byte-verified source-of-truth is authored NOW as a **manifest on the branch** — `STAGE0_RUNG2_ARTIFACT_LEDGER.md` (5 artifacts + branch sha256 + additive-on-`95e78c4` + the `box==branch` rung-2 gate + the `db.py`↔rung-1 sha256 cross-check). The **real prod-live path-checkout commit is created AT rung-2 deploy, from that manifest, post box-rehash** — NOT now. box != branch at manifest-write time; `box==branch` is the Gate-2 proof, not a present claim.
  - **★ Governance-B — do NOT disturb `origin/prod-live @ 95e78c4` (MACE forks from that tip):** rung 2 advances prod-live by a **NEW commit only (fast-forward / additive)** — **no amend, no rebase, no force-move** — leaving `95e78c4` intact as an ancestor (exactly as CP3b-2's `2fc9173→95e78c4` fast-forward). MACE's fork base stays in history, undisturbed.
- **Rung 3 — the 22-row `active=0` write** (cbb×3=`not_probed`, fifwc×8=`dormant_calendar`, unknown×11=`structural`). ONLY NOW do the live gates (rung 2) take effect. **★ The write MUST also stamp `removal_ts`** — set `removal_ts = int(time.time())`, **Unix epoch seconds (INTEGER)**, matching the PM DB's existing timestamp columns (`added_ts`/`pinned_ts`/`updated_ts` are all epoch INTEGER; the seed writes `int(time.time())`) — NOT ISO text. Leaving it NULL loses the "when" permanently. **Verify:** live `active=0` count = 22 carrying the three reason strings **AND `removal_ts` NOT NULL + a plausible epoch** (within the deploy window) on all 22; the poller skips them; `/farm` shows 15 categories / 92 pairs; `query_scoreboard` and the farm lists no longer contain the 22; `pm_paper_trade` rows for the removed pairs are still present (history preserved).

Each rung is independently reversible: rung 1 — restore the pre-ALTER online backup (clean total revert), or `DROP COLUMN` on SQLite 3.37.2; rung 2 — redeploy the prior per-file artifacts + restart; rung 3 — flip `active=1` back (the flag's whole point). The three rungs are separate Jack authorizations.

**★ RUNG-1 EXECUTION RECORD — DONE 2026-08-27 02:15Z** (migration 008 applied to LIVE; branch `aac882b`; fail-closed runner `pm_stage0_rung1.sh`; authorized rung 1 only).
- **Pre-checks passed:** UTC 02:15 (clear of the 03:00–04:00 cron window); engine PID **89366**; pm_web `/healthz`+`/farm` = **200** (`/farm` 228,564 bytes); live schema **7**, no `active` column; baseline pm_watchlist **114** / pinned 114 / **18** categories / 0 candidates / pm_paper_trade **102**; `journal_mode=wal`.
- **Online backup FIRST (kept, Gate-1 mechanism):** `~/pm_stage0_gate1_dbbackup_20260827T021526Z.db` — **`PRAGMA integrity_check=ok`**, schema-7 snapshot, pm_watchlist 114, 25,083,904 bytes, **sha256 `dfcb8ad78027b68826bed75c86e04022c744ef1a9bf3ff5ef6be1298b15820b5`**.
- **Byte-verify:** scratch `db.py` sha256 == branch `76eb52b2…dbc93782`; applied via `init_db()` from the ephemeral scratch (`IMPORT_FROM /tmp/…scratch…/db.py`) — **runtime `db.py` NOT touched**; scratch removed.
- **Post-verify:** schema **7→8**; `active` INTEGER / `removal_reason` TEXT / `removal_ts` INTEGER present; `ix_pm_watchlist_active` present; pm_watchlist **114**, **active=1: 114 / active=0: 0 / removal_reason set: 0 / removal_ts set: 0** (nothing flipped); pm_paper_trade **102** unchanged; pinned 114 / 18 categories / 0 candidates == baseline; **pm_web `/healthz`+`/farm` still 200, `/farm` byte-identical (228,564), pm_web PID 40483 NOT restarted** (behaviour-neutral proof); engine PID **89366** == before.
- **State now: live PM DB is schema 8; the runtime code is still the OLD (pre-Stage-0) `db.py`/`paper.py`/`farm.py`/`stats.py`** (which ignores the new columns — hence the byte-identical `/farm`) **plus the OLD `__init__.py`** (the 5th rung-2 artifact — its docstring lacks the `PM_REQUIREMENTS.md` pointer until rung 2 ships it). **Rungs 2 (deploy) and 3 (22-row write) remain unauthorized.**

**Size: SMALL.** A migration + an `active=1` gate on seven reads + a one-time 22-row UPDATE (Jack-run) — but a THREE-rung deploy (above), not one.

---

### ★ RUNG-2 VERIFICATION PLAN (written 2026-08-27; NOT executed — rung 2 is UNAUTHORIZED)

Rung 2 is the **first** rung where running behaviour can change and it carries the **only** pm_web restart in
the ladder. All 114 rows are `active=1`, so the gated code SHOULD be **behaviour-neutral** — but that is a
CLAIM TO VERIFY, not to assume. Deploy set = the **5 PM-only artifacts** in `STAGE0_RUNG2_ARTIFACT_LEDGER.md`.
Channel = **root `az vm run-command`** (code deploys), fail-closed, mirroring CP3b-2 Gate 2. **Nothing here runs
until Jack authorizes rung 2 as a separate act.**

**PRE-CONDITIONS (named; capture all in the SAME session, immediately before the code swap):**
- **PRE-1 Timing — clear of the 03:20 UTC cron. ★ LOAD-BEARING, not tidiness — here is WHY:** the byte-identical
  `/farm` post-check is the *entire* behaviour-neutral proof. `/farm` renders `pm_category_stats`, which the
  nightly `pm_cli refresh` **rewrites**. So if a cron (or any refresh) fires **between the `B0` baseline capture
  and the post-deploy `/farm` check**, `/farm` shifts **for ingest reasons that have nothing to do with the
  deploy** — and the byte-identical proof fails for a cause that is not the code. That would either (a) trigger a
  false STOP/rollback of a perfectly good deploy, or (b) tempt someone to hand-wave a real byte diff as "probably
  the cron." Both are unacceptable, so the deploy window must be provably clear of any refresh. **Concrete
  evidence this is real, not hypothetical:** `/farm` was **228,564** bytes at the rung-1 record and **228,566**
  at the 2026-08-27 11:58Z pre-refresh snapshot — a 2-byte drift from nightly rollup churn alone, zero code
  change. The nightly `pm_cli refresh` is the only unattended DB writer; deploy in a calm window outside
  ~03:00–04:00 UTC (rung 1 used 02:15). If the cron could fire mid-deploy, **abort and reschedule**.
- **PRE-2 Baseline capture (the comparison the neutrality proof rests on).** Capture *now*, not from the
  rung-1 record (the nightly cron updates `pm_category_stats`, which the pinned tiles render, so `/farm` bytes
  can legitimately drift day to day — the byte-identical check is only valid against a **same-session**
  baseline). **★ `B0` must be captured AFTER `/farm` has SETTLED — i.e. after the last refresh completed and no
  refresh is in flight — NOT from any earlier reading** (the 2026-08-27 ad-hoc refresh wrote rows and moved
  `/farm`; a pre-refresh reading is already stale as a baseline):
  - `/farm` → HTTP 200, record exact **body byte length `B0`** (this is THE behaviour-neutral baseline).
  - `/healthz` → 200, `pm_db_schema_version: 8`.
  - `pm_watchlist`: total **114** / pinned 114 / candidates 0 / `active=1` **114** / `active=0` **0**
    (funnel untouched — rung 3 not done). Category/pair counts as rendered = **18 categories / 114 pairs**
    (NOT 15/92 — that is the post-rung-3 state).
  - `pm_paper_trade` = **102**.
  - **engine PID** (record — must be unchanged after) and **pm_web PID** (will change — the one restart) and
    pm_web `NRestarts` (must increment by exactly 1).
- **PRE-3 Backup decision (MY CALL, as asked): a fresh DB backup is NOT warranted; a per-file CODE backup IS.**
  Rung 2 is a **code** deploy — it writes **nothing** to the DB (migration 008 already landed in rung 1; schema
  and data are untouched). So the correct rollback material is a **per-file backup of the CURRENT (pre-Stage-0)
  box versions of the 5 files**, taken on the box before overwrite (exactly CP3b-2 Gate-2's
  `pm_cp3b2_gate2_bak_*` dir) — NOT a DB snapshot. **The rung-1 DB backup is the WRONG tool for a rung-2
  rollback:** it reverts *schema* to 7 and would LOSE every PM write since 02:15Z (cron rows, analyze-cache);
  it must not be used to roll back a code-only change. (If a fresh off-host copy of the schema-8 DB is wanted
  for general safety, that is a separate, optional hygiene step — not a rung-2 rollback dependency.)
- **PRE-4 Box==branch is NOT a pre-state** (box still runs `95e78c4`); it is the **deploy gate** below.

**DEPLOY STEP (fail-closed; the pm_web restart is the only mutation to a running service):**
1. **Manifest-assert** — exactly the 5 `^trading_corp/prediction_markets/` paths; **leak-abort** any other
   path; **name-guard** `persistence/db.py` ABSENT (MACE shared file). Refuse to proceed otherwise.
2. **Chain-of-custody** — the staged artifact's sha256 == the branch sha256 in the manifest (no corrupt
   transfer) BEFORE any overwrite.
3. **Per-file backup** — copy the current box versions of the 5 files to `~/pm_stage0_rung2_bak_<UTC>/`
   (rollback material).
4. **Copy** the 5 files into place; **chown azureuser:azureuser**, dirs 755 / files 644 / no world-writable
   (GOTCHA-1/2).
5. **BOX==BRANCH GATE (fresh re-hash)** — `sha256sum` each of the 5 deployed files ON THE BOX; **each must ==
   the manifest branch sha256.** Any mismatch → **STOP, do not restart, do not advance prod-live.**
6. **Restart pm_web ONLY** — `systemctl restart prediction-markets-web`. The engine (`trading-corp.service`)
   is never referenced.
7. **prod-live commit is a SEPARATE, LAST, bookkeeping step** — authored only after the post-checks pass, from
   the manifest recipe (fast-forward only; `95e78c4` stays an ancestor). Not required to "accept" the running
   state; can wait for a separate confirm.

**ROLLBACK (if the restart comes back unhealthy):** redeploy the PRE-3 per-file backup (the pre-Stage-0 box
versions) + `systemctl restart prediction-markets-web` → code reverts to the `95e78c4` state; **no DB revert
is involved** (rung 2 changed no DB state). If pm_web is still unhealthy after restoring the backup → **STOP and
hand to Jack** (the cause is not the artifact).

**POST-CHECKS (named; all must pass):**
- **POST-1** `/healthz` 200, `pm_db_schema_version: 8`.
- **POST-2** `/farm` 200 **AND body byte length == `B0`** (the behaviour-neutral proof — the same standard rung
  1 met with its byte-identical `/farm`). A byte diff not explained by a same-moment cron write ⇒ behaviour
  changed ⇒ **STOP**.
- **POST-3** all **SEVEN** gated queries execute without error on live schema 8:
  `farm.farm_categories` (farm.py:53), `farm.farm_rows` (farm.py:82), `farm.farm_summary` candidate count
  (farm.py:120), `paper.poll_pinned` SELECT (paper.py:138, dry), `paper.assert_pinned_subset_of_refresh`
  (paper.py:293 — must NOT raise), the seeded-pairs review (paper.py:444), `stats.query_scoreboard`
  (stats.py:291). No `no such column: active` / SQL error from any.
- **POST-4** poller dry-check still sees **114** pinned (poll set unchanged — all `active=1`).
- **POST-5** `pm_paper_trade` = **102** (unchanged).
- **POST-6** schema still **8** (rung 2 does not migrate — confirm no drift).
- **POST-7** **engine PID == the PRE-2 value** (rung 2 must not touch the engine); legacy DB mtime unchanged;
  no `SQLITE_BUSY`/`locked`/traceback in the pm_web restart window.
- **POST-8** pm_web PID **changed** and `NRestarts` incremented by **exactly 1** (a clean single restart, not a
  crash loop).

**WHAT MAKES ME STOP MID-RUNG AND HAND BACK TO JACK (explicit):**
- Any of the 5 box re-hashes != the manifest sha256 (custody / GATE-5 failure) → STOP **before** the restart.
- The leak-guard trips (a non-`prediction_markets/` path, or `persistence/db.py` present) → STOP.
- pm_web does not return `/healthz` 200 in the restart window, or crash-loops (`NRestarts` jumps > 1) → roll
  back to the per-file backup; if still unhealthy → STOP + hand back.
- `/farm` not byte-identical to `B0` (and not a same-moment cron write) → STOP (the neutrality claim is false).
- Any gated query raises → STOP.
- **Engine PID changed, legacy mtime changed, or any engine/`persistence/*` file touched → STOP IMMEDIATELY**
  (blast-radius breach; the engine and MACE are out of scope).
- schema != 8, or any `pm_watchlist`/`pm_paper_trade` count moved (something wrote the DB) → STOP.
- The 03:20 cron fires mid-deploy → abort, reschedule.
- **The PK-collision (see `PK_COLLISION_TRIAGE_2026-08-27.md`) is orthogonal** — if the *next* 03:20 cron
  collides again on `0x767a…d8ac5`, that is the pre-existing ingest anomaly, **not** a rung-2 regression; do
  not roll back rung 2 for it.

---

### STAGE 1 — CLOSE THE PAPER LANE (the reframe's foundation)

> **★★ EXPECT A BLANK WATCHLIST (PINNED) LIST FOR WEEKS — THAT IS THE CORRECT OUTCOME, NOT A BROKEN DEPLOY.** At adjudicator-readiness (2026-08-25) **0 of 102** open paper trades had a resolved market. The gamma re-base changes **how** a resolved trade is judged, not **whether** trades have resolved yet — so immediately after Stage 1 the Watchlist section shows **honest-nothing** where it currently shows **wrong-something**. Meaningful paper numbers accrue over **WEEKS** as the poller runs and markets resolve. **Stage 1's win is that the promotion decision stops reading the biased completed lane — not that pinned instantly shows rich numbers.** Tell anyone watching the page, so "the deploy broke pinned" does not become a false alarm three weeks out. (The screen should say so too — an explicit "no resolved paper trades yet" state, not an empty table.)

**Scope — build:**
- **1a. Re-base the adjudicator (`paper.py::adjudicate`) on gamma resolution** (§B). Determine won/lost from `fetch_market_resolutions` (gamma `/markets`, the authority) vs the paper trade's `outcome_index`; book paper P&L via the existing `_paper_realized`; `stale` only when gamma says not-resolved past grace. **This is the load-bearing change** (and the correct source asserting itself — §B framing).
- **1b. Build the paper rollup — migration 009 `pm_paper_category_stats`** (the table `P2_PLAN §6.2` always assumed existed; Stage 0 consumed 008, so this is **009**): PK `(wallet, category)`; `n_closed, wins, losses, win_rate, net_paper_pnl, cost_basis, roi (paper), avg_entry_price, n_open, n_stale, last_resolved_ts, updated_ts`. A `paper_rollup()` deriver (new function; mirrors `stats.rollup()` structure but over `pm_paper_trade WHERE status='closed'`). `n_stale` is surfaced beside `n_closed` (§6.2's honesty requirement — stale paper exits are visible, never silently dropped).
- **1c. Wire the WATCHLIST (pinned) list to `pm_paper_category_stats`** (paper basis) instead of `pm_category_stats` (completed basis). `farm.py` pinned query + the pinned template. (Reads gate on `active=1` — Stage 0.)
- **1d. PLAN (do not run) the poller+adjudicator cadence.** The lane needs the poller running repeatedly (to accrue paper entries) and the adjudicator weekly (to resolve them). Propose a schedule (e.g. poller `*/30`, adjudicator weekly) as a **Jack-authorized deploy/cron decision** — Stage 1 writes the plan; Jack runs the one-shot unstick (poller re-run → adjudicate) and installs any cadence in a calm window.

**Scope — NOT touched:** `poll_pinned` vanish-detection logic (unchanged apart from the `active=1` gate from Stage 0); `pm_closed_position` (the adjudicator STOPS reading it); the completed lane (`stats.py`, `pm_category_stats`) — prospects keep reading it as-is.

**Dependencies:** Stage 0 (the `active=1` gate + the 92-pair funnel). External: Jack must run the poller/adjudicator (parked for a calm window — writes the live DB). No ruling needed for 1a/1b/1c (correctness + a deferred table); RULED §F-1 confirms the completed lane stays a screening source, so 1c's basis-switch is the whole fix, not a fix-plus-re-plumb.

**Reuse / replace / delete:** REUSE `PolymarketDataAPIClient.fetch_market_resolutions` (gamma), `pm_paper_trade` (migration 005), `_paper_realized`/`_past_grace`/`get_config`, `stats.rollup` as the shape template. REPLACE the `pm_closed_position`-match branch of `adjudicate()`. DELETE nothing.

**Verification (BASIS tests, not presence):**
1. **The test that would have caught the substitution:** seed a `(wallet,category)` where **paper WR ≠ completed WR** (e.g. paper 2W/3L=40%, completed 8W/1L=89%); render the Watchlist list; **assert it shows 40% (paper)**, not 89%. If the code reads `pm_category_stats`, this FAILS. (This is the exact drift that shipped undetected.)
2. **The gamma re-base test:** seed a paper trade whose market gamma-resolved as a LOSS but has **NO `pm_closed_position` row** (the omission case); assert `adjudicate()` books it **`closed`/lost** (via gamma), not `stale`. The old adjudicator staled it (inherited bias); the new one closes-lost it.
3. Standard: box-scratch pytest actuals + render the pinned list against a WAL-safe copy of live, confirming the paper basis (and the honest empty-state renders as text, not a broken-looking blank table).

**Size: MEDIUM.** The rollup + wiring mirrors `stats.py`; the adjudicator re-base is focused and load-bearing; the poller/adjudicator already exist. Not large (no new screens).

---

### STAGE 2 — THE SCREENS (Farm League hierarchy) — with a scope correction

**★ SCOPE CORRECTION:** the requirement's **main dashboard shows Account-Category (sub-division) tiles — which are P3 (no sub-divisions exist).** So Stage 2 builds the **Farm-League hierarchy** in full and only a **shell** for the main dashboard:
- **Buildable now:** the main-dashboard **shell** (two menu options: a placeholder "Sub-divisions" section [empty until P3] + "Farm League"); the Farm-League screen → **category TILES** (the **15 RULED-IN categories**, §F-2); the **per-category page** = **WATCHLIST section on top (paper basis, from Stage 1)** + **PROSPECTS section below (completed basis)**.
- **Tile set (RULED §F-2 — 15):** `mlb · nba · nfl · nhl · wnba · epl · ucl · soccer · atp · wta · tennis · cs2 · golf · ufc · fed`. **Three of these (`nhl`, `ufc`, `fed`) are included on OPERATOR KNOWLEDGE over the probe output** — the probe could not read their game-line tickers (429-throttled for nhl/ufc; Sports-only scope for fed). **Their tiles render; their matcher tickers are UNMEASURED and gated on the follow-up probes (§F-2-PROBES) — inclusion ≠ ticker-verified.** No tile is rendered for cbb/fifwc/unknown (removed in Stage 0).
- **P3-deferred:** the sub-division tiles + the sub-division detail (live trades). The main dashboard renders an empty/"coming in P3" sub-division area — honest, not fabricated.

**Scope — build:** new routes `GET /` (dashboard shell), `GET /farm` (category tile grid — replaces the flat filtered list), `GET /farm/{category}` (per-category detail: watchlist-top + prospects-below, each drilling to a whale detail). New templates. **Replace** the flat `/farm`. **`/scoreboard`: RETIRE the standalone page, REPURPOSE its ranking (`query_scoreboard`) into the prospects-section ranker** scoped to `(category, candidates)` — RULED §F-4; see §E.

**Scope — NOT touched:** `stats.py`/`pm_category_stats` (prospects read it as-is, rough per §F-1); `analyze.py`; `pm_closed_position`.

**Dependencies:** **Stage 1** (watchlist section reads `pm_paper_category_stats`), **Stage 0** (`active=1` funnel). Rulings LANDED: `/scoreboard` repurpose+retire (§F-4); tile set = the 15 (§F-2); vocabulary Prospect/Watchlist/Live (§F-3).

**Migration: NONE** (read-only reshaping of existing tables into new routes/templates).

**Reuse / replace / delete:** REUSE `farm.py` queries (scoped per category), the three-state poll logic, `scoreboard_flags`, `query_scoreboard` (repurposed into the prospects ranker), the whale-drill (`positions.py`, `pm_position_rows.html`). REPLACE `pm_farm.html` + `partials/pm_farm_lists.html` with the tile→page templates. DELETE the standalone `/scoreboard` route + `pm_scoreboard.html` after repurposing (§E).

**Verification (BASIS tests):**
- **The three-lists-three-bases test (anti-drift core):** seed a pair present as BOTH a prospect and a watchlist whale, with **different** paper vs completed numbers; render `/farm/{category}`; **assert the WATCHLIST section shows the PAPER number and the PROSPECTS section shows the COMPLETED number.** A silent cross-wiring breaks this.
- Nav: `/farm` renders exactly the **15 RULED tiles** (no cbb/fifwc/unknown tile); each links to `/farm/{category}`; the dashboard shell renders Farm League + the P3-empty sub-division area honestly.

**Size: LARGE.** New nav model + multiple new screens/templates + replaces the deployed farm. The biggest UI stage.

**★ DISCARDED BUILT WORK (say it plainly):** CP3b-1's flat farm UI — `pm_farm.html` (40) + `partials/pm_farm_lists.html` (113) + the farm-page CSS + the `/scoreboard` page (`pm_scoreboard.html` 45 + `partials/pm_scoreboard_table.html` 65) — is **superseded/reshaped**: roughly **~260 lines of template + the flat-list route logic are replaced.** The underlying `farm.py` queries, the three-state-zero logic, the caveat macros, and `query_scoreboard` are **reused** (re-scoped). Net: a real but bounded discard, concentrated in the presentation layer.

---

### STAGE 3 — THE ACTIONS — with a P3 scope split

**★ SCOPE CORRECTION:** the pinned whale's **"Promote" button = promote to a LIVE sub-division = P3** (no sub-divisions). So Stage 3 builds the **farm-level** actions only; the pinned "Promote" renders **disabled with a "P3" tooltip**.

**Scope — build:**
- **Promote-to-watchlist (prospect → pinned):** flip `pm_watchlist.status` `candidate→pinned` (and `active=1`), add the pair to `pm_roster`, and **seed the initial `pm_paper_trade` record** (reuse the paper seed path). Operator-controlled, manual. **Coupled to Stage 4** (needs prospects; dormant until Search populates candidates — §B).
- **Demote (pinned → PROSPECT) — RULED §F-5, NOT pinned→gone:** flip `status` `pinned→candidate`. **Two hard build requirements:** (a) the pair's **paper trades SURVIVE** the demotion (never cascade-delete `pm_paper_trade`); (b) the demoted pair's **screen basis flips back to completed-trades** (renders as a Prospect) **while its paper history stays reachable** (the whale-detail paper view still resolves; a later re-pin resumes with history intact). Reasoning: demote means "not proven," not "never existed." *(Note: Demote and Stage-0 removal are distinct — Demote is candidate↔pinned within the funnel; Stage-0 removal is `active=0`, off-funnel entirely. Both preserve paper history.)*
- **Promote (pinned → live):** **P3, out of scope** — disabled button.

**Scope — NOT touched:** the completed/paper rollups; `analyze.py`; discovery.

**Dependencies:** **Stage 2** (the page/buttons); **Stage 4** for Promote-to-watchlist to have prospects. Demote target RULED (§F-5).

**Migration:** likely **NONE** (uses `pm_watchlist.status`/`active` + `pinned_ts` + a seeded `pm_paper_trade`). A provenance column (who/when pinned/demoted) is worth adding for legible re-pin history — defer unless wanted.

**Reuse / replace / delete:** REUSE `pm_watchlist`/`pm_roster`/`pm_paper_trade` + the CP3a seed logic. REPLACE/DELETE nothing (never delete paper rows on demote — §F-5).

**Verification (BASIS tests):**
- Pin a prospect → assert it **moves** (disjoint: off Prospects, on Watchlist), a `pm_paper_trade` seed exists, and **its stats source SWITCHES from completed to paper.**
- **Demote round-trip (RULED §F-5):** pin → accrue a paper trade → demote → assert (a) the paper trade STILL EXISTS, (b) the pair renders as a Prospect on the COMPLETED basis, (c) the paper history is still reachable, (d) re-pinning resumes with the old paper rows intact.

**Size: SMALL–MEDIUM.** State transitions + buttons; no new tables/screens.

---

### STAGE 4 — SEARCH (populate prospects)

**Scope — build:** **fork** the legacy scout into the PM package (like Analyze — legacy is live PCT code, `DO NOT edit/import`): discovery via `/v1/leaderboard` per category → the **selection rule** (the "trackable" definition — **Jack's ruling still open, `PM_STATE_REVIEW §9 Q2`**) → **backfill** found wallets into `pm_closed_position` (reuse `ingest`) → write candidates (`pm_watchlist status='candidate'`) → rank the prospects (`query_scoreboard`, completed basis — rough, per §F-1) → record the run in **migration 010 `pm_search_run`** (Stage 1 took 009, so this is **010**; shape from the legacy summary dict — `PM_STATE_REVIEW §6 Q6`). **Resolve the rank-before-backfill circularity** (`§9 Q3`): rank **after** backfill OR on a discovery-time inline compute — **Jack's ruling still open.**

**Search categories (RULED §F-2):** search the **15 RULED-IN categories** (`mlb, nba, nfl, nhl, wnba, epl, ucl, soccer, atp, wta, tennis, cs2, golf, ufc, fed`). Do **not** search cbb/fifwc/unknown (removed in Stage 0; cbb re-enters after its probe, fifwc next WC cycle). **For `nhl`/`ufc`/`fed` the matcher needs the game-line tickers the follow-up probes (§F-2-PROBES) will confirm** — Search can discover whales now, but the copy-matcher wiring for those three waits on the ticker inventory.

**Scope — NOT touched:** legacy scout files (fork, never edit); the paper lane; the completed rollup math.

**Dependencies:** **Jack rulings still open** — Q2 (trackable definition), Q3 (rank-before-backfill). Category set RULED (§F-2). **The loss-omission (§F-1):** Search ranks candidates on the biased completed stats — a **rough screen**; Analyze is the promotion judge. **The bias is not fully quarantined:** it shapes the candidate SET, just not the pin/promote decision. Acceptable per §F-1, but stated.

**Migration: 010** `pm_search_run`.

**Reuse / replace / delete:** REUSE (as FORK source) `seed_polymarket_watchlist_deep.py` / `refresh_polymarket_whales.py`; REUSE `PolymarketDataAPIClient`, `ingest` (backfill), `stats` (rank). DELETE nothing.

**Verification (BASIS test):** a newly-searched+backfilled candidate renders **completed-trade numbers** (fresh `pm_closed_position` rows), **not** paper, **not** fabricated-empty; assert the ranking key is **cost-ROI, never win%** (chalk lesson).

**Size: LARGE.** Fork + discovery + backfill + ranking + the circularity resolution; the most ruling-blocked stage (Q2/Q3 open).

---

### STAGE 5 — ANALYZE INPUT INTEGRITY (make the promotion judge honest)

**Scope — build (RULED §F-1: screening-source):** make Analyze's **input** honest independent of the loss-omitted foundation. For the **single pair being analyzed**, re-source the **losses** via `/activity` REDEEM-grounding (the method the loss-visibility probe already implements), reconcile against `/closed-positions`, and feed Analyze the honest loss set. Per-pair, on-demand — does **not** re-plumb the platform-wide completed rollup (§F-1 (i) was ruled out precisely to avoid that).

**★ THE LARGE-WHALE CORRECTION (carried from Jack — my review was wrong here):** `/activity` truncates at 5,000 rows. Single-pair scoping helps **small** whales (full history) but **does NOT eliminate truncation for large ones** — **BetMechanic/nba alone has 6,782 resolved decisions.** So Stage 5 must **not assume truncation away.** Design: Analyze **measures the loss-completeness coverage** (the loss-probe's `A_only`-within-window method) and **stamps every report with an explicit, measured completeness bound** — full history for small whales; a recent window for large whales. Analyze tells the operator *how honest its own input is*, per whale.

**Scope — NOT touched:** the platform-wide completed rollup (per-pair, on-demand); the paper lane.

**Dependencies:** RULED §F-1 fixes Stage 5's shape. The loss-visibility measurement (done). Optionally the KV wiring (`§9 Q9`) so Analyze produces LLM verdicts — but the **input-honesty work is independent of whether the LLM is wired** (the deterministic report + completeness stamp render regardless; today every verdict is correctly `llm_unavailable`).

**Migration:** optional **011** — a `loss_completeness` field on `pm_analysis_cache`, OR fold into `report_json` (no migration).

**Reuse / replace / delete:** REUSE `analyze.py`, the client (`/activity` + gamma), and the **loss-visibility probe's method verbatim**. No deletions.

**Verification (BASIS test):** Analyze on **evanng** (known loss-omitted) → assert the report's loss input reflects the **/activity-REDEEM truth (≈89 held losses)** with a stated completeness bound, **NOT** the `/closed-positions` 33; and for **BetMechanic**, assert the report **carries a windowed-completeness caveat**, not a false "complete."

**Size: MEDIUM–LARGE.**

---

## §D. WHAT GETS DELETED OR RETIRED (part of the plan, not cleanup)

**FACT — within the PM package there is less dead code than "multiple stats attempts" implies.** Those are **git-history iterations on ONE live `stats.py`**, not parallel live programs. The cross-lineage duplication (legacy scorers/scouts vs PM's `stats.py`) is **legacy-side** — `DO NOT TOUCH LEGACY`; PM forks it, PM does not delete it. Actual PM deletions are modest and staged:

| Retire | When / depends on | Note |
|---|---|---|
| Standalone `/scoreboard` route + `pm_scoreboard.html` + `pm_scoreboard_table.html` | **In Stage 2** (RULED §F-4) — after `query_scoreboard` is repurposed | Not a required screen; re-commits the pinned-basis error flat |
| Flat farm templates `pm_farm.html` + `partials/pm_farm_lists.html` | **In Stage 2** | ~150 LOC presentation, superseded; `farm.py` logic reused |
| `roi_notional` column from the **product** UI (keep in a diagnostics view) | Stage 2 | Second ROI number invites misreading; retain for scout comparison only |
| `pm_cli analyze` subcommand mismatch | Housekeeping | On the branch, **not the box** — reconcile at the next deploy |

**Docs to mark SUPERSEDED (not delete — history):** scout-shortlist numbers in `FARM_RERANK`/`POSTP1_ITEMS`/`STEP5`. **Legacy code stays untouched by rule.** **cbb/fifwc pairs are REMOVED (Stage 0), not deleted — never recorded as "not copyable."**

---

## §E. `/scoreboard` — RULED §F-4: REPURPOSE THE RANKER, RETIRE THE PAGE

RULED option 2. `/scoreboard` is a P1 data contract that became an unspecified page ranking PINNED pairs on COMPLETED stats — the basis error in its purest form on a top-level screen. `query_scoreboard` is proven and the prospects section needs exactly that ranking. **Move the code into the prospects-section ranker (scoped to `(category, candidates)`), delete the standalone page — in Stage 2.**

| Option | What happens | Consequence |
|---|---|---|
| Keep (rejected) | Leave `/scoreboard` as a flat ranking | Wrong-basis surface persists; not a requirement screen |
| **Repurpose (RULED)** | Move ranking into the **prospects section** of `/farm/{category}`; retire the page | Reuses proven ranking; number lands where the requirement puts it |
| Retire outright (rejected) | Remove and don't reuse | Loses a tested ranker the prospects section needs |

---

## §F. DECISIONS — RULED 2026-08-26 (Jack's rulings + reasoning; recorded so they stop drifting)

**§F-1 — SCREENING SOURCE (RULED).** `/closed-positions` is a **screening source with a labelled, measured bias — NOT the system of record.** *Reasoning (Jack):* the architecture already implies it — rough prospect stats acceptable, Analyze is the judge, promotion runs on our own paper record. Ruling it system-of-record would force platform-wide loss re-sourcing through `/activity` — the endpoint the platform **deliberately left** because it truncates at 5,000 rows — trading a **measured** bias for an **unmeasured** one and reopening the problem P1 solved. *Action:* label the bias on every prospect-facing number; correct it **in Analyze, per pair, with a measured completeness bound.** *Unblocks:* **Stage 5.**

**§F-2 — NARROW THE TILES TO KALSHI-COPYABLE; MEASURE FIRST → RULED 15 IN.** *Reasoning (Jack):* he had been erring toward **keep-everything-and-remove-as-proven-uncopyable**; he accepts the **inverse** — start with only what we KNOW (or, on operator knowledge, are confident is) copyable — because his operating plan is **ONE CATEGORY AT A TIME**; a narrow known-good funnel suits that better than a wide speculative one. **★ Excluded ≠ un-copyable.** Some excluded categories are unproven, not rejected. **The 15-IN ruling + the three operator overrides + the three distinct exclusion states are in §F-2-RESULTS below.**

**§F-3 — VOCABULARY (RULED).** On screen: "Prospect" (completed) · "Watchlist" (pinned, paper) · "Live" (account-category). In code: `status='candidate'|'pinned'`; table stays `pm_watchlist`. One canonical map (§H). *Reasoning:* Jack is the operator — screens read in his language; the code is internal and renaming is a migration for no user-visible gain.

**§F-4 — REPURPOSE THE RANKER, RETIRE THE PAGE (RULED).** See §E. Move `query_scoreboard` into the prospects-section ranker; delete `/scoreboard` in Stage 2.

**§F-5 — DEMOTE SENDS PINNED → PROSPECT (RULED), not pinned → gone.** *Reasoning (Jack):* the paper record is what we spend weeks accumulating; deleting the pair discards it; demote means "not proven," not "never existed"; a demoted pair must be **re-pinnable later with history intact.** Requirements (Stage 3): paper trades **SURVIVE**; screen basis **flips back to completed** while paper history stays reachable.

### §F-2-RESULTS — KALSHI LISTING PROBE (MEASURED) + THE 15-IN RULING

**Probe:** `cc\pm_kalshi_listing_probe.ps1` + `cc-cp3b\reports\prediction_markets\runners\pm_kalshi_listing_probe.sh` — UNAUTHENTICATED public Kalshi market-data API (`/series`, `/markets`) via python stdlib `urllib`, plus `pm_watchlist` read `mode=ro`. **Run 2026-08-26 17:19Z, azureuser, engine PID 37596 unchanged (before==after), exit 0, 6,971 Kalshi calls, Sports catalog = 3,516 series.** No key, no auth, no write, no box mutation, no engine/poly_kalshi_mlb touch. Reproducible.

**⚠ RATE-LIMIT NOTE (for the next probe author):** the probe's **0.15s call spacing was slightly hot** for Kalshi's limiter and produced **HTTP 429s** that made two categories' game-line checks (`nhl`, `ufc`) **unreadable** — the direct cause of two of the three operator overrides below. **Any future probe uses WIDER spacing.** And **6,971 calls in one run is a lot** (it exhaustively checked every keyword-matched series per category) — future probes should short-circuit per category once the game-line series is confirmed, or scope the series list up front.

**18 pinned categories = 114 pairs.** The probe's **raw** verdict = "Kalshi lists an equivalent series with OPEN markets." **★ The raw verdict OVER-CONFIRMS:** it fires on *any* open market, including **futures/novelty** (mlb surfaced `KXCITYMLBEXPAND`, nba `KXBBALLTEAMUSA`, nfl `KXCOACHOUTNFL`). The **copy target** is the **GAME-LINE / MATCH series**. The table shows the measured evidence and the RULED tile decision (which OVERRIDES the probe in three rows — recorded as judgement, not oversight):

| category | pins | game-line series (measured) | game-line OPEN? | RULED |
|---|---|---|---|---|
| **mlb** | 10 | `KXMLBGAME`+`KXMLBSPREAD`+`KXMLBTOTAL` | ✅ all three | **IN** (measured; strongest, in-season) |
| **wnba** | 4 | `KXWNBAGAME` | ✅ | **IN** (measured; in-season) |
| **epl** | 6 | `KXEPLGAME` | ✅ | **IN** (measured) |
| **ucl** | 6 | `KXUCLGAME` | ✅ (today) | **IN** (measured) |
| **soccer** | 8 | `KXEPLGAME` + league games | ✅ | **IN** (measured) |
| **atp** | 8 | `KXATPMATCH` | ✅ | **IN** (measured) |
| **wta** | 4 | `KXWTAMATCH` | ✅ | **IN** (measured) |
| **tennis** | 4 | `KXATPMATCH`/`KXWTAMATCH` | ✅ | **IN** (measured) |
| **nba** | 8 | `KXNBAGAME` | ✅ (posted, Oct openers) | **IN** (measured) |
| **nfl** | 9 | `KXNFLGAME` | ✅ (posted, Sep) | **IN** (measured) |
| **cs2** | 2 | `KXCS2MAP`/`KXCS2` | ✅ (esports modality) | **IN** (measured) |
| **golf** | 3 | tournament (`KXCHAMPTOUR`…) | ✅ (tournament modality) | **IN** (measured) |
| **nhl** | 6 | `KXNHLGAME` | ❓ **429-throttled = UNREADABLE** | **IN — OPERATOR OVERRIDE #1** (National Hockey League, copyable; 429 is not absence; ticker UNMEASURED → probe #2) |
| **ufc** | 10 | `KXUFCFIGHT` | ❓ **429-throttled + last-seen settled** | **IN — OPERATOR OVERRIDE #2** (Ultimate Fighting Championship, copyable; 429 is not absence; ticker UNMEASURED → probe #2) |
| **fed** | 4 | *not in Sports catalog (Economics)* | ❓ **probe never looked** | **IN — OPERATOR OVERRIDE #3** (FOMC rate decisions, copyable — prior analysis; scope-artifact was the correct call → this is inclusion not mystery; ticker UNMEASURED → probe #3) |
| cbb | 3 | keyword false-matched `KXARGNACBBTTS` (Argentine soccer) | ❓ **NOT PROBED** | **OUT — NOT-PROBED / PENDING-ANALYSIS** (one correct-keyword NCAAB probe settles it; off-season regardless). **Never "not copyable."** |
| fifwc | 8 | WC2026 concluded (Jun–Jul); only host/futures open | ❌ **MEASURED DORMANT** | **OUT — MEASURED-DORMANT (calendar)** (a real measurement, not a knowledge gap; **dormant by CALENDAR, returns next WC cycle** — a DIFFERENT state from pending-analysis; do not conflate). **Never "not copyable."** |
| unknown | 11 | tier-1 slug-derivation **failure** — not a subject | — | **OUT — STRUCTURAL (PERMANENT)** (can never have a Kalshi equivalent; never gets a tile) |

**★ RULED TILE SET — 15 IN (92 pairs):** `mlb · nba · nfl · nhl · wnba · epl · ucl · soccer · atp · wta · tennis · cs2 · golf · ufc · fed`. 12 measured-copyable + 3 operator overrides (nhl, ufc, fed).
**★ 3 OUT — THREE DISTINCT STATES (record differently, never conflate):**
- **cbb → NOT-PROBED / PENDING-ANALYSIS** (a knowledge gap; one probe fixes it).
- **fifwc → MEASURED-DORMANT (calendar)** (measured, not a gap; returns next World Cup cycle).
- **unknown → STRUCTURAL (permanent)** (not a category; never returns).

**★ INCLUSION IS RULED; THE TICKER INVENTORY IS NOT.** nhl/ufc/fed are IN on operator knowledge; their game-line **tickers are UNMEASURED**. Do not let "we included it" become "we verified it." Stage 4's matcher needs those tickers — supplied by the follow-up probes below.

### §F-2-PROBES — THREE STANDALONE FOLLOW-UP PROBES (each individually authorized; NOT folded into any build stage)

1. **Correct-keyword NCAAB probe for `cbb`** — decides cbb's return to the tile set (currently OUT/pending). Read-only.
2. **Slower, un-throttled game-line probe for `nhl` + `ufc`** — **NOT to decide inclusion (ruled IN)** but to **CONFIRM the series tickers** (`KXNHLGAME`, `KXUFCFIGHT`) the Stage-4 matcher will need. Read-only, wider spacing.
3. **Economics-catalog probe for `fed`** — same reasoning: included on knowledge, **tickers unmeasured**; confirm the FOMC/rate series tickers for the matcher. Read-only.

These stay on the books as separate authorized runs. They do not block the 15-tile Stage 2; probe #1 can promote cbb OUT→IN; probes #2/#3 feed the matcher, not the tile.

---

## §G. CORRECTION CARRIED (Jack's catch on my review)

My state-review said the 5,000-row `/activity` truncation is "a non-issue for one whale." **TRUE for small whales, FALSE for large ones** — BetMechanic/nba has 6,782 resolved decisions. **Stage 5 handles this explicitly** (windowed, measured completeness bound per whale). Correction accepted and built in.

---

## §H. ANTI-DRIFT MEASURE (a real deliverable)

Requirements were lost because handoff docs carried schemas/SHAs and dropped the product description; the missing-rollup substitution shipped because **no test asserted a number's BASIS, only its presence.** Three-part fix:

1. **WHERE the requirements live — one durable doc, in the code's path.** Promote `PM_STATE_REVIEW §0` into **`reports/prediction_markets/PM_REQUIREMENTS.md`** (committed on the branch), referenced from **`trading_corp/prediction_markets/__init__.py`'s docstring** and from every `TRANSITION_TO_*` handoff. **Embed the canonical vocabulary map (below) verbatim.**
2. **WHAT makes reading it mandatory — a checkpoint exit question.** Every checkpoint report answers: **"Which of the three lists did this change touch, and did it keep their three data bases (completed / paper / live) separate?"** Transition-doc template required first line: *"Restate the three lists and their three bases from memory before touching code."*
3. **WHAT verification catches a substitution — requirements-as-tests (BASIS tests).** For **every displayed number**, a test seeding a pair where the **required-source value ≠ the wrong-source value**, asserting the UI shows the **required** source. The pinned-list substitution shipped because the only test asserted *presence*; a BASIS test would have **failed loudly** on the fallback. **Every stage in §C carries at least one** (Stage 1 test #1 is the exact one that would have caught the historical bug).

**★ CANONICAL VOCABULARY MAP (RULED §F-3 — write once, here and in `PM_REQUIREMENTS.md`):**

| Screen word (Jack's) | Farm-league section | Data basis | Table / source | Code value |
|---|---|---|---|---|
| **Prospect** | bottom section | **completed trades** | `pm_closed_position` → `pm_category_stats` | `pm_watchlist.status='candidate'` |
| **Watchlist** | top section | **our paper trades** | `pm_paper_trade` → `pm_paper_category_stats` | `pm_watchlist.status='pinned'` |
| **Live** | account-category sub-division | **live trades** | P3 tables (not built) | — (P3) |

Screens render Jack's words; code keeps its values; the **table name stays `pm_watchlist`.**

---

## §I — OPINIONS / RECOMMENDATIONS (mine, labeled; separate from the facts above)

- **The sequence is right; Stage 1's content is not — fix the adjudicator first.** Everything in §B. If only one thing survives to the next agent: *finishing the paper lane requires re-basing the adjudicator on gamma — the resolution authority — or the paper lane inherits the loss-omission and the reframe silently fails the same way the pinned rollup did.*
- **Sequence the 15 tiles by in-season liquidity, not alphabetically.** For Jack's one-category-at-a-time model, start where game lines are open and deep **now**: `mlb` (full ML+spread+total) and `wnba` first; then `soccer/epl/ucl` (in-season); then `nfl` (opening Sep), `nba/nhl` (opening Oct); `ufc` is rolling weekend events; `fed` is scheduled FOMC — **low-noise, unambiguous resolution, arguably the most stable category once its tickers are confirmed** (probe #3). `cs2/golf/atp/wta/tennis` are different modalities / rolling — a second wave.
- **Do the three follow-up probes before Stage 4's matcher, not before Stage 2's tiles.** The tiles are ruled and render now; the matcher is what needs nhl/ufc/fed tickers. Probe #1 (cbb) is independent — run it whenever, since cbb is off-season anyway.
- **Biggest risk in this plan:** Stage 2 is LARGE and replaces deployed UI while markets are live; gate it hardest (build → box-scratch render → **Jack looks in a browser** → deploy), exactly as CP3b-2 Gate 2 was.
- **Shortest path to a trustworthy Analyze** (the point): Stage 1 (quarantine the bias off the promotion decision) + Stage 5 (honest per-pair loss input with a measured completeness bound). Stages 0/2/3/4 make the workflow *operable*; Stages 1 and 5 make the *decision* honest. If forced to cut, cut nav polish before either.

---

**Deliverable status:** plan only. No code, no commits, no branch, no migration, no box mutation **except the one read-only Kalshi listing probe Jack authorized** (§F-2-RESULTS; engine PID 37596 unchanged, nothing written). No further probes run. The poller/adjudicator were **planned, not run**; legacy untouched; `poly_kalshi_mlb` untouched. Five rulings + the 15-IN tile ruling folded; gamma-authority framing recorded; the 22-pair reversible removal (Stage 0) proposed with preserve-history + three distinct reasons; the three follow-up probes kept standalone; the rate-limit note recorded. Path handed to Jack; NOT committed.
