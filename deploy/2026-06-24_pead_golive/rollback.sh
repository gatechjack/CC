#!/usr/bin/env bash
# ROLLBACK PEAD go-live -> prior INERT state. Run as azureuser ON PROD.
# Restores the 8 files from *.bak-pre-golive-2026-06-24. The ExecStart revert +
# daemon-reload + restart are sudo (printed at the end — run them to complete rollback).
set -uo pipefail
TC=/home/azureuser/trading_corp
TAG=pre-golive-2026-06-24
n=0
for f in config/strategies.yaml config/divisions.yaml \
         trading_corp/agents/divisions/robinhood_pead.py \
         trading_corp/agents/strategies/pead_strategy.py \
         trading_corp/brokers/robinhood.py trading_corp/main.py \
         trading_corp/persistence/db.py trading_corp/persistence/models.py; do
  if [ -f "$TC/$f.bak-$TAG" ]; then cp "$TC/$f.bak-$TAG" "$TC/$f"; echo "restored $f"; n=$((n+1)); fi
done
echo "restored $n/8 files to pre-go-live (inert: standby:true, auto_execute:false, whole-share)."
echo ""
echo "NOW COMPLETE THE ROLLBACK (sudo) — revert ExecStart + restart:"
echo "  sudo sed -i 's/ robinhood_pead//' /etc/systemd/system/trading-corp.service"
echo "  sudo systemctl daemon-reload && sudo systemctl restart trading-corp"
echo "(pending_order table stays — harmless empty table; the inert engine never reconciles.)"
