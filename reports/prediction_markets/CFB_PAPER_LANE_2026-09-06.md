# CFB → Paper Lane: classify college football so an honest forward record can accumulate

**Date:** 2026-09-06
**Branch:** `pm-cfb-category-2026-09-06` (worktree `cc-pm-cfb-category-wt`, base = box-truth `pm-driver-liveness` tip `0eb93a1`)
**Goal (Jack's reframe):** not to harvest an existing copyable whale (the scoping follow-up found
zero CFB natives — fine), but **OBSERVATION**: get CFB into the category-agnostic **paper lane** so
a whale worth copying can *emerge* over weeks of gamma-graded forward record. Execution venue
(Robinhood etc.) remains OUT of scope. **Nothing here reaches the real-money order path.**
Prereq scoping: `INTL_COLLEGE_FOOTBALL_SCOPING_2026-09-06.md`.

---

## ESTABLISH (read-only) — verified from code + box, not assumed

**1. The paper machinery is genuinely category-agnostic (poller needs one prefix).**
- Adjudicator (`paper.py:356`) and rollup (`paper.py:436`): fully agnostic — gate only on
  `pm_watchlist status='pinned' AND active=1`, no category branch. A cfb paper trade is adjudicated
  and rolled up identically to mlb.
- Poller (`paper.py:121`, cron `*/30`): reads pinned whales with no category filter, polls each
  whale's **live open-position book** (`fetch_positions_book`), then routes each position by
  `derive_category_from_slug` and keeps it only `if pc in pinned_cats` (`paper.py:164-165`). So a
  cfb whale's positions derive to `unknown` and are silently `skipped` **unless `cfb` is in
  `SLUG_PREFIX_MAP`.** That is the one load-bearing dependency this change removes.

**2. Paper is graded against GAMMA RESOLUTION, not `/closed-positions` — the whole argument.**
`paper.py:357-364` (verified on box): *"resolution authority is …fetch_market_resolutions(), NOT
pm_closed_position … /closed-positions systematically omits held losses"*; closed rows carry
`close_source='gamma_resolution'` (`paper.py:392`). **So the loss-omission bias that makes every
screening number a fiction does not touch the paper lane.** The forward CFB record will be honest
in a way CFB whales' screening stats can never be. This is the strongest reason to do this.

**3. The 2,778 historical rows will NOT reclassify — and it doesn't matter.**
`derive_category_from_slug` runs only at ingest/refresh (`ingest.py:271,308`), never re-walks stored
rows, so the historical `cfb-` rows stay `unknown`. The `repair-categories` CLI uses Tier-2 *gamma
tags* (no cfb tag) so it wouldn't move them either, and it's a live DB write. **Crucially the paper
lane never reads historical rows** — the poller polls whales' *live open* positions. We are
**waiting on a forward record, which is exactly the goal.** No reclassification is performed.

**4. Live/order path is separate and stays untouched.** The live driver is gated by
`CATEGORY_CTX_BUILDERS` (mlb/ufc/atp/wta only → cfb skipped every cycle, fail-safe: no builder → no
signals → no orders), by `pm_subdivision` rows (none for cfb), and by the arm switch (disarmed by
default). Classifying + pinning a cfb whale populates **paper only** and cannot reach real money
unless a cfb sub-division is *separately created, attached AND armed* — none of which this does.

---

## THE BUILD — small: 2 live constants + tests

| File | Change |
|---|---|
| `trading_corp/prediction_markets/category.py` | `SLUG_PREFIX_MAP += "cfb": "cfb"` (per-game `cfb-<away>-<home>-DATE` slugs) |
| `trading_corp/prediction_markets/search.py` | `CATEGORY_ALLOWLIST += "cfb"` (15 → 16) |
| `tests/prediction_markets/test_category.py` | +3 tests: cfb games→cfb / futures→unknown / boundary; **only-cfb-added** delta; **no existing category moves** |
| `tests/prediction_markets/test_search_r1.py` | allowlist count 15 → 16, cfb added to the "in" set |
| `tests/prediction_markets/test_stage2_nav.py` | farm header/tile count 15 → 16 |

Post-change CR-stripped SHA256 (first 16), for the deploy graft:
`category.py a294d229982f66a5` (box-orig `b2b85b8eb12f1548`), `search.py 2e45f343c16f47ea`
(box-orig `75aff89ff26bc945`). The two live files were edited from the byte-identical box originals,
so the deploy is a clean 2-file placement.

**Deliberate scoping of the prefix:** `cfb-` catches the **per-game moneyline binaries** (the
copyable shape the paper lane wants) but NOT `college-football-champion-*` **futures** (different
slug family → stay `unknown`). That is correct: paper copies per-event binaries, not season futures.

**Allowlist consumers (what keys off the constant) — all benign, none live:** Search candidate
selection (→ Search will now emit cfb candidates), `farm.league_categories()` (→ a cfb farm tile),
`farm.is_league_category()` (→ `/farm/cfb` reachable). None touch the driver/order path.
Four `"15-category"` **docstrings** (`farm.py`, `search_run.py`, `web/app.py`, `pm_cli.py`) are left
stale-but-harmless **on purpose**, to keep the live deploy to exactly the 2 functional files rather
than enlarging the graft surface on an armed box.

### Acceptance — proven on the box, against real data (`pm_cfb_scratch.*`, isolated scratch)
- Additive diff: `category.py` +1 line; `search.py` set-delta = exactly `{cfb}` (0 members removed).
- Imports clean on the box venv; `len(CATEGORY_ALLOWLIST)==16`.
- Logic/regression (mirrors the committed unit tests): cfb games→cfb, futures/boundary→unknown,
  15 existing categories classify unchanged and none derive to cfb, farm tile cfb reachable.
- **★ THE ACCEPTANCE TEST on all 196,469 live rows (mode=ro):** `slug_prefix` rows that change
  category = **0**; non-unknown rows that collide to cfb = **0**; unknown rows re-derive to
  `{unknown: 38,348, cfb: 2,673}` with **0** deriving to any non-cfb category. **Not a single
  existing row moves;** exactly the 2,673 `cfb-` game rows would gain cfb on future ingest.
- Engine PID + pm_web PID untouched by the scratch (isolated copy; live tree/DB never modified).

---

## THE SEQUENCE THAT ACTUALLY POPULATES PAPER — plainly, with cost + owner

Classification alone does **not** start paper trading. Paper follows PINNED whales; pinning comes
from prospects; prospects come from Search. The chain:

1. **Deploy the 2 files** (this change). Cost: place 2 files; **NO engine restart** (the paper-poll
   / adjudicate / rollup / search crons are fresh processes that re-import from disk each run). A
   **pm_web restart** is needed only to expose the cfb tile / `/farm/cfb` / Promote button in the
   UI (engine stays untouched). Owner: **Jack authorizes** (HALT item — deploy + optional pm_web
   restart). *Armed live engine is NOT restarted.*
2. **Run a Search sweep** to populate CFB prospects. Search discovers wallets from the Polymarket
   **Sports** leaderboard, first-sight-backfills the *new* ones (their `cfb-` positions now classify
   as cfb → cfb `pm_category_stats` → cfb candidates written `status='candidate'`). Already-backfilled
   wallets are skipped (Ruling 1), so cfb candidates come from **fresh discovery** — which is the
   point: we're looking for CFB whales we don't yet track. **★ COST, honestly:** it is a ~90-minute
   sweep hitting Polymarket **from the same IP the live engine polls every ~7s for eight armed
   sub-divisions**, and it can push the engine into rate-limit backoff → real copies placed late or
   missed. **Jack presses that button himself, knowing the cost.** Owner: **Jack.**
3. **Jack pins** the CFB whales he wants observed, from the prospect list (`/farm/cfb` Promote →
   `promote_to_watchlist`, status→`pinned`). Cost: a few clicks; writes `pm_watchlist`/`pm_roster`
   only. Owner: **Jack.**
4. **The existing `*/30` poller picks them up** automatically — captures each pinned whale's live
   open cfb positions as paper trades; **05:40 adjudicate** grades them at gamma resolution; **05:50
   rollup** aggregates into `pm_paper_category_stats`. Cost: zero (already running). Owner: **the
   crons.** The honest, gamma-graded forward CFB record begins accumulating from here.

There is no step that reaches the order path. A CFB whale becomes a real-money copy target only if,
*later and separately*, Jack commissions the live side (a cfb context builder + sub-division +
attach + arm) — explicitly out of scope for this change.

---

## DEPLOY PLAN (HALT — awaiting Jack)
- Graft `category.py` + `search.py` onto box-current (both == box byte-for-byte + the cfb additions;
  additive, 0 members removed — verified). Backup the 2 originals. **No engine restart.**
- Optional, Jack's call: restart **pm_web** (`prediction-markets-web`) to surface the cfb tile /
  Promote UI. The armed trading engine (`trading-corp`) is NOT touched.
- Post-deploy read-only check: re-run the real-DB acceptance (0 rows moved) against the live tree +
  confirm `/farm/cfb` 200 and the 8 armed sub-divisions unchanged.
