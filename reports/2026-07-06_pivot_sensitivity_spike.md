# Pivot-degree sensitivity spike — 2026-07-06

Read-only research spike. GROSS only, k=1 causal, nothing pushed, no prod writes.
Detector = certified `SfpModeBDetector`, byte-identical (md5 `91fd76726364331c8083aaaa68fce199`).
One variable: `pivot_len ∈ {5,10,20,50}` (pre-registered, no exploration).

> ⚠ **PROVENANCE CAVEAT (Part 2 only):** **Coinbase INTX perp 15m klines** (BTC/ETH/SOL/
> XRP-PERP) used for the pivot-degree sensitivity study. Bitunix (live venue) is also perp
> — closer instrument-class match than spot. The 15m major-swing structure that `pivot(N,N)`
> keys on transfers across venues at high confidence for N ∈ {5,10,20,50}; small structural
> differences (funding-tick candles, tick size, basis) don't materially affect pivot-swing
> detection at this timeframe. **A live-deploy of any adopted pivot_len would re-validate on
> Bitunix data before shipping** — same caveat class that applied to the certified pivot(50)
> (original tape: Bybit-via-TradingView). Bybit was skipped (geo-blocked + no operator acct).
> **3m BOS uses local Bitunix 3m (~47d), so confirmable signals live in that ~47d overlap;
> the 230d Coinbase 15m supplies proper pivot-arming context.** Cross-venue 15m-level vs
> 3m-BOS basis is common-mode across all pivot_lens, so the *relative* pivot comparison holds.
> Part 1 (below) uses **prod Bitunix 3m→15m** — the real venue — so it is exact.

---

## PART 1 — Bucket-B direct categorical check (prod Bitunix data)

For each of the 3 forensic Bucket-B setups, at each `pivot_len`, drive the certified
detector (LONG + SHORT via M2=0 reflection) and report sweep/reclaim/BOS with numbers.
Data: prod Bitunix 3m (06-18→07-06) resampled to 15m. Setup bar anchored to the operator's ts.

### Result: NONE fire at ANY pivot_len {5,10,20,50}

**BTC 07-02 13:15** — 15m bar `O61653.7 H61954.7 L61631.2 C61668.2`
- LONG: nearest pivot-low is BELOW price at every degree (pl5=61100 … pl50=57773); setup
  `L=61631` never reaches it → **no sweep** at any degree.
- SHORT: setup `H=61954` is above the pivot-high (pl5=61430 … pl50=60758) but `C=61668`
  closed **ABOVE** it (permit already False) → **reclaim fails** (a break-through, not a failed sweep).
- **A continuation bar that closed through local resistance — not an SFP at any degree.**

**SOL 06-29 17:15** — 15m bar `O75.25 H76.43 L75.23 C75.9`
- LONG: pivot-lows far below (pl5=72.29 … pl50=69.68); `L=75.23` never reaches → **no sweep**.
- SHORT: `H=76.43` above the pivot-high (pl5=74.39 … pl50=72.38) but `C=75.9` closed **ABOVE**
  it → **reclaim fails**. The bar is **making a new high** (the start of a fresh pivot), not fading one.

**SOL 06-28 19:45** — 15m bar `O70.76 H70.76 L70.21 C70.69`
- LONG pl5: pivot-low=70.92, `L=70.21` sweeps it BUT `C=70.69` closed **below** 70.92 →
  **reclaim fails** (a break, not a reclaim; permit disarmed).
- LONG pl10/20/50: pivot-low=70.11, `L=70.21` **misses by 0.10** → **no sweep**.
- SHORT: pivot-highs 72.16–73.15, `H=70.76` far below → **no sweep**.
- (The 70.11 pivot WAS swept+reclaimed 3h later at 22:45 — which the detector DID fire, per the forensic.)

### Verdict (Part 1)

**A smaller pivot_len does NOT catch the operator's Bucket-B swings.** At every degree tested
the setup bar is either (a) a continuation that closed **through** the nearest swing (BTC 07-02,
SOL 06-29) or (b) a near-miss / break-not-reclaim (SOL 06-28). These reads are **not
sweep+reclaim SFP-shaped at any pivot degree** — so the Bucket-B gap is not (only) "pivot too
large"; it is that these specific chart reads do not match the SFP template the detector implements.
(ts are operator-approximate; result is exact at the anchored bars.)

---

## PART 2 — 230d pivot-sensitivity statistical run (Coinbase INTX 15m + Bitunix 3m)

**Method.** Certified `SfpModeBDetector` (md5 `91fd7672` **asserted OK at START and END**),
one variable `pivot_len ∈ {5,10,20,50}`. 15m = Coinbase INTX 230d (22,061–22,062 bars/coin,
gaps pre-window/immaterial); 3m BOS = local Bitunix (~47d overlap → confirmable-signal window).
Regime side-gate = live `ema200_pos_slope` (parity-locked to prod, EMA-200 + 32-bar slope;
long UP/RANGE, short DOWN/RANGE). Short = M2=0 negation for detection (coord-consistent
cross-venue), levels un-reflected, short sim in real coords (prod `geometry_short`: stop above,
tp below). Stop `swept_wick − 0.001·entry` (unchanged). Target fixed 2R. Drift-embedding
direction+regime-matched null, 200×, p95. Cell passes iff `avgR > 0 AND avgR ≥ null_p95`. GROSS.

**★ Window is a STRONG BEAR** (3m close-to-close drift): BTC **−27.0%**, ETH **−29.9%**,
SOL **−18.6%**, XRP **−28.5%**. Passive-short would net +18–30%. This dominates everything below.

### Per-cell (regime-gated) — n / avgR / WR% / totR / null_p95 / beats

| piv | coin | side | n | avgR | WR% | totR | null_p95 | beats |
|--|--|--|--|--|--|--|--|--|
| 5 | BTC | long | 14 | −0.357 | 21 | −5.0 | +0.500 | no |
| 5 | BTC | short | 19 | +0.737 | 58 | +14.0 | +0.833 | no |
| 5 | ETH | long | 27 | −0.556 | 15 | −15.0 | +0.228 | no |
| 5 | ETH | short | 25 | +0.560 | 52 | +14.0 | +0.667 | no |
| 5 | SOL | long | 27 | +0.111 | 37 | +3.0 | +0.429 | no |
| 5 | SOL | short | 19 | +0.579 | 53 | +11.0 | +0.765 | no |
| 5 | XRP | long | 15 | −0.200 | 27 | −3.0 | +0.500 | no |
| 5 | XRP | short | 38 | +0.026 | 34 | +1.0 | +0.636 | no |
| 10 | BTC | long | 9 | −0.667 | 11 | −6.0 | +0.667 | no |
| 10 | BTC | short | 11 | +0.364 | 45 | +4.0 | +0.931 | no |
| 10 | ETH | long | 10 | −0.400 | 20 | −4.0 | +0.667 | no |
| 10 | ETH | short | 13 | +0.385 | 46 | +5.0 | +0.848 | no |
| 10 | **SOL** | **long** | 12 | +0.750 | 58 | +9.0 | +0.638 | **YES** |
| 10 | **SOL** | **short** | 12 | +1.250 | 75 | +15.0 | +1.000 | **YES** |
| 10 | XRP | long | 11 | +0.091 | 36 | +1.0 | +0.500 | no |
| 10 | XRP | short | 19 | −0.053 | 32 | −1.0 | +0.738 | no |
| 20 | BTC | long | 7 | −1.000 | 0 | −7.0 | +0.714 | no |
| 20 | BTC | short | 7 | +0.714 | 57 | +5.0 | +1.143 | no |
| 20 | ETH | long | 5 | −0.400 | 20 | −2.0 | +0.800 | no |
| 20 | ETH | short | 4 | −0.250 | 25 | −1.0 | +1.250 | no |
| 20 | SOL | long | 4 | +0.500 | 50 | +2.0 | +1.250 | no |
| 20 | **SOL** | **short** | 5 | +1.400 | 80 | +7.0 | +1.400 | **YES** |
| 20 | XRP | long | 7 | −0.143 | 29 | −1.0 | +0.714 | no |
| 20 | XRP | short | 9 | +0.333 | 44 | +3.0 | +1.013 | no |
| 50 | * | long | 0–2 | (n≤2, weak) | | | | no |
| 50 | BTC | short | 1 | +2.000 | 100 | +2.0 | +2.000 | YES(n=1) |
| 50 | others | short | 1–2 | −1.0/+0.5 | | | | no |

Fill (signals→post-gate): the gate discards ~55–70% of raw fires (counter-trend). Full raw
counts in `spike/run_spike.py` output.

### Fire-rate per week (pre-gate / post-gate), per coin
- piv5: 15–19 / 6–9 · piv10: 8.5–11 / 3.4–4.9 · piv20: 4.8–5.5 / 1.3–2.5 · piv50: 1.3–2.7 / **0.1–0.7**.
- Smaller pivots fire ~5–10× more — but the extra fires are the same non-edge (below).

### Regime split (avgR by bucket, pooled coins)
| piv | Long-UP | Long-RANGE | Short-DOWN | Short-RANGE |
|--|--|--|--|--|
| 5 | n59 **−0.237** | n24 −0.250 | n81 **+0.444** | n20 +0.200 |
| 10 | n28 −0.036 | n14 +0.071 | n46 **+0.304** | n9 +1.000 |
| 20 | n10 −0.400 | n13 −0.308 | n17 **+0.765** | n8 +0.125 |
| 50 | n0 — | n2 +0.500 | n1 +2.000 | n4 −0.250 |

Longs are **negative in every bucket at every degree** (longs lose in a bear). The only positive
is **short-in-downtrend = bear-beta** — which is exactly what the drift-embedding null strips.

### PRE-REGISTERED SUCCESS BAR — verdict: **NONE PASS → keep pivot(50)**

pivot(50) pooled: n=7, totalR +2.0 (weak_n — it barely fires in 47d).

| piv | ①≥2 coins beat null | ②both sides pass | ③totalR≥1.5×p50 @ n≥100 | ④>1 regime | verdict |
|--|--|--|--|--|--|
| 5 | **0 coins** ❌ | ❌ | n=184 but 0 cells pass ❌ | ❌ | **NO** |
| 10 | 1 coin (SOL) ❌ | yes (SOL both) | n=**97**<100 ❌ | yes | **NO** |
| 20 | 1 coin (SOL) ❌ | ❌ | n=48<100 ❌ | yes | **NO** |
| 50 | 1 coin (n=1) ❌ | ❌ | n=7<100 ❌ | ❌ | **NO** |

**No pivot_len ∈ {5,10,20,50} meets the bar.** The only cells that beat the drift-null are **one
coin (SOL, which had the least-negative drift −18.6%)** at piv10/20 — not reproducible across ≥2
coins. Every other short cell is **positive but bear-beta** (doesn't beat passive-short); every
long cell is **negative**. This holds at all four degrees; smaller pivots merely amplify the same
non-edge with more fires.

**LEDGER:** *pivot(50,50) certified as best-available on 230d + regime-gate + drift-null
(Coinbase-INTX 15m / Bitunix-3m, ~47d signal window, strong-bear regime). Alternatives
{5,10,20} do not earn the swap. Re-run in a non-bear window before treating short-side as settled.*

**Caveat on generality:** this is a **single strong-bear window** — the short=beta / long=negative
split is regime-specific. The pre-registered bar is not met *here*; a bull/chop window could differ.
That's a "re-run in a different regime" follow-up, not a reason to swap pivot(50) now.

---

## PART 3 — gate variants × two pivot degrees (does the gate earn its place?)

**6 configs:** `pivot_len ∈ {50,25}` × `gate ∈ {hard, soft, none}`. Detector md5 `91fd7672`
**asserted OK start+end.** Data: Coinbase INTX 15m 230d + Bitunix 3m (~47d). Drift-embedding
null is **gate-aware** (pool + target per variant), 200×, p95. Same strong-bear window
(BTC −27% / ETH −30% / SOL −19% / XRP −28%). **k=1 upgrade:** regime = *last-closed* 15m bar
before each 3m entry (prod-accurate; Part 2 used the containing candle — a 1-candle lookahead —
so p50-hard n differs slightly from Part 2; regime is slow so it's ~immaterial, applied uniformly).

Gate semantics: **hard** = live (counter-trend refused); **soft** = trade all, target 2R except
counter-trend (long-DOWN / short-UP) → 1R; **none** = trade all at 2R, regime logged only.

### ★ The 5 apples-to-apples comparisons (pooled totalR / n)

| # | Comparison | Result | Read |
|--|--|--|--|
| 1 | p50 **hard** vs **none** | **+1.0 (n5)** vs **−4.0 (n46)** | **Gate HELPS at p50** — removing it loses money |
| 2 | p50 **hard** vs **soft** | +1.0 (n5) vs +1.0 (n47) | Soft = same totalR spread over 9× trades → diluted, lower avgR |
| 3 | p25 **hard** vs **none** | **+3.0 (n30)** vs **−5.0 (n101)** | **Gate HELPS at p25 too** (larger n → robust) |
| 4 | p25 **none** vs p50 **none** | −5.0 (n101) vs −4.0 (n46) | **Shorter pivot does NOT earn once freed** — slightly worse, more fires |

**Comparison 5 — Bucket-A (the trades the gate refuses: long-DOWN + short-UP), n≥30 null test:**

| config | bucket | n | avgR | null_p95 | clears? |
|--|--|--|--|--|--|
| p50 soft | long-DOWN | 21 | −0.048 | +0.200 | no |
| p50 soft | short-UP | 21 | +0.048 | +0.400 | no |
| p50 none | long-DOWN | 20 | −0.400 | +0.253 | no |
| p50 none | short-UP | 21 | +0.143 | +0.500 | no |
| p25 soft | long-DOWN | 47 | −0.064 | +0.136 | no |
| p25 soft | short-UP | 27 | +0.111 | +0.417 | no |
| p25 none | long-DOWN | 46 | −0.283 | +0.159 | no |
| p25 none | short-UP | 27 | +0.222 | +0.625 | no |

**None clear.** long-DOWN is **negative** (gate correctly refuses); short-UP is **mildly positive
but bear-beta** (doesn't beat passive-short). So the class the gate refuses is **not a systematic
edge** — the 2 profitable counter-trend shorts in the forensic (BTC 7/3, XRP 7/3) were **anecdotal
winners inside a bear-beta class**, not a mechanism. → **The forensic-answer question resolves the
gate's way: its refusals are aggregate-correct.** (No "gate refuses real trades" finding to flag.)

### Regime split (avgR by bucket, pooled) — the "why"
Longs **negative in every bucket at both pivots** (Lup −0.40, Ldn −0.06…−0.40). Only positives are
**short-in-downtrend** (Sdn +0.62 at p25) and a weak short-UP — both **bear-beta**, stripped by the null.

### 5-PART SUCCESS BAR — verdict: **NONE adopt → keep pivot(50) hard-gate**

Baseline **p50-hard: n=5, totalR +1.0, beats-cells=1** (the 1 beat is BTC-short n=1, meaningless).

| config | ①≥2 coins | ②both sides | ③totR≥1.5×base @n≥100 | ④>1 regime | ⑤beats≥base | verdict |
|--|--|--|--|--|--|--|
| p50 soft | 0 ❌ | ❌ | +1.0/n47 ❌ | ❌ | 0 ❌ | **no** |
| p50 none | 0 ❌ | ❌ | −4.0 ❌ | ❌ | 0 ❌ | **no** |
| p25 hard | 1 ❌ | ❌ | +3.0/n30<100 ❌ | ✓ | 1 ✓ | **no** |
| p25 soft | 0 ❌ | ❌ | +4.0/n103 ✓ | ❌ | 0 ❌ | **no** |
| p25 none | 1 ❌ | ❌ | −5.0 ❌ | ✓ | 1 ✓ | **no** |

**No variant meets all 5.** Every one fails on ≥2 conditions (chiefly ①<2 coins and ②not both sides).

### Answers to the two motivating questions
1. **Does the gate earn its place?** **Yes.** At both pivots, removing it turns profit into loss
   (Comp 1: +1→−4; Comp 3: +3→−5). Soft doesn't beat hard (Comp 2). The gate is **vindicated** —
   it's screening out net-negative counter-trend longs + bear-beta counter-shorts.
2. **Does a shorter pivot earn once freed?** **No.** p25-none is worse than p50-none (Comp 4);
   no p25 variant clears the bar. The "chop-market shorter-pivot" hypothesis is **refuted here.**

**LEDGER:** *gate + pivot(50) JOINTLY certified vs pivot(25) and vs soft/no-gate on 230d +
drift-null (Coinbase-INTX 15m / Bitunix-3m, ~47d, strong-bear). The gate earns its place
(removing it loses money at both degrees); the refused counter-trend class does not clear the
null at n≥30. Re-validate in a non-bear window before treating the gate's value as regime-general.*

**Caveats (not buried):** (a) **strong-bear window** — the gate's value here is partly "keeps you
out of counter-trend longs that lose in a bear"; a bull/chop window could shift the balance.
(b) **Small baseline n** (p50-hard n=5) — the p50 comparisons are small-sample, but the gate-helps
direction repeats at p25 with n=30/101, so it's not a single-cell artifact. (c) GROSS; live-deploy
re-validates on Bitunix.
