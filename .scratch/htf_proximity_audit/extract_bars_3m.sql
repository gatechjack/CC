.mode tabs
.headers on
SELECT ts_ms, open, high, low, close, volume
FROM bitunix_bar_history
WHERE timeframe = '3m'
ORDER BY ts_ms;
