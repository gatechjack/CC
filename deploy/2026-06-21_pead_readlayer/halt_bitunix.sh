#!/usr/bin/env bash
# Durable bitunix entry-halt for the deploy window. Writes halted=True to
# agent_state via the engine's own StrategyState.persist_halt — survives the
# restart, and RiskAgent.evaluate rejects every bitunix_futures entry while set.
# Run on prod BEFORE the restart. Reverse with unhalt_bitunix.sh after verify.
set -euo pipefail
cd /home/azureuser/trading_corp
venv/bin/python - <<'PY'
from trading_corp.persistence.models import StrategyState
StrategyState.persist_halt(
    "bitunix_futures",
    "deploy-window 2026-06-21 (PEAD read-layer + metrics-epoch + issue1)",
    db_url="sqlite:///data/trading_corp.db",
)
st = StrategyState.from_persistence("bitunix_futures", db_url="sqlite:///data/trading_corp.db")
print("bitunix_futures halted =", st.halted, "| reason:", st.halt_reason)
PY
