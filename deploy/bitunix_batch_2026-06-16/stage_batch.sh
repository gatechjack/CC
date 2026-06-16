#!/usr/bin/env bash
# Bitunix deploy batch 2026-06-16 — STAGE step. Run LOCALLY from the deploy-prep
# worktree (.../bitunix-deploy-batch-2026-06-16). Pushes the 6 merged bitunix
# files as LF (matching the TARGET md5s in deploy_apply_batch.sh) to the prod
# staging dir. RUN BY THE OPERATOR (writes to the prod host — §4).
#
# Excluded by design: data_exec.py (prod behind main via polymarket E2.5;
# batch change is doc-only) and config/strategies.yaml (targeted edit done by
# the apply script, never staged whole-file).
#
# After this: run deploy_apply_batch.sh ON prod, then restart.
set -euo pipefail
HOST="${HOST:-azureuser@trading.jacksumner.com}"
STAGE="${STAGE:-/home/azureuser/deploy_stage_bitunix_batch}"

FILES=(
trading_corp/brokers/bitunix.py
trading_corp/brokers/base.py
trading_corp/agents/divisions/bitunix_futures_observer.py
trading_corp/agents/divisions/bitunix_position_reconciler.py
trading_corp/brokers/bitunix_exceptions.py
trading_corp/agents/strategies/trade_plan.py
)

for f in "${FILES[@]}"; do
  d=$(dirname "$f")
  # git LF blob -> strip any CR -> write to prod staging (preserves the TARGET md5)
  git show "HEAD:$f" | tr -d '\r' | ssh "$HOST" "mkdir -p '$STAGE/$d' && cat > '$STAGE/$f'"
  echo "staged $f"
done
echo "Staged ${#FILES[@]} files to $STAGE on $HOST. Next: run deploy_apply_batch.sh ON prod."
