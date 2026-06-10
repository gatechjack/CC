#!/usr/bin/env bash
# Q3 data pull — 3m bars + 24 HTF-rejects (score+htf) + 20 trade_plan_decision (validation). READ-ONLY.
DB=/home/azureuser/trading_corp/data/trading_corp.db
W='2026-06-09T03:49:41+00:00'
B0="CAST(strftime('%s','2026-06-08 22:00:00') AS INTEGER)*1000"
echo "===BARS3M==="
sqlite3 -readonly -csv "$DB" "SELECT ts_ms,open,high,low,close FROM bitunix_bar_history WHERE timeframe='3m' AND ts_ms>=$B0 ORDER BY ts_ms;"
echo "===REJ_SCORE==="
sqlite3 -readonly -csv "$DB" "SELECT ts, json_extract(payload_json,'\$.trigger_signal'), json_extract(payload_json,'\$.trigger_price'), json_extract(payload_json,'\$.side') FROM audit_event WHERE kind='bitunix_score_decided' AND ts>='$W' AND json_extract(payload_json,'\$.outcome')='skipped_htf_gate' ORDER BY ts;"
echo "===REJ_HTF==="
sqlite3 -readonly -csv "$DB" "SELECT ts, json_extract(payload_json,'\$.trigger_signal'), json_extract(payload_json,'\$.hard_zero_reason'), json_extract(payload_json,'\$.atr_pct_d1'), json_extract(payload_json,'\$.distance_to_support_pct'), json_extract(payload_json,'\$.distance_to_resistance_pct') FROM audit_event WHERE kind='htf_gate_decision' AND ts>='$W' AND json_extract(payload_json,'\$.size_multiplier')=0.0 ORDER BY ts;"
echo "===VAL==="
sqlite3 -readonly -csv "$DB" "SELECT ts, json_extract(payload_json,'\$.entry'), json_extract(payload_json,'\$.score_side'), json_extract(payload_json,'\$.inputs.atr_used'), json_extract(payload_json,'\$.inputs.swing_low'), json_extract(payload_json,'\$.inputs.swing_high'), json_extract(payload_json,'\$.inputs.resistance'), json_extract(payload_json,'\$.inputs.support'), json_extract(payload_json,'\$.stop_loss'), json_extract(payload_json,'\$.tp1'), json_extract(payload_json,'\$.tp2'), json_extract(payload_json,'\$.tp3'), json_extract(payload_json,'\$.sl_method'), json_extract(payload_json,'\$.tp2_method'), json_extract(payload_json,'\$.should_trade') FROM audit_event WHERE kind='trade_plan_decision' AND ts>='$W' ORDER BY ts;"
echo "===END==="
