-- READ-ONLY prod extract: native BitUnix 3m bars -> CSV for ingest_bitunix_bars.py
-- Run against the prod DB opened mode=ro (CLAUDE.md read-only-SSH policy, 82fda13):
--   Get-Content extract_bitunix_bars_3m.sql -Raw | ssh azureuser@trading.jacksumner.com \
--     "tr -d '\r' | sqlite3 -csv -header 'file:/home/azureuser/trading_corp/data/trading_corp.db?mode=ro'" \
--     | Set-Content data/native_extracts/bitunix_bars_3m.csv -Encoding ascii
-- ts_ms is epoch milliseconds (ends in 000) -> /1000 yields exact epoch seconds (ingest contract).
SELECT ts_ms/1000 AS ts, open, high, low, close, volume
FROM bitunix_bar_history
WHERE timeframe = '3m'
ORDER BY ts_ms;
