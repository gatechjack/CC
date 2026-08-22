# ROI DENOMINATOR — `total_bought` is NOTIONAL, not cost (2026-08-22, read-only, PROVEN)

Ordered by Jack (Task 1). Runner `runners/pm_total_bought_probe.py` + `cc\pk_total_bought_ro.ps1`:
reconstruct actual BUY fills from `/activity` per conditionId and compare to `/closed-positions`
`total_bought`, for clause-(a)-FIRING rows vs a NON-FIRING control, on both live MLB whales + evanng UFC.
No DB, no writes.

## HEADLINE VERDICT (proven, not inferred)
**`/closed-positions total_bought` is the NOTIONAL (share count = payout at $1), NOT the USDC cost basis.
The real cost = `total_bought * avg_price`, which equals the `/activity` BUY sum to the dollar.** Therefore
the scoreboard's primary metric `roi = net_realized_pnl / total_bought` is **return-on-NOTIONAL, not
return-on-cost** — it understates true ROI by ~`avg_price` and, because `avg_price` varies per bet/whale
(0.02 to 0.885 observed), it **distorts the ranking** (longshot bettors penalized most). This is the
disease Jack predicted; the MECHANISM is the inverse of my earlier guess (total_bought OVER-states cost
~1/avg_price, it does not understate it).

## The proof (cost = total_bought * avg_price == /activity BUY, to the dollar)
Cleanest signal = control WINS (redemptions captured), individual rows EXACT:
| row | total_bought | avg_price | cost=tb*avg | /activity BUY | c-b% |
|---|---|---|---|---|---|
| mlb-nym-ari | 153,622.47 | 0.540 | 82,925.41 | 82,937.45 | -0.0% |
| mlb-tor-phi | 100,745.08 | 0.506 | 51,017.31 | 51,021.42 | -0.0% |
| mlb-bal-det | 90,000.00 | 0.412 | 37,080.00 | 37,080.00 | +0.0% |
| mlb-cle-col | 75,789.46 | 0.501 | 37,962.94 | 37,963.62 | -0.0% |

And it reconciles realized on a win: `realized = total_bought - cost` -> mlb-nym-ari `153622 - 82937 = +70685`
(row realized = +70685.39). So `total_bought` = payout-at-$1 = shares; cost = shares*avg_price = BUY.

Slice `cost-vs-buy%` (does tb*avg == BUY?): **xifutloong3 control-win -0.3%, SDTrading control-win -4.8%**
(the residual is redemption-undercapture in `/activity`, not a cost error — the per-row wins are exact).

## The control is the point (Task 1c): the gap is UNIVERSAL, not confined to firing rows
`buy-vs-tb%` (BUY vs total_bought): SDTrading ALL **-44.8%**, FIRING -44.2%, control-win -46.1%;
xifutloong3 ALL **-43.7%**, FIRING -44.0%, control-win -42.1%. The ~-45% notional-vs-cost gap is present
on EVERY slice -> **every ROI in the scoreboard is on the wrong denominator**, not just anomalous rows.

## Magnitude (Task 1d)
- SDTrading: SUM total_bought 10.01M vs SUM cost(tb*avg) 5.13M -> true cost is ~51% of the current
  denominator; true ROI ~= current ROI * 1.95.
- xifutloong3: SUM total_bought 5.95M vs SUM cost 3.40M -> true ROI ~= current ROI * 1.75.
- Per-bet distortion is NOT uniform: evanng UFC avg_price ranged 0.02-0.885 -> a longshot bet's ROI is
  understated ~50x relative to a chalk bet. Cross-whale/bet ROI ranking is therefore unreliable as-is.

## Direction of bias
Current ROI is UNDERSTATED (denominator too big) -> the DOWN/conservative direction (consistent with the
"bias down, never up" principle, §13 dec 10). It is not a money-losing up-bias, but it IS rank-distorting
(you would copy a mediocre chalk whale over a strong longshot whale). Must be corrected; not an emergency.

## Clause-(a) firing rows are a SEPARATE, rarer anomaly
Firing rows show `realized_pnl` ~2x more negative than either cost OR the `/activity` loss
(e.g. mlb-nyy-bos: cost 4,356 / activity loss -4,200 / row realized **-9,112.99** ~= -notional). So
`/closed-positions realized_pnl` on these ~1-8% of rows disagrees with `/activity` by ~2x (realized
reported closer to -notional than -cost). Genuinely anomalous -> correctly FLAGGED by the demoted clause (a)
(`pnl_anomaly`), NOT excluded. This is distinct from the systematic denominator issue.

## evanng UFC (Task 1f): ONE root-cause family, link them
`evanng UFC: SUM row_realized = +9,778.97  vs  SUM activity(S+R-B) = +1,141.72` (matches Probe-A exactly).
The activity rebuild under-counts because (i) `/activity` under-captures REDEMPTIONS (control-win
cost-vs-buy -33.9% = redemptions missing) and (ii) the whole reconciliation is confounded by
total_bought=notional. The scout's -13,706.51 (activity method) is unreliable for the same reason. So the
open §13A(a) evanng discrepancy is NOT a separate mystery — it is the same `/closed-positions` vs
`/activity` accounting divergence + notional-total_bought. Treated as one investigation henceforth.

## PROPOSAL (NOT implemented — this is P1's PRIMARY metric; Jack rules, per the load-bearing discipline)
1. **Fix the ROI denominator to cost:** `roi = net_realized_pnl / SUM(total_bought * avg_price)`; likewise
   `avg_bet = SUM(cost)/n`. Store a `cost_basis` (=Σ tb*avg) column for visibility + auditability. One-line
   rollup change; `avg_price` is already on every row.
2. **Cost-definition edge case for Jack to rule:** `tb*avg_price` = entry cost (shares*avg entry price),
   which matches `/activity` BUY on clean buy-and-hold. For round-trip positions (buy/sell/rebuy) gross
   `/activity` BUY can differ (control-LOSS slices showed cost-vs-buy +29%/-33% noise). Recommend
   `tb*avg_price` (entry-cost basis, self-contained in the row, no `/activity` dependency) — but confirm.
3. **Keep return-on-notional as a secondary column** only if there's a use for it; the ranking metric
   should be return-on-cost.
4. Until ruled, the ranking stays GATED (DEPLOY_SEQUENCE.md) — ingestion is unaffected (`total_bought`,
   `avg_price`, `realized_pnl` are all stored faithfully; only the derived `roi`/`avg_bet` are affected).

Reproduce: `cc\pk_total_bought_ro.ps1` -> `pm_total_bought_probe.py` (avg_price/cost columns + slice gaps).
Cross-ref: P1_PLAN §7 + §13A(g), QUARANTINE_RECONCILE_2026-08-22.md, NET_VERIFY_TARGET.md.
