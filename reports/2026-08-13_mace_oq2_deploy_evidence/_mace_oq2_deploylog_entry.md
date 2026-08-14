

## 2026-08-14 04:02-04:10 UTC (2026-08-13 night ET) - MACE OQ-2 entry serialization + 3-active universe (IBIT/XLE/GDX) + /mace entry-halt button (code + frozen config, RESTART)

**Deployed LIVE** to `trading-corp.service` (tc-prod-vm / RG-SHARED-PROD) via az run-command
RunShellScript (root, self-gated payload: PRE-GATE -> STAGE-GATE -> backup+rollback.sh -> swap ->
POST-GATE -> py_compile; runners `cc\mace_oq2_{deploy,restart,verify,rollback}.ps1`).

**Commits:** ee9cfd5+66cad59 (OQ-2), 7300985+3210a4a (halt button), 80802a2 (config package),
b10c59a (Board memo), 89b07de+bb3cb7a (CP4 report + closures). Deployed tree = `bb3cb7a` on
`claude-2026-08-13b`, base `b11af9b` (= origin/prod-live tip at deploy; re-fetched + confirmed
unmoved before the prod-live advance).

**Triggered by:** Board 2026-08-13 timeline override (3-symbol live at the 2026-08-14 15:45 ET
attended eval) + same-session Phase-5 resequencing ruling: DEPLOY TONIGHT closed-market (zero
disruption, removes the 13:00 deadline); provisional roster IBIT+XLE+GDX shipped now. **The
eval-time credit-floor filter remains the operative safety on activation; the morning shadow-eval
is a confidence check, not a deploy gate, per Board ruling this session.** If a symbol fails its
0.30 x width floor on live quotes, the Board rules then: accept the engine's own eval-time
credit-floor SKIP (safe, documented) or config-only backfill restart completed before 15:40
(pre-ruling: two slots out -> backfill flips IWM FIRST then FXI; one slot -> FXI first,
liveness-gated). Board memo: `planning/mace_3active_oq2_board_memo_2026-08-13.md` (CP0 rulings:
GDX Option A PROJECTED 12/21 guard-ON + December tripwire; IWM 9/15+12/15 corrections; widths/
blackouts; OQ-3 reversal for IBIT; IBIT exdiv_guard:false non-payer deviation, memo s4).

**Backup:** `/home/azureuser/mace_oq2_bak_20260813/` (7 x `.bak` at base md5s + generated
`rollback.sh`, ET-window-guarded).

**Files deployed (8, LF-md5 pre -> post):**
- `trading_corp/mace/manager.py` - OQ-2 ordering (IVR-desc primaries then overflow) + per-symbol
  dynamic deadline + `mace_entry_window_skip` audit + per-symbol/round halt checks
  (ef84efc96790cb6afcd1b25e8b3dd6c2 -> 2f9ca06c37cdee27d55a5d48f9614c82)
- `trading_corp/mace/execution.py` - `run_entry(deadline=, halt_fn=)`; per-attempt effective cutoff
  `min(cutoff, deadline)`; stand-down reasons `window_budget`/`operator_halt` (precedence
  cutoff > operator_halt > window_budget) (1cb214fee9a4774abab6fdb9df24ab65 -> 01c0b2594b11355b5c1c98ceb3e6987f)
- `trading_corp/mace/loops.py` - slots-loop poll 30s -> 5s (015f35d8fa0bb699426d950006e894bd -> 5a9b3d9f38230407eab14c4a8f56cc9d)
- `trading_corp/web/mace_view.py` - POST /mace/halt + /mace/arm (audit-before-state), GET
  partials/halt, tri-state wiring (8251be6f6cf6b952b04f2fd8b23a1b62 -> c4a8004805d8943b604d5f149f446d91)
- `trading_corp/web/templates/mace_live.html` - halt partial include (b4dfcafdc2678774e1f4e64dbe5c89b5 -> f5bc01cd000a83ddb3f921e7a6d9e08e)
- `trading_corp/web/templates/partials/mace_halt.html` - NEW tri-state pill + button
  (absent -> 0058a239b4f3b541071786aa14c2c919)
- `config/mace.yaml` - universe [IBIT, XLE, GDX]; SPY+GLD enabled:false; new XLE/GDX/IWM blocks;
  FXI w1 no-fallback; IBIT overflow_only removed + guard:false (non-payer, memo s4); rung_risk_pct
  0.10 / deployment 0.95 / band-max 260 / weekly 1 / attempts 2 / wait 30
  (454fff5bc7249b9d104bef9aadf073ff -> 1dc7c276cbab2ac40e4ff62da3346574; config_hash fe177fcd3882 -> e9c0499886c4)
- `config/ex_dividend_calendar.yaml` - XLE 2 confirmed (SSGA SPD003792); GDX PROJECTED 12/21 +
  December-refresh tripwire (open-items deadline 2026-12-01); IWM 5 confirmed incl. 9/15+12/15
  corrections + 12/30 excise (iShares GPS0826-5839861); FXI 12/15+12/30 confirmed
  (d320ff69e964e10f9cec4b8dba29a98c -> 3feb4183ea8de4c3ddcce74dba1ed71d)

**Features shipped:**
- OQ-2 serialization: N-symbol entry round fits the 15:45->15:58 window by construction - IVR-desc
  order, dynamic per-symbol deadline (early finishers donate time forward), audited
  `mace_entry_window_skip` (no symbol silently starved), `window_budget` clean stand-down via the
  existing `_entry_standdown(clean=True)` path. Strictly ONE ladder in flight (concurrency
  REJECTED - reintroduces the 08-12 dup-entry stale-rung bug class). Chokepoint/fake-fill/
  fake-cancel/cancel-json-body/ref_id untouched by construction, re-proven by test.
- 3-active universe: IBIT (w1, non-payer; IVR floor may legitimately skip day 1), XLE (w2/w1,
  OPEC blackout, Sep ex-div guard live inside the DTE window from day 1), GDX (w2/w1, FOMC
  blackout, PROJECTED ex-div + tripwire). SPY+GLD retired to enabled:false - SPY's 2 open W33
  rungs remain fully managed (manage/exit/reconcile never read `enabled`); GLD 0 open rungs
  verified below.
- /mace entry-halt button: latch (agent_state robinhood_mace/entry_halt, PEAD dial precedent),
  auto_execute:false semantics, halts NEXT symbol/attempt (honest latency in UI), manage loop
  untouched, tri-state ARMED / HALTED (button) / HALTED (config), fail-safe read (latch-read
  error == NOT halted), audit-before-state.
- Board ladder params: entry 2 attempts x 30s wait (~70-80s/symbol typical, ~130s worst; 3-symbol
  worst ~6.5 min vs the 13-min window).

**Verification:**
- Build gates: targeted MACE suite 279 green (all 22 test_mace_* files + calendar); full suite
  3436: 3333 pass / 91 fail / 12 err - junit name-diff vs golive baseline
  (`cc\mace_golive_preflight_junit.xml`) EMPTY BOTH DIRECTIONS (same 91f/12e test-name-for-test-name,
  0 MACE deltas; +66 growth = this build's tests; junit `cc\mace_oq2_phase4_junit.xml`). CP4 report:
  `reports/2026-08-13_mace_oq2_checkpoint4.md` (incl. Board matrix -> test-name map, all PASS).
- Drift-gate (read-only, ~00:45 ET): all 7 prod runtime files LF-md5 == `b11af9b` blobs, halt
  partial absent; re-proven server-side at PRE-GATE 04:02 UTC immediately before swap.
- Deploy 04:02 UTC: all gates OK (PRE/STAGE/POST + py_compile on prod venv as azureuser); engine
  deliberately NOT restarted in the same script.
- Restart 04:04:48 UTC boot (00:04 ET - market closed, outside 15:40-15:58): MainPID 697735 ->
  707835, NRestarts=0, ActiveState=active, web :8000 HTTP 200, 0 tracebacks.
- Boot verify 04:09 UTC (all green): NEW config_hash e9c0499886c4 logged (execution_mode=live;
  the wiring-log standby-gated/go-live-BLOCKED text = known hardcoded boilerplate main.py:2010);
  4 MACE loops online (daily-slots/manage/reconcile/weekly-calendar); GET /mace 200 with
  IBIT+XLE+GDX + ENTRIES: ARMED tri-state; halt latch cycle ARM->HALT->ARM PASS (HALTED (button)
  and ENTRIES: ARMED rendered; latch left cleared halted:false; mace_ui_halt + mace_ui_arm audit
  rows, actor mace_operations); mace_rung: SPY open=2 (managed), GLD ZERO open rungs; divisions
  healthy (bitunix sfp+futures restart-resume matched=0/orphan=0, PEAD/PMCC/kalshi booted,
  home 200). Full outputs: `cc\mace_oq2_{deploy,restart,verify}_out.txt`.
- PENDING (2026-08-14): morning READ-ONLY shadow-eval confidence check >=09:35 ET
  (`cc\mace_shadow_eval_am.ps1`); attended 15:45 ET first 3-active live eval (Phase 6).

**Rollback recipe:** `bash /home/azureuser/mace_oq2_bak_20260813/rollback.sh` (via
`cc\mace_oq2_rollback.ps1`) - restores the 7 `.bak`, removes the halt partial, md5-verifies back
to `b11af9b`, restarts (refuses inside 15:35-16:00 ET). Config-only rollback: revert universe/
params + restart. Kill-switches unchanged: auto_execute:false (hot), standby:true (hot),
--live-divisions removal + restart, plus the new UI halt button.

**prod-live:** `b11af9b` -> this entry (FF, same session). Runtime blobs at the advanced tip are
identical to `bb3cb7a` (this commit adds only runbooks/deploy_log.md), so the 04:02 POST-GATE
md5s are the LF-md5 proof prod == prod-live tip.
