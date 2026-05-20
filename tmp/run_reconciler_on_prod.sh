#!/bin/bash
# Upload the reconciler to /tmp on prod and run it against the prod DB.
set -e

# Decode the reconciler from a base64 blob (inlined by the orchestrator).
mkdir -p /tmp/recon
cat > /tmp/recon/audit_reality_reconciler.py <<'PYEOF'
__REPLACE_RECONCILER_BODY__
PYEOF

echo "=== Running reconciler against prod DB ==="
PYTHONPATH=/home/azureuser/trading_corp python3 /tmp/recon/audit_reality_reconciler.py \
  --db sqlite:////home/azureuser/trading_corp/data/trading_corp.db
RC=$?
echo "=== Reconciler exit code: $RC ==="
exit 0  # don't propagate non-zero so the az invoke succeeds even on mismatch
