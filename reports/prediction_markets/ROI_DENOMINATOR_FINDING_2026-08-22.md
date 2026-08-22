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

## RESOLUTION — RATIFIED + IMPLEMENTED 2026-08-22 (Jack's ruling; §13 dec 11)
1. **DONE:** RANKED `roi = SUM(net_realized) / SUM(cost_basis)`, `avg_bet` cost-based; `cost_basis =
   total_bought * avg_price` **persisted per row** on `pm_closed_position` (not recomputed at rollup).
2. **Entry-cost basis (`tb*avg_price`) APPROVED over `/activity`-gross** — self-contained, deterministic,
   idempotent, no second API call, and avg_price is already scale-in-weighted (why it reconciles to the
   dollar). `/activity`-gross would import the under-captured-redemption defect. (Round-trip control-LOSS
   slices showed cost-vs-buy +29%/-33% noise on the activity side — another reason not to base the metric
   on it.)
3. **`roi_notional = net/total_bought` retained, NOT ranked**, labeled in schema + `report` (so an analyst
   comparing to an old scout number doesn't chase a phantom discrepancy).
4. **Guard shipped:** `cost_basis<=0 -> roi None` (a scoreable row with avg_price<=0/NULL can't div-by-zero
   the denominator); test added. **Ingestion unaffected.** Clip-saturation of the new (larger) cost-ROIs
   was MEASURED (Item 2), not retuned.

## ITEM 2 — _edge_factor clip saturation on the new cost-ROI (MEASUREMENT ONLY, 2026-08-22, read-only)
Runner `cc\pk_clip_saturation_ro.ps1` -> `pm_clip_saturation_probe.py`: cost-ROI per (wallet, category)
via the ACTUAL ingest path, across the seed roster. `_edge_factor = 1.0 + clip(roi, -0.5, +2.0)`.
- **Scored pairs (n>=10): 28. cost_roi distribution: min +0.1% / median +5.9% / max +90.3%.**
- **Pinned at CEILING (cost_roi >= +200%): 0. Pinned at FLOOR (<= -50%): 0. Categories with MULTIPLE ceiling pins: NONE.**
- **VERDICT: NEGLIGIBLE.** The feared longshot-pinning did not occur; the max (+90.3%, SDTrading mlb) is
  55% below the +200% cap, so `_edge_factor` still discriminates across all scored pairs. Do NOT retune the
  clip bounds. If a future roster produces saturation, re-open with this measurement attached.
- **COVERAGE GAP (honest):** 3 wallets hit HTTP 429 rate-limiting (ran right after the pytest + prior probes)
  — **Kickstand7 + pako (both Fed) returned NO data; BetMechanic partial (to offset 5000).** So the 28 scored
  pairs cover 9-10 of 12 whales. The 2 missing whales are FED: from the earlier fed probe their Fed realized
  is ~2-5% of notional (e.g. tb=578,231 / realized +28,529 = 4.9%), so cost-ROI ~= 5-10% (single digits) —
  nowhere near +200%; they will not pin. Data-backed, not assumed. A spaced re-run would fully close coverage.

## OPEN ITEM (logged, NOT chased — per scope discipline) — cost_basis<=0 on scoreable rows
The probe found **57 scoreable rows with cost_basis<=0** (avg_price<=0 or NULL, total_bought>0). The
div-by-zero guard (`SUM(cost_basis)<=0 -> roi None`) is proven + tested, so the denominator never breaks.
BUT such a row contributes its `net_realized` to the numerator while adding 0 to the cost denominator ->
it can UP-bias a category's cost-ROI (a winning zero-cost-basis row inflates ROI). 57 rows across the roster
is small but non-zero and the direction is UP (violates §13 dec 10). Handling (exclude/flag scoreable rows
with cost_basis<=0 from the cost-ROI, or investigate the avg_price=0/NULL source) is DEFERRED to a later
pass — logged here with the count so it is not rediscovered. Logged as P1_PLAN §13A(h).

Reproduce: `cc\pk_total_bought_ro.ps1` -> `pm_total_bought_probe.py`; `cc\pk_clip_saturation_ro.ps1` ->
`pm_clip_saturation_probe.py`. Cross-ref: P1_PLAN §7 + §13A(g)/(h), QUARANTINE_RECONCILE_2026-08-22.md, NET_VERIFY_TARGET.md.
