# Trading-Corp branch ↔ prod reconciliation — analysis + plan (2026-06-28)

Closes the tech-debt where prod's deployed code diverges from any canonical git state, so every deploy
must be a targeted-hunk. **Read-only analysis done; execution is operator-gated (it rewrites main + prunes
branches — hard to reverse).**

## The divergence (measured)
- `origin/main` `549a406` is an **ancestor** of the SFP branch `50e76d3` (branch = main + 52 commits → a
  merge fast-forwards). So main lacks everything the branch has AND everything prod-only.
- Prod's deployed code ≠ any single git commit. Of **206** prod engine files (`trading_corp/**.py` + `config/*.yaml`):
  - **167 MATCH** the branch HEAD (already in sync — incl. this session's two-state/ws/cockpit/ETH-fix).
  - **25 DIFFER** — prod content ahead of the branch.
  - **14 PROD-ONLY** — on prod, absent from the branch.
  - **5 BRANCH-ONLY** — dev/backtest tooling never deployed (correctly).
- Repo cruft: **142 local branches, 86 worktrees.**

### 25 DIFFER (prod ahead — the directly-deployed fixes)
- Bitunix engine: `bitunix_futures_observer.py`, `bitunix.py`, `bitunix_exceptions.py`,
  `bitunix_position_reconciler.py`, `agents/data_exec.py`, `persistence/db.py`, `persistence/models.py`,
  `agents/paper_trade_replay.py`, `data/bitunix_price_context.py` (= ref-vs-fill / D3 / P2 / /tpsl / D4).
- Other divisions/infra: `brokers/robinhood.py`, `agents/logger.py`, `data/earnings_provider.py`,
  `data/market_data_provider.py`, `utils/market_data.py`, polymarket whale (`polymarket_whale_audit*`,
  `_stats`, `refresh_polymarket_whales`, `seed_polymarket_watchlist_deep`), `web/data.py`, `web/routes.py`,
  `main.py`.
- **Config (operator-tuned on prod):** `config/strategies.yaml`, `config/divisions.yaml`, `config/risk.yaml`.

### 14 PROD-ONLY (deployed, not in this branch)
- PEAD division: `agents/divisions/robinhood_pead.py`, `agents/strategies/pead_strategy.py`,
  `agents/divisions/crypto_futures/` — deployed via the PEAD branch.
- 10 package `__init__.py` files (the branch tracks them elsewhere or they're untracked on the branch).
- `agents/divisions/_observer_test.py`.

### 5 BRANCH-ONLY (keep — dev tooling, never deployed)
`bitunix_confluence_gate.py`, `_ta_helpers.py`, `pead_backtest.py`, `pead_backtest_driver.py`,
`data/whale_screening.py`.

## Recommended strategy: BLESS PROD AS SOURCE-OF-TRUTH
Snapshot prod's deployed code → new canonical main. Prod is the live, coherent superset; the alternative
(cherry-pick 40+ unmerged branches in the right order) is error-prone and prod has hand-applied targeted
hunks that match no single commit.

## Plan
**Phase 1 — build the reconciliation branch (REVERSIBLE — new branch only):**
1. `git switch -c prod-reconcile-2026-06-28` off SFP HEAD (167/206 already match).
2. Fetch prod's content for the 25 DIFFER + 14 PROD-ONLY files → repo (LF preserved); keep the 5 BRANCH-ONLY.
3. Commit → branch == prod's deployed code (+ dev tooling). Verify: md5 every engine file == prod; full
   suite (expect ~baseline). This branch is the "main = prod" candidate, reviewable by `git diff`.

**Phase 2 — merge to main (operator-gated):** merge/FF `prod-reconcile` → `main`, push. main now == prod.
Future deploys become clean diffs vs main (no more targeted-hunk archaeology).

**Phase 3 — prune cruft (operator-gated, careful):** list the 142 branches / 86 worktrees, classify
merged/stale (work now in main) vs unmerged-with-value; remove only the stale ones (`git worktree remove`
+ `git branch -d`). Keep anything valuable. Clean one-off runners (`sfp_disarm.ps1` moot, `_prodsnap`).
**No deletion without an approved candidate list** (git-stash/git-clean are banned per project lessons).

## Decisions needed before execution
1. **Strategy** — confirm bless-prod-as-truth (vs rebuild-from-branches)?
2. **Config** — snapshot prod's live `strategies.yaml`/`divisions.yaml`/`risk.yaml` into the repo verbatim
   (they hold tuned values + `execution_mode:live`/`mode:` keys; NO secrets — those are separate), OR keep
   the repo config as a template and exclude those 3 from the snapshot?
3. **Pruning aggressiveness** — prune only clearly-merged/stale branches+worktrees, or also archive old
   research branches? (I'll produce a candidate list for approval before deleting anything.)

**Phase 1 is reversible and I can build it immediately on your word; Phases 2–3 wait for sign-off.**
