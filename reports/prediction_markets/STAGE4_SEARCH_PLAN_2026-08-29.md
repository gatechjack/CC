# STAGE 4 — SEARCH (whale discovery -> prospects): PLAN + RUNG LADDER (2026-08-29)

**STATUS (2026-08-29, post-ruling): ALL FIVE Q's RULED (§9A). §8 converted to the RUNG LADDER (§8A). RUNG 1
BUILT (build + box-scratch only; live untouched at schema 12; NOTHING deployed). Rungs R2-R4 UNAUTHORIZED.
R7 is SEPARATE + UNTOUCHED (order path not referenced; verified structurally). HALT after rung 1.**

The planning pass below is preserved as the grounding record. This plan answers Jack's
Stage-4 brief on his rulings, grounded in real data pulled read-only from the box (runner
`cc\pm_stage4_datagather_ro.*`, 2026-08-29T21:17Z) and the existing code (cited file:line). It ends with a RANKED
list of decisions that are Jack's. **Independent of R7** — Search is a different code path (discovery/ingest) and a
different data path (`pm_closed_position` / `pm_category_stats` / `pm_watchlist`), touching NOTHING on the order
path (the driver, arm state, `execution.py`, `pm_subdivision*`).

## 0. ORIENT (verified read-only, 2026-08-29)
- **Branch** `prediction-markets-stage3-r55-2026-08-29` @ `8dd308c` (local==origin, fetched+verified). **`origin/prod-live`**
  `c88beea` (verified; **diverged** from my branch -- my branch predates prod-live's advance, as documented -- so Stage 4,
  when it builds, deploys by EXPLICIT MANIFEST).
- **Live PM DB schema = 12** (NOT 11; migration 012 `pm_subdivision.liquidity_ratio` deployed at R7.f step 1). So the
  Stage-4 migration is **013** (Jack's number is correct; the rebuild-plan's "010 pm_search_run" is STALE).
- Prospects list is **empty by construction today** -- NO code writes `status='candidate'` (this is the gap Search fills).

---

## 1. THE TWO-PASS SHAPE (Jack ruling #3) -- forced by the endpoint reality (item a)

**A REAL `/v1/leaderboard` response** (`fetch_leaderboard`, `polymarket_data_api_client.py:424`, `GET
https://data-api.polymarket.com/v1/leaderboard`), Sports bucket, first rows:
```
rank=1 wallet=0xf031...c80c  name=BreakTheBank  vol=$1,584,511  pnl=$535,328
rank=5 wallet=0x5268...135d  name=HomeRunHazard vol=$2,364,485  pnl=$250,290
rank=6 wallet=0x2005...75ea  name=RN1           vol=$6,019,359  pnl=$245,763
```
**The ONLY fields returned:** `rank, proxy_wallet, user_name, x_username, verified_badge, vol, pnl`. There is **NO fine
category, NO recency/timestamp, NO resolved-market count.** Categories are 5 COARSE buckets only
(`Politics, Sports, Crypto, Tech, Mentions`, `client.py:68`); the finer PM categories (mlb/nba/...) return empty.
`timeframe` is silently ignored -- **all-time data only.**

**-> Which of Jack's two filters can be applied BEFORE backfill? NEITHER.**
- **Category count (N>=50):** NO -- category is derived by slug-parsing positions we must fetch; the leaderboard's
  "Sports" is one undivided bucket.
- **Recency:** NO -- **and this corrects the brief's "recency may be available":** the leaderboard carries no
  timestamp and ignores `timeframe`; all-time `vol`/`pnl` cannot distinguish a whale active this week from one dormant
  since 2023.

So the shape is unavoidable and matches ruling #3: **Pass 1 = leaderboard discovers a POOL of wallets** (Sports bucket
+ optionally global; a crude all-time-pnl seed, no Jack-filter applicable). **Pass 2 = backfill each wallet -> derive
`n_resolved` per (wallet,category) + `resolved_ts` -> apply BOTH filters -> rank -> write candidates.**

---

## 2. THE SELECTION FILTER (Jack ruling #1): N>=50 resolved-in-category + recent activity

### 2a. N >= 50 -- checked against our OWN data FIRST (item d). It CUTS HARD.
`pm_category_stats.n_resolved` (the count of scoreable resolved markets per (wallet,category); `stats.py:78`,
`db.py:147`) across the existing **114 pairs**:

| stat | value |
|---|---|
| min / p25 / **median** / p75 / p90 / max | 0 / 3 / **12** / 135 / 463 / 6782 |
| mean | 256.5 |
| **>= 50** | **46 pairs (40%)** |
| < 50 | 68 pairs (60%) |
| >= 25 | 51 (45%) &nbsp;&nbsp; >= 100 | 37 (32%) |

**The median is 12 -- so N>=50 is well above the middle and cuts ~60% of pairs.** It selects the high-volume tail.
Of the 92 active watchlist pairs, only **37 reach 50**. **Per-category the impact is UNEVEN:**
```
ufc 10/9   mlb 10/4   nfl 9/4   nba 8/3   atp 8/2   soccer 8/3   epl 6/1   ucl 6/1
nhl 6/2    fed 4/2    tennis 4/2  wnba 4/1  wta 4/1  cs2 2/2   golf 3/0   cbb 3/0
                                                                (total / >=50)
```
**-> A GLOBAL N>=50 yields ZERO prospects in golf, cbb (and is very thin in epl/ucl/wnba/wta).** These are
low-per-whale-volume categories. **Decision for Jack (evidence-backed):** keep 50 as a selective floor (accepting
golf/epl/ucl start empty until a high-volume whale appears), OR use a lower floor for niche categories (e.g. 25 ->
45% survive), OR a per-category N. 50 is defensible for the sports where whales concentrate (ufc/mlb/nfl/nba); it is
too high for the thin ones. **This is Jack's to rule with the numbers above.**

### 2b. RECENCY -- "placed OR settled" (item e). The SETTLED half is easy; the PLACED half has a real gap.
What we store (verified columns):
- **SETTLED: precise + available.** `pm_closed_position.resolved_ts` (unix settlement time; `db.py:127`, indexed
  `ix_pm_cp_wallet_resolved`). "settled in last N days" = `resolved_ts >= now - N*86400`. (Live: 30d window = 1,433
  rows across 10 wallets -- the legacy set is small.)
- **PLACED: NOT stored.** `pm_closed_position` has NO entry/placed timestamp (only `resolved_ts`, `end_date`,
  `ingested_ts`). `/positions` -> `pm_open_position` has only `refreshed_ts` (when WE polled), no fill time, and is
  **0 rows right now** (transient; the paper poller repopulates it). True placement time lives ONLY in `/activity`
  (`ActivityRow.timestamp`), which the PM package **does not persist** (and which truncates at 5,000 rows).

So Jack's "entered heavily two weeks ago, nothing resolved yet = ACTIVE" (PLACED) is **not computable from stored
data**. Two ways to honour it (Jack chooses):
- **(A) Open-position proxy -- cheap, no new ingest.** Search's pass-2 backfill already can pull `/positions`
  (`ingest.refresh_open_positions`, `ingest.py:301`) into `pm_open_position` for each discovered wallet. Then
  recency = `resolved_ts >= now-N*86400` **OR** `has >= 1 open position in the category`. This captures Jack's case
  (open positions = active now) but is NOT time-bounded (a 6-month-old open position also counts as "active").
- **(B) `/activity` ingest -- precise, more work.** Persist `/activity` (placed timestamps) -> "placed in last N
  days" exactly. Cost: a new pull + table + the 5,000-row truncation caveat for large whales (BetMechanic/nba has
  6,782 resolved alone). This is what Stage 5 also needs for loss-grounding, so it may be worth building once.

**Recommend (A)** for Stage-4 selection (open = active is the operative case; precise placed-time is a refinement),
and note (B) is the Stage-5 dependency anyway. **The window N (7 / 14 / 30d) is Jack's** -- from the data, 7d catches
10 wallets, 30d catches 10, 90d catches 11 (our set is small + concentrated, so the window barely changes the count
here; it will matter on the ~50 discovered wallets).

---

## 3. THE CIRCULARITY -> INCREMENTAL BACKFILL (Jack ruling #2). HOW BIG IS THE PROBLEM? (item c) -- SMALL.
- **Pool per run: ~50 wallets.** The leaderboard is **capped at ~50 entries per bucket** (asked limit=250, got 50).
  Sports + global overlap heavily (sbsigner/nigiri99/RN1 appear in both), so the unique discovery universe is **~50-90
  wallets per run**, not thousands. (Verify: whether `offset` pages past 50 -- if so the pool can be widened; if not,
  50 is the ceiling.)
- **Backfill cost per wallet:** `/closed-positions` returns 50 rows/call (`client.py:534`), a whale caps at ~1,500
  rows (offset ~2000 empty), so a **FULL backfill = ~30 calls (~1-2 min with 429 backoff)**. A first full run of 50
  wallets = ~1,500 calls, ~15-40 min.
- **Incremental makes re-runs cheap (ruling #2, confirmed):** the same wallets recur across runs, so after the
  one-time full backfill each run fetches ONLY new trades. **BUT `/closed-positions` has NO time filter** (only
  `limit`/`offset`) -- so "incremental" is NOT a since-T API call; it is **page newest-first and STOP at the wallet's
  last-seen `resolved_ts`**. This works ONLY IF `/closed-positions` returns newest-first. **★ VERIFY THIS ON THE BOX
  before building** -- if it is not recency-sorted, stop-at-watermark fails and every run full-pages (still ~30
  calls/wallet, still affordable, but not "incremental"). Today `ingest.backfill_wallet` always full-pages from
  offset 0 (`ingest.py:226`); there is NO incremental path -- Stage 4 adds the stop-at-watermark.

**-> Order of magnitude: ~50 wallets/run, ~30 API calls each on first sight, a few calls each thereafter. Affordable
per ruling #2. This is a small problem.**

---

## 4. THE WATERMARK REGISTRY (item b). `pm_search_run` alone is NOT sufficient.
Two distinct things are needed:
- **RUN-level: `pm_search_run` (migration 013).** Records each run: `run_id` PK, `started_ts`, `finished_ts`,
  `category` (or 'all'), `leaderboard_limit`, `n_discovered`, `n_backfilled`, `n_candidates_written`, params (N,
  window), a status/summary. `pm_watchlist.search_run_id` is the RESERVED FK to it (`db.py:465`). This answers "what
  did each run do," and stamps every candidate with its provenance run.
- **WALLET-level watermark: needed for incremental, and `pm_search_run` does NOT carry it.** A run must know, per
  wallet, the newest trade it already has -- to page-until-watermark. `pm_whale` today has `last_backfill_ts /
  last_refresh_ts / backfill_complete / last_pulled / last_stored` (verified columns) -- these are OUR-run
  provenance stamps, **NOT** the wallet's newest `resolved_ts`. Options: **(i) derive** the watermark on the fly as
  `SELECT MAX(resolved_ts) FROM pm_closed_position WHERE wallet=?` (no new column; `backfill_complete` on `pm_whale`
  gates whether the first full backfill is done); or **(ii) add** `pm_whale.last_resolved_watermark_ts` for O(1)
  reads. **Recommend (i)** (derive) -- one indexed MAX query, no schema change beyond 013, and it cannot drift from
  the actual stored data. So **migration 013 = `pm_search_run` only**; the wallet watermark is derived + gated by the
  existing `pm_whale.backfill_complete`.

---

## 5. WHERE PROSPECTS LAND (item f). Respects the three-bases + the active gate; NO auto-promotion.
- **The write:** Search inserts `pm_watchlist (wallet, category, status='candidate', active=1, source='search',
  search_run_id=<run>, added_ts, updated_ts)` + the paired `pm_roster` row (mirrors the pinned-seed precedent
  `paper.py:540-543`, but status='candidate' not 'pinned'). This is the ONE new write path -- nothing writes
  'candidate' today.
- **The three bases stay separate (the load-bearing invariant):** a candidate renders the **completed-trade basis**
  (`pm_category_stats` <- `pm_closed_position`), NOT paper (`pm_paper_category_stats`), NOT live (P3). Every
  `pm_watchlist` consumer already gates `AND active=1` (verified: `farm.py:53/91/123/166`, `paper.py:142/497/562`,
  `stats.py:290`, `farm_actions.py`).
- **NO auto-promotion, NO auto-paper:** a discovered wallet lands as `status='candidate'` (Prospect) only. It is NOT
  pinned (paper-trading is pinned-only, `paper.poll_pinned` gates `status='pinned'`) and NOT attached to a live
  sub-division. **Promotion (candidate -> pinned) stays the manual board action** (`farm_actions.promote_to_watchlist`,
  the /farm button). Search populates; the human promotes.
- **★ R2 -- the category-level exclusion (a NEW selection gate, mechanism to choose).** `active` is per-ROW; Jack's
  exclusion is per-CATEGORY (cbb/fifwc/unknown show nowhere). Search discovering a whale in an excluded category would
  insert `active=1` and surface it -- a row flag cannot stop a pair that did not exist when the 22 were deactivated
  (PM_REQUIREMENTS R2). So Search's SELECTION must filter categories to the **15 ruled-in** set
  (`mlb, nba, nfl, nhl, wnba, epl, ucl, soccer, atp, wta, tennis, cs2, golf, ufc, fed`) BEFORE writing candidates --
  a discovered position in cbb/fifwc/unknown is backfilled (R5: ingest stays all-categories) but NOT written as a
  candidate. Mechanism options (Jack rules): a code constant set of allowed categories in the selection query, OR a
  `pm_category_status` table. **Recommend the code constant** (the 15 are already RULED and stable; a table is
  over-engineering for a 15-item allowlist) -- but flag that `cbb` re-admits after its probe, so the constant must be
  a single edit point.
- **R5 honoured:** the PULL/backfill stays ALL-categories (so cbb/fifwc/unknown keep accumulating evidence for later
  analysis); exclusion is ONLY at candidate SELECTION, never at ingest.

---

## 6. THE LOSS-OMISSION (item g). Rank on cost-ROI, never win%, and LABEL the bias on screen.
Everything Search ranks on comes from `pm_closed_position`, which **systematically under-reports held losses**
(measured: evanng 89 held losses -> 33 captured, 63% dropped; shows 77% WR vs ~52% true). The candidate basis
(`pm_category_stats`) has this baked in.
- **Ranking key: cost-ROI, NEVER win%** (the rebuild-plan BASIS test + the chalk lesson). Note even cost-ROI is
  biased UP (dropped losses inflate net pnl), but **Jack's two FILTERS are bias-ROBUST**: `n_resolved` is a COUNT
  (unaffected by which rows are losses) and `resolved_ts` is a timestamp. So the SELECTION (who surfaces) is not
  loss-biased; only the within-list ORDER (cost-ROI) is, and it is a screen, not the promotion decision.
- **★ The label is currently MISSING.** The prospect screen today has no explicit loss-omission caveat -- only an
  indirect "rough screen; Analyze is the judge" and a global "bias down, never up" footer. **Stage 4 must add an
  explicit, on-list caveat** naming the bias ("win rates over-stated: the completed-trades API under-reports held
  losses, wallet-dependently; this list is a SCREEN, Analyze is the promotion judge") so a ranked list does not imply
  precision it lacks (PM_REQUIREMENTS F-1 / R4 basis discipline). Analyze (Stage 5) is where the loss set is
  re-grounded per pair; Search only screens.

---

## 7. THE SOURCES NOTE (Jack's flag). No third-party leaderboard tool is used or trusted.
Jack flagged an outside suggestion of third-party tools (polycopy.app, Polyintel, FrenFlow, Merlin, Poly Syncer)
with specific figures. **I did NOT evaluate or build on any of them** -- every filter Jack ruled (N>=50,
placed/settled recency, cost-ROI rank) is computable from data we already ingest via the public Polymarket
`/v1/leaderboard` + `/closed-positions` + `/positions` endpoints (verified above with a real response). I observed no
evidence these third-party tools exist or expose an auditable score; the plan uses only the first-party endpoints and
our own `pm_category_stats`. If Jack wants any external source evaluated, that is a separate, explicit read-only probe
(verify-it-exists-first), not a dependency of this plan.

---

## 8. THE BUILD SHAPE (superseded by §8A once rulings landed -- kept for context)
Fork the legacy scout (`seed_polymarket_watchlist_deep.py` / `refresh_polymarket_whales.py`) into the PM package
(never edit/import legacy, per rule); REUSE `PolymarketDataAPIClient.fetch_leaderboard` + `ingest` (backfill, + the
new stop-at-watermark) + `stats` (rank). Migration 013 = `pm_search_run`. A `pm_cli search` subcommand (one-shot,
like the paper cadence) that Jack runs; NO auto-cron until proven.

---

## 8A. THE RUNG LADDER (post-ruling, AUTHORIZED per-rung). Build -> box-scratch -> HALT, never chained.
Each rung forks legacy scout PATTERNS (never import/edit legacy, per rule) and deploys by EXPLICIT MANIFEST with
Gate-A incl. TRANSITIVE imports. **R7 is a different code path entirely -- Stage 4 never touches the driver, arm
state, execution.py, or pm_subdivision\*.**

- **★ RUNG 1 (THIS RUNG -- BUILT; the PURE CORE + migration 013).** `db.py` migration **013 = `pm_search_run`**
  (pure DDL; run-level provenance + the ruled knobs; SCHEMA_HEAD -> 13) + **`prediction_markets/search.py`**:
    - the **category ALLOWLIST** constant (Q4) -- the 15 ruled-in categories, single edit point;
    - the incremental-backfill **WATERMARK** decision `page_new_rows` -- asserts newest-first INTRA-page AND the
      inter-page SEAM (threaded `prev_min_ts`), re-includes unreadable-ts rows (never silently drops), raises
      `OutOfOrderPage` on any violation so an early stop can never skip a trade (the worst failure class);
    - the candidate **SELECTION + RANK** `select_candidates` -- N>=50 with the **<10-qualifier top-10 THIN-SAMPLE
      fallback** (Q1), **30d recency via the open-position proxy as a GATE** the fallback still respects (Q2),
      **cost-ROI rank NEVER win%** (F-1), and COMPLETE exclusion accounting (every dropped row counted);
    - `LOSS_OMISSION_CAVEAT` -- the exact on-screen F-1 label string (the web layer imports it in R4).
  Pure/offline, unit-tested (`tests/prediction_markets/test_search_r1.py`), 3-agent adversarial review (watermark +
  selection + migration) findings folded in. **NO candidate write, NO leaderboard/backfill run, NO deploy; LIVE
  STAYS SCHEMA 12.**
- **★ RUNG 2 (BUILT; discovery + ON-DEMAND first-sight backfill + the run record).** `prediction_markets/
  search_run.py` (NEW): `discover_wallets` (`fetch_leaderboard` -> a de-duped pool, Q5 ~50/bucket) ->
  `ensure_backfilled` (Ruling 1: a whale WITH a complete backfill is read from the DB and NEVER auto-re-pulled;
  never-seen/partial gets ONE full-page `ingest.backfill_wallet`) -> `run_search` records a `pm_search_run` row
  with per-verdict counts. `refresh_one` = the refresh button's backend (ad-hoc full re-pull, R4 wires it).
  **Reshaped by Ruling 1 + the R2 probe: backfill is FULL-PAGE only (`/closed-positions` is neither sorted nor
  date-filterable -- §9C/§9D), so `page_new_rows` is NOT called; incremental is dead.** Writes
  `pm_closed_position` (via backfill) + `pm_search_run`; NO candidate write (R3). Ingest stays ALL-categories
  (R5). Injectable client/conn/clock -> offline tests. **The R3 CONTRACT (load-bearing): a partial/failed whale
  is stamped `backfill_complete=0`; R3 MUST gate the candidate write on `backfill_complete=1` so it is never a
  candidate nor ranked.**
- **RUNG 3 (candidate write).** Run R1's `select_candidates` over `pm_category_stats` (+ the open-position recency
  signal) -> write `pm_watchlist(status='candidate', active=1, source='search', search_run_id)` + `pm_roster`. The
  Q4 allowlist gate lives in SELECTION (already in R1); provenance stamped. Respects three-bases + the `active=1`
  gate; NO auto-pin/paper -- promotion (candidate -> pinned) stays the manual /farm board action.
- **RUNG 4 (the /farm prospects screen).** Renders the populated candidate list with the VISIBLE `LOSS_OMISSION_CAVEAT`
  (F-1) + the THIN-SAMPLE flags. pm_web-restart-only activation.

---

## 9. RANKED LIST -- what I need from Jack (decisions, not code)
1. **N>=50 -- confirm or set per-category.** Evidence (item d): global 50 cuts 60% of pairs and yields ZERO prospects
   in golf/cbb/epl-thin categories (whales there have <50 resolved). Keep 50 (selective; niche categories start
   empty), lower to 25 (45% survive), or a per-category floor? **[Q1 -- has real numbers to decide on.]**
2. **The recency WINDOW + the PLACED source.** Window 7 / 14 / 30 days? And PLACED recency = the cheap open-position
   proxy (recommended; "has an open position" = active, no new ingest) OR `/activity` ingest (precise placed-time,
   more work, 5k-row truncation, also a Stage-5 dependency)? **[Q2]**
3. **Run CADENCE + how many prospects per category** Jack wants to SEE (a top-K per category after ranking, or all
   that pass the filter?). One-shot `pm_cli search` he runs, or an installed cadence? **[Q3]**
4. **The R2 category-exclusion MECHANISM:** a code constant (the 15 ruled-in allowlist -- recommended) vs a
   `pm_category_status` table. **[Q4]**
5. **Leaderboard breadth:** the pool caps at ~50/bucket -- accept 50 Sports whales/run, or verify+use `offset` to
   page deeper, or add the global bucket + other buckets? **[Q5]**
6. **Verify-then-decide (I will confirm read-only before building, not assume):** is `/closed-positions` newest-first
   (determines whether incremental stop-at-watermark is possible)? This is a build-time verify, flagged so it is not
   assumed. **[not a Jack decision -- a build precondition I will prove.]**

**HALT. No build authorized.** When Jack rules Q1-Q5, the plan converts to a rung ladder (section 8A), each rung its
own authorization, build -> box-scratch -> HALT, never chained.

---

## 9A. JACK'S RULINGS (2026-08-29) -- all five settled; the ladder (§8A) and RUNG 1 are built to these.
- **Q1 -- N>=50 STANDS, WITH A FALLBACK.** When a category yields FEWER THAN 10 qualifiers, take the **TOP 10** in
  that category regardless of the floor, flagged **THIN-SAMPLE** on screen. (`search.select_candidates`:
  `len(qualifiers) < thin_sample_target(10)` -> `pool[:10]`; each `n_resolved < 50` row `thin_sample=True`.)
- **Q2 -- RECENCY: 30 DAYS, via the OPEN-POSITION PROXY.** `/activity` ingest (precise placed-time) is a Stage-5
  dependency and **must NOT be pulled forward into Stage 4.** (`_is_recent`: `has_open_position` OR
  `last_resolved_ts >= now - 30d`; a GATE the fallback respects -- a dormant whale never surfaces.)
- **Q3 -- CADENCE: NONE.** Search is a ONE-SHOT `pm_cli` command (R2). Return **ALL that pass** (with the top-10
  fallback), not a top-K cap. (Normal category returns every qualifier; only a fallback category caps at 10.)
- **Q4 -- R2 EXCLUSION: an ALLOWLIST CONSTANT.** (`search.CATEGORY_ALLOWLIST` -- the 15 ruled-in categories, one
  edit point; cbb re-admits here after its probe. Ingest stays all-categories, R5.)
- **Q5 -- LEADERBOARD BREADTH: accept ~50/bucket.** (R2 discovery pool; no offset-paging / extra buckets required.)
- **Build-verify (not a Jack decision):** `/closed-positions` newest-first is UNVERIFIED (the endpoint has no `sort`
  param -- confirmed at `fetch_closed_positions`). RUNG 1 does NOT assume it: `page_new_rows` ASSERTS it (intra-page
  + inter-page seam) and RAISES on violation. The live cross-offset confirmation is a **RUNG 2** precondition.

## 9B. RUNG 1 BUILD RECORD (2026-08-29). Build + box-scratch only; live untouched.
- **Files:** `trading_corp/prediction_markets/db.py` (migration 013 `pm_search_run` + `MIGRATIONS`/`SCHEMA_HEAD`->13),
  `trading_corp/prediction_markets/search.py` (NEW), `tests/prediction_markets/test_search_r1.py` (NEW).
- **Adversarial review (3 agents: watermark / selection / migration+tests) findings ALL folded in:**
  - **HIGH (would have failed box-scratch):** the R7-isolation test used a substring scan -- `"arm" in src`
    false-matches "farm" (search.py mentions "the /farm screen"). FIXED: the test now parses imports via AST and
    matches whole module names.
  - **CRITICAL (design):** `_assert_descending` checked only INTRA-page order, but an early stop is safe only under
    GLOBAL (across-page) descending order -- a stream descending within each 50-row page but inverted AT A PAGE SEAM
    would stop early and skip newer trades on a later page. FIXED in the pure layer: `page_new_rows` takes an optional
    `prev_min_ts` and asserts the seam; the residual (the stop halts before the next unfetched seam) is documented as
    a RUNG 2 obligation (proven-global-sort OR a confirm-horizon page). 
  - **MEDIUM:** an "unreadable ts" (None/0) row was silently DROPPED from `new_rows` in incremental mode (violates
    "prefer re-fetch over skip"). FIXED: unreadable-ts rows are re-included (idempotent upsert), and only a REAL
    `0 < ts < wm` row triggers the stop.
  - **MEDIUM:** missing migration round-trip tests (run_id autoincrement, counter defaults, `started_ts` NOT NULL).
    ADDED.
  - **LOW:** a NaN/inf `roi` bypassed the `None` guard and would scramble the sort. FIXED: `not math.isfinite(roi)`
    is excluded like `None`. Duplicate-`(wallet,category)` input documented as a SQL-source precondition.
  - Selection verdict: **no CRITICAL/HIGH** -- obeys all five rulings exactly; only the LOW robustness gaps above.
- **§H (the three-bases exit question -- which list did this change touch, did it keep completed/paper/live separate):**
  RUNG 1 writes **NOTHING** to any of the three bases. `search.py` is pure functions; migration 013 creates ONE empty
  provenance table (`pm_search_run`), which is none of the three bases (Prospect=`pm_category_stats`<-`pm_closed_position`
  `status='candidate'`; Watchlist=paper=`pm_paper_category_stats` `status='pinned'`; Live=P3 `pm_subdivision*`). No
  `pm_watchlist`/`pm_roster`/`pm_paper_*`/`pm_subdivision*` row is created or read. The candidate WRITE (into the
  completed-trade basis, `status='candidate'`, `active=1`) is RUNG 3, and it will keep the three separate exactly as
  every existing consumer does (the `AND active=1` gate; no auto-pin, no auto-attach). So RUNG 1's answer: it touched
  none of the three lists; separation is trivially preserved (build-only, empty new table).

## 9C. ★ BUILD-VERIFY RESULT (RUNG 1 box-scratch, 2026-08-29T22:34Z) -- `/closed-positions` is NOT newest-first.
The read-only probe (whale `0xa6a856a8c8...`, 17,056 stored positions; two live pages) returned:
`page0 descending=False, page1 descending=False, seam(page1[0]<=page0[-1])=False`. **So `/closed-positions` is
NOT resolution-time-sorted -- not even WITHIN a single 50-row page.** (The timestamps are populated and non-zero;
they are simply not in resolution order -- the endpoint sorts by something else.)

**CONSEQUENCE FOR RUNG 2 (decided by this evidence): stop-at-watermark is NOT usable -> RUNG 2 ALWAYS FULL-PAGES**
each wallet from offset 0 to a short/empty page (the plan-§3 fallback; ~30 calls/wallet, affordable). The rung-1
`page_new_rows` in INCREMENTAL mode would (correctly) raise `OutOfOrderPage` on the very first page here, so wiring
it incrementally would just thrash into the full-page fallback -- rung 2 should call it in FULL mode
(`backfill_complete=False` semantics / `watermark_ts=None`), i.e. keep every row, terminate on the short page. This
is EXACTLY why the code ASSERTS order rather than assuming it: a naive incremental impl would have silently SKIPPED
trades on this endpoint (the worst failure class). If a cheaper re-run is wanted later, the options are: sort the
full fetch client-side by resolved_ts (still a full fetch, no API saving), or find a recency-filtered endpoint
(`/activity` carries placed-time but truncates at 5k -- a Stage-5 dep). NONE of this changes rung 1; it is recorded
so rung 2 is built to the evidence, not the assumption.

## 9D. RULINGS 1 & 2 (2026-08-29) + the R2 API probe result. These reshape rung 2.
- **RULING 1 -- BACKFILL IS ON-DEMAND, not per-run.** A whale ALREADY IN THE DB (complete backfill) is added to
  results FROM the DB, NO pull ("last updated" = `pm_whale.last_refresh_ts`, VISIBLE not silent). A NEVER-SEEN
  whale gets ONE first-sight backfill on discovery (unavoidable -- no stats else). A REFRESH BUTTON does an
  ad-hoc full one-whale pull on demand. NO staleness threshold, NO forced refresh -- Jack's call always. Read
  as: backfill until COMPLETE once, then never automatically again (a prior partial/failed is re-attempted; a
  complete whale never is). This dissolves the full-page cost -- API calls only for genuinely new/incomplete
  wallets, and the ~30-call full page is paid once, on purpose.
- **★ THE R2 API PROBE (`cc\pm_stage4_r2_apiprobe.*`, 2026-08-29T22:58Z, read-only):** does `/closed-positions`
  honor a date/since param? **NO.** All 21 candidate param names (12 lower-bound `startTs/start/from/since/...`
  + 9 upper-bound `endTs/end/to/before/...`) returned the baseline 50 rows UNCHANGED. So `/closed-positions` has
  NO server-side time filter. Combined with §9C (not newest-first), incremental-at-source is impossible ->
  **FULL-PAGE backfill is the only path, CONFIRMED not assumed.** (Engine 76416 untouched.)
- **RULING 2 -- COLUMN SORT, not a fixed ranking (an R4 concern; recorded here).** The prospects table is
  column-sortable; default order = **cost-ROI desc, thin-sample flagged**. The sort data (roi, n_resolved,
  last-updated, thin_sample) already lives in `pm_category_stats`+`pm_whale`. **win% decision (recommended):
  do NOT make win% a sortable column** -- it is ROI-denominator-ruled-out for ranking AND loss-omission-
  optimistic, so a sortable win% invites the exact mislead F-1 guards; the F-1 caveat stays page-level
  regardless. Build in R4.

## 9E. RUNG 2 BUILD RECORD (2026-08-29). Build + box-scratch only; live untouched.
- **Files:** `trading_corp/prediction_markets/search_run.py` (NEW), `tests/prediction_markets/test_search_run_r2.py`
  (NEW). Reuses `ingest.backfill_wallet`/`refresh_wallet` (which already carry the `backfill_complete`
  completeness verdict) + `client.fetch_leaderboard` + the `pm_search_run` table (013). Imports NO order path.
- **Adversarial review (2 agents: silent-gap + orchestration) -- R2 WRITE SIDE AIRTIGHT:** a partial/failed
  backfill can NEVER stamp `backfill_complete=1` (verdict requires short-page AND pulled==stored); a
  mid-pagination hard-failure RAISES before any upsert/stamp (nothing half-written); per-wallet isolation; run
  accounting total; run row ALWAYS closed (added a `try/finally`). Folded in: refresh-on-never-seen test,
  refresh cap-truncate downgrade (1->0) test, refresh-raises-keeps-1 test, mid-pagination-raise test,
  partial-relisted-across-runs test.
- **★★ CRITICAL FINDING -> A DECISION FOR JACK (NOT fixed unilaterally):** the review found the SHARED, DEPLOYED
  ranker `stats.query_scoreboard` selects `backfill_complete` as a DISPLAY column but does NOT filter on it, so
  it RANKS partial-backfill whales on truncated `pm_category_stats` data (live via `pm_cli report`). Its own
  `scoreboard_flags` comment says partial backfill is "excluded from ranking" -- but the WHERE never excludes
  it (`compute_scores` DOES gate; `query_scoreboard` does NOT). This is an internal INCONSISTENCY in a shared
  Stage-1/2 subsystem used by 6 test files, and fixing it is a genuine design fork -- so it is NOT a
  build-rung-2 change. **Options for Jack:** (a) add `AND COALESCE(w.backfill_complete,0)=1` to
  query_scoreboard's WHERE (matches the stated intent + the `active`-gate pattern + `compute_scores`; makes the
  INCOMPLETE-NOT-RANKED flag dead; needs ~6 fixture files updated to seed a complete `pm_whale`); (b) keep
  show-flagged but BLANK the roi/win%/net columns for incomplete rows (preserves visibility, removes the
  misleading numbers); (c) leave as-is (the flag is deemed sufficient). **Recommend (a)** -- it makes code match
  its own comment and no caller can forget the gate. **Stage-4 is protected regardless:** the R3 candidate write
  gates `backfill_complete=1`, so a partial whale never becomes a prospect; the query_scoreboard gap's live
  exposure is the GENERAL `pm_cli report` board, a pre-existing Stage-1/2 concern R2 merely makes more likely.
- **§H (three-bases):** RUNG 2 writes to the COMPLETED-trade basis ONLY -- `pm_closed_position` (via backfill,
  the same table ingest always wrote) + a `pm_search_run` provenance row. It writes NOTHING to the paper
  (`pm_paper_*`, status='pinned') or live (`pm_subdivision*`) bases, and NO `pm_watchlist` candidate row (that
  is R3). The three lists stay separate; R2 only refreshes/extends the completed-trade evidence + records a run.

*Planning pass 2026-08-29 (§0-§9). Rulings + ladder + RUNG 1 (§8A/§9A/§9B/§9C) + Rulings 1&2 + RUNG 2
(§9D/§9E) 2026-08-29. Independent of R7 (order path untouched -- verified via AST on search.py + search_run.py).
Runners: `cc\pm_stage4_datagather_ro.*` (21:17Z) / `pm_stage4_r1_boxscratch.*` (22:34Z) / `pm_stage4_r2_apiprobe.*`
(22:58Z) / `pm_stage4_r2_boxscratch.*`.*
