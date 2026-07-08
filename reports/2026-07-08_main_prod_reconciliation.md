# main -> prod reconciliation (2026-07-08)

## Invariant restored (and its honest scope)

**`main == prod`, md5-per-file, is restored as of 2026-07-08** for every deployed
executable file the 2026-07-02 -> 2026-07-08 deploys touched. `origin/main`, local
`main`, and prod (PID 108070) are byte-identical for all **17** reconciled files.

**Revised scope statement (the honest meaning of "main == prod"):** main == prod
restored 2026-07-08 for deployed executable content; **6 CRLF-cosmetic files + 1
docstring-lag file + 7 dev-only files are documented exclusions** (listed below).
"main == prod" now means: for every deployed file, executable content matches
byte-for-byte; documented cosmetic exceptions are explicitly enumerated here.

Reconcile branch `main-prod-reconcile-2026-07-08` (3 commits) merged `--no-ff` to
main as `be1d809`; pushed to origin.

## Background

Prod diverged from main starting with the 2026-07-02 SFP bidirectional deploy: a
concurrent Kalshi K5 deploy had added Kalshi content (CRLF) to `main.py` +
`strategies.yaml` on prod that the branch base lacked, so every subsequent deploy
shipped as **targeted hunks** onto prod's living files, never a wholesale
main->prod copy. Over 4+ deploys, main fell behind prod on 17 files. This
reconciliation blesses prod's bytes as truth and records them on main, per deploy,
with a clean audit trail. (origin/main `1d2a714` already carried Kalshi K5, the
bracket merge `a866377`, and the polymarket reconcile `e70af23`, so no surprise
content was introduced.)

## Approach: last-touch per-deploy commits

Each drifted file was committed **exactly once, at its last-touching deploy**, with
prod's current bytes. This is **forced, not a preference**: the parity rule
"committed blob == prod after every commit" forbids committing an intermediate
version prod never had, so a file touched by N deploys can only carry prod's final
byte-state, which belongs to its last-touch deploy. Each commit body lists every
contributing deploy for its files.

- **C1 `b9de6c5`** reconcile main to prod: SFP bidirectional (2026-07-02)
  - `config/strategies.yaml`, `trading_corp/main.py` (CRLF), `bitunix_sfp_research_log.py` (new module)
- **C2 `7b642f8`** reconcile main to prod: dashboard reorg + SFP cockpit + A2 (2026-07-07/08)
  - 9 dashboard web files; `stage1_monitoring.html` DELETED; `_state_board.html` (07-02 SFP price-labels + 07-07 dashboard); `bitunix_sfp_observer.py` (07-02 SFP + 07-07 A2); `sfp_cockpit_view.py` (07-02 SFP + 07-07 A2)
- **C3 `829ebdf`** reconcile main to prod: futures breakeven ref-vs-fill (2026-07-08)
  - `bitunix_position_reconciler.py` (07-02 SFP-exit + 07-02 SL-trail + 07-08 BE)

Merge to main: `be1d809` (`--no-ff`), pushed origin.

## Parity table (origin/main == local main == prod, md5)

| File | md5 |
|------|-----|
| `config/strategies.yaml` | `6016daea` |
| `trading_corp/main.py` | `d0d382cb` (CRLF preserved) |
| `trading_corp/agents/divisions/bitunix_position_reconciler.py` | `0cc06ab0` |
| `trading_corp/agents/divisions/bitunix_sfp_observer.py` | `28a8a4ec` |
| `trading_corp/agents/divisions/bitunix_sfp_research_log.py` | `b6b1b446` (new) |
| `trading_corp/web/sfp_cockpit_view.py` | `b50c65b7` |
| `trading_corp/web/templates/sfp_cockpit/_state_board.html` | `4c3038b4` |
| `trading_corp/web/data.py` | `6eeda43b` |
| `trading_corp/web/routes.py` | `ff0ed6d3` |
| `trading_corp/web/static/js/trade_flow_state.js` | `dcddf1b8` |
| `trading_corp/web/static/sw.js` | `04ed09c3` |
| `trading_corp/web/templates/division.html` | `9dd6cc2a` |
| `trading_corp/web/templates/home.html` | `23425212` |
| `trading_corp/web/templates/iron_condor_live.html` | `bd0fc5ec` |
| `trading_corp/web/templates/partials/stat_cards.html` | `b7da3e09` |
| `trading_corp/web/templates/partials/trade_flow.html` | `b28219c7` |
| `trading_corp/web/templates/partials/stage1_monitoring.html` | DELETED (absent on main == absent on prod) |

**17/17 parity.** `bitunix_bracket.py` was already at parity (`7794622f`, merged
via `a866377`) and was not re-reconciled.

## Documented exclusions (NOT reconciled, by design)

### 6 CRLF-only cosmetic files (content-identical; prod CRLF vs main LF)
`trading_corp/brokers/kalshi.py`, `trading_corp/brokers/kalshi_live.py`,
`trading_corp/agents/strategies/kalshi_copy_trader.py`,
`trading_corp/web/templates/partials/pm_dashboard_body.html`,
`trading_corp/agents/divisions/_observer_test.py`, `trading_corp/utils/secrets.py`.

Byte-differ only by line-endings (prod stored CRLF from prior Kalshi K5 /
polymarket / dashboard deploys); executable content is identical to main.
Committing prod's CRLF bytes would pollute main with mixed line endings for zero
content reason. The clean fix is prod-side LF normalization (a prod write, out of
this read-only session's scope). Left as cosmetic.

### 1 docstring-lag file (code-identical; main leads prod)
`trading_corp/agents/kalshi_resolver.py`: main and prod code are byte-identical;
the only difference is an 11-line **docstring** where **main is the accurate
version** (K5 leg_date-fallback description, commit `d1f5ea6`) and prod's is stale
(pre-fix wording -- the code was updated but the docstring lagged the deploy). Not
reconciled -- reconciling would *downgrade* main's docstring to prod's stale
version. main is the truth here; bless-prod-as-truth does not apply.

### 7 dev-only files (main-has / prod-lacks; deployment-subset boundary)
`_ta_helpers.py`, `bitunix_confluence_gate.py`, `pead_backtest.py`,
`pead_backtest_driver.py`, `whale_screening.py`, `kalshi_demo_smoke.py`,
`kalshi_demo_validate.py`.

Dev/test/backtest/demo helpers never deployed to prod. Prod is a deployment
subset; these correctly live in main and are correctly absent on prod. Not
drift-from-a-deploy; no action.

## Methodology

- **Completeness sweep first.** Byte-exact md5 of all 284 main-tracked files under
  `trading_corp/` + `config/` vs prod (782 prod files, noise-filtered). Surfaced
  the exact drift set (the 17 + `kalshi_resolver` + the 6 CRLF-only, beyond the
  named deploys) BEFORE any writes.
- **Autocrlf trap avoided.** Repo has `core.autocrlf=true`, no `.gitattributes`.
  Naive `git archive`/checkout CRLF-ifies the whole tree (produced a false
  251-file "drift" on the first pass). All main-side md5s use `git show` /
  `git -c core.autocrlf=false` (raw blob bytes), validated against known-good md5s
  (`bitunix_bracket.py` 7794622f). The reconcile worktree was created with autocrlf
  disabled at checkout, so `git status` showed ONLY the 17 intended files.
- **Byte-exact transfer.** Prod files pulled via `tar`-over-ssh (no line-ending
  translation), md5-verified == prod after transfer.
- **Byte-exact staging.** `git -c core.autocrlf=false add` preserves prod bytes --
  critical for `main.py`, which is fully CRLF on prod (`d0d382cb`).
- **Triple verification.** md5 checked (1) after tar transfer, (2) after each
  commit (blob vs prod), (3) post-merge (main HEAD blob vs a FRESH prod fetch).
  0 mismatches at every gate.
- **Read-only SSH throughout** (cat / md5sum / tar-read). No prod writes, no
  restarts, no deploys.

## SHA / branch references
- Reconcile branch: `main-prod-reconcile-2026-07-08` -- C1 `b9de6c5`, C2 `7b642f8`, C3 `829ebdf`
- Merge to main: `be1d809` (`--no-ff`), pushed to `origin/main`
- Base: `origin/main 1d2a714`
- Prod: PID 108070 (up 2026-07-08 02:10:35 UTC)

## Deploys retroactively recorded by this reconciliation
- **2026-07-02 SFP bidirectional** (branch `sfp-bidirectional-deploy-2026-07-01`) -- was memory-only, no prior deploy_log entry
- **2026-07-02 SL-trail fix** (branch `futures-sltrail-diag-2026-07-02`) -- was memory-only, no prior deploy_log entry
- **2026-07-06 bitunix bracket min-leg** (branch `bitunix-bracket-minleg-fullprofit-2026-07-06`, merged `a866377`) -- already at parity, noted for completeness
- **2026-07-07/08 dashboard reorg + SFP cockpit + A2** (branch `dashboard-tile-reorg-2026-07-07`)
- **2026-07-08 futures breakeven ref-vs-fill** (branch `futures-be-ref-vs-fill-2026-07-08`)
