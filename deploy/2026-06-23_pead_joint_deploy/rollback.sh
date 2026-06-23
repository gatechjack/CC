#!/usr/bin/env bash
# ROLLBACK — restore the 10 shared files from their .bak-pre-pead-2026-06-23 backups and
# remove the 5 net-new PEAD files. Restart the service afterwards to load reverted code.
#   ./rollback.sh [PROD_ROOT]
set -euo pipefail
ROOT="${1:-${PROD_ROOT:-/home/azureuser/trading_corp}}"
SUF=".bak-pre-pead-2026-06-23"
EXISTING="config/divisions.yaml config/risk.yaml config/strategies.yaml config/data_providers.yaml \
trading_corp/main.py trading_corp/persistence/models.py trading_corp/agents/paper_trade_replay.py \
trading_corp/brokers/robinhood.py trading_corp/utils/market_data.py trading_corp/utils/secrets.py"
NEW="config/nasdaq_composite.txt trading_corp/agents/divisions/robinhood_pead.py \
trading_corp/agents/strategies/pead_strategy.py trading_corp/agents/strategies/pead_signal.py \
trading_corp/data/earnings_provider.py"
echo "== ROLLBACK =="
for f in $EXISTING; do
  if [ -f "$ROOT/$f$SUF" ]; then cp -p "$ROOT/$f$SUF" "$ROOT/$f"; echo "  restored $f"; else echo "  NO BACKUP $f"; fi
done
for f in $NEW; do
  if [ -f "$ROOT/$f" ]; then rm -f "$ROOT/$f"; echo "  removed  $f"; fi
done
echo "ROLLBACK complete. Restart the service to load reverted code."
