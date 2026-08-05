#!/usr/bin/env python3
"""SCRATCH read-only tx-state checker for a stuck/timed-out Phase-2 tx. Not committed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import walletops_chain as chain
from walletops_core import POL_DECIMALS, USDC_DECIMALS, USDC_NATIVE, from_units

TXH = "0xc702968ed7a5d5380e61ffdbf92c972b4e4c34b3261c086b2cd72acd06cace12"
rpc, funder = chain.load_rpc_and_funder("POLYMARKET-COPY-PRIVATE-KEY")
w3 = chain.make_w3(rpc)

print(f"latest block: {w3.eth.block_number}")
try:
    tx = w3.eth.get_transaction(TXH)
    print(f"tx FOUND: blockNumber={tx.get('blockNumber')}  nonce={tx.get('nonce')}  "
          f"maxFeePerGas={from_units(tx.get('maxFeePerGas', 0), 9)} gwei")
except Exception as e:
    print(f"tx NOT in node ({type(e).__name__}) -> dropped or never propagated")

try:
    rcpt = w3.eth.get_transaction_receipt(TXH)
    print(f"RECEIPT: status={rcpt.get('status')}  block={rcpt.get('blockNumber')}")
except Exception as e:
    print(f"RECEIPT: none yet ({type(e).__name__}) -> not mined")

base = w3.eth.get_block("latest")["baseFeePerGas"]
print(f"current baseFee: {from_units(base, 9)} gwei")
print(f"PCT nonce  latest={w3.eth.get_transaction_count(w3.to_checksum_address(funder))}  "
      f"pending={w3.eth.get_transaction_count(w3.to_checksum_address(funder), 'pending')}")
print(f"PCT USDC: {from_units(chain.erc20_balance(w3, USDC_NATIVE, funder), USDC_DECIMALS)}")
print(f"PCT POL : {from_units(chain.native_balance(w3, funder), POL_DECIMALS)}")
