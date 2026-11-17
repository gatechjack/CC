echo "=== PV2 PRE-RESTART CHECKLIST PROBE (READ-ONLY) ==="
date -u +%FT%TZ
ROOT=/home/azureuser/trading_corp
PMDB=$ROOT/data/prediction_markets.db
LEG=$ROOT/data/trading_corp.db
RO_PM="file:$PMDB?mode=ro"
RO_LEG="file:$LEG?mode=ro"

echo "--- [1] BOOK FLAT re-verify (engine _HELD_SQL grain; expect NO rows + exact equality) ---"
sqlite3 -cmd ".timeout 8000" "$RO_PM" -cmd ".mode column" "SELECT account_id, COUNT(*) open_legs FROM (SELECT account_id, category, wallet, UPPER(ticker) tk, outcome_leg, SUM(CASE WHEN is_exit=0 THEN COALESCE(fill_count,0) ELSE 0 END) - SUM(CASE WHEN is_exit=1 THEN COALESCE(fill_count,0) ELSE 0 END) net_ct FROM pm_subdivision_order WHERE dry_run=0 AND outcome_status='filled' AND ticker IS NOT NULL AND outcome_leg IN ('yes','no') GROUP BY account_id, category, wallet, UPPER(ticker), outcome_leg HAVING net_ct>0.0001) GROUP BY account_id;" 2>&1
echo "(no rows above = zero open legs)"
sqlite3 -cmd ".timeout 8000" "$RO_PM" -cmd ".mode column" "SELECT account_id, ROUND(SUM(CASE WHEN is_exit=0 THEN fill_count ELSE 0 END),0) ct_entered, ROUND(SUM(CASE WHEN is_exit=1 THEN fill_count ELSE 0 END),0) ct_exited FROM pm_subdivision_order WHERE dry_run=0 AND outcome_status='filled' GROUP BY account_id;" 2>&1

echo "--- [2] ARMED categories + their ACTIVE attachment counts (zero-attach armed cats = not driven after restart until attached) ---"
sqlite3 -cmd ".timeout 8000" "$RO_LEG" -cmd "ATTACH 'file:$PMDB?mode=ro' AS pm" -cmd ".mode column" "SELECT d.acct, d.cat, (SELECT COUNT(*) FROM pm.pm_subdivision_attachment t WHERE t.account_id=d.acct AND t.category=d.cat AND t.active=1) n_att FROM (SELECT substr(key,5,instr(substr(key,5),':')-1) acct, substr(key,5+instr(substr(key,5),':')) cat, json_extract(value_json,'\$.armed') armed FROM agent_state WHERE agent='pm_live' AND key LIKE 'arm:%' AND key<>'arm:global') d WHERE d.armed=1 ORDER BY d.acct, n_att, d.cat;" 2>&1

echo "--- [3] contract sizes per sub-division (schema + armed subs) ---"
echo "pm_subdivision cols:"; sqlite3 "$RO_PM" "SELECT group_concat(name,' ') FROM pragma_table_info('pm_subdivision');" 2>&1
sqlite3 -cmd ".timeout 8000" "$RO_PM" -cmd ".mode line" "SELECT * FROM pm_subdivision LIMIT 2;" 2>&1

echo "--- [4] other divisions (one line each; recommend nothing) ---"
echo "MACE rungs by status:"; sqlite3 -cmd ".timeout 8000" "$RO_LEG" "SELECT status, COUNT(*) FROM mace_rung GROUP BY status;" 2>&1
echo "core position table shape:"; sqlite3 "$RO_LEG" "SELECT group_concat(name,' ') FROM pragma_table_info('position');" 2>&1
sqlite3 -cmd ".timeout 8000" "$RO_LEG" -cmd ".mode column" "SELECT division, COUNT(*) n FROM position GROUP BY division;" 2>&1
echo "division-ish tables:"; sqlite3 "$RO_LEG" "SELECT group_concat(name,' ') FROM sqlite_master WHERE type='table' AND (name LIKE 'pead%' OR name LIKE 'pmcc%' OR name LIKE '%bitunix%');" 2>&1

echo "--- [5] last Kalshi-key proof-of-life per account (fills; attestation itself is UI-only) ---"
sqlite3 -cmd ".timeout 8000" "$RO_PM" -cmd ".mode column" "SELECT account_id, datetime(MAX(COALESCE(response_ts,submitted_ts)),'unixepoch') last_fill_utc FROM pm_subdivision_order WHERE dry_run=0 AND outcome_status='filled' GROUP BY account_id;" 2>&1
echo "=== END CHECKLIST PROBE ==="
