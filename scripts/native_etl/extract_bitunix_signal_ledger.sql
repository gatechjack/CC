-- READ-ONLY prod extract: native BitUnix signal ledger -> CSV for export_bitunix_alerts.py
-- Run against the prod DB opened mode=ro (CLAUDE.md read-only-SSH policy, 82fda13):
--   Get-Content extract_bitunix_signal_ledger.sql -Raw | ssh azureuser@trading.jacksumner.com \
--     "tr -d '\r' | sqlite3 -csv -header 'file:/home/azureuser/trading_corp/data/trading_corp.db?mode=ro'" \
--     | Set-Content data/native_extracts/bitunix_signal_ledger.csv -Encoding ascii
-- ts is ISO-8601 TEXT (UTC, 'Z'-suffixed); signal uses the bitunix_futures factor vocab;
-- source = 'lord_otter' | 'market_cypher'; tf in {3m,5m,15m,30m}. No symbol/price/side columns.
SELECT ts, signal, source, tf
FROM bitunix_signal_ledger
ORDER BY ts;
