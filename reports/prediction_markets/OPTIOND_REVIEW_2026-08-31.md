# Option D — adversarial review findings + disposition (2026-08-31)

Three independent read-only reviewers (phantom-exit/K1, disarm+fail-open, NO-leg-pricing+test-quality) against the
Option D exit path (`execution.py` evaluate exit branch + `_leg_exit_bid` + `journal_net_open_contracts`;
`live_driver.py` exit adapters + wiring). Full PM suite GREEN (0 fail, 1 skip). Nothing deployed.

## CONFIRMED SAFE (traced, not just asserted)
- **Exit PRICING is correct + marketable for BOTH legs, no inversion, no accidental open.** YES exit -> side=ask at
  yes_bid - slip (crosses the bid); NO exit -> side=bid at yes_ask + slip (crosses the ask). `is_buy=(not is_exit)`
  threads identically into the pre-check `v2_side_and_price` AND `build_v2_event_order` (same base_price, no drift);
  `reduce_only=True` set iff `is_buy=False`. Edge prices (0 / None / >=1) fail closed -> skip:no_quote.
- **DISARM blocks exits on EVERY path** (airtight): exit signals flow into the SAME `run_live_arm_gated_cycle` as
  entries and hit the identical per-order `read_arm_verdict().armed` re-read before `place_fn`; global/sub/latched
  all block. Proven by armed+disarmed loop tests (not dry-run).
- **`is_exit` is UN-SPOOFABLE**: only two construction sites (detect_exit_signals hard-codes True,
  positions_to_entry_signals hard-codes False); it is code-path-derived, never a whale-controllable field. An entry
  can never bypass the caps by masquerading as an exit; the holding guard + reduce_only are a second line anyway.
- **Holding guard fails CLOSED** (`skip:not_held` when net-open <= 0; excludes dry_run/non-filled rows; UPPER
  ticker match). Gate exemptions (2a/2b/3/5/6/8/6b) are exit-only and compensated (bid precondition + holding guard
  + reduce_only + IOC/slippage clamp). Partial book -> skip + no snapshot touch. Idempotency: exit coid keyed on the
  SELL tx_hash -> same sell never double-places (gate-4 dedup); entry vs exit coids distinct.

## FIXED THIS PASS (committed 92e6d8d)
- **Snapshot-retry on /activity failure** (was: a failed exit-confirm advanced the snapshot -> the reduction was a
  PERMANENT miss despite a comment claiming "re-checked next cycle"). Now the snapshot advances ONLY after a
  completed diff + successful confirm; a transient fetch blip retries next cycle. (LOW, correctness+honesty.)
- **Test-coverage gaps** (regression-proofing): R-D3 now asserts the exit PRICE (0.5100); a NO-leg exit INTEGRATION
  test runs the full loop (side=bid, 0.5400, count=4, + a negative position_fp boot-reconcile); `_market_quote_dict`
  asserts `yes_bid_dollars` (the exit's price source).

## ★ FLAGGED — carry to Jack (NOT fixed; each is bounded or a ruling)

### F1 (reviewer-HIGH, my read: bounded) — settled-position / R-d interaction
Because there is NO settlement-close path (R-d deferred), the journal reads `held>0` for a SETTLED position, and a
whale's SETTLING Poly leg looks like a /positions reduction. **Bounded further than the reviewer credited:** a
SETTLED Kalshi market has no bid -> `_leg_exit_bid` returns None -> `skip:no_quote`, so we CANNOT sell into a
settled Kalshi book; and if the whale did a real discretionary SELL, following them OUT is the intended behavior. So
the money-risk case (our Kalshi leg still OPEN while the whale's Poly leg settled + a real sell) is a CORRECT
follow, not a phantom. Residual bad case = a coincidental STALE sell (=F3) pairing a settlement-driven vanish;
bounded by reduce_only + the R-b boot-reconcile latch. **This is the item-0 interaction: it is the strongest
argument to build R-d (settlement-close) as the next rung, reusing Option D's terminal accounting.** Recommend:
build R-d before Option D runs long unattended; safe to arm Option D short-term given the bounds above -- Jack's call.

### F2 (MEDIUM) — multi-whale full-close scope: a RULING
`journal_net_open_contracts` is per-ACCOUNT (no wallet predicate), so whale A's exit closes A's AND B's copies on
the same (ticker, leg). Over-close is risk-REDUCING (bias-to-flat, safe) but strands B's edge. With two active
attachments (SDTrading + xifutloong3) this is live-relevant TODAY. **Ruling needed:** (a) per-WALLET full-close
(matches "we exit when THE WHALE exits"; a one-line WHERE `AND wallet=?` + count from that whale's net-open) --
RECOMMEND; or (b) account-level full-close (more conservative "get fully out" when any tracked whale exits). Not a
safety blocker (either way we never oversell -- reduce_only). Small fix once ruled.

### F3 (MEDIUM) — stale-sell pairing window
A SELL in the top-100 /activity within +/-300s can pair with a NEW/unrelated reduction on the same (cid, oidx).
Bounded by the holding guard + reduce_only + coid dedup (a sell that already produced an exit -> skip:duplicate);
the residual is a sell that was a MISSED exit (never journaled) re-pairing -> a premature (not phantom) exit of a
REAL position. Recommend tightening: require the SELL ts to fall in the interval that produced THIS reduction (after
the prior snapshot), not merely within +/-300s of detection -- needs per-cycle snapshot timing (deferred; bounded).

### INFO (no change; ruled behavior)
- Entries are processed before exits; an entry storm that trips reject:count_ceiling breaks the cycle before a
  queued exit and then latches (disarm blocks the exit). Consider processing EXITS FIRST (prioritize risk-reduction)
  -- flagged, not changed (count cap is 20/day; unlikely; latch is the ruled stop).
- A disarm-blocked exit is dropped, not replayed on re-arm (human flattens by hand -- the ruled "off is off").

## VERDICT
The exit path is sound in depth (pricing correct, disarm airtight, is_exit un-spoofable, guard fail-closed,
reduce_only venue floor). No CRITICAL/HIGH code defect that opens new risk or fires a naked/oversized short. The two
things to settle before Option D runs UNATTENDED long-term: **F2 (the multi-whale ruling)** and **F1/R-d (the
settlement-close, which the item-0 scoping already recommends building next, reusing this build's terminal
accounting).** Deploy + arm are HALTED for Jack.
