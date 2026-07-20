# Coinbase BTC HODL Division — Current-State Audit

**Date:** 2026-07-18 · **Scope:** read-only · **Division slug:** `coinbase_spot` (display name "Coinbase BTC HODL")
**Method:** local repo `C:\Users\AA Incorporado\cc` (== `origin/main`) + live prod `tc-prod-vm` via ssh (journald + `data/trading_corp.db` read-only).

## TL;DR (the load-bearing findings)

1. **The division is live-but-inert.** The `coinbase_btc_donchian` strategy is `enabled: true`, its 6h scheduler is running, and it evaluates every bar — but it **cannot take a position**. It is not in the engine's `--brokers`/`--live-divisions` list and `auto_execute: false`, so every order must pass HITL board approval.
2. **The one signal it ever produced was auto-rejected.** On 2026-07-15 a valid BUY breakout fired, **passed the risk gate** ("within all risk caps"), then **board_rejected on "approval timeout"** — nobody approved it. Zero fills in ~10 weeks.
3. **Name ≠ behavior.** Despite "HODL", the wired strategy is a **binary CASH↔BTC swing trend-follower** with a full-exit SELL rule. The actual accumulator (`btc_accumulator`) is DEPRECATED/disabled.
4. **~$85.2K real cash sits 100% idle** on the Coinbase spot account. Internal state and the exchange agree (both 0 BTC).
5. **No code/config drift** repo↔prod for this division. Modules untouched since May 2026.

---

## 1. CODE FOOTPRINT

### Files belonging to the division

| Path | Role | Notes |
|---|---|---|
| `config/divisions.yaml` (L133–140) | Division registry entry | `broker: coinbase`, `account_filter: spot`, `enabled: true`, **no `strategy:` key** |
| `config/strategies.yaml` `coinbase_btc_donchian:` (L834–852) | **The wired strategy** | `enabled: true`, `auto_execute: false` |
| `config/strategies.yaml` `btc_accumulator:` (L864+) | Confluence DCA | **DEPRECATED**, `enabled: false` (dead config) |
| `config/strategies.yaml` `lord_otter:` (L483+) | Webhook scalp on `coinbase_spot` | `enabled: false` (dead) |
| `config/strategies.yaml` `crypto_futures:` (L456–467) | Coinbase futures | `enabled: false` (futures division is standby) |
| `trading_corp/brokers/coinbase.py` (23,318 B) | Broker adapter (ccxt) | spot live path implemented; futures = stub |
| `trading_corp/agents/strategies/coinbase_btc_donchian_agent.py` (660 L) | Agent wrapper (state/persistence/orders) | |
| `trading_corp/agents/strategies/donchian_btc.py` | Decision math (`evaluate_donchian`) | referenced by the agent |
| `trading_corp/main.py` | Orchestration: instantiate (L349), schedule (L1225–1227), `_scheduled_donchian_loop` (L2927), `_run_donchian_bar` (L2975), `_fetch_recent_btc_6h_bars` (L2897) | |
| `scripts/backtest_donchian.py`, `scripts/walkforward_donchian.py` | Research (backtest) | not wired to runtime |
| `scripts/backtest_btc_accumulator.py`, `scripts/walkforward_btc_accumulator.py` | Research (deprecated approach) | not wired |
| `scripts/pine/coinbase_btc_hodl.pine` | TradingView Pine | research artifact |

**Scheduler/cron:** none. There is **no separate systemd unit or cron entry** for this division — it runs as an in-process `asyncio` task inside `trading-corp.service`, waking at 00/06/12/18 UTC + ~2 min.

### git log — last commits touching division paths

Per-file last-touch (the combined `-20` rolling log over `strategies.yaml`/`main.py` is dominated by unrelated SFP/Kalshi commits):

| File | Last commit | Date | Subject |
|---|---|---|---|
| `config/divisions.yaml` | `79cbbef` | 2026-07-01 | kalshi(k5) LIVE FLIP (not coinbase-specific) |
| `config/strategies.yaml` | `220495c` | 2026-07-11 | sfp: ARM ps_trail30 (not coinbase-specific) |
| `trading_corp/brokers/coinbase.py` | `606254e` | 2026-05-01 | Bulk-track trading_corp under git |
| `coinbase_btc_donchian_agent.py` | `78e57a0` | 2026-05-09 | donchian: observe Board-driven balance changes |
| `scripts/backtest_btc_accumulator.py` | `2659c81` | 2026-06-20 | recover five_factor backtest impl |
| `scripts/walkforward_btc_accumulator.py` | `cd26a75` | 2026-05-08 | BTC Accumulator Phase 1.6 walk-forward |
| `scripts/walkforward_donchian.py` / `backtest_donchian.py` | `072a484` | 2026-05-08 | Donchian Channel Breakout research |
| `scripts/pine/coinbase_btc_hodl.pine` | `c7f2fc6` | 2026-05-09 | pine ASCII-only fix |

**The division's own executable code (broker, agent, pine, scripts) has not changed since May 2026 (~10 weeks).** Newer commits on `strategies.yaml`/`main.py` are unrelated SFP/Kalshi edits.

### Working tree & prod parity

- **Working tree:** clean for all division paths. No uncommitted diffs; no untracked `*coinbase*`/`*donchian*`/`*accumulator*`/`*hodl*` files. (Untracked scratch files elsewhere — `# GT_Jack's… .txt`, `Images/` — are unrelated.)
- **Branch/sync:** local `main` == `origin/main` (0 commits ahead, 0 behind).
- **Prod:** plain-file deploy (**not a git repo**). Content parity vs `origin/main` verified by md5:
  - `coinbase.py` `343d10d1…` and `strategies.yaml` `4a42618e…` — **byte-identical** local==prod.
  - `coinbase_btc_donchian_agent.py` and `divisions.yaml` differ raw, but **CR-stripped md5 matches exactly** (`98094520…`, `188794ad…`) — the only difference is local Windows CRLF vs prod LF. **No content drift.**
  - Prod config blocks read directly confirm the same values (`enabled: true`, `auto_execute: false`, entry 20 / exit 6 / trend 168 / gran 21600).

---

## 2. RUNTIME STATE

### Process

Single monolith. There is no per-division process.

```
● trading-corp.service — active (running) since Sat 2026-07-18 23:01:40 UTC
  Main PID: 261332 (xvfb-run) → 261345 (python -m trading_corp)
  ExecStart: … -m trading_corp --live --brokers bitunix robinhood kalshi \
             --live-divisions bitunix_sfp robinhood_pead bitunix_futures kalshi_copy_trading
```

- **`coinbase` is NOT in `--brokers`; `coinbase_spot` is NOT in `--live-divisions`.**
- VM uptime 18 days; engine last restarted **2026-07-18 23:01:40 UTC** (prior restarts 21:33 today, and PID 217996 earlier — consistent with the RH-auth deploy work in the memory log).
- Division scheduler confirmed online: `Donchian scheduler online: wakes at 00/06/12/18 UTC + ~2min (strategy enabled=True, auto_execute=False)`.

### Paper vs live — it is PAPER (with real read-only market data)

| Evidence | Source |
|---|---|
| `coinbase` absent from `--brokers` ⇒ `family_live_capable=False` ⇒ `is_live_division=False` | main.py:2388–2391 |
| `Registered paper-exec broker for division=coinbase_spot (paper=True)` | boot log 23:01:45 |
| `auto_execute: false` | strategies.yaml + boot log |
| 7/15 order `execution_mode = paper` | `proposed_order` row |
| **Reads are real:** `CoinbaseBroker(spot) connected (markets_loaded=True)` | boot log 23:01:51 |

So the division wraps a **real read-only CoinbaseBroker in a PaperExecutionBroker** — live balances/quotes, simulated fills (main.py:2445–2446).

### Logs

Recent cadence (every 6h, all `skip` — no breakout):
```
Jul 18 18:02:03 … donchian_evaluated {"decision":"skip","reason":"no breakout @ 2026-07-18T12:00:00: close=64443.32 <= 20-bar high=65559.50", "held_btc":0.0, "account_equity":85167.06}
```

**ERROR/CRITICAL/traceback in the coinbase/donchian path, last 14 days: 1** — and it is a WARNING, not an error:
```
Jul 09 00:45:04 WARNING trading_corp.brokers.coinbase: Coinbase fetch_balance failed:
  "circuit breaker open … no healthy upstream" (earn_center_program rates)
```
Transient Coinbase-side outage, self-recovered. No donchian scheduler exceptions.

---

## 3. POSITIONS & CAPITAL

### Internal state vs exchange — they AGREE

| Metric | Strategy internal (`agent_state`, upd 2026-07-18T18:02) | Coinbase account (engine live read, 2026-07-18T18:02) |
|---|---|---|
| BTC position | **0.0 BTC** (`state: cash`) | **0.0 BTC** |
| Cost basis | `null` | n/a |
| Cash | `last_known_cash = 85,167.06` | `85,167.06` |

**No mismatch** — both show 0 BTC. (An independent fresh Coinbase API pull was not performed to avoid credential handling; the figure above is the engine's own read-only snapshot, refreshed each bar and hours old.)

### Capital allocated vs deployed vs idle

- **Allocated:** no explicit `$`/`paper_capital` field for `coinbase_spot`; the division operates on whatever the real Coinbase spot account holds. `target_annual_return: 0.40` is a reporting label only.
- **Deployed by the strategy:** **$0** (0 BTC; strategy in CASH).
- **Idle:** **~$85,167 (100% cash).**

### Trade / fill history (strategy)

| Table | For `coinbase_btc_donchian` | Range |
|---|---|---|
| `proposed_order` | **1** (board_rejected, never filled) | 2026-07-15 |
| Actual fills | **0** | — |
| `paper_trade_record` | 0 (strategy doesn't write PTR) | — |
| `audit_event donchian_evaluated` | 282 | 2026-05-09 → 2026-07-18 |
| `audit_event balance_change` | 56 (Board treasury moves) | 2026-05-10 → 2026-07-16 |
| **Total fees paid by strategy** | **$0.00** (no fills) | — |

**Account-level activity is Board treasury churn, not the strategy** (all `attribution: board`, strategy `state_at_observation: cash` throughout). Cash ranged **$0 → $88,362**; BTC ranged **0 → 1.3528 BTC**. Example: on 2026-06-22 the account briefly held 1.3528 BTC (~$85K) then sold to cash on 06-23. The Donchian strategy managed none of this.

---

## 4. STRATEGY LOGIC AS-BUILT

**Wired strategy = `coinbase_btc_donchian` — a binary CASH↔BTC swing trend-follower (NOT accumulate/HODL).**

| Rule | Value (config `donchian:`) |
|---|---|
| **BUY → 100% BTC** | `close > max(high, last 20 bars)` (~5d breakout) **AND** `close > SMA(168)` (~42d trend filter) |
| **SELL → 100% cash** | `close < min(low, last 6 bars)` (~36h breakdown) |
| Cadence | 6h bar close, 00/06/12/18 UTC |
| BUY sizing | `qty = cash / close` (deploys 100% of cash) — agent L557 |
| SELL sizing | `qty = held_btc` (closes 100%) — agent L595 |

- **Sell/trim logic:** YES — full exit on breakdown (agent L588–621). No partial/scale/trailing. So the division name "HODL" is a **misnomer** for the as-built logic.
- **Accumulator:** `btc_accumulator` (confluence-scored DCA) is DEPRECATED and `enabled: false` — parsed but wired to nothing.

### Risk chokepoint interaction

`_run_donchian_bar` routes each `ProposedOrder` through **`_run_order(graph, …)`** = the standard LangGraph HITL trade-graph → `RiskAgent.evaluate()` (the single risk chokepoint) → **board-approval node** (main.py:3087). Because `auto_execute: false`, every order requires an approval click — there is **no auto-execute bypass** (unlike `polymarket_arbitrage`, which the Board approved to skip HITL). `mark_filled` only runs on `final_status == "filled"` (main.py:3106).

**Proof from the 7/15 order** (`proposed_order` row):
```
side=buy qty=1.3096 BTC @ 64988.64  status=board_rejected
risk_reason="within all risk caps"  board_reason="approval timeout"  fill_price=NULL  execution_mode=paper
signal: "breakout @ 2026-07-14T18:00: close=64988.64 > 20-bar high=64918.00 AND close > SMA(168)=62712.89"
```
Risk passed; the approval timed out unattended → rejected → no position (not even paper).

### Hardcoded values vs config

| Location | Constant | Status |
|---|---|---|
| main.py:2908 | `timeframe="6h"` | Hardcoded **independent** of config `granularity_seconds: 21600`. Currently consistent; latent drift if granularity is ever changed in YAML (fetch would still pull 6h). |
| main.py:2912 | `granularity_sec = 6 * 3600` | Same independent hardcode (in-progress-bar filter). |
| main.py:2989 | `limit=200` | Hardcoded bar window (yields `bars_considered=199`); no config equivalent — sufficient for the 168-bar filter. |
| agent L191–195 | `trend_filter_lookback` default `None` | If the YAML key were absent it would **silently disable the trend filter**; config present (168) so benign today. |

No TODO/FIXME/HACK contradictions in the donchian agent. `coinbase.py:342` raises `NotImplementedError` only for futures/stub modes (a guard) — the spot-with-creds path is implemented.

---

## 5. PERFORMANCE

### Strategy P&L since inception (2026-05-09)

- **Realized: $0.00** — 0 completed round-trips (0 fills; the single 7/15 signal was board_rejected).
- **Unrealized: $0.00** — holds 0 BTC (CASH).
- The account's cash drift from ~$88.3K peak to ~$85.2K is **Board treasury movement** (and fees on the Board's own manual BTC churn), **not attributable to the strategy**.

### Benchmark — cash vs naive DCA vs HODL over the same window

Window: 2026-05-09 (`$80,374`) → 2026-07-18 (`$64,443`). BTC fell **−19.8%**.

Weekly closes used for DCA (first eval of each ISO week):

| Date | BTC close | Date | BTC close |
|---|---|---|---|
| 2026-05-09 | 80,374 | 2026-06-15 | 65,707 |
| 2026-05-11 | 82,200 | 2026-06-22 | 63,236 |
| 2026-05-18 | 77,408 | 2026-06-29 | 59,474 |
| 2026-05-25 | 76,976 | 2026-07-06 | 63,580 |
| 2026-06-01 | 73,575 | 2026-07-13 | 63,740 |
| 2026-06-08 | 63,303 | (final 07-18) | 64,443 |

| Approach (same total capital) | Avg entry | Value at $64,443 | Return |
|---|---|---|---|
| **Donchian strategy (actual)** | — (never deployed) | cash unchanged | **~0%** |
| Flat weekly DCA (11 buys) | ~$69,327 | $10.23 per $11 invested | **−7.0%** |
| Lump-sum HODL (buy 05-09) | $80,374 | — | **−19.8%** |

**Interpretation:** staying in cash coincidentally beat both DCA and HODL in this down/choppy window — but **not through skillful trading; through total inaction**. The strategy never traded, and the one time it tried, HITL killed it. In an up-market the same inertness would *miss* the gains. Do not read this as strategy alpha.

### Fee drag

**$0.00 / ~$85,167 = 0.0%** — the division has incurred no trading fees because it has never traded.

---

## 6. OPEN ITEMS

- **TODO/FIXME/HACK:** none in the donchian agent. `coinbase.py:342` `NotImplementedError` is a futures/stub guard, not an unfinished spot path.
- **Tests:** `tests/test_coinbase_btc_donchian_agent.py` (16) + `tests/test_btc_accumulator_confluence.py` (20) → **36 passed, 0 skipped/xfailed/failed**. ⚠ The 20 accumulator tests cover the **DEPRECATED, disabled** `btc_accumulator` — test maintenance on dead config.
- **Half-finished / stubbed / dead-code:**
  - `btc_accumulator` config (~200 lines of factors/guards) — DEPRECATED, wired to nothing.
  - `lord_otter` (division `coinbase_spot`) — disabled/dead.
  - `coinbase_futures` division (standby) + `crypto_futures` (disabled); futures broker connects as **STUB** ("FCM impl pending").
  - Pine + 4 backtest scripts — research artifacts, not wired.
  - **The division itself is operationally half-finished:** fully built, running, and evaluating, yet structurally unable to take a position (auto_execute off + not live-selected + no HITL approver). Proven by the 7/15 rejection.

---

## 7. NEXT ACTIONS (ranked, highest-impact first)

| # | Action | Why | Type |
|---|---|---|---|
| 1 | **Decide the division's purpose.** ~$85K real cash idle; 0 fills in 10 weeks; the one real signal auto-rejected. Activate, repurpose the capital, or decommission. | Capital is doing nothing and the strategy can't act. | **Decision** |
| 2 | **If activating: fix the wiring.** Either (a) `auto_execute: true` **and** add `coinbase` to `--brokers` + `coinbase_spot` to `--live-divisions`, or (b) guarantee a reliable HITL approver so board approval doesn't time out. | Under current wiring every signal dies (proven 7/15). | Config + deploy (+ risk decision) |
| 3 | **Resolve the name/behavior mismatch.** "HODL" division runs a full-exit swing trend-follower. Pick intent: accumulate (donchian is wrong) or trend-follow (rename). | Prevents mis-set expectations and wrong future edits. | **Decision** |
| 4 | **If it stays HITL-gated indefinitely, set `enabled: false`** (or accept as shadow tracker). | Stops burning 6h scheduler cycles + live Coinbase reads generating dead signals. | Config |
| 5 | **Fix latent granularity hardcode** (main.py:2908/2912 hardcode 6h independent of `granularity_seconds`). | Silent drift if granularity is ever reconfigured. | Code (low) |
| 6 | **Prune or justify the deprecated `btc_accumulator` config + its 20 tests.** | Dead config with live test maintenance. | Code (low) |
| 7 | **Clarify account ownership.** The Coinbase spot account is used for manual Board treasury movement (cash $0–88K, BTC 0–1.35) while the strategy assumes it owns account state. | Latent correctness hazard; the balance-change observer already fires constantly. | **Decision** |

---

*All figures sourced from prod `data/trading_corp.db` (read-only) and `journalctl -u trading-corp` on tc-prod-vm, plus `origin/main` at parity with prod. No code, config, or runtime state was modified during this audit.*
