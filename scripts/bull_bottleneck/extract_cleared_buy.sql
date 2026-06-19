-- READ-ONLY prod extract: score-CLEARED BUY signals (the population PA validates) -> CSV
--   Get-Content extract_cleared_buy.sql -Raw | ssh azureuser@trading.jacksumner.com \
--     "tr -d '\r'|sqlite3 -csv -header 'file:/home/azureuser/trading_corp/data/trading_corp.db?mode=ro'" \
--     | Set-Content data/bull_bottleneck/cleared_buy.csv -Encoding ascii
-- outcome/note carry the LIVE PA result (skipped_pa_validation + failed validators) for fidelity cross-check.
SELECT ts,
       json_extract(payload_json, '$.tier')             AS tier,
       json_extract(payload_json, '$.net_score')        AS net_score,
       json_extract(payload_json, '$.cooldown_blocked') AS cooldown_blocked,
       json_extract(payload_json, '$.outcome')          AS outcome,
       json_extract(payload_json, '$.note')             AS note
FROM audit_event
WHERE kind = 'bitunix_score_decided'
  AND json_extract(payload_json, '$.side') = 'buy'
  AND json_extract(payload_json, '$.tier') IN ('STANDARD', 'PREMIUM')
ORDER BY ts;
