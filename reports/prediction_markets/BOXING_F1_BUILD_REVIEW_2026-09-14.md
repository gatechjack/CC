# BOXING + F1 — build review (read-only), answering the board's five questions

All numbers below are from re-running the gates with full instrumentation (RO: box pm_closed_position + live
public Kalshi + the matcher). Runners: `cc/pm_boxing_missaudit_ro.ps1`, `cc/pm_f1_missaudit_ro.ps1`. No deploy,
restart, arm, or DB write. Live tree untouched (isolated scratch).

═══════════════════════════════════════════════════════════════════════════════════════════════
## Q1 — MISSES CLASSIFIED BY REASON (chose-not-to vs could-not)
═══════════════════════════════════════════════════════════════════════════════════════════════

### BOXING — 129 real whale bets: 21 matched, 108 missed
| status | n | verdict |
|---|---|---|
| matched | 21 | copied (independently verified — Q4) |
| **fail** (parse, no-date) | **82** | **CHOSE-NOT / no-contract.** ALL 82 are DATELESS slugs — either `boxing-{a}-vs-{b}` celebrity-novelty (`boxing-andrew-tate-vs-chase-demoor`, `boxing-jake-paul-vs-gervonta-davis`, `boxing-canelo-lvarez-vs-terence-crawford`) or `boxing-will-{fighter}-win-by-{method}` method props (`will-usyk-win-by-decision`). A date-join can't touch a dateless slug — AND **none of those bouts is on KXBOXING** (probed: Tate/Crawford/Usyk/Jake-Paul all absent). So they are not copyable regardless: legitimate scope, not a defect. |
| out_of_window | 17 | **LEGITIMATE.** 3 distinct bout dates (2026-03-08, 05-10, 06-06), all before the Kalshi window min (2026-07-12). Real bouts Kalshi no longer lists. |
| winner_outcome_unresolved | 7 | **MIXED — see Q2.** 3 are bouts NOT on Kalshi at all (Hitchins-Salas 07-26 — Kalshi's 07-26 card is different fighters). 4 are the **"Surname Initial." format gap** (a real coverage miss, safe). |
| abbrev_collision_ambiguous | 2 | **SAFE-MISS caution — see Q2** (two "Garcia"s on the 09-12 card). |

**So boxing's 16% is: mostly LEGITIMATE (dateless-not-on-Kalshi 82 + out-of-window 17 + not-on-Kalshi-3 +
collision-2 = 104), plus ONE real could-not coverage gap (4 near-misses = the Surname-Initial format).**

### F1 — 543 real whale bets: 116 matched, 427 missed
| status | n | verdict |
|---|---|---|
| matched | 116 | copied (independently verified — Q4) |
| **skip_non_winner** | **268** | **CHOSE-NOT.** 100% F1 PROPS we do not copy — `-driver-pole-position-`, `-driver-podium-`, `-driver-fastest-lap-` (shape-tallied). Zero are a winner bet misclassified. |
| out_of_window | 159 | **LEGITIMATE.** 13 distinct race dates 2025-11 -> 2026-07-05, all outside the 6-date Kalshi window. |
| winner_outcome_unresolved / driver_ambiguous | **0 / 0** | **F1 has NO near-misses and NO bind failures** — 116/116 in-window winner bets bound. F1's `yes_sub_title` is uniformly "First Last"; it has no initial-format variant, so it is CLEANER than boxing. |

**F1 has ZERO could-not misses. Every F1 miss is chose-not (prop) or legitimate (out-of-window).**

═══════════════════════════════════════════════════════════════════════════════════════════════
## Q2 — THE BOXING SURNAME RISK, EXERCISED ON THE REAL CORPUS
═══════════════════════════════════════════════════════════════════════════════════════════════

**Same-card surname collisions in the 129: YES, one — and the guard fired.** `zuffa-garcia-benn-2026-09-12`
(outcome "Garcia"): "Garcia" matched a fighter in MULTIPLE bouts on the 09-12 card (Sean Garcia is in the
Morales bout; the whale's Garcia is the Benn opponent) -> `abbrev_collision_ambiguous`, ticker=None. **A SAFE
MISS, never a pick** — exactly the board-required behavior, now proven on real data (not just synthetically).
This is caution with a coverage cost: the slug carries the opponent ("benn") which COULD disambiguate, but the
matcher deliberately does not use it (safe-miss over guess).

**Near-misses (bout resolved, fighter not): the "Surname Initial." format is a real, narrow COULD-NOT gap.**
Kalshi lists SOME boxing markets in a second generation — `KXBOXING-26SEP12MA-M` / `-MA-A` with
`yes_sub_title="Magsayo M."` / `"Cortes A."` (surname + INITIAL, 2-char blob, 1-char code) — alongside the
surname-based `KXBOXING-26SEP12GARCIAMORALE-GARCIA` on the SAME card. Probed prevalence: **7 of 300 KXBOXING
markets** (Ramirez J. C., Rocha A., Magsayo M., Cortes A., Akpejiori R., Mikaelyan N., Opetaia J. — all the
09-12 main-card bouts). My surname bind takes the LAST token as the surname, which for "Cortes A." is the
initial "a", so a whale's "Cortes" does not bind -> `winner_outcome_unresolved` (accounts for the Opetaia +
Magsayo/Cortes near-misses).
- **This is a bind-too-weak for that name format — a coverage gap, NOT a wrong pick.** It fails CLOSED (safe
  miss). The code-anchor still passes correctly on these (verified), so there is no mis-route risk; the surname
  simply is not found.
- **★ ZERO wrong-competitor picks** across all 129 (the dangerous failure mode the cs2 code-anchor exists for).
  Every matched bet's competitor was independently confirmed (Q4).
- **Board call:** accept the ~2% coverage gap as a documented limitation (UFC-style), OR a small pre-enable fix
  (recognise a trailing `\w\.` initial and take the leading token as the surname; ~5 lines in `_surname_tokens`
  + the short-code ticker shape). It is not a correctness/safety defect either way.

═══════════════════════════════════════════════════════════════════════════════════════════════
## Q3 — F1 AND THE DATE JOIN (the cross-midnight concern) — WINDOW, and it is doing real work
═══════════════════════════════════════════════════════════════════════════════════════════════

**The F1 join uses a +/-1 DAY WINDOW (`WINDOW_DAYS=1`), NOT an exact match.** Proven necessary AND safe on the
real corpus:
- **The disproving case exists and the window caught it.** Of the 116 matches, **9 had a −1-day offset** (Poly
  slug date `2026-09-13` vs Kalshi `close_time` `2026-09-14` UTC) — **the Spanish GP** (race Sunday 09-13 local
  Madrid; race-end/close 09-14 UTC). Example: `f1-spanish-grand-prix-winner-antonelli-2026-09-13` -> close
  `2026-09-14` -> `KXF1RACE-SPAGP26-ANT`. **An EXACT-date join would have SILENTLY DROPPED all 9** — precisely
  the UTC-vs-local NFL-night-game class of bug. Offset distribution: `+0: 107, −1: 9` (0 at other offsets).
- **The window cannot bind two races.** The 6 in-window race dates are a **minimum of 7 days apart** (F1 runs
  one race/week), so a +/-1 window can never contain two races. If it ever did (it cannot for F1), the
  `len(races)>1 -> driver_ambiguous` guard makes it a SAFE MISS, not a wrong bind.
- Direction note: only the −1 offset appears (Poly-local <= Kalshi-UTC), because F1 races are afternoon/evening
  local so UTC is never a day behind local; the window covers +/-1 either way regardless. Las Vegas (the extreme
  West case) is out-of-window here (November), but the Spanish −1 case exercises the same UTC/local mechanism.

**Contrast — boxing correctly uses EXACT date (no window), and that is right:** boxing joins the CARD-LOCAL date
in the Kalshi TICKER (`26SEP12`) to the card-local date in the Poly slug — BOTH local, no UTC anywhere (the UFC
precedent). No cross-midnight class exists for boxing by construction; the audit found zero boxing date offsets
(the boxing unresolved cases were bouts not on Kalshi, not date mismatches). The UTC/local split is F1-only
(F1's Kalshi date comes from `close_time` UTC), which is exactly why F1 needs the window and boxing does not.

═══════════════════════════════════════════════════════════════════════════════════════════════
## Q4 — INDEPENDENT VERIFICATION (counts + the bind SOURCE, the RFI-polarity lens)
═══════════════════════════════════════════════════════════════════════════════════════════════

- **Boxing: 21 of 21 matches independently verified.** The dry-run's competitor check re-derives from the
  ticker's OWN -CODE (does the code subsequence the whale's outcome?) — a DIFFERENT field from the matcher's
  bind (which uses `yes_sub_title`). 21/21 passed, 0 wrong, 0 unverifiable.
- **F1: 116 of 116 matches independently verified** on TWO axes: competitor (ticker code subsequences the driver
  — independent of the title/`yes_sub_title` bind) AND game (matched-ticker `close_time` within +/-1 of the slug
  date — independent of the matcher's own date logic). 116/116 passed, 0 wrong.
- **Bind source (the RFI lesson: leg from resolution TEXT vs slug TAG).**
  - Boxing competitor: bound from `yes_sub_title` (Kalshi full name) -> Poly outcome surname; verified against a
    DIFFERENT source (the ticker code). Leg: ALWAYS "yes" — derived from the MARKET STRUCTURE (moneyline = buy
    YES on the bet fighter), not from any tag or text, so there is no false-friend polarity to invert.
  - F1 competitor: bound from the title full name (fallback slug surname) -> `yes_sub_title`; verified against
    the ticker code. Leg: from the OUTCOME string (Yes->yes / No->no).
  - **Honest caveat:** for F1 the leg-AUDIT re-derives the leg from the same OUTCOME the matcher used, so the LEG
    check is NOT fully independent (it shares the outcome->leg mapping). Unlike RFI, that is low-risk here — F1/
    boxing have NO slug TAG that could be a false friend (RFI's `-nrfi` tag vs the resolution text was the trap);
    the outcome string directly IS the leg semantics (Kalshi YES = driver/fighter wins = Poly "Yes"). The STRONG,
    fully-independent check in both families is the COMPETITOR bind via the ticker code — which is what
    caught-nothing-wrong across 21 + 116.

═══════════════════════════════════════════════════════════════════════════════════════════════
## Q5 — DID THE DRY-RUNS EXERCISE THE DEPLOYED PATH OR A SCRATCH COPY? (plainly)
═══════════════════════════════════════════════════════════════════════════════════════════════

**Plainly: the ORIGINAL gate dry-runs (in the deploy manifests) exercised the SCRATCH matcher, NOT the deployed
wiring.** They imported `boxing_poly_kalshi_match` / `f1_poly_kalshi_match` and called `build_index` + `match_bet`
directly. They did NOT go through `execution.evaluate -> MATCHER_ADAPTERS -> the adapter -> ctx.*_index`, and
they did NOT call the live `fetch_*_market_context` ctx builders. **So every "zero wrong" in the manifests proved
the MATCHER, not the deployed wiring** — you are exactly right, and the F1X NameError (an unimported alias in the
live ctx builder) is the proof: it was invisible to py_compile (syntax-only), to `import live_driver` (the
NameError lived inside a function), AND to the dry-run (scratch matcher, not the builder).

**What now closes that gap (added this review + the F1 fix commit):**
1. **Deployed adapter dispatch — verified in THIS audit: 0 mismatches** for both families. Every real bet was
   also routed through `execution.MATCHER_ADAPTERS['boxing'|'f1']` reading a real `execution.MarketContext`
   (`boxing_index`/`f1_race_index` slot), and the deployed dispatch produced byte-identical (status, ticker, leg)
   to the scratch matcher on all 129 + 543. So the adapter registry + the ctx-slot plumbing + the matcher-via-
   registry ARE now exercised.
2. **The ctx BUILDER wiring — `test_ctx_builder_imports.py`** (added with the F1 fix): stubs pykalshi + a fake
   client and CALLS `fetch_f1_market_context` AND `fetch_boxing_market_context`, asserting they resolve their
   matcher imports and build a MarketContext. This is the exact test that WOULD have caught the F1X NameError,
   and it now guards both builders.
- **Residual gap (honest):** the only deployed piece still not exercised end-to-end is `execution.evaluate`'s
  gate STACK (arm / sizing / quote / liquidity / shard / dedup) wrapped around the match. That stack is
  category-AGNOSTIC — identical for every category, unchanged by boxing/F1 — and is covered by the existing MLB
  dispatch tests. If you want belt-and-suspenders, a one-signal end-to-end `evaluate()` test per new category
  would close it; I did not add it in this read-only review.

═══════════════════════════════════════════════════════════════════════════════════════════════
## Verdict for your ruling
═══════════════════════════════════════════════════════════════════════════════════════════════

- **Zero wrong-competitor picks** in either family across 129 + 543 real bets (the dangerous mode). Match rates
  are dominated by LEGITIMATE misses (dateless-not-on-Kalshi, out-of-window, props, one safe collision).
- **F1 is clean**: no bind gaps, and the date-join window is proven necessary (9 real cross-midnight matches) and
  safe (races >=7 days apart). The wiring is now verified (dispatch parity + ctx-builder test).
- **Boxing has ONE narrow, SAFE coverage gap**: the "Surname Initial." Kalshi format (7/300 markets, one card)
  fails-closed to a miss, never a wrong pick. Your call: accept as a documented limitation, or a ~5-line
  pre-enable fix. It is not a correctness/safety defect.
- The original gates proved the matcher; this review adds the wiring proof. Both remain INERT until you arm.
