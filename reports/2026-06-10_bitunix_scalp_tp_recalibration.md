# Bitunix Scalp TP-Ladder Recalibration — Fee-Survivable Levels + Sizes

**Date:** 2026-06-10 (~16:50 UTC capture) · **Session:** operator-supervised, **analysis-only**, read-only agent-driven SSH (policy `82fda13`)
**Branch:** `bitunix-scalp-tp-recalibration-2026-06-10` (dedicated worktree off `origin/main` `c8d3902`; unmerged)
**Builds on:** `2026-06-10_bitunix_untaken_trades_deep_dive.md` (fee finding) · `2026-06-10_bitunix_day2_expanded_review.md` (Q3 TP-leg finding) · `2026-06-08_bitunix_silence_investigation/FINDINGS.md` (set b source)
**Reuses:** `q3harness.py` bar-walk engine (deep-dive `fec53ec`) · validated `build_trade_plan` (`trade_plan.py`, byte-identical to prod) · `FeeConfig` (taker 0.04% / maker 0.014% / slip 0.005%/side → **0.09% taker round-trip**)

> ## VERDICT — **NULL FINDING (robust).**
> **No TP ladder produces positive net-after-fee expectancy on this entry signal at the current
> fee tier (0.09% taker round-trip) in the ~3–4% ATR regime.** Nine structurally-diverse ladders
> (incl. both operator hypotheses) all cluster **net −0.12 to −0.21 R/trade** on the combined
> 211-trade set. The constraint is **structural, not ladder-shape**: gross edge tops out at
> **~0.14–0.17 R**, fee drag is **~0.27 R and ladder-invariant**, so gross < fees no matter how
> the legs are arranged. The current baseline ladder is already near the achievable frontier.
> **Recommendation: do NOT re-ladder. No §4 TP-parameter change is indicated.** The live-flip
> economics gap is gross-edge-vs-fees, which a TP-ladder change cannot close (see §6, §7).
>
> **Anti-overfit demonstration baked in (§4.1):** the set(a)-only "near-breakeven" leaders
> (single-target @ fee-floor, 2-leg-pulled) **invert to worst-in-class** on the larger same-regime
> set(b) — tuning to the 19 live trades would have selected the *worst* out-of-sample ladder.

---

## 0. Scope, constraints, hard stops

- **Analysis only.** No code/config/parameter change; no prod write. This report does NOT ship any
  TP change — that is a separate operator-gated, §4-Backtester-approved session (and §6/§7 conclude
  no ladder change is warranted regardless).
- Read-only throughout; agent-driven `sqlite3 -readonly` over SSH per CLAUDE.md `82fda13`
  (operator away from PowerShell; this agent ran the read-only streamers directly — disclosure below).
- **Hard stops (status):** any code/config/param change → STOP (**not triggered**); any prod write →
  STOP (**not triggered**); constructible N < 15 → STOP (**not triggered**, N(a)=19 alone); **baseline
  decomposition contradicts the deep-dive net-negative finding → STOP and reconcile (NOT triggered —
  baseline reproduces and confirms net-negative, §3).**
- **Out of scope:** shipping any TP change; Day-5 close-out; the P2 fee-accrual code change; gate
  tuning (gates validated by the two deep-dives); Polymarket; the live-flip decision itself; SL/entry
  changes (held fixed — see §6 structural note).
- **Disclosure (`82fda13`):** all prod contact was read-only — one `sqlite3 -readonly` CSV pull
  (`tpdata.sh`: 3m bars + `trade_plan_decision` + `paper_trade_record` + silence `audit_event` rows)
  streamed `Get-Content tpdata.sh -Raw | ssh azureuser@trading.jacksumner.com "tr -d ...|bash"`.
  No writes, no live-mode primitives, no 1m public-API GETs.

## 1. Method + validation (gates passed before trusting any alt ladder)

Each trade is re-walked under candidate ladders using the strategy's **own** `build_trade_plan`
(SL params held fixed; only TP legs vary) and the q3harness walk mechanics: SL-first worst-case tie,
ordered TP fills, **BE-after-TP1 / TP1-after-TP2 ratchet**, full-position round-trip fee
(`feeR = round_trip% · entry / risk_per_unit`). Harness: `tpcal.py` (committed).

Two validation gates — **both PASS**, so alternative-ladder walks are trustworthy:

| Gate | Check | Result |
|---|---|---|
| **V1** | baseline plan rebuilt from stored `trade_plan_decision` inputs == stored plan | **19/19** ✓ |
| **VWALK** | baseline **walk** reproduces recorded `actual_r_multiple` (set a) | **19/19** within 0.05 R ✓ (leg-fills match recorded `filled_legs` exactly) |
| intrabar | bars where SL+TP both touched (P3 `70d50f7` ambiguity) | **0/19** set(a) · **2/211** combined → negligible |

VWALK is the load-bearing gate: my walk **is** the recorded reality under the baseline ladder, so
re-walking the same trades under alternative ladders measures a real counterfactual, not a model artifact.

## 2. Trade set (state N)

| Set | Definition | N input | N walked | Skipped (`build_trade_plan`) | Validation |
|---|---|---|---|---|---|
| **(a)** | post-fix taken trades (`paper_trade_record` ≥ 2026-06-09 03:49:41Z) | **19** | 19 | 0 | recorded-R validated (VWALK 19/19) |
| **(b)** | silence-window `vol_tier_extreme` suppressed signals (2026-06-02 22:00→fix), deduped by (bar,side), reconstructed via the deep-dive method | **208** unique | **192** | 16 (`swing_too_close`/`fees_too_high`) | reconstruction method validated (V1); intrabar caveat applies |
| **(a)+(b)** | combined | 227 | **211** | 16 | — |

- **Set (a): 19 trades, 15 W / 4 L, all `sell`. TP3 filled 0/19.** (The deep-dive's 17 → +2 newer
  trades: one small win +0.125, one loss −1.0; this lowers gross expectancy from +0.175 to **+0.110 R**
  — see §3 / §9 on small-N instability.)
- **Set (b): 192 walked, 129 W / 63 L (67% sells dominate).** Proximity-gate post-fix re-block: **0 of
  208** (vol-zeroed signals are by construction not the near-support ones — those carry the
  `proximity_to_support` tag instead). **`atr_pct_d1` = 3.28–4.25%** — the *same* high-vol regime as
  set(a) (3.98–4.15%). **Expanding N adds samples, not regime diversity** (critical caveat, §9).
- Unlike set(a), **set(b) TP3 filled 24×** and TP2 74× — early June had genuine runs the post-fix
  window lacked. This drives the overfit inversion in §4.1.

## 3. Baseline decomposition — WHY it is net-negative

Baseline ladder = **TP1 max(0.5R, 2×fee) @25% · TP2 1.0R (HTF-snap 0.5–1.3R) @50% · TP3 2.5R @25%**.

| Set | gross avg R | **net avg R** | net cum R | % net-positive | avg feeR |
|---|---|---|---|---|---|
| (a) N=19 | +0.110 | **−0.204** | −3.88 | 32% | 0.314 |
| (b) N=192 | +0.146 | **−0.120** | −23.12 | 51% | 0.266 |
| (a)+(b) N=211 | +0.143 | **−0.128** | −26.99 | 49% | 0.271 |

**Premise check (hard-stop gate): PASS.** Baseline reproduces and *confirms* the deep-dive's
net-negative finding (deep-dive cited −0.13 R on 17 trades; combined here −0.128 R; set(a) now
−0.204 R after two more trades). **No contradiction → no STOP.**

Three mechanisms, all confirmed numerically:

1. **Fee drag is large and ladder-invariant.** `feeR = 0.09% · entry / risk`; with stops ~0.27–0.31%
   of price → **~0.27–0.31 R per trade**, charged once on the full position regardless of how the legs
   are arranged. Re-laddering cannot reduce it (proven by identical feeR across same-set candidates).
2. **The big leg never pays its way (regime-dependent).** TP3 @2.5R filled **0/19** in set(a). The 25%
   parked there exits at BE on wins / −1R on losses → pure drag in the post-fix window. (But it filled
   24× in set(b) — so this is a *regime* artifact, not a universal flaw; see §4.1.)
3. **The fee floor bites the wins, the losses are full −1R.** TP1's fee-floor (`2× fee`) guarantees its
   own 25% clip nets ≈ +1× fee, but the other 75% rides to BE on the many TP1-only wins (~0.13–0.20 R
   gross), while the 4 losses pay the full −1R **plus** fee ≈ −1.31 R net. The −1.3 R loss tail
   outweighs the consistent small wins (median net is *positive* +0.07 to +0.32; the mean is dragged
   negative by the tail — §5).

## 4. Candidate ladders — net-after-fee (fees charged), same trades

All candidates hold SL params fixed (risk unchanged) and vary only the TP legs.
`fills(0/1/2/3)` = trades whose deepest filled leg was none(stopped)/TP1/TP2/TP3.

### Set (a) — 19 post-fix taken trades (recorded-validated)
| candidate | N | sk | W/L | gross | **net** | net cum | %net+ | feeR | fills 0/1/2/3 |
|---|---|---|---|---|---|---|---|---|---|
| baseline 0.5/1.0/2.5 @25/50/25 | 19 | 0 | 15/4 | +0.110 | −0.204 | −3.88 | 32% | 0.314 | 4/9/6/0 |
| **H1** heavy-TP1 @40/40/20 | 19 | 0 | 15/4 | +0.146 | −0.168 | −3.19 | 37% | 0.314 | 4/9/6/0 |
| **H2** pull-in 0.5/0.8/1.3 | 14 | **5** | 11/3 | +0.149 | −0.128 | −1.79 | 43% | 0.277 | 3/5/4/2 |
| H1+H2 0.5/0.8/1.3 @40/40/20 | 14 | **5** | 11/3 | +0.166 | −0.111 | −1.55 | 43% | 0.277 | 3/5/4/2 |
| 2-leg drop-TP3 0.5/1.0 @40/60 | 19 | 0 | 15/4 | +0.166 | −0.149 | −2.82 | 37% | 0.314 | 4/9/6/0 |
| **2-leg pulled 0.5/0.9 @50/50** | 18 | 1 | 15/3 | +0.289 | **−0.016** | −0.30 | 67% | 0.305 | 3/7/8/0 |
| far-TP1 0.8/1.3/2.5 | 19 | 0 | 12/7 | −0.107 | −0.422 | −8.01 | 21% | 0.314 | 7/9/3/0 |
| **single-tgt feefloor @100%** | 19 | 0 | 15/4 | +0.290 | **−0.024** | −0.46 | 79% | 0.314 | 4/15/0/0 |
| single-tgt 1.0R @100% | 19 | 0 | 12/7 | +0.263 | −0.051 | −0.97 | 63% | 0.314 | 7/12/0/0 |

### Set (b) — 192 silence-window vol-zeroed (reconstructed, same regime)
| candidate | N | sk | W/L | gross | **net** | net cum | %net+ | fills 0/1/2/3 |
|---|---|---|---|---|---|---|---|---|
| baseline @25/50/25 | 192 | 16 | 129/63 | +0.146 | **−0.120** | −23.12 | 51% | 63/31/74/24 |
| H1 @40/40/20 | 192 | 16 | 129/63 | +0.129 | −0.138 | −26.43 | 57% | 63/31/74/24 |
| H2 0.5/0.8/1.3 | 175 | 33 | 119/56 | +0.103 | −0.147 | −25.76 | 53% | 56/27/48/44 |
| H1+H2 | 175 | 33 | 119/56 | +0.093 | −0.157 | −27.44 | 59% | 56/27/48/44 |
| 2-leg drop-TP3 @40/60 | 192 | 16 | 129/63 | +0.116 | −0.150 | −28.82 | 57% | 63/31/98/0 |
| 2-leg pulled @50/50 | 186 | 22 | 124/62 | +0.078 | **−0.181** | −33.70 | 63% | 62/28/96/0 |
| far-TP1 0.8/1.3 | 200 | 8 | 118/82 | +0.145 | −0.133 | −26.51 | 48% | 82/29/59/30 |
| **single-tgt feefloor @100%** | 205 | 3 | 135/70 | +0.065 | **−0.227** | −46.59 | 66% | 70/135/0/0 |
| single-tgt 1.0R @100% | 205 | 3 | 118/87 | +0.165 | −0.127 | −26.05 | 58% | 87/118/0/0 |

### Set (a)+(b) — combined, 211 walked (the load-bearing table)
| candidate | N | sk | W/L | gross | **net** | net cum | %net+ | fills 0/1/2/3 |
|---|---|---|---|---|---|---|---|---|
| **baseline @25/50/25** | 211 | 16 | 144/67 | +0.143 | **−0.128** | −26.99 | 49% | 67/40/80/24 |
| H1 @40/40/20 | 211 | 16 | 144/67 | +0.130 | −0.140 | −29.63 | 55% | 67/40/80/24 |
| H2 0.5/0.8/1.3 | 189 | 38 | 130/59 | +0.106 | −0.146 | −27.55 | 52% | 59/32/52/46 |
| H1+H2 | 189 | 38 | 130/59 | +0.099 | −0.153 | −28.99 | 58% | 59/32/52/46 |
| 2-leg drop-TP3 @40/60 | 211 | 16 | 144/67 | +0.121 | −0.150 | −31.65 | 55% | 67/40/104/0 |
| 2-leg pulled @50/50 | 204 | 23 | 139/65 | +0.097 | −0.167 | −34.00 | 64% | 65/35/104/0 |
| far-TP1 0.8/1.3 | 219 | 8 | 130/89 | +0.123 | −0.158 | −34.53 | 46% | 89/38/62/30 |
| single-tgt feefloor @100% | 224 | 3 | 150/74 | +0.084 | −0.210 | −47.05 | 67% | 74/150/0/0 |
| **single-tgt 1.0R @100%** | 224 | 3 | 130/94 | +0.173 | **−0.121** | −27.02 | 58% | 94/130/0/0 |

**Every candidate on every set is net-negative.** On combined, the field spans only −0.121 to −0.210;
least-bad are baseline (−0.128) and single-tgt-1.0R (−0.121). **No ladder crosses zero.**

### 4.1 The overfit inversion (read this before believing any set(a) number)
The two ladders that look *best* on set(a) — `single-tgt feefloor` (net −0.024) and `2-leg pulled`
(−0.016) — are the *worst* and near-worst on set(b) (−0.227, −0.181). Reason: set(a) had **zero**
runners, so capping everything at the fee floor looked free; set(b) had **24 TP3 + 74 TP2 fills**, so
the same cap throws away the runners that pay. **This is a direct empirical demonstration that
optimizing the ladder to the 19 in-sample trades selects the worst out-of-sample ladder.** Any "winner"
must be judged on mechanism, not the in-sample table.

- **Operator H1 (heavier TP1 ~40%):** combined net −0.140, *worse* than baseline (−0.128). Helps the
  no-runner set(a), hurts the with-runner set(b). **Not supported.**
- **Operator H2 (pull TP2/TP3 in):** combined net −0.146 *and* skips 38 trades — pulling TP2 to 0.8R
  collides with the fee-floored TP1 (`fees_too_high_for_risk`), so it fails to clear fees *and* shrinks
  the tradeable set. **Not supported.**
- **far-TP1 (TP1 → 0.8R):** clearly worst-direction (−0.158, +3 extra stops) — pushing TP1 past the
  reversal makes it miss. **TP1 must sit at the fee floor where price reliably reaches** (the one robust
  structural truth, but it's already what the baseline does).

## 5. Robustness / outlier-dependence (combined set)

The negative result is **pervasive, not outlier-driven** — dropping the single best/worst/both trades
barely moves net avg:

| candidate | N | net avg | drop-worst | drop-best | drop-both | net-R dist (min·med·max) | losers |
|---|---|---|---|---|---|---|---|
| H1+H2 | 189 | −0.153 | −0.147 | −0.158 | −0.152 | −1.40 · +0.31 · +0.79 | 80/189 (Σ −75.4) |
| 2-leg drop-TP3 | 211 | −0.150 | −0.144 | −0.154 | −0.148 | −1.49 · +0.07 · +0.75 | 95/211 (Σ −87.2) |
| 2-leg pulled | 204 | −0.167 | −0.160 | −0.171 | −0.165 | −1.43 · +0.32 · +0.69 | 74/204 (Σ −82.9) |
| single-tgt feefloor | 224 | −0.210 | −0.202 | −0.217 | −0.210 | −1.89 · +0.27 · +1.37 | 74/224 (Σ −97.2) |

The shape is consistent: a **positive median** (small, reliable TP wins) and a **−1.2 to −1.9 R loss
tail** (full stop + fee) whose mass outweighs the wins. No single trade or pair rescues any candidate.

## 6. Fee sensitivity — is the lever the LADDER or the FEE TIER? (combined set)

Holding each ladder fixed, recomputing net under maker-exit fees (post-only TP limits → exit fee
0.04%→0.014%, round-trip 0.09%→0.064%):

| ladder | gross (= net @ 0% fee) | net @ 0.064% (maker exits) | net @ 0.09% (taker, current) |
|---|---|---|---|
| baseline 25/50/25 | +0.143 | −0.050 | −0.128 |
| single-tgt 1.0R | **+0.173** | **−0.036** | −0.121 |
| 2-leg pulled | +0.097 | −0.090 | −0.167 |
| H1+H2 | +0.099 | −0.081 | −0.153 |

**Even with maker exits, the best ladder is net −0.036 R — still negative.** The raw gross edge is only
**+0.14 to +0.17 R**; taker fees cost ~0.27 R, maker exits ~0.19 R. **The binding constraint is
gross-edge-vs-fees, which no TP-ladder change addresses.** The real levers live *outside* the ladder:
the gross edge (entry-signal quality / stop placement) and the fee tier (maker exits + VIP tier) — both
out of scope here, and even *together* they only approach breakeven in this regime. (Structural note:
`feeR = fee/risk` falls as stop width grows, so wider stops would cut fee-R — but that is an SL/entry
change, out of scope, and trades against R:R and win-rate.)

## 7. FINDING + recommendation

**NULL FINDING (robust): no scalp TP ladder clears fees on this entry signal at the 0.09% taker tier in
the ~3–4% ATR regime.** Validated walk, 211 trades, 9 structurally-diverse ladders + both operator
hypotheses — all net −0.12 to −0.21 R. The gross edge (~0.14–0.17 R) is structurally below the
ladder-invariant fee floor (~0.27 R).

**Recommendation — do NOT re-ladder; no §4 TP-parameter change is indicated.**
1. The current baseline ladder is already near the achievable net frontier (combined −0.128 vs best
   −0.121). Re-laddering moves net within a −0.12…−0.21 band and never crosses zero.
2. Both operator hypotheses are unsupported: H1 (heavier TP1) is a wash-to-worse; H2 (pull-in) is worse
   *and* drops trades. The set(a) "near-breakeven" ladders are overfit artifacts (§4.1).
3. The live-flip economics question this was meant to inform has a cleaner answer than "fix the ladder":
   **the strategy's gross edge in this regime is too thin to clear its fees, ladder notwithstanding.**
   The flip decision should turn on gross-edge improvement and fee-tier (maker exits / VIP), not TP
   geometry. **Would this hold for a scalp I'd never seen? Yes — it's a fee-floor identity, not a fit.**

This does not condemn the strategy outright: it is gross-positive (+0.14 R), and the analysis is
single-regime (~4% ATR). A different regime (lower ATR, tr+ different MFE profile, or genuinely
trending tape) could change the gross edge and the runner economics. That is exactly what §8 must test.

## 8. §4 Backtester-validation plan (per CLAUDE.md §4 — required before ANY param change)

Because no ladder change is recommended, §4 is framed as **validating the null and the binding
constraint**, not approving a parameter. A change would only be revisited if §4 *falsifies* the null.

1. **Regime span (the #1 gap).** All data here is single-regime (1D ATR 3.28–4.25%). Backtest the
   baseline ladder + single-tgt-1.0R + 2-leg-drop-TP3 across **≥3 distinct BTC regimes**: low-vol
   (<2% ATR), the unlocked 3–5% band, and a strong-trend stretch — on ≥6 months of bars. **Success
   criterion to overturn the null:** any ladder shows net-after-fee > 0 with 95% bootstrap CI excluding
   0 in ≥2 regimes. **Falsifier of *this* report:** if a low-ATR regime yields net-positive at the same
   fee, the null is regime-specific (still true here, but the flip could wait for that regime).
2. **Sample size.** Target ≥150 trades per regime (this study: 211 across one regime). Report net-R
   bootstrap distribution, not point means.
3. **Fee model as a first-class axis.** Run every candidate at {taker 0.09%, maker-exit 0.064%, and the
   account's *actual* realized fills}. The maker-exit path (−0.036 R best here) is the most promising
   non-ladder lever — confirm whether post-only TP fills are achievable without adverse selection.
4. **Intrabar fidelity.** Re-walk on **1m** bars (this study: 3m, ambiguity 2/211 — negligible but
   unverified out-of-sample) to confirm the BE-ratchet vs SL ordering does not flip the sign.
5. **What would change the recommendation:** a regime/fee combination where a specific ladder's net CI
   excludes 0 → then (and only then) bring that exact ladder to §4 as a parameter-change proposal.

## 9. Caveats (stated prominently — small-N, single-regime, in-sample)

- **SMALL-N & unstable.** Set(a) gross expectancy moved **+0.175 → +0.110 R on just 2 new trades** —
  the headline is not stable at N≈19. Combined N=211 is more stable but still one regime.
- **SINGLE-REGIME.** Both sets are 1D ATR 3.28–4.25%. **Conclusions do not transfer to other
  volatility regimes** — the runner economics (TP3 0/19 in set a vs 24× in set b) are regime-driven.
- **IN-SAMPLE.** Every number is fit-free *for the null* but in-sample *for any positive*; §4.1 shows the
  in-sample optimum inverts out-of-sample. **Frame every candidate as "to validate," never "ship."**
- **RECONSTRUCTION (set b).** 192 of 208 vol-zeroed signals rebuilt via the deep-dive-validated method
  (V1 ✓); 16 skipped by `build_trade_plan`; no recorded outcomes to validate against (unlike set a).
- **INTRABAR (P3 `70d50f7`).** TP-vs-advanced-SL same-bar ambiguity affects 2/211 trades at 3m → does
  not move the aggregate; unverified at 1m out-of-sample (§8.4).
- **`max_hold` not modeled.** No set(a) trade exited on time (all TP/SL), consistent with the walk;
  out-of-regime tapes may differ.

## Appendix — reproducibility
- `tpdata.sh` — read-only `sqlite3 -readonly` CSV pull (bars 06-01→now, `VAL_TAKEN`, `PTR_OUTCOMES`,
  `SIL_SCORE`, `SIL_HTF`) → `tpdata.out`. Stream:
  `Get-Content tpdata.sh -Raw | ssh azureuser@trading.jacksumner.com "tr -d '\357\273\277\r'|bash" | Out-File -Encoding utf8 tpdata.out`
- `tpcal.py` — recalibration harness (imports the strategy's own `build_trade_plan`; reuses q3harness
  walk mechanics; V1+VWALK gates; 9 candidates; robustness + fee sensitivity). Run: `py tpcal.py`.
- `q3harness.py` — reused bar-walk engine (deep-dive `fec53ec`), retained for lineage.
- Proximity gate threshold `proximity_block_pct = 0.3%` confirmed in `bitunix_htf_regime.py:221,243,993`.
