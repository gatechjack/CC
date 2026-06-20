# Golden-Pocket Continuation — Is "Confirmation" Real Edge, or Hand-Waving?

**Date:** 2026-06-19   **Type:** read-only backtest. No live code, no config, no deploy.
**Corpus:** `btc_scalping.db` `bars_15m`, 22,086 bars (Nov 1 2025 – Jun 19 2026, regime-fair).
**Script:** `golden_pocket_2026-06-19.py`.

> **METHOD test, NOT the bot.** 15m execution ≠ 3m scalper; nothing here transfers to the live bot
> without separate 3m work. Mechanics test, regime-bounded — a null is regime-inconclusive, a positive
> is candidate-not-verdict.
>
> **Data caveat:** there is no sub-15m data across this window (3m starts Mar 2026), so the method's
> "lower-TF confirmation" is detected on the **15m execution bars themselves**. The k=0-vs-k≥1 test
> still settles the repaint/delay question regardless of TF.

## The question
The golden-pocket method claims its edge lives in **"confirmation"** at a post-BOS fib entry zone — but
confirmation is usually hand-waved. Decomposed mechanically so it's measurable: (1) does the
golden-pocket **zone** beat a no-zone baseline? (2) does **requiring confirmation** improve the zone's
net-R, or just cut trade count at the same expectancy? This is the conditional-outcome test again, but
conditional on a **structural** setup — the axis the momentum scorer lacks
([[bitunix-confluence-scorer-audit]]).

**Setup mechanics:** body-close BOS off a non-repainting two-candle swing → impulse leg (origin swing →
extreme, incl. wicks) → golden pocket = 0.618–0.65 retracement. Entry on pullback into the zone. SL
beyond the 1.0 fib (origin) + 0.15×ATR buffer; **TP = 1.5R**; fee-net (corrected model); 81% chop /
19% trend tape.

## 1. HEADLINE — repaint test (k=0 vs k=1): **CLEAN, not repaint** ✅

| arm | k=0 net-R | k=1 net-R |
|---|---|---|
| zone_only | −0.204 | −0.204 |
| zone+engulf | −0.278 | −0.204 |
| zone+sweep | −0.218 | −0.178 |
| zone+choch | −0.293 | −0.224 |
| zone+FVG | −0.128 | −0.163 |

→ Every arm's k=1 net-R is ≈ k=0 or slightly **better** (the confirmation arms *improve* with delay).
The fib-zone + ChoCH repaint worry is **disproven** — these structural entries do not depend on
look-ahead. (Methodologically the most important line: a structural method that survives entry delay.)

## 2. Does the ZONE beat a no-zone baseline? **No.**

| arm (k=1) | n | win% | **net-R** | break% |
|---|---|---|---|---|
| **baseline** (enter at BOS, no pullback) | 1464 | 34.4 | **−0.130** | 57 |
| **zone_only** (enter the golden pocket) | 882 | 37.0 | **−0.204** | 62 |

→ The golden-pocket zone raises win-rate (+2.6pts) but **worsens net-R** (−0.204 vs −0.130) and breaks
more (62% vs 57%). Mechanism: the tight zone stop (entry at 0.618, stop at 1.0) gets broken more often
(post-BOS continuations *fail into the retracement* ~60% of the time) **and** the tighter stop makes
fees bite harder in R-terms. Entering **on** the break beats waiting for the pullback — echoing the
range-fade finding that structure breaks more than it holds.

## 3. CORE TEST — does measured confirmation earn its delay? **No.**

| arm (k=1) | n | win% | net-R | vs zone_only |
|---|---|---|---|---|
| zone_only | 882 | 37.0 | −0.204 | — |
| **zone+engulf** | 311 | 36.7 | **−0.204** | **identical net-R, ⅓ the trades** → pure selection, zero expectancy gain |
| zone+choch | 234 | 35.5 | −0.224 | worse |
| zone+session | 364 | 36.5 | −0.217 | worse |
| zone+sweep | 717 | 37.2 | −0.178 | marginally better (+0.026R), keeps 80% of trades |
| zone+FVG | 315 | 38.4 | −0.163 | marginally better (+0.041R) |

→ **The textbook confirmation failure, measured:** `engulf` produces the *exact same* net-R as
zone_only while cutting trade count to one-third — it selects fewer trades at identical expectancy,
which is conviction/selection, **not edge**. `choch` and `session` are *worse*. Only the
order-flow-flavoured filters (`sweep`, `FVG`) shave a little off the loss — but both remain
**net-negative** and neither crosses into positive. **No confirmation trigger earns its delay** into an
edge on this tape.

## 4. Regime split & walk-forward — the marginal signals are unstable

**Regime (k=1):** zone_only is *worse* in trend (−0.376, n=47) than chop (−0.194) — opposite of the
"continuation works in trend" hope, though trend N is tiny (19% tape). The one near-breakeven cell is
`zone+sweep` in trend (−0.037, **n=41**) — small and not robust.

**Walk-forward (k=1):**

| arm | TRAIN ≤Mar1 | VAL ≤May1 | LOCKBOX >May1 |
|---|---|---|---|
| zone_only | −0.128 | −0.386 | −0.085 |
| zone+sweep | −0.121 | −0.361 | **−0.019** |
| zone+choch | −0.079 | −0.546 | −0.019 |

→ The slope **swings wildly**: VAL is terrible (−0.36 to −0.55), LOCKBOX recovers to near-breakeven
(sweep/choch −0.019, win 43–45%). The recent-window near-breakeven is encouraging but the VAL period
**flatly contradicts** it — period variance dominates; no stable edge.

## Plain verdict

> **Measured confirmation does NOT earn its delay.** The clearest case — `engulf` — gives *identical*
> net-R to no-confirmation while trading one-third as often: selection without expectancy, exactly the
> hand-waving the test was built to expose. `choch`/`session` are worse; `sweep`/`FVG` are marginally
> less-negative but still net-negative. **The golden-pocket zone itself doesn't even beat a no-zone
> baseline** (tighter stops break more + fee drag).
>
> **Did the structural axis do what the momentum score couldn't? No — both null here.** The best
> structural arms (FVG −0.163, sweep −0.178) are marginally less-bad than the confluence depth/score
> buckets (~−0.2 to −0.35) but land in the same net-negative territory. The orthogonal *structural*
> axis is not obviously rescuing what momentum couldn't on this tape.
>
> **The one thing that did pass cleanly: no repaint.** The structural setup survives entry delay
> (k=1 ≈ k=0), so — unlike the divergence work — this null is *real*, not a look-ahead artifact.

**Mechanism across studies:** post-BOS continuations **fail into the retracement ~60% of the time**
(break-rate 57–66%) on this corpus — the same "structure breaks more than it holds" that killed the
range-fade. Continuation and mean-reversion both lose because the tape doesn't trend cleanly enough at
this TF.

### Caveats (load-bearing)
1. **Regime-bounded.** 81% chop / 19% trend; every arm net-negative (like all prior studies). The
   regime-robust result is the **shape** (confirmation doesn't add expectancy; zone < baseline), not
   the levels. Doesn't prove the method can't pay in a cleanly-trending regime we didn't sample.
2. **Confirmation on 15m, not a true lower TF** (no sub-15m data). A genuine LTF confirm (e.g. 3m under
   a 15m setup) might behave differently — untestable on this window; would need the 3m corpus
   (Mar 2026+, shorter).
3. **The small near-breakeven cells** (sweep/FVG in trend, sweep/choch in lockbox) are candidates to
   re-check on a trending window — small-N and walk-forward-unstable here, **not** a green light.
4. **TP fixed at 1.5R**; the method's "prior-swing" target was not separately tested.

---

# Addendum — Sweep-definition stress test (closes the candidate)

The `zone+sweep` arm carried the only near-positive cells (trend −0.037, lockbox −0.019), and those
sat on an interpretive choice (I keyed the sweep off the **zone-low / 0.65**, not the swing extreme).
Re-ran the sweep under three keyings to see if the near-edge is real or definitional. Script:
`golden_pocket_sweep_2026-06-19.py`. Rails unchanged; cells with n<30 flagged.

- **sweep_zonelow** — pierce below 0.65 zone-low + reclaim *(the version already reported)*
- **sweep_origin** — pierce below the **1.0 fib / origin swing** + reclaim *(stricter, per spec)*
- **sweep_intermediate** — pierce below the recent **pullback low** (`pull_lo`) + reclaim

**Repaint (k=0 vs k=1): still clean** — every keying stays repaint-honest (k1 ≈ k0 or better).

**Full sample (k=1):** the stricter keyings are **worse**, not better:

| keying | n | net-R |
|---|---|---|
| zone_only (ref) | 882 | −0.204 |
| sweep_zonelow | 717 | **−0.178** |
| sweep_origin | 170 | −0.228 |
| sweep_intermediate | 341 | −0.245 |

→ The **loosest** sweep (zone-low) was the *least*-bad; tightening it degrades net-R. No "stricter
confirmation = better" signal.

**The near-positive cells do NOT survive:**

| cell | sweep_zonelow | sweep_origin | sweep_intermediate |
|---|---|---|---|
| TREND (k1) | −0.037 (n=41) | **+0.547 (n=6 ⚠)** | −0.042 (n=20 ⚠) |
| LOCKBOX (k1) | −0.019 (n=138) | −0.083 (n=38) | −0.091 (n=60) |

- The lockbox near-breakeven (zone_low −0.019) **degrades to clearly-negative** under both stricter
  keyings (−0.083 / −0.091).
- The only positive cell anywhere is `sweep_origin` in trend = **+0.547 at n=6** — pure noise, flagged,
  dismissed.
- The trend near-breakeven is *preserved in magnitude* (zone_low −0.037, intermediate −0.042) but it's
  **near-zero, not positive**, and small-N (41 / 20). It was never an edge — it's a flat-negative
  small-N cell that looked interesting only at one loose definition.
- VAL stays terrible across all keyings (−0.36 to −0.43).

## Verdict (addendum) — candidate CLOSED, null complete

> **The near-edge was definitional + small-N, not robust.** Under a stricter or different sweep keying
> the near-positive cells collapse to clearly-negative (lockbox) or stay flat-negative-and-tiny
> (trend); the one positive is n=6 noise. The loosest definition was the least-bad — the opposite of a
> real confirmation effect. **The golden-pocket / breakout-continuation candidate closes complete: no
> confirmation trigger, under any tested definition, earns its delay into an edge on this corpus.**
>
> Repaint-clean throughout — so this is a *real* null, not an artifact. Regime-bounded as always; a
> genuinely-trending window remains the only unsampled axis (parked, regime-deferred).
