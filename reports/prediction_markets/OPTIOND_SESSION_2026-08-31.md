# Option D (whale-exit copy) + session ledger — 2026-08-31

Branch `pm-optiond-whale-exit-2026-08-31` off `fb271ed`. Jack REMOTE; autonomy grant: build/test/box-scratch/
review/commit/push/read-only-box-reads without per-step auth; HALT for any deploy/restart/live-write/arm/disarm/
latch-clear/cap-change/order-path-on-live/prod-live-advance.

## A. STATE SNAPSHOT (observed 2026-08-31T11:24:49Z, read-only via `cc\pm_state_report_ro.ps1`)

- Arm: `effective_armed=True`, global+sub armed by `r8_arm` @ 02:35:39Z, `latched=False`, `auto_trigger=None`.
- Orders since arm: NONE. Journal has 1 row (id=1, pre-R8 Cubs YES fill @ 22:14:33Z). No R8 placement yet.
- Engine: MainPID 107937, NRestarts 0, ActiveEnter 02:10:57Z. NO overnight restart. pm_web 108138 active.
- ★ boot_reconcile: NOT latched — no engine restart since the 02:44:41Z settlement, so the expected R-b
  settlement-drift latch has not fired. It will latch on the next restart (EXPECTED, not a fault). Nothing cleared.
- Schema 14. Config: sizing_mode=contracts, contracts=5, per_order=5.5, daily=60, open=60, orders/day=20,
  slippage=2, liquidity_ratio=0.75, fixed_stake=0.01. Attachments active: SDTrading 0x16bb..8492, xifutloong3
  0x2dc1..b33c.
- Shard-3 balance = $509.1956 (delta 0.0000 vs post-fill; losing YES -> no proceeds). Shard-0 = $0.0081.
- Cadence overnight: poll */30 (last 11:00Z), refresh 05:00->05:23Z, adjudicate 05:40Z, rollup 05:50Z
  {"rolled_pairs":31}. Healthy.
- /live/kalshi_jack/mlb -> 200, still shows Cubs under "Currently held" (journal-derived; journal holds +1). Venue
  FLAT. journal(+1)-vs-venue(flat) divergence present, undetected, pending next-restart latch. NO-leg: none filled.

### Runner unit note + SHIPPED-CODE VERIFICATION (Jack flagged: check the deployed read, not just the runner)
`/portfolio/balance` carries THREE money shapes (live-verified 2026-08-31T12:18Z, raw response):
`balance`=50920 (INTEGER CENTS), `balance_dollars`='509.2037' (fixed-point dollar STRING), and
`balance_breakdown[].balance` = dollar STRINGS (`'509.1956'` shard 3, `'0.0081'` shard 0).
- **My state-report RUNNER had a /100 bug** (divided the breakdown, printing a bogus $5.09). Isolated to the runner.
- **The SHIPPED `shard_balance.py` is CORRECT** -- `_to_dollars_float` treats the breakdown + `balance_dollars` as
  dollars and does NOT divide by 100 (only the integer-cents top-level `balance` is /100, as a fallback). Verified
  live: `parse_balance(raw).shard(3) = 509.1956` (dollars), `shard_sum == total_dollars = 509.2037`,
  `can_fund(3,60)=True` / `can_fund(3,600)=False`. **Gate 6b compares the REAL shard balance -- NO bug, no fix.**
  (This is NOT the R4-caps / R5-seed class after all; shard_balance.py was written with this exact distinction.)
- Backlog #2 (shard-aware read) is already satisfied by `shard_balance.py` RUNG 1; the `bal.balance/100` at
  kalshi_live.py:278 remains the MASKED-TOTAL legacy read, untouched (a display/exposure concern, not gate 6b).
  Runner `cc\pm_shardbal_verify_ro.ps1`.

## B. ITEM 0 — R-d settlement-close path (SCOPING ONLY, no build)

**Authority: Kalshi's own settlement record** (`/portfolio/settlements` + market `status=finalized`/`result`), NOT
gamma. (1) The settlement-close erases the journal-vs-Kalshi divergence; boot_reconcile trusts Kalshi's portfolio,
so the close must be driven by the same authority or they fight. (2) Realized P&L is a Kalshi fact (revenue/value +
the shard credit); gamma gives only which side of a Polymarket condition won. (3) Kalshi `result` + our stored
`outcome_leg` -> win/loss with NO cross-venue mapping; routing through gamma would inherit the doubleheader
ticker-to-condition ambiguity for a money-terminal event. This is PM_REQUIREMENTS R3 restated PER VENUE: resolution
comes from the resolution authority for the venue the position lives on. Gamma is right for PAPER (paper positions
ARE Polymarket conditions); Kalshi is right for LIVE (live positions are Kalshi contracts).

**Before/after Option D: recommend Option D FIRST, with its exit-close designed as the SINGLE terminal-close
primitive R-d will reuse.** Both terminate a position; built separately, two mechanisms race on the same terminal
state (whale-exit-then-settle double-books; settle-then-stale-exit fires a reduce_only at a flat venue -> latch).
Option D's exit is an ORDER that nets via the existing is_exit signed-net accounting (no new terminal concept);
R-d's settlement is a PASSIVE event needing a synthetic contra/`settled` state. Keep "is this position still live?"
in ONE place; R-d becomes "synthesize a settlement contra-entry through that same primitive." R-b latch keeps
settlement drift SAFE + LOUD meanwhile, so R-d is not a safety blocker — only /live honesty. (Alt: build small R-d
first for one terminal path from day one, at the cost of delaying the authorized Option D. Separately, honest /live
alone is a display-only annotation of a held position whose Kalshi market has settled — not R-d.)
