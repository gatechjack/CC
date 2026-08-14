-- =====================================================================
-- Stage-2 DATA FIX (PREPARED — do NOT run outside operator-authorized Stage 2)
-- Correct the 2 sign-flipped LIVE combo pairs' per-leg fills to the
-- BROKER-AUTHORITATIVE fills (2026-07-24 first real atomic fills).
--
-- WHY: brokers/robinhood.py paired legs<->fills by index; Robinhood returned
-- OPEN/RKLB legs reversed, so proposed_order.fill_price was swapped per leg.
-- That column feeds _query_prior_rolls -> the approval-card "prior netted" line
-- AND the LLM ROLL HISTORY prompt block; left as-is it shows a falsely-NEGATIVE
-- prior credit on the next OPEN/RKLB roll (reproduces the phantom-debit alarm).
--
-- SCOPE: ONLY proposed_order.fill_price (card + LLM feed). Does NOT touch the
-- position book (PMCC re-derives positions live) or the immutable audit_event
-- log. Blast radius confirmed = exactly these 4 rows (2 pairs); all other filled
-- combo legs are execution_mode='paper' (never swapped).
--
-- IDEMPOTENT: absolute sets guarded by id + symbol + side + status.
-- RUN:  sqlite3 /home/azureuser/trading_corp/data/trading_corp.db < 01_fix_signflip_fills.sql
-- (engine reads these fresh each scan; no restart needed. WAL: brief write lock.)
-- =====================================================================
.mode column
.headers on

.print === BEFORE (expect the SWAPPED values) ===
SELECT id, symbol, side, fill_price FROM proposed_order
WHERE id IN ('084664a8-8353-4073-8294-d56b80bc0fee',
             'cd4b8be3-1390-4ed5-8ca2-cc2bd2eb6ba4',
             'fef29c33-d521-4018-aadb-e8db7f1150eb',
             '4eac1925-4272-469a-8204-48cc1f9a58fc')
ORDER BY ts;

BEGIN;
-- OPEN pair 5c9e347f : buy-to-close 5C = 0.03, sell-to-open 4C = 0.29
UPDATE proposed_order SET fill_price = 0.03
 WHERE id='084664a8-8353-4073-8294-d56b80bc0fee' AND symbol='OPEN' AND side='buy'  AND status='filled';
UPDATE proposed_order SET fill_price = 0.29
 WHERE id='cd4b8be3-1390-4ed5-8ca2-cc2bd2eb6ba4' AND symbol='OPEN' AND side='sell' AND status='filled';
-- RKLB pair 360f4b92 : buy-to-close 74C = 0.03, sell-to-open 75C = 1.20
UPDATE proposed_order SET fill_price = 0.03
 WHERE id='fef29c33-d521-4018-aadb-e8db7f1150eb' AND symbol='RKLB' AND side='buy'  AND status='filled';
UPDATE proposed_order SET fill_price = 1.20
 WHERE id='4eac1925-4272-469a-8204-48cc1f9a58fc' AND symbol='RKLB' AND side='sell' AND status='filled';
COMMIT;

.print === AFTER (buy legs 0.03; OPEN sell 0.29; RKLB sell 1.20) ===
SELECT id, symbol, side, fill_price FROM proposed_order
WHERE id IN ('084664a8-8353-4073-8294-d56b80bc0fee',
             'cd4b8be3-1390-4ed5-8ca2-cc2bd2eb6ba4',
             'fef29c33-d521-4018-aadb-e8db7f1150eb',
             '4eac1925-4272-469a-8204-48cc1f9a58fc')
ORDER BY ts;

.print === NET VERIFY (sell - buy; expect OPEN +0.26, RKLB +1.17) ===
SELECT 'OPEN' AS pair,
       ROUND(SUM(CASE WHEN side='sell' THEN fill_price ELSE -fill_price END), 2) AS net_credit
FROM proposed_order
WHERE id IN ('084664a8-8353-4073-8294-d56b80bc0fee','cd4b8be3-1390-4ed5-8ca2-cc2bd2eb6ba4');
SELECT 'RKLB' AS pair,
       ROUND(SUM(CASE WHEN side='sell' THEN fill_price ELSE -fill_price END), 2) AS net_credit
FROM proposed_order
WHERE id IN ('fef29c33-d521-4018-aadb-e8db7f1150eb','4eac1925-4272-469a-8204-48cc1f9a58fc');
