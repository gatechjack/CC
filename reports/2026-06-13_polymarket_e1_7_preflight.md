# E1·7 — live preflight: `PolymarketLiveBroker.connect()` on-chain funded+approved check

**Date:** 2026-06-13
**Branch:** `polymarket-e1-7-preflight-2026-06-13` (base `cde66bb` = E1·6)
**Mode:** BUILD + TEST, **read-only / fundless**, **UNMERGED**. No funds, no signing, no real
on-chain reads in tests (mocked), no deploy, no prod write. Disclosure per `82fda13`.
**Increment:** E1·7 — the final agent-buildable E1 slice (the live preflight).

---

## Fork resolved: connect()-level on-chain check, NOT a second `assert_live_ready` branch

The plan sketched E1·7 as a `"polymarket"` branch in `assert_live_ready`. **That branch already
exists** — the OP·A creds-completeness preflight shipped in **item-7 `500cc1e`**
(`utils/secrets.py:423-442`: per-division key+funder present; it explicitly scopes OUT
balance/allowance, "belong to items 4/5"). So a second creds-check there would duplicate it.

Per the operator's fork decision (**Option 1**), E1·7 is instead the **connect()-level on-chain
check**: a read-only, per-funder, async verification at LIVE start that the wallet can actually
place — run inside `PolymarketLiveBroker.connect()` (E1·6), **after** `self._read.connect()` and
**before** building/L2-authing the placement client. It is NOT a duplicate creds-check; it is the
on-chain layer (`assert_live_ready` stays the creds layer).

## What it checks (and aborts `connect()` loudly on any gap)

`connect()` → `await self._assert_funded_and_approved()`, which fails LOUD (`RuntimeError`) if:

1. **Wallet not provisioned** — no `funder` / no RPC client / no RPC URL → `"wallet not
   provisioned … cannot go live"` (the stub case).
2. **OP·B not done** — `_fetch_usdc_balance()` (the read adapter's, reused) is `<= 0` →
   `"holds 0 USDC.e — fund the wallet in USDC.e (NOT native USDC) before live"`. (USDC.e is the
   CLOB collateral `0x2791…84174`, NOT native USDC — the arb wallet's native balance is the wrong
   token.)
3. **OP·C ERC-20 not done** — `allowance(USDC.e → exchange)` is `0` for **either** the std or the
   negRisk exchange → `"USDC.e allowance to the {std|negRisk} exchange is 0 — run the one-time
   approvals before live"`.
4. **OP·C ERC-1155 not done** — `isApprovedForAll(CTF → exchange)` is not `1` for **either**
   exchange → `"CTF approval-for-all to the {std|negRisk} exchange not set — run the one-time
   approvals before live"`.

So a mis-provisioned / unfunded / unapproved wallet fails **at startup**, not mid-trade on a
placed-but-unsettling order. All-clear → proceeds to the (mocked-in-tests) L2 auth and connects.

## Read-only, fundless — the agent only CHECKS

- On-chain reads go through a new `_eth_call(to, data)` that **reuses the read adapter's httpx
  client + RPC URL** (`self._read._client` / `self._read._rpc_url`) to POST a Polygon
  `eth_call … "latest"` and parse the hex result to int. **No signing, no funds, no on-chain
  write.** OP·A–C (provision key/funder, fund USDC.e, sign the approvals) remain **operator-only** —
  E1·7 never touches them, it only verifies their result.

## Grounded (no guessing)

- **Contract addresses** — from `py_clob_client 0.17.5` `get_contract_config(137)` dumped in the
  **2026-05-29 spike (Track 1b)**: std CTF Exchange `0x4bFb…982E`, NegRisk CTF Exchange
  `0xC5d5…f80a`, Conditional Tokens (ERC-1155) `0x4D97…6045`, collateral USDC.e `0x2791…84174`
  (== `brokers.polymarket._USDC_CONTRACT`).
- **Selectors** — `keccak256(sig)[:4]`: ERC-20 `allowance(address,address)` = `0xdd62ed3e`;
  ERC-1155 `isApprovedForAll(address,address)` = `0xe985e9c5`. ABI args are each left-padded to a
  32-byte word (`_pad_addr`).

## Carry-forward (flagged, not asserted)

- **NegRisk Adapter `0x7876…f29e`** MAY also need an approval for neg-risk markets — flagged
  **UNCERTAIN** in the 05-29 spike (it is **not** in the 0.17.5 `ContractConfig`). **Not asserted
  here**, to avoid a false-abort on an unconfirmed spender. Confirm against the live approval set at
  the operator-gated **$1 shakedown (OP·E)**; add the assertion only if it proves required.
- `connect()`'s real L2 auth + real `eth_call` responses are exercised only against the live chain
  (mocked here) — confirmed at the **$1 shakedown**. Standing E1·1–6 carry-forwards still apply.

## Tests (mocked / fundless) — 22 box-passing (11 new for E1·7)

`tests/test_polymarket_live_broker_assembly.py` (extended). On-chain reads + balance are mocked;
the `_read` connect is mocked (no real endpoints); no funds, no signing.

- **connect()-preflight (7):** unprovisioned wallet aborts; balance `0` aborts (USDC.e); std/​negRisk
  ERC-20 allowance `0` aborts; std/​negRisk ERC-1155 approval-not-set aborts; **fully-ready
  connects** (`_connected=True`, proceeds to mocked L2 auth, exactly **4** on-chain reads = 2
  allowances + 2 approvals, balance read once). On every abort, `_connected` stays `False` and
  `_clob` stays `None` (no partial-connect).
- **calldata builders (4, pure):** `_pad_addr` left-pads to 32 bytes and rejects non-20-byte addrs;
  `_allowance_calldata` / `_is_approved_for_all_calldata` emit the right selector + two padded args
  (length + slice checks).
- **E1·6 L2-auth test updated:** now stubs `_assert_funded_and_approved` (preflight is exercised by
  the dedicated tests above) and asserts it runs on `connect()`.
- **Full same-env gate** (branch vs pristine `cde66bb`, `git stash -u` in the SAME worktree,
  `--continue-on-collection-errors`): **EDITED=31 == PRISTINE=31, FAILED/ERROR node-id sets
  byte-identical → 0 new regressions.** (The 31 are the box's standing env failures — missing
  `bitunix_confluence_gate`, `robin_stocks.orders`, `_resample_to_3m`, etc. — none polymarket.)

## Phase D — status

- `trading_corp/brokers/polymarket_live.py` (E1·7 constants + `_pad_addr`/`_allowance_calldata`/
  `_is_approved_for_all_calldata` + `connect()` preflight hook + `_eth_call` +
  `_assert_funded_and_approved`), `tests/test_polymarket_live_broker_assembly.py` (+11 tests) and
  this report — committed on the branch. **UNMERGED.** No deploy/merge.
- **Hard stops honored:** read-only on-chain check (no signing, no funds, agent never
  provisions/funds/approves); mocked/fundless tests (real chain never hit); stayed in-slice (did NOT
  re-touch `assert_live_ready` — OP·A creds-preflight already shipped `500cc1e`); aborts LOUD on any
  gap (no silent stub-to-live); grounded addresses/selectors; no prod write; **0 new regressions**;
  agent CLAUDE.md untouched.

## End of agent-buildable E1

E1·1–7 are all on branches, unit-tested, **fundless**, **unmerged**. Remaining are operator-only and
real-money: merge E1·6+E1·7 (operator go) → OP·A (provision KV) → OP·B (fund USDC.e) → OP·C
(approvals) → OP·D (positions-shape) → **OP·E ($1 shakedown — first/only real-money validation)**.
Loop-driven live is **E2** (wire `would_have_placed` → `data_exec.place()` → broker).

---

*E1·7 artifact — committed unmerged on `polymarket-e1-7-preflight-2026-06-13` (base E1·6 `cde66bb`).
Source: py-clob-client 0.17.5 `get_contract_config(137)` (2026-05-29 spike Track 1b); ERC-20/1155
selectors keccak256(sig)[:4]. OP·A creds-preflight already shipped `500cc1e`.*
