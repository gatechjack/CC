#!/usr/bin/env bash
# Phase 1C deploy script. Uses az vm run-command (SSH port 22 blocked from this network).
# Pattern: stage base64 chunks on prod under /tmp/1c/, then decode+move atomically.

set -euo pipefail

TAG="pre-bitunix-1c-20260516-0202"
RG="RG-SHARED-PROD"
VM="tc-prod-vm"
BASE="/home/azureuser/trading_corp"

LOCAL_FILES=(
  "config/strategies.yaml"
  "trading_corp/web/data.py"
  "trading_corp/web/templates/division.html"
  "trading_corp/web/templates/partials/bitunix_score_panel.html"
  "trading_corp/agents/divisions/bitunix_position_reconciler.py"
  "trading_corp/web/templates/partials/bitunix_decision_flow.html"
  "trading_corp/web/templates/partials/bitunix_htf_panel.html"
  "trading_corp/web/templates/partials/bitunix_pa_panel.html"
)

MODIFY_FILES=(
  "config/strategies.yaml"
  "trading_corp/web/data.py"
  "trading_corp/web/templates/division.html"
  "trading_corp/web/templates/partials/bitunix_score_panel.html"
)

# --- 1. Stage chunked base64 uploads ----------------------------------------
azrun() {
  az vm run-command invoke -g "$RG" -n "$VM" \
    --command-id RunShellScript --scripts "$1" \
    --query "value[0].message" -o tsv 2>&1
}

CHUNK=22000   # raw bytes per chunk; b64 expands ~1.33x → ~29k payload, under az 30k cap

# Reset staging dir on prod
azrun "rm -rf /tmp/1c && mkdir -p /tmp/1c"

for src in "${LOCAL_FILES[@]}"; do
  remote_b64="/tmp/1c/$(echo "$src" | tr / _).b64"
  size=$(wc -c < "$src")
  echo "[upload] $src ($size bytes) → $remote_b64"

  > /tmp/upload_buf
  base64 -w0 "$src" > /tmp/upload_buf

  # Split base64 string into 29k chunks and upload with >> append
  offset=0
  total=$(wc -c < /tmp/upload_buf)
  first=1
  while [ $offset -lt $total ]; do
    chunk=$(dd if=/tmp/upload_buf bs=1 skip=$offset count=29000 2>/dev/null)
    if [ $first -eq 1 ]; then
      azrun "echo -n '$chunk' > $remote_b64" >/dev/null
      first=0
    else
      azrun "echo -n '$chunk' >> $remote_b64" >/dev/null
    fi
    offset=$((offset + 29000))
  done

  # Validate b64 round-trip equals local md5
  local_md5=$(md5sum "$src" | awk '{print $1}')
  remote_md5=$(azrun "base64 -d $remote_b64 | md5sum | awk '{print \$1}'" | grep -oE '[a-f0-9]{32}' | head -1)
  if [ "$local_md5" != "$remote_md5" ]; then
    echo "  MD5 MISMATCH: local=$local_md5 remote=$remote_md5  →  ABORT"
    exit 2
  fi
  echo "  md5 verified: $local_md5"
done

# --- 2. Atomic apply (backup, decode, move, clear pycache, restart) ---------
APPLY_SCRIPT=$(cat <<EOF
set -e
TAG=$TAG
BASE=$BASE

# Backup the 4 modify-files
for f in ${MODIFY_FILES[@]}; do
  cp \$BASE/\$f \$BASE/\$f.\$TAG
done

# Decode all 8 staged b64s to a staging dir mirroring the destination layout
mkdir -p /tmp/1c/decoded
for b in /tmp/1c/*.b64; do
  base64 -d \$b > \${b%.b64}.dec
done

# Move into place (matches the underscore-encoded mapping from upload)
mv /tmp/1c/config_strategies.yaml.dec \$BASE/config/strategies.yaml
mv /tmp/1c/trading_corp_web_data.py.dec \$BASE/trading_corp/web/data.py
mv /tmp/1c/trading_corp_web_templates_division.html.dec \$BASE/trading_corp/web/templates/division.html
mv /tmp/1c/trading_corp_web_templates_partials_bitunix_score_panel.html.dec \$BASE/trading_corp/web/templates/partials/bitunix_score_panel.html
mv /tmp/1c/trading_corp_agents_divisions_bitunix_position_reconciler.py.dec \$BASE/trading_corp/agents/divisions/bitunix_position_reconciler.py
mv /tmp/1c/trading_corp_web_templates_partials_bitunix_decision_flow.html.dec \$BASE/trading_corp/web/templates/partials/bitunix_decision_flow.html
mv /tmp/1c/trading_corp_web_templates_partials_bitunix_htf_panel.html.dec \$BASE/trading_corp/web/templates/partials/bitunix_htf_panel.html
mv /tmp/1c/trading_corp_web_templates_partials_bitunix_pa_panel.html.dec \$BASE/trading_corp/web/templates/partials/bitunix_pa_panel.html

# Final md5 verify on the 8 deployed paths
for f in ${LOCAL_FILES[@]}; do
  md5sum \$BASE/\$f
done

# Clear pycache in touched code dirs
rm -rf \$BASE/trading_corp/agents/divisions/__pycache__ 2>/dev/null || true
rm -rf \$BASE/trading_corp/web/__pycache__ 2>/dev/null || true

# Restart
sudo systemctl restart trading-corp
sleep 12
systemctl is-active trading-corp
curl -fsS http://127.0.0.1:8000/healthz && echo
EOF
)
azrun "$APPLY_SCRIPT"
