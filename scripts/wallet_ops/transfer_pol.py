#!/usr/bin/env python3
"""Transfer native POL between wallets on Polygon. LOCAL operator script.

Usage (from repo root, in the wallet_ops venv, after `az login`):
    python scripts/wallet_ops/transfer_pol.py POLYMARKET-PRIVATE-KEY 0x2160...9F82 5
    python scripts/wallet_ops/transfer_pol.py POLYMARKET-PRIVATE-KEY 0x2160...9F82 5 --dry-run

Signs with the wallet whose KV *-PRIVATE-KEY secret name is given. Requires the
caller's `az login` principal to have Key Vault Secrets User (get) on
kv-tc-vtwbowt3wtkpy. NEVER run on the prod VM.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling-module imports

import walletops_chain as chain
from walletops_core import from_units, to_units


def main() -> None:
    ap = argparse.ArgumentParser(description="Transfer native POL between wallets (Polygon).")
    ap.add_argument("kv_secret_name", help="KV secret name of the SENDER private key (e.g. POLYMARKET-PRIVATE-KEY)")
    ap.add_argument("to", help="recipient 0x address")
    ap.add_argument("amount", help="POL amount in human units (e.g. 5)")
    ap.add_argument("--dry-run", action="store_true", help="print the tx that would be signed; do NOT sign/broadcast")
    args = ap.parse_args()

    rpc, funder = chain.load_rpc_and_funder(args.kv_secret_name)
    w3 = chain.make_w3(rpc)
    value = to_units(args.amount, 18)  # POL has 18 decimals

    bal = w3.eth.get_balance(w3.to_checksum_address(funder))
    print(f"sender {funder}  POL balance: {from_units(bal, 18)}  ->  send {args.amount} POL to {args.to}")
    if value > bal:
        sys.exit(f"ABORT: amount ({args.amount} POL) exceeds balance ({from_units(bal, 18)} POL)")

    tx, note = chain.build_tx(w3, funder, args.to, value=value, data="0x")
    details = chain.tx_to_details(w3, tx, action="Transfer native POL",
                                  amount=f"{args.amount} POL", gas_note=note)
    chain.confirm_and_send(w3, args.kv_secret_name, tx, details, args.dry_run)


if __name__ == "__main__":
    main()
