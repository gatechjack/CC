#!/usr/bin/env python3
"""Swap native POL -> native USDC on Uniswap V3 (Polygon). LOCAL operator script.

Drains native POL to native USDC (Circle) on the SENDING wallet in ONE payable
transaction. POL is the chain's native gas token: it is passed as msg.value and
SwapRouter02 wraps it to WPOL internally (router.WETH9() == WPOL on the Polygon
deployment, verified). So there is NO approve step and NO separate wrap tx, and
with msg.value == amountIn exactly there is no leftover -> no refundETH/multicall
(a plain exactInputSingle). See README "Native POL handling" for the verification.

Slippage gate (DIFFERENT from swap_native_usdc_to_usdce.py): POL -> USDC is a
MARKET swap, not a ~1:1 par swap, so the par-based gate (select_best_tier /
effective_slippage) does NOT apply — those compare 18dp POL units to 6dp USDC
units and would always abort. This script probes all 3 fee tiers, measures PRICE
IMPACT (full-size fill vs a near-spot tiny probe on the same pool), selects the
best-output tier whose impact is within --slippage, and ABORTS cleanly if none
qualify. It ALSO sets an on-chain amountOutMinimum (best_out * (1 - slippage)) so
price movement between quote and mining can't fill worse than tolerance. Never
silently degrades — to proceed past the gate the operator re-runs with a higher
--slippage consciously.

Swap RECIPIENT = the SENDER wallet, BY DESIGN. Any onward transfer (arb/PCT ->
Bitunix, etc.) is a SEPARATE transfer_erc20 step. Do NOT fold them together.

Gas: because the INPUT is the native gas token, the script refuses to swap an
amount that would not leave enough POL to pay the swap's own gas.

Usage (from repo root, in the wallet_ops venv, after `az login`):
    python scripts/wallet_ops/swap_pol_to_usdc.py POLYMARKET-PRIVATE-KEY 10 --dry-run
    python scripts/wallet_ops/swap_pol_to_usdc.py POLYMARKET-PRIVATE-KEY 10 --slippage 0.5

NEVER run on the prod VM.
"""
# === DESIGN NOTE #1 — DO NOT REVERT TO PAR-SLIPPAGE MATH ===
# This swap deliberately does NOT use select_best_tier / effective_slippage from the
# native-USDC -> USDC.e script. Those assume a ~1:1 PAR swap (same 6-dp token, both
# ~$1) and gate on `out >= amount_in*(1-tol)`. POL -> USDC is CROSS-DECIMAL (18-dp in
# / 6-dp out) AND CROSS-ASSET (POL ~$0.08, not ~$1): amount_in (1e18-scale) and out
# (1e6-scale) are not comparable, so the par gate would abort 100% of the time. The
# correct design is: PRICE IMPACT (full-size fill vs a near-spot probe) for pool
# depth + an on-chain amountOutMinimum, PLUS the --min-usdc-out oracle floor (#3) for
# off-market protection. A future session must NOT "simplify" this back to par math.
# Verified against live Quoter output 2026-06-18 (implied ~$0.0775/POL == Coinbase
# $0.0781 / CoinGecko $0.077854, agree within ~0.5%).
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import walletops_chain as chain
from walletops_core import (
    POL_DECIMALS, SWAP_ROUTER_02, USDC_DECIMALS, USDC_NATIVE, WPOL,
    effective_min_out, floor_required, floor_satisfied, from_units,
    implied_price_per_pol, price_impact_probe, select_best_tier_by_impact, to_units,
)


def main() -> None:
    ap = argparse.ArgumentParser(description="Swap native POL -> native USDC on Uniswap V3 (Polygon).")
    ap.add_argument("kv_secret_name", help="KV secret name of the SENDER private key (e.g. POLYMARKET-PRIVATE-KEY)")
    ap.add_argument("amount_pol", help="native POL amount to swap, human units (e.g. 10)")
    ap.add_argument("--slippage", type=float, default=0.5, help="max price-impact percent (default 0.5)")
    ap.add_argument("--min-usdc-out", type=float, default=None,
                    help="oracle fair-price floor: minimum USDC out. REQUIRED on live runs, optional on "
                         "--dry-run. Compute as pol_amount x external_POL_USD x (1 - slippage/100), using a "
                         "POL/USD price from Coinbase/Kraken/CoinGecko.")
    ap.add_argument("--dry-run", action="store_true", help="print the tx that would be signed; do NOT sign/broadcast")
    args = ap.parse_args()

    # fork #3: a LIVE swap REQUIRES an oracle-derived floor (a --dry-run does not, so
    # the operator can read the Quoter quote first, then compute the floor).
    if floor_required(args.dry_run, args.min_usdc_out):
        sys.exit("operator must specify --min-usdc-out for live execution; verify POL/USD on "
                 "Coinbase/Kraken/CoinGecko first and compute as: pol_amount x external_price x (1 - tolerance)")

    tol = args.slippage / 100.0
    min_usdc_out_units = to_units(args.min_usdc_out, USDC_DECIMALS) if args.min_usdc_out is not None else None
    rpc, funder = chain.load_rpc_and_funder(args.kv_secret_name)
    w3 = chain.make_w3(rpc)
    amount_in = to_units(args.amount_pol, POL_DECIMALS)

    bal = chain.native_balance(w3, funder)
    print(f"sender {funder}  native-POL balance: {from_units(bal, POL_DECIMALS)}"
          f"  ->  swap {args.amount_pol} POL to native USDC")
    if amount_in > bal:
        sys.exit(f"ABORT: swap amount ({args.amount_pol} POL) exceeds POL balance ({from_units(bal, POL_DECIMALS)})")

    # 1) quote every tier at full size AND at a near-spot probe; gate on PRICE IMPACT.
    probe_amount = max(1, amount_in // 1000)
    quotes = chain.quote_best_tier(w3, WPOL, USDC_NATIVE, amount_in)
    probes = chain.quote_best_tier(w3, WPOL, USDC_NATIVE, probe_amount)
    impacts = {fee: price_impact_probe(quotes.get(fee, 0), amount_in, probes.get(fee, 0), probe_amount)
               for fee in quotes}

    print("\nQuoterV2 (native POL -> native USDC):")
    for fee in sorted(quotes):
        out = quotes[fee]
        if out > 0:
            print(f"  tier {fee/10000:.2f}% : out {from_units(out, USDC_DECIMALS)} USDC"
                  f"  (price-impact {impacts[fee]*100:.3f}%)")
        else:
            print(f"  tier {fee/10000:.2f}% : no pool / revert")

    picked = select_best_tier_by_impact(quotes, impacts, tol)
    if picked is None:
        best = max((o for o in quotes.values()), default=0)
        sys.exit(f"\nABORT: no fee tier fills {args.amount_pol} POL within {args.slippage}% price impact "
                 f"(best out {from_units(best, USDC_DECIMALS)} USDC). No swap built. "
                 f"Options: re-run with an explicit higher --slippage, swap a smaller amount, "
                 f"or use a different route (aggregator/bridge).")
    fee, best_out = picked
    print(f"\nimplied price {implied_price_per_pol(best_out, amount_in):.6f} USDC/POL on tier {fee/10000:.2f}%"
          f"  — cross-check vs Coinbase/Kraken/CoinGecko before trusting the pool")

    # fork #3: oracle floor — abort if the pool's expected out is below the operator's floor.
    if not floor_satisfied(best_out, min_usdc_out_units):
        sys.exit(f"\nABORT: expected out {from_units(best_out, USDC_DECIMALS)} USDC is below your --min-usdc-out "
                 f"floor of {args.min_usdc_out} USDC — the pool price may be off-market vs your oracle. "
                 f"No swap built. Re-verify POL/USD and recompute the floor.")

    amount_out_min = effective_min_out(best_out, tol, min_usdc_out_units)
    floor_note = "" if min_usdc_out_units is None else f"  [floor {args.min_usdc_out} USDC enforced]"
    print(f"selected tier {fee/10000:.2f}%  expected {from_units(best_out, USDC_DECIMALS)} USDC  "
          f"minOut {from_units(amount_out_min, USDC_DECIMALS)} USDC (@{args.slippage}% slippage){floor_note}")

    # 2) the swap. native POL is msg.value (no approve, no pre-wrap). recipient = funder BY DESIGN.
    sdata = chain.swap_exactinputsingle_calldata(w3, WPOL, USDC_NATIVE, fee, funder, amount_in, amount_out_min)
    stx, snote = chain.build_tx(w3, funder, SWAP_ROUTER_02, value=amount_in, data=sdata, fallback_gas=250000)

    # native-input safety: refuse to swap so much POL that no POL is left to pay gas.
    est_max_cost = stx["gas"] * stx["maxFeePerGas"]
    if amount_in + est_max_cost > bal:
        sys.exit(f"\nABORT: swap ({args.amount_pol} POL) + est gas ({from_units(est_max_cost, POL_DECIMALS):.6f} POL) "
                 f"exceeds POL balance ({from_units(bal, POL_DECIMALS)}). "
                 f"Leave POL for gas — swap a smaller amount.")

    sdetails = chain.tx_to_details(
        w3, stx, action=f"Uniswap V3 exactInputSingle  native POL -> native USDC  (tier {fee/10000:.2f}%)",
        token=f"in native POL (router wraps to WPOL {WPOL})  /  out native USDC {USDC_NATIVE}",
        amount=f"in {args.amount_pol} POL -> minOut {from_units(amount_out_min, USDC_DECIMALS)} USDC"
               f"  (recipient = sender {funder})",
        gas_note=snote)
    chain.confirm_and_send(w3, args.kv_secret_name, stx, sdetails, args.dry_run)


if __name__ == "__main__":
    main()
