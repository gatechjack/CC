# E1 — Polymarket live broker: REUSE-FIRST design

**Date:** 2026-06-13
**Branch:** `polymarket-e1-live-broker-design-2026-06-13` (base `main` `1327764`)
**Mode:** READ-ONLY analysis + design. No code, no `/plan`, no prod write, no deploy, no
on-chain action, no wallet/key/funding/allowance touch (disclosure per `82fda13`).
**Scope:** E1 = the live Polymarket order-placement broker (the foundational path-to-live
blocker from `reports/2026-06-13_polymarket_copy_path_to_live_scoping.md`). Reuse audit FIRST,
then the honest remaining build. Out of scope: building E1, the `/plan`, E2–E6, screening.

---

## 0. Correction to the record (must read)

**The prior scoping report (`2026-06-13_polymarket_copy_path_to_live_scoping.md`) wrongly
declared `reports/2026-05-28_polymarket_copy_live_readiness.md` and its item numbers /
`py_clob_client==0.17.5` / effort estimates "fabricated." They are NOT fabricated — they are
real, and they ARE the prior reuse audit this session was asked to find.**

Root cause: a `Glob` tooling error on my part, not an agent hallucination. `Glob` (a) defaults
to the process cwd (`Desktop`), not the repo, when no `path` is given, and (b) does not expand
`{a,b,c}` brace patterns. Both prior globs hit one or both faults and returned empty; I read
that as "files don't exist." `Grep`/`Read` with an explicit repo path find them immediately
(verified this session). Three reports + the signing spike are real:
- `reports/2026-05-28_polymarket_copy_live_readiness.md` — the live-readiness audit (incl. a
  dedicated reuse audit and a 16-item prioritized build list).
- `reports/2026-05-29_polymarket_live_prep_groupB_spike.md` — the **executed** signing spike.
- `reports/2026-05-29_polymarket_item6_wallet_plan.md` — the per-division wallet plan.
- `scripts/spike_polymarket_signing/spike_sign.py` — the real sign-only spike script.

Related: the prior scoping's "daily-loss cap is arbitrage-only" was a **stale** reading (true in
the 2026-05-28 snapshot, `risk.py:407`), **since fixed** (`5b947ea`, 2026-05-29). Current
`risk.py:439/476/505` sums both polymarket actors — so the present-state correction stands, but
it was a fix-over-time, not an agent error. **Lesson applied here: every cited file verified via
Read/Grep with an explicit path before inclusion.** The prior scoping report remains unmerged;
its "fabricated" claim should be retracted before it lands.

---

## 1. Phase 1 — REUSE AUDIT

### 1a. py-clob-client — the headline: order path is WIRING, not building

**The official Polymarket Python SDK provides the entire order lifecycle out of the box, with
EIP-712 signing internal.** Confirmed from two independent sources: the SDK's public API and a
spike that actually ran it.

Entry points (SDK docs + `scripts/spike_polymarket_signing/spike_sign.py`):
- **Construct:** `ClobClient(host, chain_id, key, signature_type, funder)` — `signature_type` 0=EOA.
- **Create + sign:** `client.create_order(OrderArgs(token_id, price, size, side))` and
  `client.create_market_order(MarketOrderArgs(token_id, amount, side, order_type))` → return a
  **signed** order. **EIP-712 signing is internal** to the client.
- **Post:** `client.post_order(signed_order, OrderType.GTC|FOK|GTD)`.
- **Cancel:** `client.cancel(order_id)` and `client.cancel_all()`.
- **L2 auth:** `creds = client.create_or_derive_api_creds(); client.set_api_creds(creds)`.

**Proven, not theoretical** — the 2026-05-29 spike (`spike_sign.py`, ran in a throwaway venv)
constructed `ClobClient` + `OrderArgs(token_id, 0.50, 5, BUY)` against a **live** market and
`create_order()` returned a **valid EIP-712 signature** (`makerAmount=2500000`,
`takerAmount=5000000`, `signatureType=0`, top-level `signature`). `post_order` was deliberately
not called.

**Verdict: E1's order-construction + signing + placement + cancellation is "call py-clob-client"
(wiring), NOT "implement what it doesn't" (building).** There is no EIP-712 to write.

**Version nuance (a real E1 sub-task, not a blocker):**
- The spike proved **`py_clob_client==0.17.5`** (+ `py_order_utils==0.3.2`, `web3==6.11.0`,
  `eth-account==0.13.1`) — also what Polymarket's own MIT `agents` framework pins.
- The SDK's latest is **`v0.34.6`** (2026-02-19); the **repo is archived/read-only** (migrate
  target is the beta `py-sdk`). The constructor arg order **differs by version** (0.17.5:
  `host, chain_id, key`; later docs: `host, key, chain_id`) — so **pin a version and use
  kwargs**. The deps are **not** in `requirements.txt` today (verified) — they lived only in the
  throwaway spike venv. The lockfile must be **compiled for prod py3.12/Linux** (spike ran
  3.14/Windows and pulled `eth-abi==6.0.0b1` beta transitively — pin the full graph).

### 1b. Polymarket MCP — NONE (no shortcut)

No Polymarket MCP server exists. This session's available MCP tools are **Robinhood only**; the
2026-05-28 audit confirmed empty `mcpServers` in every `~/.claude.json`, no `.mcp.json`, nothing
in memory/runbooks/reports. **There is no MCP order-placement path to wire.** E1's placement is
the py-clob-client adapter, full stop.

### 1c. tastytrade shape — the Broker contract E1 conforms to (copyable)

The interface E1 implements is small and known (`trading_corp/brokers/base.py`):
- `Broker(ReadOnlyBroker)` (`base.py:71`) adds two abstract methods:
  - `async def place_order(self, order: ProposedOrder) -> FillEvent` (`:87-88`)
  - `async def cancel_order(self, order_id: str) -> bool` (`:90-91`)
  - `place_multi_leg` / `get_option_greeks` default to `NotImplementedError` — **Polymarket
    inherits the default; never needs them.**
- `ReadOnlyBroker` (`:45`) supplies `connect / disconnect / snapshot / quote` — the existing
  `PolymarketBroker` already implements these (read path), directly reusable.

`TastytradeBroker(Broker)` (`tastytrade.py:92`) is the copyable template: `place_order` (`:259`)
/ `cancel_order` (`:273`), and a **place→poll-until-terminal→map-to-FillEvent** pattern with
timeout (`:398-460`) — structurally exactly what E1 needs (post_order → poll status → FillEvent).
Downstream, `data_exec.place()` already handles the fill/audit/dry-run plumbing (reused across
6+ HITL call sites per the 2026-05-28 audit). **Caveat:** tastytrade's `is_test` cert/sandbox
(`:14-15,103`) has **no Polymarket equivalent** — there is no CLOB sandbox (see §4).

### 1d. Prior prototype / unused signing code — sign-only spike EXISTS; no in-repo EIP-712

- `scripts/spike_polymarket_signing/spike_sign.py` is the real prototype: it signs (does NOT
  post). It anchors the adapter's create_order path but is **not** a `Broker` subclass.
- `brokers/polymarket.py:30-32`: `private_key` is accepted but **unused in Phase 1** ("signing
  only matters at Phase 3") — a deliberately-stable constructor, not an abandoned prototype.
- **No in-repo EIP-712/signing code** — the read path deliberately avoids `eth-utils`. None is
  needed; the SDK signs.
- `brokers/polymarket.py:5-6` already names the intended class: *"Live order placement is Phase 3
  work and will land as a separate `PolymarketLiveBroker(Broker)`."*

---

## 2. Phase 2 — the GENUINE remaining build (honest)

After reuse, **E1 is "wrap py-clob-client in a `Broker` subclass," not a from-scratch broker.**
The crypto (signing) is zero-build (SDK). What remains is bounded glue + Polymarket-specific
mapping:

**WIRING (small — call the SDK / copy the shape):**
- Construct/auth the client (`ClobClient(...)` + `create_or_derive_api_creds`/`set_api_creds`).
- `place_order` body: `create_order` → `post_order(signed, OrderType.GTC)`.
- `cancel_order` body: `client.cancel(order_id)`.
- Reuse `connect/disconnect/snapshot/quote` from the existing read adapter.
- Reuse `data_exec.place()` downstream (fill/audit/dry-run).

**GENUINELY NEW (Polymarket-specific; the real work, still M-sized):**
1. **`ProposedOrder → OrderArgs` mapping** — resolve `token_id` from the strategy's
   `condition_id`+`outcome_index`; map side/price/size; choose `OrderType` (GTC vs FOK for a
   copy-fill). This is the one non-trivial mapping.
2. **`post_order` result → `FillEvent`** + a **status-poll loop** (CLOB ack ≠ on-chain fill;
   mirror tastytrade's place→poll→map with a timeout).
3. **`cancel_order` semantics** — map our `order_id` to the CLOB order id.
4. **Quote/snapshot consolidation** onto SDK methods (replace the unverified raw-httpx
   `/last-trade-price` in `polymarket.py:415-477`; item-8 consolidation note).
5. **Dependency pinning + prod lockfile** (0.17.5 set, compiled for py3.12/Linux, pin the
   `eth-abi` beta transitive) + a sign-only re-confirm on the pinned set.
6. **Error handling** — auth failure, post rejection (collateral/allowance), timeout, partial fill.

**Honest size:** the 2026-05-28 audit put item 8 at **M–L**; with item 6/7 (wallet plumbing +
preflight) already **shipped** (`500cc1e`) and signing already **proven**, the adapter itself is
a solid **M** — bounded wiring + the five mapping concerns above. It is **not** the from-scratch
**L** the (now-corrected) prior scoping implied. (Making it *reachable* — routing the copy loop's
`auto_execute`/live branch through `data_exec.place()` — is the adjacent **E2**, audit item 9;
not E1.)

---

## 3. Phase 3 — the OPERATOR-ONLY boundary (load-bearing regardless of reuse)

py-clob-client signing the order **in-process** does not change that the key, the funds, and the
allowance approvals are **operator-controlled**. The agent never signs with a private key, never
moves funds, never approves allowances. Every step below is **OPERATOR-ONLY**:

| Step | Why operator-only | State |
|---|---|---|
| **Provision `POLYMARKET_COPY_PRIVATE_KEY` / `_FUNDER_ADDRESS` to KV** | live signing key; agent must never hold/print it | KV **paths wired** (`secrets.py:116`, item 6/7 shipped `500cc1e`); **values not provisioned** |
| **Fund the PCT EOA with USDC.e** | moves real capital; CLOB collateral is **USDC.e** (`0x2791…84174`, on-chain `getCollateral()`), not native USDC | Option A (operator): fund PCT EOA in USDC.e after item 6 (done). Existing arb EOA holds 500 **native** USDC (wrong token) + 98 POL |
| **USDC.e allowance approvals** — ERC-20 `approve(USDC.e → std `0x4bFb…982E` + negRisk `0xC5d5…f80a` exchanges)` + ERC-1155 `setApprovalForAll(CTF `0x4D97…6045` → same)` | signs on-chain txs with the private key | **None done** — funder nonce = 0, all allowances 0 (verified on-chain 2026-05-29). One-time, per-EOA, ~4–6 txs ≈ 0.01–0.015 POL |
| **POL/gas reserve** | wallet ops | 98 POL ample; CLOB placement is **gasless** (off-chain signed, operator-settled); POL only for approvals + per-resolution `redeemPositions` |

The agent's E1 build can proceed (adapter + deps + unit tests with a **mocked** client, and an
**ephemeral-key** sign-only check like the spike) **without** any of the above. The operator-only
steps gate only the first **posted** order.

---

## 4. Phase 4 — validation path

**There is no Polymarket CLOB sandbox/testnet** (unlike tastytrade `is_test`). Validation
increments:
1. **Sign-only (no funds, agent-doable):** unit-test the adapter against a **mocked** `ClobClient`;
   re-run the ephemeral-key sign-only spike on the **prod-pinned** lockfile to confirm the pin +
   constructor kwargs hold on py3.12/Linux. Proves construction + signing without capital.
2. **`--live --dry-run` (agent-doable once E2 routes the loop):** `data_exec.place()` emits
   `dry_run_skip` instead of `post_order` — exercises the full path minus the network post.
3. **Real placement (OPERATOR-gated):** first real validation requires a **posted** order, which
   requires USDC.e funding + allowances → a **$1 min-size shakedown**, observed on-chain with a
   matching `filled` audit row. `create_order` signs regardless of collateral, so signing success
   ≠ settle success — only the shakedown proves the end-to-end path.
4. **Positions-shape verification (operator-gated, pre-first-trade):** the `data-api/positions`
   response shape is unverified against a funded wallet (`polymarket.py:34-39`); diff it once the
   EOA holds a position (audit item #7 / item-6 runbook).

---

## 5. Grounded E1 design (for a follow-up `/plan` to slice into thin increments)

`class PolymarketLiveBroker(Broker)` in `trading_corp/brokers/polymarket.py` (or a sibling
module), wrapping pinned `py_clob_client`. Suggested thin increments (all agent-buildable except
where marked):

- **E1·1 — Deps + lockfile.** Pin `py_clob_client==0.17.5` + `py_order_utils==0.3.2` +
  `web3==6.11.0` + `eth-account==0.13.1`; compile the lockfile for prod py3.12/Linux; pin the
  `eth-abi` beta transitive. Re-run the ephemeral sign-only spike on the pinned set. *(no funds)*
- **E1·2 — Broker skeleton.** `PolymarketLiveBroker(Broker)`: reuse read methods
  (`connect/disconnect/snapshot/quote`) from the read adapter; construct `ClobClient` +
  `create_or_derive_api_creds`/`set_api_creds`; `place_order`/`cancel_order` raise "not wired" for
  now. Unit tests with a **mocked** `ClobClient`. *(no funds)*
- **E1·3 — `place_order`.** `ProposedOrder → OrderArgs(token_id,…)` (token_id from
  condition_id+outcome_index) → `create_order` → `post_order(…, OrderType.GTC)` → status-poll →
  `FillEvent` (copy tastytrade `:398-460`). Mocked-client unit tests for ack/fill/timeout/reject.
  *(no funds)*
- **E1·4 — `cancel_order`.** `client.cancel(order_id)` → bool; map order ids; unit tests. *(no funds)*
- **E1·5 — Quote/snapshot consolidation** onto SDK methods (retire the unverified raw-httpx
  quote); positions-shape verification is **operator-gated** (post-funding). 
- **Operator-only gates (interleave before any `post_order`):** provision `POLYMARKET_COPY_*`
  key/funder → fund PCT EOA in USDC.e → ERC-20/ERC-1155 allowance approvals. **Agent never does
  these.**
- **Adjacent (NOT E1):** E2 = route the copy loop's `auto_execute`/live branch through
  `data_exec.place()` (audit item 9) to make the broker reachable; then the $1 shakedown.

**Bottom line:** E1 is an **M** wiring/mapping job over a proven SDK + an existing Broker contract
+ already-shipped wallet plumbing — not a from-scratch build. The genuinely hard/irreversible
parts (key, funds, allowances) are **operator-only** and outside the agent's build path.

---

## 6. Hard stops / disclosure

- No code, no `/plan`, no prod write/deploy, no on-chain action, no wallet/key/funding/allowance
  touch this session. Design only.
- Every cited file/spec verified to exist via Read/Grep (explicit repo path) — the prior
  fabricated-source trap was itself a Glob tooling artifact, corrected in §0.
- Disclosure per `82fda13`: no prod, schema, SSH, or DB touched. Findings from local `main`
  `1327764` + the official py-clob-client public API.

---

*E1 design artifact — committed unmerged on `polymarket-e1-live-broker-design-2026-06-13`.
Seeds a follow-up `/plan`. Builds on the 2026-05-28 readiness audit + 2026-05-29 spike/wallet
plan; supersedes the E1 framing in `2026-06-13_polymarket_copy_path_to_live_scoping.md`.*
