# MACE morning brief — 2026-08-14 (fresh session; everything cold)

Written 2026-08-14 at session wrap so a NEW agent session can run the day from committed
artifacts alone. **Standing rule: this session is READ-ONLY unless the operator rules otherwise.**

## Where things stand (as of 2026-08-14 ~05:30 UTC)

| Item | State |
|---|---|
| prod-live | `a7ec388` (pushed; runtime blobs == deployed tree `bb3cb7a`) |
| Engine | MainPID **707835**, NRestarts=0, 0 tracebacks at boot verify |
| config_hash | `e9c0499886c4` (execution_mode=live, auto_execute:true — evals PLACE) |
| Universe | **[IBIT w1, XLE w2/w1, GDX w2/w1]** active; SPY+GLD `enabled:false` |
| Open rungs | SPY 2 (W33, fully managed — manage/exit/reconcile ignore `enabled`); GLD 0 verified |
| Halt button | /mace tri-state LIVE, boot-tested ARM->HALT->ARM, latch left cleared |
| Blackout calendar | 45 rows seeded 08-14 (FOMC/CPI/NFP + LPR_FIX) + OPEC 2026-09-07 scope ALL |
| Params | rung_risk 0.10 / deployment 0.95 / band-max 260 / weekly 1 / entry 2x30s / max_contracts 1 |

Deploy evidence: `reports/2026-08-13_mace_oq2_deploy_evidence/` (junit, deploy/restart/verify
outputs, calendar-seed proofs). Deploy_log: `runbooks/deploy_log.md` 2026-08-14 04:02-04:10 UTC
entry. Board memo: `planning/mace_3active_oq2_board_memo_2026-08-13.md`.

## 1) Morning: read-only shadow-eval (>= 09:35 ET)

Operator runs from `C:\Users\AA Incorporado\cc`:

    powershell -ep bypass -f .\mace_shadow_eval_am.ps1

READ-ONLY confidence check (Board ruling: NOT a deploy gate — the engine's eval-time
credit-floor filter is the operative safety). Depends on `_mace_oq2_shadow_am.sh` in the same
directory (both stay in place). Output lands in `mace_shadow_eval_am_out.txt` — operator pastes
it back to the agent.

**Interpretation:**
- Each of IBIT / XLE / GDX must clear **0.30 x width** credit floor on live quotes.
- **XLE is the likeliest casualty** (w1 fallback already ratified for it).
- A floor fail is **NOT an emergency** — at 15:45 the engine's own eval-time filter SKIPs that
  symbol safely (audited `mace_entry_eval` skip_reason=credit_floor).
- The operator then rules: **accept the SKIP** (default, zero-touch) vs **config-only backfill
  restart**. Pre-ruled backfill order: ONE slot out -> **FXI first** (pending its liveness gate;
  FXI is w1 no-fallback), TWO slots out -> **IWM first, then FXI**.
- Any backfill restart must be COMPLETE before 15:40. **Never restart 15:40-15:58 ET** (the
  daily-slots catch-up can evaluate + PLACE). Restart boot takes ~2.5 min.

## 2) Afternoon: attended first 3-active live eval (15:45-16:00 ET)

Operator watches **/mace + Telegram**, halt button hot (POST /mace/halt — halts next
symbol/attempt; manage loop unaffected).

Expected:
- One `mace_entry_eval` audit row per active symbol, evaluated in **IVR-descending order**.
- **IBIT may legitimately IVR-skip** (floor >= 25; IVR was ~9 at stage-A) — coherent, not a defect.
  Legit skips: IVR floor, no_wing, credit_floor.
- All entry activity **terminal by ~15:59:35**; stand-down reasons if seen: cutoff /
  operator_halt / window_budget (+ `mace_entry_window_skip` if a symbol's turn never starts).
- **No duplicate ref_ids** (dup-entry fix live since 08-13; OQ-2 keeps one ladder in flight).
- **SPY's 2 rungs untouched** by entries; 15:50 Telegram summary may arrive late (~15:58+) on a
  full-window day — known and honest.
- Post-eval: audit-trail review (window_skip / window_budget), RH order+position reconcile.

## 3) Monday 2026-08-17: Sunday-loop proof-of-life

Run `cc\_mace_cal_check.sh` (read-only) via az run-command. **ANY `mace_calendar_refresh` audit
row proves the Sunday 08-16 weekly loop ran** — the 08-14 manual CLI seed writes NO audit row
(only manager.refresh_calendar audits, manager.py:384), so the manual seed cannot mask it.

## 4) Rollback + kill-switches (cold reference)

- **Rollback this deploy:** `powershell -ep bypass -f .\mace_oq2_rollback.ps1` (from cc\) ->
  `bash /home/azureuser/mace_oq2_bak_20260813/rollback.sh` — restores the 7 `.bak` to `b11af9b`,
  removes the halt partial, md5-verifies, restarts. Prod-side script REFUSES inside 15:35-16:00 ET.
- **Hot halts (no restart):** UI halt button; `strategies.yaml robinhood_mace auto_execute:false`
  (entries stop, exits continue); `divisions.yaml robinhood_mace standby:true` (scan+manage stop).
- **Full disarm:** remove `robinhood_mace` from systemd `--live-divisions` + restart.
- Restart mechanism: az run-command RunShellScript (root, NO sudo), ~2.5 min boot, outside
  15:40-15:58 ET only.

## 5) Standing discipline for the fresh session

- **Read-only unless the operator rules otherwise.** No manual eval trigger (auto_execute:true
  PLACES). Agent may not place live orders or edit settings (classifier-enforced).
- Command-paste rule: operator commands ONE line <=~100 chars; long payloads as pure-ASCII
  LF `.ps1`/`.sh` files in `C:\Users\AA Incorporado\cc`.
- prod-live is the deploy base; advance same session with LF-md5 proof if anything deploys.

## 6) Horizon items (not today)

- **OPEC tripwire:** after each OPEC+ meeting (next 2026-09-06), pull the announced next-meeting
  date from opec.org PR and CLI-add it **weekend-rolled to Monday** (weekend-dated rows are
  permanently inert — is_blackout exact-date vs {session, rolled next_session}).
- **GDX December refresh (HARD deadline 2026-12-01):** replace the PROJECTED 12/21 ex-date with
  VanEck's confirmed December press-release date + redeploy the calendar yaml (frozen -> restart).
- Open items (a)-(e): memory `mace-open-items-and-ops-2026-08-11` + deploy_log.
