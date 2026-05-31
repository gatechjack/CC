.mode tabs
.headers on
SELECT
  id,
  ts,
  json_extract(payload_json, '$.hard_zero_reason')           AS hard_zero_reason,
  json_extract(payload_json, '$.regime')                     AS regime,
  json_extract(payload_json, '$.score_side')                 AS side,
  json_extract(payload_json, '$.score_tier')                 AS tier,
  json_extract(payload_json, '$.distance_to_support_pct')    AS dist_support_pct,
  json_extract(payload_json, '$.distance_to_resistance_pct') AS dist_resist_pct,
  json_extract(payload_json, '$.composite_score')            AS composite_score,
  json_extract(payload_json, '$.volatility_tier')            AS vol_tier,
  json_extract(payload_json, '$.atr_pct_d1')                 AS atr_pct_d1,
  json_extract(payload_json, '$.h1.regime')                  AS h1_regime,
  json_extract(payload_json, '$.h4.regime')                  AS h4_regime,
  json_extract(payload_json, '$.d1.regime')                  AS d1_regime,
  json_extract(payload_json, '$.trigger_signal')             AS trigger_signal,
  json_extract(payload_json, '$.session')                    AS session,
  json_extract(payload_json, '$.mode')                       AS mode,
  json_extract(payload_json, '$.bar_h1_last_close_ms')       AS bar_h1_ms,
  json_extract(payload_json, '$.bar_h4_last_close_ms')       AS bar_h4_ms,
  json_extract(payload_json, '$.bar_d1_last_close_ms')       AS bar_d1_ms,
  json_extract(payload_json, '$.permission_reason')          AS permission_reason
FROM audit_event
WHERE kind = 'htf_gate_decision'
  AND json_extract(payload_json, '$.hard_zero_reason') IN
      ('proximity_to_support','proximity_to_resistance')
ORDER BY ts;
