#!/usr/bin/env bash
# PEAD joint-deploy — STEP 3 (apply). Runs ON PROD. Backs up the 10 touched shared
# files, installs the 15-file payload. Does NOT restart the service (the window owns
# the restart). Default = DRY-RUN; pass --go to write.
#
#   ./apply.sh           # dry-run: drift guard only, no writes
#   ./apply.sh --go      # backup + install + integrity check
#
# Exit codes: 9 = prod drifted from superset source (rebuild). 8 = post-install md5
# mismatch. Any nonzero before --go means do NOT proceed to the window.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="${PROD_ROOT:-/home/azureuser/trading_corp}"
PL="$HERE/payload"
SUF=".bak-pre-pead-2026-06-23"
GO=0; [ "${1:-}" = "--go" ] && GO=1

EXISTING="config/divisions.yaml config/risk.yaml config/strategies.yaml config/data_providers.yaml \
trading_corp/main.py trading_corp/persistence/models.py trading_corp/agents/paper_trade_replay.py \
trading_corp/brokers/robinhood.py trading_corp/utils/market_data.py trading_corp/utils/secrets.py"
NEW="config/nasdaq_composite.txt trading_corp/agents/divisions/robinhood_pead.py \
trading_corp/agents/strategies/pead_strategy.py trading_corp/agents/strategies/pead_signal.py \
trading_corp/data/earnings_provider.py"

echo "== PEAD joint-deploy apply =="
echo "ROOT=$ROOT  PAYLOAD=$PL  MODE=$([ $GO = 1 ] && echo APPLY || echo DRY-RUN)"

# 0) DRIFT GUARD — prod EXISTING files must still match the blob the superset was built against
echo "-- drift guard (prod vs prod_source.md5) --"
fail=0
while read -r want f; do
  [ -z "${want:-}" ] && continue
  case "$want" in \#*) continue;; esac
  [ -f "$ROOT/$f" ] || { echo "  MISSING $f"; fail=1; continue; }
  got=$(md5sum "$ROOT/$f" | cut -d' ' -f1)
  if [ "$got" != "$want" ]; then echo "  DRIFT   $f  prod=$got  expected=$want"; fail=1
  else echo "  ok      $f"; fi
done < "$HERE/prod_source.md5"
[ $fail = 1 ] && { echo "ABORT(9): prod drifted from superset source — rebuild superset against fresh prod."; exit 9; }

if [ $GO = 0 ]; then echo "DRY-RUN OK (no writes). Re-run with --go to apply."; exit 0; fi

# 1) backup + install EXISTING (replace-in-place; .bak left for guard + rollback)
echo "-- backup + install (existing) --"
for f in $EXISTING; do
  cp -p "$ROOT/$f" "$ROOT/$f$SUF"
  install -m 644 "$PL/$f" "$ROOT/$f"
  echo "  installed  $f   (backup: $f$SUF)"
done
# 2) install NEW (no backup; net-new to prod)
echo "-- install (net-new) --"
for f in $NEW; do
  mkdir -p "$ROOT/$(dirname "$f")"
  install -m 644 "$PL/$f" "$ROOT/$f"
  echo "  installed  $f   (new)"
done
# 3) INTEGRITY — installed must equal payload.md5
echo "-- integrity (installed vs payload.md5) --"
ifail=0
while read -r want f; do
  [ -z "${want:-}" ] && continue
  case "$want" in \#*) continue;; esac
  got=$(md5sum "$ROOT/$f" | cut -d' ' -f1)
  if [ "$got" != "$want" ]; then echo "  MISMATCH $f"; ifail=1; else echo "  ok       $f"; fi
done < "$HERE/payload.md5"
[ $ifail = 1 ] && { echo "ABORT(8): post-install md5 mismatch — run rollback.sh."; exit 8; }

echo "-- backup paths (feed to the EXTENDED guard) --"
for f in $EXISTING; do echo "  $ROOT/$f$SUF"; done
echo "APPLY OK.  Next: ./preserve_check.sh \"$ROOT\"   then   ./bootsmoke.sh \"$ROOT\""
