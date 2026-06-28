# Phase 3 — prune candidate KILL-LIST (for review; NOTHING deleted) — 2026-06-28

**Status: REVIEW ONLY. No branch, worktree, or ref has been deleted. No `git clean`/`git stash` used.**
Phase 2 is merged + pushed (`main` @ `ed1f338`, verified `main-runtime == prod`). This is the candidate
list you asked for before any deletion.

## Inventory
- **143 local branches**, **86 worktrees**, 114 remote branches.
- **79 branches are fully contained in `main`** (`ahead_main == 0` → every commit is an ancestor of `main`,
  so deleting the *ref* loses **zero commits**; content stays reachable from `main`'s history).
- **63 branches have unique commits NOT in `main`** (`ahead_main > 0` → each is the **sole home** of that
  work). Of these, **51 are on origin** (recoverable) and **12 are unpushed local-only** (deletion = permanent
  loss).

## Method / safety basis
A branch is a **zero-loss prune candidate** only if `ahead_main == 0` (its commits are reachable from `main`).
`ahead_main > 0` ⇒ sole home of unique work ⇒ **protected** (your rule: "nothing that's the sole home of
undeployed tooling or research gets pruned"). Under strategy A, *content-captured ≠ git-merged*: deployed
branches have their content in `main`'s tree but a research branch's report commit is usually unique → it
shows `ahead_main > 0` and is protected automatically.

## ⚠ DO THESE BEFORE ANY PRUNE (safety nets)
1. **`bitunix-sfp-division-2026-06-25` (16f2985) is LOCAL-ONLY (not on origin).** It is the documented
   re-integration home of all dropped undeployed work. **Push it to origin first** so the safety net itself is
   backed up.
2. **12 branches have unpushed unique commits** (see Group C4). **Push them all to origin** before pruning
   anything, so nothing is one `branch -D` away from permanent loss.
3. **2 branches are 1 commit ahead of their origin** (`bitunix-b1-first-fill-validation`,
   `bitunix-deploy-batch`) — push the missing commit before treating them as recoverable.
4. **Worktree-first:** a branch checked out in a worktree can't be deleted until the worktree is removed
   (`git worktree remove <path>`). Never `git clean`.

---

## GROUP A — SAFE TO PRUNE (ephemeral/operational, zero loss) — recommend prune
Transient operational branches; `ahead_main == 0`; nothing of research/feature value. (`[WT]` = remove its
worktree first.)

| branch | origin | note |
|---|---|---|
| `worktree-agent-a8e930abb23618fc2` [WT] | none | ephemeral agent worktree (locked) |
| `worktree-agent-ac7d026084ea1a3ff` [WT] | none | ephemeral agent worktree (locked) |
| `worktree-polycopy` [WT] | none | ephemeral |
| `worktree-stage1-blockers-items3-4-5-2026-05-30` [WT] | none | old stage-1 ops |
| `worktree-stage1-post-deploy-admin-closeout-2026-05-31` [WT] | none | old stage-1 ops |
| `worktree-phase3-prod-deploy-2026-06-01` [WT] | pushed | old deploy ops |
| `deploy-prep-2026-06-16` [WT] | pushed | deploy staging (content live in main) |
| `stage1-deploy-2026-05-30` [WT] | pushed | old deploy |
| `stage1-paper-dashboard-2026-05-31` [WT] | pushed | old dashboard staging |
| `stage1-forward-fix-2026-05-30` | pushed | old fix (in main) |
| `n2-phase3-scoping-2026-06-01` | pushed (WT) | scoping doc (superseded) |
| `session-wrap-2026-05-29-diagnostics-and-kalshi-disable` | pushed | session wrap |
| `c1-apify-cred-rotation` | pushed | cred-rotation op |
| `c1-bitunix-cred-rotation` | pushed | cred-rotation op |
| `c1-tastytrade-verify-2026-05-29` | pushed | verify op |
| `disable-kalshi-scanners-2026-05-29` | pushed | one-off config op |
| `bitunix-prod-surface-md5diff-2026-05-30` [WT] | pushed | one-off md5 audit |
| `bitunix-runbooks-gate-b-2026-05-30` [WT] | pushed | runbook (superseded) |
| `forensic-snapshot-2026-06-04` [WT] | none | one-off snapshot |

**19 branches.** All zero-loss.

## GROUP B — SAFE TO PRUNE, YOUR CALL (deployed feature/fix; content in `main` + on origin)
`ahead_main == 0` **and** pushed to origin → deleting the ref loses nothing (recoverable from `main` *and*
origin). These are named feature/fix branches whose work is **live on prod**. Prune for tidiness, or keep for
named findability — your preference.

`bitunix-10006-ratelimit-fix-2026-06-15` [WT], `bitunix-b1-entry-attached-stop-2026-06-10` [WT],
`bitunix-b2-maker-execution-2026-06-15` [WT], `bitunix-breaker-abstain-partial-equity-2026-06-15` [WT],
`bitunix-d1-drawdown-flatten-fix-2026-06-11` [WT], `bitunix-deblock-eventloop-2026-06-16` [WT],
`bitunix-hitl-removal-2026-06-13` [WT], `bitunix-noninteractive-live-auth-2026-06-13` [WT],
`bitunix-signed-fetch-autobook-2026-06-15` [WT], `bitunix-staleness-reject-gate-2026-06-16` [WT],
`bitunix-htf-vol-classifier-fix-2026-06-08`, `bitunix-live-engine-stage1-broker-write`,
`bitunix-live-entry-path-2026-05-29`, `bitunix-live-exit-path-2026-05-29-rebased` [WT],
`bitunix-live-exit-path-impl-2026-06-01` [WT], `bitunix-live-exit-path-impl-session-b-2026-06-01` [WT],
`bitunix-orderpath-safety-2026-05-29`, `bitunix-rest-resilience-2026-05-30`, `bitunix-risk-tier-pre-live`,
`kalshi-sports-arb-shelve-2026-06-14`.

**20 branches.** Recoverable from origin. (Excludes `b1-first-fill-validation` + `deploy-batch` → Group C4,
unpushed commit.)

---

## GROUP C — PROTECT (do NOT prune)

### C1 — Absolute
- `main` (the trunk), `prod-reconcile-2026-06-28` (just-merged candidate / current checkout),
  `bitunix-sfp-division-2026-06-25` (**16f2985** — re-integration home of all dropped undeployed work; **push
  to origin**).

### C2 — Inventory research/feature homes (named-protected by the reconciliation inventory)
- `bitunix-five-factor-recovery-2026-06-20` (five-factor `2659c81`).
- Polymarket **whale tooling** homes: `polymarket-option-c-phase1/2/3/4-2026-06-1x`, `analyze-whale-dashboard`,
  `pm-watchlist-clustering-fix`, `pm-watchlist-windowed-rescore` (origins `f448c93`/`41ca5b9`/`a6d8c30`).
- `robinhood-pead-2026-06-20` (PEAD earnings adapter + backtest `8307ade`/`c6d1a9f`/`759dc03`; also `ahead=39`).

### C3 — All 63 branches with unique commits (`ahead_main > 0`) — sole home of that work
Research / investigation / feature work not in `main`'s tree. **51 on origin** (recoverable) + **12 unpushed**
(C4). Includes (ahead count): `bitunix-d3-role` (47), `bitunix-redeem-sim` (43), `bitunix-metrics-epoch` (28),
`bitunix-d1-netted-close` (30), `bitunix-d4-concurrent-position-guard` (26), `bitunix-tpsl-rebuild` (23),
`bitunix-silence-investigation` (19), `bitunix-native-etl` (4), `bitunix-research` (3),
`bitunix-stop-slippage-analysis`, `bitunix-fee-model-reconciliation`/`fee-model-reconciliation`,
`htf-regime-timeframe-sweep`, `bull-signal-starvation-diagnostic`, `bull-bottleneck-scorer-pa`, `otter-*` (5),
`range-scalp`, `stop-distance-sensitivity`, `btc-scalping-db-native-data-scope`, `bitunix-*-review`/`*-eval`/
`*-deep-dive`/`*-audit` set, `robinhood-agentic-*`, `polymarket-*-investigation`/`*-scoping`/`*-design`,
`paper-trade-visualizer`, `pm-watchlist-pnl-aggregation-fix`, `backup-2026-05-19-2300`, the `stage1-*review*`/
`*redeploy*` with unique commits. (Full list: `_prodsnap/branch_analysis.tsv`, rows with `ahead_main > 0`.)

### C4 — Unpushed local-only unique commits (HIGHEST risk — permanent loss if deleted)
**Push to origin before any prune.** `bitunix-bracket-exit-rebased-2026-06-17` (ahead 8) [WT],
`worktree-TradeViewPS` (7) [WT], `c7-webhook-secret-scrub` (3), `worktree-phase3-day2-audit-2026-06-02` (2)
[WT], `tooling-run-capped-python-alias-fix-2026-06-21` (2, +1 unpushed) [WT — primary repo checkout],
`bitunix-bracket-exit-redesign-scope-2026-06-16` (1) [WT], `bitunix-entry-latency-investigation-2026-06-16`
(1) [WT], `bitunix-orphan-managed-exit-scope-2026-06-16` (1) [WT], `e5b-recon-2026-06-15` (1),
`exit-chase-draft-2026-06-17` (1), `polymarket-copy-paused-docs-2026-06-17` (1),
`polymarket-op-track-prep-2026-06-14` (1).

### C5 — Local-only homes of UNdeployed work (`ahead_main==0` but not on origin; undeployed) — keep
No remote backup + the work is not live on prod. `e5a-polymarket-broker-config-plumbing-2026-06-15`,
`e5b-exit-chase-2026-06-16`, `e2-2-fak-synth`, `e2-3-sizing-clamp`, `e2-4-live-divisions`,
`e2-5-execution-mode`, `e2-6-loop-wiring`, `e2-7-deps-lock-fix`, `e2-7-runbook-delivery-fix`,
`e1-lock-setuptools-pin`, `polymarket-e2-7-phase0-validation`, `polymarket-e2-1-tokenid` (pushed → could move
to B), `polymarket-gamma-5xx-retry`, `polymarket-live-prep-2026-05-29`, `pm-metrics-epoch`,
`bitunix-backlog-yfinance-removal-2026-06-14`, `kalshi-shelve-wrap-2026-06-14`, `wallet-ops-pol-swap` [WT],
`wallet-ops-toolchain`, `e2-scoping-2026-06-14`, `polymarket-e1-1..7` (whale/broker build).

---

## Worktrees (86)
Branch pruning ≠ worktree pruning. The Group A/B branches in worktrees would have their worktrees removed
*first* (`git worktree remove`). The clearest stale worktrees: the two `agent-*` (locked, ephemeral), and the
old `stage1-*`/`phase3-*` deploy worktrees. Protected branches keep their worktrees. A separate worktree-only
sweep can follow once the branch list is settled.

## Recommended order (only after you approve a specific subset)
1. Push 16f2985 + all C4 + the 2 local-ahead branches to origin (full backup).
2. `git worktree remove` the worktrees of the approved-to-prune branches.
3. `git branch -D` the approved branches (start with Group A; Group B only if you opt in).
4. Re-verify `main` unchanged + `main-runtime == prod` still holds.
**No `git clean`, no `git stash` at any step.**

## Proposed default for your sign-off
Prune **Group A (19)** now; **hold Group B (20)** unless you want maximal tidiness; **protect everything in
Group C**. Tell me which groups/branches to action and I'll do the backup-first sequence above.
