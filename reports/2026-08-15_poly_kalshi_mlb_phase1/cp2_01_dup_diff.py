#!/usr/bin/env python3
"""CP2 proof: the copied placement helpers are byte-identical to kalshi_live.py.
READ-ONLY. Uses inspect.getsource so the comparison is of the actual loaded code."""
from __future__ import annotations

import difflib
import inspect
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))

from trading_corp.brokers import kalshi_live as SRC          # noqa: E402
from trading_corp.agents.strategies import poly_kalshi_executor as DUP  # noqa: E402

FUNCS = ["round_to_cent", "usd_to_contracts", "client_order_id",
         "v2_side_and_price", "build_v2_event_order"]

all_identical = True
for name in FUNCS:
    a = inspect.getsource(getattr(SRC, name))
    b = inspect.getsource(getattr(DUP, name))
    same = a == b
    all_identical = all_identical and same
    print(f"{'IDENTICAL' if same else 'DIFFERS  '}  {name}")
    if not same:
        for line in difflib.unified_diff(a.splitlines(), b.splitlines(),
                                         fromfile=f"kalshi_live.{name}",
                                         tofile=f"poly_kalshi_executor.{name}", lineterm=""):
            print("   " + line)

# constants carried over
for c in ["_CENTS_PER_DOLLAR", "_MIN_PRICE", "_MAX_PRICE", "_SELF_TRADE_PREVENTION",
          "_TIF", "_COID_NAMESPACE", "_V2_ORDERS_PATH"]:
    sv, dv = getattr(SRC, c, None), getattr(DUP, c, None)
    print(f"{'OK ' if sv == dv else 'DIFF'} const {c}: src={sv!r} dup={dv!r}")

print(f"\nALL COPIED HELPERS BYTE-IDENTICAL: {all_identical}")
