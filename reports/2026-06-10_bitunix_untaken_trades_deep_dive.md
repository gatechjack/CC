# Bitunix Post-Fix Deep-Dive — Untaken Trades + Numbers Audit

**Date:** 2026-06-10 · **Session:** operator-supervised, read-only, agent-driven SSH (policy `82fda13`)
**Branch:** `bitunix-untaken-trades-deep-dive-2026-06-10` (dedicated worktree; unmerged)
**Window:** 2026-06-09 03:49:41 UTC (fix deploy) → 2026-06-10 13:54 UTC (capture; ~34 h)
**Builds on:** `reports/2026-06-10_bitunix_day2_expanded_review.md` (Day-2, taken-trades)

> **STATUS: COMPLETE.** Q3 used the operator's granularity-escalation method; **0 of 19 walked
> rejects needed 1m escalation or a Tier-3 assumption** — gate verdicts are fully observed at 3m.
> Both surviving gates earn their keep. Numbers audit CLEAN. Fee finding remains the headline.

> **Headline (numbers audit): the recorded numbers are CLEAN** (every R/PnL reproduces from
> raw prices), **but paper P&L is fee-free, and at the strategy's own assumed 0.09% round-trip
> fee the gross +0.175R/trade expectancy flips to roughly −0.13R/trade net.** The remaining
> gates' value is the open Q3 question.

---

## 0. Scope, constraints, hard stops

- Read-only throughout; agent-driven SSH (read-only `sqlite3 -readonly`, file reads) per CLAUDE.md `82fda13`.
- **Out of scope:** changing any gate; the staged-P1 silence-window backtest + TP-structure deep work (post-Day-5); Polymarket; the Day-5 close-out.
- **Hard stops:** prod write → stop; **Q4 recorded-vs-recomputed discrepancy → stop** (NOT triggered — audit clean); `execution_mode ≠ paper` → stop (NOT triggered — `paper` confirmed).

## 1. State verification
- `origin/main` = HEAD = `82fda13` (≥ expected). Dedicated worktree created (was in main checkout — `--worktree` quirk again; new worktree made before any write).
- SSH probe OK: `tc-prod-vm`, 2026-06-10T13:45:20Z. `execution_mode: paper` (`strategies.yaml:1022`). Live-mode primitives since window: **0**.

---

## Q1 — Decision funnel (full window)

`bitunix_score_decided` is emitted once per signal at its exit gate. The funnel reconciles
**exactly** against the stage-specific event counts (each stage's event count = signals that
reached it):

| Stage | Pass count | Died here | Cross-check |
|---|---|---|---|
| Signals scored | **526** | — | `bitunix_score_decided` = 526 ✓ |
| Pass score gate | 365 | `skipped_score` 161 | — |
| Pass cooldown | **324** | `skipped_cooldown` 41 | `pa_validation_decision` = 324 ✓ |
| Pass PA validation | **44** | `skipped_pa_validation` 280 | `htf_gate_decision` = 44 ✓ |
| Pass HTF gate | **20** | `skipped_htf_gate` 24 | `trade_plan_decision` = 20 ✓ |
| Pass trade-plan build | **17** | `skipped_trade_plan` 3 | `placed` = 17 = trade ledger ✓ |

**Top of funnel = 526 signals; bottom = 17 fires (3.2%).** The two biggest filters are **PA
validation (280, 53%)** and the **score gate (161, 31%)**. The HTF directional/structure gate
(the focus of Q2/Q3) kills 24. Every stage's independent event count matches the funnel
arithmetic — the pipeline accounting is internally consistent.

*(Minor: `bitunix_observer_classified` = 16 vs 17 fires — a 1-count lag, almost certainly the
newest fire `fb531800` 11:07Z emitting its classify event near the capture boundary. The trade
ledger itself has all 17 rows and all recompute clean (Q4); not a data discrepancy.)*

---

## Q2 — Untaken-trade inventory

**Upstream gates (counted, not individually walked — by-design weak or pre-plan):**
PA-validation 280 · score 161 · cooldown 41.

**HTF-gate rejections — 24 (the load-bearing set; all carry side + `trigger_price`):**

| Reason | 06-09 | 06-10 | Total |
|---|---|---|---|
| `proximity_to_support` | 7 | 13 | **20** |
| `regime_forbids_side` | 2 | 2 | **4** |

- **`proximity_to_support` (20):** all `sell` signals where price sits within ~0.3% of support
  (`distance_to_support_pct` as low as 0.004–0.07%). The gate blocks shorting into nearby
  support (bounce risk).
- **`regime_forbids_side` (4):** all `buy` signals in a bear regime — counter-trend longs
  vetoed by the directional gate.
- **Dedup note:** several are same-bar repeats of one setup (e.g. `06-10T09:00:02/05/06/18`
  — 4 within 16s; `06-09T13:23:29` ×2). Q3 must de-dup so one setup isn't counted as 4 trades.

Full 24-row detail (ts, side, reason, `atr_pct_d1`, dist-to-S/R) in the appendix.

---

## Q3 — Counterfactual walk of rejected signals (do the gates earn their keep?) — YES, both

**Method (operator-chosen granularity escalation; harness `q3harness.py`).** Each reject's plan
was reconstructed via the strategy's OWN `build_trade_plan` / `get_recent_swing` /
`get_htf_levels` (imported, not re-derived) with ATR-14 from the real `LiveBarCache.get_atr`
(60-bar 3m window). Forward walk on 3m bars mirrors `_classify_v2_multi_leg` (SL-first worst-case
tie, ordered TP fills, BE-after-TP1 / TP1-after-TP2 ratchet). A 3m bar spanning BOTH the active
SL and an unfilled TP = *ambiguous* → escalate to 1m (Tier 2) → conservative floor (Tier 3).

**Reconstruction validated against the 20 stored `trade_plan_decision` inputs (plan-mismatch guard):**
V1 `build_trade_plan`(stored inputs)==stored plan **18/18** ✓ · V2 my ATR≈stored `atr_used` (<5%)
**19/21** ✓ · V3 swing **42/42** ✓ · V4 HTF S/R **35/38** ✓ → premise holds, no STOP.

**Accounting (the trust metric) — 24 raw → 20 unique setups (4 same-bar dups removed):**

| Bucket | n |
|---|---|
| Walked, **Tier-1 clean (0 ambiguous 3m bars)** | **19** |
| Tier-2 (needed 1m escalation) | **0** |
| **Tier-3 (intrabar *assumed*)** | **0** |
| Plan-would-skip (gate moot — `fees_too_high_for_risk`) | 1 |
| Still-open (ran past available bars) | 0 |

**Zero setups fell to assumption.** Every walkable reject resolved unambiguously on 3m bars (no
losing bar also touched a TP), so **no 1m fetch was required** and the verdict is **fully observed
at 3m granularity, not assumption-dominated.** (No public-API 1m GETs occurred.)

**Per-gate counterfactual — NET first (fee lens), gross secondary:**

| Gate | walked | W/L | gross avg R | gross cum R | **net avg R** | **net cum R** | Verdict |
|---|---|---|---|---|---|---|---|
| `proximity_to_support` | 15 | 6/9 | −0.238 | −3.57 | **−0.596** | **−8.94** | **Earns its keep** — rejects gross- *and* net-losers |
| `regime_forbids_side` | 4 | 2/2 | −0.036 | −0.14 | **−0.431** | **−1.73** | **Earns its keep (net)** — gross ≈ neutral, net-negative |

- **`proximity_to_support` (n=15):** trades it blocked would have lost **gross** (−0.238R avg) and
  clearly **net** (−0.596R). Shorting into nearby support is bounce-prone; the gate filters
  losers. Robust — gross & net agree, 0 assumptions.
- **`regime_forbids_side` (n=4, small):** blocked counter-trend longs were ~**gross-neutral**
  (−0.036R) but **net-negative** (−0.431R) — a **gross/net divergence**: marginal on gross,
  earns its keep on the net basis that matters live. Directional only (n=4).

Net = gross − round-trip fee drag (`FeeConfig` 0.09%; `feeR = 0.09%·entry/risk`). Structural note:
the TP1 fee-floor makes a TP1+TP2 win net **exactly +0.5R** while losses net ≈ −1.4R, so gates
that reject mostly-losers are net-accretive. **No gate is destroying expectancy → no tuning indicated.**

---

## Q4 — Numbers audit on the taken trades — **CLEAN**

**17 fires** (Day-2 had 16; `fb531800` 11:07Z is a new win), **14 W / 3 L**, all `sell`.

**Independent recomputation reproduces every recorded figure (≤ float noise):**
- **3 losses** — `result_price` == original `stop_price` exactly → `actual_r_multiple` = −1.0 exactly. ✓
- **Wins** — `Σ(target_r×frac for filled legs) + r_at_exit×unfilled_frac` matches recorded R to 4 dp.
  Examples: `cf40deeb` 0.805·0.25 + 1.0·0.5 + 0.805·0.25 = **0.9025** (rec 0.9024);
  `c6adb85c` 0.507·0.25 + 0.575·0.5 + 0.507·0.25 = **0.5410** (rec 0.541);
  `171d7a46` 0.804·0.25 + 0·0.75 = **0.201** (rec 0.201).
- **PnL** — `expected_gain·(R/tp_r_multiple)` reproduces each `actual_pnl_dollars`; re-summed cumulative = **+$1.00** (gross).
- `tp_r_multiple` is the correct blended Σ(frac·target_r) (1.04–1.37, floating with TP1's fee-floored target). ✓
- `atr_source` = `live_atr_14` on all 17 (no fallback estimates). `execution_mode` blank (paper).

**→ No recorded-vs-recomputed discrepancy. Hard-stop NOT triggered.**

### Q4 fee finding (material) — P&L is fee-free; net expectancy is negative
Paper fills carry `fee = 0.0` (`PaperBroker` never sets it) and the replay never subtracts
fees — so **all recorded R/PnL are gross**. Using the strategy's *own* `FeeConfig`
(taker 0.04% + 0.005% slippage/side, taker exits → **round-trip ≈ 0.09%**):

- Fee drag per trade ≈ round-trip% ÷ stop-distance%. Stops average ~0.29% of price → **≈ 0.31R/trade**.
- Gross expectancy **+0.175R/trade** → net ≈ **−0.13R/trade** (14 wins ≈ +1.63R net, 3 losses ≈ −3.93R net, ÷17).
- The TP1 fee-floor protects the TP1 leg, but the ~0.75 of each position exiting at break-even still pays fees → the many TP1-only "wins" (~0.13–0.20R gross) are **likely net-negative live**.

Estimate (strategy's assumed rate; real fees could be higher → worse). **This is the central
live-flip caveat and sharpens the staged-P1 TP-structure question: gross-profitable, net-negative as-is.**

---

## Q5 — Distribution sanity

- **Side:** **17/17 `sell`** — strategy is short-only across the window, consistent with the
  bearish backdrop (BTC ~63.3k → ~61.1k). The 4 `regime_forbids_side` rejects were all `buy`
  signals correctly vetoed. Directional gate is enforcing trend alignment.
- **Time-of-day (fires):** spread across 14 distinct UTC hours (00,02–05,09,11,13,18–23) — **no
  single-session clustering.** Losses fell at 13:39, 18:33, 20:21Z.
- **Signal arrivals:** all 24 hours populated (heaviest 01Z=52, 05Z=45, 07Z=41; lightest 03/15Z=5)
  — continuous 24/7 flow.
- **Gap analysis:** **zero gaps > 2h** in score-decisions → no upstream TradingView silence;
  every rejection is a *gate* decision, not signal absence.
- **Losses vs wins (structure):** the 3 losses span the full stop-width range (0.187% `c51a18c5`
  → 0.495% `a7a84015`) and 3 different hours — **no shared structural tell** beyond being shorts
  the market moved against (price ticked up to the stop before any TP). Not a distinct regime/hour cluster.

---

## Synthesis (interim)

**(b) Do the recorded numbers withstand independent recomputation? — YES, fully.** Every R and
PnL reproduces from raw entry/SL/exit/legs; the 3 losses are exact −1.0; the funnel reconciles
stage-by-stage. The one caveat is interpretive, not integrity: **P&L is gross/fee-free**, and at
the strategy's own 0.09% round-trip the **net expectancy is ≈ −0.13R/trade**.

**(a) Are the remaining gates earning their keep? — YES, both** (fully-observed Q3, 0 Tier-3
assumptions). `proximity_to_support` rejected trades that lose gross (−0.24R) *and* net (−0.60R);
`regime_forbids_side` rejected gross-neutral / net-negative (−0.43R) trades. Neither destroys
expectancy — both filter (net-)losers. No gate-tuning indicated (and gate changes are downstream
of the staged P1 + Day-5 anyway).

**(c) Anything askew worth a BACKLOG filing?**
1. **Fee-vs-expectancy (recommend filing / sharpening staged-P1):** gross +0.175R → net −0.13R
   after assumed fees. The live-flip should not proceed on gross paper numbers; TP-structure
   tuning and/or fee assumptions need resolving first. Sharpens the existing P1.
2. **No gate-tuning item:** Q3 found neither surviving gate destroys expectancy — both reject
   (net-)losers — so no BACKLOG filing for the gates (and gate changes are downstream of the
   staged P1 / Day-5 regardless).
3. No data-integrity issues found.

---

## Appendix — reproducibility
- Q3 counterfactual engine committed alongside this report: **`q3harness.py`** (imports the
  strategy's own `build_trade_plan`/`get_recent_swing`/`get_htf_levels`; validation-first; tiered
  3m→1m→conservative walk). Run: `run_capped.ps1 <python> q3harness.py` from the worktree, reading
  `qdata.out` (regenerate via `qdata.sh` — read-only `sqlite3 -readonly` CSV pull of 3m bars +
  rejects + the 20 `trade_plan_decision` validation rows).
- Probe scripts `s7.sh`/`qprobe.sh`/`qdata.sh` (repo root) — read-only; retained, not committed.
- Q4 raw 17-trade numbers + 24 HTF-reject detail captured in `s7.sh` output (this session's log).
