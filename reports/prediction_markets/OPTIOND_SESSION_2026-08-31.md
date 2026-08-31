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

### Runner unit note (carry into backlog #2 shard-aware balance read)
`/portfolio/balance` `balance_breakdown[].balance` is in FRACTIONAL DOLLARS (`'509.1956'`, `'0.0081'`), NOT cents.
The TOP-LEVEL `balance` field is cents (`bal.balance/100`, kalshi_live.py:278). A shard-aware reader must read the
breakdown WITHOUT the /100 the top-level reader uses. (My first state-report runner mis-divided; corrected here.)

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
