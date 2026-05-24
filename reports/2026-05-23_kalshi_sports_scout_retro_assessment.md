# Kalshi Sports Scout — Retro EV-at-Fill Assessment

**Date:** 2026-05-23
**Author:** Session work — Phase 0 of Kalshi Sports Arbitrage division. Distinct from the existing scout's own Phase-0 review (commit 7054bff `reports/2026-05-23_kalshi_sports_scout_phase0_review.md`), which analyzed the same 461-row corpus at divergence-in-percentage-points and laid out the scout's three-way gate (full / scope-down / shelve) for the Board's decision. **This doc adds the EV-at-fill layer that the scout review explicitly defers** ("EV-at-fill + fees + fillability layered on" — 7054bff commit message), and frames the result for whether the new Kalshi Sports Arbitrage division should reuse the corpus or run a fresh observer.
**Scope:** Read-only mining of the existing `kalshi_sports_scout` 9-day corpus to determine how much of the Kalshi Sports Arbitrage Phase 0 verdict can be answered without a fresh observer collection.
**Status of corpus:** 461 `kalshi_sports_observed` rows from 2026-05-16T06:16 UTC → 2026-05-23T19:54 UTC. Max payload 538 bytes (no truncation concern). Read in full via `az vm run-command` against prod (`rg-shared-prod` / `tc-prod-vm`).

---

## TL;DR

- **Math validated.** EV-at-fill hand-verification passed to the cent on the NBA max-EV row. The `kalshi_fee` formula + `compute_ev_at_fill_b_directional` agree with SQL aggregation and pencil-and-paper. Systematic bug class (fee direction, fill side, units) ruled out for the new observer's math layer.
- **Recoverability is partial.** The scout's 100× units bug at `scout.py:232-240` is **deterministically reversible** (`true_implied = stored × 100`); 459/461 rows recoverable after the reversal. But the scout's payload does NOT store raw `yes_ask`/`no_ask`/`yes_bid`/`no_bid` nor per-book breakdown — only the median bookmaker-implied across N books. Spreads/totals are not covered at all (h2h only).
- **Hypothesis A (cross-venue arb) is uncomputable from this corpus.** A-arb needs the opposing-side price at a specific book, not a median. New observer must collect per-book.
- **Hypothesis B (lead-lag) is computable BUT only against a soft-book proxy** (median DK/FD/BetMGM-class) — NOT Pinnacle. Aggregate result on the soft-book proxy: mean EV negative across every league; 34/459 (7.4%) of rows positive-EV at $10 sizing. NBA-only is too thin (n=24 over 2 days) for a verdict.
- **Recommended action:** Skip a fresh 5–7 day B-only collection at $10 sizing — the scout corpus already says the soft-book B test is null on aggregate, and adding a few more days will not change that. Run the fresh observer for what only it can give: **per-book breakdown for Hypothesis A**, **raw Kalshi bid/ask sides** (so we don't reinvent the same units-bug class), and **spreads/totals coverage**. NBA-only window is small (~5-7 game days remaining in playoffs); expect INCONCLUSIVE on Hypothesis B for NBA regardless of fresh collection unless the new observer adds Pinnacle.

---

## Corpus inventory

| League | n_observed | First ts (UTC) | Last ts (UTC) | n_recoverable (post bug-reversal) |
|---|---|---|---|---|
| NFL | 210 | 2026-05-17 20:21 | 2026-05-22 05:12 | 210 |
| MLS | 113 | 2026-05-16 12:18 | 2026-05-23 19:54 | 113 |
| MLB | 96 | 2026-05-17 18:20 | 2026-05-22 04:12 | 96 |
| NBA | 24 | 2026-05-20 19:45 | 2026-05-21 11:13 | 24 |
| NHL | 18 | 2026-05-16 06:16 | 2026-05-23 16:53 | 18 |
| **TOTAL** | **461** | — | — | **461** (all reversed values fell in (0,1); 0 unrecoverable) |

**NBA cap is tight: 24 rows spanning 40 hours.** Playoff calendar — only Conference Finals games during this window, with off-days. The fresh observer's 5–7 game-day plan will hit similarly thin volume because the NBA Finals haven't started yet; expect ~ low-double-digits of NBA fresh observations.

Adjacent counts (for context, not analyzed here): `kalshi_sports_scout_scan` 188 cycles; `kalshi_sports_scout_unmapped` 1960 rows (per [[project-kalshi-sports-scout-phase0-blocked]]: 95.8% are `ticker_parse_fail_or_unsupported_league` — Kalshi ticker grammar issue, not the 155-team mapping).

---

## Field-level recoverability

Scout payload structure (verified on 3 most-recent rows, no truncation):

| Field | Present? | Recoverable for our EV-at-fill? | Notes |
|---|---|---|---|
| `bookmaker_yes_implied` | yes | YES (correct, vig-removed median) | Drives Hypothesis B `model_prob_outcome` as **soft-book proxy** |
| `kalshi_implied_yes` | yes | YES via `× 100` reversal | Corrupted by 100× bug; deterministic fix |
| `n_books` | yes | yes (informational) | Median taken across N books; tells us how many |
| `median_vig_pct` | yes | yes (informational) | Higher vig → larger soft-book vs sharp-book gap |
| `divergence_pct` | yes | DERIVED — discard, recompute from raw | Bug-poisoned; recomputable from corrected fields |
| `would_fire_buy` | yes | DERIVED — discard | 461/461 = "yes" due to bug; meaningless |
| `expected_expiration_time` | yes | yes (informational) | For game-time vs observation-time analysis |
| `commenced_at`, `league`, ticker/team metadata | yes | yes | For matching key + slicing |
| **Raw `yes_ask`** | **NO** | — | Future observer MUST store this |
| **Raw `no_ask`, `yes_bid`, `no_bid`** | **NO** | — | Future observer MUST store all sides |
| **Per-book breakdown (DK/FD/BetMGM/etc)** | **NO** | — | Required for Hypothesis A; future observer MUST capture |
| **Pinnacle-specific quote** | **NO** | — | the-odds-api free-tier coverage unknown; probe needed |
| **Spread / total markets** | **NO** | — | Scout is h2h only; future observer must add |

---

## Hand-verification gate (HARD GATE — passed)

Per the Phase 0 plan and user discipline standard: one sampled row must be hand-recomputed to the cent before any aggregate is trusted.

**Sampled row:** NBA max-EV row at $10 sizing.

- ts: 2026-05-20T19:45:06+00:00
- Ticker: `KXNBAGAME-26MAY22OKCSAS-OKC` (OKC Thunder home vs SAS Spurs, game tip 2026-05-22T00:40 UTC — ~28h forward)
- Stored `kalshi_implied_yes`: 0.0049 → reversed: **$0.49** Kalshi YES ask
- `bookmaker_yes_implied`: 0.6818 (vig-removed median across n_books=9; vig 4.145%)

**Hand math:**
- `kalshi_fee(10, 0.49)` = `ceil(0.07 × 10 × 0.49 × 0.51 × 100) / 100` = `ceil(17.493) / 100` = **$0.18**
- Cost: 10 × $0.49 + $0.18 = **$5.08**
- Expected payoff: 10 × 0.6818 = **$6.818**
- **EV = $6.818 − $5.08 = $1.738**

**Cross-checks:**
- SQL aggregate `ev_at_10` for this row: **$1.738** ✓
- Python `LegFill("kalshi","yes",qty=10,price_per_unit=0.49,fee=0.18)` → `cost_total = 5.08`; `compute_ev_at_fill_b_directional(..., model_prob_outcome=0.6818)` → `ev_dollars = 1.738` ✓

**Conclusion:** systematic bug class (units, fee direction, fill side, decimal-vs-American odds confusion) ruled out at the math-module level. Safe to trust the aggregate numbers in the next section AS COMPUTATIONS. Whether those computations represent a real edge is a separate question — addressed below.

**Plausibility caveat on this row.** The +$1.738 max EV at $10 is at 28h pre-tip in a thin pre-game window. A 19pp Kalshi-vs-book divergence at that horizon is consistent with low Kalshi liquidity / wide spread / stale price, NOT necessarily a real lead-lag edge. Fresh observer must capture raw bid/ask + multiple intra-day snapshots per game to distinguish "real edge at fillable price" from "stale ask in a 5-share market."

---

## Aggregate B-leadlag EV-at-fill (soft-book proxy)

At qty=10 contracts:

| League | n_rec | mean EV ($) | min EV ($) | max EV ($) | n positive | % positive |
|---|---:|---:|---:|---:|---:|---:|
| NFL | 210 | −0.4731 | −1.82 | +0.226 | 5 | 2.4% |
| MLS | 113 | −0.2536 | −0.813 | +0.041 | 4 | 3.5% |
| MLB | 96 | −0.4036 | −2.557 | +0.917 | 16 | 16.7% |
| NBA | 24 | −0.2942 | −2.398 | +1.738 | 7 | 29.2% |
| NHL | 18 | −0.4011 | −2.698 | +0.290 | 2 | 11.1% |
| **TOTAL** | **461** | **−0.418** (weighted) | — | +1.738 | **34** | **7.4%** |

At qty=25 contracts: positives count is identical (sign doesn't change with linear sizing modulo discrete fee rounding — verified manually), but absolute EV scales ~2.5× with sizing (matches expectation since fee scales sub-linearly per-row).

**Interpretation:**

1. **Aggregate is negative-EV across every league.** Expected for a soft-book median proxy — vig-removed median of competitive US books is approximately efficient, so betting Kalshi against it should be roughly break-even before fees, and Kalshi fees push it slightly negative on average. This is the predictive null result for a B-leadlag instrument tested against a non-sharp proxy.

2. **NFL is unusually efficient (2.4% positive).** 210 rows during off-season window — most NFL futures (championship, division winner) are deeply illiquid on both Kalshi and books; few divergences large enough to overcome a $0.18-$0.20 Kalshi fee shell.

3. **NBA shows the highest tail (max +$1.74) and highest positive rate (29%).** With n=24 this is noisy, BUT consistent with the hypothesis that thinner-liquidity pre-game windows on Kalshi produce wider book-vs-Kalshi gaps that LOOK like edge but may not be fillable. Cannot distinguish "real edge" from "thin-book artifact" from this corpus alone — the absence of raw bid/ask and trade volume in the payload is the binding constraint.

4. **MLB shows 16/96 positive (16.7%) — second-highest hit rate.** Daily MLB schedule + active Kalshi MLB liquidity make this the most plausible category for a non-artifact B signal at hour-scale. **For a future inquiry that wants to test B-leadlag for real, MLB is the better starting point than NBA**, especially given NBA's playoff calendar will end before the Phase 0 window completes.

---

## What the new observer MUST add (cannot be retro-derived)

Captured into the Phase 0 plan's Step 4 observer spec:

1. **Per-book breakdown** — for Hypothesis A. Median is useless for arb; need each book's offering price for the opposing leg.
2. **Raw Kalshi bid/ask on BOTH sides** (`yes_bid, yes_ask, no_bid, no_ask`) — store raw in dollars; do NOT pre-compute implied (avoids the scout's class of units bug). Add a sanity-guard `kalshi_quote_invalid` flag if `yes_ask + no_ask` outside [0.5, 1.5].
3. **Pinnacle-specific quote where available** — for a real B test, not just soft-book proxy. Need the `scripts/probe_odds_api_pinnacle_nba.py` probe to confirm free-tier availability before relying on it.
4. **Spreads + totals markets** — scout is h2h only. New observer adds h2h + spreads + totals via extended `OddsAPIClient.get_lines(markets=("h2h","spreads","totals"))`.
5. **`observation_id`** (uuid4 per row) — for future order-correlation FK, avoiding the ±2s VIEW join bug in `kalshi_crypto_vol_v2.py`.

---

## Recommendation for Phase 0 Step 4 (fresh observer)

- **Do NOT skip the fresh observer.** Scout corpus tells us the soft-book-proxy B test is null on aggregate; that's a known result and not the question we're asking. The questions Step 4 must answer (Hypothesis A frequency/persistence/after-fees-survival; sharp-book-vs-Kalshi B if Pinnacle is available; spreads/totals coverage) all require fields the scout never collected.
- **Reduce expectation on NBA-only verdict.** NBA n=24 + thin Finals-not-yet-started calendar ⇒ even after 5–7 game days, n_NBA may be ≤ 50. Phase 0 verdict on NBA-only is structurally biased toward `INCONCLUSIVE_INSTRUMENT_TOO_WEAK` (per the Verdict design's anti-false-KILL discipline). Plan accordingly: the Phase 0 verdict report will likely recommend a Phase 0.5 instrument-strengthening pass (faster polling, Pinnacle integration, OR sport-scope flex) rather than a clean GO/KILL.
- **Cross-league context** (NFL/MLB/NHL/MLS) is `OUT OF SCOPE` for this division per the user's locked NBA-only scope, but the corpus has incidentally validated that the math layer works across all of them. Future divisions can reuse `_sports_math.py` unchanged.

---

## Artifacts produced this step

- `trading_corp/agents/strategies/_sports_math.py` (NEW) — pure math module; 23/23 unit tests green.
- `tests/test_sports_math.py` (NEW) — hand-worked fee table + A-arb pos/neg + B EV cases.
- `scripts/retro_compute_ev_at_fill_scout.py` (NEW) — canonical retro script for any future re-analysis or for running against a downloaded DB copy.
- This report.

## Next steps (per Phase 0 plan)

- Step 5: Grading-alignment matrix (`reports/2026-05-23_kalshi_nba_grading_alignment.md`) — primary-source NBA grading rules Kalshi vs DK/FD/BetMGM. Independent track; can start now.
- Step 1 Decision 2: Run `scripts/probe_odds_api_pinnacle_nba.py` to determine whether B can be tested against Pinnacle or only soft-book median.
- Step 3: Extend `OddsAPIClient` with `get_lines` + per-book breakdown + spreads/totals.
- Step 4: Build `kalshi_sports_arb_observer.py`; dev `--once` dry-run + Step 5 grading-matrix INCLUDE/EXCLUDE filter baked in; flip enabled after manual hand-verification on the FIRST observed row.
