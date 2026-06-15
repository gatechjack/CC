# BitUnix server-side stop: fill-vs-trigger slippage analysis

**Status:** READ-ONLY INVESTIGATION — no code, no fix (a fix, if any, is a separate §4 task).
**Date:** 2026-06-15 · agent SSH read-only per 82fda13 · prod DB read-only.
**Question:** does the B1 server-side catastrophic stop systematically fill past its trigger, by how much, and does it threaten risk accuracy / B2 (maker) economics?

---

## TL;DR

- **Mechanism confirmed (deliberate):** the B1 stop is `slStopType=MARK_PRICE` + `slOrderType=MARKET` (bitunix.py:1019-1022). It triggers on MARK price then sends a **market** close, so the fill is the prevailing market price *after* the trigger fires. On a fast move the market runs past the trigger before the close fills → slippage. This is a chosen tradeoff: the code comment (bitunix.py:980-981) picks MARKET for a *guaranteed* exit "not a limit that could sit unfilled through the move."
- **Slippage is velocity-driven, not constant:** on a normal-velocity stop-out the fill lands at/near the trigger (~0); on a fast-move bar it can blow well past. The one large case (trade 2, +138pt / +0.21%) occurred on a 524-pt 3-minute bar — ~10× the range of the small-slip case (trade 1, −5pt, on a 52-pt bar).
- **Sample is tiny — LOW confidence:** only **2 real measurements** (trades 1 & 2, operator-booked fills). Trade 3 (tonight) was P2-auto-booked at the stop *level* → its real fill is `slippage_unreconciled=true` (unmeasured). The paper simulator fills stops **exactly at the level** (zero slippage) — so it provides no slippage data and **under-models real stop risk**.
- **Risk-accuracy impact (real):** on the fast-move case, realized stop distance was **+54.7%** vs the planned level. Position sizing and the paper backtest both assume fill-at-level, so they **under-state** per-trade risk on fast-move stop-outs.
- **B2 economics:** the fast-move slippage (0.21% of notional) is **~4–8× the entire maker fee saving** for a round trip. So B2's projected maker benefit must be assessed against **real slipped stop fills**, not the idealized fill-at-level the paper sim uses.
- **Fix warranted?** **No change to the live stop.** Market-fill slippage is the acceptable cost of a *guaranteed* catastrophic exit; a limit-stop's non-fill risk is unacceptable for a catastrophic stop. The warranted follow-up (separate §4) is to **model** the slippage in the sim, in sizing, and in B2 — and to gather more live stop-out data to estimate its distribution.

---

## 1. Mechanism — MARK trigger → MARKET fill (confirmed in code)

`BitunixBroker._build_order_body` (bitunix.py:1019-1022) attaches the B1 stop to the entry order:

```python
body["slPrice"]     = _amount_str(sl_px)   # the trigger level = recorded stop_price
body["slStopType"]  = "MARK_PRICE"         # triggers on MARK price (wick-resistant)
body["slOrderType"] = "MARKET"             # market close on trigger (guaranteed fill)
```

Design intent, verbatim from the code comments (bitunix.py:978-981):
- `slStopType=MARK_PRICE` — "fires ahead of liquidation on the same reference the venue liquidates against; wick-resistant vs LAST_PRICE."
- `slOrderType=MARKET` — "guaranteed exit on trigger (not a limit that could sit unfilled through the move)."

So the **recorded `stop_price` is the trigger**, and the **`result_price` (when it's a real fill) is the post-trigger market execution**. The gap between them is the market move that occurs between MARK crossing the trigger and the MARKET order filling at LAST. On a fast move that gap is large. This is exactly the hypothesised mechanism — confirmed.

---

## 2. Data — every BitUnix stop-out with a recorded level AND a fill

Source: prod `paper_trade_record`, `division='bitunix_futures'`. All three live trades are SHORT (sell), so slippage (adverse) = `fill − trigger` (a higher buy-to-close fill is worse).

| # | entry→exit (UTC) | trigger (stop_price) | recorded fill (result_price) | slippage | as % px | provenance |
|---|---|---|---|---|---|---|
| 1 | 06-14 18:24 → 19:12 | 63805.34 | 63800.10 | **−5.24 pt (favorable)** | −0.008% | `operator_manual_booking` (real fill) |
| 2 | 06-14 21:30 → 21:40 | 65004.48 | 65142.30 | **+137.82 pt (adverse)** | **+0.212%** | `operator_manual_booking` (real fill) |
| 3 | 06-15 23:49 → 23:51 | 66291.08 | 66291.08 (= trigger) | **unknown** | — | `auto_booked_from_stop_level`, `slippage_unreconciled=true` |

**Paper / replay closes (N≈dozens, 2026-05):** every paper loss has `result_price == stop_price` exactly and every paper win `result_price == tp_price` exactly. **The simulator models ZERO stop slippage** — it fills at the recorded level by construction. So paper data cannot measure slippage and the **backtest under-models real stop risk** by the live slippage term.

**Net measurable real slippage sample: N = 2** (trades 1 & 2). One favorable (−5pt), one large-adverse (+138pt). A distribution (median/max) from N=2 is not statistically meaningful — reported only descriptively:
- range observed: −5.24 pt … +137.82 pt (−0.008% … +0.212% of price)
- the +138pt case is the only adverse one and is the figure that motivated this analysis; whether it is typical, an outlier, or the low end of the adverse tail **cannot be determined from N=2**.

> Trade 3 is independently notable: it is the **first real stop-out under the P2 engine**, and P2 **auto-booked it** (`result_source=auto_booked_from_stop_level`) — i.e. P2 self-recovery fired on a genuine event. But because P2 books at the *level*, it contributes no slippage measurement (and its booked loss is optimistic by the unmeasured slippage).

---

## 3. Driver — bar velocity (3-minute bars around each exit)

| trade | exit bar | bar range (H−L) | what happened | slippage |
|---|---|---|---|---|
| 1 | 06-14 19:12 | **51.6 pt** | high 63809 barely wicked past the 63805 trigger, closed back to 63758 | −5 pt (≈ at trigger) |
| 2 | 06-14 21:39 | **523.8 pt** (body +325) | violent up-move 64925→65437; trigger 65004 blown through, market filled 138pt deep into the bar | +138 pt |
| 3 | 06-15 23:51 | 37–77 pt | price hovering at the trigger; calm | unknown (likely small) |

The large-slip trade occurred on a bar **~10× the range** of the small-slip trade. This is direct, mechanism-consistent evidence that **slippage scales with the velocity of the move that triggers the stop** — exactly what MARK-trigger → MARKET-fill predicts. (Causally strong; statistically weak at N=2. Trade 3's calm bars suggest its real, unmeasured slippage was small.)

---

## 4. Risk-accuracy impact

The plan sizes risk to the **entry→trigger** distance (the modeled max loss = 1R). The realized loss is the **entry→fill** distance.

| trade | planned stop dist (entry→trigger) | realized (entry→fill) | realized / planned |
|---|---|---|---|
| 1 | 125.94 pt | 120.70 pt | **0.96× (−4%)** |
| 2 | 251.78 pt | 389.60 pt | **1.55× (+55%)** |

So on the fast-move stop-out, the **per-unit realized risk was ~55% larger than the model assumed**. Consequences:
- **Sizing is optimistic for fast-move stops:** a trade sized for a 1R = X loss can realize ~1.5R on a fast-move stop-out — the per-trade risk budget is exceeded on exactly the trades that matter most.
- **Both the paper backtest and the P2 auto-book record stops at the trigger level** (zero slippage). So realized-loss accounting is **systematically optimistic** vs real slipped fills. The drawdown breaker reacts to *actual* equity (so it isn't "wrong"), but the *expected* drawdown path is worse than the plan models, because per-trade risk is under-stated.

> Accounting caveat: the booked `actual_r_multiple` for trade 2 was −0.629 (i.e. < 1R), which is inconsistent with a +55% over-run. This is almost certainly a qty/`max_dollar_risk` accounting artifact (BitUnix truncates qty to a min increment — see the first-fill note "PnL on broker-trunc qty 0.0004"). The **price-level** slippage (+138pt, +0.21%, +55% of planned stop distance) is the model-independent, trustworthy metric and is what this section uses.

---

## 5. B2 (maker) economics input

Fee rates (`config/strategies.yaml:1344-1348`): modeled **taker 0.04% / maker 0.014%** → maker saving **0.026%/side**, **0.052%/round-trip**. (The live trades' recorded fees imply the *actual* VIP3 taker ≈ **0.019%**, so the real maker saving is smaller still — making the comparison below conservative.)

Compare to the fast-move stop slippage (trade 2, **0.212%** of notional):
- vs one side's maker saving (0.026%): **≈ 8×**
- vs the full round-trip maker saving (0.052%): **≈ 4×**
- even if maker were *free*, one fast-move stop's slippage = ~**5.6×** the entire round-trip taker fee.

**A single fast-move stop-out's slippage dwarfs the maker fee saving by ~4–8×.** B2 only makes the *entry* (and possibly TP exits) a maker fill — it cannot make the *catastrophic stop* a maker order (that must stay market for guaranteed fill). So the relevant question is the trade-population expectation:

> Expected slippage cost / trade ≈ P(stop-out) × P(fast-move | stop-out) × E[slip | fast] (≈0.21%).
> Maker saving / trade (entry-maker) ≈ 0.026% (modeled).
> These are **the same order of magnitude** for plausible rates — e.g. if ~40% of trades stop out and ~30% of those are fast-move blow-throughs, the expected slippage term (~0.025%) ≈ the entire entry-maker saving.

**Conclusion for B2:** the maker fee saving is real but small, and it is materially offset — possibly fully — by fast-move stop slippage. **B2's net edge must be evaluated against real (slipped) stop fills, not the fill-at-level the paper sim uses.** The paper backtest currently over-states B2's net benefit by exactly this slippage term. (Rates above are illustrative — the actual offset depends on the slippage distribution this analysis cannot yet pin down.)

---

## 6. Is a fix warranted? (options — NOT building any)

The slippage is the **deliberate cost** of `slOrderType=MARKET` (guaranteed fill). Options:

1. **Limit-stop** (`slOrderType=LIMIT` at the trigger / a buffer). Caps slippage **but risks NON-FILL**: on a fast move the limit can be jumped and the order sits unfilled, leaving the catastrophic position **open through the move** — far more dangerous than slippage. ⛔ **For a catastrophic stop, a fill that might not happen defeats the entire purpose.** Not recommended.
2. **Tighter trigger buffer** (set `slPrice` earlier). Does not reduce slippage (the market still runs past the trigger on a fast move); only shifts where the trigger fires, and tighter triggers cause more whipsaw stop-outs. Marginal, not a real fix.
3. **Accept it as the cost of guaranteed protection.** Market-fill slippage is bounded by the move size; the catastrophic stop's job is to *guarantee* the position closes, not to close at an exact price. This is the current (deliberate) design.
4. **Model the slippage instead of changing the stop.** Add a slippage assumption to (a) the paper sim/backtest (today: zero), (b) position sizing (size with a slippage allowance so realized risk ≤ budget even on fast-move stops), and (c) B2's economics (maker savings net of expected stop slippage). This corrects the risk-accuracy and B2 problems **without touching the live catastrophic stop.**

**Recommendation:** **Do not change the live stop.** Market-fill slippage is the acceptable, deliberate cost of a guaranteed catastrophic exit, and the limit-stop alternative's non-fill risk is unacceptable. The warranted work (separate §4 task, if pursued) is **option 4 — model the slippage** in the sim, in sizing, and in B2 economics. The single highest-value enabler is **more data**: the signed-fetch auto-book (task #1) would book *real* stop fills instead of the level estimate, which both fixes the optimistic loss accounting AND supplies the slippage distribution this analysis lacks.

---

## 7. Confidence & limitations

- **N = 2 real slippage measurements.** One favorable, one adverse. No distribution can be estimated; the +138pt figure may be typical, an outlier, or the low end of the adverse tail — unknown.
- **Both measured fills are `operator_manual_booking`** — operator-recorded fills, not programmatically reconciled from BitUnix trade history (a signed call, out of scope here / barred read-only). Treat as accurate-but-not-machine-verified.
- **Trade 3's real fill is unreconciled** (auto-booked at the level); its calm bars suggest small real slippage, but it is unmeasured.
- **Paper sim models zero slippage**, so it cannot extend the sample — and itself under-models risk (a finding in its own right).
- The B2 expected-value rates (stop-out %, fast-move %) are **illustrative guesses**, not measured.

**Bottom line:** the mechanism is confirmed and the fast-move slippage is real and material (+0.21%, +55% of planned risk on the one fast case), but its *frequency/distribution* is not yet estimable. No live-stop change is warranted; model the slippage and gather real-fill data (via the auto-book task) before sizing B2 on idealized fills.
