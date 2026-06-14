#!/usr/bin/env python3
"""Swap native USDC -> USDC.e on Uniswap V3 (Polygon). LOCAL operator script.

Probes fee tiers (0.01% / 0.05% / 0.3%), picks the BEST output, and ABORTS cleanly
if even the best tier can't fill within --slippage (default 0.5%). Never silently
degrades to a worse fill. To proceed past the gate the operator must consciously
re-run with a higher --slippage.

Approval: requests EXACT amountIn allowance to SwapRouter02 (no standing/infinite
allowance); builds an approve() first only if the current allowance is short.

Swap RECIPIENT = the SENDER (arb) wallet, BY DESIGN. The arb -> PCT move is a
SEPARATE transfer_erc20 step. This two-step separation keeps the audit trail clean
and avoids encoding a multi-hop swap. Do NOT fold these into a single tx.

Usage (from repo root, in the wallet_ops venv, after `az login`):
    python scripts/wallet_ops/swap_native_usdc_to_usdce.py POLYMARKET-PRIVATE-KEY 120
    python scripts/wallet_ops/swap_native_usdc_to_usdce.py POLYMARKET-PRIVATE-KEY 120 --slippage 0.5 --dry-run

NEVER run on the prod VM.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import walletops_chain as chain
from walletops_core import (
    SWAP_ROUTER_02, USDC_DECIMALS, USDC_E, USDC_NATIVE,
    effective_slippage, erc20_approve_calldata, from_units, min_out,
    select_best_tier, to_units,
)


def main() -> None:
    ap = argparse.ArgumentParser(description="Swap native USDC -> USDC.e on Uniswap V3 (Polygon).")
    ap.add_argument("kv_secret_name", help="KV secret name of the SENDER private key (e.g. POLYMARKET-PRIVATE-KEY)")
    ap.add_argument("amount", help="native USDC amount to swap, human units (e.g. 120)")
    ap.add_argument("--slippage", type=float, default=0.5, help="max slippage percent (default 0.5)")
    ap.add_argument("--dry-run", action="store_true", help="print the tx(s) that would be signed; do NOT sign/broadcast")
    args = ap.parse_args()

    tol = args.slippage / 100.0
    rpc, funder = chain.load_rpc_and_funder(args.kv_secret_name)
    w3 = chain.make_w3(rpc)
    amount_in = to_units(args.amount, USDC_DECIMALS)

    bal = chain.erc20_balance(w3, USDC_NATIVE, funder)
    print(f"sender {funder}  native-USDC balance: {from_units(bal, USDC_DECIMALS)}  ->  swap {args.amount} to USDC.e")
    if amount_in > bal:
        sys.exit(f"ABORT: swap amount ({args.amount}) exceeds native-USDC balance ({from_units(bal, USDC_DECIMALS)})")

    # 1) quote every tier, pick best, gate on slippage (abort cleanly if none qualify)
    quotes = chain.quote_best_tier(w3, USDC_NATIVE, USDC_E, amount_in)
    print("\nQuoterV2 (native USDC -> USDC.e):")
    for fee, out in quotes.items():
        if out > 0:
            print(f"  tier {fee/10000:.2f}% : out {from_units(out, USDC_DECIMALS)} USDC.e"
                  f"  (slippage-from-par {effective_slippage(amount_in, out)*100:.3f}%)")
        else:
            print(f"  tier {fee/10000:.2f}% : no pool / revert")

    picked = select_best_tier(quotes, amount_in, tol)
    if picked is None:
        best = max(quotes.values()) if quotes else 0
        sys.exit(f"\nABORT: no fee tier fills {args.amount} USDC within {args.slippage}% slippage "
                 f"(best out {from_units(best, USDC_DECIMALS)} USDC.e). No swap built. "
                 f"Options: re-run with an explicit higher --slippage, swap a smaller amount, "
                 f"or use a different route (aggregator/bridge).")
    fee, best_out = picked
    amount_out_min = min_out(best_out, tol)
    print(f"\nselected tier {fee/10000:.2f}%  expected {from_units(best_out, USDC_DECIMALS)} USDC.e  "
          f"minOut {from_units(amount_out_min, USDC_DECIMALS)} USDC.e (@{args.slippage}% slippage)")

    # 2) approval (EXACT amountIn) only if current allowance is short
    allowance = chain.erc20_allowance(w3, USDC_NATIVE, funder, SWAP_ROUTER_02)
    if allowance < amount_in:
        print(f"\nnative-USDC allowance to router {from_units(allowance, USDC_DECIMALS)} < {args.amount}"
              f" -> approve() needed (exact amountIn).")
        adata = erc20_approve_calldata(SWAP_ROUTER_02, amount_in)
        atx, anote = chain.build_tx(w3, funder, USDC_NATIVE, value=0, data=adata)
        adetails = chain.tx_to_details(
            w3, atx, action="Approve native USDC -> SwapRouter02 (EXACT amountIn)",
            token=f"native USDC {USDC_NATIVE}",
            amount=f"{args.amount} USDC ({amount_in} units)", gas_note=anote)
        chain.confirm_and_send(w3, args.kv_secret_name, atx, adetails, args.dry_run)
    else:
        print(f"\nnative-USDC allowance to router already sufficient ({from_units(allowance, USDC_DECIMALS)}).")

    # 3) the swap. recipient = funder (arb) BY DESIGN — arb->PCT is a separate step.
    sdata = chain.swap_exactinputsingle_calldata(w3, USDC_NATIVE, USDC_E, fee, funder, amount_in, amount_out_min)
    # A pre-approval dry-run can't estimate the swap's gas (allowance not yet on-chain) -> fallback.
    stx, snote = chain.build_tx(w3, funder, SWAP_ROUTER_02, value=0, data=sdata, fallback_gas=300000)
    sdetails = chain.tx_to_details(
        w3, stx, action=f"Uniswap V3 exactInputSingle  native USDC -> USDC.e  (tier {fee/10000:.2f}%)",
        token=f"in native USDC {USDC_NATIVE}  /  out USDC.e {USDC_E}",
        amount=f"in {args.amount} USDC -> minOut {from_units(amount_out_min, USDC_DECIMALS)} USDC.e"
               f"  (recipient = sender {funder})",
        gas_note=snote)
    chain.confirm_and_send(w3, args.kv_secret_name, stx, sdetails, args.dry_run)


if __name__ == "__main__":
    main()
