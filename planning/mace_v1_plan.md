# Robinhood MACE (Multi-Asset Condor Engine) — Phased Build Plan

Planned 2026-08-09 against worktree `cc-2026-08-09-wt` @ `7d34d82` (== origin/prod-live) on branch `claude-2026-08-09`. Verified against installed `robin_stocks 3.4.0`, `tastytrade 13.2.2` (p2venv). All file:line citations verified this session.

**APPROVED 2026-08-09 by the Board (GT_Jack) with 7 rulings, all incorporated below.** Execution mode: phase-by-phase with scoped commits; **STOP at every phase checkpoint for operator verification**; Phase 3's `brokers/robinhood.py` diff requires explicit operator review BEFORE commit (pre-authorization conditions); **do not begin Phase 0 until the operator confirms he is ready to run the probe steps.**

**AMENDMENT 2026-08-09 (Board decision — account resolution, supersedes the "new account" premise):** the new RH account came back options **Level 2** (L3 requires margin; RH permits one individual margin account per login). **MACE takes over the JOINT account** (already margin + L3; runs the joint IC condors today). Joint IC migrates to a different account/strategy in its own separate workstream — **out of scope here**. All 7 prior rulings stand. Amendment details are marked **[A2026-08-09]** throughout.

## Context

New fully-atomic (zero-HITL) defined-risk options division: iron condors on liquid ETFs via Robinhood, on a NEW account (same login), all rules deterministic + YAML-driven, live at build completion (no paper phase), Telegram as the human safety net. Operator retains deploys/config; engine retains all order decisions.

**Board decisions ratified in-session (record in deploy_log at go-live as a Board memo):**
1. **Coexist, reuse rails only.** `robinhood_joint_iron_condor` + `tasty_options_iron_condor` stay byte-untouched. No forking their files, no generic condor framework — but three extraction seams built in (§ Future extraction).
2. **`brokers/robinhood.py` additive-only pre-authorization** with conditions: (a) md5 drift-gate prod vs git BEFORE any edit is planned, report drift; (b) PMCC call-site regression tests prove byte-path-identical defaults; (c) any non-additive necessity ⇒ stop-and-report, no exceptions. MACE's reconcile loop owns polling the resting GTC PT order; PT fill detection lives in MACE reconcile under the fake-fill guard.
3. **[A2026-08-09] Account = the JOINT account (takeover), self-sourced from the repo.** Same RH login/creds, no new KV entries, plaintext filter, startup assertions — unchanged. Self-sourcing note (premise correction, surfaced): `robinhood_joint`'s `account_filter` in divisions.yaml:52 is the KEYWORD `joint`, not a number. Resolution flow: MACE inherits that filter value; Phase 0 resolves it against live account enumeration to the concrete account number, asserts `type == margin` and `option_level >= 3` (expected true today — no L2/upgrade language anywhere), and echoes the number in the probe report for operator confirmation; the confirmed numeric is then pinned as `mace.yaml account_number` + `divisions.yaml robinhood_mace account_filter` at Phase 1 (numeric hard-bind per PEAD precedent, satisfying the startup assertion `account_number == mace.yaml == divisions.yaml filter`).
4. **Go-live authorization:** zero-HITL, live at build completion, launch scope 1 contract / SPY only / breakers alert-only. Compensating controls: Telegram alerting, operator-gated config expansion, operator-supervised window for first entries (presence + kill-switch; no approval gates in the order path). Supersedes CLAUDE.md "HITL default" + Backtester human gate for this division (T8).

## Verified ground truth (drives the design)

| # | Finding | Evidence |
|---|---|---|
| V1 | **RH combo orders are LIMIT-ONLY** — `'type': 'limit'` hardcoded | robin_stocks `orders.py:1002`; repo replica `brokers/robinhood.py:1639`. ⇒ all "MARKET" exits = marketable-limit ladder (§7.3) |
| V2 | `DataExecAgent.place(order, division)` :178 / `place_combo(orders, division)` :742; PMCC LEAP guard scoped `division=="robinhood_pmcc"` (:197,:762) — MACE can't trip it. Combos are risk-gated **per-leg in the strategy layer before `place_combo`** (joint IC `_ic_orchestration.py:156`; resize verdicts ignored — reject-only gates, `_pmcc_combo.py:473,503`) | data_exec.py, _ic_orchestration.py |
| V3 | Live mechanics: `--live-divisions` CLI (`main.py:150-170`), gate at `:2571-2574` (LIVE mode AND broker family AND slug listed), `TC_LIVE_AUTHORIZED=LIVE` for systemd (`:196-197`) | main.py |
| V4 | ET helpers `utils/time.py` (`ET` :7, `now_et()` :14); PEAD window-loop pattern `main.py:3404-3455`; PMCC 4x/day at-time slots `main.py:2972+` | — |
| V5 | `pending_order` table UNFIT for 4-leg combos (single-leg shape + PEAD-locked `extra_json` keys, db.py:408-425) ⇒ MACE-owned lifecycle in `mace_rung.status` | db.py |
| V6 | `config/macro_calendar.yaml` holds 2026 FOMC(8)/CPI(12)/NFP(12); `MacroCalendar.load` mtime-hot, `is_within_halt_window` :142 — seed source for `economic_event` | data/macro_calendar.py |
| V7 | tastytrade 13.2.2 **has** `get_market_metrics(session, symbols)` → `MarketMetricInfo.implied_volatility_index_rank` (+tos/tw variants) — net-new call, SDK-supported | tastytrade/metrics.py:67-128 |
| V8 | **`config/ex_dividend_calendar.yaml` + `data/ex_dividend_calendar.py` already exist** (`next_ex_date`, `is_within_window`; used by joint IC :456,:757). SPY 2026 populated, TLT monthly rule, GLD empty. Missing: EWZ/FXI/USO/IBIT entries — config addition, not new code | — |
| V9 | Test pattern: tmp strategies.yaml fixture + fake broker + `db.SCHEMA` executescript into tmp sqlite (`tests/test_iron_condor_strategy.py:1-90`); ALL local pytest via `.\scripts\run_capped.ps1` | — |
| V10 | WebDeps `web/app.py:32-102` — new fields MUST default `None` (tg_deps constructor at `main.py:915-929` is defaults-reliant); cockpit pattern = view module `register(app)` from `routes.py:193-213`; home tile ladder `templates/home.html:82-92` | — |
| V11 | **GTC combos at RH unproven from disk** (all repo call-sites hardcode gfd) ⇒ Phase-0 probe; fallback = synthetic PT rule (T9) | robinhood.py:1261,1637 |
| V12 | `planning/iron_condor_v1_plan.md` conventions inherited: atomic 4-leg POST w/ single ref_id, per-leg risk gate unchanged, combo metadata in `extra` (no schema changes) | :135-143 |

## Architecture

### Module layout (hard boundary from pead/; no robin_stocks types above the broker layer)

```
trading_corp/mace/
  __init__.py
  config.py        # frozen dataclasses; load_mace_config(path) -> MaceConfig; fail-fast validation; sha256 config_hash
  domain.py        # CondorSpec, RungState, OptionQuote, EvalResult, ExitReason, BreakerState — neutral types only
  calendar.py      # economic_event read/write; weekly refresh; seeds (macro_calendar.yaml import, LPR 20th-monthly generator)
  ivr_provider.py  # Tasty get_market_metrics daily fetch + unavailable-fallback; ATM-IV snapshot writer (mace_iv_history)
  exdiv.py         # wraps data/ex_dividend_calendar.py for MACE symbols (+ config additions for EWZ/FXI/USO/IBIT)
  strategy.py      # PURE decision logic: entry pipeline, sizing, overflow, management precedence — no I/O
  broker_port.py   # OptionsBrokerPort ABC: chain(), leg_quote(), place_condor(), place_resting_close(),
                   #   cancel(), order_status(), open_orders(), snapshot(), account_assertions()
  rh_broker.py     # RobinhoodOptionsBroker — the ONLY port impl; the ONLY mace file importing trading_corp.brokers.*
  execution.py     # entry ladder, exit ladder, PT lifecycle, reconcile state machine (drives port, updates mace_rung)
  notify.py        # Telegram formats (§ Observability); URGENT first-line convention
  manager.py       # MaceManager: constructed from MaceConfig + injected deps (db_url, risk_agent, data_exec,
                   #   logger_agent, port, channel). No module-level singletons, no yaml re-reads in strategy logic.
trading_corp/agents/divisions/robinhood_mace.py   # thin shell, PEAD pattern (enabled/standby mtime-hot; attach_manager)
trading_corp/web/mace_view.py + templates/mace_live.html + partials/mace_live_sections.html
scripts/mace_phase0_probe.py · scripts/mace_calendar_cli.py · scripts/mace_shadow_eval.py
config/mace.yaml
```

**Import rule (enforced by an AST-walking test):** only `rh_broker.py` may import `trading_corp.brokers.*`; `strategy.py`/`manager.py` import only `mace.*`, stdlib, injected callables.

### Config split (T1)

- `config/strategies.yaml` → tiny hot block (kill-switches ONLY): `robinhood_mace: {enabled, auto_execute, division: robinhood_mace, config_file: config/mace.yaml}`. Flipping `auto_execute: false` halts new placements on the next decision; exits continue.
- `config/mace.yaml` → EVERYTHING else, loaded ONCE at boot into frozen `MaceConfig`, fail-fast on any invalid field, restart-gated, sha256 hash logged at boot + shown on `/mace`.
- `config/divisions.yaml` → `slug: robinhood_mace, broker: robinhood, account_filter: "<NEW-ACCT-#>", strategy: robinhood_mace, standby: true (until go-live), enabled: true`. `standby` is hot via shell property reads (T7); registration/broker-binding needs restart.

### Draft `config/mace.yaml` (every spec number as a default)

```yaml
account_number: "<JOINT acct # — resolved at Phase 0, operator-confirmed>"  # [A2026-08-09] must equal divisions.yaml account_filter (startup assertion)
acknowledge_foreign_positions: false    # [A2026-08-09] foreign-position guard override; entries stay disabled while foreign positions/orders exist and this is false
universe: [SPY]                         # LAUNCH. Expansion order: SPY, GLD, TLT, USO, EWZ, FXI, IBIT
max_contracts: 1
entry:
  eval_time_et: "15:45"
  entry_cutoff_et: "15:58"
  dte_min: 30
  dte_max: 45                           # prefer highest DTE in window
  short_delta_target: 0.20
  short_delta_band: [0.15, 0.25]
  credit_floor_pct_of_width: 0.30
  risk_band_usd: [150, 250]             # (width - credit)*100 target band
  enforce_risk_band: true               # "where strike listings allow"
  ivr_floor: 25
  weekly_new_rungs_per_symbol: 1        # + refill: closures re-open budget from next session
  max_rungs_per_symbol: 4
  stop_cooldown_sessions: 2
  ibit_overflow_cap: 6
  overflow_max_per_symbol_session: 1    # T6 ruling 2026-08-09
sizing:
  rung_risk_pct: 0.05
  deployment_target_pct: 0.80
  equity_snapshot_time_et: "15:40"
management:
  check_interval_sec: 300
  window_et: ["09:35", "15:55"]
  pt_pct_of_credit: 0.50                # resting GTC buy-to-close
  stop_multiple: 2.0
  time_exit_dte: 21
  time_exit_at_et: "15:30"
  exdiv_guard_sessions: 5
execution:
  entry_start_offset_usd: 0.02          # limit = mid - 0.02
  entry_tick_usd: 0.01
  entry_fill_wait_sec: 60
  entry_max_attempts: 5
  exit_fill_wait_sec: 20
  exit_max_attempts: 5
  exit_hard_ceiling_mult_of_width: 1.00
breakers:                               # ALERT-ONLY at launch (Board memo)
  day_loss_pct: 0.05
  week_loss_pct: 0.08                   # ISO week
  hwm_soft_pct: 0.85
  hwm_hard_pct: 0.75
  breaker_enforcement: "off"            # off | pause_entries | halt_flat (code exists, unit-tested, inert)
data:
  ivr_source: tastytrade_market_metrics
  iv_snapshot_daily: true
  calendar_refresh_weekday: "SUN"
notifications:
  daily_summary_time_et: "15:50"
symbols:
  SPY:  {enabled: true,  width_dollars: 3, blackout_event_types: [FOMC, CPI],           exdiv_guard: true}
  GLD:  {enabled: false, width_dollars: 3, blackout_event_types: [],                    exdiv_guard: false}
  TLT:  {enabled: false, width_dollars: 2, blackout_event_types: [FOMC, CPI],           exdiv_guard: true}
  USO:  {enabled: false, width_dollars: 2, blackout_event_types: [OPEC],                exdiv_guard: false}  # USO pays no distributions — verified at Phase 0
  EWZ:  {enabled: false, width_dollars: 2, blackout_event_types: [COPOM, BR_ELECTION],  exdiv_guard: true}
  FXI:  {enabled: false, width_dollars: 2, fallback_width_dollars: 1, blackout_event_types: [PBOC, LPR_FIX], exdiv_guard: true}
  IBIT: {enabled: false, width_dollars: 2, blackout_event_types: [], overflow_only: true, exdiv_guard: false}
```

### DB (all net-new tables; idempotent `CREATE TABLE IF NOT EXISTS` appended to `persistence/db.py` SCHEMA per house pattern db.py:438-443; migration script in `scripts/`; rollback = tables inert with no reader/writer)

- **`mace_rung`** — `rung_id` PK (`mace-{sym}-{expiry}-{strikes}-{yyyymmdd}`), `symbol`, `status` (`submitting|open|closing|closed|abandoned`), `expiry`, `legs_json` (4 legs: type/strike/side/effect/option_id/fill_price), `width_dollars`, `contracts`, `credit_actual`, `max_risk_usd`, `entry_ts`, `entry_order_id`, `pt_order_id`, `pt_debit`, `exit_ts`, `exit_reason` (`pt|stop|time|exdiv|gap|manual`), `exit_debit`, `realized_pnl`, `entry_iso_week`, `extra_json`. Weekly markers + cooldowns + day/week realized P&L are DERIVED from this table (no separate marker tables).
- **`mace_equity_snapshot`** — `snap_date` PK, `equity`, `cash`, `market_value`, `ts`. E for all sizing until next snapshot; missing today ⇒ use most recent; none ever ⇒ entries skip (`no_equity_snapshot`).
- **`mace_iv_history`** — (`symbol`, `snap_date`) PK, `atm_iv`, `ivr_tasty`, `source`, `ts`.
- **`economic_event`** — `id` PK, `event_type`, `symbol_scope`, `event_date`, `source` (`feed|seed|manual|rule`), `fetched_at`, UNIQUE(`event_type`,`event_date`,`symbol_scope`) for idempotent re-seeds. (Unprefixed by explicit spec naming — documented exception to the `mace_` rule, T2.)
- **`agent_state`** rows (`agent='robinhood_mace'`): `hwm`, breaker latches, last-eval report JSON (dashboard), calendar-refresh marker.

## Behavior specifications (decision-complete)

### Entry pipeline (daily 15:45 ET slot; sequential over universe in config order; audit `mace_entry_eval` BEFORE each branch per CLAUDE.md #2; first failing filter = recorded skip reason)
1. capacity: `open_rungs(symbol) < max_rungs_per_symbol`
2. weekly budget: `entries_this_ISO_week(symbol) < weekly_new_rungs_per_symbol + closures_this_week_before_today(symbol)` (refill: replacement eligible from the session after a close)
3. cooldown: no stop-loss exit within last 2 sessions (`stop_cooldown`)
4. blackout: no `economic_event` matching symbol's `blackout_event_types` dated today OR next trading session
5. IVR ≥ 25 (Tasty down ⇒ filter skipped + audit + non-urgent alert; credit floor + blackouts still gate)
6. build: expiry = highest DTE in [30,45] (`no_expiry`); shorts nearest |Δ|=0.20 within [0.15,0.25] each side (`no_delta_strike`); wings exactly `width_dollars` beyond (FXI retries at `fallback_width_dollars: 1` if wing unlisted); risk-band `(width−credit_mid)*100 ∈ [150,250]` when `enforce_risk_band` (`risk_band`)
7. credit floor: `credit_mid ≥ 0.30×width` (`credit_floor`)
8. size: `contracts = min(floor(0.05·E / ((width−credit_mid)·100)), max_contracts)`; 0 ⇒ `budget`
9. reserve: `Σ open max_risk + candidate ≤ 0.80·E` (`reserve`)
10. risk gate: per-leg `RiskAgent.evaluate()` with `extra["is_option"]=True` (joint-IC pattern; any reject aborts the whole condor)
Pass ⇒ entry ladder. **Overflow (T6 as RULED 2026-08-09):** capital forfeited by a failed primary filter routes to IBIT first (cap 6 rungs), then highest-IVR primary with capacity. Overflow entries are **EXEMPT from the receiving symbol's weekly-budget filter** (a receiver never has spare weekly budget — the exemption is what makes overflow non-inert). All OTHER filters apply to the receiver: capacity, cooldown, blackout, IVR, build, credit floor, sizing, reserve, risk gate. New bound: **max 1 overflow entry per symbol per session** (`overflow_max_per_symbol_session: 1`). IBIT is `overflow_only`, never a primary (OQ-3 RATIFIED). Inert at launch (universe=[SPY]).
IV snapshot: during eval, write each evaluated symbol's ATM IV + Tasty IVR into `mace_iv_history` (self-sufficiency corpus from day 1).

### Entry ladder (V1: everything is a limit)
Attempt k∈1..5 at `credit_mid_fresh − 0.02 − (k−1)·0.01` (fresh mid each attempt; never below the 0.30×width floor — crossing ⇒ stand down `credit_floor_drift`). `combo_id = mace-{sym}-{expiry}-{strikes}-{date}-a{k}` — distinct ref_id per attempt (RH dedupes repeated ref_ids). Place via `place_condor` (tif gfd, fill timeout 60s). `filled` ⇒ book rung + place resting PT + Telegram. `pending` ⇒ cancel → poll terminal: `cancelled` ⇒ next attempt; `filled` in the cancel race ⇒ book manually off the confirmed order dict (the ONE entry-side booking outside place_combo; guarded by confirmed `state=="filled"` only). Attempts exhausted or 15:58 cutoff ⇒ stand down until next daily eval + audit + Telegram. **No fill = no trade.**

### Management (5-min ticks, 09:35–15:55 ET)
Mark = cost-to-close at mid from 4 fresh leg quotes. Precedence per rung: **stop** (`mark ≥ 2.0×credit`; the 09:35 tick IS the gap rule) → **time** (`DTE ≤ 21` AND `now ≥ 15:30 ET`) → **exdiv** (`exdiv_guard` on, short call ITM (spot > short-call strike), ex-div within 5 sessions via `data/ex_dividend_calendar.py`). Any exit: FIRST cancel the resting PT and confirm terminal — if the PT turns out `filled` in that race, book the PT exit and stop. Never adjust/roll/leg out; whole structures only; never past 21 DTE or into expiration.

### Emulated-MARKET exit ladder (V1 consequence; spec's "MARKET" — T4)
Basis = fresh natural (buy-to-close shorts at ask, sell-to-close wings at bid ⇒ net natural debit). Attempt 1 = natural rounded up to tick; unfilled 20s ⇒ cancel (same race handling) ⇒ attempt k+1 = fresh natural + (k−1) ticks; max 5 attempts; hard ceiling `width×1.00` per spread. Exhaustion ⇒ rung stays `closing` + **URGENT Telegram** (operator manual action is the backstop).

### PT (resting GTC)
Placed at entry-fill time: resting GTC buy-to-close at `round_tick(credit_actual×0.50)` via new `place_multi_leg_resting` (no poll). Live = real resting order; paper mode = synthetic manage-tick rule (`mark ≤ pt_debit` ⇒ close at PT price) since PaperExecutionBroker has no resting simulation (T11 — accepted; live is the target).

### Reconcile loop
Polls each `pt_order_id` (`order_status`): `filled` ⇒ book exit (realized = `(credit − pt_debit)·100·contracts`) + Telegram; unexpectedly `cancelled` ⇒ alert + re-place next manage tick. Also drains `submitting` rungs (boot/crash): match by deterministic combo_id against open/recent orders; confirmed `filled` ⇒ promote to `open` (+ place PT); confirmed terminal-dead or unmatched past a 2-session horizon ⇒ `abandoned` + alert. **Fake-fill guard everywhere: an HTTP error/exception NEVER books a fill; booking requires confirmed order state.**

### Sizing/E
E = `mace_equity_snapshot` for the current session (15:40 ET slot, `broker.snapshot()` on the MACE-bound broker — acct-scoped per robinhood.py:495-497). Never intraday buying power.

### Startup assertion (fail-closed — deliberately stricter than PMCC's warn-only :4630-4658)
On first live-mode tick: `account_number == mace.yaml == divisions.yaml filter` (numeric bind already raises on mismatch, robinhood.py:452-461); `option_level` ≥ 3 else entries disabled + URGENT alert + audit (exits/reconcile still allowed); every order/snapshot path passes `account_number` (structurally true: place_multi_leg :1249, snapshot :497, open-orders orders.py:78; `cancel_option_order` is order-URL-routed with no acct param — noted in code comment).

**[A2026-08-09] Account-exclusivity assertion (fail-closed):** at arm time, NO other enabled division in divisions.yaml may carry this account_filter (keyword-or-numeric resolving to the same account) — specifically the `robinhood_joint`/`robinhood_joint_iron_condor` block must have been repointed or disabled so this filter is MACE's alone. Two engines on one account ⇒ **refuse to arm** + URGENT alert.

**[A2026-08-09] Foreign-position guard:** at startup, inventory open option positions AND open orders on the account; anything not matching a `mace-` combo_id or a known `mace_rung` ⇒ **entries disabled** + URGENT Telegram (exits/reconcile still permitted) until the account is clean OR the operator explicitly acknowledges via `acknowledge_foreign_positions: true` (default `false`). Snapshot-E remains the sizing basis and is only trusted under this guard. NOTE: load-bearing at launch — the Joint account holds legacy joint-IC positions until that migration workstream clears them; go-live requires clean-or-acknowledged (see Phase 5).

### Breakers (alert-only)
Evaluated each manage tick + at daily summary against snapshot-E and HWM (`agent_state`): day realized ≥5% E; ISO-week realized ≥8% E; equity <0.85·HWM; <0.75·HWM. Firing ⇒ Telegram FIRST LINE `🚨 URGENT — MACE <condition>` + numbers + suggested manual action. `breaker_enforcement` branches (`pause_entries`: entry pipeline short-circuits; `halt_flat`: pause + ladder close-all) exist, unit-tested, SHIPPED `"off"`.

## Additive `brokers/robinhood.py` change spec (exact; pre-authorized additive-only)

| Change | Kind | Default behavior |
|---|---|---|
| `_submit_spread_with_ref_id(…, time_in_force="gfd")` | new trailing kwarg (:1610-1648) | byte-identical payload |
| `place_multi_leg` reads `extra["combo_time_in_force"]` (dflt "gfd") + `extra["combo_fill_timeout_s"]` (dflt class 20.0) from leg 0 | new optional extra-key reads (:1206,:1574) | identical when keys absent (PMCC/IC never set them) |
| `place_multi_leg_resting(orders, *, ref_id, time_in_force="gtc") -> str` | new method | no existing caller |
| `get_option_order_status(order_id) -> dict` | new method | no existing caller |

Gate: md5 drift check of prod `brokers/robinhood.py` vs `git -c core.autocrlf=false show` immediately before editing (deploy_log recipe). Drift or any non-additive necessity ⇒ **STOP-AND-REPORT**. PMCC golden-payload regression tests: capture the exact spread POST payload for existing PMCC/IC call-shapes pre/post change; assert byte-identical.

## Observability

**Telegram** (existing `TelegramChannel.push`/`push_split`; fill-detail follows PEAD/PMCC precedent — T3):
- Entry: `✅ MACE ENTRY {SYM} {expiry} {sp}/{lp}P {sc}/{lc}C ×{n} — credit ${c} (floor ${f}) · PT resting ${pt} GTC · maxrisk ${mr}`
- Exit: `🔔 MACE EXIT {SYM} {expiry} ×{n} — {REASON} @ ${debit} · P&L {±$} ({±% of credit})`
- Stand-down/reject: `⚠️ MACE {SYM} entry stand-down — {k}/5 attempts unfilled (last ${p})` / `⚠️ MACE order rejected …`
- Unhandled error: `⚠️ MACE ERROR {loop}: {exc}` (top-level try in every loop)
- Daily 15:50 ET summary: equity, HWM, open rungs (one line each), day realized P&L, breaker states, next-session blackouts
- Breakers: `🚨 URGENT — MACE {condition}` first line + numbers + suggested action; Tasty-IVR-down = non-urgent note

**Dashboard `/mace` v1** (mace_view.py `register(app)` pattern; WebDeps fields default `None`; home.html tile): (1) header — execution_mode badge, config_hash, standby/enabled/auto_execute states; (2) open rungs table — symbol, strikes, DTE, credit, current mark, P&L, distance-to-stop, distance-to-PT; (3) equity + HWM; (4) full effective config (from the frozen object, not the file); (5) last eval results per symbol (entered / skip reason); (6) calendar next-7-days; (7) IVR per symbol. Plain + functional; htmx 30s partial for (2).

## Phases

### Phase 0 — Capability confirmation, drift gate, feed probes (NO repo changes)
**WILL:** read-only API calls on both RH accounts' metadata; at most ONE far-OTM deliberately-unmarketable cancel-immediately test order on the NEW account; read-only Tasty/EODHD/yfinance HTTP calls; md5 reads of prod files over read-only SSH. **WILL NOT:** place any marketable order, touch existing accounts' orders/positions, modify any prod or repo file, restart anything, create KV entries.
1. **Drift gate (md5 sweep)** per deploy_log recipe + CRLF trap (CLAUDE.md :399-411): `brokers/robinhood.py`, `main.py`, `agents/data_exec.py` (sweep even though default-untouched), `web/app.py`, `web/routes.py`, `templates/home.html`, `persistence/db.py`, `config/{divisions,strategies,risk,ex_dividend_calendar}.yaml`. **Any drift on `brokers/robinhood.py` ⇒ STOP-AND-REPORT before planning any edit.** Report drift table.
2. **[A2026-08-09] Account-resolution probe** (operator-run script/REPL, later committed as `scripts/mace_phase0_probe.py`): account list → resolve the repo's `joint` keyword filter to the concrete JOINT account number; assert present, `type == margin`, `option_level >= 3` (expected true today); all other accounts still enumerate (routing sanity); **echo the resolved number in the probe report for operator confirmation** (it becomes the Phase-1 numeric hard-bind). Also inventory the account's open option positions/orders (baseline for the foreign-position guard + joint-IC migration scope).
3. **The one test order (CORRECTED per Board ruling 1 — credit-limit direction). [A2026-08-09] SEQUENCING GATE: runs ONLY AFTER the operator confirms joint IC is disabled/standby on this account** (its HITL engine must not observe a foreign resting condor). Steps 1/4/5/6 have no such dependency and run first. SPY iron condor, 30–45 DTE, shorts ~5Δ (far-OTM), qty 1, `timeInForce='gtc'`, `account_number=<JOINT>`, **credit limit = 0.95×width (e.g. $2.85 on a $3-wide condor) — deliberately UNFILLABLE** (demanding ~the full width as credit can never be marketable). NOTE the inversion this ruling caught: for a NET-CREDIT limit order, a LOW limit ($0.01) is the minimum-acceptable credit ⇒ instantly marketable ⇒ would FILL; a HIGH limit rests. Assert: response `id` present, account URL contains NEW acct, `time_in_force=='gtc'` echoed, state ∈ {queued, confirmed, unconfirmed} with **zero fills**; then cancel → poll to `cancelled`. Proves in one shot: 4-leg condor accepted, account routing, **GTC combos (V11)**, cancel+status path. **The probe script must carry a marketability-direction comment block (credit: lower limit = more marketable; debit: higher limit = more marketable) so this inversion cannot recur in the entry/exit ladder implementations** — entry ladder walks the credit limit DOWN toward marketability (mid−0.02, −$0.01/attempt); exit ladder walks the debit limit UP toward marketability (natural, +$0.01/attempt). Both already conform; the comment pins the rule.
4. **Tasty probe:** `get_market_metrics` for all 7 symbols — field map incl. ETF `market_cap` pydantic parse risk (T10; fallback = raw `session._get("/market-metrics")` manual parse).
5. **EODHD probe:** GET `/api/calendar/economic-events` with our key → in-plan verdict (OQ-1; expectation: not on Free/Starter tier).
6. **USO distribution check** (currently pays none — confirms `exdiv_guard: false`).
**Checkpoint 0:** probe report (account #, option level, order JSON round-trip, GTC echo, Tasty field map, EODHD verdict, drift table) → operator ratifies GTC-PT design GO (else T9 fallback) + OQ-1 calendar answer. **Rollback:** nothing (one cancelled order remains in RH history; id noted in deploy_log Phase-0 entry).

### Phase 1 — Config, domain, DB, calendar, data providers
**New:** `mace/{__init__,config,domain,calendar,ivr_provider,exdiv}.py`; `config/mace.yaml`; `scripts/mace_calendar_cli.py` (add/remove/list manual events); `scripts/mace_phase0_probe.py` (committed for the record); tests `test_mace_{config,calendar,ivr,exdiv}.py`. **Modified:** `persistence/db.py` (SCHEMA append — net-new tables, no ALTER; migration script `scripts/migrate_mace_tables.py` for the prod DB); `config/ex_dividend_calendar.yaml` (+EWZ/FXI/USO/IBIT). Calendar seeds: macro_calendar.yaml import (FOMC/CPI → `source='seed'`; YAML stays master, weekly loop re-seeds idempotently — T12), LPR-fix rule generator (20th monthly, next-business-day roll — `source='rule'`), manual entries for OPEC+/Copom/BR-elections/politburo (`source='manual'`, operator-supplied dates via CLI).
**Checkpoint 1:** `run_capped` pytest green; `init_db` against a prod-DB COPY shows the 4 new tables; `mace_calendar_cli list` shows seeded FOMC/CPI + generated LPR rows; operator spot-checks dates. **Rollback:** delete new files; SCHEMA block additive + inert.

### Phase 2 — Decision engine (pure logic, no I/O)
**New:** `mace/strategy.py` (entry pipeline, sizing, overflow, management precedence, breaker math); tests `test_mace_{strategy_entry,sizing,strategy_manage,breakers}.py` with golden fixture chains (incl. band-edge deltas, FXI fallback width, credit-floor edges, refill/weekly/cooldown matrices, overflow routing, ISO-week P&L); `scripts/mace_shadow_eval.py` — runs the full entry eval on LIVE data, prints the per-symbol decision table, places NOTHING.
**Checkpoint 2:** pytest green; operator runs `mace_shadow_eval` on a market afternoon and sanity-checks strike/credit/size decisions against the broker app. **Rollback:** delete files.

### Phase 3 — Broker port, additive robinhood.py changes, execution, division shell
**New:** `mace/{broker_port,rh_broker,execution,notify,manager}.py`; `agents/divisions/robinhood_mace.py`; tests `test_mace_{rh_broker,execution,division_shell}.py` (mocked port: ladder sequences, cancel races, fake-fill guard, reconcile drains, PT lifecycle) + **PMCC golden-payload regressions** for robinhood.py. **Modified:** `brokers/robinhood.py` (additive table above; drift-gate re-run immediately before). Risk integration: per-leg evaluate + `risk.yaml` `overrides.robinhood_mace` block (per_trade_risk_pct aligned to 5% rung risk; daily-loss/DD autohalt neutralization per T5 — PEAD precedent risk.yaml:157-160; **RATIFIED by Board 2026-08-09**; per-leg evaluate stays active).
**Checkpoint 3:** full `run_capped` pytest green incl. PMCC golden-payload regressions; drift-gate re-run shows exactly the intended robinhood.py diff; operator code-review of the additive diff. **Rollback:** revert robinhood.py to pre-edit md5 (golden tests prove equivalence); delete mace files; config appends inert while `standby: true`.

### Phase 4 — Engine wiring + observability
**Modified:** `main.py` (construct MaceManager from mace.yaml + deps; execution_mode triple-gate PEAD-style :1798-1803; four loops: daily-slots loop [15:40 snapshot → 15:45 entry → 15:50 summary, PMCC-slot pattern], 5-min manage loop, reconcile loop, weekly calendar-refresh loop); `web/app.py` (WebDeps `mace_*` fields, default None); `web/routes.py` (`mace_view.register(app)`); `templates/home.html` (tile); **New:** `web/mace_view.py`, `templates/mace_live.html`, `partials/mace_live_sections.html`; `config/{divisions,strategies}.yaml` MACE blocks (`standby: true`, `auto_execute: false` until go-live); tests incl. WebDeps construction-completeness extension.
**Checkpoint 4:** local paper-mode boot (`run_capped`, scratch DB): all four loops log online, config hash on `/mace`, tile renders; forced-clock unit run shows snapshot→entry→summary slot sequence; Telegram formats verified via test hook. **Rollback:** revert main.py/app.py/routes.py/home.html hunks; tiles fall back to generic `/division/robinhood_mace`.

### Phase 5 — Go-live
1. Pre-deploy: full `run_capped` pytest green; prod-vs-main md5 sweep of every touched file; config review — `universe: [SPY]`, `max_contracts: 1`, `breaker_enforcement: "off"`, account number matches Phase-0 probe. **Standing config-review rule (Board ruling 7, applies to every future expansion edit): do NOT expand universe beyond 2 symbols until the entry-window serialization work (OQ-2: shorter fill waits or parallel ladders) is built — this line also goes in the expansion runbook.**
2. Funding confirmed in NEW account (Monday); deploy-day 15:40 snapshot shows expected E.
3. Deploy per [[prod-live-deploy-base-rule]]: prod-live is deploy base; per-file LF-md5 proof (worktree==prod==expected); advance prod-live same session; deploy_log entry incl. **Board memo** (decision 4 zero-HITL/live-at-completion + T5 risk-autohalt neutralization + T8 Backtester-gate supersession + T6 overflow ruling) and Phase-0 test-order id.
3b. **[A2026-08-09] Account-takeover preconditions:** (i) exclusivity — `robinhood_joint`/joint-IC block repointed or disabled in divisions.yaml so the account_filter is MACE's alone (assertion refuses to arm otherwise); (ii) foreign-position guard satisfied — account clean of legacy joint-IC positions/orders (migration workstream, out of scope here) OR `acknowledge_foreign_positions: true` set consciously; entries stay disabled until then.
4. Flip: `standby: false`, `auto_execute: true`, add `robinhood_mace` to `--live-divisions` (systemd unit args), `TC_LIVE_AUTHORIZED=LIVE` already set for the process; restart; startup assertion passes (account + L3 + exclusivity + foreign-position guard).
5. **Supervised first-entry window:** operator present 15:35–16:05 ET on first eval day(s); kill-switch ready (`auto_execute: false` = hot halt of new placements; `standby: true` = hot scan/manage halt; `--live-divisions` removal + restart = full disarm). No approval gates in the order path.
6. Watch: first PT resting order visible in RH app as GTC; first 5-min manage tick logs; 15:50 summary arrives.

## Deviations / tensions / open questions (NONE silently resolved)

**Board rulings 2026-08-09 (all resolved — nothing left to ratify):**
- **T5 RATIFIED:** neutralize daily-loss/DD autohalts for `robinhood_mace` in risk.yaml per PEAD precedent (exits-deadlock rationale: a `strategy_state` halt rejects EXITS too, risk.py:113-118/:144-168). Per-leg `RiskAgent.evaluate()` stays active. Record in the Board memo.
- **T6 MODIFIED:** overflow entries EXEMPT from the receiver's weekly-budget filter (specced version was structurally inert — a receiver never has spare weekly budget). All other filters apply to the receiver + new bound: max 1 overflow entry per symbol per session. §7.1 + test matrices updated.
- **OQ-1 RATIFIED as defaulted:** launch on manual+seed+rule calendar sources; macro_calendar.yaml FOMC/CPI seed covers the SPY launch scope. Phase-0 EODHD verdict stands either way. FMP deferred post-launch — note: Board decision 3 governed ACCOUNT credentials, not data-provider keys; it does not bar a future FMP KV entry.
- **OQ-3 RATIFIED:** IBIT overflow-only, never a primary.
- **T4 ACCEPTED as specced:** emulated market-exit ladders are the required adaptation of the operator's market-order decision given V1 (limit-only API); residual stop-slip bounded by defined risk. No change.
- **Ruling 1 (Phase 0):** test-order credit limit corrected to deliberately-unfillable 0.95×width (see Phase 0 step 3 + marketability-direction comment requirement).
- **Ruling 7 (OQ-2 carry-forward):** universe must not expand beyond 2 symbols until entry-window serialization work is done — added to Phase 5 config review + expansion runbook line.
- **[A2026-08-09] Amendment 8 — account resolution:** new acct is L2-only ⇒ MACE takes over the JOINT account (margin + L3). Self-sourced from repo (`joint` keyword filter → Phase-0 numeric resolution + operator confirmation → Phase-1 numeric pin). New fail-closed assertions: account-exclusivity (refuse to arm if any other enabled division carries the filter; joint IC must be repointed/disabled by go-live) + foreign-position guard (`acknowledge_foreign_positions` flag, default false; entries disabled while foreign positions/orders exist). Phase-0 resequenced: drift/Tasty/EODHD/USO first; test order only after operator confirms joint IC disabled/standby on the account. L2/upgrade language deleted — assertion is simply `option_level >= 3` on Joint. Joint-IC migration = separate workstream, out of scope.
Already-decided deviations, recorded: **T1** config hybrid (hot kill-switches in strategies.yaml; all else frozen mace.yaml) · **T2** `economic_event` unprefixed (spec names it) · **T3** Telegram fill detail follows PEAD/PMCC precedent · **T4** MARKET exits emulated as ladders (V1 limit-only; true stop can slip past 2.0× during a burst — bounded by defined-risk max loss) · **T7** divisions.yaml standby hot via shell re-stat; registration needs restart · **T8** Backtester gate superseded by Board decision 4 · **T9** GTC unproven until Phase 0 (fallback = synthetic PT manage-tick rule = spec deviation requiring operator note) · **T10** Tasty ETF `market_cap` parse risk (raw-GET fallback) · **T11** paper-mode PT divergence accepted · **T12** YAML remains FOMC/CPI master; weekly re-seed idempotent · **OQ-2** entry window fits ~2 symbols/day worst-case (5×60s ladders, 15:45–15:58) — fine at launch; shorten waits or parallelize before expanding past 2 symbols (future work, deliberately not built).

## Future extraction note (per Board decision 1)
Seams: (a) `mace/domain.py` neutral types, zero broker leakage above `rh_broker.py`; (b) `OptionsBrokerPort` single broker surface — a future Tasty impl replaces `rh_broker.py` only; (c) `MaceManager` constructible from `MaceConfig` + injected deps (no singletons, no yaml re-reads in strategy logic). **Deliberately NOT built:** second port impl, HITL/approval hooks, adjustment/roll logic, generic condor framework, shared code with the two existing IC engines (byte-untouched), parallel entry ladders, paper resting-order simulation.

## Verification (end-to-end)
- Every phase: `.\scripts\run_capped.ps1 python -m pytest` (house discipline) — new tests + full suite; baseline count from latest deploy_log entry must hold.
- Phase 0/3: drift-gate md5 sweeps (CRLF-aware) with reported tables.
- Phase 2: `mace_shadow_eval` live-data dry run reviewed by operator.
- Phase 4: paper-mode boot on scratch DB; loop/slot logs; dashboard + Telegram render checks.
- Go-live: startup assertion, supervised first entry, resting GTC PT visible in RH app, 15:50 summary, deploy_log + Board memo appended, prod-live advanced same session.

## Build-session execution notes
- Worktree `cc-2026-08-09-wt` branch `claude-2026-08-09`; commit per phase (scoped commits, artifacts as-you-go); push with `-u origin claude-2026-08-09`.
- All operator paste commands: ONE line ≤100 chars; anything longer ships as a pure-ASCII `.ps1` runner per command-paste-rule.
- Agent SSH to prod: read-only only; writes/restarts operator-run.
