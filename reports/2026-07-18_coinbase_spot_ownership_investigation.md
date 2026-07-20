# Coinbase Spot Account Ownership — Read-Only Investigation

**Date:** 2026-07-18 · **Scope:** read-only (no code/config/state changes)
**Question:** who owns the Coinbase spot balance, and can `coinbase_btc_donchian` safely size off it?

## Bottom line

**No — auto_execute cannot be safely enabled today.** The strategy sizes every order off the **whole live commingled account** (BUY = 100% of account cash, SELL = 100% of account BTC); the account is **actively used for Board treasury flows** (56 exogenous balance changes, cash $0–88K, BTC 0–1.35); and the **single risk chokepoint is deliberately disabled for this strategy *and* blind to the real balance** (evaluates against a synthetic $100K account). There is no allocation, no sub-account partition, and no funds re-validation between signal and fill.

---

## 1. ACCOUNT ACTIVITY ATTRIBUTION

### Full `balance_change` ledger — Coinbase spot, 2026-05-01 → 2026-07-18 (all 56 rows)

Every row's `attribution = "board"` (verified: `SELECT DISTINCT attribution` returns only `board`). Cash/BTC deltas and resulting balances:

| Timestamp (UTC) | Attr | ΔCash | ΔBTC | → Cash | → BTC |
|---|---|---:|---:|---:|---:|
| 2026-05-10T00:02 | board | +48,180.22 | −0.595255 | 87,405.66 | 0.000306 |
| 2026-05-10T06:02 | board | −23,942.07 | −0.000306 | 63,463.60 | 0.0 |
| 2026-05-10T18:02 | board | −54,459.44 | 0.0 | 9,004.15 | 0.0 |
| 2026-05-11T00:02 | board | +79,144.74 | 0.0 | 88,148.90 | 0.0 |
| 2026-05-12T00:02 | board | 0.0 | +0.000302 | 88,148.90 | 0.000302 |
| 2026-05-13T00:02 | board | 0.0 | +0.000614 | 88,148.90 | 0.000916 |
| 2026-05-13T18:02 | board | −88,148.90 | −0.000916 | 0.0 | 0.0 |
| 2026-05-14T00:02 | board | +88,148.90 | +0.001228 | 88,148.90 | 0.001228 |
| 2026-05-14T18:02 | board | +41.89 | 0.0 | 88,190.79 | 0.001228 |
| 2026-05-15T00:02 | board | 0.0 | +0.000303 | 88,190.79 | 0.001531 |
| 2026-05-16T00:02 | board | 0.0 | +0.000313 | 88,190.79 | 0.001844 |
| 2026-05-17T00:02 | board | 0.0 | +0.000316 | 88,190.79 | 0.002160 |
| 2026-05-18T00:02 | board | 0.0 | +0.000316 | 88,190.79 | 0.002477 |
| 2026-05-18T06:02 | board | −12,840.66 | 0.0 | 75,350.13 | 0.002477 |
| 2026-05-18T12:02 | board | +12,149.56 | 0.0 | 87,499.68 | 0.002477 |
| 2026-05-19T00:02 | board | 0.0 | +0.000323 | 87,499.68 | 0.002800 |
| 2026-05-20T00:02 | board | 0.0 | +0.000644 | 87,499.68 | 0.003444 |
| 2026-05-21T00:02 | board | 0.0 | +0.000320 | 87,499.68 | 0.003764 |
| 2026-05-21T18:02 | board | +58.19 | 0.0 | 87,557.88 | 0.003764 |
| 2026-05-22T00:02 | board | −199.99 | +0.000319 | 87,357.89 | 0.004083 |
| 2026-05-22T18:02 | board | +225.54 | 0.0 | 87,582.44 | 0.004083 |
| 2026-05-23T00:02 | board | 0.0 | +0.000325 | 87,582.44 | 0.004408 |
| 2026-05-24T00:02 | board | 0.0 | +0.000326 | 87,582.44 | 0.004734 |
| 2026-05-25T00:02 | board | 0.0 | +0.000323 | 87,582.44 | 0.005057 |
| 2026-05-26T00:02 | board | 0.0 | +0.000320 | 87,582.44 | 0.005377 |
| 2026-05-26T18:02 | board | +102.57 | 0.0 | 87,685.01 | 0.005377 |
| 2026-05-27T00:02 | board | 0.0 | +0.000653 | 87,685.01 | 0.006029 |
| 2026-05-28T00:02 | board | 0.0 | +0.000331 | 87,685.01 | 0.006360 |
| 2026-05-28T18:02 | board | +57.69 | 0.0 | 87,742.70 | 0.006360 |
| 2026-05-29T00:02 | board | 0.0 | +0.000337 | 87,742.70 | 0.006697 |
| 2026-05-30T00:02 | board | 0.0 | +0.000338 | 87,742.70 | 0.007035 |
| 2026-05-31T00:02 | board | 0.0 | +0.000335 | 87,742.70 | 0.007370 |
| 2026-06-01T00:06 | board | 0.0 | +0.000337 | 87,742.70 | 0.007707 |
| 2026-06-02T00:02 | board | 0.0 | +0.000341 | 87,742.70 | 0.008048 |
| 2026-06-02T06:02 | board | +619.47 | +0.004626 | 88,362.17 | 0.012674 |
| 2026-06-03T00:02 | board | −88,362.17 | +0.000728 | 0.0 | 0.013402 |
| 2026-06-03T06:02 | board | +43.96 | +1.335105 | 43.96 | 1.348507 |
| 2026-06-04T00:02 | board | 0.0 | +0.000369 | 43.96 | 1.348876 |
| 2026-06-04T18:07 | board | −43.96 | −1.348876 | 0.0 | 0.0 |
| 2026-06-05T00:02 | board | 0.0 | +0.000380 | 0.0 | 0.000380 |
| 2026-06-05T06:02 | board | +86,010.84 | −0.000380 | 86,010.84 | 0.0 |
| 2026-06-06T00:02 | board | 0.0 | +0.000673 | 86,010.84 | 0.000673 |
| 2026-06-07T00:10 | board | 0.0 | +0.000401 | 86,010.84 | 0.001074 |
| 2026-06-08T00:02 | board | 0.0 | +0.000396 | 86,010.84 | 0.001470 |
| 2026-06-11T06:02 | board | −85,955.01 | −0.001470 | 55.84 | 0.0 |
| 2026-06-11T18:04 | board | +49.03 | 0.0 | 104.86 | 0.0 |
| 2026-06-15T00:02 | board | −3.37 | +1.348777 | 101.49 | 1.348777 |
| 2026-06-18T18:02 | board | +24.37 | 0.0 | 125.86 | 1.348777 |
| 2026-06-22T00:02 | board | −125.86 | +0.004047 | 0.0 | 1.352824 |
| 2026-06-22T18:02 | board | 0.0 | −1.352824 | 0.0 | 0.0 |
| 2026-06-23T06:02 | board | +86,398.18 | 0.0 | 86,398.18 | 0.0 |
| 2026-06-29T00:02 | board | −86,398.25 | 0.0 | 0.0 | 0.0 |
| 2026-06-30T18:02 | board | +85,022.07 | 0.0 | 85,022.07 | 0.0 |
| 2026-07-02T18:02 | board | +40.73 | 0.0 | 85,062.80 | 0.0 |
| 2026-07-09T18:02 | board | +48.11 | 0.0 | 85,110.91 | 0.0 |
| 2026-07-16T18:02 | board | +56.16 | 0.0 | 85,167.06 | 0.0 |

### How attribution is established (and its limit)

- The rows are written by the **Donchian agent's own observer**: `record_balance_snapshot()` diffs consecutive broker snapshots and emits a payload with `attribution: "board"` hardcoded (`coinbase_btc_donchian_agent.py:448`), logged via `main.py:3021` `logger_agent.log_event(agent.name, "balance_change", delta)`.
- `"board"` means **"any balance delta NOT produced by the strategy's own fills"** — the strategy's fills would instead flow through `mark_filled` (`agent:625`). Because the strategy has **0 fills**, *every* delta is exogenous by elimination.
- **Limit of the artifact:** the audit records only that a delta occurred and that it wasn't the strategy. It does **not** capture the real Coinbase-side initiator (manual UI order, portfolio transfer, deposit/withdrawal, Coinbase recurring-buy/rewards, or any other holder of the `COINBASE-API-KEY`). Those cannot be determined from these artifacts. The small recurring ~0.0003 BTC/day accruals (05-12→06-08) with ΔCash=0 look like staking/rewards or a recurring buy, but that is inference, not established by the data.

### The 06-22 acquisition / 06-23 sale

| When | Event | Resulting balance |
|---|---|---|
| ≤ 06-22T00:02 | Account holds **1.352824 BTC**, ~$0 cash | 0 cash / 1.3528 BTC |
| 06-22T18:02 | **−1.352824 BTC** (BTC leaves account — sold or transferred out) | 0 / 0 |
| 06-23T06:02 | **+$86,398.18 cash** arrives | 86,398 / 0 |

- **Who wrote them:** the `balance_change` *audit rows* were written by the Donchian observer (`main.py:3021`), but they only **record** an exogenous move. There is **no `proposed_order` or fill** from any division for this BTC — so the actual acquisition/sale was performed **outside the engine** (Board/manual on Coinbase). The engine cannot name the initiator beyond `board`.
- **Did the agent's state track it?** Two levels, and they diverged:
  - **Sizing input — YES.** `donchian_evaluated` rows show the agent read `held_btc = 1.34877654` (06-20→06-21), `1.3528235` (06-22 00:02→12:02), then `0.0` (from 06-22 18:02). The live broker figure the agent sizes off *did* reflect the Board's BTC appearing and disappearing.
  - **Strategy state — NO.** Internal state stayed `cash` / `cost_basis=null` the entire time. `record_balance_snapshot` never flips state ("State is NEVER auto-flipped here" — `agent:415`); `restore_from_broker` only runs on first bring-up (`agent:348`).
  - **This decoupling is the hazard:** order *sizing* keys off the live broker balance, not the strategy state. Had a SELL signal fired on 06-22, the agent would have proposed selling **1.3528 BTC — the Board's coins** — while its own state said it owned nothing.

---

## 2. STATE COUPLING

### Cash-read path for BUY sizing (`qty = cash / close`)

| Step | File:line | Code |
|---|---|---|
| Snapshot the broker | `main.py:2994` | `snap = await cb.snapshot()` (`cb = data_exec.brokers["coinbase_spot"]`) |
| Extract cash | `main.py:3001` | `cash = float(getattr(snap, "cash", 0.0) or 0.0)` |
| Pass to agent | `main.py:3033–3034` | `agent.on_bar_close(bars, account_equity=…, held_btc=…, cash=cash)` |
| Choose sizing basis | `agent:554` | `sizing_basis = cash if cash is not None else account_equity` |
| Compute qty | `agent:557` | `qty = sizing_basis / current_close` |
| What `snap.cash` IS | `coinbase.py:226–234, 302–308` | sum of `total` balances for `USD/USDC/USDT/DAI/PYUSD` from `fetch_balance()` — **the whole live account's cash** |
| Paper-exec delegates reads | `paper.py:188–189` | `async def snapshot(): return await self._live.snapshot()` (real Coinbase) |

**Read type: a LIVE whole-account balance.** Not a division-scoped allocation, not a strategy-local value. SELL sizing is the mirror: `qty = held_btc` (`agent:595`), where `held_btc` is the whole account's BTC from the same live snapshot (`main.py:2996–2998`).

**Empirical proof of whole-account sizing:** the only order it ever produced (7/15) was `buy 1.309627 BTC @ 64,988.64` = **~$85,113 notional**, versus account cash ~$85,110 at the time — i.e., **100% of the account**.

### Signal-to-fill failure mode (concrete)

1. `qty` is **frozen** at bar close (`agent:557`) into the `ProposedOrder`, then routed through `_run_order` → risk → HITL board approval → execution (`main.py:3087`). The gap can be substantial — the 7/15 order sat until an **~1-hour approval timeout**.
2. **No live-cash re-check exists in that gap.** The risk node evaluates against a **synthetic account** (see §below), and `_run_order` builds the graph state **without an `account` key** (`main.py:4402–4406`), so nothing re-reads the broker between proposal and execution.
3. If the Board moves cash **out** before the fill (were this live):
   - **BUY:** `place_order` computes `quote_size = amount × mark` (`coinbase.py:398–430`); if it exceeds available USD, Coinbase returns INSUFFICIENT_FUNDS and `create_order` raises (`coinbase.py:431–436`) — the order fails loudly.
   - If the Board added **more** cash, the order still deploys the frozen (smaller) qty → under-deploys.
   - **SELL:** `qty = held_btc` captured at bar close; if the Board's BTC left the account by fill time, the sell qty exceeds holdings → rejected; if it's still there, the strategy **sells the Board's BTC**.

The order always acts on a **stale snapshot of a moving, shared balance.**

---

## 3. ISOLATION

### Allocation / sub-account partition for `coinbase_spot`: NONE

- `config/divisions.yaml` coinbase_spot block has **no** `paper_capital` / `allocation` / `sleeve` / capital key:
  ```yaml
  - slug: coinbase_spot
    name: Coinbase BTC HODL
    broker: coinbase
    account_filter: spot
    intent: aggressive
    benchmark: BTC-USD
    target_annual_return: 0.40
    enabled: true
  ```
- The `Division` dataclass (`utils/divisions.py:44–91`) has a `paper_capital` field (`:54`) but it applies only to `broker: paper` divisions (Kelly-sized paper equity); coinbase_spot is `broker: coinbase` and never sets it.
- **Enforcement code: none.** Sizing reads `snapshot.cash` / `held_btc` directly (§2). The whole account IS the sleeve.
- **Risk caps are deliberately relaxed to nothing for this strategy** (`config/risk.yaml:105–121`):
  ```yaml
  coinbase_btc_donchian:
    per_trade_risk_pct: 1.0          # 100% of equity → notional cap effectively off
    per_strategy_daily_loss_pct: 1.0 # effectively off
    max_drawdown_disabled: true      # opts out of the 15% account auto-flatten
  ```
- **The risk gate is also blind to the real balance:** `risk_node` falls back to a synthetic `AccountState(equity=100_000.0)` when state has no `account` key (`ceo_graph.py:334–336`), and `_run_order` never supplies one (`main.py:4402–4406`). So even the % cap is measured against a phantom $100K, not the ~$85K real account.

### Other writers to the same Coinbase spot account

| Writer | In engine? | Path | Can place a REAL Coinbase order? |
|---|---|---|---|
| `coinbase_btc_donchian` (auto) | Yes | `_run_donchian_bar` → `_run_order` → `broker.place_order` | **Not today** (paper-wrapped); **yes** if `coinbase`∈`--brokers` and `coinbase_spot`∈`--live-divisions` |
| `manual_coinbase_spot` (dashboard form) | Yes | Coinbase Spot dashboard manual order; risk override at `risk.yaml:97–103` (5%/trade). *Full form path not traced here.* | Not today (same single broker instance, same paper wrap while not live-selected); deliberate Board action |
| `kalshi_crypto_arb` | Yes | resolves `data_exec.brokers["coinbase_spot"]` but calls **`quote()` only** (`kalshi_crypto_arb.py` via `CryptoSpotProvider`) | **No — read-only** (never `place_order`/`snapshot`); its orders go to **Kalshi** |
| `coinbase_futures` | Yes | separate futures API keys; STUB | **No** — different portfolio/keys; cannot touch spot |
| **External Board / manual** (Coinbase UI, transfers, recurring buys, any `COINBASE-API-KEY` holder) | No (outside engine) | Coinbase directly | **Yes — this is the actual writer today** (all 56 changes) |

- There is exactly **one** `CoinbaseBroker(spot)` instance (`main.py:2437`), shared by reference. Within the engine only the donchian auto-path and the manual dashboard form target it for *orders* — both currently paper-wrapped. Every real balance movement observed is **external/manual**.

---

## 4. VERDICT

### Can `auto_execute` be safely enabled today without a capital-isolation mechanism? **NO.**

| # | Reason | Evidence |
|---|---|---|
| 1 | Whole-account sizing, no partition — BUY = 100% of live account cash, SELL = 100% of account BTC | `agent:554–557, 595`; `coinbase.py:302–308`; §3 (no allocation/enforcement) |
| 2 | Account is actively commingled with Board treasury flows (not hypothetical) | §1 ledger: 56 exogenous changes, cash $0–88K, BTC 0–1.35, repeated in/out transfers |
| 3 | Proven collision windows | 06-22 held the Board's **1.3528 BTC** (a SELL would liquidate it); 06-23 held **$86,398** (a BUY would sweep it); 7/15 BUY was sized at **~$85,113 = the whole account** |
| 4 | Risk gate provides no backstop | caps disabled (`risk.yaml:118–121`) **and** gate uses a synthetic $100K account (`ceo_graph.py:334–336`; no `account` in state `main.py:4402`); no funds re-check pre-fill |
| 5 | State decoupling — the agent cannot tell its own BTC from the Board's | sizing keys off live broker, state never flips on Board deltas (`agent:415, 494–497`) |

Enabling `auto_execute` today would let the strategy transact the **entire commingled account**, colliding with ongoing Board activity, with **no cap and no funds check**.

### Minimum set of changes for safe activation

**Decisions (yours):**
- **D1 — Isolation model.** Dedicate a separate **Coinbase Portfolio/sub-account with its own API key** as the strategy sleeve (cleanest: `snapshot.cash` then reflects only the sleeve), *or* accept a software allocation cap. Recommend the separate Portfolio.
- **D2 — Sleeve size + treasury discipline.** Decide the $ the strategy owns, and whether the Board will stop shuttling treasury through that portfolio (or accept the strategy owns 100% of it).
- **D3 — Cap policy.** Decide whether 100%-in/out with no notional/drawdown cap is acceptable for real money, or re-enable caps (note: re-enabling changes behavior vs the validated backtest).
- **D4 — Signal-to-fill policy.** Accept a market BUY that sweeps sleeve cash, or require a funds-revalidation / re-size step at execution.

**Config:**
- **C1** — If separate portfolio: add its key to KeyVault; point `coinbase_spot` `secret_ref`/`account_filter` at it; add `coinbase` to `--brokers` and `coinbase_spot` to `--live-divisions` (systemd ExecStart); set `auto_execute: true`.
- **C2** — Optionally restore a real max-notional / per-trade cap for `coinbase_btc_donchian` in `risk.yaml` (currently 1.0 = off).

**Code:**
- **K1** — Pass the **real** `AccountState` into the trade-graph for coinbase orders so the risk gate uses real equity, not the $100K phantom (`ceo_graph.py:334` / `main.py:4402`).
- **K2** — Cap BUY `sizing_basis` at `min(live_cash, allocated_capital)` instead of 100% of account cash (`agent:554–557`).
- **K3** — Size SELL off strategy-owned BTC (tracked position), not whole-account `held_btc` (`agent:589–595`).
- **K4** — Re-validate / re-size against live funds at execution time to handle the signal-to-fill gap (`coinbase.py:place_order`).

**Lowest-code safe path:** a **dedicated Coinbase Portfolio (D1)** makes K2/K3 largely moot (the account == the sleeve), so the minimum becomes **separate Portfolio + K1 (real equity into the risk gate) + C1**. Pure-software isolation *without* a separate portfolio requires **K1–K4**.

---

*Sources: prod `data/trading_corp.db` (read-only) on tc-prod-vm; `origin/main` at content-parity with prod. No code, config, or runtime state was modified.*
