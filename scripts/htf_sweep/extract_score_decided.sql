-- READ-ONLY prod extract: bitunix score-decided events -> CSV for htf_regime_permit_sweep.py
-- The population the HTF gate sees. We filter to score-CLEARED (tier STANDARD/PREMIUM) in the harness.
--   Get-Content extract_score_decided.sql -Raw | ssh azureuser@trading.jacksumner.com \
--     "tr -d '\r'|sqlite3 -csv -header 'file:/home/azureuser/trading_corp/data/trading_corp.db?mode=ro'" \
--     | Set-Content data/htf_sweep/score_decided.csv -Encoding ascii
SELECT ts,
       json_extract(payload_json, '$.side')      AS side,
       json_extract(payload_json, '$.tier')      AS tier,
       json_extract(payload_json, '$.net_score') AS net_score,
       json_extract(payload_json, '$.outcome')   AS outcome
FROM audit_event
WHERE kind = 'bitunix_score_decided'
ORDER BY ts;
