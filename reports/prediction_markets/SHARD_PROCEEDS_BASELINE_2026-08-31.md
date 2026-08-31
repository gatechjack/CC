# Shard-proceeds baseline -- do settlement PROCEEDS return to shard 3 or sweep to shard 0? (2026-08-31)

The Cubs LOSS could not answer this (a $0 credit moves nothing). Tonight's 4 R8 positions settle; any WIN credits
real money, so the delta finally tells us whether shard 3 is self-sustaining or needs continuous topping-up. This
records the BEFORE; the AFTER is a post-settlement read (next session / a scheduled check).

## BEFORE (captured 2026-08-31 ~14:43Z, read-only)
- **shard-3 = $499.5224** (breakdown, dollars). shard-0 = $0.0081. (kalshi_jack, exchange_index 3 = Tennis&Baseball.)
- 4 OPEN R8 positions, all wallet SDTrading `0x16bb9951`, all YES, all 5 contracts, all shard 3, venue `position_fp=+5`:
  | id | ticker | market | leg | ct | fill$ | cost (fill*5+fee) | WIN credit (5 * $1) |
  |---|---|---|---|---|---|---|---|
  | 2 | KXMLBTOTAL-26AUG311840SDCIN-9 | SD@CIN Over | yes | 5 | 0.55 | ~2.793 | $5.00 |
  | 3 | KXMLBGAME-26AUG312140PHIAZ-AZ | PHI@AZ, Arizona ML | yes | 5 | 0.47 | ~2.394 | $5.00 |
  | 4 | KXMLBGAME-26AUG311840SDCIN-CIN | SD@CIN, Cincinnati ML | yes | 5 | 0.42 | ~2.143 | $5.00 |
  | 5 | KXMLBGAME-26AUG311940DETMIN-DET | DET@MIN, Detroit ML | yes | 5 | 0.46 | ~2.344 | $5.00 |
- Game start times (from the tickers, ET): SDCIN 18:40, DETMIN 19:40, PHIAZ 21:40 -> settle ~3h after each end (late tonight/early tomorrow UTC).

## THE MEASUREMENT
Each WINNING position pays **5 contracts * $1 = $5.00** proceeds (a losing position pays $0). Let `W` = number of
wins. Then:
- **If proceeds RETURN to shard 3 (self-sustaining):** shard-3(after) ~= 499.5224 + 5*W  (minus nothing; the cost
  was already debited at fill). shard-0 stays ~$0.008.
- **If proceeds SWEEP to shard 0 (needs topping-up):** shard-3 stays ~499.5224 (flat, no credit), shard-0 rises by
  ~5*W. -> shard 3 would DEPLETE with every trade and only refill via a manual move / target_balance_allocation.
Note SDCIN appears twice (a total id=2 AND the Cincinnati ML id=4) -- if CIN wins, BOTH could win (Over + CIN win),
so read each ticker's settlement result individually.

## AFTER-CAPTURE METHOD (post-settlement)
1. Read `/portfolio/balance` breakdown -> shard-3, shard-0 (dollars; do NOT /100 the breakdown -- runner bug).
2. Read `/portfolio/settlements` -> per-ticker `market_result` + `revenue` (the authoritative proceeds).
3. Compute: shard-3 delta vs (5 * n_wins); shard-0 delta. Report which shard the credits landed on.
The eventual combined deploy's BOOT settlement-scan (R-d) will also BOOK these as terminal-closes with realized P&L
per position -- cross-check its booked realized vs the balance delta.

## STANDING
NO-leg guard: all 4 are YES. If tonight any Under/away-spread copy fills, STOP + report (first NO position).
