.mode tabs
.headers on
SELECT 'A_total_proximity' AS probe,
       COUNT(*) AS n,
       MIN(ts) AS first_ts,
       MAX(ts) AS last_ts
FROM audit_event
WHERE kind = 'htf_gate_decision'
  AND json_extract(payload_json, '$.hard_zero_reason') IN
      ('proximity_to_support','proximity_to_resistance');

SELECT 'B_breakdown' AS probe,
       json_extract(payload_json, '$.hard_zero_reason') AS reason,
       json_extract(payload_json, '$.regime') AS regime,
       json_extract(payload_json, '$.score_side') AS side,
       json_extract(payload_json, '$.score_tier') AS tier,
       COUNT(*) AS n,
       MIN(ts) AS first_ts,
       MAX(ts) AS last_ts
FROM audit_event
WHERE kind = 'htf_gate_decision'
  AND json_extract(payload_json, '$.hard_zero_reason') IN
      ('proximity_to_support','proximity_to_resistance')
GROUP BY reason, regime, side, tier
ORDER BY n DESC;

SELECT 'C_bar_pointer_coverage' AS probe,
       CASE WHEN json_extract(payload_json, '$.bar_h1_last_close_ms') IS NULL
            THEN 'missing' ELSE 'present' END AS state,
       COUNT(*) AS n,
       MIN(ts) AS first_ts,
       MAX(ts) AS last_ts
FROM audit_event
WHERE kind = 'htf_gate_decision'
  AND json_extract(payload_json, '$.hard_zero_reason') IN
      ('proximity_to_support','proximity_to_resistance')
GROUP BY state;

SELECT 'D_bar_history' AS probe,
       timeframe,
       COUNT(*) AS n,
       MIN(ts_ms) AS first_ts_ms,
       MAX(ts_ms) AS last_ts_ms,
       datetime(MIN(ts_ms)/1000, 'unixepoch') AS first_iso,
       datetime(MAX(ts_ms)/1000, 'unixepoch') AS last_iso
FROM bitunix_bar_history
GROUP BY timeframe
ORDER BY timeframe;
