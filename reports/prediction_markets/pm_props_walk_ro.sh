cd /home/azureuser/trading_corp || exit 2
echo "=== ENGINE (expect PID 619011, boot 2026-10-02 20:08:06Z) ==="
systemctl show trading-corp -p MainPID -p ActiveState -p SubState -p ActiveEnterTimestamp 2>&1
echo "=== A. 5 props-ahead files: crsha16 + mtime (loaded in PID619011 iff mtime < boot 20:08:06Z) ==="
for f in trading_corp/data/player_props_match.py trading_corp/data/sports_structural_match.py trading_corp/data/mlb_poly_kalshi_match.py trading_corp/prediction_markets/execution.py trading_corp/prediction_markets/live_driver.py; do if [ -f "$f" ]; then echo "$f crsha16=$(tr -d '\r'<"$f"|sha256sum|cut -c1-16) mtime=$(date -u -r "$f" +%FT%TZ)"; else echo "$f ABSENT"; fi; done
echo "(expect crsha16 == b4b2c089: player_props e3f85ba977e0db3c / sports b787d1f11b63af61 / mlb 83881fc73f6543dd / execution a8a03b59e422a8a2 / live_driver 4bbe3023610aaadc)"
echo "=== B. main.py 16.7 ==="
echo "bitunix=$(grep -c bitunix trading_corp/main.py) mace_ci=$(grep -ci mace trading_corp/main.py) phantom=$(grep -c 'mace\|MACE' trading_corp/main.py) main_crmd5-12=$(tr -d '\r'<trading_corp/main.py|md5sum|cut -c1-12)"
echo "=== C. schema head (data/trading_corp.db schema_version; expect 24) ==="
sqlite3 -readonly data/trading_corp.db "SELECT MAX(version) FROM schema_version;" 2>&1
echo "=== D. FULL-TREE WALK crsha16 (trading_corp + config) ==="
find trading_corp config -type f \( -name '*.py' -o -name '*.yaml' -o -name '*.yml' -o -name '*.txt' -o -name '*.json' -o -name '*.html' -o -name '*.js' -o -name '*.css' \) ! -path '*/__pycache__/*' -print0 | while IFS= read -r -d '' f; do printf "%s %s\n" "$(tr -d '\r'<"$f"|sha256sum|cut -c1-16)" "$f"; done | sort -k2
echo "DONE_WALK"
