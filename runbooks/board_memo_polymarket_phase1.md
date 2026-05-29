# Board memo — Polymarket Phase 1 ship + Phase 2 open questions

**Date:** 2026-05-09 20:13 UTC
**Status:** Phase 1 SHIPPED to prod. Inert (stub mode) until KV secrets land.

---

## What landed

A new "Polymarket" investment-type group on the home dashboard with two
tiles:

- **Polymarket Arbitrage** — real read-only adapter; reads USDC balance
  from Polygon RPC, open YES/NO positions from `data-api.polymarket.com`,
  last-trade prices from `clob.polymarket.com`. STANDBY today; goes live
  on the next service restart after the KV secrets land.
- **Polymarket Copy Trading** — paper-fallback STANDBY placeholder for
  the Phase 4+ copy-trading strategy. Stays $0 until that strategy ships.

The architecture is read-only-by-ABC-not-runtime-flag: `PolymarketBroker`
subclasses a new `ReadOnlyBroker` ABC. There is no `place_order` method
on the class. A code path that tries to place orders against the
Polymarket adapter is a **static type error**, not a runtime exception.
This was the migration TODO from CLAUDE.md §7 sharp edges; it's now
strictly possible (Fidelity rebase deferred to a separate cleanup item).

Phase 0.5 (EU egress proxy) was scoped, smoke-tested, and ruled
unnecessary. The 2026-05-09 smoke test verified Polymarket's read APIs
serve `tc-prod-vm`'s US-east IP without geo-block. **~$12/mo + ongoing
operational surface saved.** Caveat: only READ endpoints were tested;
Phase 3 trade placement may still hit write-path geo-checks (tracked as
task #31; existing runbook is the starting point if a proxy is ever
needed).

## What you do next (your side)

When ready — no rush, no time pressure on this:

1. **Generate the wallet** (~30 seconds, on your laptop):
   ```bash
   python3 -c "
   from eth_account import Account
   acct = Account.create()
   print('address:    ', acct.address)
   print('private key:', acct.key.hex())
   "
   ```
   Save both lines somewhere secure.
2. **Sign up at alchemy.com** (free), create a "Polygon Mainnet" app,
   copy the HTTPS URL.
3. **Fund** the address from Coinbase: $500 native USDC on Polygon +
   ~$5 MATIC for gas. Native USDC contract is `0x3c499c542cef5e3811e1192ce70d8cc03d5c3359`
   (NOT USDC.e bridged).

   > ⚠ **CORRECTION (2026-05-29):** the native-USDC instruction above is **WRONG for CLOB
   > trading.** On-chain `getCollateral()` on both live Polymarket exchange contracts
   > (std `0x4bFb…982E`, negRisk `0xC5d5…f80a`) returns **USDC.e bridged
   > `0x2791Bca1f2de4661eD88A30C99A7a9449Aa84174`** — the CLOB settles in USDC.e, not native
   > USDC. The $500 funded here as native USDC **cannot back a CLOB order as-is**; it must be
   > converted to USDC.e (or the wallet re-funded with USDC.e) before Phase 3. The "Polymarket
   > migrated to native USDC" belief was never verified against the live exchange.
   > **Gas:** CLOB order placement is *gasless* for the user (orders are signed off-chain and
   > settled by Polymarket's operator); MATIC/POL is consumed only by one-time approvals and
   > per-resolution `redeemPositions`, so "~$5 for gas" is generous (the 98 POL actually funded
   > is ample). Full detail: `reports/2026-05-29_polymarket_live_prep_groupB_spike.md`.
4. **Upload to KV** (hyphens not underscores — KV constraint):
   - `POLYMARKET-PRIVATE-KEY` = the private key from step 1
   - `POLYMARKET-FUNDER-ADDRESS` = the address from step 1 (signer == funder, EOA pattern)
   - `POLYGON-RPC-URL` = full Alchemy URL with `/v2/<api-key>` path
5. **Tell me when done** so I can request a service restart on prod —
   the next restart picks the keys up automatically.

## Open questions before Phase 2 (arbitrage scanner) starts

These need your call before I write strategy code:

### 1. Risk caps — confirm or override

Per the original Polymarket scope prompt:

| Cap | Proposed |
|---|---|
| Max % division equity per position | 5% (forward-compat — fixed $1 sizing dominates today) |
| Max single-market notional | $250 (forward-compat) |
| Min market 24h volume | $50,000 |
| Max bid-ask spread | 3¢ |
| Min time-to-resolution | 24 hours |
| Implied-probability bounds | reject < 5% or > 95% |
| Daily aggregate Polymarket exposure | 25% of division equity, capped at $1,000 USDC |
| Aggregate cap on open positions | $1,000 USDC notional |
| Per-position size during shakedown | $1 USDC (fixed) |

These all live in `config/risk.yaml` under `polymarket.*` keys when
Phase 2 ships. Let me know if any need to change before I bake them in.

### 2. Time horizon (short-tail vs long-tail)

Original prompt said Phase 2 is short-tail only (markets resolving ≤ 7
days). Long-tail (≤ 30d, ≤ 90d, etc.) deferred to a later phase.

Confirm 7-day cap, or set a different boundary for Phase 2.

### 3. Research firm consultation rate-limit

Phase 2's scanner pulls market list, deterministic-filters, then asks
Research firm's Thesis product for an LLM probability estimate on
survivors. The Thesis consult costs ~$0.05-0.20 each (depending on
which Anthropic model it uses) and takes 5-30s.

To bound cost: cap K markets per cycle. Original prompt suggested K=10.
At every-30-second poll cadence, that's 10 × ($0.05 to $0.20) × 2880
cycles/day ≈ $1,400 to $5,800/day if every cycle hits the cap.

Two ways to bound:
- **(a)** Hard K=10 per cycle with longer cooldown TTL per market (6h).
  Realistic per-day Thesis calls = ~50-200 unique markets, ~$2-50/day.
- **(b)** Lower K (e.g. K=3) with shorter cooldown to revisit more often.
  Lower per-day cost ceiling but slower coverage.

Lean **(a)**. Confirm or override.

### 4. Phase 2.5 Backtester scope (already approved minimal-viable)

You greenlit minimal-viable Phase 2.5: replay-only, no Monte Carlo / no
slippage / no time-decay. Keeping that locked in. Just flagging here as
context — it's the Phase-3 gate.

### 5. Polymarket API rate-limit posture

Polymarket's CLOB has a documented rate limit (varies by endpoint;
~10-30 req/sec for public reads). Phase 1 broker doesn't push it; Phase
2 scanner could approach it if the universe filter is loose. I'll bake
in defensive rate-limiting (httpx-level concurrency cap + backoff on
429) when Phase 2 starts. Flagging so you know it's on my radar.

## What's NOT done that you might expect

- **No Phase 0 wallet creation.** That's your manual side. Code lands
  inert until you upload.
- **No `polymarket_arbitrage` strategy code.** That's Phase 2 (separate
  ship after this memo + your answers above).
- **No live order placement.** That's Phase 3, gated on Backtester
  verdict + Board memo.
- **No EU egress proxy.** Confirmed unnecessary by the 2026-05-09 smoke
  test. Re-evaluate at Phase 3 if write-path geo-checks block live
  orders.

## Observability + rollback

- Deploy log entry: `runbooks/deploy_log.md` 2026-05-09 20:13 UTC.
- Backup tarball: `/home/azureuser/backups/pre-polymarket-phase1-20260509.tar.gz` (4 modified files) + `secrets.py.pre-polymarket-phase1-20260509.bak` (Phase 0 was caught at deploy time — see deploy_log for the lesson).
- Rollback recipe documented in the deploy_log entry.

---

*Awaiting your answers on §§1-3, 5 before I start Phase 2 strategy
code. No urgency — Phase 2 doesn't ship before you fund the wallet and
confirm the gates.*
