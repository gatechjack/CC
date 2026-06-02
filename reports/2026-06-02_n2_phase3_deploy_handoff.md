# N+2 Phase 3 deploy — next-session handoff prompt

**Date this prompt was written:** 2026-06-02 00:25 UTC. **Predecessors:**
- `reports/2026-06-01_n2_phase3_session_a_complete.md` (Session A scope; MERGED via `36157bf`)
- `reports/2026-06-01_n2_phase3_session_b_complete.md` (Session B scope; MERGED via `920a33a`)
- `runbooks/deploy_log.md` 2026-06-01 ~18:50 UTC + ~21:10 UTC entries (both Session A + Session B merges)

**Phase 3 implementation status:** **fully MERGED on `origin/main`** (HEAD `1bdad2d`); **NOT DEPLOYED**. Prod at `7352f8f` per redeploy3 (paper-mode). The next session is a **deploy session** to land Phase 3 + Decision 6.2 + Layer 1 fee plumbing on prod.

Use this prompt as the paste-ready brief when ready to execute the deploy. Adjust dates as needed.

---

```
N+2 Phase 3 deploy — Stage-1 BitUnix live exit path (Session A + B MERGED on main; prod still at 7352f8f redeploy3 paper-mode). Operator-supervised; deploy-mechanism only (no source changes).

Read first (in order):
- runbooks/deploy_log.md — 2026-06-01 ~18:50 UTC + ~21:10 UTC entries (Session A + B merges); 2026-05-31 05:36 UTC entry (redeploy3 — current prod state); preamble for md5-diff verification.
- runbooks/bitunix_panic_halt.md + runbooks/bitunix_credential_compromise.md — re-read for current code-surface SHA on the # Last verified header.
- reports/2026-06-01_n2_phase3_session_a_complete.md + reports/2026-06-01_n2_phase3_session_b_complete.md — full activation ledger; what's REACHABLE vs INERT on prod paper-mode.
- CLAUDE.md §STOP AND READ + §Session discipline + §Environment + §Testing discipline + §Process + safety + the "Before any deploy-adjacent task" mitigation block.

Pre-deploy state verification:
- git rev-parse origin/main — quote SHA. Should be 1bdad2d or whatever's current. NOTE: operator may land more close-out + investigation commits between sessions; verify HEAD before proceeding.
- ssh azureuser@trading.jacksumner.com 'systemctl show trading-corp --property=MainPID,ActiveState,SubState,NRestarts,ExecMainStartTimestamp; curl -sS -o /dev/null -w "healthz=%{http_code}\n" https://trading.jacksumner.com/healthz'
  Expected: MainPID stable, NRestarts single digits, ActiveState=active, healthz=200. ExecMainStartTimestamp should be after 2026-05-31 05:36 UTC (redeploy3) and unchanged across the days since.
- Pre-deploy file-level sweep: `python scripts/bitunix_prod_surface_md5diff.py` (or whatever the canonical surface sweep tool is in scripts/). Quote the manifest output. If the sweep reports DIFFER files outside the Session A+B transfer set, surface for diagnosis BEFORE transfer.

Pre-deploy gates (per [[file-level-prod-vs-main-sweep-as-standing-discipline]] + [[pre-deploy-filesystem-audit-discipline]]):
G1. Test gate on a fresh worktree off origin/main: expect 28/3 (canonical fresh-worktree baseline per the 2026-06-01 ~21:10 UTC deploy_log entry + P3 BACKLOG refile). STOP-and-report on any deviation.
G2. Prod stability: per the SSH probe above.
G3. md5-diff sweep + transfer-set composition: union of `git diff <prod-pointer>..origin/main` and the sweep's DIFFER-STALE/MISSING findings. Per [[deploy-transfer-set-diff-derived-misses-stale-prod-files]] do NOT trust the diff alone.
G4. Backup tag on prod: `pre-stage1-phase3-deploy-YYYYMMDD-HHMM` for rollback.
G5. Operator authorization for the transfer: explicit, per CLAUDE.md §Process + safety + "no prod write without in-session authorization".

Phase 3 deploy scope (what lands on prod from main):
- Session A source changes (4 commits):
  * Path C revert (live entries write paper_trade_record); INERT on paper-mode
  * _record_exit_outcome canonical helper; DORMANT (caller is _execute_live_exits which is dormant)
  * _execute_live_exits + BitunixBroker.get_pending_positions; DORMANT
  * reconcile_position_state on bitunix_position_reconciler.py; DORMANT (gates short-circuit)
- Session B source changes (5 commits):
  * Layer 1 fee plumbing (FillEvent.fee + downstream stamps); ACTIVE with default 0.0
  * Decision 6.2 db-lock retry on insert_paper_trade_record; ACTIVE on every call (happy-path byte-identical)
  * main.py startup wires reconcile_position_state for live mode; DORMANT (gate)
  * paper_trade_replay wires _record_exit_outcome + _execute_live_exits; DORMANT (fork gates on row's execution_mode tag)
  * _resume_live_positions + 60s sanity poll + 8 alerts; DORMANT (gates + notifier=None)

Expected prod behavior post-deploy (paper-mode unchanged):
- config/strategies.yaml:1022 execution_mode: paper — keep as-is on prod. Phase 3 deploy does NOT flip this.
- No live orders placed. No new audit kinds emitted from the new live-mode paths (Path C / _execute_live_exits / reconcile_position_state / restart-resume / sanity poll).
- Decision 6.2 db-lock retry silent until contention (logs warning on retry; logs error on exhaustion).
- FillEvent.fee=0.0 on every paper-mode placement (no behavior change).

Post-deploy verification:
- systemctl status trading-corp — ActiveState=active; new MainPID; NRestarts back to 0; ExecMainStartTimestamp updated to deploy time.
- Tail journalctl since deploy moment for ~30 min — watch for unexpected errors. Specifically:
  * No tracebacks from main.py startup re Phase 3 imports (reconciler, resume_live_positions, notifier).
  * No tracebacks from paper_trade_replay's _replay_tick_async.
  * No "database is locked" log spam (would indicate Decision 6.2 retry was needed; should be silent normally).
- audit_event probe: no new kinds from the live-mode paths SHOULD appear on paper-mode.
- healthz=200.

Post-deploy close-out:
- runbooks/deploy_log.md entry under "## YYYY-MM-DD HH:MM UTC — Stage-1 Phase 3 deploy (paper-mode, source-only)" per the template. Include backup tag, MainPID before/after, full transfer-set list, audit-event probe results.
- Memory: [[2026-06-02-n2-phase3-deployed]] (or similar) recording the DEPLOYED state-class transition.
- BACKLOG: update Phase 3 entry from "MERGED on main; NOT DEPLOYED" to "DEPLOYED YYYY-MM-DD HH:MM UTC".

Hard stops:
- Pre-deploy test gate ≠ 28/3 → STOP, investigate.
- Pre-deploy prod stability deteriorated (NRestarts > single digits in last 24h, ActiveState ≠ active, healthz ≠ 200) → DEFER deploy.
- Pre-deploy md5-diff surfaces DIFFER files outside the expected transfer set → STOP, diagnose (per [[deploy-transfer-set-diff-derived-misses-stale-prod-files]] the "prod is at commit X" pointer doesn't reflect file-level state).
- Post-deploy crash loop / restart spiral → ROLLBACK via backup tag.
- ANY indication of live-mode execution (live_order_placed audit row, live_exit_order_placed audit row, or `_execute_live_exits` call site reached) → IMMEDIATE rollback. Paper-mode is the deploy invariant.

Out of scope (deploy session does NOT touch):
- execution_mode flip — STAYS paper.
- auto_execute flips on any division.
- tasty_options / kalshi / polymarket / robinhood divisions.
- Dashboard surface.
- CLAUDE.md / runbook content edits.
- Any source change beyond what's already MERGED on origin/main.

Discipline standards (CLAUDE.md):
- Read-only by default for prod; prod writes require explicit, in-session operator authorization.
- Stop-at-fork.
- Worktree isolation per session.
- Verify timestamps against system clock before committing deploy_log entries.
- Direct SSH preferred over az run-command per §Environment.

Output expected at session close:
- Phase 3 successfully DEPLOYED on prod with new MainPID + ActiveState=active + healthz=200.
- deploy_log entry quoting before/after MainPID + transfer-set + verification probes.
- Memory [[2026-06-02-n2-phase3-deployed]] (or similar dated entry).
- BACKLOG status: Phase 3 DEPLOYED.
- Confirmation: execution_mode stays paper; no auto_execute flip; no live placements expected.

Carry-forward investigations from this session (NOT blocking deploy):
- P3 `0b8419a` refile: `test_paper_run_tooling.py` readiness-check DB-fixture coupling — remediation pending future maintenance session per the file's documented options (a/b/c).
- P3 `39e2361` (operator-filed): Finding #5 analogous-cases audit — investigation deferred.
- Operator WIP in `cc/` working tree: CLAUDE.md edits (state-class verb discipline §9d/9e); h.sh probe (bitunix_futures 2026-06-02 audit). Not for this deploy session to commit.
- 8 BitunixLifecycleNotifier methods (Commit 5c of Session B) are PRESENT but have no production caller — deferred to a wiring session (N+3 or sooner per operator decision).
- Layer 2 funding accrual (Phase 1b §3) — N+3 scope.
- Restart-resume Case C full auto-resolve — N+3 scope (current implementation halts + pages; that's the Phase 3 design).
```

---

## Notes for the operator before invoking the deploy prompt

1. **Prod baseline reference:** prod is at `7352f8f` per redeploy3 (2026-05-31 05:36 UTC); last verified stable at Session B pre-merge probe (2026-06-01 ~21:10 UTC ± timestamp drift):
   `MainPID=1961197, NRestarts=0, ActiveState=active, SubState=running, healthz=200`.
   Re-probe at deploy session start to confirm continued stability.

2. **Transfer set composition** — Per `[[deploy-transfer-set-diff-derived-misses-stale-prod-files]]`, the canonical transfer set = UNION of:
   - `git diff 7352f8f..origin/main` (the in-git delta, which should be Session A's 4 source files + Session B's 8 source files + reports/* + BACKLOG.md + runbooks/deploy_log.md + CLAUDE.md if committed),
   - The file-level md5 sweep's DIFFER-STALE / MISSING findings (the safety net).
   Quote both lists, take the union, do NOT trust the diff alone.

3. **Decision 6.2 db-lock retry behavior on prod paper-mode** — should be silent (no retries triggered) on a healthy DB. If you see `database is locked on attempt N` log lines after deploy, surface as P-something: it means the new path is firing, which is functionally correct but indicates DB contention worth investigating.

4. **The 8 dormant BitunixLifecycleNotifier methods** — PRESENT on the singleton but no production caller. Deploying them is safe (no behavior change); they activate only when a future session wires them. Note in the deploy_log entry that they're shipped DORMANT.

5. **Recommended deploy timing** — paper-mode observation window has only been ~30 min since Session B merged (at ~21:10 UTC). Operator may prefer a longer pre-deploy observation window (e.g. ride main for a few hours to confirm no test infrastructure or import issues on a fresh clone before transferring to prod). Not required; flag for operator judgment.

6. **`scripts/bitunix_prod_surface_md5diff.py`** is the canonical pre-deploy sweep tool per `[[file-level-prod-vs-main-sweep-as-standing-discipline]]`. Quote its output as a deploy-log artifact.

7. **Backup tag pattern:** `pre-stage1-phase3-deploy-YYYYMMDD-HHMM` per [[stage1-redeploy3-landed-2026-05-31]]'s recipe. Apply on prod before transfer.

8. **Post-deploy memory entries** — when the deploy session lands, write `[[2026-06-XX-n2-phase3-deployed]]` (or similar) following the DEPLOYED state-class verb discipline per CLAUDE.md §Session discipline §9e. Phase 3 transitions from MERGED to DEPLOYED on this event.
