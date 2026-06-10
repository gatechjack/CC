# Polymarket option (c) Phase 1 — validation (Phase E, F-3 light-touch)

**Date:** 2026-06-10 13:34 UTC
**Branch:** `polymarket-option-c-phase1-2026-06-10` (4 commits off `origin/main` `32aa884`; pushed, unmerged)
**Mode:** Phase E validation. Local dry-run of the WORKTREE code against the live PUBLIC
Polymarket APIs (read-only; the new code is unmerged, so an on-prod run would execute the OLD
naive code — validating nothing). One read-only SSH SELECT for the current prod roster sample.
**Authorization:** operator in-session; agent-run read-only SSH ratified.

---

## Method

- **Steps 1 & 3 (dry-run + sensitivity):** `refresh_polymarket_selection(dry_run=True)` from the
  worktree, prod-equivalent params (top 2/cat + 2 global, candidates 20/cat, min_resolved 10,
  activity 500×10, target_buy_rows 150), at inflation thresholds 0.5 and 0.3. Throwaway local DB;
  dry-run writes nothing. Pins empty locally → roster shown is pure-algorithm (no operator pins).
- **Step 2 (reconciliation):** per sampled whale, MY REDEEM-grounded realized per condition_id
  (`group_fills_by_decision` over the paginated window) vs Polymarket's `/closed-positions`
  `realized_pnl`, matched on shared condition_ids. Tolerance: within $5 or 5%, whichever greater.
- **Prod read (read-only SSH):** `selected_whales` (13, all `source=dashboard_button`),
  `pinned_whales` (15 = the 13 + the 2 autopaused), autopause audit wallets.

---

## Step 1 — dry-run @0.5 + cause attribution

99 candidates, **5121 resolutions fetched** (deep pagination engaged), 12 selected (Rule B).
Algorithm realized-basis top-12 (none are the current operator-promoted 13 — those are manual pins):

| # | user | ROI | inflR | nDec | decWR |
|---|------|-----|-------|------|-------|
| 1 | almost-never | 2.91 | −17.8 | 11 | 0.91 |
| 2 | wr0ngw4yb3tt0r | 3.39 | −6.2 | 53 | 0.55 |
| 3 | lakemichigan | 0.21 | 0.06 | 202 | 0.91 |
| 4 | nizami | 0.11 | −32.3 | 70 | 0.99 |
| 5 | justdance | 11.5 | −185.9 | 19 | 0.47 |
| 6 | 0xE5F0…(self) | 1.17 | −0.07 | 26 | 0.54 |
| 7 | iusedtowritepoetry… | 0.23 | −2.7 | 13 | 0.85 |
| 8 | noman2026 | 0.20 | 0.31 | 35 | 0.74 |
| 9 | TheBronxTale | 0.08 | −0.35 | 131 | 0.68 |
| 10 | oidocrop | 0.01 | −0.27 | 163 | 0.96 |
| 11 | (blank) | 0.48 | 0.22 | 24 | 0.67 |
| 12 | I-love-pizzas | −0.30 | 0.41 | 17 | 0.47 |

**Cause attribution (D2b) — decisive:** `delta_timeweight` is ≈0 for **every** mover (0.0001–0.02)
**except `noman2026` (dTW=0.67)**. So roster churn is driven almost entirely by the **realized-basis
inputs** (`delta_realized` dominant), *not* by dropping time-weighting. Every mover's cause is
cleanly attributable; the one time-weighting-driven mover is flagged. Many naive-roster whales drop
with reason `resolved<10` — the **decision-unit** count falling below the floor (the clustering-fix
working; per-fill counts that previously cleared 10 collapse to fewer real decisions).

Note: edge factor saturates at 3.0 for any ROI ≥ 2.0 (clip), so the several ROI>2 whales differ only
by their Wilson term — intended outlier defense.

---

## Step 2 — `/closed-positions` reconciliation (8-whale sample)

| whale | tag | walk | nDec / win | matched pass/total | my Σmatched | pm Σmatched | verdict |
|-------|-----|------|-----------|--------------------|-------------|-------------|---------|
| theboss2 | sel/GLOBAL | **exhausted** | 17 / 11 | **16/16** | 230,158 | 237,322 | ✅ clean |
| Magamyman | sel/Politics | capped | 76 / 49 | **62/65** | 749,569 | 730,100 | ✅ (6-figure positions match to the $) |
| TimmyTurner123 | sel/Sports | capped | 18 / 8 | **10/10** | 40,502 | 41,155 | ✅ |
| Johnnyboy42069 | AUTOPAUSED | capped | 13 / 4 | **4/4** | 27,828 | 27,865 | ✅ |
| damed21 | AUTOPAUSED | capped | 11 / 7 | **7/7** | 132,593 | 133,248 | ✅ |
| BigodinSagaz | sel/Tech | capped | 63 / 50 | 24/27 | 11,832 | 10,911 | ⚠ 3 fails (truncation) |
| kitten147 | sel/Crypto | capped | 10 / 8 | 4/9 | 9,203 | 2,069 | ⚠ truncated cost basis |
| AdrianCronauer | sel/Politics | capped | 11 / 11 | 2/11 | 488,570 | −179,893 | ❌ heavily truncated |

**Two conclusions:**

1. **The REDEEM-grounded compute is CORRECT** where the activity window is complete. `theboss2`
   (full history, "exhausted") reconciles 16/16 to ~1%; `Magamyman`'s six-figure positions match
   Polymarket to the dollar (232,776 vs 232,784; 165,176 vs 165,177). The math is validated.

2. **Window-depth limitation (calibration finding).** Every mis-reconciling whale hit
   `target_buys_reached` — the walk stopped at `target_buy_rows=150` (inherited from the seed's WR
   screen), truncating older BUYs → **understated cost basis → my realized runs HIGHER than
   Polymarket's full-history number** (all fail deltas are my>pm). `target_buy_rows=150` is too
   shallow for cost-basis-accurate realized P&L on the highest-volume whales. The fix the
   200→500 pagination commit closed exists one level deeper at the 150-buy early-stop.

**Goal-1 vs goal-2 divergence — empirically confirmed.** Both autopaused whales have **positive**
whale-own realized (Johnnyboy +8.4k, damed21 +83.8k) and reconcile cleanly, despite autopause
flagging them on **negative our-copy** `round_trips` P&L. They are good traders whose copies lost us
money — exactly the separation option (c) exists to expose, and the reason a refresh re-adding them
(they're still pinned) is a real consideration.

---

## Step 3 — inflation-gate sensitivity (single 0.3 run, consistent data)

22 candidates have `pnl_inflation_ratio > 0.3`. Cutoffs on the same data:

| gate | # gated | band added vs next-looser |
|------|---------|---------------------------|
| > 0.7 | 18 | — |
| > 0.5 (default) | 19 | +eyesuck (0.58, −299) |
| > 0.3 | 22 | +The-Joker (0.47, **+8,681**, 93 dec), +I-love-pizzas (0.41, −17,062), +noman2026 (0.31, **+18,829**, 35 dec) |

- **0.5 → 0.7 changes one whale.** The default gate is not knife-edge sensitive at the top.
- **0.3 → 0.5 keeps 3 whales** including two *profitable* ones (The-Joker, noman2026) — tightening to
  0.3 would exclude genuinely profitable but churny traders. **0.5 looks like a reasonable default.**
- Several gated whales have **positive** realized (The-Joker, divshah11, cryptofund, lranon, mtm3):
  the gate fires on headline-vs-realized *gap*, not on losing — operator should confirm that's intended.

**Metric-stability caveat.** `pnl_inflation_ratio = (held − realized)/max(|held|,1)` is **unstable when
held-to-resolution ≈ 0**: across the two live runs `mofi0091` read 0.82 then 11.40 for ~identical
realized (−3.6k) and decision count. The ratio is a sound *relative* churn signal but its absolute
value is noisy near held≈0 — treat the gate as "high inflation = churn," not a precise cutoff;
consider a denominator/ cap refinement in Phase 3.

---

## Findings → the merge decision

1. **Compute validated.** REDEEM-grounded realized P&L is correct on complete windows
   (reconciles to the dollar on six-figure positions). Ship-worthy on the math.
2. **`target_buy_rows=150` is too shallow for realized P&L** on the highest-volume whales →
   over-stated realized. **Recommend** raising it for the realized path (e.g. 500–1000, or prefer
   exhaustion up to `max_pages`) and re-validating the capped whales. This is the one code change
   I'd want before merge; it's small (a param) but changes API cost.
3. **Inflation gate 0.5 is reasonable.** 0.5↔0.7 negligible; 0.3 over-excludes profitable whales.
   The ratio metric is relatively noisy near held≈0 (refine in Phase 3).
4. **Pinned-merge consequence (pre-existing, untouched).** A real refresh ADDS the algorithm's ~12
   realized picks on top of the 15 pins (roster ~doubles from today's 13) and re-introduces the 2
   autopaused whales (pinned) until autopause re-pauses them. Operator should expect this.

**Operator decision:** merge as-is / bump `target_buy_rows` (+ optional threshold) and re-validate /
hold. No prod write occurred; the roster only changes when the operator merges and deliberately runs
the refresh.

---

## Re-validation — after the walk-to-exhaustion fix (commit `b44e3ed`)

Re-ran the 4 capped whales + `theboss2` control with the walk to exhaustion (early-stop disabled,
bounded by `max_pages=10`):

| whale | walk | rows | matched pass/total | my Σmatched | pm Σmatched | before → after |
|-------|------|------|--------------------|-------------|-------------|----------------|
| theboss2 (control) | exhausted | 84 | **16/16** | 230,158 | 237,322 | unchanged ✅ |
| Magamyman | **exhausted** | 822 | **85/85** | 806,235 | 806,254 | 62/65 → **85/85** ✅ (Δ −$18 on $806k) |
| kitten147 | **exhausted** | 3045 | **156/162** | 18,465 | 18,826 | 4/9 → **156/162** ✅ (6 fails sub-$110) |
| AdrianCronauer | **fetch_error** | 3500 | 13/29 | 1,561,611 | 107,644 | 2/11 → still off ⚠ |
| BigodinSagaz | **fetch_error** | 3500 | 130/155 | 26,477 | 25,192 | 24/27 → still off ⚠ |

**The fix is validated.** Every whale whose window `exhausted` now reconciles — `Magamyman` recovered to
**85/85** (six-figure positions to the dollar), `kitten147` 4/9 → **156/162** (residual fails are
sub-$110 fee/rounding noise on small positions). The deep walk closes the cost-basis truncation.

**The 2 residual mis-reconcilers are NOT a compute bug** (per the "STOP-and-surface" instruction): both
hit `walk=fetch_error` at exactly page 8 (~3500 rows) — a Cloudflare-403 / `/activity` pagination
ceiling — so their windows are still incomplete, and `my realized` over-states accordingly. The new
**`window_truncated=true` flag caught both**, so the unreliable realized is observable rather than
silently trusted. This is a data-retrieval ceiling on the very-highest-volume whales (the scoping doc
§6 "pagination cap, acceptable, window is by design"), not a math error.

**Open merge question (selection integrity).** A `window_truncated` whale's realized is over-stated →
it could rank too high or wrongly clear the inflation gate. The flag makes this observable; whether the
scorer should **gate/penalize** truncated whales (conservative: don't select on a floor-bounded
estimate) vs keep-but-flag is a merge-time decision — flagged, not auto-resolved.

## Known limitations registered

1. **Goal-1 vs goal-2 is a real, operationally-relevant split.** Both autopaused whales are
   *profitable* on whale-own realized (Johnnyboy42069 +$8.4k, damed21 +$83.8k) yet our copies lost
   money (autopause's `round_trips` P&L). When **BACKLOG P3 — demotion transparency** ships, the
   dashboard should surface BOTH numbers: "good whale, bad copy" is a category pure copy-P&L hides.
2. **`pnl_inflation_ratio` is unstable when held-to-resolution ≈ 0** (denominator `max(|held|,1)`):
   `mofi0091` read 0.82 vs 11.40 across two live runs for ~identical realized. Sound *relative* churn
   signal; noisy *absolute* value near held≈0. First place to look if the gate ever drops a whale you
   care about. Candidate metric refinement for Phase 3.
3. **`/activity` retrieval ceiling (~3500 rows / page 8).** The very-highest-volume whales can't have
   full history retrieved (Cloudflare-403 / API cap); `window_truncated` flags them.

---

## Addendum — pre-merge follow-ups (2026-06-10 14:32 UTC, commits `f448c93`, `c14e786`)

Two operator merge-gate decisions landed on the branch before the merge call:

1. **Truncation gate** (`f448c93`). `window_truncated=true` whales are now **excluded from
   algorithmic selection** — a hard gate in `score_whale_from_audit` (same mechanism class as the
   inflation gate). Rationale: the screen must not vouch for a realized number it could not fully
   fetch, and the inflation gate's denominator is itself corrupted by truncation. Truncated whales
   are **surfaced** in a labeled `unrankable` report section (partial numbers + flag), not silently
   dropped. **Manual promotion is unaffected** (the promote button writes `selected_whales`/
   `pinned_whales` directly, not via the scorer), and a **pinned** truncated whale **survives** via
   the pinned merge (pin overrides the gate). Tests: scorer gate excludes a top-score whale; refresh
   excludes + lists in `unrankable`; pinned-truncated survives.

2. **Pins-only refresh mode as the default** (`c14e786`). A real refresh no longer auto-expands the
   copy roster. **Default invocation writes ONLY pinned whales** — the algorithm produces the full
   report (rankings, gated-out, unrankable, cause attribution) but **never auto-selects**, preserving
   the manual-promotion workflow. The legacy "write algo top-N + pins" behavior is now an **explicit
   `--algo-select` opt-in**. The pinned-merge logic is unchanged (only the base list it merges into
   differs by mode). Tests: pins-only → roster == pins exactly (algo not written) with the ranking
   still in the report; `--algo-select` → algo + pins; `--dry-run` writes nothing.

**Gate:** 2256 passed / 28 failed (baseline 2229 + 27 new tests; the 28 are the identical pre-existing
robinhood/tasty/IC/webhooks set; zero regressions). No SSH, no prod write, no merge, no deploy this
session — all local code/test work.

---

*Phase E validation artifact — committed on `polymarket-option-c-phase1-2026-06-10`. All SSH this
session was read-only (probe + one selected_whales/pinned/audit SELECT). All `/activity` +
`/closed-positions` reads were public-API GETs from the local worktree run — no prod write.*
