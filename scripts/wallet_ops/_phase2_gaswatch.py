#!/usr/bin/env python3
"""SCRATCH background gas watcher. Read-only. Not committed. Exits when Polygon base
fee falls below the threshold (safe to resume) or after ~30 min."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import walletops_chain as chain

THRESH_GWEI = 80
rpc, _ = chain.load_rpc_and_funder("POLYMARKET-COPY-PRIVATE-KEY")
w3 = chain.make_w3(rpc)

for i in range(20):
    base = w3.eth.get_block("latest")["baseFeePerGas"] / 1e9
    print(f"[{i}] base={base:.0f}gwei", flush=True)
    if base < THRESH_GWEI:
        print(f"EASED: base fell to {base:.0f} gwei (< {THRESH_GWEI}); safe to resume", flush=True)
        break
    time.sleep(90)
else:
    print(f"TIMEOUT: base still elevated after ~30 min; re-assess", flush=True)
