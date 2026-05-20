#!/bin/bash
DB=/home/azureuser/trading_corp/data/trading_corp.db
YAML=/home/azureuser/trading_corp/config/strategies.yaml

V2_FLIP="2026-05-17T05:14:00+00:00"
H2_LIVE="2026-05-16T19:21:00+00:00"
PRE_H2_START="2026-05-10T00:00:00+00:00"

run() {
  echo "--- $1 ---"
  sqlite3 -readonly "$DB" "$2" 2>&1 || true
  echo ""
}

# ──────────────────────────────────────────────────────────────────
# Signal → intrinsic side mapping (from config)
# ──────────────────────────────────────────────────────────────────
echo "=== A: strategies.yaml bitunix_futures.scoring.factors block ==="
# Extract from 'bitunix_futures:' through the next top-level key — sed range.
sed -n '/^bitunix_futures:/,/^[a-zA-Z_]/{
  /^bitunix_futures:/p
  /^  /p
}' "$YAML" | sed -n '/scoring:/,/^  [a-z]/{p}' | head -200
echo ""

# ──────────────────────────────────────────────────────────────────
# Step 1a — raw webhook side mix (using factor mapping)
# ──────────────────────────────────────────────────────────────────
run "Step1a: webhook_received signal counts by strategy + signal since v2 flip" \
"SELECT json_extract(payload_json,'\$.strategy') strategy,
        json_extract(payload_json,'\$.signal') signal,
        COUNT(*) n
 FROM audit_event
 WHERE kind='webhook_received' AND ts>='$V2_FLIP'
   AND json_extract(payload_json,'\$.strategy') IN ('market_cypher','lord_otter')
 GROUP BY strategy, signal
 ORDER BY n DESC;"

run "Step1a-pre: webhook signal counts in PRE-H2 window (5/10 → 5/16 19:21)" \
"SELECT json_extract(payload_json,'\$.strategy') strategy,
        json_extract(payload_json,'\$.signal') signal,
        COUNT(*) n
 FROM audit_event
 WHERE kind='webhook_received'
   AND ts>='$PRE_H2_START' AND ts<'$H2_LIVE'
   AND json_extract(payload_json,'\$.strategy') IN ('market_cypher','lord_otter')
 GROUP BY strategy, signal
 ORDER BY n DESC;"

# ──────────────────────────────────────────────────────────────────
# Step 1c — bitunix_signal_ledger inspect
# ──────────────────────────────────────────────────────────────────
run "Step1c: bitunix_signal_ledger schema" \
"SELECT sql FROM sqlite_master WHERE type='table' AND name='bitunix_signal_ledger';"

run "Step1c: ledger side mix (if 'side' column) since v2 flip" \
"SELECT side, COUNT(*) n FROM bitunix_signal_ledger
 WHERE ingested_ts >= '$V2_FLIP' GROUP BY side;"

run "Step1c-fallback: ledger raw signal_name distribution since v2 flip" \
"SELECT signal_name, COUNT(*) n FROM bitunix_signal_ledger
 WHERE ingested_ts >= '$V2_FLIP' GROUP BY signal_name ORDER BY n DESC LIMIT 30;"

# ──────────────────────────────────────────────────────────────────
# Step 1d — score_decided fresh-only side comparison across windows
# ──────────────────────────────────────────────────────────────────
run "Step1d: score_decided side mix, PRE-H2 window (5/10 → 5/16 19:21), FRESH only" \
"SELECT json_extract(payload_json,'\$.side') side, COUNT(*) n
 FROM audit_event
 WHERE kind='bitunix_score_decided'
   AND ts>='$PRE_H2_START' AND ts<'$H2_LIVE'
   AND json_extract(payload_json,'\$.trigger_source') IN ('market_cypher','lord_otter')
 GROUP BY side ORDER BY n DESC;"

run "Step1d: score_decided side mix, H2-LIVE→V2 window (5/16 19:21 → 5/17 05:14), FRESH only" \
"SELECT json_extract(payload_json,'\$.side') side, COUNT(*) n
 FROM audit_event
 WHERE kind='bitunix_score_decided'
   AND ts>='$H2_LIVE' AND ts<'$V2_FLIP'
   AND json_extract(payload_json,'\$.trigger_source') IN ('market_cypher','lord_otter')
 GROUP BY side ORDER BY n DESC;"

run "Step1d: score_decided side mix, POST-V2 window (5/17 05:14 → now), FRESH only" \
"SELECT json_extract(payload_json,'\$.side') side, COUNT(*) n
 FROM audit_event
 WHERE kind='bitunix_score_decided' AND ts>='$V2_FLIP'
   AND json_extract(payload_json,'\$.trigger_source') IN ('market_cypher','lord_otter')
 GROUP BY side ORDER BY n DESC;"

run "Step1d: PREMIUM tier side mix across windows (all three, fresh only)" \
"SELECT
  CASE WHEN ts<'$H2_LIVE' THEN 'PRE_H2'
       WHEN ts<'$V2_FLIP' THEN 'H2_PRE_V2'
       ELSE 'POST_V2' END AS window,
  json_extract(payload_json,'\$.side') side,
  COUNT(*) n
 FROM audit_event
 WHERE kind='bitunix_score_decided'
   AND ts>='$PRE_H2_START'
   AND json_extract(payload_json,'\$.tier')='PREMIUM'
   AND json_extract(payload_json,'\$.trigger_source') IN ('market_cypher','lord_otter')
 GROUP BY window, side
 ORDER BY window, n DESC;"

# ──────────────────────────────────────────────────────────────────
# Step 1f — BTC trend characterization
# ──────────────────────────────────────────────────────────────────
run "Step1f: BTC 1d bars covering window (5/14 → 5/20)" \
"SELECT datetime(ts_ms/1000,'unixepoch') day,
        open, high, low, close, volume,
        ROUND(close - open, 2) net,
        ROUND((close-open)/open*100, 3) pct_change,
        ROUND((high-low), 2) range,
        ROUND(ABS(close-open)/(high-low+0.001), 3) trendiness
 FROM bitunix_bar_history WHERE timeframe='1d'
   AND ts_ms >= strftime('%s','2026-05-14') * 1000
 ORDER BY ts_ms ASC;"

run "Step1f: BTC 1h endpoint snapshots — open at v2 flip + close right now" \
"SELECT datetime(ts_ms/1000,'unixepoch') ts, open, high, low, close
 FROM bitunix_bar_history WHERE timeframe='1h'
   AND ts_ms BETWEEN strftime('%s','2026-05-17 05:00') * 1000
                 AND strftime('%s','2026-05-17 06:00') * 1000;"

run "Step1f: BTC 1h endpoint — last 3 hours" \
"SELECT datetime(ts_ms/1000,'unixepoch') ts, open, high, low, close
 FROM bitunix_bar_history WHERE timeframe='1h'
 ORDER BY ts_ms DESC LIMIT 3;"

run "Step1f: BTC overall window stats (3m bars 5/17 05:14 → now)" \
"SELECT
  MIN(low) min_low, MAX(high) max_high,
  ROUND(MAX(high) - MIN(low), 2) absolute_range,
  ROUND((MAX(high) - MIN(low)) / MIN(low) * 100, 3) range_pct,
  COUNT(*) n_bars
 FROM bitunix_bar_history WHERE timeframe='3m'
   AND ts_ms >= strftime('%s','2026-05-17 05:14') * 1000;"

# ──────────────────────────────────────────────────────────────────
# Step 2 — full trade_plan_decision detail (all 11)
# ──────────────────────────────────────────────────────────────────
run "Step2: all trade_plan_decision rows since v2 flip — full numeric detail" \
"SELECT ts,
        json_extract(payload_json,'\$.should_trade') should,
        json_extract(payload_json,'\$.skip_reason') skip,
        json_extract(payload_json,'\$.score_tier') tier,
        json_extract(payload_json,'\$.score_side') side,
        json_extract(payload_json,'\$.entry') entry,
        json_extract(payload_json,'\$.stop_loss') sl,
        json_extract(payload_json,'\$.tp1') tp1,
        json_extract(payload_json,'\$.tp2') tp2,
        json_extract(payload_json,'\$.tp3') tp3,
        json_extract(payload_json,'\$.sl_method') sl_m,
        json_extract(payload_json,'\$.risk_per_unit') rpu,
        json_extract(payload_json,'\$.inputs.atr_used') atr,
        json_extract(payload_json,'\$.inputs.swing_low') sw_lo,
        json_extract(payload_json,'\$.inputs.swing_high') sw_hi
 FROM audit_event
 WHERE kind='trade_plan_decision' AND ts >= '$V2_FLIP'
 ORDER BY ts ASC;"

# Also fetch the inferred fee floor — from yaml
run "Step2: bitunix_futures fees block + tp1 floor params from yaml" \
"SELECT 'see-yaml-dump-below';"
echo "=== B: yaml bitunix_futures.fees + bitunix_futures.trade_plan ==="
grep -A 8 "^  fees:" "$YAML" | head -12
echo ""
grep -A 25 "^  trade_plan:" "$YAML" | head -30

echo "=== ANALYSIS COMPLETE ==="
