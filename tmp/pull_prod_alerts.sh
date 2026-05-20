#!/usr/bin/env bash
# 6-hour-paginated prod alert pull via az vm run-command.
# az caps stdout at ~4KB; busy days exceed 4KB so we slice 6h windows.
# Output: one JSON file per 6h slice under tmp/prod_alerts/.
set -u
OUT_DIR="tmp/prod_alerts"
mkdir -p "$OUT_DIR"
START="${1:-2026-04-30}"
END="${2:-2026-05-18}"

S_S=$(date -d "${START} 00:00 UTC" +%s)
E_S=$(date -d "${END} 00:00 UTC" +%s)
SLICE=21600   # 6 hours in seconds
CUR=$S_S

while [ "$CUR" -lt "$E_S" ]; do
  NXT_S=$((CUR + SLICE))
  if [ "$NXT_S" -gt "$E_S" ]; then NXT_S=$E_S; fi
  TAG=$(date -u -d "@$CUR" +%Y-%m-%dT%H)
  CUR_ISO=$(date -u -d "@$CUR" +%Y-%m-%dT%H:%M:%S)
  NXT_ISO=$(date -u -d "@$NXT_S" +%Y-%m-%dT%H:%M:%S)
  OUT="$OUT_DIR/slice_${TAG}.json"
  # Cache hit: file exists, looks like a successful az response (has the
  # `[stdout]` marker), and has at least one pipe-row OR is a known empty
  # window. Earlier check used `"stdout` which never matches because the
  # az JSON escapes the marker as `\n[stdout]\n` inside the message field.
  if [ -s "$OUT" ] && grep -q '\[stdout\]' "$OUT" && ! grep -q 'Conflict' "$OUT"; then
    LINES=$(grep -c '|' "$OUT" || echo 0)
    BYTES=$(wc -c < "$OUT")
    # >=1 pipe row → cached with data. Otherwise treat <=300 bytes as a
    # cached empty window (the az success envelope alone is ~220 bytes).
    if [ "$LINES" -ge 1 ]; then
      echo "$TAG: cached ($LINES with |)"
      CUR=$NXT_S
      continue
    elif [ "$BYTES" -le 300 ]; then
      echo "$TAG: cached (empty window)"
      CUR=$NXT_S
      continue
    fi
  fi

  SQL="sqlite3 -separator \"|\" /home/azureuser/trading_corp/data/trading_corp.db \"SELECT ts, actor, json_extract(payload_json,'\$.signal') AS signal, json_extract(payload_json,'\$.symbol') AS symbol, json_extract(payload_json,'\$.price') AS price, json_extract(payload_json,'\$.interval') AS interval_raw FROM audit_event WHERE actor IN ('lord_otter','market_cypher') AND kind='webhook_received' AND ts >= '${CUR_ISO}' AND ts < '${NXT_ISO}' ORDER BY ts;\""

  echo -n "$TAG: pulling... "
  while true; do
    RESP=$(az vm run-command invoke -g rg-shared-prod -n tc-prod-vm \
      --command-id RunShellScript --scripts "$SQL" 2>&1)
    if echo "$RESP" | grep -q 'Conflict\|in progress'; then
      echo -n "(busy) "
      sleep 5
      continue
    fi
    break
  done
  echo "$RESP" > "$OUT"
  ROWS=$(echo "$RESP" | grep -c '|') || true
  # Look for truncation: response starts mid-row (no leading ISO timestamp)
  FIRST_DATA_LINE=$(echo "$RESP" | sed -n 's/.*\[stdout\]\\n\(.*\)/\1/p' | head -c 20)
  if [[ -n "$FIRST_DATA_LINE" && ! "$FIRST_DATA_LINE" =~ ^[0-9]{4}- ]]; then
    echo "TRUNCATED — first chars: $FIRST_DATA_LINE"
  else
    echo "done (~$ROWS rows)"
  fi
  CUR=$NXT_S
done

# Clean up the old day_*.json files (superseded by slice_*.json)
rm -f "$OUT_DIR"/day_*.json 2>/dev/null || true
echo "All slices fetched to $OUT_DIR/"
