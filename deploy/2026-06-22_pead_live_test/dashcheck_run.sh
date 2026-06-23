#!/usr/bin/env bash
# PEAD STEP 3 — GATE 3 round-trip DASHBOARD CHECK (engine pressures == dashboard).
#     cd ~/pead_branch && bash deploy/2026-06-22_pead_live_test/dashcheck_run.sh
# Read-only; builds build_pead_view + recomputes engine pressures on the same quote.
set -euo pipefail
export KEY_VAULT_URI="https://kv-tc-vtwbowt3wtkpy.vault.azure.net/"
export PYTHONPATH="$PWD"
export PYTHONIOENCODING="utf-8"
exec /home/azureuser/trading_corp/venv/bin/python deploy/2026-06-22_pead_live_test/gate34_roundtrip.py check
