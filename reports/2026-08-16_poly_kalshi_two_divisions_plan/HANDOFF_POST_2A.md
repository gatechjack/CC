# HANDOFF — Post Phase 2a (roster split COMPLETE + deployed live)

A fresh agent/chat picks up here. Phase 2a is **done and live**. This is orientation for what comes next.
Do not re-open closed work. The memory anchor **`poly-kalshi-mlb`** is authoritative.

## STATE — Phase 2a COMPLETE + deployed live (2026-08-17 ~04:39 UTC)
- The paper/live **double-state is gone.** The live loop reads its OWN key
  `agent_state[poly_kalshi_mlb/live_whales]` (**4 whales**); the PCT paper farm reads
  `polymarket_copy_trader/selected_whales` which is now **empty** and is **Telegram-silenced**. Boot
  invariant confirmed live: **`4 live / 0 paper, disjoint`**.
- Engine **PID 765455**, `poly_kalshi_mlb` **ARMED** (auto_execute=true / dry_run=false / halted=false),
  $5 stake, $100 loss-halt + 25/day count-halt. Open BALTB-TB position rode the restart (flag-3 clean).
- **Promote/demote endpoints are LIVE** — `POST /api/polymarket/whales/promote-live/{wallet}` and
  `.../demote-live/{wallet}`: manual (operator-triggered), atomic (one 3-key `set_agent_state_multi`),
  invariant-guarded (`live ∩ paper == ∅`). Promote flattens the paper book (reuse); demote rides the
  open live position to settlement (no broker action).
- **Git/deploy state (all pushed):** source branch `poly-kalshi-phase2a-2026-08-16` @ **`ebd394e`**
  (origin, tip==origin). **`origin/prod-live` @ `e7af3bc`** — in sync with the box (no longer lags; the
  2 undeployed whale-recency scripts were excluded; deploy_log hand-unioned, history preserved). Full
  suite base-vs-branch FAILED+ERROR diff **EMPTY at every checkpoint** (zero new failures). Shared
  byte-locked files (`kalshi_copy_trader.py`, `sports_team_mapping.py`, `kalshi_live.py`) **byte-unchanged
  throughout.** Checkpoint reports: `reports/2026-08-16_poly_kalshi_two_divisions_plan/PHASE2A_*.md`.

## ROLLBACK (if ever needed)
Backups `.bak_cp6_20260817_043609` retained on the box (the 11 modified files).
```
powershell -ep bypass -f .\pk_cp6_rollback.ps1 -BackupSuffix .bak_cp6_20260817_043609 -CutoverWasApplied
```
`-CutoverWasApplied` is REQUIRED: the cutover seeded `live_whales`/emptied `selected_whales`, so the
rollback reverses the cutover FIRST (self-contained txn — the OLD code reads `selected_whales`) then
restores + removes `roster_split.py` + restarts.

## FORWARD THREADS (fresh agent picks up next — PRIORITY ORDER)
**(a) Claude Design prompt for the live dashboard — GATED on observed data.** Do NOT design against
mock/empty data. Wait until a game populates the live mark + sparkline (BAL@TB tonight 18:05 ET, or the
next game) so the mark poller has real `poly_kalshi_mark_live/_history` rows. Capture the FUNCTIONAL
dashboard screenshots (populated marks + sparkline) and use them as the "make it fun" anchor — the design
binds to observed data only. This is the primary next step once live data exists.

**(b) Live-whales roster TAB on the poly_kalshi_mlb live dashboard.** A specified requirement (not
optional). Parallel to PCT's watchlist / paper-whale tabs. The roster is already correctly seeded
(`live_whales`=4) — this is a **display/dashboard-read gap only** (surface the live roster in the UI).
Fold into the Design work (a) or ship as a small standalone add.

**(c) Backlog (not urgent):**
  - **Equity-curve wiring decision** — KAREN account attribution so the poly_kalshi_mlb equity curve
    doesn't double-count vs kalshi_arbitrage (poly_kalshi wires no equity loop today; decide before
    adding one).
  - **Shared `secrets.py` RedactingFilter fix** — `log("%s", dict)` raises `TypeError` app-wide (bit the
    mark-poller tick log, fixed locally at `_log_tick`, but the root filter still bites any
    `log(..., dict)` caller). Core-scoped fix.
  - **Gross-vs-net fee confirm** at the first post-CP6 settled fill (is Kalshi `pnl_dollars` net-of-fee?
    the resolver persists `fill_fee`).

## HOW JACK (operator) WORKS — hold this bar
- **No shell.** Every prod mutation = an operator-run `az @file` `.ps1` you write to
  `C:\Users\AA Incorporado\cc\` (command-paste-rule: ASCII, no-BOM, `[scriptblock]::Create`-validated;
  ONE short line `powershell -ep bypass -f .\NAME.ps1`; complex remote payloads base64/STDIN). Agent is
  READ-ONLY on prod; operator runs; you verify the pasted output.
- **Checkpoint discipline is ABSOLUTE:** build → STOP → report with EMPIRICAL evidence (file:line or real
  pasted output, NEVER narrate/hallucinate) → operator reviews → proceed. Never chain checkpoints.
- **Live-money status LEADS every report.** Verify, don't narrate. Stop-and-report at forks. Surface
  anomalies with diagnostics. Don't expand scope. Tighter commits (commit artifacts as you go).
- **Work in a NEW worktree branch per build; no sudo.** The memory anchor `poly-kalshi-mlb` is
  authoritative for live state.
