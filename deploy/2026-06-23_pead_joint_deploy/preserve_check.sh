#!/usr/bin/env bash
# EXTENDED GUARD (joint-deploy step 3 gate). Proves PEAD's edits to the Bitunix-/prod-
# sensitive shared files are PURELY ADDITIVE: every non-blank line in the pre-deploy
# BACKUP is still present in the INSTALLED file. If any backup line vanished, prod
# content was dropped -> ABORT(9).
#
# Sound because PEAD's diff on each of these is additive only (verified at build time:
# strategies bitunix_futures byte-identical; risk/divisions/main/models additive;
# paper_trade_replay = 2 new clauses). Files that legitimately REPLACE base-era code
# (robinhood, market_data, secrets, data_providers) are NOT checked here — their safety
# is the prod_source drift guard + payload.md5 integrity check in apply.sh
# (all have prod_only=0: prod had no unique content there).
#
#   ./preserve_check.sh [PROD_ROOT]
set -euo pipefail
ROOT="${1:-${PROD_ROOT:-/home/azureuser/trading_corp}}"
SUF=".bak-pre-pead-2026-06-23"
ADDITIVE="config/strategies.yaml config/risk.yaml config/divisions.yaml \
trading_corp/agents/paper_trade_replay.py trading_corp/main.py trading_corp/persistence/models.py"

echo "== extended guard: additive-preservation (backup subset of installed) =="
rc=0
for f in $ADDITIVE; do
  bak="$ROOT/$f$SUF"; cur="$ROOT/$f"
  [ -f "$bak" ] || { echo "  MISSING backup $bak"; rc=9; continue; }
  [ -f "$cur" ] || { echo "  MISSING current $cur"; rc=9; continue; }
  miss=$(grep -vE '^[[:space:]]*$' "$bak" | sort -u | comm -23 - <(grep -vE '^[[:space:]]*$' "$cur" | sort -u))
  n=$(printf '%s' "$miss" | grep -c . || true)
  if [ "$n" -gt 0 ]; then
    echo "  DROP   $f : $n pre-deploy line(s) missing from installed:"
    printf '%s\n' "$miss" | head -20 | sed 's/^/        /'
    rc=9
  else
    echo "  ok     $f (100% of pre-deploy content preserved)"
  fi
done
[ $rc = 9 ] && { echo "ABORT(9): prod content dropped by deploy — run rollback.sh."; exit 9; }
echo "PRESERVE_CHECK OK."
