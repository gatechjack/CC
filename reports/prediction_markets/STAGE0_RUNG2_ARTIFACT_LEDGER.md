# Stage-0 Rung-2 Artifact Ledger (MANIFEST) — 2026-08-27

**What this is:** the byte-verified **source-of-truth manifest** for the Stage-0 rung-2 code deploy. It is a
DOC on the branch `prediction-markets-stage0-2026-08-26` — **not** a prod-live commit and **not** the prod-live
lineage. The real prod-live path-checkout commit (the CP3a/CP3b-2 format) is created **AT rung-2 deploy,
post-deploy, from this manifest** (see "How the prod-live commit is created" below).

**Mode:** manifest only. **No deploy, no pm_web restart, no prod-live movement, no row write.** Authored under
Jack's Ruling 1 (2026-08-27): manifest-on-branch, NOT a prepared commit on a new ref, NOT a prod-live-format
artifact on the stage0 tree.

---

## ⚠ THE ONE THING A LATER READER MUST NOT MISREAD

**box != branch AT THE TIME OF WRITING.** The box runtime still runs the pre-Stage-0 code (prod-live `95e78c4`
== the box for these files). This manifest records the **branch-side** sha256 of the files rung 2 WILL deploy.
**`box == branch` is a rung-2 GATE — to be PROVEN by a fresh `sha256sum` on the box at Gate-2 deploy time —
NOT a claim being made now.** This is exactly why this is a manifest and not a prod-live commit: authoring the
CP3a/CP3b-2 "record ... (== box)" commit now would assert something currently false. That commit is inherently
POST-deploy (its standard is a fresh box re-hash); it is created at rung 2.

---

## Deployed artifact set — 5 PM-ONLY files (Ruling 2, 2026-08-27)

Base = branch `prediction-markets-stage0-2026-08-26`. sha256 = of the file CONTENT (== `git show <branch>:<path>
| sha256sum`, == what `sha256sum <file>` yields on the box for LF-identical bytes). Additive on prod-live
`95e78c4`.

| # | Artifact (box path under `~/trading_corp/`) | branch sha256 | change |
|---|---|---|---|
| 1 | `trading_corp/prediction_markets/db.py`       | `76eb52b22ed9e9a1eb45091e56535cd54d681536a7f15a36b4938618dbc93782` | migration 008 (`active`/`removal_reason`/`removal_ts` + `ix_pm_watchlist_active`) |
| 2 | `trading_corp/prediction_markets/paper.py`    | `14020d62376b70f90c56d80e6392305065806dd36f8a439685d2f1569f2caddc` | `AND active=1` on poller / subset-assertion / seeded-pairs review |
| 3 | `trading_corp/prediction_markets/farm.py`     | `428c4a7ccc717f44bbea89c2dcddd54ec972c2c68bf16aec639c41294bae6b20` | `AND active=1` on tile set / pinned+candidate list / candidate count |
| 4 | `trading_corp/prediction_markets/stats.py`    | `2867de83ee0f93103472c7c0d6aa9af4b0799bf057d61ae7e1f0a2703fddc681` | `query_scoreboard` LEFT-JOIN gate `(wl.active IS NULL OR wl.active=1)` |
| 5 | `trading_corp/prediction_markets/__init__.py` | `958a809ac7c38d0a91fd461a7306da58c4d8b81309d59f793a78e19f4c633df7` | anti-drift docstring pointer to `PM_REQUIREMENTS.md` (behaviorally inert) |

### db.py sha256 CROSS-CHECK (independent confirmation)
Artifact #1 `db.py` = `76eb52b2…dbc93782`, which is **identical** to the sha256 recorded in the **rung-1
execution record** (`PM_REBUILD_PLAN_2026-08-26.md` §Rung-1, "scratch `db.py` sha256 == branch
`76eb52b2…dbc93782`"). This is independent confirmation that the ephemeral scratch extract that applied
migration 008 to live on 2026-08-27 02:15Z **was the branch `db.py` it claimed to be** — the migration on the
box and the code rung 2 will deploy are the same artifact.

### Why `__init__.py` is IN the set (Ruling 2)
The plan's earlier "4 files" was **stale text written before `__init__.py` changed**, not a ruling. Including
it keeps `box == branch` clean across the **whole** PM package (no accepted-drift file that a future Gate-A
byte-verify must special-case), and the anti-drift pointer only does its job if it actually reaches the box
runtime. The change is a docstring only — behaviorally inert, no added deploy risk; the `/farm` byte-identical
post-check still holds as the behaviour-neutral proof. **Supersedes** the stale "4 files (db/paper/farm/stats)"
wording in `TRANSITION_STAGE0_TO_NEXT_2026-08-27.md` and the memory index (recorded here; those are point-in-time
docs, not re-edited).

### NOT in the artifact set (proven, not assumed)
- **`persistence/db.py` — ABSENT.** That is the ENGINE/MACE shared file (`trading_corp.db`), a *different* file
  with a *similar name* — exactly how a wrong artifact ships. `db.py` here is **only**
  `trading_corp/prediction_markets/db.py`. Rung-2's deploy runner MUST name-guard + leak-abort any path not
  matching `^trading_corp/prediction_markets/` (as CP3b-2 Gate 2 did).
- `trading_corp/scripts/pm_cli.py` — **unchanged** on this branch (no diff vs base); not an artifact.
- tests, reports/docs, `config/*` — not runtime; not deployed.

---

## Behaviour the artifacts carry — the SEVEN gated consumer reads
Each adds `AND active=1` so a removed (off-funnel) pair is invisible to it. With all 114 rows `active=1` at
rung 2, behaviour is IDENTICAL to today (the behaviour-neutral property rung 2 proves).

| # | consumer | site | BASIS test |
|---|---|---|---|
| 1 | `paper.poll_pinned` (the poller) | `paper.py:138` | `test_removed_pair_invisible_to_poller` |
| 2 | `farm.farm_categories` (tile/tab set) | `farm.py:53` | `test_removed_categories_yield_no_tile` |
| 3 | `farm.farm_rows` (pinned + candidate list) | `farm.py:82` | `test_removed_pair_off_pinned_list_and_summary` |
| 4 | `farm.farm_summary` (candidate count) | `farm.py:120` | `test_removed_candidate_not_counted` |
| 5 | `paper.assert_pinned_subset_of_refresh` | `paper.py:293` | `test_removed_pinned_excluded_from_subset_assertion` (+ `..._still_fires_for_ACTIVE_unrefreshed`) |
| 6 | seeded-pairs review | `paper.py:444` | (review-table gate) |
| 7 | `stats.query_scoreboard` (prospects ranker) | `stats.py:291` | `test_deactivated_pair_absent_from_query_scoreboard` (+ `..._pair_grain_no_fanout`) |

(`db.py` carries migration 008 itself; `__init__.py` is the docstring pointer — neither is a gated read.)

---

## How the prod-live commit is created (AT rung-2 deploy, from this manifest)
This mirrors CP3a (`2fc9173`) / CP3b-2 (`95e78c4`) — post-deploy, byte-verified by a **fresh box re-hash**:
1. Rung-2 Gate-2 deploys the 5 files to the box and restarts pm_web (separate Jack authorization).
2. On the box, `sha256sum` each of the 5 deployed files and confirm **each == the branch sha256 in this
   manifest** (this is the `box == branch` proof). If ANY disagrees → STOP, do not advance prod-live.
3. In the prod-live worktree (`cc-prodlive-cp7-wt`, on `prod-live @ 95e78c4`), path-checkout the 5 files from
   the branch and commit with the precedent message:
   `deploy(pm-cp3b-stage0): record Stage-0 rung-2 active-gate artifacts on prod-live (== box)` + body recording
   "sha256 re-hashed on the box at commit time — all 5 MATCH `<branch tip SHA>`. Additive on `95e78c4`.
   Excludes persistence/db.py (MACE shared), scripts/pm_cli.py (unchanged), tests, docs."
4. **Fast-forward push only** — no amend/rebase/force. `95e78c4` stays an ancestor (MACE forks from it).

**Governance constraints (unchanged):** prod-live advances by a NEW fast-forward commit only; `origin/prod-live
@ 95e78c4` is never amended/rebased/force-moved.

---

## Ledger status
Manifest authored on the branch, uncommitted-at-write / committed with this doc. **prod-live NOT advanced;
nothing deployed; no restart; no row write.** The prod-live artifact commit remains a **rung-2 deliverable**,
created at deploy per the recipe above.
