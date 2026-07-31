# Wide-Stop Trend-Cross — Division Candidate Dossier

**Status:** PARKED by operator decision (2026-07-31). Fully researched, go-live math drafted, build not started.
**Owner:** GT_Jack / Trading Corp — Bitunix candidate division
**Research artifacts:** `research/optitrade/`, `research/optitrade_ai/`, `research/optitrade_followups/` (repo CC, branch claude-2026-07-31b lineage)
**This document:** everything needed to resume without re-deriving. Written 2026-07-31.

---

## 1. Executive summary

A 1h trend-following strategy on BTC/SOL/XRP perpetual futures (ETH sits out), discovered during the falsification of two purchased "OptiTrade" TradingView scripts. The vendor's products were shown to have no edge; this strategy is a **house-owned residue**: the vendor's EMA-cross signal family, re-geometried with lessons from the SFP construct research (wide stop, single 3R target, no trade management) and gated by the platform's existing micro-regime classifier.

- Expected: **~+0.17R per trade, ~9.5 trades/month, ~+1.65R/month** (3-coin universe, net of 0.06%/side taker fees, Binance 4-year backtest)
- Return stream is **independent of the SFP construct** (<6% time overlap, ~0.00 daily-R correlation) — pure diversification if real
- Best evidence grade achieved: pooled 4-coin p=0.035 on Binance (drift-controlled null); never cleared p95 on the division's own-bucket null standard
- Honest label: **strongest lead the arc produced, not a proven edge.** In-sample, one venue, marginal significance. The live log is the only remaining upgrade instrument.

---

## 2. The frozen specification (do not tune without restarting the evidence chain)

### Signal (per coin, 1h bars, evaluated at bar close)
| Component | Value |
|---|---|
| Fast EMA | EMA(close, 30) |
| Slow EMA | EMA(close, round(30 × 2.2)) = EMA(close, 66) |
| Long entry | crossover(fast, slow) AND fast > slow AND RSI(14) > 55 |
| Short entry | crossunder(fast, slow) AND fast < slow AND RSI(14) < 45 |
| Cooldown | ≥ 6 bars since last same-direction signal (minSep=6) |
| Entry price | Signal-bar close |

### Regime gate (part of the strategy, not optional)
| Component | Value |
|---|---|
| Classifier | `micro_regime` — the existing corpus tagger: 15m ADX(14) + EMA50 + ATR |
| Rule | Long only when direction = trend_up; short only when trend_down; range/ambiguous/warmup = no trade |
| Provenance | Chosen causally in STUDY C ablation (only gate above its own null median); ps_trail30, RD-nonrange, ribbon all tested and inferior for THIS strategy |

### Risk geometry
| Component | Value |
|---|---|
| Stop | entry ∓ 3.0 × ATR(14) — wide stop is load-bearing (mirrors the SFP exit-sweep finding: wider wins monotonically) |
| Target | Single TP at 3R (no rungs, no breakeven move, no trailing, no management) |
| Ambiguity | SL-first accounting (conservative) |
| Position limits | 1 open per coin, max 3 per division |

### Universe
BTC, SOL, XRP. **ETH sits out** — gated ETH is breakeven (avgR +0.047); adds fees and ops surface for ~zero R. (A valid cell state per the librarian doctrine.)

---

## 3. Provenance & evidence chain (with commits)

The full arc, in order. Every step committed under `research/optitrade*/`.

| # | Step | Result | Commit / artifact |
|---|---|---|---|
| 1 | Corpus audit + venue search | Binance USD-M perp corpus adopted (12M rows, 2022-07→2026-06, native 4h/1d, provenance proven 3 ways); Bybit DBs = secondary venue | bd1d5f7, 03c4c54 |
| 2 | Vendor script 1 (TP-SL) honest backtest | Marketed +183R (BTC 15m) → OOS **−31R gross / −295R net**. Fees dominate low TFs (0.66–0.93 R/trade at 3m). 2/20 cells net-positive, single-window | RESULTS.md |
| 3 | Rolling 5-window WF on survivors | Survivors dissolve except: SOL 1h **gross** signal persistent 5/5 windows but fee-fragile. **Operator ruling: TP-SL strategy = no edge, closed** | ROLLING_RESULTS.md |
| 4 | Vendor script 2 ("OptiTrade AI") signal transplant | All 8 families net-negative in aggregate. Its on-chart "backtest" is not a simulation (close-vs-signal-close labels; repaints) | 0357a45, AI_RESULTS.md |
| 5 | Validation + spec-diff gate | **Material divergence found**: agent's spacing (emission-clock) differed from vendor's (fresh-event clock). The accidental house variant tested BETTER. Vendor-exact fails Binance null (p=0.125) | a4148c3, SPEC_DIFF.md |
| 6 | Drift control + long/short split | ETH 1h emission variant: both sides beat matched random baselines; short side dominant. Not drift-riding | 553adcb, ITEM3.md |
| 7 | Pre-registered cross-coin falsification | **Continuation config does NOT travel**: 0/6 out-of-coin cells clear the null. ETH = selection coin. Arc's continuation lead dies | b61c85a, XCOIN.md |
| 8 | Wrap + refund | FINDINGS.md written; vendor refund requested under lifetime guarantee | d02e56e |
| 9 | STUDY B — wide-stop trend-cross (the hybrid) | Signal family from #3 + geometry lessons from SFP research. **Travels**: SOL +28.4 / BTC +42.2 / XRP +22.2 net06 on Binance; strongest coin (BTC) is a travel coin, not the selection coin | bc2a242, STUDY_B.md |
| 10 | STUDY B deepening | Pooled 4-coin clears Binance p=0.035; Bybit does not (p=0.135, SOL flips negative there — operator ruled Bybit sample one-regime/short, Binance trusted). **Construct-overlap: independent stream** (<6% overlap, −0.02 pooled daily-R corr) | 9413cea |
| 11 | STUDY C — regime attribution + gate ablation | Directional hypothesis supported (long R in up-regimes, short in down, all classifiers). **micro-aligned = only causal gate above its own null** (58th pctl, ~2× avgR, keeps 74% of trades, flips recent window −7.3→+7.1). macro60 gate non-causal (contaminated, flagged). ps_trail30 below null here — gates are strategy-specific | 04216b7, STUDY_C.md |
| 12 | STUDY D — RD_range anomaly | Dead: the os-flip-with bucket is outcome-correlated (near-tautology), not predictive. n<30 everywhere | af26180 |
| 13 | STUDY E — timeframe extension | 15m: signal-dead (gated GROSS ≈ 0) — closed. 4h: positive (+44 gated, avgR +0.31 gross) but thin (n=137), SOL-carried, gate adds nothing there — **parked lead** for a possible second cell later | af26180 |
| 14 | Go-live math (DRAFT) | Tables in §4–5 below | 2599aeb |

**Sanity discipline held throughout:** the SFP fractal canary (n=13,426, sumR −1.07, long 6718/−84.14, short 6708/+83.08) reproduced byte-exact before the hybrid studies (587900b). All nulls seed-pinned after the STUDY A blemish.

---

## 4. Expected performance (adopted stream: 1h + micro gate, Binance net06)

| coin | armed n (4y) | net06 sumR | avgR | trades/mo | R/mo | E[R] @ n=30 |
|---|---|---|---|---|---|---|
| SOL | 164 | +35.0 | +0.244 | 3.4 | +0.73 | +6.4 |
| BTC | 164 | +25.7 | +0.220 | 3.4 | +0.54 | +4.7 |
| XRP | 129 | +18.1 | +0.178 | 2.7 | +0.38 | +4.2 |
| ETH (excluded) | 188 | +0.4 | +0.047 | 3.9 | +0.01 | +0.1 |
| **3-coin division** | **457** | **+78.9** | **+0.173** | **~9.5** | **+1.65** | **+5.2** |

Dollar translation (linear in the risk knob): at `risk_per_trade_usd` = $100 → ~$165/month expected; $25 → ~$41/month.

Win-rate profile: low-30s% (single 3R target does that) — P&L arrives as clusters of −1R stops punctuated by +3R wins. Losing months are normal and expected.

---

## 5. Risk envelope, kill/keep calibration, leverage

### Rolling 30-trade net06 envelope (pooled, from the 4y backtest)
| p5 | median | p95 | worst historical stretch | share of 30-trade windows negative |
|---|---|---|---|---|
| −10.4R | +3.6R | +20.4R | −19.9R (BTC) | **31%** |

Interpretation for the future pre-registration: even if the edge is exactly as measured, there is ~1-in-3 chance the first ~30 live trades (~3 months) net negative. Kill thresholds should sit outside the envelope (e.g., ≤ −10R at n=30 is 5th-percentile territory; ≤ −20R is beyond anything the backtest produced), keep thresholds inside it. **Numbers to be set by operator at arm time, before first fill.**

### Leverage
Stop = 3·ATR ≈ 2.1–4.1% of price (p50) up to 3.8–8.3% (p95) per coin. Max-safe leverage at a 2× liquidation buffer: SOL ~6.0×, XRP ~6.2×, BTC ~13.2×. **Uniform 6× recommended** (margin-efficiency only; leverage does not change expected P&L under R-sizing). Build must include the LIQ-BUFFER GUARD: reject any entry where liquidation distance < 2× stop distance; never silently reduce leverage.

### Fee sensitivity
Primary net column = 0.06%/side taker. At 0.04%/side (VIP tier), all numbers improve modestly; fee-in-R at 1h with the wide stop is small (~0.03–0.05 R/trade) — fees are NOT the binding constraint at this TF (they were the entire story at 3m/15m; see §7 lesson 2).

---

## 6. Why this might be real (and why it might not)

**For:**
- Strongest coin (BTC) was a falsification coin, never selected on — the opposite profile of the ETH-continuation lead that died at cross-coin
- Coherent mechanism: directional R concentrates correctly by regime across three independent classifiers
- Independent return stream vs the SFP construct — portfolio value even at modest edge
- The geometry (wide stop, no management) independently re-derives the SFP exit-sweep finding
- Micro gate fixes the recent-window softness causally, not by shrinking n

**Against:**
- In-sample throughout; the Binance corpus shares the live feed lineage (P5) — a LEAD, not OOS
- Pooled significance cleared once (Binance p=0.035); never cleared the division's own-bucket p95 standard
- Bybit sample disagreed (operator ruled it one-regime/short; still on the record)
- SOL's most recent ungated window was −11.1R (gate mitigates; doesn't erase)
- The signal family passed through multiple rounds of looking; residual selection history cannot be fully discounted

**Only remaining arbiter:** live fills on Bitunix, n≥30, judged against pre-registered thresholds.

---

## 7. Transferable lessons banked from this arc (apply to ALL future evaluations)

1. **Vendor dashboards are optimizer maxima.** Any "backtest" rendered by the strategy being sold is best-of-grid in-sample until proven otherwise. Reproduce independently or ignore.
2. **The fee-in-R law:** fee drag per trade ≈ fee_rate × 2 × price / (stop_multiple × ATR). Fixed dollar fee ÷ shrinking risk unit = tight-stop low-TF strategies pay 0.3–0.9R/trade in costs. Wide stops and higher TFs are structurally advantaged at taker rates. Gross-sum-R is the wrong optimization objective under taker fees.
3. **Falsification ladder that worked:** honest replication → rolling windows → drift-controlled permutation null (preserve direction multiset per window) → config-neighborhood perturbation → cross-venue → **pre-registered cross-coin** → regime attribution. Each rung killed something that looked alive on the rung before.
4. **Spec-diff gate:** when replicating third-party logic, diff implementation against source at code level once a result survives — the one divergence found here moved headline numbers 40–60%.
5. **Gates are strategy-specific:** ps_trail30 (best for SFP) is below-null for this strategy; micro-aligned (best here) was never SFP's winner. Regime routing is per strategy×coin — the librarian thesis, confirmed from a second direction.
6. **Outcome-correlated splits are tautologies:** any post-entry event that co-moves with the trade's own P&L (STUDY D's os-flip) describes, never predicts.

---

## 8. Reactivation checklist (do these IN ORDER when resuming)

1. **Corpus freshness:** Binance corpus ends 2026-06-30; monthly refresh is a standing backlog item. Refresh through the latest full month, then re-run STUDY B + micro gate (frozen config, zero tuning) on the extended window. The new months are true OOS relative to every study in this dossier — treat that re-run as the reactivation gate: if the frozen config is materially negative on the fresh months, stop and reassess before building.
2. **Fee check:** confirm current Bitunix taker rate for the (new) division account; regenerate the net column if the tier changed.
3. **Re-read §5 and set kill/keep thresholds + `risk_per_trade_usd` + leverage** in the pre-registration BEFORE any build session, so the numbers predate the code.
4. **Account provisioning:** NEW Bitunix account (one-account-per-division; venue nets per (symbol, side) — sharing the SFP account would merge positions and corrupt both reconcilers).
5. **Then issue the build prompt (§9).**
6. Post-arm: maiden-trade watch (house tradition), n≥30 clock, judge only against the pre-registered numbers.

Also parked, revisit only after live 1h data accrues: the **4h variant** (positive, thin, SOL-carried — STUDY E) as a possible second cell.

---

## 9. The build prompt (ready to paste when reactivating — after checklist steps 1–4)

```
New division build: wide-stop trend-cross, LIVE from arm (no shadow mode, do not build one).
Universe: BTC, SOL, XRP 1h (ETH sits out). Frozen config: L=30 EMA cross vs EMA 66 on close, RSI(14) bias 5, minSep 6, SL=3*ATR(14), single TP at 3R, SL-first semantics via venue OCO.
GATE (part of the strategy, not optional): micro-aligned - entry direction must match micro_regime direction at the entry bar (trend_up for longs, trend_down for shorts; range/ambiguous = no trade). micro_regime computed live from bitunix_bar_history 15m exactly as the corpus tagger defines it (ADX(14)+EMA50+ATR) - reuse the tagger logic, do not reimplement.
Architecture: SFP division pattern - own division config, single risk chokepoint, venue OCO bracket per entry, paper_trade_record authoritative, audit events, per-division reconciler, kill-switch + per-coin disable + side filter + all params hot YAML. risk_per_trade_usd YAML knob, value mine at arm.
Leverage: YAML knob, default 6x, recorded per-trade in extra_json.leverage. LIQ-BUFFER GUARD: at entry, compute venue liquidation price for intended qty/leverage; REJECT entry with loud audit event unless liq distance >= 2x the 3*ATR stop distance. Never silently reduce leverage.
Account: NEW Bitunix account (never touch the SFP account); stub credentials, I provision via the usual secrets path. Max 1 open/coin, max 3/division.
Deliverable: module + tests (reuse optitrade_bt signal logic verbatim, do not reimplement), deploy plan with rollback knobs, and the pre-registration doc pre-filled from the go-live math (3-coin: E[R]@30 = +5.2, envelope p5 -10.4 / p95 +20.4, 31% of 30-trade windows negative) with kill/keep thresholds BLANK for me. No deploy - build and show me.
```

---

## 10. Artifact index

| Location | Contents |
|---|---|
| `research/optitrade/` | Script-1 (TP-SL) engine, study, rolling WF, maker-variant hold notes |
| `research/optitrade_ai/` | Script-2 transplant, VALIDATION, SPEC_DIFF, ITEM3, XCOIN, FINDINGS, SHADOW_LOGGER_SPEC (dormant), redacted vendor .pine (gitignored) |
| `research/optitrade_followups/` | repro_sanity, STUDY_A_RIBBON (ribbon gate — dead), STUDY_B + deepening, STUDY_C (regime), STUDY_D/E, DRAFT go-live math |
| `Desktop/backtest_corpus/binance_perp_corpus.db` | Primary corpus (refresh monthly) |
| `cc/data/*_scalping.db` | Bybit secondary venue (operator: one-regime, short — reference only) |
| Engine to reuse | `optitrade_bt.py` signal + bracket logic (26/26 unit tests incl. SL/TP-first straddle) |

**Key commits:** 587900b (sanity) · bc2a242 (STUDY B) · 9413cea (deepening) · 04216b7 (STUDY C) · af26180 (D/E) · 2599aeb (go-live DRAFT)

---

*End of dossier. Nothing in this document is a verdict; all rulings remain the operator's.*
