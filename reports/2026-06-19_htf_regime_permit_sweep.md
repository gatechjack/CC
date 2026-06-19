# HTF-regime timeframe PERMIT sweep — does a lower regime TF restore longs?

**Date:** 2026-06-19
**Branch:** `htf-regime-timeframe-sweep-2026-06-19` (off origin/main `1c12d5c`)
**Mode:** READ-ONLY analysis tooling. NOTHING applied to prod — no scoring/regime-config change, no deploy. Prod read via read-only SSH (`mode=ro`, 82fda13). Per §4.
**Scope (operator-chosen):** PERMITS only (trade-walk performance deferred). PA held FIXED — the population is the real prod score-CLEARED events; only the HTF composite is varied, isolating the regime contribution. The tested classifier (`compute_regime`/`get_trade_permissions`) is REUSED, not reimplemented.

> **HEADLINE — the hypothesis is NOT supported on this data.** Lowering the regime timeframe does **not** restore bull permits — it slightly *reduces* them (regime-permitted longs: current 664 → lower-TF 382–420). Two reasons: (1) the daily composite was **NEUTRAL 58% of the time, not pinned-bear** — it already permitted 45% of cleared longs at the regime level; (2) the real bull bottleneck is **upstream** (the scorer emits ~9× more sell than buy cleared signals) and at **PA validation** (kills ~94% of cleared longs), **not** the HTF regime gate. Lowering the regime TF is the wrong lever. (Data caveat: one window; see §6.)

---

## 0. Harness fidelity (validated before trusting the result)

Reconstructed composite (a) = 1h/4h/1d regime vs the LIVE `htf_gate_decision` regime at the same 992 timestamps:
- **Regime agreement: 974/992 = 98.2%** · buy-only 61/62 = 98.4% · **buy long-forbidden agreement 62/62 = 100.0%**.
- All 18 mismatches are near-boundary BEAR↔NEUTRAL / BEAR↔STRONG_BEAR flips (expected: corpus is Bybit, live used BitUnix bars + live funding; I use `funding=None`). The reconstruction is faithful.

---

## 1. The current regime calc (pinned)

`trading_corp/agents/strategies/bitunix_htf_regime.py`. Per-TF on **1H/4H/1D**: EMA(20/50/200) alignment + market-structure (HH/HL) + ADX(14)>20 + MACD-hist → Bull/Bear/Range/Transitional. Composite = signed weighted sum, weights **{d1:0.5, h4:0.3, h1:0.2}** → ≥0.7 STRONG_BULL, ≥0.3 BULL, ≤−0.3 BEAR, ≤−0.7 STRONG_BEAR, else NEUTRAL. `regime_forbids_side`: BEAR/STRONG_BEAR/SAFE_MODE → long size×0; **NEUTRAL permits BOTH sides at 0.5×**; BULL/STRONG_BULL permit longs.
**The TF set (h1/h4/d1) is hardcoded in `HTFContext`; only the weights/thresholds are config-driven** → swapping in lower TFs is a CODE change, which this harness simulates by feeding lower-TF bars into the three slots.

---

## 2. The sweep (score-cleared population: buy 1,469 / sell 13,669; 2026-05-11 → 06-19)

Primary metric = **regime-level permit** (`_matrix_base` allows the side; pure regime effect, independent of price/levels/funding). Secondary = **full permit** (layers proximity/vol/funding hard-zeros).

| composite | slots (lo→hi) | **bull regime-permit** | bull full-permit | bear regime-permit | bear full-permit | NEUTRAL-for-long % | bull:bear (regime) |
|---|---|---:|---:|---:|---:|---:|---:|
| **(a) current** | 1h/4h/1d | **664** (45%) | 244 | 13,653 | 7,817 | 58.2 | 1 : 20.6 |
| (b) | 30m/1h/4h | 420 | 99 | 12,495 | 4,477 | 72.3 | 1 : 29.8 |
| (c) | 15m/30m/1h | 382 | 87 | 11,122 | 3,103 | 79.6 | 1 : 29.1 |
| (d) | 3m/15m/1h | 414 | 84 | 10,350 | 1,426 | 79.2 | 1 : 25.0 |

Regime distribution over the cleared population:
- **(a) current: NEUTRAL 8,766 (57.9%)**, BEAR 4,335 (28.6%), STRONG_BEAR 1,997 (13.2%), BULL 40 (0.3%).
- (b): NEUTRAL 9,750, BEAR 2,859, STRONG_BEAR 1,331, BULL 1,192, STRONG_BULL 6.
- (c): NEUTRAL 9,502, BEAR 1,606, STRONG_BEAR 1,480, BULL 1,544, STRONG_BULL 1,006.
- (d): NEUTRAL 8,668, BEAR 1,911, STRONG_BEAR 1,236, BULL 2,656, STRONG_BULL 667.

---

## 3. What the numbers say

1. **Current (a) permits the MOST bull, not the least.** 664/1,469 cleared longs (45%) pass the regime gate at the regime level; lowering the TF *reduces* that (420/382/414). So the HTF regime gate is not what's starving longs.
2. **The daily composite was mostly NEUTRAL (58%), not pinned-bear.** The operator's premise — "entire window was a bear daily → daily forbids longs" — is only partly true: STRONG_BEAR was 13%, BEAR 29%, but NEUTRAL (which permits both sides) was the plurality. The daily already let longs through 45% of the time.
3. **Lowering the TF makes the book *more* bear-skewed at the regime level** (1:20.6 → 1:29.8 / 1:29.1 / 1:25.0), and the full-permit longs collapse (244 → 84) because lower-TF swing levels sit closer → more `proximity_to_resistance` hard-zeros.
4. **The "NEUTRAL-or-better" frequency does rise with lower TF (58→79%)** — the operator's stated mechanism is real *in aggregate* — but it does NOT translate to more *bull* permits, because buy signals fire during short-term weakness (counter-/mean-reversion entries); at those moments the lower-TF regime is more often decisively BEAR and forbids the long. The higher TF, being more often NEUTRAL, is paradoxically *more* permissive of counter-trend longs.

---

## 4. The real bull bottleneck (reframe — HTF-vs-PA isolation)

Holding PA fixed and isolating HTF shows the HTF regime gate permits 45% of cleared longs. So the starvation is elsewhere, and it compounds:
- **Upstream scorer skew:** the score-cleared population is already **1,469 buy : 13,669 sell (1 : 9.3)** — the scorer emits ~9× more sell signals. No regime gate can fix that; it's a signal/scoring-side imbalance.
- **PA validation:** per the diagnostic (3e6a608), PA kills **93.9%** of cleared longs (vwap/structure alignment fail) — and PA runs *before* the HTF gate, so most longs never reach the regime gate at all.
- The HTF regime gate is the **least** of the three bull filters. Lowering its TF addresses the wrong stage.

---

## 5. Tradeoff curve / sweet spot

There is **no sweet spot that restores a 2-sided book by lowering the regime TF** on this data. Lowering the TF: (i) does not increase bull permits, (ii) reduces full-permit longs via proximity noise, and (iii) makes the regime more "decisive" (more BULL *and* BEAR, fewer NEUTRAL at signal times) which cuts permits on both sides. If anything, the current 1h/4h/1d composite is the most permissive of longs of the four. **Candidate conclusion: don't lower the regime TF; the lever for longs is the PA-validation gate and the upstream signal/scorer skew.**

---

## 6. Caveats & confounds

- **One regime window.** May 11 → June 19 is a single (mostly-NEUTRAL, partly-bear) daily regime — though notably the daily was *not* pinned-bear. A real verdict on the best composite needs regime-**transition** / high-vol data; this run finds candidates and **refutes the lower-TF hypothesis on this data**, it is not a final cross-regime answer.
- **mc_a_yellow_x side-bug** (miscategorized `side: buy`): this is a **scorer-side** issue, upstream of the regime gate. It does NOT distort these permit counts (permits are computed on the already-score-cleared population from the regime classification only). It mildly *favors* bull in the scorer, so it works against — not for — the starvation.
- **Venue:** regime reconstructed from Bybit corpus bars; live used BitUnix. 98.2% fidelity (§0) bounds the impact.
- **PERFORMANCE DEFERRED** (per scope): the gated trade-walk per config is not run here. Given the permit result, performance on the *lower-TF composites* is **low-value** (they don't add bull opportunity) — if a follow-up runs, it should target the PA gate and the scorer skew, not the regime TF.

---

## 7. Deploy note
Even setting the data aside, lowering the regime TF would be a **CODE change** (the TF set is hardcoded in `HTFContext`, only weights/thresholds are config) — but this sweep says **don't**: the regime TF is not the binding constraint for longs. **Nothing applied. Read-only.**

**Hard stops honored:** read-only harness, nothing applied to prod; PA held fixed (HTF isolated); tested classifier reused (not reimplemented); no verdict rendered (candidates + hypothesis-refutation on one window, transition data flagged); no git stash; no signed/live API; no polymarket.
