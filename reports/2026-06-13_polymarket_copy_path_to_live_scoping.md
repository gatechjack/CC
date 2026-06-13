# Polymarket copy-trading — EXECUTION path-to-live scoping

**Date:** 2026-06-13
**Branch:** `polymarket-copy-path-to-live-scoping-2026-06-13` (base `main` `3d8cc1a`)
**Mode:** READ-ONLY investigation. No code change, no prod write, no deploy, no live flip,
no SSH (disclosure per `82fda13`). **Analysis only — seeds future build sessions.**
**Scope:** the copy-**EXECUTION** half (detection → … → placement → management → live-flip).
The copy-**screening** half (which whales to copy; option (c) Phase 1–4; roster review) is
SEPARATE and explicitly out of scope here — see "Relationship to the screening track" below.

---

## 0. Method + accuracy caveat

Mapped via three parallel read-only agents, then **every load-bearing claim re-verified
against the code directly** (file:line below). Two agent-reported items were **discarded as
unverifiable/incorrect** and are NOT used here:
- A prior `reports/2026-05-28_polymarket_copy_live_readiness.md` (+ "item 6/7/8/8a/9",
  "Path A py_clob_client==0.17.5", a "~2–3 week" estimate). **No such report exists** —
  `reports/**/*polymarket*` returns nothing. Fabricated; excluded.
- "The daily-loss aggregator is hardcoded to `polymarket_arbitrage` (copy uncounted)."
  **False** — `risk.py:439/476` sum `actor IN ('polymarket_arbitrage','polymarket_copy_trader')`;
  the comment at `:505` confirms the $-cap binds for both. Corrected below.

The authoritative prior planning is **`BACKLOG.md` Priority 2**, not any separate report.

---

## 1. Execution path, end to end (current state)

| # | Stage | State | Anchor (file:line) |
|---|---|---|---|
| 1 | **Detection** | **WORKS (paper, live-running)** | `main.py:3323` loop, registered `:1039`; polls Polymarket data API every `poll_interval_sec` (default 60, min 15) via `polymarket_copy_trader.py:158 run_scan_cycle`. Free public API, no auth. Cold-start + txhash dedup in the strategy. |
| 2 | **Decision / filter** | **WORKS (paper)** | `selected_whales` loaded `polymarket_copy_trader.py:611`; autopause demotion `:526` (runs ~60s); entry skips on already-resolved market `:345` and stale-price drift `:387`; **every** ProposedOrder hits the risk gate `main.py:3428`. |
| 3 | **Sizing** | **WORKS (paper)** | Tiered copy size by whale bet size `polymarket_copy_trader.py:511 _size_tier_usdc` (config-driven; ~$1/$2/$5), qty normalized to contracts. Built, not stubbed. |
| 4 | **Placement** | 🔴 **MISSING (the core blocker)** | Copy loop terminates at `main.py:3450 log_event(..., "would_have_placed")` + a Telegram "— logged" push. **No `broker.place_order()` call.** Division broker is `paper` (`divisions.yaml:171`); the real `PolymarketBroker` is a `ReadOnlyBroker` with **no `place_order`** (`brokers/polymarket.py:221`, enforced at ABC level `:3`). `PolymarketLiveBroker` **does not exist** — "Live order placement is Phase 3 work" (`brokers/polymarket.py:5-6`). The arbitrage division is also ReadOnly/paper (`main.py:2657`). **No real-money Polymarket placement exists anywhere in the system.** |
| 5 | **Position management / exit** | 🟡 **Exit modeled (paper); attribution unreliable; no reconciliation** | EXIT ProposedOrders are emitted when the whale sells (`_emit_exit:454`); `force_close_whale_positions:660` on demotion. So "follow the whale's exit" IS modeled. BUT round-trip attribution is broken (BACKLOG P1 SELL-pairing: ~99.86% of copy SELLs unpaired; resolver settle-path consumes BUYs). Positions live in `agent_state(whale_state:<wallet>)`; **no broker-truth reconciliation** on restart. |
| 6 | **Safety / risk** | 🟡 **Notional caps real & cover copy; no kill switch** | `risk.py _evaluate_polymarket`: per-position equity-pct, single-market $250 (`:347`), daily-aggregate cap (min 25%·equity / $1000, `:373-378`), total-open-notional, max-open-count (0=off). Daily + open sums span **both** polymarket actors (`:439/476/505`) — enforced on every order. **MISSING:** no account-level drawdown auto-flatten kill switch (the Bitunix **D1** equivalent). Per-whale autopause `:526` is selection-level (demotes a whale), NOT an execution-halt. Caveat: aggregate caps sum `would_have_placed` *intents*; live needs reconciliation vs real fills. |
| 7 | **Live-flip mechanism** | 🔴 **MISSING** | No `execution_mode: paper\|live` for copy in `strategies.yaml` (only Bitunix has it, `:1022`). Division is `broker: paper`, `standby: true` (`divisions.yaml:169-177`). `auto_execute: false` ("Phase 4+ before this can flip live", `strategies.yaml:1702`) — and flipping it does nothing: the loop has **no branch** that places on auto_execute. No HITL routing for copy (loop never calls `data_exec.place()`). Systemd autonomous-live gates Bitunix only (`--brokers bitunix`); no copy gating. Per-division wallet env-var paths exist (`secrets.py:116` → `POLYMARKET_COPY_PRIVATE_KEY/FUNDER`; `assert_live_ready :403`); live signing deps (`py_clob_client`/`web3`/`eth-account`) are **not in requirements.txt**; whether key values are provisioned to the vault is prod state (not checked — read-only). |

**One-line verdict:** detection→decision→sizing are real and running (paper); **everything
downstream of the risk gate is a stub** — the loop logs `would_have_placed` and stops. No live
broker, no place call, no live-flip, no account-level kill switch. A live copy trade cannot fire
today.

---

## 2. THE PUNCH LIST — execution-path blockers (the D1 / HITL / item-4 equivalent)

Ordered as a recommended build/go-live sequence. Size = rough effort (S/M/L). "Safety
prereq" = must land before any real-money order.

| Rank | Blocker | What it is | Size | Safety prereq |
|---|---|---|---|---|
| **E1** | **Build `PolymarketLiveBroker`** | Real order placement: signed CLOB order submission (`place_order` on a real `Broker`, not `ReadOnlyBroker`). Add live signing deps (`py_clob_client`/`web3`/`eth-account`) to `requirements.txt` + lock. The keystone — nothing places without it. | **L** | enabling |
| **E2** | **Wire the copy loop's execute branch** | At `main.py:3450`, on (live + auto_execute) route approved ProposedOrders through `data_exec.place()`/`broker.place_order()` instead of stopping at `would_have_placed`. Include HITL/board-approval routing parity with the Bitunix path. | **M** | partial (HITL) |
| **E3** | **Provision + fund + approve the copy wallet** | Put `POLYMARKET_COPY_PRIVATE_KEY/FUNDER` values in the vault, fund USDC.e, execute on-chain CTF/exchange allowance approvals, extend `assert_live_ready` to check balance + allowance (not just key presence). | **M** | **yes** |
| **E4** | **Account-drawdown kill switch (D1-equivalent)** | Real-equity high-water-mark + auto-flatten/halt on X% drawdown, mirroring Bitunix D1. Per-whale autopause is NOT this. Bitunix treated its D1 as a hard go-live gate. | **M** | **yes** |
| **E5** | **Live position reconciliation** | On startup recover open-position truth from the broker (not just `agent_state`); reconcile aggregate-cap sums against real fills, so a crash can't orphan live positions or mis-count exposure. | **M** | **yes** |
| **E6** | **Live-flip control surface** | Add a controlled flip for the copy division (`execution_mode: live` or `broker: paper→polymarket` + `auto_execute`), gated in the systemd autonomous-live runner (`--brokers polymarket`) with the durable non-interactive LIVE authorization pattern from Bitunix item-4. Land LAST, after E1–E5, then a $1 shakedown. | **S–M** | partial (controlled flip) |

**Critical path:** E1 → (E3 ∥ E4 ∥ E5) → E2 → E6 → $1 shakedown. E1 is the long pole and
gates everything; E4/E5 are the safety gates that must precede real capital; E6 is the
deliberate flip, last.

---

## 3. Relationship to the screening track (parallel prerequisite, NOT in this punch list)

`BACKLOG.md` P2 currently frames its "highest-impact open item" as the **SELL-pairing → option
(c)** work (P&L attribution; ~99.86% unpaired). That is a **screening-accuracy** blocker —
"can we trust which whales are good?" — and is independent of the execution mechanics above.
The execution punch list (E1–E6) is "even with a perfect whale list, can an order fire?" Both
must be true to go live confidently, but they are separate tracks. The screening track is
tracked under option (c) (Phase 1 merged; 2–4 pending) and is out of scope here.

---

## 4. Hard stops / disclosure

- Code change / prod write / deploy / live flip / agent CLAUDE.md commit → **none performed**
  (read-only scoping; this report + a BACKLOG entry are the only artifacts, committed unmerged).
- A stage that does not exist (real placement, live broker, kill switch, live-flip) is stated
  plainly as such — not papered over.
- Disclosure per `82fda13`: no prod, schema, SSH, or DB touched. Findings are from the local
  `main` tree at `3d8cc1a`.

---

*Execution-path scoping artifact — committed unmerged on
`polymarket-copy-path-to-live-scoping-2026-06-13`. Seeds future build sessions.*
