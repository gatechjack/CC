# PM "UNBOOKED" INVESTIGATION — 2026-09-14 (READ-ONLY)

Investigating the UI "unbooked" counts (12 kalshi_jack/mlb, 2 ufc, 2 nfl). Read-only; build/change/deploy
nothing. Engine PID 370246; 30 sub-divisions armed and trading. STOP command (do NOT run unless a confirmed
wrong-side/wrong-fighter fill):
`PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global`

## 0. Git-truth note (verified myself)
Brief says prod-live is git truth at `5e03b7a2`. Actual `origin/prod-live` tip fetched = **`8f35f254`**.
`5e03b7a2` (Prospects-display, 2026-09-13) is an **ancestor**; two commits landed on top after the brief was
written — `fcbcd4a7` + `8f35f254`, both *legacy-PM removal* ("remove legacy PM loop wiring from main.py + disarm
poly_kalshi"). `git diff 5e03b7a2..8f35f254 --name-only` = only `config/strategies.yaml` + `trading_corp/main.py`.
**No prediction_markets / live_view / live_driver / execution / matcher file differs between the two shas** — the
PM platform code I read is byte-identical to the brief's stated truth. Investigation worktree =
`pm-unbooked-investigation-2026-09-14` @ `8f35f254`. (Box sha to be confirmed via the read-only runner; expected
`8f35f254` or `5e03b7a2` — immaterial to the PM code.)

═══════════════════════════════════════════════════════════════════════════════════════════════
## 1. THE HEADLINE — what "unbooked" actually counts (this is the finding the brief anticipated)
═══════════════════════════════════════════════════════════════════════════════════════════════

**"Unbooked" is NOT "a whale bet we did not copy." It is the opposite: a position we DID copy AND DID close,
whose terminal close carries no settlement-booked realized P&L.** It is a P&L / close-accounting count, not a
skipped-signal count.

Source of the number on `/live` (per (account, category) tile): `subdivision.py::subdivision_pnl_all`
(subdivision.py:592-617), surfaced via `live_view.py:1239` (`p = pnl_all.get(key)`) → `:1261`
(`"unbooked": int(p.get("unbooked_closes", 0))`) → templates `pm_live_list.html:45` (`{{ r.unbooked }} unbooked`)
and `:140` (`{{ r.unbooked }} closes unbooked (no P&L computable)`). The defining SQL (subdivision.py:603-611):

```sql
SUM(CASE WHEN realized_pnl IS NULL THEN 1 ELSE 0 END) AS unbooked_closes
FROM pm_subdivision_order
WHERE dry_run = 0 AND is_exit = 1 AND outcome_status = 'filled'
GROUP BY account_id, category
```

So an "unbooked" item is a **terminal close** (`is_exit=1`), that **filled** (`outcome_status='filled'`), real
money (`dry_run=0`), whose **`realized_pnl IS NULL`**. The docstring is explicit: "closes with realized_pnl NULL
(opposed/exit without a booked P&L) — counted separately so the gap is VISIBLE, never folded into realized or the
W-L."

### 1a. Why realized_pnl is NULL on a close — the mechanism (from code, decisive)
`realized_pnl` is written to a non-NULL value by **exactly one path**: `settlement.book_settlements`
(settlement.py:182-187), which books `realized_pnl = net_open*settled_value - cost_basis_open` for a market that
**resolved on Kalshi** (`close_source='settlement'`, win→1.0/loss→0.0) or `close_source='settlement_void'`
(refund → realized_pnl = 0, still NOT NULL).

The live order-write path **never** touches `realized_pnl`: `grep realized_pnl trading_corp/prediction_markets/
live_driver.py` → **zero matches**. Neither the pre-POST INSERT (live_driver.py:711-725) nor `_finalize_order`
(live_driver.py:734-739) sets it. Therefore every close that is NOT a settlement leaves `realized_pnl` NULL.

Two non-settlement close types exist (`CopySignal.close_source`, execution.py:102-104):
- **whale-exit close** — `close_source = NULL`. The whale exited their Polymarket position, so Option-D fires a
  `reduce_only` SELL on our held leg (execution.py:584-602). It fills at the bid — real cash moves — but the
  platform books no `realized_pnl` for it.
- **opposed close** — `close_source = 'opposed'`. Two copied whales hold opposite sides of the same market, so the
  guard flattens the whole market per-wallet (`detect_opposing_closes`, execution.py:957-1008). "guard-terminated,
  not settled" (subdivision.py:342); never folded into realized or W/L.

**Net: `unbooked_closes` = (whale-exit closes) + (opposed closes) on real filled positions. A settlement close can
never be unbooked (it always writes a float).** This is a deliberate, documented accounting convention:
`realized_pnl` means *settlement* P&L only; a position closed early (by the whale exiting, or by an opposed
flatten) is counted as an unbooked close and excluded from realized/W-L rather than assigned a sale-based P&L.

### 1b. What this reframes
The brief's second hypothesis is correct: this is unbooked-P&L, not skipped bets — so the original "12 uncopied
whale bets" framing does not apply. **But** the code's own design treats these as an EXPECTED, visible category
(early exits + opposed flattens legitimately have no *settlement* outcome), not as a pending reconciliation queue.
The open question that remains — and that only the box journal can settle — is whether all 16 are legitimate
whale-exit/opposed closes (by design), or whether any is a genuine defect (an oversell, a double-close, or a
market that actually settled but got booked as a manual close). That is what the read-only runner determines
(§3, pending authorization). A secondary, real design question also falls out: **should an early whale-exit book
its sale-based realized P&L** (fill_price − avg cost) rather than being left "unbooked (no P&L computable)"? The
cash is real and the figure IS computable for a whale-exit; "not computable" is precise only for opposed flattens.

═══════════════════════════════════════════════════════════════════════════════════════════════
## 2. MATCHER SCOPE (from code) — for the coverage statement, not the unbooked count
═══════════════════════════════════════════════════════════════════════════════════════════════

The two-layer scope gate (both matchers): (1) matcher-level `COPYABLE_MARKET_TYPES`; (2) the sub-division's
configured `market_types` (`skip_market_type_excluded`).

- **MLB** (`trading_corp/data/mlb_poly_kalshi_match.py`): `COPYABLE_MARKET_TYPES = ("moneyline","total","spread")`
  (line 425). Confirmed — the brief's understanding is right. Everything else is a labeled scope skip:
  - `-total-{W}pt{F}` → KXMLBTOTAL (EXACT strike only), `-spread-{home|away}-{W}pt{F}` → KXMLBSPREAD (exact).
  - any OTHER suffix (e.g. `-nrfi`, F5/first-five, team-total) → `prop` → `skip_non_ml` (line 192-194, 550).
  - `mlb-` prefix but not a single-game slug (World Series / division / MVP / season wins) → `mlb_non_game` →
    `skip_non_game` (line 147-149). Non-`mlb-` slug → `non_mlb` → `skip_non_game`.
  - **So F5, team totals, player props, futures, extra innings, first-inning run are all correct scope skips.**
- **NFL / structural** (`sports_structural_match.py`, shared by nfl/nba/nhl/wnba/cfb): same
  `COPYABLE_MARKET_TYPES = (moneyline, total, spread)` (line 48). EVERY non-total/spread suffix — incl. `-1h-*`,
  quarter, `-team-total-*`, `-corners-*` — → `non_moneyline` → `skip_non_moneyline` (line 137-140, 433-435).
- **UFC** (`ufc_poly_kalshi_match.py`): moneyline(KXUFCFIGHT) / go_the_distance(KXUFCDISTANCE) /
  method_finish(KXUFCMOF) / method_victory(KXUFCMOV). Rounds O/U + RoV + decision are ruled-out scope skips.

### 2a. F5 (baseball) vs the half-game class — same shape, DIFFERENT code paths (brief's question)
The half-game/quarter class in the STRUCTURAL matcher falls to `skip_non_moneyline`. MLB's F5 falls to the `prop`
branch → `skip_non_ml`. **Same fundamental mechanism** (the parser only recognizes `-total`/`-spread` suffixes;
all else is a labeled non-copyable skip BEFORE the config `market_types` gate) — but they live in **two separate
matcher modules with two different labels**. One fix would NOT cover both: adding F5 requires an MLB-matcher +
KXMLBF5* series build; adding half-game requires a structural-matcher + KX*1H/1Q series build. (Verify F5's exact
Poly slug shape against the whale's real bets — §4 — since if an F5 slug fails `_POLY_SLUG_RE` entirely it lands
in `skip_non_game`, not `prop`.) Ticket already filed for the half-game class; F5 is the parallel baseball case.

═══════════════════════════════════════════════════════════════════════════════════════════════
## 3. THE 16 ITEMS — pending the read-only box pull (runner: pm_unbooked_ro.ps1)
═══════════════════════════════════════════════════════════════════════════════════════════════

Since "unbooked" = closes-without-settlement-P&L, the chain-walk for each of the 16 is: whale position → poller →
signal → matcher matched → gates passed → order placed → **fill recorded (close FILLED)** → STOPPED at "P&L
booked". The classification per item becomes:
- **BY DESIGN — whale-exit** (`close_source=NULL`): we sold reduce_only when the whale exited; no settlement P&L.
- **BY DESIGN — opposed** (`close_source='opposed'`): guard flatten on two-whale disagreement; no settlement P&L.
- **GENUINE DEFECT**: oversell (net-open < 0), double-close (a settlement AND a manual close on the same
  wallet/ticker/leg), or a phantom close (no held entry). Leads the report if found.

Runner `pm_unbooked_ro.ps1` (read-only, mode=ro) pulls: the count reproduction (confirm 12/2/2), the
close_source breakdown, the 16 rows in full, a per-(wallet,ticker,leg) oversell/double-close integrity check,
attachments+attribution, and per-category coverage classified by the real matchers. Awaiting authorization.

*(§4 coverage table + §3 item table to be filled from the runner output.)*
