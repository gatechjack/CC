#!/bin/bash
DB=/home/azureuser/trading_corp/data/trading_corp.db

run() {
  echo "--- $1 ---"
  sqlite3 -readonly "$DB" "$2" 2>&1 || true
  echo ""
}

# For trade #1: entered 2026-05-18T16:24:02 @ 76407.4 sell
# SL 76610.9, TP1 76269.87, TP2 76203.90, TP3 75898.64
# Open period: 2026-05-18 16:24 → 2026-05-19 05:44
run "T1: BTC 3m bars during trade #1 open window (5/18 16:24 → 5/19 05:44) — extremes" \
"SELECT
  MIN(low) min_low, MAX(high) max_high,
  COUNT(*) n_bars,
  SUM(CASE WHEN low <= 76269.87 THEN 1 ELSE 0 END) bars_below_tp1,
  SUM(CASE WHEN low <= 76203.90 THEN 1 ELSE 0 END) bars_below_tp2,
  SUM(CASE WHEN low <= 75898.64 THEN 1 ELSE 0 END) bars_below_tp3,
  SUM(CASE WHEN high >= 76610.9 THEN 1 ELSE 0 END) bars_above_sl
 FROM bitunix_bar_history WHERE timeframe='3m'
   AND ts_ms BETWEEN strftime('%s','2026-05-18 16:24') * 1000
                AND strftime('%s','2026-05-19 05:44') * 1000;"

run "T1: timestamp of first bar that violates entry SL (high >= 76610.9)" \
"SELECT datetime(ts_ms/1000,'unixepoch') ts, open, high, low, close
 FROM bitunix_bar_history WHERE timeframe='3m'
   AND ts_ms BETWEEN strftime('%s','2026-05-18 16:24') * 1000
                AND strftime('%s','2026-05-19 05:44') * 1000
   AND high >= 76610.9
 ORDER BY ts_ms ASC LIMIT 3;"

run "T1: timestamp of first bar that touches TP1 (low <= 76269.87)" \
"SELECT datetime(ts_ms/1000,'unixepoch') ts, open, high, low, close
 FROM bitunix_bar_history WHERE timeframe='3m'
   AND ts_ms BETWEEN strftime('%s','2026-05-18 16:24') * 1000
                AND strftime('%s','2026-05-19 05:44') * 1000
   AND low <= 76269.87
 ORDER BY ts_ms ASC LIMIT 3;"

run "T1: entry-bar (5/18 16:24-16:27) high/low" \
"SELECT datetime(ts_ms/1000,'unixepoch') ts, open, high, low, close
 FROM bitunix_bar_history WHERE timeframe='3m'
   AND ts_ms BETWEEN strftime('%s','2026-05-18 16:24') * 1000
                AND strftime('%s','2026-05-18 16:30') * 1000;"

# For trade #2: entered 2026-05-18T18:30:05 @ 76319.1 sell
# SL 76466.1, TP1 76181.73, TP2 76172.09, TP3 75951.58
run "T2: BTC 3m bars during trade #2 open window (5/18 18:30 → 5/19 07:50) — extremes" \
"SELECT
  MIN(low) min_low, MAX(high) max_high,
  COUNT(*) n_bars,
  SUM(CASE WHEN low <= 76181.73 THEN 1 ELSE 0 END) bars_below_tp1,
  SUM(CASE WHEN low <= 76172.09 THEN 1 ELSE 0 END) bars_below_tp2,
  SUM(CASE WHEN low <= 75951.58 THEN 1 ELSE 0 END) bars_below_tp3,
  SUM(CASE WHEN high >= 76466.1 THEN 1 ELSE 0 END) bars_above_sl
 FROM bitunix_bar_history WHERE timeframe='3m'
   AND ts_ms BETWEEN strftime('%s','2026-05-18 18:30') * 1000
                AND strftime('%s','2026-05-19 07:50') * 1000;"

run "T2: first bar to touch TP1 (low <= 76181.73)" \
"SELECT datetime(ts_ms/1000,'unixepoch') ts, open, high, low, close
 FROM bitunix_bar_history WHERE timeframe='3m'
   AND ts_ms BETWEEN strftime('%s','2026-05-18 18:30') * 1000
                AND strftime('%s','2026-05-19 07:50') * 1000
   AND low <= 76181.73
 ORDER BY ts_ms ASC LIMIT 3;"

run "T2: first bar to violate entry SL (high >= 76466.1)" \
"SELECT datetime(ts_ms/1000,'unixepoch') ts, open, high, low, close
 FROM bitunix_bar_history WHERE timeframe='3m'
   AND ts_ms BETWEEN strftime('%s','2026-05-18 18:30') * 1000
                AND strftime('%s','2026-05-19 07:50') * 1000
   AND high >= 76466.1
 ORDER BY ts_ms ASC LIMIT 3;"

run "T2: entry-bar (5/18 18:30-18:33) high/low" \
"SELECT datetime(ts_ms/1000,'unixepoch') ts, open, high, low, close
 FROM bitunix_bar_history WHERE timeframe='3m'
   AND ts_ms BETWEEN strftime('%s','2026-05-18 18:30') * 1000
                AND strftime('%s','2026-05-18 18:36') * 1000;"

echo "=== DONE PART 5 ==="
