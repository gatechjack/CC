# HANDOFF — Poly→Kalshi Phase 2a (roster split)

A fresh CODE agent picks up **Phase 2a**. This is orientation/housekeeping. Read it in full (and
the ratified two-division plan in this dir) before building. **Do NOT start 2a until the operator
confirms.** Live-money status leads every report you write.

## Git state (verified 2026-08-16, not narrated)
- **Branch:** `poly-kalshi-phase2b-cp3-2026-08-16`. Tip at handoff: **`dcebfcc`** (this HANDOFF file is
  committed on top of it). Pushed to origin this session.
- **origin/prod-live tip:** **`18db30e`** ("deploy(poly-kalshi CP7): advance prod-live … 5 files") =
  **Phase 1 only**.
- ⚠️ **prod-live git does NOT contain the deployed Phase 2b.** `git merge-base --is-ancestor 3706a3a
  origin/prod-live` → **NO**. The Phase 2b data layer is deployed & running on the box (branch
  `3706a3a`) but prod-live git was never advanced for it (only Phase 1 CP7 advanced prod-live). The
  git RECORD (18db30e) lags the DEPLOYED box by the whole Phase 2b batch. Operator may want to advance
  prod-live git → 3706a3a as a bookkeeping catch-up — **flagged, not done.**

## DEPLOYED vs COMMITTED-BUT-NOT-DEPLOYED (critical)
- **DEPLOYED & LIVE on the box (branch @ `3706a3a`, verified this session):** Phase 2b data layer —
  CP1 trigger journaling, CP2 mark poller + volatile tables (`poly_kalshi_mark_live/_history`,
  migration ran), CP3 broker-free dashboard read + HTMX refresh + copy-moment feed. (Deployed via the
  Stage-2 tar bundle = 10 runtime files; PID 760172.)
- **COMMITTED on the branch but NOT on the box → rides the NEXT batched deploy:**
  - **`8dc4d97`** — poller log fix (RedactingFilter TypeError on the `mark tick` log; own file
    `poly_kalshi_marks.py`). *The current box still logs that TypeError every ~60s until deployed —
    cosmetic; the poller works.*
  - **`dcebfcc`** — display/notify batch:
    - Part 1 — readable team names in the poly_kalshi read path (`data.py` + 2 templates), broker-free.
    - Part 2 — **live-copy Telegram** (`poly_kalshi_executor.py` `notify_fn` + `main.py:1504`
      `notify_fn=channel.push`). **NB: changes `main.py` + the executor — not yet on the box.** Fires
      ONLY on a live placement, best-effort.
    - Part 3a — hide the PCT tile (`home.html` guard); 3c — PCT link (`poly_kalshi_live.html`).
    - Part 3b (recency column) **NOT built** — premise "recency stored on selected_whales" was wrong;
      recency is computed offline by `scripts/score_whale_recency.py` from round-trip history. Decision
      pending (inline-score vs persist vs defer).
- **Next batched deploy therefore carries:** `8dc4d97` + `dcebfcc` + your 2a work — ONE restart.
  Phase 2b (`3706a3a`) is ALREADY on the box → it is the drift-gate BASELINE, not part of the delta.

## ⚠️ Deploy-baseline nuance
Drift-gate against the **actual box md5s** (read fresh, read-only), **NOT prod-live git**. The box =
branch `3706a3a` for the Phase-2b-touched files (`data.py`, `main.py`, `poly_kalshi_*`, templates) +
`18db30e`/`5fba5ee` for the rest. prod-live git (18db30e) does not reflect the deployed 2b, so it is
the wrong baseline for the 2b-touched files. This session's Stage-1/Stage-2 runners drift-gated
against the box directly — mirror that. Box is NOT a git repo → deploy = file overwrite + per-file
md5 install-verify; git SHA on the box is meaningless.

## Live state — must stay UNDISTURBED until the operator-run batched deploy
- Engine **PID 760172**, `poly_kalshi_mlb` **ARMED**: auto_execute=true / dry_run=false / halted=false.
- Mark poller running (~60s ticks).
- **1 open position:** pre-game **BAL@TB** (`KXMLBGAME-26AUG171805BALTB-TB`), "marking…" (pre-game
  market not quotable yet; populates as liquidity builds).
- **Do NOT restart / touch the live loop.** All 2a work is branch-only + read-only prod verification
  until the operator runs the deploy.

## Phase 2a task — summary (full spec: the ratified plan in this dir + memory `pct-kalshi-repurpose-plan-2026-08-15`)
- **Roster split:** whales split watched → **papered** (PCT paper farm = `polymarket_copy_trader`) ⇄
  **live** (`poly_kalshi_mlb`). A whale is copied by exactly ONE side.
- **Atomic move:** promote (paper→live) / demote (live→paper) must be **atomic** — no window where a
  whale is on both or neither roster.
- **INVARIANT `live ∩ paper == ∅`:** a live-copied whale MUST NOT also be paper-copied (and vice
  versa). Enforce + test.
- **promote/demote semantics:** define exactly what moves (the `selected_whales` entry); idempotency is
  **wallet-keyed**.
- **Kill paper Telegram on promote:** ⚠️ *finding from this session* — there is **no paper-sim Telegram
  in the copy path today** (`poly_kalshi_executor`/`polymarket_copy_trader` send none; the only
  `[PAPER]` sender is `comms/bitunix_lifecycle_notifier.py`, unrelated). So this item may be a no-op or
  depends on a paper-telegram you add — clarify with the operator. The **live**-copy Telegram was added
  this session (`dcebfcc`, `poly_kalshi_executor._notify_live_copy`, live-placement only).
- **MUST-TEST — pin-back invariant:** demoting a whale back to paper must be proven: after demote the
  whale is paper-only, live stops copying it, no double-copy, open-position handling is defined. This
  is the critical regression.

## How Jack (operator) works — hold this bar
- **No shell.** Every prod mutation = an **operator-run `az vm run-command` `@file` runner** you write
  to `C:\Users\AA Incorporado\cc\` (**command-paste-rule**: ASCII, no-BOM, validate with
  `[scriptblock]::Create`; hand ONE short line `powershell -ep bypass -f .\NAME.ps1`; pipe complex
  remote bash via base64/STDIN). Agent is READ-ONLY on prod; operator runs; you verify the pasted
  output.
- **Checkpoint discipline is ABSOLUTE:** build → STOP → report with EMPIRICAL evidence (file:line or
  real pasted output, NEVER narrate/hallucinate) → operator reviews → proceed. **A prior agent's
  hallucinated checkpoint was caught — hold that bar.** Verify, don't narrate.
- **Live-money status LEADS every report.** Stop-and-report at forks (don't auto-resolve); surface
  anomalies with diagnostics; don't expand scope; tighter commits (commit artifacts as you go).
- **Work in `C:\Users\AA Incorporado\cc`; NEW worktree branch per build; no sudo.**

## Shared-files byte-unchanged rule (ABSOLUTE)
Keep byte-identical (diff each build): `trading_corp/agents/strategies/kalshi_copy_trader.py`,
`trading_corp/agents/sports_team_mapping.py`, `trading_corp/brokers/kalshi_live.py`. The poly_kalshi
division DUPLICATES their logic in its own files — never edit the shared ones.

## Deploy plan
Phase 2a ships in **ONE batched operator-run deploy** carrying everything already committed (log fix
`8dc4d97` + display `dcebfcc` + your 2a) → ONE restart. Verify after: **re-arm** (new PID /
auto_execute=true / dry_run=false / halted=false — THE critical check), 0 boot tracebacks, poller
ticks + marks, migration (if 2a adds tables), shared-files byte-unchanged. Roll back via the `.bak`
set if boot fails. Avoid the 15:40–15:58 ET restart window.

## Live-loop-untouched confirmation
This session was 100% branch-only + read-only prod verification: no prod mutation, no restart, no
order placed by the agent. The live loop (PID 760172) was not touched.
