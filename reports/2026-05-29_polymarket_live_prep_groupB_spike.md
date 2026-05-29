# Polymarket Live-Build Prep — Group B (silent-failure ops) + Item 8a SDK spike

**Date:** 2026-05-29
**Branch:** `polymarket-live-prep-2026-05-29`
**Scope:** Prep only off `reports/2026-05-28_polymarket_copy_live_readiness.md`. **Group C NOT started.**
Neither track commits to going live. No on-chain transactions. Prod touched read-only (geo-probe + balance/allowance `eth_call`s) via `az vm run-command`. No code in `trading_corp/`.
**Sunday whale-scoring gate untouched.**

---

## Headline

Both tracks are **mechanically clean** — Path A signs end-to-end, no geo-block — but the session **caught a real, capital-relevant problem before any build:** the **live CLOB exchange contracts settle in USDC.e (`0x2791…`)** — confirmed on-chain via `getCollateral()` — **while the live wallet is funded with 500 native USDC (`0x3c49…`) and 0 USDC.e.** The "Polymarket migrated to native USDC" premise (Board memo + deploy_log + broker code) is **wrong for the CLOB**; pinned py_clob_client 0.17.5's USDC.e collateral config was correct all along. **The wallet cannot fund a single CLOB order as-funded** — the 500 native USDC must be converted to USDC.e (or the wallet re-funded with USDC.e), and item-4 approvals must target USDC.e.

---

## Track 2 — Item 8a SDK signing spike: **PASS (Path A signs end-to-end today)**

Ran in a throwaway venv (`C:\Users\AA Incorporado\.polymarket_spike_venv`), no prod/requirements touched. Script at `scripts/spike_polymarket_signing/spike_sign.py`.

- All four pins installed clean, no conflicts: `py_clob_client==0.17.5`, `py_order_utils==0.3.2`, `web3==6.11.0`, `eth-account==0.13.1`.
- Constructed `ClobClient` + `OrderArgs(token_id, price=0.50, size=5, side=BUY)` against a **live** market (`get_sampling_markets()`), called `create_order()`, got a valid EIP-712 signature. **`post_order` NOT called.**
- Arithmetic sane: `makerAmount=2500000` (0.50×5 USDC), `takerAmount=5000000`, `signatureType=0` (EOA), top-level `signature` present.
- **SDK shape note:** constructor is `ClobClient(host, chain_id, key, creds, signature_type, funder)` — `chain_id` precedes `key` (used kwargs; no positional risk). Everything else matched the readiness-report shape. No blocking API drift.

**Caveats (for the eventual prod lockfile, not the path decision):**
1. Spike ran on **Python 3.14.4 / Windows**; prod target is **3.12 / Linux x86_64**. The lockfile must be compiled for the prod target (per `reference_uv_pip_compile_cross_platform`), not lifted from this run.
2. `web3==6.11.0` pulled **`eth-abi==6.0.0b1` (a beta)** transitively on 3.14 — pin/verify the full transitive graph for reproducibility.
3. **Signing success is necessary but not sufficient.** `create_order()` signs locally and succeeds regardless of whether the wallet holds the right collateral. Collateral correctness is only enforced at `post_order` (not exercised) and on-chain at settlement. The Track 1b finding (wallet holds native USDC, CLOB needs USDC.e) means a *posted* order would currently fail to settle even though signing passed.

---

## Track 1a — Polygon write-path geo-check: **GO (no geo-block on authed surface)**

Closes the deferred **task #31** from `runbooks/eu_proxy_smoke_test.md` ("re-run against an authed endpoint before Phase 3"). Probed from tc-prod-vm.

- **Egress:** `20.51.145.253` — Washington VA, **AS8075 Microsoft (Azure US-East)**, US.
- Control public read `GET /markets?limit=1` → **200** (read path fine, matches 2026-05-09).
- Authed endpoints, **no** signature header:
  - `GET /data/orders` → **401** `{"error":"Unauthorized/Invalid api key"}`
  - `GET /data/trades` → **401** (same)
  - `GET /balance-allowance?asset_type=COLLATERAL` → **401** (same)
  - `GET /auth/api-keys` → **401** (same)
- CF headers: `server: cloudflare`, `cf-ray: …-IAD`, `cf-cache-status: DYNAMIC`. **No `cf-mitigated`, no 403/451, no challenge page.**

**Interpretation:** the authed/write CLOB surface is reachable from the US Azure IP — the 401 is a clean **application-layer** auth rejection (request reached the CLOB app), not an edge/geo block. **The EU-proxy architecture is NOT triggered.**

**Residual (documented, not a blocker):** jurisdiction enforcement at actual order *submission* (POST `/order` with a valid signed order) is only fully provable at the $1 shakedown — out of scope here (no order placement).

---

## Track 1b — USDC→CTF allowance scoping + the collateral fork

### Authoritative contract addresses (dumped from pinned `py_clob_client==0.17.5` `get_contract_config(137, …)` — the exact client a Path A build uses):

| Role | Address |
|---|---|
| CTF Exchange (standard) | `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` |
| NegRisk CTF Exchange | `0xC5d563A36AE78145C45a50134d48A1215220f80a` |
| **Collateral (per 0.17.5)** | **`0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` (USDC.e bridged)** |
| Conditional Tokens (ERC-1155) | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` |

### The approve() call shape (per wallet, one-time, on-chain — NOT executed):

1. **ERC-20** `approve(address spender, uint256 amount)` (selector `0x095ea7b3`) on the **collateral token**, with `spender` = each exchange contract that pulls collateral:
   - `0x4bFb…982E` (std exchange)
   - `0xC5d5…f80a` (negRisk exchange)
   - *(likely also the NegRisk Adapter `0x78769D50Be1763ed1CA0D5E878D93f05aabff29e` for neg-risk conversion/redemption — **NOT** in the 0.17.5 `ContractConfig` I dumped; confirm against the live approval set in the build, don't assume.)*
   - `amount` = unlimited (`2^256-1`) is the MIT-reference convention; a bounded amount is the conservative alternative.
2. **ERC-1155** `setApprovalForAll(address operator, bool approved)` (selector `0xa22cb465`) on the Conditional Tokens `0x4D97…6045`, with `operator` = each of the same contracts, `approved=true`.

**Gas:** each `approve`/`setApprovalForAll` ≈ 46k gas; ~4–6 one-time txs ⇒ ≈ 0.01–0.015 POL total at 30–50 gwei. Trivial vs the 98 POL on hand.

### On-chain state of the live wallet (`polymarket_arbitrage` funder `0x2FC7…DA11`, chainId 137):

- **nonce = 0** → wallet has **never sent an on-chain tx**. No approvals exist; **item-4 approve is confirmed still required.**
- `allowance(USDC.e → std exchange)` = **0**, `(→ neg exchange)` = **0**.
- `CTF.isApprovedForAll(→ std)` = **false**, `(→ neg)` = **false**.

### ⚠ RESOLVED — collateral token mismatch is a real blocker (wallet mis-funded)

On-chain `getCollateral()`/`getCtf()` against the live exchanges (public RPC `polygon-bor-rpc.publicnode.com`):

| Exchange | `getCollateral()` | `getCtf()` |
|---|---|---|
| std `0x4bFb…982E` | **`0x2791…84174` (USDC.e)** | `0x4D97…6045` (standard CTF) |
| neg `0xC5d5…f80a` | **`0x2791…84174` (USDC.e)** | `0xd91e80cf2e7be2e162c6513ced06f1dd0da35296` (NegRisk CTF wrapper) |

**Verdict: the live CLOB settles in USDC.e (`0x2791…`).** Therefore:
- Pinned py_clob_client 0.17.5's collateral config (USDC.e) is **current/correct, not stale.**
- The "Polymarket migrated to native USDC" premise — asserted in `board_memo_polymarket_phase1.md:51-53`, `deploy_log.md:7466`, and `brokers/polymarket.py:56-58` but **verified against the live exchange in none** — is **wrong for the CLOB.** (Likely conflated Polymarket's deposit-UI native-USDC support with the underlying CTF collateral, which is still USDC.e.)
- **Action required before any live trading:** the wallet's **500 native USDC (`0x3c49…`) is the wrong token** — it must be swapped to USDC.e (DEX or Polymarket deposit flow) or the wallet re-funded with USDC.e. Item-4 approvals (above) must target **USDC.e**, which currently holds **$0**.

**Latent read-path consequence (note, not in scope to fix):** `brokers/polymarket.py:58` reads the snapshot balance from **native USDC**. It happens to show the correct $500 *today* (because that's what's funded), but once funds move to USDC.e for trading, the snapshot will read **$0** and under-report equity. The read-path collateral address should flip to USDC.e in lockstep with the funding fix.

---

## Track 1c — MATIC gas reserves + monitoring: **premise correction + plan**

- **Current balance: 98.375 POL.** Gas is a **non-issue near-term** (thousands of txs of headroom).
- **Premise correction (verify-against-ground-truth):** the readiness report's "every order burns MATIC … live placement silently fails when MATIC drains" is **overstated for CLOB**. Polymarket CLOB orders are signed **off-chain** and matched/settled by Polymarket's operator — **order placement is gasless for the user.** The wallet pays gas only for: (a) **one-time approvals** (item 4, ~6 txs), and (b) **per-resolution `redeemPositions`** (CTF redeem, ~100–200k gas ≈ 0.005–0.01 POL each). At nonce 0, neither has happened yet.
- **Threshold proposal:** the recurring consumer is redeems, not orders. A "100 orders" framing doesn't apply; reframe as "headroom for ~100 redeems + the one-time approval set" ≈ a few POL. Suggest a low-balance alarm at **< 5 POL** (still hundreds of redeems of margin) — near-ceremonial for *this* wallet, but load-bearing for the **Group C generalized per-division wallet pattern**, where new EOAs may start under-funded.
- **Monitoring hook location (plan only):** `PolymarketBroker.snapshot()` already does the USDC `eth_call`; the docstring (`:283-285`) explicitly anticipates surfacing MATIC in `AccountSnapshot.extra`. Add a native-balance read there, then a lightweight check (existing periodic snapshot loop, or a small scheduled task) that emits a **Telegram notification-only ping + audit event** when any division wallet drops below threshold. Per-wallet, to fold cleanly into the Group C per-division pattern.

---

## Decision gate

- **Track 2 (signing path):** Path A confirmed — signs end-to-end with the pinned versions today. Lockfile must target prod py3.12/Linux.
- **Track 1a (geo):** clean — authed surface reachable from the US VM; no EU proxy needed; task #31 closed.
- **Track 1c (gas):** ample (98 POL); premise corrected (CLOB orders gasless); monitoring is a Group C per-division-wallet concern.
- **Track 1b (collateral/approvals):** addresses + approve() shape scoped; on-chain state grounded (no approvals, all allowances 0). **Confirmed blocker: the live CLOB uses USDC.e but the wallet holds native USDC** — a funding/token-conversion problem, not a code problem.

**Recommendation:**
1. **Resolve the funding-token problem first** (operator decision): convert the 500 native USDC → USDC.e, or re-fund the wallet with USDC.e. This is a prerequisite for *any* CLOB order and gates item-4 approvals (which must target USDC.e).
2. Then item-4 approvals (ERC-20 approve + ERC-1155 setApprovalForAll to the std/neg exchanges) can be planned/executed against USDC.e.
3. Correct the "native USDC" premise in `board_memo_polymarket_phase1.md`, `brokers/polymarket.py:56-58`, and the read-path collateral address — bundle with the funding fix.
4. Path A + geo are green; the rest of Group C can be planned deliberately once funding is corrected.

---

## Status log

- **2026-05-29** — Group B (3/3) + item 8a spike complete. USDC-collateral fork **RESOLVED** on-chain: live CLOB = USDC.e; wallet mis-funded with native USDC (real blocker, caught pre-build). Report + spike script committed on branch `polymarket-live-prep-2026-05-29`; spike venv is throwaway (outside repo).
