#!/usr/bin/env python3
"""Transfer an ERC-20 token between wallets on Polygon. LOCAL operator script.

Usage (from repo root, in the wallet_ops venv, after `az login`):
    # USDC.e (0x2791Bca1...84174) arb -> PCT, amount in HUMAN units:
    python scripts/wallet_ops/transfer_erc20.py POLYMARKET-PRIVATE-KEY 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174 0x2160...9F82 119.4
    ... --dry-run

The token's on-chain decimals() is read to scale the amount; symbol() is shown in
the confirmation. NEVER run on the prod VM.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import walletops_chain as chain
from walletops_core import erc20_transfer_calldata, from_units, to_units


def main() -> None:
    ap = argparse.ArgumentParser(description="Transfer an ERC-20 token (Polygon).")
    ap.add_argument("kv_secret_name", help="KV secret name of the SENDER private key")
    ap.add_argument("token", help="ERC-20 contract address")
    ap.add_argument("to", help="recipient 0x address")
    ap.add_argument("amount", help="token amount in human units")
    ap.add_argument("--dry-run", action="store_true", help="print the tx that would be signed; do NOT sign/broadcast")
    args = ap.parse_args()

    rpc, funder = chain.load_rpc_and_funder(args.kv_secret_name)
    w3 = chain.make_w3(rpc)
    decimals = chain.erc20_decimals(w3, args.token)
    symbol = chain.erc20_symbol(w3, args.token)
    units = to_units(args.amount, decimals)

    bal = chain.erc20_balance(w3, args.token, funder)
    print(f"sender {funder}  {symbol} balance: {from_units(bal, decimals)}  ->  send {args.amount} {symbol} to {args.to}")
    if units > bal:
        sys.exit(f"ABORT: amount ({args.amount} {symbol}) exceeds balance ({from_units(bal, decimals)} {symbol})")

    data = erc20_transfer_calldata(args.to, units)
    tx, note = chain.build_tx(w3, funder, args.token, value=0, data=data)
    details = chain.tx_to_details(
        w3, tx, action=f"Transfer {symbol} (ERC-20)",
        token=f"{symbol} {args.token} ({decimals} dp)",
        amount=f"{args.amount} {symbol} ({units} units)  ->  recipient {args.to}", gas_note=note)
    chain.confirm_and_send(w3, args.kv_secret_name, tx, details, args.dry_run)


if __name__ == "__main__":
    main()
