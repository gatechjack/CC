#!/usr/bin/env bash
# PEAD STEP 3 — GATE 2 safety/diagnosis check (READ-ONLY + targeted cleanup).
# Run FROM the staged branch root on prod:
#     cd ~/pead_branch && bash deploy/2026-06-22_pead_live_test/check_run.sh
# Only needs robin_stocks + the cached pickle (no KV / no PYTHONPATH).
set -euo pipefail
exec /home/azureuser/trading_corp/venv/bin/python \
    deploy/2026-06-22_pead_live_test/gate2_check.py
