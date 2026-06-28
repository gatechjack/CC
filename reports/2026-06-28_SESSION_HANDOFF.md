# SESSION HANDOFF — Reconciliation (A) + SFP cockpit fixes (2026-06-28)

Pick-up doc for a fresh session. This session: **repo↔prod reconciliation (Phases 2 & 3) + two SFP
dashboard fixes deployed live.** No trading-logic changes; account never touched.

## 1. GIT STATE — `main` is now the source of truth (deploy-clean)
- **`main` @ `7283cc1`** == `origin/main` == **live prod runtime** (verified by md5). Future deploys are
  clean diffs off `main`. **Start the next session from `main`** (cut a fresh branch).
- **Working branch `prod-reconcile-2026-06-28`** (worktree `cc/.claude/worktrees/bitunix-sfp-2026-06-25`)
  is fully merged into `main` (0 ahead). It was the strategy-A candidate; its job is done.
- **`bitunix-sfp-division-2026-06-25` (`16f2985`)** — the SFP feature branch + sole home of all dropped
  undeployed work; **now pushed to origin** (was local-only). Protected; never prune.
- Key commits this session: `ed1f338` (Phase-2 reconciliation merge), `fa2c7dd` (test reconciliation),
  `6656edf` (cockpit nav+flicker), `7283cc1` (final merge → main). Reports under `reports/2026-06-28_*`.

## 2. WHAT SHIPPED THIS SESSION
1. **Reconciliation strategy A — `main` blessed == prod (Phase 2).** `main`-runtime byte-matches prod for
   every deployed file (206 files: 194 exact + 11 empty `__init__` + 1 `_observer_test.py` CRLF-only).
   Config snapshotted VERBATIM from prod. Test suite reconciled: **52F+2E → 28F/0E/2726P** (28 = documented
   pre-existing baseline; 24 dead-feature tests removed; 2 D1/D3 test-lag fixed; **0 regressions**).
   Report: `reports/2026-06-28_reconciliation_test_classification.md` + `_A_undeployed_inventory.md`.
2. **Phase 3 prune — Group A only (19 ephemeral branches), push-first.** Pushed SFP + 12 unpushed-unique
   branches to origin first (safety gate), then deleted 17/19 Group A (2 skipped: dirty+locked agent
   worktrees). Branches **143→126**, worktrees **86→75**. Group B (20, held for findability) + Group C
   (protected) untouched. Kill-list: `reports/2026-06-28_phase3_prune_candidate_killlist.md`.
3. **SFP cockpit fixes — DEPLOYED LIVE (hot, no restart).** (a) added back-to-Overview nav bar (cockpit is
   a standalone page, lacked it); (b) killed the every-5s whole-page flicker (root cause: `.panel`
   `animation:rise` replayed on each `outerHTML` poll) via idiomorph morph swaps + a REFRESH indicator
   chip. Commit `6656edf`; deploy pkg `deploy/2026-06-28_sfp_cockpit_nav_flicker/`.

## 3. CURRENT PROD STATE (verified 2026-06-28 ~15:25 UTC)
- **Engine:** systemd `trading-corp` MainPID **3730922**, active, NRestarts=0, ~12h uptime.
  ExecStart: `--live --brokers bitunix robinhood --live-divisions bitunix_sfp robinhood_pead`.
- **SFP:** `bitunix_sfp` LIVE + armed (`mode:trading`, `auto_execute:true`, BTC/USDT.P); **feed is LIVE**
  (15m BTC bar fresh) via the ws-primary/REST-fallback hybrid (Piece 3) on the NAT-gw egress IP
  **168.62.60.79** (Piece 2). Account FLAT (SFP has never traded; venue-side B1 stop + TP leg protect any
  position). **No first live SFP→BOS trade yet** — still the key pending validation.
- **Two-state:** `bitunix_futures` HALTED-INERT, replay DISABLED (Piece 1). `robinhood_pead` live.
- **Cockpit:** `/sfp` serves the new nav + smooth (no-flicker) panels + REFRESH chip. `prod==main` for all
  8 cockpit files. Backup on prod: `~/cockpit_bak_2026-06-28/cockpit_pre.tgz`.

## 4. OPEN ITEMS / NEXT STEPS (no blockers)
- **Phase 3 Group B + C — HELD.** Group B (20 deployed feature/fix branches, recoverable from origin) prune
  is the operator's call. Group C protected. The 2 skipped agent worktrees (`worktree-agent-*`, dirty=8
  uncommitted JS *deletions* only, no real work) can be force-removed on OK. Nothing else to prune blind.
- **First live SFP→BOS trade validation** — the headline pending item: confirm the 2-leg bracket
  (B1 stop + venue TP via `place_tpsl_order`) round-trips cleanly on the first real fill, and the cockpit's
  TIER-A/C panels populate from real data.
- **Cockpit TIER-B mocks → real reads.** `_mock_armed_watch` / `_mock_near_miss` / `_mock_bos_confirm`
  need the observer `sfp_watch_state` emit to become real (armed-watch overlay, near-miss list, BOS-confirm
  rate). Currently painted as dashed MOCK ribbons (honest).
- **Arm ETH/SOL/XRP later** = add to SFP `config.symbols` (captured-only today). Separate gated step.
- **deploy_log.md had drifted** (last pre-2026-06-28 entry was 2026-06-21; 06-22→27 deploys live only in
  memory). The 2026-06-28 entry restores it; consider back-filling 06-22→27 from memory if a clean log
  matters. Going forward: `main`==prod is the parity anchor.
- Backlog (older, still open): P1-A/B Bitunix TP-structure + silence-window backtest; fee/slippage levers;
  polymarket SELL-pairing; InfoSec audit items. See `BACKLOG.md` (open items only).

## 5. ENVIRONMENT SYNC
- local `main` == `origin/main` == `7283cc1`; `main`-runtime == live prod (deployed files). ✓
- SFP branch `16f2985` + the 12 previously-unpushed branches now on origin. ✓
- Untracked leftovers in the worktree (NOT committed, harmless): `_prodsnap/` (this session's analysis
  scratch + snapshots), `scripts/_expA2_run.log` / `_expB2_run.log` (run logs), `deploy/2026-06-27_sfp_cockpit/ethfix.sh`,
  `deploy/2026-06-27_sfp_tpfix/` (prior-session artifacts; tpfix was moot per memory). Leave or clean at will.

## 6. DEPLOY DISCIPLINE REMINDERS (unchanged)
- Targeted-hunk / drift-gate vs prod md5 before any deploy; prod is scp-deployed (no git).
- **CRLF gotcha:** `git archive` applies CRLF on Windows (autocrlf) — for prod deploys push LF blobs via
  `git show HEAD:<f> | tr -d '\r' | ssh "cat > <prod path>"` (NOT `git archive`). Verify md5 == HEAD LF blob.
- Operator has NO sudo password; NOPASSWD = systemctl/journalctl/sqlite3 only. Restarts must be flat-guarded.
- Never `git clean` / `git stash` (stash races across worktrees). Push-first before any branch delete.
