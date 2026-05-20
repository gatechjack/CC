#!/usr/bin/env bash
set -u
DB=/home/azureuser/trading_corp/data/trading_corp.db

echo "=== UTC now ==="
date -u

echo
echo "=== Q1a: PCT pruner audit rows (kind=pct_stale_prune, apply=true, last 3) ==="
sqlite3 -header -column "$DB" <<'SQL'
SELECT ts,
       json_extract(payload_json,'$.apply')      AS apply,
       json_extract(payload_json,'$.candidates') AS candidates,
       json_extract(payload_json,'$.deleted')    AS deleted
  FROM audit_event
 WHERE kind='pct_stale_prune'
   AND json_extract(payload_json,'$.apply')='true'
 ORDER BY ts DESC LIMIT 3;
SQL

echo
echo "=== Q1b: ALL pct_stale_prune audit rows (last 5, both dry-run + apply) ==="
sqlite3 -header -column "$DB" <<'SQL'
SELECT ts,
       json_extract(payload_json,'$.apply')      AS apply,
       json_extract(payload_json,'$.candidates') AS candidates,
       json_extract(payload_json,'$.deleted')    AS deleted
  FROM audit_event
 WHERE kind='pct_stale_prune'
 ORDER BY ts DESC LIMIT 5;
SQL

echo
echo "=== Q1c: PCT would_have_placed count (was 1707 pre-pruner; expect ~1253 post-prune) ==="
sqlite3 -header -column "$DB" <<'SQL'
SELECT COUNT(*) AS pct_pending_count
  FROM audit_event
 WHERE actor='polymarket_copy_trader' AND kind='would_have_placed';
SQL

echo
echo "=== Q1d: systemd timer status for trading-corp-pct-pruner.timer ==="
systemctl status trading-corp-pct-pruner.timer --no-pager 2>&1 | head -20
echo
echo "--- Last run journal (timer-triggered service) ---"
journalctl -u trading-corp-pct-pruner.service --since "2026-05-17 11:00:00" --no-pager 2>&1 | tail -40

echo
echo "=== Q2: target_iso on fresh kalshi_weather audit rows (since 2026-05-17T03:09:30+00:00) ==="
sqlite3 -header -column "$DB" <<'SQL'
SELECT ts,
       json_extract(payload_json,'$.ticker')     AS ticker,
       json_extract(payload_json,'$.target_iso') AS target_iso,
       json_extract(payload_json,'$.expires_at') AS expires_at
  FROM audit_event
 WHERE actor='kalshi_weather_arb' AND kind='would_have_placed'
   AND ts >= '2026-05-17T03:09:30+00:00'
 ORDER BY ts ASC LIMIT 10;
SQL

echo
echo "=== Q2b: count of fresh kalshi_weather would_have_placed rows since target_iso ship ==="
sqlite3 -header -column "$DB" <<'SQL'
SELECT COUNT(*) AS n_rows_since_target_iso_ship,
       SUM(CASE WHEN json_extract(payload_json,'$.target_iso') IS NOT NULL THEN 1 ELSE 0 END) AS with_target_iso,
       SUM(CASE WHEN json_extract(payload_json,'$.target_iso') IS     NULL THEN 1 ELSE 0 END) AS missing_target_iso
  FROM audit_event
 WHERE actor='kalshi_weather_arb' AND kind='would_have_placed'
   AND ts >= '2026-05-17T03:09:30+00:00';
SQL

echo
echo "=== Q3: post-cutoff RT win rate on kalshi_weather + kalshi_crypto ==="
sqlite3 -header -column "$DB" <<'SQL'
SELECT division,
       COUNT(*) AS n,
       SUM(won) AS wins,
       ROUND(100.0*SUM(won)/NULLIF(COUNT(*),0),1) AS wr_pct,
       ROUND(SUM(realized_pnl),2) AS pnl
  FROM kalshi_round_trips
 WHERE division IN ('kalshi_weather','kalshi_crypto')
   AND entry_ts >= CASE division
                     WHEN 'kalshi_weather' THEN '2026-05-16T19:18:00+00:00'
                     WHEN 'kalshi_crypto'  THEN '2026-05-16T19:37:00+00:00'
                   END
 GROUP BY division;
SQL

echo
echo "=== Q3b: post-cutoff RT count breakdown by resolved/unresolved ==="
sqlite3 -header -column "$DB" <<'SQL'
SELECT division,
       SUM(CASE WHEN won IS NOT NULL THEN 1 ELSE 0 END) AS resolved,
       SUM(CASE WHEN won IS     NULL THEN 1 ELSE 0 END) AS pending
  FROM kalshi_round_trips
 WHERE division IN ('kalshi_weather','kalshi_crypto')
   AND entry_ts >= CASE division
                     WHEN 'kalshi_weather' THEN '2026-05-16T19:18:00+00:00'
                     WHEN 'kalshi_crypto'  THEN '2026-05-16T19:37:00+00:00'
                   END
 GROUP BY division;
SQL

echo
echo "=== BONUS: BitUnix trade_plan_decision audit rows (Phase 1E watch) ==="
sqlite3 -header -column "$DB" <<'SQL'
SELECT COUNT(*) AS n_trade_plan_decision_rows
  FROM audit_event WHERE kind='trade_plan_decision';
SQL
sqlite3 -header -column "$DB" <<'SQL'
SELECT ts,
       json_extract(payload_json,'$.trigger_signal') AS trigger,
       json_extract(payload_json,'$.should_trade')   AS should_trade,
       json_extract(payload_json,'$.skip_reason')    AS skip_reason
  FROM audit_event
 WHERE kind='trade_plan_decision'
 ORDER BY id DESC LIMIT 5;
SQL

echo
echo "=== BONUS: pa_validation_redeem + pa_validation_expired counts (deferred-fire watch) ==="
sqlite3 -header -column "$DB" <<'SQL'
SELECT kind, COUNT(*) AS n
  FROM audit_event
 WHERE kind IN ('pa_validation_redeem','pa_validation_expired')
 GROUP BY kind;
SQL

echo
echo "=== Service health ==="
systemctl is-active trading-corp
ps -ef | grep -E 'python.*trading_corp' | grep -v grep | head -3

echo
echo "=== DONE ==="
