# Prediction Markets — Vision Recovery + Full State Review

**Date:** 2026-08-26 · **Branch:** `prediction-markets-cp3b2-2026-08-25` @ `f4fb61d` · **Author:** review agent, read-only pass.
**Status of the build:** CP3b-1 + CP3b-2 deployed + ledgered (`origin/prod-live 95e78c4`); CP3b-3 Search and all stats work FROZEN pending this review and the loss-visibility ruling.
**Mode:** READ-ONLY. No code, no fixes, no commits. Facts are in §0–§9 (labeled); opinions are isolated in §10. Live counts are mode=ro-measured (dated below) or flagged for the appendix probe. Nothing is softened.

> **Why this document exists (context, not blame):** Jack's vision has not changed since it was locked 2026-08-21. What changed is that agent-handoff documents faithfully carried the *technical* state (schemas, SHAs, tests) and quietly dropped the *product* requirements — nobody knew the vision was theirs to carry. Every checkpoint was genuinely verified and the build drifted anyway. This document recovers the requirements from where they are written down and measures the build against them, so the gap can't reopen at the next handoff.

---

## §0. THE REQUIREMENTS, RECOVERED (the most important section)

**Finding up front: the vision is NOT lost. It is written down, in full, in two committed docs** — `reports/prediction_markets/PLATFORM_VISION.md` and `P1_PLAN.md` §2 — both sourced verbatim from Jack's 2026-08-21 requirements interview. Every element of Jack's description today is in them. The drift is not that the requirements were unrecorded; it is that later sessions **read these docs for migration numbers and checkpoint scope and skipped the product description in §2.** This section makes the product description the point, so a fresh agent could build to it.

### 0.1 The product requirements, quoted with file:line

**The screen hierarchy** (`PLATFORM_VISION.md:14-16`, identical at `P1_PLAN.md:25-27`):
- `:14` — "**PREDICTION MARKETS** = one main-page tile → dashboard of SUB-DIVISIONS viewed by sub-division (flat cards, e.g. Jack-MLB, Karen-MLB, Jack-UFC). Sub-division detail page: its whales + OPEN trades + CLOSED trades."
- `:15` — "**SUB-DIVISION** = an **(ACCOUNT, CATEGORY)** pair. One Kalshi-API account can own MULTIPLE sub-divisions … there is **NO person entity**."
- `:16` — "**FARM LEAGUE** = SEPARATE from divisions, organized by CATEGORY tabs. Each tab has TWO lists: (1) **CANDIDATES** = search results, does NOT paper-trade, but Analyze-able; (2) **PINNED PAPER LIST** = forward paper-trading, **ONE paper record per whale-category pair** (category-level, shared across all sub-divisions … not duplicated per sub-division)."

**The lifecycle** (`PLATFORM_VISION.md:18`): "search → category CANDIDATES → board review (ANALYZE button) → PIN to paper list (forward paper starts) → observe → PROMOTE (asks which sub-division(s) in that category: 1/some/all) → live; whale STAYS pinned while live … **CANNOT go candidate→live directly; pin is mandatory**."

**Analyze** (`PLATFORM_VISION.md:23`): "already LIVE on legacy PCT … Improve by wiring to `/closed-positions` for UNTRUNCATED full history. Runs on candidate AND pinned whales."

**Search** (`PLATFORM_VISION.md:25`): "per category tab, a button + basic filters runs a Polymarket-category search → adds to that tab's CANDIDATES. MUST include the two ranking routines already built … + net-scoring (**win% is chalk / rank on NET ROI** — hard-won scout lessons)."

**Data foundation** (`PLATFORM_VISION.md:27`): "`/closed-positions` is THE record-keeping backbone … Complete cross-category history, direct `realizedPnl`, per-market grain." **← This "Complete" claim is the one the loss-visibility finding (§5, §9) contradicts.**

**The P2 screen specs** (`P2_PLAN.md` §6) turn the vision into pages:
- `§6.1` Scoreboard `GET /` — `stats.query_scoreboard` rendered.
- `§6.2` Farm League `GET /farm (+ /farm/{category})` — "Category sub-tabs …, each with **two lists**: CANDIDATES (`pm_watchlist.status='candidate'` ⋈ stats) … PINNED PAPER LIST (**`pm_paper_category_stats` ⋈ pm_roster**): product columns **+ paper columns** … `n_stale` beside `n_resolved`."
- `§6.3` Whale detail `GET /whale/{wallet}[/{category}]`.
- `§6.4` Search `GET /search`.
- `§6.5` Diagnostics `GET /diagnostics`.

### 0.2 The three lists, each with its own data basis (Jack's ruling today; matches `PLATFORM_VISION.md:16`)

| List | Required data basis | Answers |
|---|---|---|
| **Farm-league PROSPECT** (= "candidate") | **Completed trades only** — the whale's own resolved Polymarket history (`pm_closed_position`). Does NOT paper-trade. | "Is this whale worth paper-trading?" |
| **WATCHLIST / PINNED** | **Our paper trades only** (`pm_paper_trade`). | "How is our paper copy of this whale doing?" |
| **LIVE** (attached to an Account-Category) | **Live trades only** (P3 tables, not yet built). | "How is the real money doing?" |

A single whale-category pair can appear on all three at once and **must show different numbers on each** because each answers a different question.

### 0.3 ⭐ Analyze is the point (Jack's ruling today)

"Rough prospect stats are a screen. ANALYZE is what the operator actually uses to decide promotion — passing the whale-category's detail to the LLM to establish the truth of that pair's track record. **A defect in what Analyze is FED matters more than an imprecise number on a screening list.**" → Weight the whole review toward *what Analyze reads*, not its prose. This is why §5 and the loss-visibility finding dominate the risk picture.

### 0.4 Reconciliation against Jack's description today — three buckets

**(a) IN THE PLANS AND CONSISTENT with what Jack said today:**
- The hierarchy: main dashboard of Account-Category tiles → sub-division detail (live + closed trades). `PLATFORM_VISION.md:14-15`.
- Farm League as a separate menu option, category tiles, per-category page with pinned on top + prospects below. `PLATFORM_VISION.md:16`.
- Pinned whales paper-trade; prospects do not and only have completed-trade-API stats. `PLATFORM_VISION.md:16`.
- Analyze on both lists to decide promotion. `PLATFORM_VISION.md:23`, `:25`.
- The three data bases (completed / paper / live). `PLATFORM_VISION.md:16` + today's ruling.
- Clicking a whale → detail page (paper trades for pinned; closed trades for prospects). `PLATFORM_VISION.md:14`.

**(b) IN THE PLANS BUT CONTRADICTED by / in tension with what Jack said today — flag, do not resolve:**
- **VOCABULARY KNOT (three-way).** Jack today: prospects → button "**Promote to watchlist**" → "watchlist … begin paper trading" — i.e. Jack's "watchlist" = the PINNED paper list, and "prospects" = candidates. `P1_PLAN.md:27` calls the search-results list "**WATCHLIST**". `PLATFORM_VISION.md:16` (CP3b-0 rename) calls it "**CANDIDATES**" and the paper list "PINNED PAPER LIST". The shipped code uses `status='candidate'|'pinned'` and a table still named **`pm_watchlist`**. So "watchlist" means *the paper/pinned list* to Jack, *the search list* in P1_PLAN, and *a table holding both* in code. Not resolvable by an agent — needs Jack's word on the canonical noun for each list.
- **Farm tiles = "Kalshi-copyable categories" (today) vs "all 18 categories paper-trade" (CP3a ruling).** Jack today: "the category only tiles … are the **Kalshi copyable** Polymarket categories." But the CP3a Ruling-B seeding pinned **all 18 categories** (incl. cs2/atp/wta/unknown), which are unlikely to be Kalshi-copyable. Either the tile set is narrower than the pinned set, or one ruling moves. (Also in §9.)
- **"Complete cross-category history"** (`PLATFORM_VISION.md:27`) vs the **measured loss-omission** (§5): the foundation doc asserts completeness; the data is complete on wins and short on some whales' losses. Not a wording fix — a foundation question for Jack.

**(c) SAID TODAY BUT NOT IN ANY PLAN — the at-risk requirements, written down here so they survive:**
- **Prospect stats basis is explicitly "the completed trade api" and nothing else** — today's wording nails the prospect list to `pm_closed_position` with no paper mixed in. The plans imply it; today states it flatly. Record it.
- **The three lists can co-exist for one pair and MUST show different numbers** — stated as a hard ruling today; the plans describe three lists but never say "same pair, three views, three numbers, simultaneously." This is the requirement most likely to be lost, because the current build has only one number per pair.
- **"Analyze is the point; a defect in what Analyze is FED matters more than a screen number"** — a prioritization ruling, nowhere in the plans. It reorders the whole backlog and belongs in the durable record.
- **Buttons enumerated per list:** Demote + Promote + Analyze on **pinned**; Promote-to-watchlist on **prospects**. The plans mention promote/analyze; today gives the exact per-list button set (see §1).

---

## §1. NAVIGATION & SCREEN STRUCTURE — DESIGNED vs BUILT

**The core gap.** The requirement is a **hierarchy**; the build is **flat**.

```
REQUIRED (PLATFORM_VISION.md:14-16)              BUILT (deployed, prod-live 95e78c4)
────────────────────────────────────            ──────────────────────────────────
Main PM Dashboard                                (does not exist)
  └─ Sub-division tiles (Account-Category)       (does not exist — P3)
       └─ Sub-division detail                    (does not exist — P3)
            (its whales + OPEN + CLOSED trades)
  └─ Farm League (menu option)                   nav link "Farm league" -> /farm  [BUILT]
       └─ Category tiles                          category TABS (18) as a filter bar [SHAPE DIFFERS: tabs not tiles]
            └─ Per-category page:                  /farm?category=X  [ONE flat page, category is a filter]
                 - Pinned (top)  paper stats        "Pinned" table — renders COMPLETED-trade stats, NOT paper (§4) [BASIS WRONG]
                 - Prospects (bottom) closed stats   "Candidates" list — empty until Search [BUILT, EMPTY]
(no such screen in the vision)                    /  and /scoreboard — flat whale ranking across categories [EXTRA — see below]
```

**Screen-by-screen:**

| Screen | Built? | Shows | Requirement says |
|---|---|---|---|
| `GET /` + `GET /scoreboard` | **BUILT, DEPLOYED** | Flat, whale-centric ranked board across all categories (`stats.query_scoreboard`): rank, wallet/name, category, score, win%, cost-ROI, net PnL, caveats. Controls: category dropdown, routine toggle, min_resolved. | **No top-level scoreboard exists in the vision.** The vision's only two top-level menu items are the sub-division dashboard and Farm League (`PLATFORM_VISION.md:14,16`). |
| `GET /farm (+ /farm/list)` | **BUILT, DEPLOYED** | Category **tabs** (18, data-driven) + a **Pinned** table (114 pairs, three-state poll badge, completed-trade stats + an open-paper *count*) + a **Candidates** table (empty; "No search has run yet"). Per-row **[Analyze]** button. | Category **tiles** → a per-category page with pinned-**paper** on top and prospects (closed-trade stats) below. Nav shape is close (category-scoped); **data basis and actions are not** (§4, actions below). |
| `GET /whale/{w}` + `/{w}/{c}` + `/positions` | **BUILT, DEPLOYED** | Whale drill-through: score decomposition, caveat profile, `pm_position_rows` drill (scoreable/won/two_sided/quarantined/all), live reconcile banner. | The vision's whale detail: for a **prospect** → all their **closed** trades; for a **pinned** whale → all their **paper** trades. Built version shows closed-position drills only; **no paper-trade detail view exists** (paper table exists, no page renders it). |
| `POST /farm/analyze/{w}/{c}` | **BUILT, DEPLOYED** | Analyze result partial (verdict-or-reasoned-null + deterministic report). | ✓ matches "Analyze on candidate AND pinned." Correct surface; its **input** is the concern (§5). |
| Main PM dashboard / sub-division tiles / sub-division detail | **NOT BUILT** | — | Required top of the hierarchy. This is **P3** (`PLATFORM_VISION.md:31`), legitimately not built yet — but it means the *entry point* of Jack's mental model does not exist, which is why the flat `/farm`+`/scoreboard` reads as the whole app. |
| `GET /search`, `GET /diagnostics` | **NOT BUILT** | — | P2_PLAN §6.4/§6.5; parked. Search is CP3b-3 (blocked). |

**Is `/scoreboard` the prospect list in the wrong shape, or work that doesn't belong?** — Opinion (see §10 for the full argument): it is **P1's data contract (`query_scoreboard`) promoted to a page it was never speced as.** It is not any of the three required lists — it ranks *pinned* whales' *completed-trade* stats flat across categories, which is closest to a "prospect ranking" but is applied to the *pinned* set and shows *completed* (not paper) numbers. It is the most visible symptom of the flat-vs-hierarchy drift.

**ACTIONS — designed vs built:**

| Action | On which list (requirement) | Built? |
|---|---|---|
| **Analyze** | pinned + prospects | **BUILT** (`/farm/analyze/{w}/{c}`, both lists, same path) |
| **Promote** (whale-category → sub-division) | pinned | **NOT BUILT** (P3) |
| **Demote** | pinned | **NOT BUILT** |
| **Promote-to-watchlist** (prospect → pinned/paper) | prospects | **NOT BUILT** — this is the pin action; there is **no way in the UI to move a whale from Candidates to Pinned.** The 114 pinned pairs were seeded by a one-shot CLI (`pm_cli migrate-roster`), not by this button. |

**Only Analyze is built. The three lifecycle actions that make the farm a workflow (pin/promote/demote) do not exist in the UI.**

---

## §2. CODE INVENTORY (PM platform only)

All files below are on `prediction-markets-cp3b2-2026-08-25 @ f4fb61d`. DEPLOYED = on the box + in `origin/prod-live 95e78c4`. Line counts measured 2026-08-26.

**Runtime package `trading_corp/prediction_markets/` (DEPLOYED unless noted):**

| File | LOC | Purpose | State |
|---|---|---|---|
| `db.py` | 524 | DB path/connect (WAL), migrations 001–007, `schema_version`, the ONE `scoreable_where()` predicate | DEPLOYED |
| `ingest.py` | 329 | `/closed-positions` backfill/refresh, `/positions` refresh, §3A quarantine at ingest, category derivation | DEPLOYED |
| `category.py` | 150 | slug-prefix → category + gamma tag-join for unknowns | DEPLOYED |
| `stats.py` | 352 | `rollup()` → `pm_category_stats` (+ one-sided companion), 2 ranking routines, `query_scoreboard`, `scoreboard_flags` | DEPLOYED |
| `positions.py` | 135 | whale-detail drill reads + `reconcile()` (CP2 Phase 3) | DEPLOYED |
| `names.py` | 108 | `sync_user_names` (roster labels → `pm_whale.user_name`), `pm_meta` | DEPLOYED |
| `paper.py` | 384 | paper-farm poller + two-phase adjudicator (CP3a); `pm_paper_trade` lifecycle | DEPLOYED (code); poller/adjudicator run manually only |
| `rosters.py` | 82 | READ-ONLY legacy `agent_state` roster loads + `G0_KNOWN_LOSERS` | DEPLOYED |
| `farm.py` | 109 | farm-league READ-ONLY queries (CP3b-1): three-state poll, data-driven tabs, `farm_rows`/`farm_summary` | DEPLOYED |
| `analyze.py` | 504 | on-demand Analyze fork (CP3b-2): deterministic report from `pm_closed_position` + Haiku narrator + cost ledger + cache | DEPLOYED |
| `web/app.py` | 240 | pm_web FastAPI: `/healthz`, `/`, `/scoreboard`, `/whale/*`, `/farm*`, `/farm/analyze` | DEPLOYED |
| `web/static/pm.css` | 240 | hand-authored dark theme | DEPLOYED |
| `web/static/htmx.min.js` | (vendored) | HTMX | DEPLOYED |
| `web/templates/*` | — | `pm_base` 38, `pm_macros` 66, `pm_scoreboard` 45 + `partials/pm_scoreboard_table` 65, `pm_whale` 131 + `pm_whale_overview` 40 + `partials/pm_position_rows` 74, `pm_farm` 40 + `partials/pm_farm_lists` 113, `partials/pm_analyze_result` 154 | DEPLOYED |
| `scripts/pm_cli.py` | 225 | CLI: g0-validate, backfill, refresh, rollup, repair-categories, report, sync-names, paper-poll, paper-adjudicate, migrate-roster, **analyze** | **BUILT-NOT-DEPLOYED** — the `analyze` subcommand is on the branch but **excluded from Gate 2** (Q3 ruling); the box still runs the pre-CP3b-2 `pm_cli.py` |
| `scripts/pm_web.py` | 18 | uvicorn launcher | DEPLOYED |

**Tests `tests/prediction_markets/` (19 files, ~2,400 LOC, run offline; not deployed):** test_analyze 243, test_paper 361, test_ingest 167, test_integrity 167, test_scoreboard_render 164, test_db 146, test_stats 127, test_whale_detail 148, test_caveat_analytics 144, test_farm 143, test_drill_reconcile 116, test_names 109, test_category 96, test_cli 53, test_rosters 57, test_ranking 54, test_fixtures 48, test_web_healthz 36, test_smoke_live 20.

**Legacy files PM FORKED FROM (read-only reference, NOT edited — `DO NOT TOUCH LEGACY`):** `data/polymarket_data_api_client.py` (imported for `fetch_closed_positions`/`fetch_activity`/`fetch_market_resolutions`), `data/kalshi_whale_stats.py` (Wilson/edge/time-weight primitives, imported by `stats.py`), `agents/polymarket_whale_analyst.py` + `data/polymarket_whale_audit.py` + `agents/research/polymarket_whale_audit_cache.py` (Analyze **forked** into `analyze.py`, not imported), `scripts/seed_polymarket_watchlist_deep.py` + `scripts/refresh_polymarket_whales.py` (the scout — Search will fork, not yet built).

**Nobody-looked-at-recently flags:** `test_smoke_live.py` (20 LOC — a live smoke test, unclear if run), `positions.py`'s `reconcile()` (CP2 Phase 3, exercised only by the whale page).

---

## §3. DATA MODEL — from the live DB (schema 7)

DDL from `db.py` (migrations 001–007). Row counts are **mode=ro-measured on the live DB 2026-08-26** where marked ✓; a few are flagged `[probe]` and can be filled by the appendix runner (§ appendix). Live schema_version = **7** (confirmed at Gate 1 and Gate 2 healthz today).

| Table | Migr | Real rows | Who WRITES | Who READS | Feeds which list |
|---|---|---|---|---|---|
| `schema_version` | 001 | 7 ✓ | `init_db` | healthz | — |
| `pm_whale` | 001/003 | ~14 `[probe]` | ingest backfill; `names.sync_user_names` | scoreboard/farm/analyze (name, backfill_complete) | all |
| `pm_closed_position` | 001/002 | **29,741 ✓** | `ingest` (from `/closed-positions`) | `stats.rollup`, `analyze`, whale drills | **PROSPECT (completed) — and, wrongly, PINNED (§4)** |
| `pm_category_stats` | 001/004 | ~ per (wallet,category) `[probe]` | `stats.rollup` (weekly) | scoreboard, farm, whale detail, **analyze reconcile** | feeds the PINNED + scoreboard views (completed-trade stats) |
| `pm_category_onesided_stats` | 004 | `[probe]` | `stats._rollup_onesided` | scoreboard/farm/whale (one-sided ROI, UPPER BOUND) | same |
| `pm_open_position` | 001/002 | `[probe]` (from `/positions`; may be stale/empty) | `ingest.refresh_open_positions` | whale detail (live mark) | — |
| `pm_score_snapshot` | 001 | `[probe]` (scored pairs, backfill_complete only) | `stats.compute_scores` | scoreboard/farm ranking | scoreboard |
| `pm_paper_trade` | 005 | **102 ✓ (all `status='open'`)** | `paper.poll_pinned` (poller); `paper.adjudicate` | farm open-count only | **WATCHLIST/PINNED (paper) — but NOT yet surfaced as stats (§4)** |
| `pm_paper_config` | 005 | 3 (config defaults) | migration seed | `paper.get_config` | — |
| `pm_roster` | 006 | **114 ✓** | `paper.seed_farm_roster` (`migrate-roster`) | poller, farm (last_polled_ts), refresh subset | pinned roster |
| `pm_watchlist` | 006 | **114 ✓ (all `status='pinned'`)** | `migrate-roster`; (Search would write `candidate`) | farm (base table) | PINNED (114) + CANDIDATES (0) |
| `pm_analysis_cache` | 007 | **0 ✓** | `analyze.analyze_whale` (successful verdict only) | analyze (cache hit) | analyze |
| `pm_analysis_cost` | 007 | **0 ✓** | `analyze` ($20/day ledger) | analyze cap check | analyze |
| `pm_meta` | (outside chain) | 1 `[probe]` | `names.sync_user_names` | names.last_sync | — |

**Empty and why:** `pm_analysis_cache`/`pm_analysis_cost` = 0 (Analyze always returns `llm_unavailable`, key unwired — nulls are not cached, nothing spends). `pm_watchlist` candidates = 0 (Search never ran — the only source of `status='candidate'` rows). `pm_paper_trade` = 102 all `open` (poller ran once at CP3a Gate 3; the adjudicator has never run, so nothing has reached `closed`/`stale`).

**Named-but-never-built** (P1_PLAN.md:166, P2_PLAN): `pm_farm` (superseded by `pm_watchlist`+`pm_roster`), **`pm_paper_category_stats`** (the paper rollup the farm's PINNED list was speced to read — never migrated), `pm_search_run`, `pm_account`/`pm_sub_division`/`pm_promotion`/`pm_copy_trade` (P3), `pm_user`/`pm_role`/`pm_grant` (auth).

---

## §4. THE THREE DATA BASES — DESIGNED vs BUILT

| List | Required basis | Actual basis in code | Gap |
|---|---|---|---|
| **PROSPECT (candidate)** | completed trades (`pm_closed_position`) | `farm.farm_rows(status='candidate')` LEFT JOINs `pm_category_stats` (a completed-trade rollup) | **Basis correct**, but the list is **empty** (no Search). When populated it will read completed-trade stats — correct. |
| **PINNED** | **our paper trades** (`pm_paper_trade`) | `farm.farm_rows(status='pinned')` renders **`pm_category_stats` = COMPLETED-trade stats** (win_rate, cost-ROI, net PnL, avg_win_price) + only an **open-paper COUNT** from `pm_paper_trade`. | **★ THE HEADLINE DISCONNECT.** The 114 pinned pairs show the whale's *own completed Polymarket record*, not *our paper copy's* performance. `P2_PLAN.md §6.2` speced the PINNED list to read **`pm_paper_category_stats`** — a paper rollup table that was **never built**. With no paper-stats table, the farm fell back to the completed-trade rollup. So the pinned list answers the *prospect* question with the *prospect* data, mislabeled as pinned. |
| **LIVE** | live trades (P3 tables) | — | Not built (P3). Correctly absent. |

**Every disconnect of this kind found:**
1. **PINNED shows completed-trade stats, not paper** (above) — the load-bearing one.
2. **`pm_paper_category_stats` does not exist** — the paper rollup that would make (1) correct was deferred and never renumbered/built (CP3b kickoff contamination gate B). Until it exists, the pinned list *cannot* show paper stats.
3. **The paper record itself is thin:** `pm_paper_trade` has 102 rows, **all `open`** — no `closed`/`stale` exist because the adjudicator has never run. So even a correct paper-stats table would currently be **vacuous** (no realized paper P&L to show). The paper basis is not just unwired — it has no resolved data yet.
4. **`/scoreboard` and `/` rank the PINNED set on completed-trade stats** — same basis error as (1), surfaced as a second, flatter screen.
5. **Whale detail** renders `pm_closed_position` drills for pinned whales; there is **no paper-trade detail page** (the vision's "clicking a pinned whale → all their paper trades").

---

## §5. STAT LINEAGE — every displayed number → its source

One table. "Basis" ties to §4. **Every row sourced from `pm_closed_position` carries the loss-visibility caveat (measured, below).**

| Number (UI label) | Source table.column | Derivation | Caveats / biases |
|---|---|---|---|
| `n` / n_resolved | `pm_category_stats.n_resolved` | COUNT of scoreable rows (`pnl_suspect=0`) per (wallet,cat) | Excludes §3A-quarantined rows. **Loss-visibility: undercounts held losses omitted by the endpoint.** |
| `win%` | `pm_category_stats.win_rate` | wins/(wins+losses), scoreable | Labeled "chalk indicator, never rank key". **Loss-visibility: OVERSTATED for loss-short whales** (evanng stored 77% vs ~52% truth). |
| **`roi (cost)`** (ranked) | `pm_category_stats.roi` | `SUM(net_realized_pnl)/SUM(cost_basis)`, `cost_basis=total_bought*avg_price` | The RANKED metric (§7 dec 11). **Loss-visibility: net_realized_pnl is inflated when losses are dropped → ROI overstated.** |
| `roi (notional)` | `pm_category_stats.roi_notional` | net/total_bought | Not ranked; total_bought is NOTIONAL not cost (~-45% understatement, universal). Legacy/scout comparison only. |
| `net pnl` | `pm_category_stats.net_realized_pnl` | SUM(realized_pnl) scoreable | **Loss-visibility: inflated (missing negative rows).** Also §3A: negRisk attribution excluded. |
| `avg win px` | `pm_category_stats.avg_win_price` | AVG(avg_price) on scoreable wins | Chalk ≥0.85 / contested <0.70. Only wins → **unaffected by loss omission**, but that's exactly why the board reads "chalky." |
| `1-sided roi ↑bound` | `pm_category_onesided_stats.roi` | net/cost on condition_ids held one-sided | Labeled UPPER BOUND (survivorship, §13A(f)) + loss-visibility on top. |
| `two-sided %` | `pm_category_stats.two_sided_pct` | distinct-oi>1 cids / cids | Structural (all rows), so less loss-sensitive. |
| `score` | `pm_score_snapshot.score` | `wilson_lcb_95(wins,n) × _edge_factor(roi)` (net_roi) or recency-weighted | Inherits win% + roi biases → **overstated for loss-short whales.** |
| `data_quality CONTAMINATED` | `pm_category_stats.data_quality` | set if `n_excluded/(n+n_excl) > 0.10` OR $-fraction | §3A quarantine visibility. **Does NOT catch loss omission** (omitted losses are absent, not quarantined). |
| poll state / `n open` | `pm_paper_trade` (count `status='open'`) | three-state (never/none/open) | The ONLY paper-sourced number on the farm today. |
| Analyze deterministic report | `pm_closed_position` (fresh aggregate) | same predicate + formulas as `stats.rollup` | **★ Analyze is FED the loss-omitted foundation** — §0.3 makes this the highest-priority defect. |
| Analyze verdict | Haiku over the above | LLM narration | Always `llm_unavailable` today (key unwired). When wired, it will narrate loss-omitted inputs. |

**★ Loss-visibility finding — MEASURED (board-authorized read-only probe 2026-08-26):** for **evanng** (both `/activity` and `/closed-positions` COMPLETE — no truncation), the whale's **89 held-to-resolution losses** (gamma-confirmed) appear as only **33 rows in `/closed-positions`**; **58 of the 58 decisions present in `/activity`-held but absent from `/closed-positions` are losses (0 wins)**; wins are ~complete. `pm_closed_position` stores the loss-blind version verbatim (145 rows / 33 losses), so evanng reads at **77% win-rate when the held truth is ~52%**. Across the 5-wallet sample, 4/5 had an omitted-set that was ~100% losses (d1k21 the wallet-dependent exception). **This is MEASURED.** The candidate mechanism — `/closed-positions` may only row positions with a redeem/claim or a partial-sell realizedPnl, so a loss **held to $0 with no claim** generates no row — is an **UNTESTED HYPOTHESIS**. The measured finding stands regardless of mechanism; §3A quarantine cannot fix it (the losses are absent, not present-and-wrong). Full method + numbers: `pm-closed-positions-loss-omission-2026-08-26` (memory) + probe `runners/pm_loss_visibility_probe.sh`.

---

## §6. DUPLICATION & OVERLAP

**Whale-category stats — how many programs compute them, and which are live:**

| Program | Produces | Basis | Still called? |
|---|---|---|---|
| `prediction_markets/stats.py` (`rollup`, 2 routines) | `pm_category_stats` + `pm_score_snapshot` | `pm_closed_position` (completed) | **LIVE** — the PM foundation |
| `prediction_markets/analyze.py` (`build_pm_analysis`) | on-demand per-pair report | `pm_closed_position`, fresh | **LIVE** (CP3b-2) — deliberately re-aggregates via the same predicate (reconciles to `pm_category_stats`) |
| legacy `data/polymarket_whale_stats.py` (`compute_polymarket_stats`, `score_polymarket_whale`, `score_whale_from_audit`) | `WhaleStats` / audit scores | `/activity` fills (REDEEM-grounded) | legacy-only (PCT); PM does **not** call it (P1_PLAN.md:149 impedance mismatch) — **dead to PM** |
| legacy `scripts/seed_polymarket_watchlist_deep.py` (4-floor gate) | `agent_state.watch_only_whales` | `/activity` windowed | legacy weekly timer `trading-corp-pm-watchlist-deep.service`; **PM will FORK it for Search, not call it** |
| legacy `scripts/refresh_polymarket_whales.py` (Rule B) | `agent_state.selected_whales` | `/activity` audit | legacy-only; pins-only default |

**Overlap:** three different "whale stats" lineages exist — PM's `stats.py` (closed-positions basis), the legacy `/activity` audit basis, and Analyze's fresh re-aggregate. They intentionally **do not share code** (fork ruling). The dead-to-PM ones are the legacy `polymarket_whale_stats.py` scorers and both scout scripts — **PM reads them only as fork sources.**

**STATS-FIX HISTORY — every recorded stat-calc change** (from `reports/prediction_markets/`):
1. **G0** (`G0_RESULT.md`) — proved negative rows exist (survivorship not naive-zero). *Bug faced: does `/closed-positions` even carry losses.*
2. **negRisk quarantine** (`REALIZEDPNL_PROBE_RESULT.md`, `P1_PLAN.md §3A`) — `realizedPnl` decoupled from cost on negRisk winner-take-all → `pnl_suspect`. *Bug: event-level attribution inflates/tanks net.*
3. **Quarantine reconcile** (`QUARANTINE_RECONCILE_2026-08-22.md`) — clause (a) false-positives on ordinary MLB losses → demoted to `pnl_anomaly`. *Bug: the loss-detector flagged real losses.*
4. **ROI denominator** (`ROI_DENOMINATOR_FINDING_2026-08-22.md`) — `total_bought` is NOTIONAL not cost → cost-based ROI, re-orders whales. *Bug: the ROI denominator was wrong, and wrong non-uniformly.*
5. **Two-sided metric** (`TWO_SIDED_METRIC_RECONCILE_2026-08-23.md`) — hedge/MM tell grain. 
6. **Net verify / Step 5** (`NET_VERIFY_TARGET.md`, `STEP5_REPORT.md`) — reconcile net to the cent vs independent recompute.
7. **Farm re-rank** (`FARM_RERANK_2026-08-23.md`) — one-sided directional slice; scout shortlists SUPERSEDED.
8. **Loss-visibility** (today) — `/closed-positions` omits held losses. *Bug: the foundation is missing loss rows entirely.*

**Were these the same bug found repeatedly, or genuinely different?** — Facts: (1)–(2)–(3) are all about **negRisk realizedPnl semantics**; (4) is a **separate** denominator error; (8) is a **third, deeper** class (missing rows, not wrong rows). Opinion on the pattern is in §10 (short answer: one root cause — an unreliable upstream endpoint — with several distinct failure surfaces).

---

## §7. THE PAPER TRAIL — what is still true

30 docs in `reports/prediction_markets/`. Classification (CURRENT / PARTLY STALE / SUPERSEDED) + discrepancies:

**CURRENT (durable, still true):** `PLATFORM_VISION.md` (the requirements — §0), `P1_PLAN.md` §2 vision (product), `CP3A_COMPLETE.md`/`CP3A_DEPLOY_COMPLETE.md`, `CP3B_DEPLOY_COMPLETE.md` (today's deploy), `OPS_GOTCHAS.md`, `MLB_TOTALS_COPYABILITY_2026-08-24.md`, `EVENTS_TAG_SCHEMA.md`, `KALSHI_403_SCOPE_2026-08-24.md`, `CP3A_CONTAMINATION_GATE.md`.

**PARTLY STALE:** `P1_PLAN.md` (§3A assurances self-flagged SUPERSEDED at :78-82; §10 "test locally" broken — no local Python on Windows), `P2_PLAN.md` (§5.4/§7.4 amended in CP3b-2; §5.2 paper lifecycle is pre-CP3a; §6.2 PINNED reads a table never built; §7.3 has the rank-before-backfill bug), `DEPLOY_SEQUENCE.md`, the four `TRANSITION_TO_*` handoffs (technical state current, **product requirements dropped — the exact drift this review exists to fix**).

**SUPERSEDED:** the scout-shortlist numbers inside `FARM_RERANK`/`POSTP1_ITEMS`/`STEP5` are superseded by the P1 scoreboard (ROI-denominator fix), and now **the P1 scoreboard itself is caveated by the loss-visibility finding**; `SDT_MLB_DRILL_INVESTIGATION`, `NET_VERIFY_TARGET` are point-in-time.

**Doc-vs-code / doc-vs-doc discrepancies (all of them, incl. small):**
1. `P2_PLAN §5.4` cache DDL (`input_hash`+`activity_max_ts`) vs shipped `(wallet,category,skill_version)` — amended in `db.py` migration 007 header. 
2. `P2_PLAN §7.4` cap "$2" vs code default $1 vs ruled $20 — amended.
3. `P2_PLAN §7.4/§7.3` Search "rank candidates with the two routines … then backfill" — **circular** (routines read `pm_category_stats` which requires the backfill first). Unbuilt.
4. `P2_PLAN §5.2` paper lifecycle (`/activity` BUY entry + direct-stale) vs shipped `/positions`-observation + two-phase `pending_adjudication`.
5. `P2_PLAN §6.2` PINNED PAPER LIST reads `pm_paper_category_stats` — table never built; farm reads `pm_category_stats` instead (§4).
6. `migration 006` `pm_watchlist … DEFAULT 'watchlist'` (vestigial) vs vocab `'candidate'|'pinned'` (documented immutable in `db.py`).
7. `P1_PLAN §2` "WATCHLIST" (search list) vs `PLATFORM_VISION.md` "CANDIDATES" vs Jack's "watchlist"=pinned — the vocabulary knot (§0b).
8. `/scoreboard` page exists but is in **no** vision screen list (§1).
9. `P1_PLAN §10` "full test suite green **locally** before box contact" — impossible (no local Python; box-scratch harness required).
10. `P1_PLAN §2:27` / `PLATFORM_VISION:16` "**Complete** cross-category history" vs measured loss omission (§5).
11. `pm_cli.py` has an `analyze` subcommand on the branch that is **not** on the box (excluded from Gate 2) — doc/deploy mismatch to watch.

---

## §8. WHAT IS LEFT TO BUILD (honest, dependency-ordered)

**Blocking everything (foundation):**
- **F0. Resolve the loss-visibility finding** (§5, §9). Until the foundation's honesty is known, every stat and every Analyze input is suspect. This is *prior to* all UI work. Options span "test the mechanism → accept + caveat" to "re-source losses from `/activity` REDEEM-grounding" (which reopens the truncation problem the platform moved *away* from). **Nobody has scoped this; it may invalidate the "closed-positions is THE backbone" decision (`PLATFORM_VISION.md:27`).**

**P2 completion (farm reaches its spec):**
- The **paper basis** for PINNED: build `pm_paper_category_stats` (the deferred rollup) **and** run the adjudicator so paper trades resolve — *both* are required or the pinned list stays completed-stats or vacuous (§4). Depends on the poller/adjudicator chain (needs a poller re-run first — parked).
- **Pin / Promote-to-watchlist / Demote actions** (§1) — the farm is a read-only board today; the lifecycle verbs don't exist.
- **CP3b-3 Search** — blocked on (a) the "trackable" definition and (b) the rank-before-backfill circularity, and now (c) whether it should rank on a foundation with the loss hole.
- **Whale paper-detail page** (pinned → their paper trades).

**Navigation (close the hierarchy gap):**
- Main PM dashboard of sub-division tiles → sub-division detail. This is **P3** and legitimately last — but it is *the entry point of Jack's model*, so the flat `/farm`+`/scoreboard` should be understood as un-topped scaffolding, not the finished shape.

**P3 (the money layer):** sub-divisions, account/API mgmt, category-filtered copy, shared execution engine + per-sub-division config, promote/remove, multi-user auth (admin + PM-viewer). Not started (`P3_KICKOFF_2026-08-24.md` exists as a stub).

**Candidate for DISCARD (see §10):** `/scoreboard` as a standalone top-level page — it is not a required screen and its basis-error duplicates the farm's. Whether to delete it or refit it as the in-category prospect ranking is a Jack call.

---

## §9. OPEN QUESTIONS & UNRESOLVED DECISIONS

| # | Question | Status | Blocks |
|---|---|---|---|
| Q1 | **Loss-visibility:** is `/closed-positions` acceptably honest, or must losses be re-sourced? Test the redeem-vs-held-to-zero mechanism? | MEASURED (evanng clean); mechanism UNTESTED | The entire scoreboard, the 114-pair basis, every Analyze input. **Everything.** |
| Q2 | **"Trackable whale" definition** — undefined in code/plans; the legacy's only concrete rule is the 4 calibrated floors (n≥10 / recency≤60d / WR≥0.62 / PnL≥$5,000, the last hand-calibrated to size ~155). Keep it? Redefine? | Jack's ruling pending | CP3b-3 Search selection |
| Q3 | **Search rank-before-backfill circularity** — discovery-time inline compute (legacy style) or backfill-first-then-rank? | Jack's ruling pending | CP3b-3 Search |
| Q4 | **Farm tiles = "Kalshi-copyable categories" vs all-18-paper-trade ruling** — narrower tile set, or revisit the all-categories pin? | (b)-bucket contradiction | Farm tile set; Search scope |
| Q5 | **Vocabulary** — canonical noun per list (prospect/candidate/watchlist/pinned) given the three-way knot | (b)-bucket | UI labels, docs, future handoffs |
| Q6 | **PINNED shows completed-trade stats not paper** — accept as interim, or is fixing it (build `pm_paper_category_stats` + adjudicate) the next priority over Search? | measured (§4) | Whether the pinned list means what it says |
| Q7 | **§3A clause (a) rework** — demoted to `pnl_anomaly` (false-positived real MLB losses); event-group propagation scope | recorded, not acted | scoreboard trust (separate from Q1) |
| Q8 | **Adjudicator chain** — needs a poller re-run first (writes live PM DB); parked for a calm window | parked | any paper stats |
| Q9 | **KV wiring for Analyze** (e3) — every verdict is `llm_unavailable` until wired; capability≠token | Jack's hands | Analyze producing verdicts |
| Q10 | **poly_kalshi_mlb HWM-on-403** (NOT PM's; Jack handling) | recorded | — |
| Q11 | **`/scoreboard` — keep, delete, or refit?** | opinion in §10 | nav clarity |

---

## §10. OPINIONS (clearly marked — mine, not fact)

**Biggest structural problem.** The build has excellent *engineering discipline* on an *unmarked map*. Every checkpoint is verified; the checkpoints were pointed at a flat scoreboard/farm that isn't the product. The single biggest structural problem is that **the pinned list shows the wrong data basis** (completed-trade stats where paper is required) *and nobody noticed for three checkpoints*, because the requirement that "the same pair shows three different numbers on three lists" was never in the code's face — it lived in §2 of a plan doc that later sessions mined for schema. The flat-vs-hierarchy nav gap is the visible symptom; the basis error is the load-bearing one.

**⭐ Why does every session find a stats problem?** — It is a **real pattern with a single root cause, not unrelated bugs.** The root cause is that **`/closed-positions` is a convenience endpoint being used as a system of record, and it is unreliable in three independent ways:** (1) `total_bought` is notional, not cost (denominator wrong); (2) `realizedPnl` is event-decoupled on negRisk (attribution wrong); (3) it omits held losses for some whales (rows *missing*). Fixes (1)–(2) were genuine and are done. But **no amount of recomputation fixes (3)** — you cannot average your way back to rows that aren't there. So the honest answer to "why does every session find a stats problem" is: **the sessions keep finding new faces of one fact — the upstream source is not a ledger, and treating it as one guarantees the next session finds the next face.** The platform's founding decision ("`/closed-positions` is THE backbone") is the thing actually in question, and until that is confronted, this pattern recurs by construction.

**What I would delete.** (1) `/scoreboard` as a top-level page — it is not a required screen, and it re-commits the pinned-basis error in a flatter form; refit its ranking *inside* the per-category prospect section or delete it. (2) The `roi_notional` column from the *product* UI (keep it in diagnostics) — it exists only for scout comparison and adds a second ROI number that invites misreading. (3) Nothing else yet — the rest is correct code aimed at the wrong shape, not wasted code.

**Shortest path to Analyze being TRUSTWORTHY (input honest, not prose polished).** Analyze reads `pm_closed_position`. Its input is honest **only** if the loss-omission is resolved. The shortest path is **not** more narration work — it is: **(1)** run the mechanism test (redeem-vs-held-to-zero) on evanng's 58 missing losses to know *why* they're gone; **(2)** decide per Q1 whether to (a) accept + stamp every Analyze report with a measured loss-completeness ratio per whale (cheap, honest, immediate), or (b) re-source losses via `/activity` REDEEM-grounding for the *specific pair being analyzed* (Analyze is on-demand and single-pair, so the 5,000-row truncation that killed the bulk scout is a non-issue for one whale — this is the one place the expensive-but-honest path is affordable). Option (b) makes Analyze's input *independently honest of the flawed foundation* for exactly the decision Jack says matters most, without re-plumbing the whole platform. That is the highest-leverage move available.

**What we are building that we don't need.** The flat `/scoreboard` (above). Possibly the full 18-category pinned set, if the tile set is really only the Kalshi-copyable categories (Q4) — half of those pairs may never be promotable, so paper-trading and Analyzing them is effort against pairs that can't reach live.

**How to stop requirements being lost at the next handoff.** The technical-state handoff template carried SHAs and dropped §0. Fix: **make §0 of THIS document the mandatory first read in every handoff, and add a one-line gate to the transition template — "restate the three lists and their three data bases from memory before touching code."** Requirements drift because they're in prose no checkpoint tests; the counter is to put a product-level assertion where an agent must engage it. Concretely: a `PM_REQUIREMENTS.md` (this §0, promoted) linked from every `TRANSITION_TO_*` doc and from the package `__init__`, and a review question in each checkpoint's exit criteria — "which of the three lists did this change touch, and did it keep their bases separate?"

---

## Appendix — read-only counts probe (fills the `[probe]` gaps in §3)

The `[probe]`-flagged counts (`pm_whale`, `pm_category_stats`, `pm_category_onesided_stats`, `pm_open_position`, `pm_score_snapshot`, `pm_meta`) were not captured in tonight's earlier probes. A single mode=ro `SELECT name, COUNT(*)` sweep over `sqlite_master` fills them without any write — say the word and I'll author `runners/pm_state_inventory_probe.sh` (read-only, engine-bracketed, sanctioned channel) to complete §3 with exact numbers. Everything else in §3 is live-measured 2026-08-26.
