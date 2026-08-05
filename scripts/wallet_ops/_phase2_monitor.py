#!/usr/bin/env python3
"""SCRATCH background monitor for the stuck nonce-8 Tx 4. Read-only. Not committed.
Polls every 30s; exits when the tx is mined OR the pending nonce returns to latest
(dropped) OR ~15 min elapses."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import walletops_chain as chain
from walletops_core import USDC_DECIMALS, USDC_NATIVE, from_units

TXH = "0xc702968ed7a5d5380e61ffdbf92c972b4e4c34b3261c086b2cd72acd06cace12"
rpc, funder = chain.load_rpc_and_funder("POLYMARKET-COPY-PRIVATE-KEY")
w3 = chain.make_w3(rpc)
funder = w3.to_checksum_address(funder)

for i in range(30):
    try:
        rcpt = w3.eth.get_transaction_receipt(TXH)
        usdc = from_units(chain.erc20_balance(w3, USDC_NATIVE, funder), USDC_DECIMALS)
        print(f"RESOLVED=MINED block={rcpt['blockNumber']} status={rcpt['status']} PCT_USDC={usdc}", flush=True)
        break
    except Exception:
        pass
    latest = w3.eth.get_transaction_count(funder)
    pending = w3.eth.get_transaction_count(funder, "pending")
    base = w3.eth.get_block("latest")["baseFeePerGas"] / 1e9
    print(f"[{i}] latest_nonce={latest} pending_nonce={pending} base={base:.0f}gwei", flush=True)
    if pending == latest:
        print(f"RESOLVED=DROPPED pending nonce returned to {latest}; nonce-8 tx fell out of mempool", flush=True)
        break
    time.sleep(30)
else:
    print("TIMEOUT: still pending after ~15 min; re-assess", flush=True)
