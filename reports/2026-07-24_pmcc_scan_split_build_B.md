# PMCC scan-split — Build B (local; NOT deployed)

Addendum build on top of Build A (`fd0f490`). Branch `pmcc-scan-split-2026-07-24`, developed in an
**isolated git worktree** (`cc-pmcc-wt`) after a shared-tree branch collision (see "Provenance").
**Nothing deployed, nothing restarted.** Its own Stage-2, separate from Build A.

Commits (on top of `fd0f490`): `147d5a7` (item-0 investigation) · `9f00ef7` (additive core) ·
`b59c86e` (scheduler wiring).

## The problem (from the healthcheck + investigation)

The only scheduled source of Approve cards was the **pre-open scan (8:30–9:25 ET)**, which does all
option-data work (strike selection, credit math, liquidity gate) off **last night's settlement marks**
(equity options are closed pre-market). That single timing flaw is the shared root of (a) the opening
`liquid=0` aborts and (b) the RKLB stale-card strike drift. Today's good post-open cards came from a
*manual* re-scan on live quotes — there was no scheduled post-open actionable pass.

## What was built

**Additive core (`9f00ef7`, `pmcc_robinhood.py`, fully unit-tested):**
- `triage(broker)` — Phase-A only: near-DTE + breach/assignment via `broker.quote()` spot (live pre-market,
  policy-compliant). NO option-chain reads, strike selection, credit/greek math, cards, or ABORTED alerts.
- `reference_quotes_live(broker)` — GLOBAL liveness probe: a broadly-liquid reference (SPY/QQQ) returns
  two-sided quotes with a sane spread. Per-name thin-ness stays the existing liquidity gate.
- `_format_triage_digest(report)` — calm two-register digest (routine near-DTE → reassuring + "cards
  after the open, no manual action needed"; breach/assignment → escalated). No per-name aborts.
- Config under `robinhood_pmcc.scan`: `triage_near_dte_days` (5), `liveness_ref_symbols` ([SPY,QQQ]),
  `liveness_max_spread_pct` (0.15).

**Scheduler wiring (`b59c86e`, `main.py`):**
- `_scheduled_pmcc_scan_loop` now (when `on_triage_callback` is wired):
  - **Pre-open window** (8:30–9:25 ET) fires **triage** (`_on_triage` → calm digest). No cards/aborts.
  - **Post-settle window** ([9:38, 10:30] ET) fires the **actionable** scan (`_on_scan` → cards off LIVE
    marks) **only once `reference_quotes_live` passes**. Past the **~9:50 backstop** if still not live it
    emits **one** calm *"actionable scan deferred: quotes not live, retrying next cadence"* notice and
    keeps retrying on the poll cadence. **Never force-scans on garbage, never hangs.**
- New `_settle_should_attempt` gate (weekday/window/dedup/holiday; mirrors `_scan_should_fire`), unit-tested.
- `_on_triage` + `_pmcc_liveness_probe` callbacks wired at the scheduler creation site.
- **Legacy path unchanged** (no triage callback → original pre-open actionable behaviour, no post-settle).
  **Terminal 0-DTE pass unchanged.** Windows/thresholds are scheduler params (configurable at the wiring
  site) — a follow-up can source them from `strategies.yaml` if desired.

## Reconciliation (addendum item 2)

- #3 opening-settle window was **never built** (dropped in Build A) → nothing to remove; no competing
  mechanism.
- Item-2 **consent guard stays** as defense-in-depth (it's in Build A, on `fd0f490`).
- **BULL-style persistent-low-volume names**: the post-settle pass runs on live quotes, so a genuinely
  illiquid name simply yields **no candidate** (the existing per-name liquidity gate), with **no abort
  spam** (item-4 calm wording + no pre-open aborts). No scary alerts on names it would roll fine.

## Tests

`test_pmcc_scan_split.py` — 11 tests: triage classification (breach/routine/far-DTE skip/uncovered
LEAP/spot-unavailable→routine), liveness probe (live / zero-bid / wide-spread), digest (two registers /
empty), and `_settle_should_attempt` (window/dedup/weekend). All green in isolation (204 with PMCC +
multi-leg). `main.py` compiles; gate functions import.

**Full-suite note:** same pre-existing `robin_stocks` test-isolation pollution as Build A (22
`robinhood_multi_leg` full-run failures, present at baseline). Additionally, in the **fresh worktree**
the `data/trading_corp.db` is an empty 4 KB file (no schema), so 4 DB-table **readiness** tests
(`test_pmcc_paper_run_readiness`, `test_paper_run_tooling`) fail with `no such table` — **these fail
identically at `fd0f490` (pre-Build-B) in this worktree**, i.e. environmental (missing DB), NOT a
Build-B regression. Apples-to-apples in-worktree diff (Build B vs `fd0f490`, identical environment): **51 failures each,
`comm` diff EMPTY → zero Build-B regressions**.

## Provenance (branch-collision rescue)

A concurrent PEAD agent shared the main working tree; its `git checkout pead-drift-anchor-fix` moved the
shared `HEAD`, so two Build-B commits initially landed on the PEAD branch. Recovered non-destructively:
anchored a rescue ref (`pmcc-buildB-rescue`), created an isolated `git worktree` on
`pmcc-scan-split-2026-07-24`, cherry-picked the two commits (verified: exactly 2, zero PEAD SHAs), and
did all further work in the worktree. The PEAD branch was left untouched.

## GO / NO-GO

**GO for the code** — correct, isolated, unit-tested; legacy + terminal paths unchanged; PMCC-scoped
(IC untouched); `auto_execute` unaffected. No Build-B regressions (in-worktree diff clean).

**Stage-2 (operator-authorized, SEPARATE from Build A) still required:**
1. **Boot-smoke first** — this changes the live scheduler loop. Before trusting it in prod, run the
   engine boot-smoke and confirm: scheduler comes up, a pre-open triage fires a digest (no cards), and a
   post-settle pass fires only after the liveness probe clears (or defers calmly). The scheduler-loop
   integration (liveness retry / backstop / defer) is unit-tested at the gate level but not exercised
   end-to-end here.
2. Deploy `main.py` + `pmcc_robinhood.py` (Gate-A drift → `.bak` → stage → atomic mv → flat-guarded restart).

**Deploy sequencing:** deploy **Build A first** (money/observability fix, self-contained), then Build B
after its own review + boot-smoke. The two are independent files except both touch `pmcc_robinhood.py`
(Build A: combo guards; Build B: additive triage methods — non-overlapping regions), so a combined
Stage-2 is also feasible if you prefer one restart.
