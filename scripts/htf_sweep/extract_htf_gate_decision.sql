-- READ-ONLY prod extract: live HTF-gate decisions -> CSV, for the harness FIDELITY cross-check
-- (does composite (a)=current reproduce the live regime/permit on matching timestamps?).
--   Get-Content extract_htf_gate_decision.sql -Raw | ssh azureuser@trading.jacksumner.com \
--     "tr -d '\r'|sqlite3 -csv -header 'file:/home/azureuser/trading_corp/data/trading_corp.db?mode=ro'" \
--     | Set-Content data/htf_sweep/htf_gate_decision.csv -Encoding ascii
SELECT ts,
       json_extract(payload_json, '$.score_side')      AS score_side,
       json_extract(payload_json, '$.regime')          AS regime,
       json_extract(payload_json, '$.size_multiplier') AS size_multiplier,
       json_extract(payload_json, '$.hard_zero_reason') AS hard_zero_reason
FROM audit_event
WHERE kind = 'htf_gate_decision'
ORDER BY ts;
