echo "=== PM HEALTH RO PROBE 2 ==="
date -u +%FT%TZ
ROOT=/home/azureuser/trading_corp
PMDB=$ROOT/data/prediction_markets.db
LEG=$ROOT/data/trading_corp.db
RO_PM="file:$PMDB?mode=ro"
RO_LEG="file:$LEG?mode=ro"
C16=$(date -u -d 2026-10-16 +%s)
C01=$(date -u -d 2026-11-01 +%s)
echo "anchors: C16=$C16 C01=$C01"

echo ""; echo "--- [0] schemas (attachment, event, shard) ---"
sqlite3 "$RO_PM" ".schema pm_subdivision_attachment" 2>&1
sqlite3 "$RO_PM" ".schema pm_subdivision_attachment_event" 2>&1
sqlite3 "$RO_PM" ".schema pm_shard_balance_snapshot" 2>&1

echo ""; echo "--- [1] AUTH-LATCH reason verbatim (marc, trey samples) ---"
sqlite3 "$RO_LEG" -cmd ".mode line" "SELECT key, value_json FROM agent_state WHERE agent='pm_live' AND key IN ('arm:kalshi_marc:nfl','arm:kalshi_trey:nfl','arm:kalshi_marc:mlb','arm:kalshi_trey:itf');" 2>&1

echo ""; echo "--- [2] ERROR+REJECTED rows since 2026-10-16, grouped (status x msg) ---"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, outcome_status, substr(error_detail,1,95) AS err, COUNT(*) n, MIN(datetime(COALESCE(response_ts,submitted_ts),'unixepoch')) first, MAX(datetime(COALESCE(response_ts,submitted_ts),'unixepoch')) last FROM pm_subdivision_order WHERE dry_run=0 AND outcome_status IN ('error','rejected') AND COALESCE(response_ts,submitted_ts)>=$C16 GROUP BY account_id, outcome_status, err ORDER BY last;" 2>&1

echo ""; echo "--- [3] full error_detail, last 12 error/rejected for marc+trey ---"
sqlite3 "$RO_PM" -cmd ".mode line" "SELECT account_id, category, datetime(COALESCE(response_ts,submitted_ts),'unixepoch') ts, outcome_status, error_detail FROM pm_subdivision_order WHERE dry_run=0 AND outcome_status IN ('error','rejected') AND account_id IN ('kalshi_marc','kalshi_trey') ORDER BY COALESCE(response_ts,submitted_ts) DESC LIMIT 12;" 2>&1

echo ""; echo "--- [4] per-account per-day status breakdown since 2026-10-16 ---"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT date(COALESCE(response_ts,submitted_ts),'unixepoch') d, account_id, outcome_status, COUNT(*) n FROM pm_subdivision_order WHERE dry_run=0 AND COALESCE(response_ts,submitted_ts)>=$C16 GROUP BY d, account_id, outcome_status ORDER BY d, account_id, outcome_status;" 2>&1

echo ""; echo "--- [5] jack recent 15 attempts ---"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT category, datetime(COALESCE(response_ts,submitted_ts),'unixepoch') ts, outcome_status, is_exit, fill_count, substr(error_detail,1,45) err FROM pm_subdivision_order WHERE dry_run=0 AND account_id='kalshi_jack' ORDER BY COALESCE(response_ts,submitted_ts) DESC LIMIT 15;" 2>&1
echo "-- 5b karen recent 15 attempts --"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT category, datetime(COALESCE(response_ts,submitted_ts),'unixepoch') ts, outcome_status, is_exit, fill_count, substr(error_detail,1,45) err FROM pm_subdivision_order WHERE dry_run=0 AND account_id='kalshi_karen' ORDER BY COALESCE(response_ts,submitted_ts) DESC LIMIT 15;" 2>&1

echo ""; echo "--- [6] fills per account per day since 2026-10-16 (INTEGER anchor) ---"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT date(COALESCE(response_ts,submitted_ts),'unixepoch') d, account_id, COUNT(*) fills FROM pm_subdivision_order WHERE dry_run=0 AND outcome_status='filled' AND is_exit=0 AND COALESCE(response_ts,submitted_ts)>=$C16 GROUP BY d, account_id ORDER BY d, account_id;" 2>&1

echo ""; echo "--- [7] CASH: latest shard-balance snapshot per account ---"
sqlite3 "$RO_PM" -cmd ".mode line" "SELECT s.account_id, datetime(s.snapshot_ts,'unixepoch') ts, s.total_dollars, s.by_shard_json FROM pm_shard_balance_snapshot s JOIN (SELECT account_id, MAX(snapshot_ts) m FROM pm_shard_balance_snapshot GROUP BY account_id) t ON s.account_id=t.account_id AND s.snapshot_ts=t.m ORDER BY s.account_id;" 2>&1

echo ""; echo "--- [8] OPEN POSITIONS (journal net-open) summary per account ---"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, COUNT(*) open_legs, ROUND(SUM(net_cost),2) net_cost_basis FROM (SELECT account_id, ticker, outcome_leg, SUM(CASE WHEN is_exit=0 THEN fill_count ELSE -fill_count END) net_ct, SUM(CASE WHEN is_exit=0 THEN fill_count*fill_price ELSE -fill_count*fill_price END) net_cost FROM pm_subdivision_order WHERE dry_run=0 AND outcome_status='filled' GROUP BY account_id, ticker, outcome_leg HAVING ABS(net_ct)>0.0001) GROUP BY account_id ORDER BY account_id;" 2>&1

echo ""; echo "--- [9] OPEN POSITIONS detail (oldest-touched first) ---"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, ticker, outcome_leg, ROUND(SUM(CASE WHEN is_exit=0 THEN fill_count ELSE -fill_count END),2) net_ct, datetime(MAX(COALESCE(response_ts,submitted_ts)),'unixepoch') last_touch FROM pm_subdivision_order WHERE dry_run=0 AND outcome_status='filled' GROUP BY account_id, ticker, outcome_leg HAVING ABS(SUM(CASE WHEN is_exit=0 THEN fill_count ELSE -fill_count END))>0.0001 ORDER BY account_id, last_touch;" 2>&1

echo ""; echo "--- [10] SETTLEMENT watch: non-standard close_source since 2026-10-16 ---"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, close_source, COUNT(*) n, MIN(datetime(COALESCE(settled_ts,response_ts),'unixepoch')) first, MAX(datetime(COALESCE(settled_ts,response_ts),'unixepoch')) last FROM pm_subdivision_order WHERE close_source IS NOT NULL AND close_source NOT IN ('settlement','settlement_void') AND COALESCE(settled_ts,response_ts)>=$C16 GROUP BY account_id, close_source ORDER BY last;" 2>&1
echo "-- 10b all close_source classes since 2026-10-16 --"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT close_source, COUNT(*) n FROM pm_subdivision_order WHERE close_source IS NOT NULL AND COALESCE(settled_ts,response_ts)>=$C16 GROUP BY close_source ORDER BY n DESC;" 2>&1

echo ""; echo "--- [11] last settlement booked per account + realized pnl since 2026-10-16 ---"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, datetime(MAX(COALESCE(settled_ts,response_ts)),'unixepoch') last_settle, COUNT(*) n_settle FROM pm_subdivision_order WHERE close_source LIKE 'settlement%' GROUP BY account_id ORDER BY account_id;" 2>&1
echo "-- 11b realized pnl since 2026-10-16 (settled closes) --"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, ROUND(SUM(realized_pnl),2) realized, COUNT(*) n FROM pm_subdivision_order WHERE realized_pnl IS NOT NULL AND COALESCE(settled_ts,response_ts)>=$C16 GROUP BY account_id ORDER BY account_id;" 2>&1

echo ""; echo "--- [12] ATTACHMENT events since 2026-10-16 ---"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT datetime(ts,'unixepoch') ts, account_id, category, substr(wallet,1,14) wallet, action FROM pm_subdivision_attachment_event WHERE ts>=$C16 ORDER BY ts;" 2>&1
echo "-- 12b active attachments per account now --"
sqlite3 "$RO_PM" -cmd ".mode column" "SELECT account_id, COUNT(*) n_active FROM pm_subdivision_attachment WHERE active=1 GROUP BY account_id ORDER BY account_id;" 2>&1

echo "=== END PROBE 2 ==="
