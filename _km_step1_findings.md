# Kalshi copy — MaggieTheEagle mass_disappearance: STEP 1 evidence

Investigation 2026-07-29/30 (read-only). Prod clock at pull: 2026-07-30T01:57:14Z. PID 429030.

## Anomaly characterization (audit_event, actor=kalshi_copy_trader)
- **46** `kalshi_copy_feed_anomaly` events, **first `2026-07-29T18:09:53Z`**, last `2026-07-30T01:50:53Z`.
- **100% MaggieTheEagle**, **100% reason=`mass_disappearance`**. Zero non-Maggie anomalies.
- Identical payload every cycle: `n_prev_tracked=3, n_removed=2, n_current=1, pct_removed=66.7`.
- Cadence ~10 min = one per scan cycle (matches `last_poll_ts`).
- `n_current=1` => feed is NOT empty; it returns 1 of Maggie's 3 tracked tickers each cycle.

## Maggie's retained (frozen) snapshot — updated_ts `2026-07-29T17:59:48Z` (FROZEN)
All three are Fed-decision markets, none copied by us (our_side="", copy_size_usd=0):
- `KXFEDDECISION-26JUL-H0`   contracts=73908  pnl=430.68  first_seen 2026-06-16
- `KXFEDDECISION-26JUL-H25`  contracts=24530  pnl=379.82  first_seen 2026-06-25
- `KXFEDDECISION-26SEP-H0`   contracts=710    pnl=-106.26 first_seen 2026-06-17

## Roster / feed-wide health
- selected_whales = ["MaggieTheEagle","AI.EDGE"] (only 2 active; other positions:* keys are stale ex-whales).
- **AI.EDGE snapshot FRESH** updated_ts `2026-07-30T01:50:53Z`, 16 positions => batched Apify actor call is
  SUCCEEDING and returning data; feed is not down feed-wide. Only 2 of Maggie's tickers are missing.
- last_poll_ts `2026-07-30T01:50:53Z` => scan loop healthy, polling ~10 min.

## Leading hypothesis (to verify in STEP 2/3)
July FOMC decision announced ~2:00 PM ET = 18:00 UTC on 2026-07-29. The two `KXFEDDECISION-26JUL-*`
markets settled at the announcement and left Maggie's OPEN-positions feed; the surviving `n_current=1`
is expected to be the still-open `-26SEP-H0`. 2/3 removed in one cycle = 66.7% >= 60% mass_exit_threshold
=> breaker trips. Because the breaker RETAINS the stale 3-position snapshot (continue before save), every
subsequent cycle re-compares frozen-3 vs feed-1 and re-fires indefinitely. Not a feed bug per se — a real
settlement event tripping a conservative heuristic with no settlement-awareness / no advance path.
