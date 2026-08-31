# Option D — whale-exit copy: DESIGN + FORKS (2026-08-31)

Branch `pm-optiond-whale-exit-2026-08-31`. Ruled: /activity SELL = trigger, /positions size-reduction =
confirmation; BOTH must agree in-window or the exit is MISSED (accepted failure direction, bias-down); exits are
exit-exempt on gates 5/6/8/6b but DISARM STILL BLOCKS (off is off). Touches the ORDER PATH -> full adversarial
review before any deploy.

## 1. WHAT ALREADY EXISTS (do not rebuild)

- `execution.detect_exit_signals(activity_sells, position_reductions, *, window_sec)` — the trigger+confirmation
  matcher. Emits an exit CopySignal only where a SELL and a reduction for the SAME (wallet, condition_id,
  outcome_index) agree within window_sec. No single-signal fallback. (execution.py:485)
- `execution.evaluate(...)` exit path — gates 5/6/8 and 6b are entry-only (`if not signal.is_exit:`); an exit goes
  `is_buy=False` -> reduce_only. Gates 1(disarm)/3(match+liquidity)/4(dedup)/7(slippage) still apply.
- `kalshi_live.build_v2_event_order(is_buy=False)` -> `body["reduce_only"]=True`, correct YES-centric side for BOTH
  legs (sell YES -> ask; sell NO -> bid). (kalshi_live.py:143,187)
- `pm_subdivision_order.is_exit` column + the live journal write already stamps `1 if signal.is_exit else 0`.
- `PolymarketDataAPIClient.fetch_activity(wallet)` -> `list[ActivityRow]` with `side` (BUY/SELL), `type` (TRADE),
  `transaction_hash`, `timestamp`, `condition_id`, `outcome_index`, `size`, `slug`, `outcome`. The driver ALREADY
  holds this client (it's the same object as `positions_client`) — no new client needed. (data client :449)

## 2. WHAT OPTION D ADDS (the wiring)

Per cycle, per attached whale, ALONGSIDE the existing entry path:
1. `activity_sells_from_activity(rows, wallet)` — filter /activity to type==TRADE & side==SELL -> the dict shape
   detect_exit_signals wants: {wallet, condition_id, outcome_index, ts, tx_hash}.  [PURE, fork-agnostic]
2. `detect_position_reductions(prior_sizes, book, now_ts)` — diff the current /positions book against a prior
   per-(cid,oidx) size snapshot; emit {wallet, condition_id, outcome_index, ts=now_ts, slug, outcome} where
   cur < prior - eps (INCLUDING a drop to 0). A settlement-vanish won't pair (no SELL activity), so requiring
   BOTH filters redemptions out.  [PURE, fork-agnostic]
3. `detect_exit_signals(...)` (EXISTS) over (1)+(2).
4. HOLDING GUARD (new): keep an exit only if we currently HOLD net-open contracts on the matched (ticker, leg) —
   never fire a reduce_only against a position we do not hold.  [read-only journal helper]
5. Feed exit signals into `run_live_arm_gated_cycle` with the entries (evaluate handles the rest).

Idempotency: an exit's signal_id = stable_signal_id(wallet, cid, oidx, SELL tx_hash) -> per-SELL identity. The
SAME sell never re-places (gate-4 coid dedup); a DIFFERENT sell (another reduction) -> different coid -> a second
exit, capped by reduce_only.

## 3. ★ FORKS — Jack's rulings (I build the fork-agnostic machinery now; wiring waits on these)

### Fork B (PRIMARY) — exit COUNT: full close vs proportional vs flat
The exit's contract count. Today evaluate() in contracts-mode would size an exit at `sub.contracts` (=5), which is
only coincidentally a full close when holding==5 and STRANDS a residual when holding>5 (the exact Cubs failure).
- **(B1) FULL close, count = our journal net-open for (ticker,leg).  ★ RECOMMEND.** The Cubs lesson is "don't get
  stranded riding to 0"; a partial mirror re-introduces stranding; reduce_only makes full close safe (cannot
  oversell). Requires evaluate() to size an exit from journal net-open, not sub.contracts.
- (B2) PROPORTIONAL, count = round(holding * whale_reduction_fraction). Mirrors the whale; more complex; can leave
  a residual that rides to settlement.
- (B3) FLAT sub.contracts (today's default). Wrong for accumulated holdings.
Recommendation: **B1**. It is a money-path behavior change -> Jack's ruling.

### Fork A — reduction detection / prior-size persistence
Where the prior /positions size lives.
- **(A1) IN-MEMORY per-driver-process snapshot, boot-seeded from current /positions.  ★ RECOMMEND.** Missed-exit
  is the accepted failure direction, so a reduction straddling a restart being missed is acceptable; no schema
  change; no per-cycle DB write; keeps the live-lane data basis clean (PM_REQUIREMENTS S2). The diff FUNCTION is
  pure (fork-agnostic); only the snapshot's HOME differs.
- (A2) PERSISTED (new live-lane table/column). Survives restart; more surface; only needed if a restart-straddling
  missed reduction is unacceptable.
Recommendation: **A1**.

### Fork C — Finding 5 (per-entry tx_hash dedup key) scope
Backlog: entries key on `pos:{cid}:{oidx}` (no per-entry identity) so a genuine same-market RE-ENTRY is refused.
Fix keys on an /activity BUY tx_hash. But a /positions holding may aggregate multiple BUYs -> which tx_hash?
- (C1) Source entries from /activity BUYs (each has a tx_hash), correlate with the current holding. Big entry-path
  change.
- (C2) Keep entries /positions-based; derive the key from the MOST-RECENT /activity BUY tx_hash for (cid,oidx).
  Moderate.
- **(C3) DEFER within Option D — ship the EXIT path first (the high-value part), do Finding-5 as a follow-on
  sub-rung once /activity reads are proven live.  ★ RECOMMEND.** The re-entry gap is known + backlogged; folding
  an entry-path rewrite into the exit build widens the adversarial surface unnecessarily.
Recommendation: **C3** (sequence Finding-5 after the exit path lands).

### Fork D (tunable, not a deep ruling) — window_sec
Gap between the SELL ts (actual) and our reduction-detection ts (poll time >= sell). Default **300s** (covers
data-api lag + one 7s poll); configurable. Too tight -> missed confirmations; too loose -> negligible risk (keyed
on exact cid+oidx). I will default 300 and surface it.

## 4. ADVERSARIAL / STANDING-LENS notes (for the review)

- NO-leg inversion (#1): an exit of a NO leg -> sell NO -> side=bid, reduce_only. Only reachable AFTER a NO ENTRY,
  which itself trips the standing NO-leg STOP — so the NO-exit path is downstream of that halt. Tested for
  correctness regardless.
- Safety-check-that-stops-checking (#2): the HOLDING GUARD must FAIL CLOSED — an unknown/zero holding -> NO exit
  (never a blind reduce_only). Disarm still blocks exits.
- Green suite / real path (#3): the exit wiring gets a test with the driver ARMED + a stub place_fn that actually
  runs the exit through placement (not just dry-run), asserting a reduce_only POST body.
- Fixture mirrors real object (#4): activity/position fixtures mirror ActivityRow/PositionRow field names
  (transaction_hash, outcomeIndex-in-extra, size, slug, outcome) — not a faked shape.
- Gate-never-passes -> suspect input (#5): if exits never fire, check the activity/positions ADAPTERS (the input)
  before detect_exit_signals.

## 5. BUILD ORDER

R-D1 (UNBLOCKED, build now): the PURE pieces + tests — `activity_sells_from_activity`,
`detect_position_reductions`, `journal_net_open_contracts` (read-only), and tests incl. a NO-leg exit-through-
evaluate test. No live wiring, no behavior change to the armed path.
R-D2 (needs Fork B): exit sizing in evaluate() = net-open (B1). Build behind the ruling.
R-D3 (needs Fork A + is a DEPLOY): wire exit detection into scheduled_pm_live_loop; armed real-path test.
R-D4 (Fork C / Finding-5): follow-on.
Deploy/restart/live-write all HALT for Jack.
