#!/usr/bin/env bash
# Local generator: produce one self-contained staging script per runtime file.
# Each staged script (run as root via az) decodes gzip+base64 -> $APPROOT/<file>.new,
# matches owner+mode to the live file, and md5-verifies against the TARGET.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
OUT=cc
declare -A TARGET=(
  ["config/strategies.yaml"]="ce2f1c0ee5fc"
  ["trading_corp/agents/divisions/pmcc_robinhood.py"]="0d199b237c05"
  ["trading_corp/web/pmcc_pricing.py"]="af9a674e79aa"
  ["trading_corp/web/routes.py"]="c15e84c74521"
)
declare -A SLUG=(
  ["config/strategies.yaml"]="strategies_yaml"
  ["trading_corp/agents/divisions/pmcc_robinhood.py"]="pmcc_robinhood"
  ["trading_corp/web/pmcc_pricing.py"]="pmcc_pricing"
  ["trading_corp/web/routes.py"]="routes"
)
for f in "${!TARGET[@]}"; do
  s="${SLUG[$f]}"; t="${TARGET[$f]}"; out="$OUT/stage_${s}.sh"
  {
    echo '#!/usr/bin/env bash'
    echo 'set +e'
    echo 'APPROOT=$(systemctl show -p WorkingDirectory --value trading-corp 2>/dev/null); [ -d "$APPROOT" ] || APPROOT=/home/azureuser/trading_corp'
    echo "F=\"$f\""
    echo "EXPECT=\"$t\""
    echo 'TMP=$(mktemp)'
    echo "cat > \"\$TMP\" <<'B64EOF'"
    git show "HEAD:$f" | tr -d '\r' | gzip -9 | base64 -w0
    echo ''
    echo 'B64EOF'
    echo 'base64 -d "$TMP" | gunzip > "$APPROOT/$F.new"'
    echo 'rm -f "$TMP"'
    echo 'chown --reference="$APPROOT/$F" "$APPROOT/$F.new" 2>/dev/null'
    echo 'chmod --reference="$APPROOT/$F" "$APPROOT/$F.new" 2>/dev/null'
    echo 'GOT=$(tr -d "\r" < "$APPROOT/$F.new" | md5sum | cut -c1-12)'
    echo 'echo "STAGED $F md5=$GOT expect=$EXPECT $([ "$GOT" = "$EXPECT" ] && echo OK || echo MISMATCH)"'
    echo 'ls -l "$APPROOT/$F.new"'
  } > "$out"
  echo "generated $out ($(wc -c < "$out") bytes)"
done
