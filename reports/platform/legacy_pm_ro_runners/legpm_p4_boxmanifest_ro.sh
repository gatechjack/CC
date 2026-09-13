set -u
ROOT=/home/azureuser/trading_corp
echo "### PHASE-4 BOX MANIFEST (READ-ONLY, CR-stripped md5) $(date -u +%FT%TZ) ###"
echo "===OVERLAY==="
cd "$ROOT" || { echo "cd ROOT FAILED"; exit 2; }
find . -type f \
  ! -path './.git/*' ! -path './venv/*' ! -path './data/*' ! -path '*/__pycache__/*' \
  ! -name '*.pyc' ! -path './logs/*' ! -name '*.bak_*' ! -name '*.bak' ! -name '*.tmp*' \
  ! -path './node_modules/*' ! -path './.pytest_cache/*' ! -name '*.db' ! -name '*.db-*' \
  ! -name '*.sqlite' ! -path './.mypy_cache/*' \
  -print0 | while IFS= read -r -d '' f; do
    printf '%s  %s\n' "$(tr -d '\r' < "$f" | md5sum | cut -c1-32)" "${f#./}"
  done | LC_ALL=C sort
echo "===OVERLAY_END==="
echo "===PEAD==="
if [ -d /home/azureuser/pead_earnings ]; then
  cd /home/azureuser/pead_earnings
  find . -maxdepth 2 -type f ! -name '*.db' ! -name '*.db-*' ! -path '*/__pycache__/*' ! -name '*.pyc' -print0 | while IFS= read -r -d '' f; do
    printf '%s  %s\n' "$(tr -d '\r' < "$f" | md5sum | cut -c1-32)" "${f#./}"; done | LC_ALL=C sort
fi
echo "===PEAD_END==="
echo "===CARD==="
if [ -d /home/azureuser/card_assets ]; then
  cd /home/azureuser/card_assets
  find . -maxdepth 1 -type f -name '*.py' -print0 | while IFS= read -r -d '' f; do
    printf '%s  %s\n' "$(tr -d '\r' < "$f" | md5sum | cut -c1-32)" "${f#./}"; done | LC_ALL=C sort
fi
echo "===CARD_END==="
echo "===UNITS==="
cd /etc/systemd/system 2>/dev/null && for f in trading-corp*.service trading-corp*.timer pead-earnings*.service pead-earnings*.timer sfp-card-watcher.service prediction-markets-web.service card-watcher*.service; do
  [ -f "$f" ] && printf '%s  %s\n' "$(tr -d '\r' < "$f" | md5sum | cut -c1-32)" "$f"; done | LC_ALL=C sort
echo "===UNITS_END==="
echo "===CRON==="
crontab -l 2>/dev/null | grep -vE '^\s*#' | grep -vE '^\s*$' | LC_ALL=C sort
echo "===CRON_END==="
echo "===COUNTS==="
echo "overlay_files=$(cd "$ROOT" && find . -type f ! -path './.git/*' ! -path './venv/*' ! -path './data/*' ! -path '*/__pycache__/*' ! -name '*.pyc' ! -path './logs/*' ! -name '*.bak_*' ! -name '*.bak' ! -name '*.tmp*' ! -path './node_modules/*' ! -path './.pytest_cache/*' ! -name '*.db' ! -name '*.db-*' ! -name '*.sqlite' ! -path './.mypy_cache/*' | wc -l)"
echo "===DONE==="
