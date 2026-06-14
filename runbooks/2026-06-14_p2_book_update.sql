-- P2 booking — STEP 2: the booking UPDATE. DRAFT for operator review.
-- *** DO NOT EXECUTE until Step 1 (2026-06-14_p2_confirm_select.sql) returns
--     EXACTLY 1 row. *** This is a PROD DB write — operator-gated, deploy window.
-- Run on a WRITABLE connection (NOT sqlite3 -readonly). Single targeted UPDATE;
-- the `AND result IS NULL` guard makes it idempotent (won't double-book).
--
-- Values are EXCHANGE-AUTHORITATIVE (operator, from BitUnix trade history 2026-06-14):
--   exit fill 63800.1 (BUY-to-close, server-side SL); realized PnL -0.04880000;
--   exit fee 0.00510400; entry SELL 63678.1 / fee 0.00509424 already recorded.
-- Schema-grounded (prod .schema, read this session): result domain
--   'win'|'loss'|'open'|'expired' -> a stop-loss close = 'loss' (no 'stopped'/'closed'
--   literal exists; the SL provenance is kept in extra_json.exit_method). Exit price =
--   result_price; PnL = actual_pnl_dollars (GROSS price PnL — fees live in extra_json).
-- Reconciliation note: BitUnix realized PnL -0.0488 = (63678.1-63800.1) * 0.0004
--   (broker-truncated qty); the row's qty 0.000485497 is the full requested size.
--   The authoritative realized dollar PnL (-0.0488) is what we book.
UPDATE paper_trade_record
SET result             = 'loss',
    result_ts          = '2026-06-14T19:12:20+00:00',  -- exchange exit. Operator wrote 15:12:20 = EDT(UTC-4); 19:12:20 UTC corroborated by reconciler bracket 19:11:46-19:12:46. CONFIRM tz.
    result_price       = 63800.1,                      -- BitUnix exit fill (BUY-to-close, SL); ~5pts inside the 63805.34 trigger
    actual_pnl_dollars = -0.04880000,                  -- BitUnix realized PnL (GROSS; computed on broker qty 0.0004)
    actual_r_multiple  = -0.39906,                     -- DERIVED = pnl / max_dollar_risk(0.12228668); vs actual-stop-risk ~ -0.96. Optional/confirm.
    bars_to_resolution = NULL,                         -- server-side broker close (not a bar-walk resolution)
    extra_json = json_set(extra_json,
                   '$.exit_fee_usd',     0.00510400,   -- BitUnix exit fee
                   '$.exit_side',        'buy',         -- BUY-to-close
                   '$.exit_method',      'server_side_sl_B1',
                   '$.result_source',    'operator_manual_booking',
                   '$.net_realized_usd', -0.0590)        -- = pnl - entry_fee(0.00509424) - exit_fee(0.00510400)
WHERE order_id = '6741f62f-d950-4356-8deb-578f603f8db0'
  AND result IS NULL;
-- Expect "1 row(s) modified". Re-run Step 1 to verify: result='loss',
-- result_price=63800.1, exit_fee 0.005104.
