# TASK 2b/2c — Pre-target trail backtest + recommendation (2026-08-05)

**Bottom line: no pre-target trail configuration beats flat-3R on net-of-fee R (0 of 25). H0 — the
2026-06-26 tight-stop finding — is NOT rejected. Recommendation: PARK the trail; keep the flag OFF.**

Harness: `_sfp_pretrail_sweep.py` (extends the already-run `_sfp_betrail_exit.py`; run capped via
`run_capped.ps1`). Entries are byte-identical to the deployed construct — **parity gate passed**
(flat-3R gross reproduces 149/+0.085, 139/+0.397, 170/+0.073, 169/+0.199, pooled **627/+0.182**).
Exit-only change; path-dependent 3m replay; stop-first (honest/worst-case); **net of the SFP fee
model** (entry taker 0.000243; exit maker 0.00014 if the 3R TP fills else taker 0.0004; slip 0.0001);
4.00 yr, 4 coins; in-sample Binance proxy. GROSS+box → evidence, not closure.

## Baseline

| | gross avgR | **net avgR** | net R/yr | WR |
|---|---|---|---|---|
| flat-3R (current live exit) | +0.182 | **+0.085** | +13.40 | 30.0% |

Fees roughly halve gross avgR (~0.10R drag/trade at the SFP ~0.3–1.5% R geometry). All comparisons
below are **net**.

## The full sweep — pooled net avgR by (activation × ATR-multiple), Δ vs flat

| activation ↓ / ATR-mult → | 3.0× | 2.5× | 2.0× | 1.5× | 1.0× | 0.5× |
|---|---|---|---|---|---|---|
| **none** (trail from entry) | +0.035 (−0.051) | +0.004 (−0.082) | −0.009 (−0.095) | −0.070 (−0.155) | −0.076 (−0.161) | **−0.119 (−0.204)** |
| **+1R** | +0.051 (−0.034) | +0.023 (−0.063) | −0.003 (−0.088) | +0.007 (−0.079) | −0.004 (−0.089) | +0.002 (−0.083) |
| **+1.5R** | +0.061 (−0.024) | +0.049 (−0.036) | +0.040 (−0.045) | +0.043 (−0.042) | +0.040 (−0.046) | +0.051 (−0.035) |
| **+2R** | **+0.074 (−0.012)** | +0.070 (−0.015) | +0.060 (−0.026) | +0.049 (−0.036) | +0.070 (−0.015) | +0.066 (−0.020) |
| R-ladder (+1R→BE, +2R→lock1R) | +0.038 (−0.047) | | | | | |

**Every cell is ≤ flat (negative Δ). Monotone structure:** the later the activation and the wider the
ATR trail, the smaller the damage — because a trail that only arms at +2R and sits 3×ATR back rarely
engages before the 3R target, so it approaches "do nothing." The R-ladder reference reproduces the
prior study (−0.047, ≈ the −0.042 in `SFP_BREAKEVEN_TRAIL.txt`).

## Is the best cell real? (clustered SE + holdout)

Best cell **(+2R, 3.0×ATR)**: net avgR +0.074, netR/yr +11.59, WR 36.5%.
- **ΔavgR vs flat = −0.0115, clustered SE (coin×month) = 0.0317, z = −0.36** → statistically
  indistinguishable from flat, and the point estimate is **negative**. Not a beat.
- **Holdout:** IS Δ +0.006 / **OOS Δ −0.029** — the tiny in-sample near-parity flips negative
  out-of-sample. No durable benefit.
- The aggressive cell **(none, 0.5×ATR)**: ΔavgR −0.2045, SE 0.0671, **z = −3.05** → statistically
  significant *damage*. IS −0.169 / OOS −0.240.

## Why — rescue vs tax (the mechanism)

The trail rescues some flat losers (moves the stop up before they die) but taxes flat winners (stops
them out before 3R). **The tax always exceeds the rescue:**

| config | gross rescue (438 losers) | gross tax (182 winners) | net |
|---|---|---|---|
| (+2R, 3.0) best | +91.8R | −99.1R | **−7.3R** |
| (+2R, 2.0) | +118.3R | −134.4R | −16.1R |
| (+1R, 2.0) | +220.4R | −270.2R | −49.7R |
| (none, 0.5) worst | +417.8R | −533.6R | −115.8R |

The tighter/earlier the trail, the more it rescues **and** the more it taxes — and the tax slope is
steeper. The 3R winners carry the strategy; truncating their right tail costs more than the losers
it saves. Drift-null standing does not improve either (flat 55th pct → best 50th pct).

## Where it helps vs hurts (per coin × regime, best cell +2R/3.0×ATR)

| cell | flat net | trail net | Δ | read |
|---|---|---|---|---|
| ETH bull (n=62) | +0.348 | +0.050 | **−0.298** | runner cell — trail guts the right tail |
| XRP bull (n=55) | +0.386 | +0.419 | +0.032 | runner cell, best cell barely holds; tighter cells −0.5 |
| ETH bear (n=77) | +0.261 | +0.283 | +0.023 | ≈flat |
| BTC bull (n=87) | +0.013 | +0.030 | +0.017 | ≈flat |
| SOL bull (n=82) | −0.065 | +0.011 | +0.076 | HELPS — no right tail to protect |
| BTC bear (n=62) | −0.130 | +0.052 | **+0.182** | HELPS — no right tail to protect |
| SOL bear (n=88) | +0.062 | −0.046 | −0.108 | hurts |
| XRP bear (n=114) | −0.021 | −0.037 | −0.016 | ≈flat |

**Where a trail helps:** only the **no-right-tail, low/negative-avgR cells** (BTC-bear, SOL-bull) —
where there's little upside to truncate, so rescue > tax locally. **Where it hurts:** the **runner
cells (ETH-bull, XRP-bull)** that pay for the whole strategy — the trail caps exactly the right tail
those cells depend on. Pooled, the runner damage swamps the negative-cell rescue → net-negative.

## Recommendation

| | verdict |
|---|---|
| **Enable a pre-target trail on the live SFP construct?** | **NO — PARK.** 0/25 configs beat flat net; best is −0.012R and not significant (z=−0.36), worse OOS; aggressive configs do significant harm (z up to −3.05). |
| **Does any config escape H0?** | No. Consistent with both the prior betrail study (−0.042R) and the 2026-06-26 tight-stop arc. This is the expected evidence-closing result. |
| **Only place a trail is not harmful** | The no-right-tail negative cells (BTC-bear, SOL-bull) — but that is a coin×regime-conditional effect that does not pool positive and would need its own study; **not actionable now**. |
| **Implementation note** | Moot regardless: the live SL-ratchet path is an unbuilt `NotImplementedError` stub (`bitunix.py:2229`). Parking costs nothing; enabling would require building + reconciling that path for a negative-EV change. |

**Standing flag stays `pre_tp1_trail.enabled: false` (never wired).** If revisited after the n≥30
live OOS sample, the only hypothesis with any support is a **regime-conditional** trail restricted to
the low/no-tail cells — not a global trail. Evidence only; the operator rules.
