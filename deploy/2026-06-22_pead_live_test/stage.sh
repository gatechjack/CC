#!/usr/bin/env bash
# Stage the branch checkout to the prod VM (~/pead_branch) for the PEAD live test.
# Run LOCALLY in Git Bash (resolves its own root, cwd-independent):
#     bash deploy/2026-06-22_pead_live_test/stage.sh
# Idempotent — replaces ~/pead_branch on prod each run. Transfers only the code
# the harness needs (trading_corp + config + this deploy dir), not data/ or logs.
set -euo pipefail
HOST="azureuser@trading.jacksumner.com"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
echo "staging from: $ROOT"
tar czf /tmp/pead_branch.tgz --exclude='__pycache__' --exclude='*.pyc' \
    trading_corp config deploy/2026-06-22_pead_live_test
scp /tmp/pead_branch.tgz "$HOST:pead_branch.tgz"
ssh "$HOST" "rm -rf pead_branch && mkdir pead_branch && tar xzf pead_branch.tgz -C pead_branch && echo STAGED_OK"
echo "staged. next (on prod): cd ~/pead_branch && bash deploy/2026-06-22_pead_live_test/gate1_run.sh"
