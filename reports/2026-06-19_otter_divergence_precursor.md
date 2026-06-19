# Divergence-precursor search — can a real-time signal anticipate the Otter divergence?

**Date:** 2026-06-19
**Branch:** `otter-div-precursor-2026-06-19` (off origin/main `1c12d5c`)
**Mode:** READ-ONLY research. NO prod/deploy/live. §4. Otter-primary; Cypher studied descriptively only.
**Goal:** the divergence-scalp is real but un-bot-able (the column repaints; the live alert fires ~2–3 bars late). Find a real-time corpus signal that reliably PRECEDES the bull/bear divergence by 1–4 bars, so a bot could enter early at the low.

> # VERDICT: NULL — no real-time signal anticipates the divergence tradeably.
> The best precursors raise the div probability only from a **~2% base rate to ~3–5%** (95%+ false positives), don't hold train→validate, and the highest-scoring ones are Cypher divergences that **themselves repaint**. Empirically, trading the best real-time precursors with the operator model is **net-negative on train, validate AND lockbox** (−0.31 to −0.83R). **No Otter signal leads the divergence at all.** A price-pivot reversal simply isn't telegraphed by any prior indicator firing in this corpus. (Interim: one bear/neutral window; div N~150/fold.)

---

## Why it's hard (the base-rate frame)
A divergence appears in a random 2–4 bar window only **~2.0–2.2%** of the time. For a precursor to be a usable trigger, P(div follows | precursor) must be *far* above 2%. It isn't.

## Step A — precursor mining (precision / recall / lift, bull & bear, train→validate)
Top precursors by lift (N-gated), with validate stability:

**bull_divergence** (base 2.16%): yellow_cross *(Cypher)* prec 5.1% / lift 2.4 (val 3.7, N=24); `ema_dn` 3.1% / 1.5 (val 1.4); short_ema_signal *(Cy)* 1.6 (val 2.7). Best lifts ~2–3× = **precision ~3–5% (95%+ false positives).**
**bear_divergence** (base 2.05%): super_sell_high 6.5% / 3.2 → **validate 0.0 (N=6, collapses)**; long_ema_signal *(Cy)* 2.1 → 1.2; `rsi_exit_ob` 1.4 → 2.7 (prec ~3–6%); buy_signal *(Cy)* 2.0 → 2.8.

Two structural failures:
1. **The constant-firing trap:** the high-*recall* signals (stoch/wt divergence, bull_candle, red_diamond — recall 0.5–0.58, "present before most divs") have precision ≈ base rate (lift ~1) — useless, exactly as warned.
2. **No Otter signal leads:** `otter_buy` recall 0.006; `cvd_flip`/`ribbon`/`super` lift ~0.9–1.5 (≈ base rate). The Otter family does not precede its own divergence.

## Step B — repaint-check the leaders
The best-scoring precursors that aren't ≈base-rate are themselves **divergences** (`stoch_*_divergence`, `wt_*_divergence`, `rsi_*_divergence`, the `*_circle` pivots) — pivot-based, **future-confirmed → repaint**, so they fire as late as the Otter div and can't lead it. Dropped. The surviving *real-time* precursors (RSI/MACD/EMA crosses, ribbon, cvd_flip) are the ≈base-rate weak ones.

## Step C — trade the real-time precursors (operator model): all net-negative
Enter on each surviving real-time precursor (toward the anticipated div), tight local-extreme stop + R=2, corrected fees:

| precursor | side | TRAIN | VALIDATE | LOCKBOX |
|---|---|---|---|---|
| ema_dn | buy | −0.62 | −0.83 | −0.76 |
| macd_dn | buy | −0.76 | −0.81 | −0.67 |
| rsi_exit_os | buy | −0.37 | −0.50 | −0.33 |
| rsi_exit_ob | sell | −0.43 | −0.48 | −0.31 |
| macd_up | sell | −0.68 | −0.60 | −0.48 |
| ema_up | sell | −0.69 | −0.60 | −0.54 |

Net-negative everywhere, win rates 20–40% (R=2 scalp on a non-leading trigger = mostly stop-outs). The precursor entry is ~random scalping → loses after fees, as the base rate predicts.

## The deeper finding
**A price-pivot reversal is, by nature, not reliably telegraphed by a prior indicator firing.** If it were, that indicator would itself be the reversal signal — and the earlier searches would have found *it* (they didn't). The divergence's value is that it *identifies* the pivot (in hindsight, ±3 bars); nothing in the corpus reliably *predicts* the pivot 2–4 bars early. So the "anticipate the repainting div with a real-time leader" path does not unlock a bot-able edge **on this data**.

## Cypher handling (as agreed)
Cypher signals were studied descriptively as precursors. The Cypher leaders that scored above base rate were themselves repainting divergences (Step B) or unstable on validate — so there is **no tradeable Cypher precursor to bring back for an approval decision.** The standing ban stands; no fork raised.

## Honest scope + what would actually be worth trying
- **One bear/neutral window**, div N~150/fold; small-N precursor stats are noise-prone — guarded by precision/base-rate + walk-forward + lockbox, all of which the candidates failed.
- The operator's real edge is **discretionary** (reading the real-time low + structure). The genuinely promising forward paths are **off this corpus**: (1) order-flow / footprint / L2 book-imbalance data (a real leading microstructure signal, not a TradingView indicator column); (2) the **actual order-block / supply-demand zones** as the filter (not the swing proxy); (3) more data / a non-bear regime. None of these are testable from the current indicator-column corpus.

**This is a rigorous null, not a failure.** No real-time indicator-column precursor anticipates the Otter divergence well enough to bot. Nothing applied; not live-blessed.

**Hard stops honored:** research only, nothing deployed/traded; Cypher studied descriptively only (no Cypher in a tradeable candidate); LOCKBOX reported (no train-as-headline); repainting precursors dropped (not treated as tradeable); no cross-regime/live verdict; corrected effective fees (not all-taker); no git stash; no signed/live API; no polymarket.
