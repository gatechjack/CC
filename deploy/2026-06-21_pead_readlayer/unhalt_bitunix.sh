#!/usr/bin/env bash
# Reverse halt_bitunix.sh — resume bitunix entries. Run on prod AFTER the
# post-restart verify confirms clean reconcile + issue1/metrics behaviour.
set -euo pipefail
cd /home/azureuser/trading_corp
venv/bin/python - <<'PY'
from trading_corp.persistence.models import StrategyState
StrategyState.clear_halt("bitunix_futures", db_url="sqlite:///data/trading_corp.db")
st = StrategyState.from_persistence("bitunix_futures", db_url="sqlite:///data/trading_corp.db")
print("bitunix_futures halted =", st.halted, "(entries resume)")
PY
