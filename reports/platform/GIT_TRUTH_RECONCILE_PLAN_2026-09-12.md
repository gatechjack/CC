# GIT-TRUTH RECONCILE PLAN — PM + MACE box-truth into prod-live (2026-09-12)

**For Jack to approve. Built and proven locally; NOTHING pushed. The box was read-only; the
engine, DB, and arm state were never touched.** Two candidate commits are ready (a scope-pure
two-line fold and a complete-box variant) plus the exact push commands.

Reserved for Jack (never taken here): PUSH, DEPLOY, RESTART, ARM/HALT, any live-DB write.
This reconcile is **git-only** — a local commit + a fast-forward push of a git ref. It deploys
nothing and cannot change what the box runs.

---

## 0. RE-VERIFIED FACTS (cheap, this session)

| fact | result | evidence |
|---|---|---|
| origin/prod-live tip | **b1c552b1** (unchanged vs report) | VERIFIED `git rev-parse origin/prod-live` after `git fetch` |
| origin/main tip | **61de372** (unchanged; 0 PM files) | VERIFIED `git rev-parse origin/main` |
| box unchanged since report (03:08Z) | **YES** — all 11 divergent-file CR-sha8 identical | VERIFIED `recon`-run 03:40Z == report table |
| merge-base(d19c0ab5, ce5b748c) | **6a5ed04** | VERIFIED |
| brokers/base.py | identical everywhere (e7c8eb28) | VERIFIED (inherited, spot-checked) |
| 95e78c4 reachable | YES (kept; not disturbed) | INHERITED — reconcile adds no history that removes it |

**Provenance table (CR-stripped sha8, local git blobs) — confirms zero graft drift on the 11
divergent files and that the two lines touch DISJOINT files:**

```
file                                   prod-live  PM(d19c0ab5) MACE(ce5b748c) base(6a5ed04)  BOX
prediction_markets/live_driver.py      d0c25810   6561b569     ed9ddd1f       ed9ddd1f       6561b569  (=PM)
prediction_markets/execution.py        09c7e275   14787ba4     b33a8d8c       b33a8d8c       14787ba4  (=PM)
data/ufc_poly_kalshi_match.py          1a16b3aa   ebabe6ce     e5263328       e5263328       ebabe6ce  (=PM)
main.py                                5908b5dc   5908b5dc     cbf4b928       5908b5dc       cbf4b928  (=MACE)
agents/data_exec.py                    ffeca7f7   ffeca7f7     fc8ab253       ffeca7f7       fc8ab253  (=MACE)
brokers/robinhood.py                   ecf5457e   ecf5457e     a26b8d0c       ecf5457e       a26b8d0c  (=MACE)
brokers/base.py                        e7c8eb28   e7c8eb28     e7c8eb28       e7c8eb28       e7c8eb28  (same)
mace/config.py                         d5aeb365   d5aeb365     43fabae5       d5aeb365       43fabae5  (=MACE)
mace/execution.py                      847f0b83   847f0b83     073b6eed       847f0b83       073b6eed  (=MACE)
mace/manager.py                        deb073d3   deb073d3     a28d4e63       deb073d3       a28d4e63  (=MACE)
config/mace.yaml                       49476b0e   49476b0e     bfde856f       49476b0e       bfde856f  (=MACE)
```

Every MACE-side file has **PM == BASE** (PM line never touched them) and every PM file has
**MACE == BASE** (MACE line never touched them). The lines are disjoint at the file level.

---

## 1. Q1 — MERGE COMMIT vs LINEAR LEDGER — evidence + recommendation

**Recommendation: (B) LINEAR LEDGER (single squashed commit). Jack rules.**

### The deciding fact (from the deploy RECORDS, not commit messages)
The five MACE commits were **NOT five deploys.** There were exactly **TWO** box deploy events:

| # | content | box graft | PID | when | config_hash |
|---|---|---|---|---|---|
| 1 broker-failsafe | **7683f59b** (base 6a5ed04) | 6a5ed04 -> 7683f59b (main/data_exec/robinhood/mace-exec) | 313359 -> 344155 | 2026-09-11 22:07:47Z | — |
| 2 GDX time-exit cap | **c2593e34** (base 7683f59b) | 7683f59b -> c2593e34 (mace.yaml + mace/{config,execution,manager}) | 344155 -> 346127 | 2026-09-11 22:51:14Z | 49476b0e -> bfde856f |

Evidence: `reports/platform/BROKER_FAILSAFE_DEPLOY_RECORD_2026-09-11.md` (on 8d2d3bba) and
`reports/mace/GDX_TIME_EXIT_CAP_DEPLOY_RECORD_2026-09-11.md` (on ce5b748c). The GDX record states
"box-truth for MACE files is now **c2593e34**" and "shared trio UNCHANGED" from #1.

VERIFIED: **c2593e34 runtime == ce5b748c runtime** for all 7 files. So of the 5 MACE commits:
- **7683f59b** = deploy #1 (runtime-bearing).
- **c2593e34** = deploy #2 code (runtime-bearing).
- **f049468f** (build manifest doc), **eb487c39** (deploy runners), **ce5b748c** (deploy-record +
  `_gdxcap_deploy/` tooling) = doc/tooling commits; **no new runtime state, never a distinct box state.**

VERIFIED: prod-live is a **strictly linear** ledger — `6a5ed04..b1c552b1` = 13 commits, **0 merges**
(the merges that exist in the repo are all on old, superseded branches). The 2026-09-07 reconcile
convention (which B matches) was a linear ledger.

### Why B over A
- A **merge** (parents d19c0ab5 + ce5b748c) would import all 5 MACE commits as reachable prod-live
  history — including 3 that never ran as a box state. That records states that never deployed =
  a MIRROR of dev history, which Jack's "deploy ledger, not a mirror" ruling rejects.
- A merge would also be the **first merge commit** in the modern prod-live line (breaks the 0-merge
  convention).
- Full MACE-line provenance is preserved regardless, because STEP ZERO pushes the
  `mace-gdx-cap-boxbase-2026-09-11 @ ce5b748c` branch to origin (all 5 commits stay reachable via
  that ref). The merge is not needed to keep history.
- Both A and B produce the **same tree** and both are a fast-forward onto prod-live.

The recommended single ledger commit's message narrates **both** deploy events (PIDs, times,
config_hash) so the one ledger entry accurately documents the two box deploys it folds.
(Alternative if Jack wants event-granularity: a 2-commit ledger, one per deploy event — more
faithful to "one commit per deploy" but adds a synthetic intermediate tree that is already
superseded. Not recommended; noted for completeness.)

---

## 2. Q2 — THE THIRD-LINE RISK (8d2d3bba) — RESOLVED

**8d2d3bba is a superseded SIBLING of the MACE line; it is NOT a third contributing source. The
two-line model is COMPLETE with respect to 8d2d3bba.** VERIFIED:

- `7683f59b` **is an ancestor of** 8d2d3bba (8d2d3bba = 7683f59b + 2 commits). 8d2d3bba is **not** an
  ancestor of ce5b748c. `merge-base(8d2d3bba, ce5b748c) = 7683f59b`. So both descend from the
  broker-failsafe fork 7683f59b; 8d2d3bba went one way (kept building the failsafe branch),
  ce5b748c the other (GDX). 7683f59b itself IS in the ce5b748c chain, so the broker-failsafe is
  already inside the MACE line and the merge.
- 8d2d3bba's 2 extra commits (eb77e633, 8d2d3bba) add **only** `_platform_deploy/` tooling +
  `reports/platform/*` — **zero `trading_corp/` runtime files.**
- For the runtime files where 8d2d3bba differs from ce5b748c (mace/config, mace/execution=46464fa3,
  mace/manager, mace.yaml — all at base/failsafe versions), those versions are **NOT on the box**
  (box == ce5b748c). So 8d2d3bba carries **no box runtime content** that is absent from ce5b748c.

Note: 8d2d3bba's `reports/platform/BROKER_FAILSAFE_DEPLOY_RECORD` is the deploy evidence for MACE
deploy #1 (used in Q1). Pushing the `platform-broker-failsafe-boxbase-2026-09-11 @ 8d2d3bba` branch
(optional, STEP ZERO) preserves that record + the failsafe tooling as provenance.

---

## 3. Q3 — THE SHARED TRIO (main.py / agents/data_exec.py / brokers/robinhood.py) — hunk analysis

**All three are changed ONLY on the MACE side; the PM side adds nothing. Clean take-MACE, zero
overlap, zero conflict. Resolved content == box.** VERIFIED:

| file | prod-live has | PM line adds | MACE line adds | resolved (= box) |
|---|---|---|---|---|
| main.py | 5908b5dc (== base) | **nothing** (base..PM diff EMPTY) | +7 lines, from 7683f59b | cbf4b928 |
| agents/data_exec.py | ffeca7f7 (== base) | **nothing** (EMPTY) | +149 / -6, from 7683f59b | fc8ab253 |
| brokers/robinhood.py | ecf5457e (== base) | **nothing** (EMPTY) | +31 lines, from 7683f59b | a26b8d0c |

- `git diff 6a5ed04 d19c0ab5 -- <trio>` = EMPTY (PM changed nothing).
- `git diff 6a5ed04 b1c552b1 -- <trio>` = EMPTY (prod-live trio == base trio).
- All trio changes were introduced by the single commit **7683f59b** (broker-failsafe); the GDX
  commits never touched the trio.
- Because one side (PM) is unchanged from base, the three-way merge is trivial: take the MACE side.
  The `~28h armed-but-not-trading` failure mode (a MACE branch clobbering PM's main.py additions)
  **cannot occur here** — PM has no trio hunks to lose; taking MACE's main.py drops nothing from PM.
- Independent confirmation: `git merge --squash ce5b748c` reported **"Automatic merge went well"**
  with **zero unmerged files**. The resolved trio blobs == box (cbf4b928 / fc8ab253 / a26b8d0c).

**db.py note (migration-021 preservation):** db.py also diverges (prod-live/PM = 3b5ae50d
[migration-021]; MACE/base = 39b5ac8e [pre-021], because MACE forked at 6a5ed04 before 021 landed).
But db.py is **not** in the MACE overlay set (MACE never modified it), so the merge correctly keeps
the PM/prod-live 3b5ae50d. A naive wholesale overlay that included db.py would have reverted schema
head to 20 and orphaned the leg_audit tables. The disjoint-file merge avoids that. Candidate db.py
== box db.py == 3b5ae50d. VERIFIED.

---

## 4. Q4 — THE CANDIDATE, BUILT AND PROVEN

Built in an isolated temp worktree (`cc-reconcile-boxtruth-wt`) via `git merge --squash ce5b748c`
onto d19c0ab5, committed single-parent (linear ledger).

**PRIMARY candidate: `2924e1c0`** (branch `reconcile-pm-mace-boxtruth-2026-09-12`, parent d19c0ab5).
- vs prod-live b1c552b1: exactly **29 files** = 6 PM-delta (3 runtime + UFC report + 2 tests) union
  23 MACE-delta (7 runtime + 2 reports + 2 tests + 12 `_gdxcap_deploy/`). Disjoint; nothing else.
- FF onto prod-live: **YES** (b1c552b1 is an ancestor of 2924e1c0). VERIFIED.
- **All 11 divergent runtime files: candidate CR-sha8 == box CR-sha8**, byte-for-byte. VERIFIED.

**Both-directions proof (full-tree walk of the 1497 tracked files vs the box overlay
`/home/azureuser/trading_corp/`, CR-stripped both sides):**
- FORWARD (tracked -> box): **MATCH=519, DIFFER=3, ABSENT=975, NOREAD=0.** The 519 matches include
  every deployed engine-overlay runtime file the candidate carries. The 3 DIFFERs are analysed in Q5.
- REVERSE (box overlay -> tracked): **~987 box-extra files, 0 unexplained runtime code.** Every
  box-extra is a documented exclusion class (runtime state / DB / log / backup / `.bak_*` / `.pre-*`
  / rollback). The genuine residue: `data/trading_corp.sqlite`, `data/trading_corp.pid`,
  `data/pm_search_ui_run{2..5}.log`, `data/prediction_markets.db.lossy_bak`,
  `config/strategies.yaml.block_bs` — all runtime state / backups. **No hidden division, no
  box-only source file.** VERIFIED.

The candidate reproduces the box for every engine-overlay runtime file **except one**:
`trading_corp/prediction_markets/subdivision.py` (see Q5 — the third-line finding).

**COMPLETE-BOX variant: `93b5e908`** (branch `reconcile-pm-mace-boxtruth-plus-subdiv-2026-09-12`,
parent 2924e1c0) — the primary plus subdivision.py set to box truth (f11d755e). vs prod-live = 30
files. FF onto prod-live: **YES**. subdivision.py in the variant CR-sha8 = **f11d755e = box**.
VERIFIED. This variant reproduces the box for **all** engine-overlay runtime files.

---

## 5. Q5 — FULL-TREE WALK: every box-vs-git difference + its reason

### 5a. ★ THE FINDING — subdivision.py is a THIRD divergence source (box AHEAD, un-reconciled)
`trading_corp/prediction_markets/subdivision.py`: **box = f11d755e**, prod-live / d19c0ab5 /
ce5b748c / 6a5ed04 all = **752e244a**. The divergence report's "zero graft drift for EVERY
code+config file" was an overstatement — it held only for the files in its table; subdivision.py
was never checked. VERIFIED:

- box f11d755e = full sha256 of commit **57c9c2cf** ("readers + assembler + scoping (build steps
  1-2)", 2026-09-10) on `pm-tiles-redesign-2026-09-10` (already on origin) — not an sha8 collision.
- 57c9c2cf is **NOT** an ancestor of prod-live. prod-live's 752e244a was set by 86204ef8
  ("reconcile pm 1d: advance prod-live to box ... == box", 2026-09-07). The box was later moved to
  the tiles-redesign version (deployed box-only, Deploy-7) and **never folded to prod-live**. Box is
  AHEAD; prod-live is behind on this file.
- The change is **+92 insertions, 0 deletions** (purely additive reader/assembler code) and stable
  across the entire tiles line (57c9c2cf .. 59be5c9e all f11d755e). The farm-livewhale/Deploy-8 line
  that became prod-live b1c552b1 kept the old 752e244a. It is the **only** engine-overlay runtime
  file that drifts (the forward walk found no other), so within the engine overlay it is
  self-contained; the box runs it live and healthy.

**This is the "content on the box in NEITHER line" case the handoff flagged. It does not change the
PM/MACE fold (orthogonal), but it means the PRIMARY candidate does not fully reconcile prod-live to
box truth.** DECISION FOR JACK:
- **Recommended: use the COMPLETE-BOX variant `93b5e908`** (folds subdivision.py=f11d755e). It closes
  the gap this whole exercise exists to close, is a clean +92-line additive file, is live box truth,
  and its source (57c9c2cf) is already on origin. Requires ratifying a third-workstream file.
- Alternative: push the PRIMARY `2924e1c0` (scope-pure two-line fold) and file subdivision.py as a
  SEPARATE follow-up reconcile. Downside: prod-live still diverges from the box on a live PM runtime
  file — the exact kind of silent gap that caused past incidents.

### 5b. The other 2 DIFFERs — non-runtime, benign, pre-existing
Both box versions == commit 2fe3805b ("prod-truth snapshots ... verbatim from running prod
2026-07-31"); git edited them after that snapshot. Box is behind on both. VERIFIED.
- `BACKLOG.md` (box 05ecd45a / git dc9de2cf) — documentation. Not runtime.
- `tests/test_pmcc_logic.py` (box a313f314 / git 7dd88450) — a test. Not runtime.
Neither affects the reconcile; out of scope (pre-existing since July). Report-only.

### 5c. PROD-LIVE-AHEAD (in git, absent from box) — a real category, not resurrected by this fold
- **ABSENT-CORE package modules** already present in prod-live: `agents/strategies/_ta_helpers.py`,
  `agents/strategies/bitunix_confluence_gate.py`, `data/whale_screening.py`,
  `trading_corp/scripts/kalshi_demo_{smoke,validate}.py`, `trading_corp/scripts/pmcc_paper_run_readiness.py`
  — retired/never-deployed modules. My fold does NOT add or resurrect them; they were already in
  prod-live and stay. VERIFIED.
- **infra/systemd/*.service|*.timer (9 files)** — deploy to `/etc/systemd/system/` per the exclusions
  manifest (non-overlay), not to the overlay `infra/`. Their absence from the overlay is expected;
  my forward check mapped them to the overlay (a check-side mapping gap, not drift). UNVERIFIED that
  each is installed in /etc (not required for this git reconcile; note-only).
- **~840 ABSENT laptop-only files** (cc/, tmp/, deploy/*/staged/, docs/, planning/, research/,
  reports/*, scripts/* dev/backtest tooling) — never deployed; prod-live carries them, box does not.
  Expected. Summarized, not enumerated.

### 5d. Exclusions applied (per RECONCILIATION_EXCLUSIONS.md)
Overlay root `/home/azureuser/trading_corp/`. Non-overlay maps: pead_earnings/*.py ->
/home/azureuser/pead_earnings/, card_assets/*.py -> /home/azureuser/card_assets/, *.service|*.timer
-> /etc/systemd/system/. Auto-excluded on the box: runtime-state yaml/cursors, DBs + WAL/SHM, logs,
`.bak_*`/`*_backup_*`/rollback/`.pre-*` artifacts, __pycache__/venv/.pytest_cache. Note: the box's
`data/` dir is **azureuser-owned** (not root:root) — the handoff's root:root warning did not apply;
only `backups/` is root:root (2 files, excluded).

---

## 6. MIGRATION-NUMBER CHECK

**Next free is 022; no number claimed twice; the fold introduces no migration.** VERIFIED:
- PM migrations live in `trading_corp/prediction_markets/db.py` as `(version, MIGRATION_0NN)` tuples
  ending at **(21, MIGRATION_021)** = schema HEAD 21, with a tested contiguity invariant
  (`test_schema_head_tracks_migrations: [v...] == range(1, HEAD+1)`) -> next free 022.
- **Neither line touched any migration** (`b1c552b1..d19c0ab5` and `6a5ed04..ce5b748c` diffs have 0
  migration files; db.py not in either delta). So no new number is claimed and no collision is
  possible across prod-live / d19c0ab5 / ce5b748c / candidate.
- Candidate db.py == box db.py (3b5ae50d, head 21). The reconcile is git-only and does not run any
  migration.

---

## 7. PRESERVE REQUIREMENTS — satisfied BY CONSTRUCTION (git-only reconcile)

The reconcile is a local commit + a FF push of a git ref. It does not deploy, restart, or write the
DB, so it cannot change what the box runs.
- **MACE config_hash bfde856f** — VERIFIED: box config/mace.yaml = bfde856f = candidate.
- **PM schema head 21** — VERIFIED (code): candidate db.py = box db.py at head 21; DB untouched.
- **30 armed sub-divisions armed + live** — preserved by construction (no box/DB/arm touch;
  arm-relevant runtime files verified unchanged since the report). A live arm count is Jack's
  authenticated read, not a git-reconcile step; not re-counted here (UNVERIFIED by direct query,
  preserved by construction).

---

## 8. LOCAL COMMITS

| branch | SHA | shape |
|---|---|---|
| reconcile-pm-mace-boxtruth-2026-09-12 | **2924e1c0** | PRIMARY: two-line linear ledger fold (29 files) |
| reconcile-pm-mace-boxtruth-plus-subdiv-2026-09-12 | **93b5e908** | COMPLETE-BOX: primary + subdivision.py (30 files) |

Both single-parent (linear), both FF onto prod-live. This plan doc is committed on top of the
PRIMARY branch.

---

## 9. PUSH COMMANDS FOR JACK (nothing run here; run from the cc repo root)

Re-fetch first (tips were stable at b1c552b1 / 61de372 this session):
```
git fetch origin
```

### STEP ZERO — put the local-only box-truth branches on origin (do this FIRST)
Advances no deployed ref; creates new branches; fully reversible
(`git push origin --delete <branch>`). NOT force pushes.
```
git push origin pm-ufc-method-types-2026-09-12
git push origin mace-gdx-cap-boxbase-2026-09-11
```
Optional (provenance for MACE deploy #1 + the failsafe deploy record):
```
git push origin platform-broker-failsafe-boxbase-2026-09-11
```

### THE RECONCILE — advance prod-live (a FAST-FORWARD; never --force)
Choose ONE. Recommended = complete-box variant.
```
git push origin 93b5e908:refs/heads/prod-live
```
Scope-pure alternative (two-line fold only; leaves the subdivision.py gap):
```
git push origin 2924e1c0:refs/heads/prod-live
```
Then push the reconcile branch itself for the record (carries this plan doc):
```
git push origin reconcile-pm-mace-boxtruth-plus-subdiv-2026-09-12
```

Rollback, if ever needed, is a FORWARD revert pushed fast-forward — **never a force-push**
(ruleset 22485002 refuses non-fast-forward / deletion with GH013). `main` is left untouched;
PM->main remains a separate whole-subtree merge, out of scope.

---

## 10. EVIDENCE INDEX (VERIFIED unless noted)

- Tips, box-unchanged, provenance table, disjoint-file proof — VERIFIED (git + box RO hash runs).
- Q1 two-deploy-events, c2593e34==ce5b748c, 0-merge convention — VERIFIED (deploy records + git).
- Q2 8d2d3bba sibling, no box runtime — VERIFIED (git ancestry + box hashes).
- Q3 empty PM trio diff, clean squash — VERIFIED (git diff + merge --squash).
- Q4 candidate == box (11 runtime + full-tree both directions) — VERIFIED (box RO walk).
- Q5 subdivision.py third-line, 2 benign DIFFERs, prod-live-ahead, reverse-clean — VERIFIED.
- Migration next-free 022, no collision — VERIFIED (git + db.py invariant).
- config_hash bfde856f, schema head 21 — VERIFIED; 30-armed — preserved by construction (UNVERIFIED
  by direct arm count; not a git-reconcile step).
- infra/systemd units installed in /etc — UNVERIFIED (note-only; not required).

## 11. RE-RUN / RUNNERS (read-only, local; box read via ssh-as-azureuser STDIN pipe)
`cc/pm_divergence_boxhash_ro.ps1` (box 11-file hashes); `cc/recon_probe_ro.ps1` (box tree shape);
`cc/recon_fwd_ro.ps1` (1497 tracked vs box, self-classified); `cc/recon_rev_ro.ps1` (box overlay
list). Local manifests under `cc/_recon_scratch/`. Nothing was mutated to produce this.
