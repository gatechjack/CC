#!/usr/bin/env bash
# TP-recalibration data pull. READ-ONLY (sqlite3 -readonly). Mirrors qdata.sh join structure.
# set(a) post-fix taken trades (plan inputs + recorded outcomes); set(b) silence-window
# vol_tier_extreme suppressed signals; 3m bars 06-01->now spanning both windows.
DB=/home/azureuser/trading_corp/data/trading_corp.db
FIX='2026-06-09T03:49:41+00:00'
SIL_START='2026-06-02T22:00:00+00:00'   # silence began 06-02T22:15 (vol-tier hard-zero)
B0="CAST(strftime('%s','2026-06-01 00:00:00') AS INTEGER)*1000"
echo "===BARS3M==="
sqlite3 -readonly -csv "$DB" "SELECT ts_ms,open,high,low,close FROM bitunix_bar_history WHERE timeframe='3m' AND ts_ms>=$B0 ORDER BY ts_ms;"
echo "===VAL_TAKEN==="
sqlite3 -readonly -csv "$DB" "SELECT ts, json_extract(payload_json,'\$.entry'), json_extract(payload_json,'\$.score_side'), json_extract(payload_json,'\$.inputs.atr_used'), json_extract(payload_json,'\$.inputs.swing_low'), json_extract(payload_json,'\$.inputs.swing_high'), json_extract(payload_json,'\$.inputs.resistance'), json_extract(payload_json,'\$.inputs.support'), json_extract(payload_json,'\$.stop_loss'), json_extract(payload_json,'\$.tp1'), json_extract(payload_json,'\$.tp2'), json_extract(payload_json,'\$.tp3'), json_extract(payload_json,'\$.sl_method'), json_extract(payload_json,'\$.tp2_method'), json_extract(payload_json,'\$.should_trade') FROM audit_event WHERE kind='trade_plan_decision' AND ts>='$FIX' ORDER BY ts;"
echo "===PTR_OUTCOMES==="
sqlite3 -readonly -csv "$DB" "SELECT ts, substr(order_id,1,8), entry_reference_price, stop_price, actual_r_multiple, result, json_extract(extra_json,'\$.filled_legs'), tp_r_multiple FROM paper_trade_record WHERE division='bitunix_futures' AND ts>='$FIX' ORDER BY ts;"
echo "===SIL_SCORE==="
sqlite3 -readonly -csv "$DB" "SELECT ts, json_extract(payload_json,'\$.trigger_signal'), json_extract(payload_json,'\$.trigger_price'), json_extract(payload_json,'\$.side') FROM audit_event WHERE kind='bitunix_score_decided' AND ts>='$SIL_START' AND ts<'$FIX' AND json_extract(payload_json,'\$.outcome')='skipped_htf_gate' ORDER BY ts;"
echo "===SIL_HTF==="
sqlite3 -readonly -csv "$DB" "SELECT ts, json_extract(payload_json,'\$.trigger_signal'), json_extract(payload_json,'\$.hard_zero_reason'), json_extract(payload_json,'\$.atr_pct_d1'), json_extract(payload_json,'\$.distance_to_support_pct'), json_extract(payload_json,'\$.distance_to_resistance_pct') FROM audit_event WHERE kind='htf_gate_decision' AND ts>='$SIL_START' AND ts<'$FIX' AND json_extract(payload_json,'\$.hard_zero_reason')='vol_tier_extreme' ORDER BY ts;"
echo "===END==="
