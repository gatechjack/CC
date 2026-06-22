#!/usr/bin/env bash
# PEAD STEP 3 — GATE 1 read-only preview runner.
# Run FROM the staged branch-checkout root on the prod VM:
#     cd <branch_checkout_root> && bash deploy/2026-06-22_pead_live_test/gate1_run.sh
# Uses the prod venv's python (deps) with PYTHONPATH=branch (branch trading_corp,
# incl. the hard-bind broker). READ-ONLY: places nothing, writes no prod DB.
set -euo pipefail
export KEY_VAULT_URI="https://kv-tc-vtwbowt3wtkpy.vault.azure.net/"
export PYTHONPATH="$PWD"
export PYTHONIOENCODING="utf-8"
exec /home/azureuser/trading_corp/venv/bin/python \
    deploy/2026-06-22_pead_live_test/gate1_preview.py
