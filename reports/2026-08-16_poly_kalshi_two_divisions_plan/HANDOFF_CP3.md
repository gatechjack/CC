# HANDOFF → next code agent — Poly→Kalshi two-division split, Phase 1 (CP3+)

**You are picking up a checkpoint-gated build. Read this, then read the plan. Do NOT touch the live loop until CP7. Do NOT chain checkpoints — build one, STOP, operator reviews.**

## Where things stand
- **Branch:** `poly-kalshi-mlb-phase1-2026-08-15` @ **`34ce5ac`** (pushed, tree clean).
- **Plan (your spec):** `reports/2026-08-16_poly_kalshi_two_divisions_plan/PLAN.md` @ `eff65ef` — full scoping findings + file:line + Phase 1/2 checkpoints + reuse-vs-bespoke + atomic-move + epoch mechanism. **Rulings are ratified; do not relitigate.**
- **prod-live tip:** `5fba5ee` (box main.py md5 `044cc21`). The engine box is NOT git → deploy = file-overwrite + md5-vs-blob; every prod mutation is operator-run.
- **split-CP2 DONE** (`34ce5ac`, config-only, **NOT deployed**, live loop undisturbed): registered `poly_kalshi_mlb` as a `broker:kalshi` division in `config/divisions.yaml`; fixed the inert `division:` field in `config/strategies.yaml:1788` → `poly_kalshi_mlb`. Verified: classifies `prediction_markets`, appears in `_pm_divisions_all`; poly_kalshi suite 62/62. These configs sit in the branch only — the running engine will not see them until the CP7 deploy.

## ★ LIVE STATE — division is ARMED, real money. Keep it UNDISTURBED until CP7.
- `poly_kalshi_mlb`: **auto_execute=true, dry_run=false, halted=false**, $5/trade, 4 whales, `$100/day` loss-halt + `25/day` count-halt, wallet-keyed idempotency, engine PID 753629.
- **3 live orders placed 2026-08-16, ALL FILLED, n=1 reviewed CLEAN** (side/ticker/size/no-double-fire/confidence all pass, proven on real money against each whale's actual Poly bet — see `verify_fills_poly.py`):
  - #1 SDTrading → YES **MIA** 9@0.54 (KXMLBGAME-26AUG161340MIACIN-MIA), Kalshi order 7000441c…
  - #2 0x0x23kj → YES **CIN** 10@0.48 (…MIACIN-CIN), order d4645fb2… (same game as #1, opposite side — two whales disagreeing, correct)
  - #3 xifutloong3 → YES **AZ** 10@0.47 (…AZATL-AZ), order …5eb8437f
- **orders_today 3/25**; `realized_pnl_day=$0` (games were in progress at review; not settled). Both halts armed (settlement sweep every ~10min; count-halt counting). Zero guardrail trips since arm.
- **Do NOT restart, re-arm, or edit the running loop.** Only the CP7 deploy restarts it, and CP7 must re-verify ARMED/unhalted immediately after.

## The three n=1 flags (found during fill review) — with scope
- **FLAG 1 — persist fill data. FOLD INTO PHASE 1 (CP4 prerequisite).** Executor adds `rec["resp"]` (the Kalshi FillEvent: filled count, fill price, fee) AFTER `_record` already wrote the audit row (`poly_kalshi_executor.py:295-297`; the audit write is at `:315`). So the journal stores the **limit** price, not the **fill** — fill data is lost. **Fix before CP4** (persist the FillEvent into the poly_kalshi_order payload), else CP4's reconciliation gate is meaningless.
- **FLAG 2 — persist Poly trigger.** The triggering Poly `slug`/`outcome`/`side` live only in the in-memory `shadow_log` (`poly_kalshi_copy_trader.py:177,201`), lost on restart. Post-hoc trigger audit currently needs re-fetching Poly. **Backlog, or fold into Flag 1** (add slug/outcome to the persisted order payload).
- **FLAG 3 — in-memory idempotency resets on restart. PHASE 2.** `_placed` (executor) is the dedup; robust within a session (key = `division|wallet|ticker|outcome|action`, immune to fill-splitting — one order per logical copy), but resets on restart, so a whale re-betting the SAME game after a restart could re-order. A persistent idem ledger closes it. Not seen in the n=1.

## CP4 HARD GATE (operator-held STOP)
The resolver's computed realized P&L (settlement-based, `kalshi_resolver._compose_round_trip` realized = `qty*(1-price)` won / `-qty*price` lost, `:230`) **MUST reconcile** with `StrategyState.realized_pnl` (the number driving the $100 halt, fed by the settlement sweep `record_realized`). **Disagreement = STOP, not a warning.** Flag 1 must be fixed first (need real fill price/qty for an honest reconciliation).

## Two pre-existing issues (NOT introduced by this work)
- **10 failing tests in `tests/test_prediction_markets_dashboard.py`** — proven pre-existing (fail with CP2 stashed). Root cause: fixtures use `entry_ts=2026-05-11` but `DASHBOARD_RT_CUTOFFS['kalshi_llm_arbitrage']='2026-07-07'` (`data.py:3963`), so `_kalshi_cutoff_clause` filters the fixtures out. **CP5 must fix/avoid worsening** when adding the kalshi agent_state epoch.
- **Equity double-count risk:** `poly_kalshi_mlb` and `kalshi_arbitrage` both use `secret_ref: kalshi_karen` (same KAREN account; `kalshi_arbitrage` is `standby:true`). **Verify equity is not double-counted before wiring equity snapshots at CP7.**

## Non-negotiables
- **Shared files BYTE-UNCHANGED, diff every checkpoint:** `trading_corp/agents/strategies/kalshi_copy_trader.py`, `trading_corp/data/sports_team_mapping.py`, `trading_corp/brokers/kalshi_live.py`. (Diff vs `origin/prod-live`.)
- `trading_corp/agents/strategies/poly_kalshi_executor.py` is **this division's own file — edits allowed** (that's where Flag 1's fill-persistence goes).

## Remaining Phase 1 checkpoints (build one, STOP, review)
- **CP3** — surface OPEN live fills on the dashboard: add `division` (+ Flag-1 fill fields) to the poly_kalshi_order payload; extend `_query_pm_open_trades` kalshi branch to recognize `actor='poly_kalshi_mlb'` / the poly_kalshi_order kind (fields differ from the kalshi arb actors — `count` not `qty`, `side` is yes/no). **Now includes Flag-1 fill persistence.**
- **CP4** — surface RESOLVED P&L: resolver adapter for `kind='poly_kalshi_order'` → `kalshi_round_trips`; **+ the reconciliation HARD GATE above.**
- **CP5** — agent_state epoch for the kalshi division (mirror `_get_polymarket_metrics_epoch`), so both dashboards use the same reversible cutoff; don't worsen the 10 pre-existing failures.
- **CP6** — reset BOTH epochs to the split date (**operator-run runner**); verify both dashboards read 0 from the epoch, on-disk history retained.
- **CP7** — deploy (**operator-run runner**): drift-gate + prod-live advance + restart; **verify the live loop comes back ARMED/unhalted**; verify no equity double-count.

## How Jack (operator) works
- **No shell.** Every prod mutation (epoch write, deploy, restart) → hand him an **az `@file` run-command runner** (RG-SHARED-PROD / tc-prod-vm); he runs it, you verify read-only. (`az … --scripts @file`; inline args >~32KB fail "filename too long".)
- **Checkpoint discipline is absolute** — build to a checkpoint, STOP, he reviews, then proceed. First prod change is his explicit go.
- **Verify empirically, never narrate.** Every claim → file:line or a real paste. If it isn't built, say so — a prior agent hallucinated a checkpoint report and it was discarded. Hold that standard.
- **Live-money events LEAD the report**, never appended after config diffs. Brief, evidence-dense.
- Read-only ops runners already on Jack's machine under `cc\pk_*.ps1` (status, fills, positions, drift-gate, deploy, rollback) — reusable patterns.
