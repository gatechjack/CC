# Polymarket Arbitrage — Clean-Data Edge Assessment (Board decision input)

**Date:** 2026-08-06
**Author:** read-only analysis session (no code/config changes; `sqlite3 -readonly` against prod `trading_corp.db`)
**Scope:** Evaluate whether `polymarket_arbitrage` shows defensible edge on the clean post-cap sample, for a Board decision to **continue / scope-reduce / close**.
**Posture unchanged:** strategy is paper-only (`ReadOnlyBroker`, `auto_execute: false`). Nothing was deployed, proposed, or altered.

Clean cohort definition (per memo §4B + deploy_log epoch): `polymarket_round_trips`, `COALESCE(division,'polymarket_arbitrage')='polymarket_arbitrage'`, `entry_ts >= 2026-05-21T12:28:07 UTC`. Queries: `queries/poly_edge_p1.sh`, `queries/poly_edge_p2.sh`. Raw output: `poly_edge_p1_out.txt`, `poly_edge_p2_out.txt` (in operator CWD).

---

## TL;DR

**No demonstrated edge — and the strategy's core premise is affirmatively refuted, not merely unproven.**

- The n≥50 floor is cleared: **272 clean resolved trades** (well past the gate).
- Overall clean PnL is **+$6.30 over 272 trades (+$0.023/trade, ROI +2.3%), WR 45.96%** — **statistically indistinguishable from zero** (t = 0.25, p ≈ 0.80; 95% CI on total PnL ≈ **[−$43, +$56]**).
- **The LLM signal is worse-calibrated than the market it bets against** (Brier: LLM **0.254** vs market **0.185** vs coin-flip **0.250**). On the very markets where the LLM disagreed with the market by ≥10% and acted, the **market price was the more accurate estimate**. The LLM barely ties a coin flip.
- No single category or probability band shows edge that would survive as a proven filter. The only coherent n≥30 category signal is **negative** (geopolitics −$5.99).
- **Recommendation: close the live-execution track (never flip `auto_execute`); the honest characterization is "no edge, premise refuted."** Keeping it running only accrues LLM API cost against a thesis its own data contradicts. See §5.

---

## 1. The gate + overall clean performance

| metric | value |
|---|---|
| Clean resolved n | **272** (gate ≥50 CLEARED) |
| Wins / Losses | 125 W / 147 L |
| Win rate | **45.96%** |
| Total PnL | **+$6.30** |
| PnL / trade | **+$0.0232** |
| Total notional | $272.00 (fixed **$1/trade** shakedown sizing) |
| ROI | **+2.3%** |

Cohort hygiene confirmed: all 272 rows are genuine arbitrage rows (`entry_order_id IS NULL`); zero copy-trader leakage (copy-trader is a separate `polymarket_copy_trading` division, 8,793 rows, excluded). Pre-cap resolved = 205, post-cap (clean) = 272.

### Is +$6.30 real or noise? (Not eyeballed — computed.)

Per-trade PnL: mean = 0.02318, E[X²] = 2.34088 → **σ = 1.530**, SE = σ/√272 = **0.0928**.
- **t = mean/SE = 0.25** (p ≈ 0.80). The sign is positive but the magnitude is pure sampling noise.
- **95% CI on total PnL ≈ [−$43.1, +$55.8]** — zero sits comfortably inside; so does a meaningful loss and a meaningful gain. This sample cannot distinguish the strategy from a random $1 bettor.
- WR vs 50%: z = −1.33 (p ≈ 0.18) — not significantly different from a coin flip.
- The market's own model *expected* 116.3 wins (42.8% WR) on this bet set; the strategy delivered 125 (45.96%). The **+8.7 excess wins is within noise** (z ≈ +1.07, p ≈ 0.28) — a whisper of "won slightly more than the market priced," not a signal.

**This is materially different from the pre-cap −$17.12/34 baseline in two ways:** the clean sample is (a) ~flat rather than negative, but (b) large enough (n=272) and paired with a calibration test that lets us make the stronger statement below.

---

## 2. Slice breakdowns (per-slice n<30 flagged "insufficient n")

### (a) By category

| category | n | WR | total PnL | PnL/trade | verdict-eligible (n≥30)? |
|---|---|---|---|---|---|
| other | 97 | 55.7% | +$4.86 | +$0.050 | yes — but "other" is a catch-all, not a coherent tradeable category |
| sports | 82 | 31.7% | +$1.89 | +$0.023 | yes — low WR, near-zero PnL (cheap-NO tilt) |
| geopolitics | 37 | 45.9% | **−$5.99** | **−$0.162** | yes — **the one coherent negative** |
| celebrity | 23 | 65.2% | +$1.36 | +$0.059 | **insufficient n** |
| politics | 20 | 35.0% | +$6.27 | +$0.313 | **insufficient n** (strong PnL but n=20; do not act) |
| crypto | 8 | 50.0% | +$0.13 | +$0.016 | **insufficient n** |
| entertainment | 4 | 25.0% | −$2.50 | −$0.624 | **insufficient n** |
| finance | 1 | 100.0% | +$0.29 | +$0.285 | **insufficient n** |

> Deviation from the requested category list: the data stores **`celebrity` (23)** as its own category distinct from `entertainment` (4). Reported as-stored, not folded.

### (b) By llm_prob bucket

| llm_prob | n | WR | total PnL | PnL/trade |
|---|---|---|---|---|
| 0–20 | 103 | 69.9% | **+$12.82** | +$0.125 |
| 20–40 | 63 | 41.3% | −$0.73 | −$0.012 |
| 40–60 | 38 | 26.3% | +$2.21 | +$0.058 |
| 60–80 | 53 | 24.5% | −$1.01 | −$0.019 |
| 80–100 | 15 | 26.7% | **−$6.99** | −$0.466 (**insufficient n**) |

The 0–20 bucket (n=103) carries the book. But this is the low-YES-prob bucket → almost entirely NO bets on longshots, and its profitability is the mirror image of the entry-price story below — a market-calibration artifact, not an independent signal. The high-conviction 80–100 bucket **loses badly** (see §4 calibration).

### (c) By side

| side | n | WR | total PnL | PnL/trade |
|---|---|---|---|---|
| no | 225 | 48.0% | +$7.58 | +$0.034 |
| yes | 47 | 36.2% | −$1.27 | −$0.027 |

Strategy bets NO 83% of the time. NO side mildly positive, YES side mildly negative; neither significant (NO-side t ≈ 0.33).

### (d) By entry-price bucket (price of the side actually bet)

| entry price | n | WR | total PnL | PnL/trade |
|---|---|---|---|---|
| 0–20c | 70 | 11.4% | **−$11.55** | −$0.165 |
| 20–40c | 43 | 39.5% | **+$12.76** | +$0.297 |
| 40–60c | 81 | 46.9% | −$3.64 | −$0.045 |
| 60–80c | 64 | 76.6% | +$6.89 | +$0.108 |
| 80–100c | 14 | 92.9% | +$1.84 | +$0.131 (**insufficient n**) |

**WR rises monotonically with entry price (11%→39%→47%→77%→93%)** — that is exactly what a *well-calibrated market* produces (price ≈ probability). The PnL pattern is "overpaid for cheap longshots (0–20c bled −$11.55), roughly fair elsewhere." This is a property of the market's pricing, not a discovered edge.

---

## 3. Per-slice discipline (in-sample trap avoided)

The two positive concentrations — **llm_prob 0–20 (+$12.8, n=103)** and **entry_price 20–40c (+$12.8, n=43)** — are the strongest-looking cells and both clear n≥30. They are reported as **leads for out-of-sample validation only.** They are NOT combined into a "would-have-been" strategy on this same data: that is precisely the in-sample composition that produced the LLM-prob gate, category whitelist, and sports-blacklist gates the Board **withdrew** in the 2026-05-21 memo. Any such filter must be validated on trades placed *after* it is defined, never scored on the sample that suggested it.

---

## 4. Calibration — does the LLM beat the free market prior? **No.**

| Brier score (YES basis, lower = better) | value |
|---|---|
| LLM probability | **0.2540** |
| Market-implied probability | **0.1847** |
| Naive coin-flip (always 0.5) | 0.2500 |
| Base YES rate | 0.493 (balanced) |

**The market is decisively better calibrated than the LLM** (0.185 vs 0.254 — the LLM's squared error is ~37% higher). The LLM is a **hair worse than a coin flip.** Because the sample is balanced (YES rate 0.493), coin-flip Brier = 0.25 is the correct no-information reference — and the LLM sits right on it.

Bucketed calibration (LLM's stated YES prob vs what actually happened):

| llm bucket | n | avg LLM YES prob | avg market YES prob | **actual YES rate** |
|---|---|---|---|---|
| 0–20 | 103 | 0.127 | 0.370 | **0.291** |
| 20–40 | 63 | 0.322 | 0.511 | **0.540** |
| 40–60 | 38 | 0.509 | 0.673 | **0.737** |
| 60–80 | 53 | 0.690 | 0.710 | **0.642** |
| 80–100 | 15 | 0.835 | 0.498 | **0.533** |

In **every** bucket the market's probability is closer to the realized outcome than the LLM's. The LLM is systematically **too extreme in both directions**: when bearish it is too low (says 0.13/0.32/0.51, reality 0.29/0.54/0.74), and when bullish it is too high (says 0.835, reality 0.533). Aggregate: the LLM *expected* 182.6 wins (67% implied WR) and delivered 125 (46%) — **overconfident by ~58 wins.** Its highest-conviction disagreements with the market (bucket 5, where it said 83.5% vs the market's 49.8%) resolved at 53.3% — the market was right, the LLM was badly wrong, and that bucket lost −$6.99.

**This is the load-bearing finding.** The strategy's entire thesis is that an LLM identifies mispricings the market missed. On the markets where the LLM disagreed enough to bet, it was the *less* accurate party. There is no edge mechanism.

---

## 5. Verdict (honestly framed)

**(a) Does the strategy as a whole show statistically defensible edge on clean data?**
**No.** +$6.30 / 272 trades, t = 0.25, 95% CI ≈ [−$43, +$56]. Indistinguishable from zero. It would not survive reasonable variance in either direction.

**(b) Does any single category or prob-band show edge with n≥30, as an out-of-sample lead?**
No *positive* n≥30 slice constitutes edge:
- `other` (+$4.86, n=97) is a catch-all bucket, not a coherent, reproducible category.
- The llm_prob 0–20 / entry_price 20–40c positives are **in-sample and price-mechanical** (§2d, §3) — leads at best, and the honest read is they reflect the market's own calibration, not the strategy's.
- The only coherent n≥30 *directional* signal is **negative**: geopolitics −$5.99 (the Iran/Israel/US peace-deal deadline cluster — also the heaviest skip-concentration category, §6).

**(c) If no edge anywhere — say so directly.**
**There is no demonstrated edge anywhere in the clean sample, and the calibration test refutes the premise the strategy is built on.** Given n=272 (well past the floor) and a decisive calibration result, this is not a "needs more paper data" verdict — more data would only tighten the CI around ~zero; it would not rescue a model that is worse than the market it trades against.

**Recommendation to the Board: CLOSE the live-execution track.** Do not flip `auto_execute` — there is no basis for it and there is no plausible path to one absent a fundamentally different (non-LLM, or materially better-calibrated) signal. Because the strategy is paper-only, keeping it *dormant* costs nothing in capital — but it does continue to spend LLM API calls per scan cycle to produce probabilities its own data shows are worse than free market prices. The cleanest honest action is closure / scope-to-zero; if the Board prefers to retain a passive observer, it should do so with the explicit understanding that **there is no path to live.** (This is a decision recommendation, not a config change — none was made.)

---

## 6. Concentration the cap absorbed + correlated-underlying (parked P2)

**Dedupe skips since epoch: 2,090** `polymarket_dedupe_skipped` events — the per-condition_id cap refused 2,090 stacking attempts. By category: other 937, geopolitics 599, politics 276, sports 109, crypto 76, celebrity 62, finance 20, entertainment 11.

Top single-market concentration the cap held back (skips / max open entries seen):
- Mbappe top-goalscorer 48; US×Iran peace-deal Jun-15 46; Messi top-goalscorer 46; US×Iran Jun-7 43; Peru election (Sánchez) 43; Claude Fable 5 restored 42; Peru (Fujimori) 39; IEM Cologne 38; Israel×Iran ceasefire 37; NVIDIA largest-company 36; France WC 35; WTI $95 HIGH 35; **US×Iran Jun-30 33 skips with max_open_seen = 19** (i.e. the cap saw 19 pre-cap open entries on a single market and refused every re-entry — the exact single-market 10×+ stacking the cap was built to stop).

**Correlated-underlying pattern (memo addendum §2) — CONFIRMED still present in clean data.** All 272 clean trades are on distinct condition_ids (no within-condition re-stacking — the cap works), but distinct condition_ids that are effectively one bet persist, and the per-condition cap does not catch them:
- **Crude oil:** `crude-oil-cl-hit` 9 trades (9 strikes) + WTI strikes labeled `other` — the canonical WTI-band cluster from the memo.
- **World Cup:** `soccer-fifwc` 20 + `world-cup-*` series (semifinals/round-of-16/final/group winners/golden-boot/etc.) + Mbappe/Messi goalscorer markets — one tournament, many correlated condition_ids.
- **Iran/Israel/US peace-deal & diplomacy:** `us-x-iran-permanent-peace-deal-by`, `us-x-iran-diplomatic-meeting`, `trump-agree-iran`, `israel-x-iran-*`, `israel-x-hezbollah-*`, `us-iran-deal-*`, `nuclear-deal`, `enrichment-of-uranium`, etc. — same geopolitical event with many deadlines/framings. This cluster maps onto the **geopolitics** category, which is the worst performer (−$5.99), and generated 599 of the 2,090 skips.
- **Elon tweets:** `elon-tweets` 22 (tweet-count thresholds). **MLB:** `mlb` 30 (−$14.89 — different games, so diversified rather than correlated, but a concentrated bet on a category the strategy is weak in).

Implication for the parked P2 follow-up: a per-condition cap alone leaves material correlated-underlying exposure (crude-oil bands, World-Cup outcomes, Iran-deal deadlines). **However, since §4–5 conclude there is no edge to protect, a correlation-aware sizing/cap follow-up is only worth building if the Board elects to continue rather than close.** It is a risk-control refinement, not an edge source.

---

## Appendix — method notes / caveats

- **$1 fixed notional** (total_notional = n = 272). Metrics are per-contract worst-case, not volume-scaled; a live sizing model would change absolute PnL but not the calibration verdict.
- **Selection:** the strategy only bets where |LLM − market| ≥ 10% (divergence gate). The Brier comparison in §4 is therefore on the *selected disagreements* — which is the decision-relevant set: when the model thinks the market is wrong and acts, who is right? The market. There is no selection bias favoring the market here; if anything the sample is chosen where the LLM was most confident the market was mispriced.
- **Significance math** computed offline from SQL aggregates (`AVG(pnl)`, `AVG(pnl²)`, counts) to avoid depending on SQLite math extensions; formulas: σ = √(E[X²]−E[X]²), SE = σ/√n, t = mean/SE.
- **Read-only, no sudo.** All queries ran via `sqlite3 -readonly` per `runbooks/session_start_2026_05_22_polymarket_post_cap.md`. No prod state changed.
