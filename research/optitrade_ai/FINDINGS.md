# OptiTrade AI -- findings (evidence chain, stated flat)

Independent replication of the "OptiTrade AI" vendor script's ENTRY signals,
transplanted into the honest `optitrade_bt` bracket (SL-first, 4 scaled TP rungs,
gross primary + net06/net04 taker fees). The vendor's own exit/label layer was NOT
reproduced -- it repaints and labels "wins" by close-vs-signal-close, not bracket
simulation (see the vendor-methodology note in `AI_RESULTS.md`). Binance-perp
corpus primary; Bybit-perp as the independent cross-venue arbiter. No verdicts
below -- only the chain of measured results. Full detail in the linked docs.

## The chain

1. **Transplant probe** (`AI_RESULTS.md`, `ai_results.csv`).
   8 signal variants x 3 RR across 12 coin/TF cells, 5 windows. Aggregate: every
   signal family is net06-negative summed across cells; reversal survives fees in
   more cells than continuation. Best-config-per-cell is in-sample-selected across
   24 configs (caveated). ETH 1h Normal/continuation surfaced as a lead.

2. **Validation** (`VALIDATION.md`, `validation_results.csv`).
   Cross-venue Bybit replay + neighborhood (RR2.5, adjacent preset) + shuffled-entry
   permutation on 3 candidate cells. ETH 1h continuation showed the strongest Bybit
   transfer (net06 +43.0, 5/5) and cleared the (then) permutation p=0.035.

3. **Spec-diff vs vendor source** (`SPEC_DIFF.md`, `spec_diff_spacing.py`).
   Vendor `.pine` obtained. Four audited areas match exactly (EMA source `hlc3`;
   Normal `30..120` / VeryHigh `60..240`; `isbull` 3-bar chain; 5-bar freshness;
   MACD 12/26/9). One residual: continuation spacing. Vendor
   `buy = buy2 and ta.barssince(buy2[1])>30` resets the clock on every FRESH event;
   the as-implemented "emission" variant resets on EMISSION -> emission is a strict
   superset (0 only-vendor), ~13-15% more signals. Bracket impact, ETH 1h:
   net06 Binance **+24.1 -> +9.5** (vendor-exact), Bybit **+43.0 -> +27.7 (5/5 -> 4/5)**.
   Scope: all continuation numbers in docs 1-2 are the emission variant (overstated);
   reversal shares the residual class with smaller effect. Corrected rollup pinned to
   the top of `AI_RESULTS.md`.

4. **Drift-control + long/short split** (`ITEM3.md`, `item3_results.csv`).
   ETH 1h, both signal sets x both venues, per-side matched random-direction null
   (200 perms, direction counts preserved). Recomputed drift-controlled p:
   vendor Binance **0.125** (does not clear null; the stale 0.035 was the emission
   set), vendor Bybit **0.035**, emission Binance **0.030**, emission Bybit **0.005**.
   Per side: the LONG side beats random-longs everywhere (pctile >= 0.79, not ~0.50);
   the SHORT side carries more net06 at a higher above-random pctile (0.84-1.00).
   Effect concentrates on the short side and the most-recent window.

5. **Pre-registered cross-coin falsification** (`XCOIN.md`, `xcoin_results.csv`).
   ONE config fixed a priori (emission continuation, Normal, MACD off, SL 2.5*ATR,
   RR 3.5, SL-first, 1h) run unchanged on BTC/SOL/XRP (never touched) + ETH restated,
   both venues. Only ETH (the selection coin) clears the drift-controlled null
   (p 0.035 Binance / 0.005 Bybit). All 6 out-of-coin cells have **p >= 0.26**:
   BTC +2.5 / +6.6 (ns), SOL -17.3 / -13.8, XRP -10.9 / +1.9. The ETH short-side
   dominance does not replicate.

## Numbers pinned (net06, R; 0.06%/side both sides)

| stage | cell | emission | vendor-exact |
|---|---|--:|--:|
| ETH 1h Binance | net06 (p) | +24.1 (0.030) | +9.5 (0.125) |
| ETH 1h Bybit | net06 (p) | +43.0 (0.005) | +27.7 (0.035) |
| cross-coin (emission, 1h) | BTC B/Y | +2.5 (0.260) / +6.6 (0.340) | -- |
| cross-coin (emission, 1h) | SOL B/Y | -17.3 (0.660) / -13.8 (0.640) | -- |
| cross-coin (emission, 1h) | XRP B/Y | -10.9 (0.620) / +1.9 (0.575) | -- |

## Reproduce
Engine `optitrade_bt.py` (unit-tested 26/26, `t_unit.py`); signals
`optitrade_ai_signals.py`. Scripts: `run_ai_transplant.py`, `run_validation.py`,
`spec_diff_spacing.py`, `run_item3.py`, `corrected_rollup.py`, `run_xcoin.py`.
All reads read-only; nothing written to `trading_corp.db`.
