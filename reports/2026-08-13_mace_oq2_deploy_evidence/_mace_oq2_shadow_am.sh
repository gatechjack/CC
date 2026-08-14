#!/bin/bash
# MACE morning shadow-eval - READ-ONLY confidence check (Board ruling
# 2026-08-13: NOT a deploy gate; eval-time credit-floor filter is the
# operative safety). Places NOTHING, writes NOTHING. Run >= 09:35 ET.
# Read: each active (IBIT/XLE/GDX) should clear credit floor 0.30 x width
# on live quotes. A symbol failing floor -> Board rules: accept the engine's
# eval-time SKIP, or config-only backfill restart completed before 15:40 ET.
set -u
cd /home/azureuser/trading_corp || exit 1
runuser -u azureuser -- venv/bin/python scripts/mace_shadow_eval.py --json
