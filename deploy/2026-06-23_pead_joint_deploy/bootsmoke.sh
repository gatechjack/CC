#!/usr/bin/env bash
# COMBINED BOOT-SMOKE (joint-deploy step 4). Imports the engine in the PROD venv from
# the PROD tree WITHOUT starting the service or placing any order. Asserts: PEAD
# strategy/division/signal/earnings import; FillEvent additive fields; Bitunix wiring
# imports; and the full trading_corp.main module imports clean (the real boot path).
#
#   ./bootsmoke.sh [PROD_ROOT]
# Exit: 0 ok, 7 a check failed. Run AFTER apply.sh --go + preserve_check.sh, BEFORE the
# service restart.
set -euo pipefail
ROOT="${1:-${PROD_ROOT:-/home/azureuser/trading_corp}}"
VENV="${VENV:-$ROOT/venv}"
cd "$ROOT"
"$VENV/bin/python" - <<'PY'
import importlib, dataclasses as dc, sys
ok=True
def chk(mod,*attrs):
    global ok
    try:
        m=importlib.import_module(mod)
        for a in attrs:
            if not hasattr(m,a): print(f"  MISS {mod}.{a}"); ok=False
        print(f"  ok  import {mod}")
    except Exception as e:
        print(f"  FAIL import {mod}: {type(e).__name__}: {e}"); ok=False
print("-- PEAD modules --")
chk("trading_corp.agents.strategies.pead_pressures","compute_pressures","stop_level","drift_dead_level")
chk("trading_corp.agents.strategies.pead_signal")
chk("trading_corp.data.earnings_provider","EarningsProvider")
chk("trading_corp.agents.strategies.pead_strategy","PEADStrategy")
chk("trading_corp.agents.divisions.robinhood_pead")
print("-- models additive fields --")
try:
    from trading_corp.persistence.models import FillEvent
    have={f.name for f in dc.fields(FillEvent)}
    for need in ("fee","role","broker_order_id","account"):
        if need not in have: print(f"  MISS FillEvent.{need}"); ok=False
    print("  ok  FillEvent has:", sorted(have & {"fee","role","broker_order_id","account"}))
except Exception as e:
    print(f"  FAIL FillEvent: {type(e).__name__}: {e}"); ok=False
print("-- Bitunix wiring intact --")
chk("trading_corp.brokers.bitunix")
chk("trading_corp.agents.divisions.bitunix_futures_observer")
chk("trading_corp.agents.divisions.bitunix_position_reconciler")
print("-- full engine import (real boot path) --")
chk("trading_corp.main")
sys.exit(0 if ok else 7)
PY
rc=$?
[ $rc = 0 ] && echo "BOOTSMOKE OK" || echo "ABORT($rc): boot-smoke failed — run rollback.sh, do NOT restart."
exit $rc
