echo "=== PM HEALTH RO PROBE 3 ==="
date -u +%FT%TZ
ROOT=/home/azureuser/trading_corp
PMDB=$ROOT/data/prediction_markets.db
RO_PM="file:$PMDB?mode=ro"
NOW=$(date +%s)
DAY=$((NOW-86400))

echo ""; echo "--- [1] HELD positions (engine _HELD_SQL grain: account,category,wallet,ticker,leg) ---"
echo "-- 1a summary: open legs + cost-basis per account (entered>exited) --"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, COUNT(*) open_legs, ROUND(SUM(net_ct),2) net_contracts, ROUND(SUM(entry_cost_rem),2) cost_basis FROM (SELECT account_id, category, wallet, UPPER(ticker) tk, outcome_leg, SUM(CASE WHEN is_exit=0 THEN COALESCE(fill_count,0) ELSE 0 END) - SUM(CASE WHEN is_exit=1 THEN COALESCE(fill_count,0) ELSE 0 END) net_ct, SUM(CASE WHEN is_exit=0 THEN COALESCE(fill_count,0)*COALESCE(fill_price,0) ELSE 0 END) entry_cost_rem FROM pm_subdivision_order WHERE dry_run=0 AND outcome_status='filled' AND ticker IS NOT NULL AND outcome_leg IN ('yes','no') GROUP BY account_id, category, wallet, UPPER(ticker), outcome_leg HAVING net_ct>0.0001) GROUP BY account_id ORDER BY account_id;" 2>&1
echo "-- 1b grand totals: filled entry vs exit rows (sanity) --"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, SUM(CASE WHEN is_exit=0 THEN 1 ELSE 0 END) entry_rows, SUM(CASE WHEN is_exit=1 THEN 1 ELSE 0 END) exit_rows, ROUND(SUM(CASE WHEN is_exit=0 THEN fill_count ELSE 0 END),0) ct_entered, ROUND(SUM(CASE WHEN is_exit=1 THEN fill_count ELSE 0 END),0) ct_exited FROM pm_subdivision_order WHERE dry_run=0 AND outcome_status='filled' GROUP BY account_id ORDER BY account_id;" 2>&1
echo "-- 1c ANY held leg at all (top 20 by net_ct) --"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, category, UPPER(ticker) tk, outcome_leg, ROUND(SUM(CASE WHEN is_exit=0 THEN COALESCE(fill_count,0) ELSE -COALESCE(fill_count,0) END),2) net_ct FROM pm_subdivision_order WHERE dry_run=0 AND outcome_status='filled' AND ticker IS NOT NULL AND outcome_leg IN ('yes','no') GROUP BY account_id, category, UPPER(ticker), outcome_leg HAVING net_ct>0.0001 ORDER BY net_ct DESC LIMIT 20;" 2>&1

echo ""; echo "--- [2] shard snapshot: has_breakdown + freshness (reads succeeding?) ---"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT s.account_id, datetime(s.snapshot_ts,'unixepoch') ts, s.has_breakdown, ROUND(s.total_dollars,2) tot FROM pm_shard_balance_snapshot s JOIN (SELECT account_id, MAX(snapshot_ts) m FROM pm_shard_balance_snapshot GROUP BY account_id) t ON s.account_id=t.account_id AND s.snapshot_ts=t.m ORDER BY s.account_id;" 2>&1
echo "-- 2b snapshots written in last 24h per account (is the snapshot task alive for all 4?) --"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, COUNT(*) n_24h, SUM(has_breakdown) n_read_ok FROM pm_shard_balance_snapshot WHERE snapshot_ts>=$DAY GROUP BY account_id ORDER BY account_id;" 2>&1

echo ""; echo "--- [3] journal (retained ~2d): PM driver + auth + settlement lines ---"
echo "-- 3a shard/auth/attestation read failures --"
journalctl -u trading-corp --no-pager 2>/dev/null | grep -iE "attestation|location attestation|shard-balance read FAILED|open-exposure read FAILED|403|401|auth_failure" | tail -30
echo "-- 3b pm_live_driver settlement/boot lines --"
journalctl -u trading-corp --no-pager 2>/dev/null | grep -iE "pm_live_driver|settlement-scan|boot-reconcile|pm settlement" | tail -25
echo "-- 3c classify tracebacks: exception summary lines (last line of each) --"
journalctl -u trading-corp --no-pager 2>/dev/null | grep -iE "Error:|Exception:|Error\(|raise |Timeout|NoneType" | grep -ivE "error_detail|OrderPlacementError\('kalshi" | tail -30

echo ""; echo "--- [4] PM file CR-stripped md5 (first8) on box ---"
for f in prediction_markets/execution.py prediction_markets/settlement.py prediction_markets/arm.py prediction_markets/boot_reconcile.py prediction_markets/live_driver.py prediction_markets/heartbeat.py prediction_markets/db.py prediction_markets/driver_roster.py brokers/kalshi.py brokers/kalshi_live.py scripts/pm_cli.py; do
  if [ -f "$ROOT/trading_corp/$f" ]; then a=$(tr -d '\r' < "$ROOT/trading_corp/$f" | md5sum | cut -c1-8); else a=MISSING; fi
  printf '%-44s %s\n' "$f" "$a"
done

echo "=== END PROBE 3 ==="
