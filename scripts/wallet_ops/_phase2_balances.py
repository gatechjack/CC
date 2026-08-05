#!/usr/bin/env python3
"""SCRATCH read-only balance reader for the Phase-2 drain checkpoints. Not committed.
Prints POL / native-USDC / USDC.e for the arb and PCT wallets. No keys, no writes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import walletops_chain as chain
from walletops_core import POL_DECIMALS, USDC_DECIMALS, USDC_E, USDC_NATIVE, from_units

for label, key in [("arb", "POLYMARKET-PRIVATE-KEY"), ("PCT", "POLYMARKET-COPY-PRIVATE-KEY")]:
    rpc, funder = chain.load_rpc_and_funder(key)
    w3 = chain.make_w3(rpc)
    pol = chain.native_balance(w3, funder)
    usdc = chain.erc20_balance(w3, USDC_NATIVE, funder)
    usdce = chain.erc20_balance(w3, USDC_E, funder)
    nonce = w3.eth.get_transaction_count(w3.to_checksum_address(funder))
    print(f"{label}  {funder}  (nonce={nonce})")
    print(f"    POL    : {from_units(pol, POL_DECIMALS)}")
    print(f"    USDC   : {from_units(usdc, USDC_DECIMALS)}")
    print(f"    USDC.e : {from_units(usdce, USDC_DECIMALS)}")
