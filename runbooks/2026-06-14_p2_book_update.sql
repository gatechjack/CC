-- P2 booking — STEP 2: the booking UPDATE. DRAFT for operator review.
-- *** DO NOT EXECUTE until Step 1 (2026-06-14_p2_confirm_select.sql) returns
--     EXACTLY 1 row. *** This is a PROD DB write — operator-gated.
-- Run on a WRITABLE connection (NOT sqlite3 -readonly). Single targeted UPDATE;
-- the `AND result IS NULL` guard makes it idempotent (won't double-book).
--
-- Schema-grounded (prod .schema): result domain 'win'|'loss'|'open'|'expired'
-- (stop-out => 'loss'); columns mirror _record_exit_outcome. actual_pnl_dollars
-- is GROSS price PnL (fees live in extra_json). See the plan §3 for the
-- result_price DISCREPANCY (PnL implies exit ~63778.62, below the 63805.34 SL
-- trigger) — CONFIRM the exact exit fill from the BitUnix UI before booking.
UPDATE paper_trade_record
SET result             = 'loss',
    result_ts          = '2026-06-14T19:12:00+00:00',  -- ~stop-out; CONFIRM exact broker fill ts (bracketed 19:11:46-19:12:46)
    result_price       = 63778.62,                     -- DERIVED from PnL; CONFIRM exact exit fill (see discrepancy note)
    actual_pnl_dollars = -0.04880000,                  -- operator-supplied GROSS price PnL
    actual_r_multiple  = -0.39906,                     -- DERIVED = actual_pnl_dollars / max_dollar_risk(0.12228668); CONFIRM
    bars_to_resolution = NULL,                         -- server-side broker close (not a bar-walk resolution)
    extra_json = json_set(extra_json,
                   '$.exit_fee_usd',     0.00510400,   -- operator-supplied
                   '$.result_source',    'operator_manual_booking',
                   '$.exit_method',      'server_side_sl_B1',
                   '$.net_realized_usd', -0.0590)        -- = pnl - entry_fee(0.00509424) - exit_fee(0.00510400)
WHERE order_id = '6741f62f-d950-4356-8deb-578f603f8db0'
  AND result IS NULL;
-- Expect "1 row(s) modified". Re-run Step 1 to verify: result='loss', exit_fee 0.005104.
