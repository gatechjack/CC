# BitUnix Live-Trading Readiness Audit

**Date:** 2026-05-29 · **Type:** read-only structural/operational audit (no code/config/deploy changes) · **Status:** planning doc for operator review (committed local, not pushed).

**Premise (operator's, accepted, not re-litigated):** the *strategy* has passed sufficient testing/tuning to be *considered* for live. This audit covers **everything else** — the structural and operational gap between paper-mode-bitunix-today and a defensible first real dollar, then scaled operations. It does **not** evaluate strategy edge, tuning, or the 60-day paper-eval clock decision (those are separate; see [[bitunix-paper-clock]]).

---

## TL;DR — the one thing to internalize

**Going live is not a config flip. There is no live execution path at all.** `BitunixBroker.place_order`, `cancel_order`, and `modify_position_tp_sl_order` are deliberate `NotImplementedError` stubs (`trading_corp/brokers/bitunix.py:375,384,456`). The broker is **read-only** today: `snapshot`/`quote`/`get_funding_rate` hit the real BitUnix REST API; nothing can place, cancel, or modify a real order. The paper system routes every order to a `PaperBroker` via `PaperExecutionBroker` (`main.py:~1767-1782`). "Phase 4" (live placement) is **unbuilt**.

So Stage 1 ("first $10") is a **build**, not a flip: real `place_order` + fill observation + position/balance reconciliation against the broker + a kill switch + cost accrual, with several failure modes currently unhandled. The good news: because `place_order` raises, **accidental live placement is structurally impossible today** — the system fails closed.

---

## Stage model

| Stage | Definition | Gate |
|---|---|---|
| **0** | Paper mode (current). Real broker reads, simulated fills. | — (here now) |
| **1** | Smallest live: 1 position, **$10–$50 risked**, accepted as validation cost. | "Can we place a real order, observe the real fill, reconcile to the cent, and never lose more than the risked amount *to a system bug* — with a kill switch that works." |
| **2** | Small live: 1 position, **$100–$500 risked**, many trades over days/weeks. | Validates the daily/weekly operational shape: cost accrual, reconciliation drift, restart-with-open-position, operator workflow. |
| **3** | Scaled live: intended sizing (PREMIUM 4%/8x … per `bitunix_futures_observer.py:~190`), multi-position, full tempo. | Validates concurrency, exposure caps, circuit breakers, capital management at size. |

Cross-cutting process gates that exist *independent* of this audit: the 60-day paper-eval clock (`~2026-07-19`, [[bitunix-paper-clock]]), Board sign-off for `auto_execute`, and the webhook↔LangGraph `auto_execute_caps` harmonization (CLAUDE.md §1 sharp edge). Don't pull Stage-1 forward without those.

---

## 1. Broker integration completeness

**Current state.** `trading_corp/brokers/bitunix.py` — `BitunixBroker(paper=False)` reads the real API but is a **read-only stub** for writes:
- ✅ `snapshot()` (`:172-290`) — real `GET /api/v1/futures/account` + `/position/get_pending_positions`; real equity + open positions (USDT/USDC margin). Powers the dashboard.
- ✅ `quote()` (`:292-317`), ✅ `get_funding_rate()` (`:319-373`) — real public endpoints.
- ✅ Auth scheme implemented (`:71-99`, SHA256 nonce+ts+key signing).
- ❌ `place_order()` (`:375`), `cancel_order()` (`:384`), `modify_position_tp_sl_order()` (`:456`) — **all `NotImplementedError`**.
- ❌ **No websocket** at all — REST-only. No fill-update stream; no `get_fills`.
- ❌ **No fill semantics validated** — IOC / post-only / reduce-only / partial-fill: none exist (nothing places orders).
- ❌ **No testnet/sandbox path** — reads hit live mainnet only.

**Gap → Stage 1 (LARGE):** implement `place_order` (market/limit + reduce-only for exits) against the real REST API; observe the resulting fill (poll `get_pending_positions` / an orders/fills endpoint — no WS needed for Stage 1); implement `cancel_order`. Validate against BitUnix **testnet first** if one exists, else accept the first live $10 as the test. **Gap → Stage 2 (MEDIUM):** `modify_position_tp_sl_order` for the v2 SL-ratchet lifecycle; partial-fill-aware fill reading. **Gap → Stage 3 (MEDIUM):** websocket fill stream (latency/reliability at tempo) or a hardened poll loop; multi-symbol.

**Sequencing:** this is the **critical-path root** — nothing else in live matters until `place_order`+fill-observation exists.

---

## 2. Order-path safety

**Current state.** `config/strategies.yaml` `bitunix_futures.auto_execute: true` (paper-mode auto-execute, "caps are the gate, no per-trade HITL"). Safety today is **process-mode + broker-wrapping**, not the flag: in `--paper` (default) `main.py:~1767-1782` wraps the real broker in `PaperExecutionBroker(bitunix, PaperBroker)` so orders simulate; in `--live` the raw broker is returned and `place_order` raises. The observer never calls `data_exec.place()` — it writes `would_have_placed` + a `paper_trade_record` (`bitunix_futures_observer.py:~2388-2422`).

**Critical nuance for Stage 1:** `auto_execute: true` + no per-trade HITL means that **the moment `place_order` is real and the process is `--live`, the first live trade executes with no human in the loop.** That is acceptable only if (a) sizing is genuinely tiny and (b) the kill switch works.

- ❌ **No hard kill switch.** There is a *soft* daily cap (`DAILY_RISK_KILL_PCT=0.03`, `bitunix_futures_observer.py:~2354`) that rejects *new* orders once cumulative at-risk hits 3%/UTC-day — but **no runtime command to halt new orders + cancel resting orders + flatten open positions** without editing config + restarting. `RiskAgent` can set `flatten_account=True` on a drawdown breach (`risk.py:~164`) but there's no broker code to actually flatten (place_order stub).
- ❌ **No per-symbol/per-strategy live-disable without restart.** `enabled`/`auto_execute` are mtime-hot-reloaded for *some* reads but there's no audited, instant "disable bitunix live now" control.

**Gap → Stage 1 (MEDIUM):** a real kill switch (halt-new + cancel-resting + flatten, callable from the dashboard/Telegram or a one-command script); **decide HITL-for-first-N-live-trades** (recommend: yes, a manual confirm on the first handful, even though paper runs no-HITL). **Gap → Stage 2 (SMALL):** instant per-strategy live-disable + an audited `bitunix_live_halted` latch in `agent_state`. Relates to security **C-3** (unvalidated `strategies.yaml` hot-reload can flip `auto_execute`) — see §13.

---

## 3. Risk gate at live values

**Current state.** Two layers: bitunix observer overrides (`bitunix_futures_observer.py:~190-206`: `EFFECTIVE_RISK_PER_TRADE_PCT=0.005`, `DAILY_RISK_KILL_PCT=0.03`, tier sizing PREMIUM 4%/8x … WEAK 1%/2x) size the order so `qty·price·leverage·stop_pct/equity ≤ 0.5%`; then `RiskAgent.evaluate()` (`risk.py:82-252`, the single chokepoint) applies global caps: `per_trade_risk_pct 0.015`, `per_strategy_daily_loss_pct 0.03` (halts strategy on realized loss), `per_account_max_drawdown_pct 0.15` (sets `flatten_account`). 

**These numbers have only ever run against *paper* accounting.** They've never been exercised against real margin, real fills, or real funding debits.
- ⚠️ **Leverage/margin only implicitly gated.** The per-trade cap is on **notional** (`qty·price`, `contract_multiplier=1` for perps), not margin-required or liquidation distance. The observer's sizing formula accounts for leverage; `RiskAgent` does not. **No liquidation-price check** — a Stage-3 concern but worth a Stage-1 sanity guard.
- ❌ **No real-time circuit breakers** (rapid-loss-in-window, latency-anomaly, order-rate-limit). Risk is deterministic + daily, not continuous.
- ⚠️ **`per_strategy_daily_loss_pct` keys off `realized_pnl`** which today is paper-computed — for live it must read broker-reconciled realized PnL (see §4).

**Gap → Stage 1 (SMALL):** confirm the caps compute correctly against a *real* equity figure from `snapshot()` (not the paper $0/placeholder) and that `flatten_account`/`halt_strategy` actually wire to a flatten action (today they don't — see §2 kill switch). **Gap → Stage 2 (MEDIUM):** real-realized-PnL feeding the daily-loss cap; a liquidation-distance guard. **Gap → Stage 3 (MEDIUM):** concurrency cap, per-symbol exposure cap, rapid-loss circuit breaker.

---

## 4. Reconciliation for live

**Current state.** Two reconcilers exist, **neither checks live broker truth**:
- `bitunix_position_reconciler.py` — reconciles paper-replay ATR-trail SL state; **dormant in paper** (its own docstring: "emits no audit rows until Phase 4 broker fill state lands"). `list_open_positions()` reads `paper_trade_record`, not the broker.
- `scripts/audit_reality_reconciler.py` — **post-mortem** check of *closed* paper trades vs bars (just fixed to 1m granularity this session, deploy_log 2026-05-29 ~03:12). Not a live open-state check.

**For live you need three new comparisons, none of which exist:** broker-reported fills vs system-recorded orders; broker positions vs system-tracked positions; broker balance vs system-tracked equity. `snapshot()` *reads* positions + equity + `crossUnrealizedPNL` but nothing compares them to recorded state or alerts on drift.

**Gap → Stage 1 (MEDIUM):** at minimum, a **post-trade reconciliation** — after each live close, pull broker fills/position/balance and assert they match the recorded order to the cent; alert + halt on mismatch. (Single position, so this can be simple.) **Gap → Stage 2 (MEDIUM-LARGE):** periodic (e.g. 60s) open-position + balance reconciliation with mismatch-threshold alerting; design the pull frequency + mismatch-handling policy (halt? alert-only? auto-correct?). **Gap → Stage 3 (MEDIUM):** multi-position reconciliation + a divergence dashboard tile + daily PnL reconciliation (system vs broker). This is the live analogue of the discipline in [[telegram-audit-success-is-confirmed-delivery]] / [[committed-not-deployed-recurring-drift]]: **confirm against the authoritative external source, don't assume.**

---

## 5. Failure modes and recovery

Per-mode current handling (`bitunix.py`, `paper_trade_replay.py`):

| Failure | Handled? | State |
|---|---|---|
| REST API down | ❌ Gap | `httpx` 15s timeout, **no retry/backoff/fallback**; errors log a warning and are swallowed; `_connected` stays True (stale-snapshot blind). |
| Websocket disconnect | n/a today | No WS exists; REST-poll is primary. **No stale-feed health check** — needed before any WS-based fill stream. |
| Restart with open position | ⚠️ Paper-only | Paper replay resumes from `extra_json.filled_legs`/`current_sl` (`paper_trade_replay.py:~430-438`). **Live: no code to query broker open positions on boot and re-attach tracking.** This is a Stage-1 *must* — a restart mid-position must not lose the position or double-count it. |
| Order stuck pending | ❌ Gap | No timeout/cancel policy. Paper only has a `max_hold_seconds` expiry window, not an order-cancel. |
| Partial fill of a TP leg | ❌ Gap | Lifecycle is **binary** (leg filled / not) — `_classify_v2_multi_leg` checks price-touch, no fractional fills. A 50%-filled TP1 has no representation. |
| Credential rotation mid-trade | ❌ Gap | Creds loaded once at init; rotation → auth failures (swallowed) until restart. |
| Clock drift | ⚠️ Implicit | Signing uses local `time.time()`; no server-time sync. >~30s drift → auth reject (swallowed). |

**Gap → Stage 1 (MEDIUM):** REST retry/backoff + a stale-snapshot/connection health signal; **restart-with-open-position resume from broker truth** (load broker positions on boot, reconcile to `paper_trade_record`); a stuck-order timeout→cancel policy. **Gap → Stage 2 (MEDIUM):** partial-fill representation in the lifecycle; clock-skew guard. **Gap → Stage 3 (MEDIUM):** WS fill stream + disconnect→poll fallback. Credential-rotation mid-trade is acceptable-with-restart at all stages if rotations are scheduled during flat periods.

---

## 6. Cost accrual (fees + funding)

**Current state.** Close-out messages literally say `Fees: not tracked in paper / Funding: not tracked in paper` (`bitunix_lifecycle_notifier.py:~160`). Filed MEDIUM in BACKLOG. Pieces that exist: `get_funding_rate()` is fetched (for the HTF gate, not accrued); `FeeConfig` (`trade_plan.py:~29-60`, taker 0.04% / maker 0.014% / slippage) is used to **floor TP1 above round-trip cost** but not booked to PnL. **No `fees_paid`/`funding_paid` columns** on `paper_trade_record`; **no funding/fee audit event kind**; **no reconciliation against broker-reported funding payments.**

**For live this MUST be real** — net PnL, the daily-loss cap, and tax records all depend on it. **Gap → Stage 1 (MEDIUM):** book the real entry/exit fees (from the fill response) and any funding debits to the closed-trade record (a 1-position trade makes this tractable); even a `fees_paid`/`funding_paid` field on `paper_trade_record` + the close-out message. **Gap → Stage 2 (MEDIUM):** per-position funding accrual across funding intervals + a `funding_accrual` audit kind + reconciliation vs broker-reported funding. **Gap → Stage 3 (SMALL):** roll into the divergence dashboard.

---

## 7. Live observability surfaces

**Built:** Telegram lifecycle (TP/SL/close-out, `bitunix_lifecycle_notifier.py`, shipped + audit-semantics-hardened this session); lifecycle *delivery* divergence monitor (`scripts/telegram_lifecycle_divergence_check.py`, daily cron); dashboard tiles for PA/HTF/decision-flow/trade-plan/score (`web/templates/partials/bitunix_*`).

**Gaps (per the operator's list):**
- ❌ Alerts for **connection loss, daily-kill triggered, halt mode, unusual fill** — none push to Telegram (the lifecycle notifier is close-out-only). *Stage-1 must:* at least connection-loss + daily-kill + halt alerts.
- ❌ Dashboard tiles for **broker connection status, equity-vs-system-tracked, open-positions-vs-broker, unrealized PnL, today's realized PnL, reconciler status.** *Stage-2.*
- ❌ Divergence monitors for **orders-sent-vs-fills-received** and **system-PnL-vs-broker-PnL.** *Stage-2.* (The existing monitor is telegram-*delivery* only.)

**Effort:** Stage-1 alerts SMALL-MEDIUM (reuse the channel + a few send sites); Stage-2 tiles + divergence monitors MEDIUM.

---

## 8. Capital management

**Current state: ABSENT.** No low-equity alert, no balance-threshold monitoring, no deposit/withdrawal handling. `snapshot()` reads equity but nothing watches it. The daily kill halts silently (no push). Funding the account, profit-withdrawal cadence, loss-top-up policy: all undefined.

**Gap → Stage 1 (SMALL):** a low-equity Telegram alert (e.g. equity < starting − risked-amount) + an operator-decided **funding amount** for the account (the "$10–$50" must physically be there). **Gap → Stage 2 (SMALL):** a written deposit/withdrawal + profit-sweep policy; balance-history audit. **Gap → Stage 3:** multi-account segregation if you ever run multiple strategies on BitUnix.

---

## 9. Regulatory and tax

**Jurisdiction:** US / Florida (operator context). BitUnix is an offshore exchange — assume **self-report** (no 1099; verify with BitUnix). For US tax you need, per trade: every fill (price, qty, side, ts, USD value), every fee, every funding payment, with timestamps + USD values, to compute realized gains + ordinary-income funding.

**Current state:** `paper_trade_record` captures entry/exit/result/R/PnL but **not fees, not funding, and PnL is paper-computed** (§6). So **tax-grade records do not exist yet** — they're a direct consequence of the §6 cost-accrual gap plus the §4 broker-reconciliation gap (need *broker-confirmed* fills/fees, not simulated).

**Gap → Stage 1 (SMALL, mostly process):** ensure the first live trade's broker fills/fees/funding are captured verbatim (overlaps §4/§6). **Gap → Stage 2 (MEDIUM):** a complete, exportable per-fill ledger (CSV/JSONL) with USD values; confirm completeness against broker statements. Not Stage-1-blocking for $10, but **blocking before any volume of real trades accrues** (don't create an untracked tax liability).

---

## 10. Recovery procedures

**Current state:** the auth-lockout runbook exists (`runbooks/auth_lockout_recovery.md`, Authelia only). **No bitunix-live recovery runbooks.** Security H-12 (the report) flags "no DR runbook for VM/KV compromise, broker-key rotation, or panic-halt-all-trading."

**Gap → Stage 1 (SMALL-MEDIUM, writing):** four short runbooks — (a) **panic halt** (kill switch + manually flatten on BitUnix UI if the bot can't); (b) **roll back a buggy deploy that already placed orders** (how to reconcile + flatten + revert); (c) **dispute a broker-side discrepancy** (what evidence the audit trail provides); (d) **credential compromise mid-trade** (revoke key on BitUnix, flatten, rotate). These gate Stage 1 because a real position with no written panic procedure is how a $10 test becomes a bad day. The Tastytrade rotation runbook ([[reference-tastytrade-rotation-runbook]]) is a template for (d).

---

## 11. Operator daily workflow

**Current state: undefined for live.** Paper has no daily ritual.

**Gap → Stage 1/2 (SMALL, writing):** define the daily loop — morning check (broker connection, equity vs system, open positions match, overnight fills/funding, any divergence alerts); which alerts demand *immediate* response (daily-kill, connection-loss, reconciliation mismatch, unusual fill); and what authorizes a manual override (e.g. operator manually closes a position the bot wants to hold — and how that's recorded so the bot doesn't fight it). This is cheap but **must exist before Stage 2** (the operational-tempo stage is, by definition, about the daily workflow).

---

## 12. Committed-not-deployed drift (bitunix-relevant)

**Current state:** no *known* bitunix-specific drift. The three known drift items — tasty_options `main.py` (`94b3129` not on prod), tasty_options `strategies.yaml` block, kalshi_weather `db.py` schema (filed P3 this session) — are **not bitunix-blocking** (see [[committed-not-deployed-recurring-drift]]). The bitunix Phase-2 notifier IS deployed (verified live 2026-05-28).

**But the pattern is the warning:** going live means prod state must be **airtight**. **Gap → Stage 1 (SMALL):** before flipping live, **md5-diff the full bitunix surface against git** — `brokers/bitunix.py`, `agents/divisions/bitunix_futures_observer.py`, `agents/divisions/bitunix_position_reconciler.py`, `agents/paper_trade_replay.py`, `agents/strategies/bitunix_*.py`, `comms/bitunix_lifecycle_notifier.py`, `config/strategies.yaml` bitunix block, `config/risk.yaml`, and the Phase-4 `place_order` code when it exists. No "git has it, prod doesn't" surprises on the execution path.

---

## 13. Pre-existing security findings (live-trading relevance)

From the 2026-05-21 review (`reports/2026-05-21_security_review.md`) + current status ([[project-security-review-2026-05-22]], [[project-security-tracks-fbd-shipped-2026-05-23]]):

| Finding | Status | Live-trading relevance |
|---|---|---|
| **C-1** secrets in plaintext `.env`, incl. `BITUNIX_FUTURES_API_KEY/SECRET` | **OPEN, unrotated** | **DIRECT / Stage-1 BLOCKER.** A live trading key sitting in a dev-box plaintext `.env` is real-money exposure. Before live: rotate, mint a **fresh key scoped to the minimum** (ideally trade-only, no withdrawal permission if BitUnix supports scoping), keep it **only** in Key Vault, confirm it's never echoed (and add it to `register_redact_literal` — the agents noted bitunix keys are *not* currently redacted). |
| **C-3** `strategies.yaml` hot-reload, no validation, flips `auto_execute` | **OPEN** | **DIRECT.** A single file write (or a typo) can flip/keep `auto_execute: true` and clear approval gates with no validation. Tighten before live (pydantic validate + mtime cache + `strategies_yaml_reloaded` audit), or at minimum guard the bitunix live-enable behind something stronger than a YAML bool. |
| **C-5** no DB backup (single SQLite WAL) | **OPEN (confirmed)** | **DIRECT-ish.** Your audit trail, PnL, and (future) tax records live in one unreplicated file. Lose the disk → lose the records you need for reconciliation *and* taxes. Nightly `sqlite3 .backup` → blob before any real-money volume. |
| **H-11** webhook risk gate falls back to `equity=100_000` on snapshot failure | **OPEN** | **DIRECT if bitunix uses the webhook path with that fallback.** Verify the bitunix order path's equity source on `snapshot()` failure fails *closed* (reject), not to a $100k placeholder that would mis-size live orders. |
| **C-2**, **C-6** | **Closed in code** (deployed) | Indirect (Otter/Cypher webhook + deps). |
| **C-4** systemd root | **Remediated** (runs as `azureuser`) | Indirect. |
| **C-7** rejected-webhook secret scrub | **Drafted** ([[project-c7-draft-pending-deploy]]) | Indirect (webhook path). |
| **NEW P1** NOPASSWD:ALL sudo | **OPEN** | General hygiene; indirect to trading safety but tightens blast radius if the VM is touched. |

**Live-critical-path security:** C-1 (rotate bitunix keys + KV-only + redact) is a Stage-1 blocker; C-3 and C-5 should land before Stage 2; H-11 needs a 10-minute verification of the bitunix path's snapshot-failure behavior before Stage 1.

---

## Stage 1 minimum-viable checklist (conservative — err toward "more required")

Before the **first real $10**, ALL of:

1. **Build `place_order` + real fill observation** (§1) — market entry + reduce-only exit, read the actual fill (price/qty/fee) back from the broker. *(LARGE — the root build.)*
2. **Build `cancel_order`** + a **working kill switch** (§2): one command/control that halts new orders, cancels resting orders, and flattens the open position — tested on paper-then-testnet-then-the-$10. *(MEDIUM.)*
3. **Restart-with-open-position resume from broker truth** (§5) — on boot, read broker positions and re-attach, never lose/double-count. *(MEDIUM.)*
4. **Post-trade reconciliation** (§4): after the live close, assert broker fills/position/balance match the recorded order to the cent; alert + halt on mismatch. *(MEDIUM.)*
5. **Real fee/funding capture** on the live trade (§6) + a `fees_paid`/`funding_paid` field, so PnL and (eventual) tax records are real. *(MEDIUM.)*
6. **REST retry/backoff + stale-snapshot health signal**, and a **stuck-order timeout→cancel** (§5). *(MEDIUM.)*
7. **Operational alerts** (§7): connection-loss, daily-kill-triggered, halt, reconciliation-mismatch → Telegram. *(SMALL-MEDIUM.)*
8. **Low-equity alert** + the account actually funded with the test capital (§8). *(SMALL.)*
9. **Decide & implement HITL for the first N live trades** (§2) — recommend a manual confirm on the first handful despite paper's no-HITL setting. *(SMALL.)*
10. **Security C-1:** rotate + KV-only + redact the bitunix keys; **verify H-11** fails closed for the bitunix path. *(SMALL, operator-led + a verification.)*
11. **Panic-halt + credential-compromise runbooks** written (§10). *(SMALL.)*
12. **md5-diff the full bitunix prod surface vs git** before flipping (§12). *(SMALL.)*
13. **Confirm risk caps compute on real equity**, and `flatten_account`/`halt_strategy` actually flatten (§3). *(SMALL, but depends on #2.)*

Explicitly **deferrable past Stage 1** (gaps, but not first-$10 blockers): partial-fill fractional lifecycle, websocket fill stream, per-position multi-interval funding accrual, dashboard reconciliation tiles, full tax-export ledger, concurrency/exposure caps, circuit breakers, clock-skew guard, deposit/withdrawal automation. **Each is a Stage-2 or Stage-3 blocker** — see sections above.

---

## Recommended backlog sequencing

**On the bitunix-live critical path (in order):** §1 broker write methods → §2 kill switch + cancel → §5 restart-resume + REST resilience → §4 post-trade reconciliation → §6 cost accrual → §7 alerts → §10 runbooks. The existing BACKLOG **"bitunix paper-mode cost-accrual (fees+funding)" (P2 MEDIUM)** is squarely on this path (it's checklist #5). Security **C-1** is a parallel hard blocker (do first — it's cheap and gates everything). 

**Independent of bitunix-live (can run anytime):** the kalshi_weather schema P3, tasty_options drift P3, the reconciler 1m-fix (done), the telegram/db-lock fixes (done). The 60-day paper clock + Board sign-off ([[bitunix-paper-clock]]) run on their own track and gate the *decision*, not the build.

**Effort shape:** Stage 1 is dominated by one LARGE item (#1) + ~6 MEDIUM + ~6 SMALL → realistically several focused sessions, with the broker-write build being the long pole. Stage 2 adds ~3 MEDIUM (real-realized-PnL risk feed, periodic reconciliation, partial-fill lifecycle, tax ledger). Stage 3 is concurrency/exposure/circuit-breakers/WS.

---

## Honest unknowns (won't be known until real operation) + mitigations

- **Real fill behavior:** slippage vs the assumed 0.01%, partial fills on market orders, reduce-only edge cases, BitUnix's actual order-ack latency. *Mitigation:* tiny Stage-1 sizing; post-trade reconciliation that halts on any surprise; the kill switch.
- **Funding-debit timing/magnitude** vs the fetched rate; whether the rate fetched == the rate charged. *Mitigation:* reconcile booked funding vs broker-reported (§6) before any volume.
- **API reliability / rate limits under live order flow** (reads are proven; writes are not). *Mitigation:* retry/backoff (#6); single-position Stage 1 keeps call volume trivial.
- **Restart/disconnect during an open live position** — paper-resume is proven, broker-truth-resume is new code. *Mitigation:* #3 + reconcile-on-boot + halt-on-mismatch.
- **Liquidation behavior at leverage** — never exercised. *Mitigation:* Stage-1/2 sizing keeps liquidation far from entry; add a liquidation-distance guard before Stage 3.
- **BitUnix counterparty/withdrawal risk** (offshore exchange). *Mitigation:* keep only the working capital on-exchange; sweep profits; a withdrawal policy (§8) before scaling.
- **Tax/jurisdiction specifics** (self-report assumptions). *Mitigation:* capture complete per-fill records from trade #1; confirm BitUnix's reporting before volume.

**Bottom line:** the strategy may be ready; the *execution and operational system around it is not* — and that's normal, because the live half (Phase 4) was deliberately never built. Stage 1 is a real build with one large item and a dozen smaller must-haves, fronted by a cheap-but-hard security gate (C-1). Because the system fails closed today (`place_order` raises), there's no urgency-driven risk — sequence it deliberately.

---

*Sources: read-only code audit 2026-05-29 (3 parallel Explore passes, file:line cited inline); `reports/2026-05-21_security_review.md`; memories [[bitunix-paper-clock]], [[bitunix-pa-2of3-deploy]], [[project-security-review-2026-05-22]], [[project-security-tracks-fbd-shipped-2026-05-23]], [[committed-not-deployed-recurring-drift]], [[telegram-audit-success-is-confirmed-delivery]]. No code/config/deploy changes made.*
