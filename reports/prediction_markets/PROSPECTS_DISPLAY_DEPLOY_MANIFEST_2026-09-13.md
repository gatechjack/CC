# PROSPECTS DISPLAY (pm_whale_score on the whale lists) — STAGED DEPLOY MANIFEST (2026-09-13)

**Status: STAGED. HALT AT THE DEPLOY + THE RESTART.** Nothing runs until Jack authorizes each step
("board authorizes atomic execution: `powershell -ep bypass -f .\NAME.ps1`"), one at a time. Runners are authored +
validated from a fresh read-only pre-flight at authorization time.

- **Branch:** `pm-prospects-display` — HEAD **`8cf29d9b`** (`5157470f` build → `8cf29d9b` skeptic fixes).
- **Base (== box):** prod-live **`614313bb`** — pm_web `prediction-markets-web` (PID from pre-flight), engine
  `trading-corp` **370246** (untouched), PM schema **head 23** (`pm_whale_score` present — the display reads it).
- **Deploy class:** ★ **pm_web-only, NO migration, NO db.py, ONE `prediction-markets-web` restart, engine NEVER
  touched.** The score table (migration 023) is already live; this deploy only READS + DISPLAYS it. The simplest
  deploy shape (like the milestone / livefix pm_web-only deploys).

---

## 1. What ships (9 runtime files — scp+tar graft; tests + this doc NOT grafted)

| File | Change |
|---|---|
| `scoring.py` | `tier_rank` + `score_sort_key` (the tier-caps-the-number composite) — additive; pm_web-only (engine imports none of scoring/analyze/loss_grounding) |
| `web/app.py` | `_score_cell`, `_load_whale_score_map`, `_SCORE_UNANALYZED_SORT`, `import scoring`; wired into `_load_farm_category` (prospects + watchlist) and `_load_live_subdivision` (roster); `_run_analyze` emits `score_oob` |
| `web/templates/pm_macros.html` | `score_cell` (full) + `score_badge` (compact) macros |
| `web/templates/partials/pm_prospects_rows.html` | the JUDGE column (tier + score, sortable; data-sort-value NEGATED for the ascending-first sorter) |
| `web/templates/partials/pm_watchlist_rows.html` | compact score badge |
| `web/templates/partials/pm_whale_roster.html` | compact score badge on the /live roster |
| `web/templates/partials/pm_analyze_result.html` | OOB score-cell update (loop closing on the list) |
| `web/templates/pm_shell.html` | `pm_desk.css?v=` bump **422c45ec → 2a290250** |
| `web/static/pm_desk.css` | tier/score badge styles (`.pm-tier-*`, `.pm-score-*`, `.pm-judge-col`) |

**Engine-untouched proof:** none of the 9 files is imported by the engine (`live_driver`/`execution`/`main` import
no `scoring`/`analyze`/`web.*`). No `db.py`, so no schema/migration surface at all. The running engine (370246) is
unaffected; only pm_web reloads on its restart.

---

## 2. Deploy sequence (each step Jack-authorized, in order)

| # | Step | Runner | Reversible? |
|---|---|---|---|
| 0 | **Pre-flight RO** | `pm_pd_pre_ro.ps1` | read-only |
| 1 | **Graft 9 files** (scp+tar, drift-gated box==`614313bb`) | `pm_pd_graft.ps1` | yes (roll back from a Step-0 file backup) |
| 2 | **ONE pm_web restart** | `pm_pd_restart.ps1` (`az … systemctl restart prediction-markets-web`) | yes (restore + restart) |
| 3 | **Post-check + acceptance** | `pm_pd_post_ro.ps1` | read-only |
| 4 | **FF-push prod-live + tag + docs** | git | git only |

### Step 0 — Pre-flight RO (must hold, else STOP)
- box PM schema **head == 23**, `pm_whale_score` present (the display source).
- the 9 files' box sha256 **== the `614313bb` baseline** (no drift — nobody deployed underneath).
- engine `trading-corp` PID (record — must be unchanged through the deploy); pm_web PID (changes once).
- back up the 9 box files to a dated dir (rollback source — a file backup is enough; no DB backup, nothing writes the DB).

### Step 1 — Graft (the standing scp+tar channel; git-archive LF tar)
- stage the 9 files, drift-gate box == `614313bb` for all 9, `cp` into place, re-verify sha256 == `8cf29d9b` target + CR=0.
- **py_compile** `scoring.py` + `web/app.py`; **import gate**: app + scoring import clean, **zero engine/broker/execution
  modules** (the pm_web standalone invariant); **`pm_desk.css` sha8 == the `?v=` in pm_shell.html** (`2a290250`).
- any mismatch → roll back all 9 from the Step-0 backup, do NOT restart.

### Step 2 — ONE pm_web restart
- `az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "systemctl restart prediction-markets-web"`.
- ★ targets `prediction-markets-web` ONLY. Bouncing `trading-corp` (via restart_tc.ps1) is a STOP — engine 370246 must not move.

### Step 3 — Post-check + THE ACCEPTANCE
- box == prod-live `<new tip>` on the 9 files; **engine still 370246** (a checked line); pm_web NEW pid; healthz 200 schema 23;
  served `pm_desk.css?v=2a290250`.
- ★★ **THE ACCEPTANCE — `0x684baa57c3` must NOT look good.** It is live-copied on `kalshi_jack/mlb` (222 fills), so
  GET `/live/kalshi_jack/mlb` (Remote-User: jack) and assert its roster badge renders **`INSUF DATA`** + a **flagged**
  score (the `pm-score-flagged` marker / `*`), NOT a clean healthy number. Also GET `/farm/mlb` and assert (a) the
  JUDGE column renders, (b) any un-analyzed prospect reads **"not analyzed"** (an [Analyze] control), never a 0, and
  (c) `0x684baa57c3` (if a prospect/watchlist row) shows `INSUF DATA` flagged. If the list makes that whale look like a
  healthy top prospect anywhere, that is a STOP (the display undid the scorer).
- **do-no-harm:** `/farm` and `/live` pages render 200 (no 500 from the new cell); existing columns intact; engine journal
  no new PM errors from the restart; **no DB write** (this deploy reads only).

### Step 4 — Fold, prove, FF
- three-way prove box == prod-live == branch on the 9 files; FF prod-live `614313bb` → `<new tip>`; report the FF push
  command in-session; tag `pm-prospects-display-deploy-2026-09-13`; docs/ledger.

---

## 3. STOP CONDITIONS
- Pre-flight: head ≠ 23 / `pm_whale_score` absent; any of the 9 files' box sha ≠ `614313bb` baseline (drift → reconcile first).
- Graft: any post-`cp` sha/CR mismatch, py_compile failure, import gate loads an engine/broker module, or the CSS `?v=`
  ≠ the file hash → roll back all 9, do NOT restart.
- Restart: must be `prediction-markets-web` only; bouncing `trading-corp` is a STOP. Post-restart pm_web unhealthy →
  restore 9 files + restart pm_web (engine unaffected).
- Acceptance: `0x684baa57c3` renders unflagged / looks like a top prospect, OR an un-analyzed whale renders a 0/low score,
  OR `/farm` or `/live` 500s → STOP and roll back.

---

## 4. Verification already banked (pre-deploy)
- Box-scratch (isolated /tmp, engine untouched): `scoring.py`+`app.py` py_compile OK; **app imports clean, zero
  engine/broker modules**; **`pm_desk.css` sha8 = 2a290250 == the pm_shell `?v=`**; **38/38 new tests pass**
  (scoring tier-cap + `test_prospects_score`, incl. the `0x684`-mirage acceptance and the DOM-negation ordering);
  full `tests/prediction_markets` = the **same 21 pre-existing UI-render failures** (0 new regressions).
- Two adversarial skeptics: Skeptic-1's HIGH (ascending-first sorter would float the mirage/un-analyzed to the top)
  **fixed** (negated JUDGE `data-sort-value`, + a DOM-ordering test); Skeptic-2 found **no BLOCKER/HIGH** (read-only,
  engine-untouched, category/wallet-case-consistent, NULL-correct), its LOW "never break the page" addressed with a
  `_score_cell` try/except. No outstanding BLOCKER/HIGH.

---

## 5. DEPLOYED LIVE 2026-09-13 — OUTCOME (every step board-authorized)

**origin/prod-live `614313bb` → `6afa5ccc` (FF); tag `pm-prospects-display-deploy-2026-09-13`.** pm_web-only, NO
migration; engine `trading-corp` **PID 370246 NEVER touched**; pm_web `prediction-markets-web` **376953 → 379568
→ 381803** (two restarts: the initial deploy + the OOB-fix redeploy).

Sequence as executed:
- **Step 0 pre-flight RO**: head 23, box == `614313bb` (9/9), `pm_whale_score` present (3 scored), `0x684/mlb` =
  INSUFFICIENT_DATA + active on jack/karen mlb rosters (acceptance data present).
- **Step 1 graft** (9 files) → **Step 2 restart** → landed `69921719`.
- **★ Watchlist bug caught in review (Jack):** the Watchlist/roster JUDGE badge stayed "not analyzed" after Analyze.
  Read-only diagnosis: LOAD path fine (all 11 pinned-scored ATP whales render tiers on reload; the mirage acceptance
  actually passes — the earlier post-check "fail" was a slicing bug in the CHECK, two `data-whale` per roster whale).
  Root cause: `score_badge` had no element id, so the analyze OOB (targeting `score_cell`'s `pm-score-{w}-{c}`) never
  reached it → no live update. **Fix:** wallet-only OOB id `pm-scoreb-{wallet}` on `score_badge` (keeps the F-3
  casing guard — a category-bearing id had regressed `test_category_page_knows_its_category` 21→22) + a second OOB
  fragment from the analyze result. `614313bb`→`5157470f`→`8cf29d9b`→`69921719`→`6afa5ccc`.
- **Step 1b re-graft** (4 templates) → **Step 2b restart** → landed `6afa5ccc`.
- **Post-check green:** box == `6afa5ccc` (9/9); engine 370246 unchanged; `/farm/atp` Watchlist = **11 OOB anchors +
  11 tier badges**; mirage acceptance PASSES (`0x684` = `INSUF DATA` + flagged on the live roster); `/farm` + `/live`
  200, heartbeats fresh. No DB write throughout (display reads `pm_whale_score` only).

**Live now:** the stored ANALYZE score (tier + sort number, tier-capped, trust-flagged) shows on **Prospects
(sortable JUDGE column), Watchlist, and the /live roster**; un-analyzed reads "not analyzed" (never a 0); analyzing
from any surface updates the badge in place. Closes Requirement One (the score is viewable and sortable).
