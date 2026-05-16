#!/usr/bin/env bash
# Generate /tmp/deploy_1c_full.sh — a single self-contained deploy script with
# all 8 Phase 1C files inlined as base64 heredocs. Submitted to prod via
# `az vm run-command create --script @file` which has a much larger payload
# limit than `invoke`'s 28k.

set -euo pipefail

OUT=/tmp/deploy_1c_full.sh
TAG=pre-bitunix-1c-20260516-0202
BASE=/home/azureuser/trading_corp

# Source-of-truth: local repo on Windows
REPO="/c/Users/AA Incorporado/CC"

FILES=(
  "config/strategies.yaml"
  "trading_corp/web/data.py"
  "trading_corp/web/templates/division.html"
  "trading_corp/web/templates/partials/bitunix_score_panel.html"
  "trading_corp/agents/divisions/bitunix_position_reconciler.py"
  "trading_corp/web/templates/partials/bitunix_decision_flow.html"
  "trading_corp/web/templates/partials/bitunix_htf_panel.html"
  "trading_corp/web/templates/partials/bitunix_pa_panel.html"
)

MODIFY=(
  "config/strategies.yaml"
  "trading_corp/web/data.py"
  "trading_corp/web/templates/division.html"
  "trading_corp/web/templates/partials/bitunix_score_panel.html"
)

cat > "$OUT" <<'HEADER'
#!/usr/bin/env bash
set -euo pipefail
TAG="pre-bitunix-1c-20260516-0202"
BASE=/home/azureuser/trading_corp

echo "=== Phase 1C deploy starting ==="
date -u

# Backup the 4 modify-files
for f in config/strategies.yaml \
         trading_corp/web/data.py \
         trading_corp/web/templates/division.html \
         trading_corp/web/templates/partials/bitunix_score_panel.html; do
  if [ -f "$BASE/$f" ]; then
    cp "$BASE/$f" "$BASE/$f.$TAG"
    echo "BACKED-UP: $f -> $f.$TAG"
  else
    echo "WARN: $f not present on prod (cannot back up)"
  fi
done

# Stage decoded files in /tmp/1c-final/
rm -rf /tmp/1c-final
mkdir -p /tmp/1c-final
HEADER

# For each file, embed as base64 heredoc that decodes to /tmp/1c-final/<sanitized>
for src in "${FILES[@]}"; do
  sanitized=$(echo "$src" | tr / _)
  # LF-normalize before encoding — Windows checkout has CRLF, prod is LF.
  b64=$(tr -d '\r' < "$REPO/$src" | base64 -w0)
  raw_lf_size=$(tr -d '\r' < "$REPO/$src" | wc -c)
  echo "" >> "$OUT"
  echo "echo \"=== decoding $src ($raw_lf_size bytes LF-normalized) ===\"" >> "$OUT"
  echo "base64 -d > \"/tmp/1c-final/$sanitized\" <<'B64EOF'" >> "$OUT"
  echo "$b64" >> "$OUT"
  echo "B64EOF" >> "$OUT"
  # md5 check against LF-normalized version
  local_md5=$(tr -d '\r' < "$REPO/$src" | md5sum | awk '{print $1}')
  echo "ACTUAL_MD5=\$(md5sum \"/tmp/1c-final/$sanitized\" | awk '{print \$1}')" >> "$OUT"
  echo "EXPECTED_MD5='$local_md5'" >> "$OUT"
  echo "if [ \"\$ACTUAL_MD5\" != \"\$EXPECTED_MD5\" ]; then echo \"MD5 MISMATCH on $src: got \$ACTUAL_MD5 expected \$EXPECTED_MD5\"; exit 3; fi" >> "$OUT"
  echo "echo \"  md5 verified: \$EXPECTED_MD5\"" >> "$OUT"
done

cat >> "$OUT" <<'FOOTER'

echo "=== all 8 files decoded + md5-verified, moving into place ==="
mv /tmp/1c-final/config_strategies.yaml $BASE/config/strategies.yaml
mv /tmp/1c-final/trading_corp_web_data.py $BASE/trading_corp/web/data.py
mv /tmp/1c-final/trading_corp_web_templates_division.html $BASE/trading_corp/web/templates/division.html
mv /tmp/1c-final/trading_corp_web_templates_partials_bitunix_score_panel.html $BASE/trading_corp/web/templates/partials/bitunix_score_panel.html
mv /tmp/1c-final/trading_corp_agents_divisions_bitunix_position_reconciler.py $BASE/trading_corp/agents/divisions/bitunix_position_reconciler.py
mv /tmp/1c-final/trading_corp_web_templates_partials_bitunix_decision_flow.html $BASE/trading_corp/web/templates/partials/bitunix_decision_flow.html
mv /tmp/1c-final/trading_corp_web_templates_partials_bitunix_htf_panel.html $BASE/trading_corp/web/templates/partials/bitunix_htf_panel.html
mv /tmp/1c-final/trading_corp_web_templates_partials_bitunix_pa_panel.html $BASE/trading_corp/web/templates/partials/bitunix_pa_panel.html

echo "=== final on-disk md5s ==="
for f in config/strategies.yaml \
         trading_corp/web/data.py \
         trading_corp/web/templates/division.html \
         trading_corp/web/templates/partials/bitunix_score_panel.html \
         trading_corp/agents/divisions/bitunix_position_reconciler.py \
         trading_corp/web/templates/partials/bitunix_decision_flow.html \
         trading_corp/web/templates/partials/bitunix_htf_panel.html \
         trading_corp/web/templates/partials/bitunix_pa_panel.html; do
  md5sum "$BASE/$f"
done

echo "=== clearing __pycache__ in touched dirs ==="
rm -rf $BASE/trading_corp/agents/divisions/__pycache__ 2>/dev/null || true
rm -rf $BASE/trading_corp/web/__pycache__ 2>/dev/null || true

echo "=== restarting trading-corp ==="
sudo systemctl restart trading-corp
sleep 12

echo "=== service status ==="
systemctl is-active trading-corp

echo "=== healthz probe ==="
curl -fsS http://127.0.0.1:8000/healthz && echo

echo "=== recent journalctl (last 40 lines) ==="
sudo journalctl -u trading-corp -n 40 --no-pager

echo "=== Phase 1C deploy COMPLETE ==="
date -u
FOOTER

chmod +x "$OUT"
wc -c "$OUT"
echo "Built: $OUT"
