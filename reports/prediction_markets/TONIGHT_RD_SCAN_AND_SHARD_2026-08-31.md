# Tomorrow's report checklist -- R-d's FIRST unattended run + the shard-proceeds answer (2026-08-31 -> AM read)

Tonight R-d's periodic settlement-scan (600s) books terminal-closes UNATTENDED for the first time, on SIX positions
settling in sequence. The Cubs was one hand-inspected case; six booked automatically is NOT the same evidence.
★ THE FIRST REPORT WALKS EACH SETTLEMENT INDIVIDUALLY (R7.g-style), not a total.

## PART A -- WALK EACH OF THE 6 SETTLEMENTS INDIVIDUALLY (per position, not a sum)
The 6 open positions (all wallet SDTrading 0x16bb, YES, 5ct, shard 3), each with its entry fill_price:
  SDCIN-CIN, DETMIN-DET, ATHTEX-ATH, PHIAZ-AZ (moneylines) + SDCIN-total-9, DETMIN-total-9 (totals).
For EACH: (a) the venue settlement record (`/portfolio/settlements`): market_result yes/no, revenue, settled_time;
(b) the R-d booked settlement-close row: is_exit=1, close_source='settlement', fill_count=5, won, realized_pnl,
settled_ts; (c) VERIFY realized_pnl == 5*settled_value - (5*entry_fill_price + entry_fee) -- i.e. booked against
THIS position's OWN entry price (not a shared/wrong price). Compare booked realized vs venue revenue (fee tolerance).

## ★ THE 4 NAMED RISKS TO CHECK (Jack, 2026-08-31) -- the review said these can't happen; tonight is the first real chance to be wrong
1. **Two positions settling in the SAME 600s scan window** -> both booked in one book_settlements call. Check: each
   got its OWN settlement-close row with its OWN realized (no cross-contamination in the GROUP BY per wallet/ticker).
2. **A position settling WHILE Option D also evaluates it** (whale exits near settlement) -> the DOUBLE-CLOSE path.
   Check: exactly ONE terminal-close per (ticker) -- either a whale-exit reduce_only OR a settlement-close, never
   both. The shared net-open guard should have made the second a no-op; verify no ticker has two closes summing
   past its holding, and journal net-open went to 0 exactly once.
3. **A partially-filled or zero-fill order in the set** -> a position whose fill_count < 5 (or a no_fill). Check the
   settlement books the ACTUAL net-open (not a hardcoded 5); a no_fill row (never held) must NOT get a settlement-close.
4. **Realized booked against the WRONG entry price on a 5-contract position** (Cubs was 1ct -> the avg-cost /
   fill_count arithmetic was never exercised at N=5). Check each realized = net_open*settled_value - net_open*avg_cost,
   avg_cost = entry_cost/entered -- recompute by hand for at least the winners.
Also: boot_reconcile still CLEAN after the scans (journal flat on each settled ticker vs the now-flat venue); NO NO-leg.

## PART B -- ★ THE SHARD-PROCEEDS ANSWER (the one operational fact still missing)
Baseline shard-3 = **$495.19** (16:35Z). Each WINNING position credits 5*$1 = **$5.00** (a loser credits $0).
Let W = wins. Read `/portfolio/balance` breakdown AFTER all 6 settle:
- shard-3 delta ~= +5*W  AND shard-0 flat  ->  ★ PROCEEDS RETURN TO SHARD 3 (shard 3 SELF-SUSTAINS).
- shard-3 flat  AND shard-0 up ~5*W        ->  ★ PROCEEDS SWEEP TO SHARD 0 (shard 3 DEPLETES -> needs topping-up /
  a target_balance_allocation; a shard-0 sub would be Karen's silent-death state).
Say PLAINLY which. **If every position loses, say so -- the question stays OPEN, a legitimate answer, not a failure**
(a $0-only night moves nothing, same as the Cubs). Cross-check the summed booked realized vs the balance delta.

## HOW (tooling ready)
Runner `cc\pm_settlement_walk_ro.ps1` (authored, validated, NOT run -- games settle 18:40 ET onward): per-position
settlement record + booked close + arithmetic recompute + shard delta. Run it AM after all 6 settle. Also re-run
`cc\pm_state_report_ro.ps1` for the journal/venue/arm picture.
