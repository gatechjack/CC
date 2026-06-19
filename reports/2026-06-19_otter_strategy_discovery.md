# Otter strategy discovery — walk-forward search for a fee-clearing scalp edge

**Date:** 2026-06-19
**Branch:** `otter-strategy-discovery-2026-06-19` (off origin/main `1c12d5c`)
**Mode:** READ-ONLY research. NO prod change, NO deploy, NO live trading, NO live sizing. §4. Corpus = local `data/btc_scalping.db`.
**Goal:** find a genuinely net-positive-after-fees scalping strategy on the **Lord Otter** signal set, **Market Cypher BANNED**, walk-forward validated against the **corrected** fee hurdle. A rigorous null is an acceptable result.

> # HEADLINE VERDICT: NULL — no fee-clearing Otter edge survives on this window.
> The single train+validate-positive candidate (`bull/bear_divergence`) is a **look-ahead / repaint artifact**: it nets +0.3–0.5R only when you enter *on the pivot bar the indicator marks in hindsight*; at any tradeable entry (≥1 bar later) it goes negative. The **lockbox confirms this on held-out data.** Every other Otter trigger is net-negative even at k=0, and **zero** of 64 Otter+CVD/MACD/EMA/RSI/regime confluence variants cleared both train and validate. **There is no validated, look-ahead-honest, fee-clearing Lord-Otter edge in the May–Jun 2026 bear/neutral corpus.** (Open interest could not be tested — see §6.)

---

## 1. Method
- **Corpus:** `bars_3m` (38,899 bars, 2026-03-30 → 06-19), signal columns from the TradingView export. Other TFs (1m/15m/30m/1h) present.
- **Otter set (enumerated, corpus-subset):** `otter_buy/sell`, `super_buy/sell_high/std`, `top/bottom_signal`, `bull/bear_divergence`, `ribbon_buy/sell_cross`, `cvd_flip_bullish/bearish`, continuous `vpmo`/`money_flow`/`cvd_*`. ⚠ The prod-ledger Otter names **`spoon_*`, `water_*`, `money_bag_*`, `bias_*`, `pink_box_*` are ABSENT as columns** (not exported) — they could not be tested.
- **Cypher BANNED + isolated:** 55 Cypher/Vumanchu columns (`mc_*`, `wt_*`, `sommi_*`, all `*_circle`, `red/blood_diamond`, etc.) identified and excluded from every candidate. Confirmed zero Cypher in any rule.
- **Walk-forward split (chronological, bear/neutral throughout):** TRAIN Mar30→May15 · VALIDATE May15→Jun1 · **LOCKBOX Jun1→Jun19 (touched once, §5).**
- **Fee hurdle = CORRECTED effective fees** (`fee-model-reconciliation-2026-06-19`): entry 0.0243% (discount card), TP exit 0.0140% (maker), SL exit 0.0400% (taker), slip 0.005%/leg, on notional; per-trade drag = (entry+exit+slip)/stop%. **Not** the overstated all-taker headline.
- **Trade economics:** the strategy's own `build_v2_plan` (ATR/swing SL + 3-leg TP) + `walk_v2`, reused not reimplemented. Entries are corpus bar rows where the signal ≠ 0 (3m-aligned → the `find_bar_at` 60s-window bug does not apply here; noted).

---

## 2. Phase 1 — per-signal (corrected net-per-fire)

| signal | side | TRAIN (n / win% / net) | VALIDATE (n / win% / net) |
|---|---|---|---|
| **bull_divergence** | buy | 57 / 86.0 / **+0.314** | 11 / 81.8 / **+0.213** |
| **bear_divergence** | sell | 45 / 91.1 / **+0.386** | 11 / 81.8 / **+0.241** |
| otter_buy | buy | 23 / 60.9 / −0.163 | 9 / 44.4 / −0.405 |
| otter_sell | sell | 41 / 48.8 / −0.489 | 3 / 66.7 / −0.017 |
| cvd_flip_bullish | buy | 224 / 65.2 / −0.110 | 62 / 40.3 / −0.538 |
| cvd_flip_bearish | sell | 235 / 52.8 / −0.321 | 55 / 60.0 / −0.204 |
| super_buy/sell_high, super_*_std, top/bottom_signal | — | all ≤ 0 or n<12 | inconsistent |

Only **divergence** was net-positive on both folds. Its **86–91% win rate** flagged a look-ahead suspicion.

---

## 3. The repaint test (decisive) — entry delayed k bars after the marking

| k (delay) | bull_div TRAIN | bull_div VAL | bear_div TRAIN | bear_div VAL |
|---|---|---|---|---|
| **0** (on marked bar) | **+0.314** (86%) | +0.213 | **+0.386** (91%) | +0.241 |
| **1** | **−0.194** (60%) | −0.107 | **−0.060** (65%) | +0.240 |
| 2 | −0.154 | +0.003 | −0.287 | −0.048 |
| 3 | −0.395 | −0.040 | −0.295 | −0.360 |
| 5 | −0.251 | −0.223 | −0.261 | −0.344 |

The entire edge lives at **k=0** — entering on the pivot bar the divergence indicator only confirms in hindsight (repaint). At **k=1**, the minimum tradeable delay, win rate collapses to ~60–65% and net goes negative and stays there. **The divergence "edge" is a look-ahead artifact, not a tradeable signal.**

---

## 4. Confluence + regime-conditioning (Otter+CVD/MACD/EMA/RSI) — 64 variants

Tested the tradeable (non-repaint) triggers × {none, macd-align, ema-align, cvd-align, cvd-contra, rsi-mean-revert, regime-with-trend, regime-counter-trend}, train→validate, N-gated (train≥15, validate≥8).

**Candidates clearing BOTH train and validate: NONE.**
- The closest train-positives fail validate: `cvd_flip_bullish`+ema train +0.057 (n=111) → validate **−0.472** (overfit).
- `top_signal`+macd shows validate +0.287/+0.589 on **n=4–12** with train inconsistent — a textbook **noise-fit** (tiny-N, sign-unstable), correctly rejected.
- Regime-conditioning (with-trend / counter-trend proxy) did not rescue any trigger — subsetting net-negative components doesn't create an edge.

---

## 5. LOCKBOX (Jun 1 → Jun 19, held out, one touch) — the headline

The only train+validate-positive candidate was divergence; evaluated on the never-searched lockbox:

| signal | k | n | win% | **net R** | |
|---|---|---|---|---|---|
| bull_divergence | 0 | 44 | 95.5 | **+0.541** | repaint (non-tradeable) |
| bull_divergence | **1** | 47 | 70.2 | **−0.121** | **honest entry** |
| bull_divergence | 2 | 56 | 67.9 | −0.011 | honest entry |
| bear_divergence | 0 | 29 | 96.6 | **+0.585** | repaint (non-tradeable) |
| bear_divergence | **1** | 32 | 78.1 | **+0.093** | honest entry |
| bear_divergence | 3 | 38 | 76.3 | +0.137 | honest entry |

- **bull_divergence:** lockbox confirms the repaint exactly (k=0 +0.54 / honest k≥1 negative). Dead.
- **bear_divergence:** honest entry is *lockbox-positive* (+0.09 to +0.14) — but it was **TRAIN-NEGATIVE** at the same k (k=1 train −0.06, k=3 train −0.295) and **sign-flips across folds** (train− / validate mixed / lockbox+). **Per the discipline it fails selection — you cannot select a signal that lost on the optimization set — so its lockbox-positivity is uncorroborated noise / a small-N regime coincidence, NOT a validated edge.** Reporting this rejection is itself the required finding.

**Train→lockbox gap:** at the *naive* (repaint) k=0, train +0.31/+0.39 "held" to lockbox +0.54/+0.59 — but only because the artifact is consistent. At *honest* entry there is no stable positive to carry. There is no candidate whose train selection is corroborated by an honest lockbox.

---

## 6. Otter + CVD + Open Interest (operator's priority)
- **Open interest: NOT in the corpus** — no OI column in any table. The TradingView exports are OHLCV + Otter/Cypher/CVD/MACD/EMA/RSI only. **Otter+OI could not be tested.** To test it would need a separate **read-only OI history fetch** (BitUnix/Bybit public OI endpoint; the operator authorizes; not a signed/account call) ingested alongside the bars — a clean future add, not available tonight.
- **Otter + CVD: tested, no edge.** The corpus's CVD signals are `cvd_flip_bullish/bearish` (≈960 each) + continuous `cvd_close`. `cvd_flip` alone is net-negative and flips train→validate; as a confluence filter (cvd-align / cvd-contra) on other triggers it rescued nothing (§4). A constructed price-vs-CVD *divergence* was deliberately **not** built — it adds degrees of freedom and, being a divergence, would face the same repaint problem just shown to be fatal.

---

## 7. Verdict + honest scope
**There is no validated, fee-clearing Lord-Otter scalp edge in this corpus.** The only thing that looked like one is a divergence repaint artifact (proven by entry-delay collapse and confirmed on the lockbox); all other Otter signals are net-negative even with look-ahead, and no CVD/MACD/EMA/RSI/regime confluence clears walk-forward. This is consistent with tonight's five prior diagnostics: **on this May–Jun 2026 bear/neutral tape the gross edge is too faint to clear corrected fees — and removing the Cypher volume does not reveal a hidden Otter edge.** The honest posture for this regime remains **shorts-discretion / don't force a signal that the data doesn't support.**

**Scope limit (stated, not fought):** this is ONE bear/neutral regime. Walk-forward catches noise-fitting *within* it; a null here is "no edge on this regime," not "no edge ever." A real edge — if one exists — would more likely appear in a **different regime (trend/transition/high-vol)** that this corpus doesn't contain. The native ETL pipe accumulates forward data; **re-run this exact harness on a future independent window** (especially a non-bear one) before concluding the strategy family is dead. This produced a rigorous **null + harness**, not a live-blessed strategy — and nothing here is recommended for live use.

---

## 8. What was tried / rejected
- 12 single Otter triggers (3m) — only divergence positive → **repaint artifact, rejected**.
- Divergence entry-delay k=0..8 — edge exists only at non-tradeable k=0.
- 64 confluence/regime variants (8 triggers × 8 filters) — **none** cleared train+validate.
- `top_signal`+macd, `cvd_flip`+ema — train/validate-inconsistent **noise-fits**, rejected.
- bear_divergence honest-entry lockbox-positive — **rejected** (train-negative → unselectable; sign-unstable).
- Not done (flagged): open interest (absent), constructed CVD-divergence (DOF/repaint), exhaustive other-TF triggers + exit-structure search (deferred — the consistent negativity of the building blocks makes deeper search mainly a noise-fit risk; better spent on a new-regime window).

**Hard stops honored:** research only, nothing applied/deployed/traded; ZERO Cypher in any candidate (banned set isolated); the LOCKBOX is the reported headline (no train number passed off as the verdict); corrected effective fees used (not all-taker); no cross-regime / live-ready claim (one-window null, future-window confirmation flagged); no git stash; no signed/live API; no polymarket.
