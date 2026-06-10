# Bitunix Live-Execution-Path Safety Review — Pre-Flip Adversarial Audit

**Date:** 2026-06-10 · **Session:** operator-supervised, **read-only**, agent read-only SSH (policy `82fda13`)
**Branch:** `bitunix-live-exec-safety-review-2026-06-10` (dedicated worktree off `origin/main` `c8d3902`; unmerged)
**Scope:** the code paths that place, manage, and halt REAL orders with REAL money — NOT a general architecture review.
**Governing question:** *what breaks when real money is live that paper mode never exercised?*
**State:** `origin/main = c8d3902` (= worktree HEAD); prod `execution_mode: paper` (strategies.yaml:1022), service `active` — **still paper, no flip has occurred.**

> ## GO / NO-GO — **NO-GO for a flip as-is.** Two blockers, one of which voids the flip's own premise.
> **B1 — No exchange-resident stop-loss. A live position is protected only by the bot polling and
> placing reduce-only market closes; during any bot downtime (crash, the known ~22-min restart
> hang, broker disconnect) the position's only backstop is exchange liquidation (~4% adverse at
> 25× = full margin loss on that position).** The SL-placement wiring (`modify_position_tp_sl_order`)
> is an unimplemented Phase-4 stub.
> **B2 — Maker-both execution is NOT implemented. The live path places MARKET (taker) orders only.**
> The flip's stated purpose — run limit entries + limit exits to capture the maker fee tier and turn
> the strategy net-positive — cannot happen with this code. A flip today executes at the **taker tier
> (0.09% round-trip) = the net-NEGATIVE expectancy** established in the TP-recalibration analysis.
>
> The order *mechanics that exist* are largely sound (idempotency, fail-closed risk gate, HITL-first-10,
> snapshot-staleness halt, kill switch, restart reconciliation). The gaps are **what was never built
> for live**, not what was built wrong. Detail + a conditional supervised-pilot configuration below.

---

## 0. Scope, constraints, hard stops, disclosure

- **Read-only review.** No code/config/param changes, no prod writes, no live orders. Findings only.
- **Hard stops (status):** any code/config/param change → STOP (**not triggered**); any prod write/live
  order → STOP (**not triggered**); a live order path bypassing the risk gate OR a fail-open risk check
  → flag as BLOCKER and continue (**checked — entries are gated + fail-closed; see A3**); if the live
  path is materially less tested than expected → say so plainly (**it is — see the inventory in A1**).
- **Out of scope:** the TP-ladder (settled); fixing any blocker (separate operator-gated session); the
  fee/maker-fill economics (prior analysis); Polymarket; Day-5 close-out; the flip itself.
- **Disclosure (`82fda13`):** prod contact was one read-only SSH probe (`grep execution_mode` +
  `systemctl is-active`). All other evidence is local source read at `c8d3902`. No writes.

## 1. The two-control live boundary (and a footgun)

A real Bitunix order requires **TWO independent controls**, not one:
1. **CLI `--live --brokers bitunix`** selects the broker *class* (`main.py:1871,1928-1944`):
   `is_live_family=False` → `PaperExecutionBroker(BitunixBroker, PaperBroker)`; only `--live` flips it to
   the raw `BitunixBroker`. **Flipping YAML alone does NOT wire the real broker.**
2. **YAML `execution_mode: live`** (`main.py:325,344`, fail-closed default `paper`) activates the
   observer's live code paths (`bitunix_futures_observer.py:2469`: `is_live = execution_mode=="live" AND
   _yaml_auto_execute_for_bitunix()`).
3. A **third** hot gate: `_yaml_auto_execute_for_bitunix()` is re-read every decision (fail-closed on read
   error) — a hot kill switch that downgrades to the paper path without a restart.

This is good defense-in-depth, but a **footgun**: flipping only `execution_mode: live` (without `--live`)
routes orders to `PaperExecutionBroker` (simulated) while logging them as "live" intent — the operator
could believe they are live when they are not, or vice-versa. **The flip is a coordinated change of
CLI flags + YAML + restart, not a one-line config edit.** Document the exact sequence before flip.

## 2. Audit areas

### A1 — paper→live boundary + untested-in-paper inventory
The live order primitives below exist in deployed code but have **NEVER executed** under `execution_mode:
paper` (paper intercepts at `PaperExecutionBroker`; the `is_live` branch is never taken). This is the
highest-risk surface — every one of these runs for the first time with real money on flip:

- `_place_live()` entry path incl. `data_exec.place()` real entry (`observer:2630, 2812`)
- `_execute_live_exits()` reduce-only real close (`observer:2984, 3075`)
- `BitunixBroker.place_order` / `_observe_fill` / `_assert_position_mode_one_way` / `_ensure_leverage`
  (`bitunix.py:829, ~1060, 1046, 863`) — real REST writes (position-mode set, leverage set, order POST)
- HITL `pending_registry.wait()` gate (`observer:2774`)
- Live startup: `resume_live_positions` / `reconcile_position_state` / 60s sanity poll
  (`reconciler:451,609`; gated `main.py:1574,1616,1654`)
- `_halt_new_orders` divergence latch (`reconciler:579`); real fee accrual from `fill.fee`
  (`observer:2879,2576`); ~15 `live_*` / `*_resume_*` / `position_state_*` audit kinds
- Broker-truth reads `get_pending_positions` (`bitunix.py:958`)

**Paper-assumption leak check:** sizing uses `broker.snapshot()` which returns REAL account equity even
in paper (the wrapper delegates to the real BitunixBroker), so live sizing is calibrated on real equity
from the first snapshot — **no placeholder-equity leak into bitunix sizing** (see A5). One latent issue:
`result_source` mixes `paper_replay_bars` (simulated) and `live_broker_truth` (real fills) in the same
column — a downstream reader that doesn't filter on it would conflate simulated and real outcomes
(flagged in `.scratch/architectural_review.md`). **Finding: CONCERN (audit-provenance), not a placement risk.**

### A2 — order-placement integrity — **CLEAN (for market orders)**
- **Idempotency: solid.** `clientId = tc-<order.id>` is deterministic (`bitunix.py:918-922,954`); a
  30042 duplicate is treated as already-placed success (`bitunix.py:872-880`); POST retries are gated on
  `clientId` presence (`bitunix.py:728`). A retry/reconnect **cannot** double-place. ✓
- **Halt latch in the broker:** `place_order` raises if `_halt_new_orders` (`bitunix.py:849-852`). ✓
- **Position-mode guard:** fail-closed ONE_WAY assert before placement (`bitunix.py:859,1046`). ✓
- **Partial fills (market):** `FillEvent.qty = filled_qty if >0 else order.qty` (`bitunix.py:911`) — a
  partial market fill records the partial; rare for market. **CONCERN, minor** (reduce-only exits + the
  reconciler bound the mismatch).
- **Entry timeout:** market entries that don't confirm hit the `_observe_fill` stuck-order timeout →
  `BitunixStuckOrderCancelled` (cancel). ✓
- **Finding: CLEAN for the market-order path that exists.** (Limit-entry integrity is unbuilt — see A6.)

### A3 — the risk gate — **CLEAN (no entry bypass, fail-closed, real limits)**
- **Every entry routes through `RiskAgent.evaluate()`** before placement, on both entry paths
  (`observer:3249` Phase 3.1, `observer:1520` Phase 3.2). No entry reaches `place_order` ungated.
- **Fail-CLOSED:** `evaluate()` is wrapped `try/except` at the call site; any exception →
  `error_risk_eval` audit + `return` (no placement) (`observer:3253-3257, 1516-1527`). The only
  fail-OPEN in `RiskAgent` is the **Polymarket** aggregate-cap degrade (`risk.py:358-370`) — explicitly
  belt-and-suspenders, **does not apply to bitunix** (atomic caps are load-bearing).
- **Real limits** (`config/risk.yaml`): `per_trade_risk_pct 0.015`, `per_strategy_daily_loss_pct 0.03`,
  `per_account_max_drawdown_pct 0.15`. `correlation_cap 0.7` is a documented **placeholder, not enforced**
  (CONCERN, low — not a live-money gap). Observer adds **tighter** pre-risk caps:
  `EFFECTIVE_RISK_PER_TRADE_PCT 0.005` (0.5%), `DAILY_RISK_KILL_PCT 0.03`, HITL-first-10.
- **Drawdown → auto-flatten:** a `flatten_account` risk verdict routes to `flatten_division` before the
  reject handler, failures re-raised loudly (`observer:3259-3265`). ✓
- **One ungated path:** `_execute_live_exits` calls `place()` with no risk gate (`observer:3075`) —
  reduce-only de-risking; qty = tracked position qty. **CONCERN, accepted** (an exit must not be blocked
  by a risk check), not a bypass blocker.

### A4 — halt / kill / recovery — **kill + detection solid; the downtime gap is B1**
- **Kill switch: solid.** `BitunixBroker.flatten()` = latch `_halt_new_orders` + `cancel_all_orders` +
  `close_all_position` (market flatten), best-effort each step (`bitunix.py:1339-1354`). Wrapped by
  `data_exec.flatten_division`. So an operator (or the drawdown verdict) CAN cancel resting orders +
  flatten. ✓
- **Restart recovery: present.** `reconcile_position_state` compares bot-tracked rows vs broker truth →
  `missing_on_broker` / `orphan_on_broker` → **halt new entries** (exits allowed) + divergence audit
  (`reconciler:451-587`); `resume_live_positions` handles restart cases a/b/c; a 60s sanity poll repeats
  it. Restart double-manage/orphan is **detected and halted**, not silently double-managed. ✓
- **THE GAP (→ B1):** the reconciler's own comments anticipate *"broker auto-closed via liquidation while
  the bot was idle"* (`reconciler:354,707,770`). It **detects the aftermath**; it cannot **prevent** a
  downtime loss because there is no exchange-resident stop. On broker disconnect mid-trade, the SL does
  not exist at the venue — it lives in the bot's poll loop. Combined with the known **~22-min restart
  hang** (RH interactive login blocks startup; memory `[[2026-06-09-robinhood-pickle-regenerated]]`),
  there is a realistic multi-minute window where a 25× position has no stop.

### A5 — equity / sizing under live — **CLEAN for bitunix**
- Bitunix sizes off `broker.snapshot()` **real** equity; the `$100_000` placeholder lives in
  `telegram_commands.py:465` and `main.py:456` (paper-default) — **not on the bitunix live order path.**
- **Fail-closed on snapshot trouble:** `_place_live` runs `_assert_snapshot_fresh()` first →
  `BitunixStaleSnapshot` → `_handle_stale_snapshot` + halt + `return` (no trade) (`observer:2679-2690`);
  `account_equity <= 0` → no trade. The data_exec paper-fallback broker uses `starting_equity=0.0`
  (fail-closed) (`data_exec.py:73-77`).
- **10006 rate-limit (P2):** a failed account snapshot poll trips the staleness gate → **halt, not
  missize.** **Finding: CLEAN** — worst case is a missed/halted trade, not a mis-sized real order.

### A6 — the maker-execution change — **B2 (unbuilt) + a latent naked-order trap**
- **Maker-both is not implemented.** Every order the observer builds is `order_type="market"` (entries
  `observer:2046,2235`; exits `3042`). There is **no limit-order construction, no `limit_price`**, in the
  live path. `entry_is_taker`/`tp_is_maker` are **fee-math knobs only** (`FeeConfig`, for the TP1
  fee-floor) — flipping them changes the fee-floor calc, **not the order type sent to the venue.** ⇒
  **footgun:** an operator could flip those config flags expecting maker execution and still place taker
  market orders.
- **If limit entries were added (to get maker fees), a naked/phantom-position trap exists today:**
  `_observe_fill` returns `FillEvent.qty = order.qty` when `filled_qty == 0` (`bitunix.py:911`), and
  `_place_live` writes the `paper_trade_record` as a fully-open live position regardless of fill status
  (`observer:2866-2880`) — it does not branch on the non-terminal `:new`/`:part_filled` venue suffix. A
  limit entry that hasn't filled would be tracked as open, and the bot would later place reduce-only
  exits against a position that does not exist. There is also **no entry timeout / reprice / cancel** for
  unfilled limit entries. **Building maker entries is a real feature with real failure modes, not a config flip.**

## 3. Ranked findings

**BLOCKERS (must be resolved or explicitly accepted before any flip):**
- **B1 — No exchange-resident stop; downtime backstop = liquidation (25×).** `modify_position_tp_sl_order`
  is a Phase-4 `NotImplementedError` stub (`bitunix.py:1428-1447`); reconciler only logs SL intent
  (`reconciler:14`). Live SL = bot poll-cadence reduce-only closes. Bot down → no stop → liquidation.
- **B2 — Maker execution unbuilt; live = taker (market) only.** Voids the flip's economic premise:
  a flip executes at the 0.09% taker tier = net-negative expectancy (per the TP-recalibration report).

**CONCERNS (monitor / mitigate; not flip-blocking on their own):**
- C1 — Poll-cadence SL granularity (60s reconciler / 3m bars) → slippage beyond intended SL on fast
  moves even when the bot is up; amplified at 25×.
- C2 — Limit-entry readiness gap (phantom position + no entry timeout) — only relevant once B2 is built.
- C3 — `_execute_live_exits` not risk-gated (reduce-only; accepted).
- C4 — `result_source` paper/live mixing (audit-provenance, not placement).
- C5 — partial market-entry fill records full qty (minor; reduce-only + reconciler bound it).
- C6 — `correlation_cap` is an unenforced placeholder (documented).

**CLEAN (verified this session):** idempotency (clientId/30042); risk gate (every entry gated,
fail-closed, real limits + tighter observer caps + drawdown→flatten); HITL-first-10; snapshot-staleness
halt; bitunix sizing on real equity (no placeholder leak); kill switch (`flatten`); restart
reconciliation (missing/orphan → halt); position-mode + halt-latch guards; two-control boundary.

## 4. Recommendation

**NO-GO for an unsupervised flip, and NO-GO for the maker-fee premise, as-is.** The mechanics that exist
are sound; what's missing is exactly what live needs: an exchange-resident protective stop (B1) and the
maker-order path the economics depend on (B2). Fixing either is a separate, operator-gated, §4-Backtester
/ implementation session — out of scope here.

**If the operator chooses a minimal-risk *supervised pilot* anyway** (accepting B1/B2 explicitly), the
lowest-risk configuration:
1. **Supervised only** — operator at the screen for every open position (no downtime stop exists).
2. **Lower the leverage** — 25× makes liquidation (~4% adverse) the de-facto stop during any gap.
   Dropping leverage widens the liquidation distance, the only downtime backstop.
3. **Place a manual catastrophic stop on the exchange** for each open position (the bot will not), or
   explicitly accept liquidation as the cap.
4. **Tiny size** — keep the 0.5% effective-risk cap + HITL-first-10; add a hard per-trade notional cap.
5. **Expect taker fees / net-negative economics** until the maker path (B2) is built and validated — do
   not flip *for* the maker economics, because they aren't wired.
6. **Kill switch rehearsed** — confirm `flatten_division` invocation path before the first live order.
7. **First-N watch:** confirm (a) entry fills land + `paper_trade_record` tagged live; (b) reduce-only
   exits actually fire on SL/TP detection; (c) `reconcile_position_state` reports `matches` (not
   missing/orphan); (d) no restart hang; (e) snapshot-staleness halts behave; (f) the two-control
   boundary is set as intended (both `--live` and YAML).

## 5. Appendix — files reviewed (read-only, `c8d3902`)
`brokers/base.py` (ABC) · `brokers/bitunix.py` (place_order 829, _build_order_body 924, cancel/flatten
1289-1354, modify_position_tp_sl_order STUB 1428) · `agents/risk.py` (RiskAgent 45) · `agents/divisions/
bitunix_futures_observer.py` (_record_placement_outcome 2455, _place_live 2630, _execute_live_exits 2984,
risk gate 3244) · `agents/divisions/bitunix_position_reconciler.py` (reconcile_position_state 451,
resume 590+) · `main.py` (execution_mode 325/344, broker selection 1871/1928, live startup gates
1574-1676) · `agents/data_exec.py` (place 179, flatten_division 412) · `config/risk.yaml` · `config/
strategies.yaml` (execution_mode 1022, fees 1343). Two read-only sub-agents enumerated every
`execution_mode` branch and every order→risk-gate path; their file:line claims were spot-verified by
primary read on the load-bearing lines (risk gate fail-closed, the SL stub, the market-only order type).
