#!/usr/bin/env bash
# Runbook step 5 wrapper: one-time API-only REST backfill (engine running, idempotent).
set -euo pipefail
cd ~/trading_corp
PYTHONPATH=. venv/bin/python ~/capfix/backfill_capture.py
