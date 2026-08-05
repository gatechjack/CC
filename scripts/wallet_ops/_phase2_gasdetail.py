#!/usr/bin/env python3
"""SCRATCH read-only gas detail. Not committed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import walletops_chain as chain

rpc, _ = chain.load_rpc_and_funder("POLYMARKET-COPY-PRIVATE-KEY")
w3 = chain.make_w3(rpc)
base = w3.eth.get_block("latest")["baseFeePerGas"]
try:
    mpf = w3.eth.max_priority_fee
except Exception:
    mpf = 0
gp = w3.eth.gas_price
priority = max(mpf, w3.to_wei(30, "gwei"))
maxfee = base * 2 + priority
print(f"base: {base/1e9:.1f} gwei")
print(f"max_priority_fee (node suggestion): {mpf/1e9:.1f} gwei")
print(f"gas_price (legacy): {gp/1e9:.1f} gwei")
print(f"script would set -> priority {priority/1e9:.1f} gwei | maxFee {maxfee/1e9:.1f} gwei | "
      f"effective ~{(base+priority)/1e9:.1f} gwei")
