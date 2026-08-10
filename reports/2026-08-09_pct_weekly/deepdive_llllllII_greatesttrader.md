# PCT Deep-Dive — llllllII (hard-cut vs demote) + GreatestTrader (durability)

**Date:** 2026-08-10 (data 03:12 UTC) · **Mode:** READ-ONLY. Operator actions UI.
Follow-up to `pct_weekly_assessment.md`. Uncapped own-activity pulls (25 pages) + our copied day-by-day + LoL calendar cross-ref.

---

## 1) llllllII — hard-cut vs demote-to-watch

### ⚠ Correction to the weekly report
The weekly report's **−$36,081 realized / −$33,040 clean-holds was a FETCH-CAP ARTIFACT.** That audit stopped at the 5,000-fill cap (hit_cap=true), dropping llllllII's profitable late-April/early-May weeks and truncating boundary decisions. The uncapped pull (5,500 fills, **full 94-day history**) shows:

- **Full-history realized = +$5,038 ; clean-hold = +$7,328 — mildly POSITIVE.**

llllllII is a **high-variance, roughly-breakeven-to-slightly-positive** whale, **not** a chronic loser. The hard-cut-a-loser thesis is refuted.

### The realized trend — a sharp final week, not a gradual bleed
Weekly cumulative realized: **+$38.5k through 07-20**, then a single **−$33.5k week (07-27→07-31)** dropped it to +$5k — and then it went silent. Its history is full of big swing weeks (−$95k, +$74k, −$27k), so the final −$33.5k is within its normal variance. The pattern is **"volatile positive whale has one bad week, then stops,"** not a decline.

### The gap pattern — and the calendar
- Over 94 days, **exactly ONE prior silence ≥3 days: 06-21→06-27 (6 days)** — and it resumed into a very active, profitable July.
- **Current open gap = 9.4 days** (last own trade 07-31) — longer than the only precedent, and ongoing.
- **LoL calendar cross-ref is the tiebreaker:**
  - The **June gap sat in the spring→summer split transition** (summer splits hadn't started) → a genuine *calendar-driven* gap that resumed when summer began. Consistent with esports off-weeks.
  - The **current silence is NOT calendar-explained**: LEC Summer started **2026-07-24 and runs nine weeks with no breaks**; LCK regular season runs through **Aug 23**; LPL active. **Matches were available the whole time — the whale simply stopped**, right after its worst week.

### Verdict: **DEMOTE-TO-WATCH (lean cut), NOT leave-on-copy, NOT hard-cut yet**
Two facts point in opposite directions, and demote is the low-regret resolution of both:
- **Against hard-cut:** the edge is mildly *positive* (+$5k/+$7.3k), not the −$36k loser the capped audit implied. You should not permanently delete a positive-edge whale on a data artifact.
- **Against leaving it on the copy list:** the copyable signal has genuinely *stopped* (9.4d, no calendar excuse, post-loss timing) — a dormant whale earns nothing and occupies a slot.

**→ Demote off the active copy list now** (stop copying the silence, free the slot for a promote candidate) **but keep it pinned on watch** (preserve the re-copy option — there's precedent for a gap resuming). **Hard-decision date:** if it hasn't resumed by ~**2026-08-17** (≈2.5 weeks silent, well past its 6-day precedent, during active season), convert to hard-cut. If it resumes before then, it's re-promotable as the same breakeven-positive, high-variance esports whale it always was — with the standing caveat that its edge is thin and volatile.

*(If you prefer a single UI action over demote-then-maybe-cut, hard-cut is defensible too — the current silence is not calendar-backed. But demote strictly dominates on optionality since the edge is positive.)*

---

## 2) GreatestTrader — durability second-time-point check

**Result: durability CONFIRMED. It is NOT a 7-day hot flash — but it is running hot recently.**

- The screen's `window_days_span = 6.9d` was only its *sampling window*. The whale's real track record spans **51 days (2026-06-19 → 08-09), 238 resolved decisions.**
- **Positive in 7 of 8 weeks** (single down week 06-29 at −$20k, fully recovered next week), and **clean-holds track realized every week** (e.g., last week clean +$52.3k vs realized +$52.8k) — a genuine holder across the whole span, not a one-week spike. This is categorically unlike `boomingtest` (which was deprioritized last batch for a genuinely short/thin window).
- **Caveat (size, don't skip):** the last two weeks (07-27 +$29.1k, 08-03 +$52.8k) = **+$81.9k = 58% of the +$141.9k total.** The edge is real across 51 days but the headline is inflated by a recent acceleration. Promote it, but **size conservatively and expect ROI to normalize below the recent pace** — the +$142k is not a run-rate.

**Net:** GreatestTrader stays a **Tier-1 PROMOTE, durability confirmed** — upgrade the earlier "confirm it isn't a hot run" flag to "confirmed durable, recently hot → size conservatively." rollobravado (335 dec, positive-holder) and Kosherlocks (99%-clean, sharp) remain the cleanest two; GreatestTrader is the biggest and now durability-verified, with a recency-weighting note.

---

### Provenance
Read-only. Uncapped `/activity` (25 pages) → daily activity, gap detection, weekly realized/clean via `group_fills_by_decision`; our copied day-by-day via `sqlite3 -readonly`. LoL schedule via web (Leaguepedia/Red Bull/lolesports). Raw: `raw_deepdive.txt`. No prod writes.
