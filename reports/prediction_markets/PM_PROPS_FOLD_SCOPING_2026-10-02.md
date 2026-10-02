# PM-props box-ahead -> prod-live: fold or merge? — RO scoping pass 2026-10-02

Read-only. Nothing pushed, folded-for-real, or deployed. Cherry-pick was a dry-run in a throwaway
temp worktree (removed). Hashes CR-stripped. The fold is **git-only** (records what the box already
runs) — it cannot disturb PM trading, the arm state, the DB, or MACE's Monday acceptance.

## ★ VERDICT: A CLEAN FOLD (cherry-pick, proven). Not a merge, not worse.
Disjoint on the money path: prod-live has NOT touched the five files since the props fork; dry-run
cherry-pick onto prod-live is rc=0 / 0 conflicts and the result's five files are byte-identical to the
box. (Different risk CLASS from MACE — this is the live Kalshi order path — but same clean mechanics.)

## 1. Verified tips (VERIFIED: git fetch; rev-parse)
- `origin/prod-live` = **2ce0b5c2** (2026-10-02, the MACE fold landed today) · `origin/main` = **1d9bdf06** (2026-09-21)

## 2. Per-file delta (box vs prod-live 2ce0b5c2, CR-sha16) + what it DOES
| file | prod-live | box (==b4b2c089) | change |
|---|---|---|---|
| prediction_markets/live_driver.py | 1dc12cbc587e5f21 | 4bbe3023610aaadc | passes titles + prop/inning ctx to the matchers |
| prediction_markets/execution.py | f86e660c9ec7c493 | a8a03b59e422a8a2 | MarketContext.prop_index + inningwin_index |
| data/sports_structural_match.py | 5b6585e80585bfcb | b787d1f11b63af61 | NFL prop match wiring |
| data/mlb_poly_kalshi_match.py | decc3f39dae71025 | 83881fc73f6543dd | MLB prop + per-inning match wiring |
| data/player_props_match.py | ABSENT | e3f85ba977e0db3c | **NEW** — shared prop-match core (title full-name gate) |
WHAT IT DOES: NFL/MLB player props (Phase B/C) + MLB per-inning winners (Phase D) — threads
`prop_index`/`inningwin_index` into the matchers, gated PER-STAT behind `market_types` tokens. Additive;
the non-prop match path is unchanged. (Build detail: [[pm-props-perinning-build-2026-09-22]].)

## 3. Provenance (VERIFIED)
Deployed **2026-09-22 20:01:58Z** (5-file scp+tar graft + engine restart; session = the props build).
Git home = **local branch `pm-props-perinning-2026-09-22 @ b4b2c089`** (base 558fc143). **NOT on any
origin remote** (`git branch -r --contains b4b2c089` = empty) — it exists only on the box + this local
branch. b4b2c089's five files == the build-manifest TARGET and == the box, exactly.

## 4. Is it running? YES — loaded in the live engine (VERIFIED)
All five on the box, **mtime 2026-09-22T20:01:58Z < the current boot 2026-10-02 20:08:06Z** (PID 619011),
so they are in the running process (today's MACE restart reloaded them). Nothing grafted post-boot.

## 5. Self-consistent set? YES — complete, not partial (VERIFIED)
The props branch delta (558fc143..b4b2c089) is **exactly 10 files**: the 5 engine files + 5 new
`tests/prediction_markets/test_*_2026_09_22.py`. The 5 engine files are the box-ahead set (all ==
b4b2c089); the 5 tests are not deployed to the box (tests aren't). Box == b4b2c089 for all five -> a
complete capture, no dangling partial.

## 6. Cherry-pick dry-run (VERIFIED, temp worktree at 2ce0b5c2, removed)
`git cherry-pick 558fc143..b4b2c089` -> **rc=0, all 8 commits applied, 0 unmerged/conflict files.**
Why clean: prod-live never touched the 5 engine files since 558fc143 (its crsha16 == the 558fc143 base:
live_driver 1dc12cbc / execution f86e660c / sports 5b6585e8 / mlb decc3f39 / player_props ABSENT); the
5 tests are new (not on prod-live). Disjoint from prod-live's post-fork commits.

## 7. Box-equality (VERIFIED both sides, the proof obligation)
Dry-run result's five files == the running box, exactly:
player_props e3f85ba977e0db3c · sports b787d1f11b63af61 · mlb 83881fc73f6543dd · execution
a8a03b59e422a8a2 · live_driver 4bbe3023610aaadc.

## 8. §16.7 shared-file baseline — HOLDS (VERIFIED on box)
main.py `grep -c bitunix` = **196**, `grep -ci mace` = **119**, phantom `grep -c 'mace\|MACE'` = **116**
— exact. Box main.py crmd5 **b7cc5dd7c7dd** == prod-live 2ce0b5c2 main.py **b7cc5dd7c7dd**. None of the
five is a shared-trio file; the fold doesn't touch main.py/db.py/robinhood.py/app.py.

## 9. Migration state (VERIFIED; record had aged)
`schema_version` is in **prediction_markets.db** (NOT trading_corp.db as a 9/12 note said — suspect the
measurement; it moved/was misremembered), `MAX(version)` = **24**, 24 contiguous rows -> head 24, next
free **025**. The props branch delta contains **NO migration file** -> none of the five implies a
migration that never ran (props are enabled via `market_types` tokens in `agent_state`, not DDL).

## 10. Full-tree walk (VERIFIED: box crsha16 of 377 code/config files vs prod-live 2ce0b5c2's 381)
- **DRIFT = exactly the PM-props set** (4 modified above) + **BOX-ONLY** `player_props_match.py`. Nothing else.
- **MACE files are NO LONGER drift** (prod-live==box) -> confirms today's MACE fold landed.
- `config/Lets start Phase 1 - Plumbing now.txt` = box-only, **documented exclusion** (RECONCILIATION_EXCLUSIONS.md), not drift.
- prod-live-only = the 6 known never-deployed dev/backtest files (box-behind, expected).
- **No new straggler, no unknown division** this pass.

## ★ Exact ordered commands for Jack (UNEXECUTED — push is his, reserved)
From `C:\Users\AA Incorporado\cc`:
```
git fetch origin
git branch reconcile-pmprops-2026-10-02 origin/prod-live
git worktree add _pmprops_reconcile_wt reconcile-pmprops-2026-10-02
git -C _pmprops_reconcile_wt cherry-pick 558fc143..b4b2c089
```
Verify (CR-stripped) the worktree's five files == box (e3f85ba9/b787d1f1/83881fc7/a8a03b59/4bbe3023), then:
```
git -C _pmprops_reconcile_wt push origin reconcile-pmprops-2026-10-02:prod-live
git worktree remove _pmprops_reconcile_wt
```
FF-only (prod-live is an ancestor of the result). NEVER `--force` (ruleset 22485002 + would clobber live
work). Also recommended for provenance (the branch is local-only): `git push origin
pm-props-perinning-2026-09-22` as a named handle. Alternative (box-is-truth canonical, 0-merge linear
ledger, no test files): a single squashed ledger commit path-checking out the 5 engine files from
b4b2c089 onto prod-live.

## Live-system notes
- The fold is **git-only / behavior-neutral**: the box already runs this code (loaded in PID 619011);
  advancing prod-live records it and PREVENTS the real hazard — a future engine deploy based off prod-live
  (without props) silently reverting the props on the box (the exact "advance prod-live later" trap).
- Dormancy of the props families is RECORD-STATED ([[pm-props-perinning-build-2026-09-22]]: enabled on
  8 subs but the attached whales have never bet a prop/per-inning; 0 fills ever) and NOT independently
  re-verified this pass — it does not bear on the fold verdict (behavior-neutral). If the live firing
  behavior needs re-proving, that is a separate RO activity-based check (not a success-timestamp read).
- `main` carries a different/older MACE+PM base; bringing it current is separate pre-existing debt.

EVIDENCE: tips (rev-parse); delta (cat-file crsha16 both sides); provenance (branch -r --contains empty,
box mtime); loaded (mtime<boot); set (diff --name-only 558fc143..b4b2c089 = 10 files); dry-run (cherry-pick
rc=0 + diff-filter=U empty + result==box); §16.7 (box grep 196/119/116 + main.py crmd5 match); migrations
(prediction_markets.db schema_version MAX=24, no migration in the branch); full walk (box find+sha256 vs
prod-live blobs vs RECONCILIATION_EXCLUSIONS.md). Box READ-ONLY via ssh-STDIN; PM trading + 3 XLE rungs undisturbed.
