# wallet_ops — Path B funding toolchain (operator runbook)

Three LOCAL operator scripts to fund the Polymarket **copy-trading (PCT)** wallet
`0x2160…9F82` from the **arb** wallet `0x2FC73f6803eFe6A9c50A005B941Ea2Bd6b7aDA11`.
All sign with the **arb** key (`POLYMARKET-PRIVATE-KEY` in KV `kv-tc-vtwbowt3wtkpy`).

> **Run these on YOUR machine, never on the prod VM.** Every signing op shows a
> full confirmation block and requires a y/n. Each tx is run deliberately with
> Claude's explicit go. Default-deny: the swap aborts rather than execute >0.5%.

## What it does (the three ops)
1. **transfer_pol.py** — native POL: arb → PCT (gas for PCT; also a low-risk smoke test of the signing path).
2. **swap_native_usdc_to_usdce.py** — ~120 native USDC → USDC.e on Uniswap V3, on the arb wallet, ≤0.5% slippage.
3. **transfer_erc20.py** — the resulting USDC.e: arb → PCT.

## Prerequisites
- `az login` as a principal with **Key Vault Secrets User (get)** on `kv-tc-vtwbowt3wtkpy`.
  Verify: `az keyvault secret list --vault-name kv-tc-vtwbowt3wtkpy --query "[?starts_with(name,'POLYMARKET')].name" -o tsv`
- A **py3.12** venv with the deps (web3 6.11 pulls beta eth-abi on py3.14):
  ```
  uv venv --python 3.12 .venv-walletops
  .venv-walletops\Scripts\python -m pip install -r scripts/wallet_ops/requirements.txt
  ```
- Arb wallet funded (it is: 500 native USDC + 98.375 POL). Confirm you are on **Polygon mainnet** (chainId 137).

## How keys are handled
The private key is pulled from KV **only at the moment of signing**, after you type
`y`, and only on a real (non-dry-run) send. It is passed solely to
`eth_account` for signing, never printed/logged/written, and dropped immediately
after. The script asserts the key derives the expected funder address before
signing (wrong-key guard). The RPC URL (embedded Alchemy key) is never printed.
**Dry-run never reads the private key.**

## Run order (deliberate, one at a time — dry-run first, then live with Claude's go)
All commands from the repo root. `PK` below = `POLYMARKET-PRIVATE-KEY`.
`PY` = `.venv-walletops\Scripts\python`.

### Step 1 — POL transfer (arb → PCT), smoke test first
```
PY scripts/wallet_ops/transfer_pol.py PK 0x2160...9F82 5 --dry-run     # review the block
PY scripts/wallet_ops/transfer_pol.py PK 0x2160...9F82 5               # y/n -> sign
```
Verify: the printed PolygonScan tx URL shows status Success; PCT POL balance = 5.

### Step 2 — swap native USDC → USDC.e (on arb)
```
PY scripts/wallet_ops/swap_native_usdc_to_usdce.py PK 120 --dry-run    # shows per-tier quotes + approve + swap blocks
PY scripts/wallet_ops/swap_native_usdc_to_usdce.py PK 120              # approve (y/n) then swap (y/n)
```
- If it prints `ABORT: no fee tier fills … within 0.5% slippage` — **stop**. Do not
  raise `--slippage` without a deliberate decision; consider a smaller amount or
  another route (aggregator/bridge).
Verify on PolygonScan: arb native-USDC −120, arb USDC.e +~119.x. Note the exact
USDC.e amount received — it's the input to step 3.

### Step 3 — USDC.e transfer (arb → PCT)
```
PY scripts/wallet_ops/transfer_erc20.py PK 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174 0x2160...9F82 <usdce_amount> --dry-run
PY scripts/wallet_ops/transfer_erc20.py PK 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174 0x2160...9F82 <usdce_amount>
```
Verify: PolygonScan shows USDC.e moved to PCT. Then re-run the read-only check:
`deploy/polymarket_e1/pm_copy_state_check.py` → PCT **OP·B** now shows USDC.e funded.

## Between every step
- Open the printed PolygonScan URL; confirm **status = Success** before the next step.
- Confirm balances moved as expected.
- If any tx reverts (status 0) or the script raises, stop and report — do not retry blindly.

## Failure triage
| Symptom | Likely cause | Action |
|---|---|---|
| `Polygon RPC not reachable` | KV `POLYGON-RPC-URL` / network | check `az login` + connectivity |
| KV `get` denied | `az login` principal lacks Secrets User | grant get on the vault |
| `wrong-key guard` raised | wrong `kv_secret_name` for the funder | pass the correct `*-PRIVATE-KEY` |
| swap `ABORT … slippage` | thin native-USDC→USDC.e liquidity | smaller amount / explicit `--slippage` / other route |
| tx stuck pending | priority fee too low | script floors at 30 gwei; if still stuck, network congestion |

## After a clean run
- PCT wallet `0x2160…9F82` holds USDC.e (collateral) + POL (gas).
- **Still required before PCT can trade:** the **OP·C CLOB approvals** for the PCT
  wallet (USDC.e→exchanges + CTF setApprovalForAll) — **out of scope for this
  toolchain**, a separate operator action.
- Append the tx hashes + outcomes to `runbooks/deploy_log.md`.

## Native POL → USDC drain (`swap_pol_to_usdc.py`)
Swaps native **POL → native USDC** (Circle) on the sending wallet, e.g. to convert
leftover gas POL into USDC for a drain to Bitunix. Recipient = sender; any onward
transfer is a separate `transfer_erc20.py` step.
```
PY scripts/wallet_ops/swap_pol_to_usdc.py PK 10 --dry-run           # per-tier quotes + price impact + swap block
PY scripts/wallet_ops/swap_pol_to_usdc.py PK 10 --slippage 0.5      # y/n -> sign
```

**Native POL handling (the deposit gotcha — verified, do NOT assume Ethereum's pattern).**
On Polygon, SwapRouter02 (`0x68b3…Fc45`) handling of native POL was confirmed against
the PolygonScan-verified source + Codeslaw bytecode + Uniswap's official Polygon
deployment docs:
- `exactInputSingle` is **payable**, and the router's `WETH9()` immutable **==
  WPOL `0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270`** (our `tokenIn`). So native POL
  sent as `msg.value` is wrapped internally — **no approve, no separate wrap tx**.
- With `msg.value == amountIn` exactly there is **no leftover → no `refundETH`/
  multicall**; the script is a single plain `exactInputSingle`.
- SwapRouter02's `exactInputSingle` has **no `deadline`** param (unlike SwapRouter01).
- Triple-check on-chain if desired: call `WETH9()` (selector `0x4aa4a4fb`) on the
  router → expect `…0d500b1d8e8ef31e21c99d1db9a6444d3adf1270`.
- A **fork** router (QuickSwap/Sushi) could differ — this is verified ONLY for the
  genuine Uniswap deployment at the address above. Always confirm with a `--dry-run`
  before live execution.

**Slippage gate differs from the USDC→USDC.e swap.** POL→USDC is a *market* swap, not
a ~1:1 par swap, so the par gate (`select_best_tier`/`effective_slippage`) does not
apply. Instead it gates on **price impact** (full-size fill vs a near-spot tiny probe
on the same pool) AND sets an on-chain `amountOutMinimum`. `ABORT … price impact` →
**stop**; don't raise `--slippage` blindly. Because the input is the gas token, it
also refuses to swap so much POL that none is left to pay the swap's own gas.

## Files
- `walletops_core.py` — pure helpers (amount/slippage math, calldata, formatting); unit-tested.
- `walletops_chain.py` — web3 + KV layer (gas/fees, quotes, sign/broadcast, confirm).
- `transfer_pol.py` / `transfer_erc20.py` / `swap_native_usdc_to_usdce.py` — the original three CLIs.
- `swap_pol_to_usdc.py` — native POL → native USDC drain (price-impact gated, single payable tx).
- `requirements.txt` — local venv pins.
