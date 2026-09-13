set -u
ROOT=/home/azureuser/trading_corp
echo "===MD5==="
for f in "$ROOT"/config/*.yaml; do printf '%s  %s\n' "$(tr -d '\r' < "$f" | md5sum | cut -d' ' -f1)" "$(basename "$f")"; done
echo "===B64BEGIN==="
base64 -w0 "$ROOT/config/strategies.yaml"
echo ""
echo "===B64END==="
