# Prediction Markets — REQUIREMENTS (durable anti-drift artifact)

**Read this before touching the `trading_corp/prediction_markets` package.** It is referenced from the
package docstring (`trading_corp/prediction_markets/__init__.py`) so it is reachable from the code, not only
the reports directory. Handoffs that carried SHAs but dropped the product description are what produced the
2026-08 rebuild; this file exists so that class of loss stops here. When a requirement lands, record it HERE
— a migration comment or a plan section is not an adequate carrier.

Fuller context (not a substitute for this file): the recovered requirements with line cites live in
`PM_STATE_REVIEW_2026-08-26.md §0`; the staged build plan + rulings live in `PM_REBUILD_PLAN_2026-08-26.md`.

---

## 1. THE SCREEN HIERARCHY

- **Main Predictions-Market Dashboard** — the Account-Category (sub-division) tiles (P3; not built) + one
  other menu option, **Farm League**.
- **Farm League** → **category-only tiles** (the Kalshi-copyable categories) → per-category detail page.
- **Per-category page** = **Watchlist section on top** (pinned whales; buttons Demote / Promote / Analyze) +
  **Prospects section below** (farm-league prospects; button Promote-to-watchlist).
- Clicking a whale → a detail page (paper trades + stats for a pinned whale; closed trades for a prospect).

## 2. THE THREE LISTS, THREE DATA BASES  ⭐ the load-bearing invariant

One (wallet, category) pair can appear on all three at once and **show a different number on each**. Keeping
the three bases separate is the single requirement whose violation caused the rebuild.

| List (screen word) | Where | DATA BASIS | Table / source | Code value |
|---|---|---|---|---|
| **Prospect** | Farm League, bottom | **completed trades only** | `pm_closed_position` → `pm_category_stats` | `pm_watchlist.status='candidate'` |
| **Watchlist** | Farm League, top | **our paper trades only** | `pm_paper_trade` → `pm_paper_category_stats` | `pm_watchlist.status='pinned'` |
| **Live** | Account-Category sub-division | **live trades only** | P3 tables (not built) | — (P3) |

**⭐ ANALYZE IS THE POINT.** Rough prospect stats are a screen; Analyze is what decides promotion. A defect
in what Analyze is FED outranks any imprecise number on a screening list.

**Checkpoint exit question (every handoff must answer):** *Which of the three lists did this change touch,
and did it keep their three data bases (completed / paper / live) separate?*

## 3. CANONICAL VOCABULARY (RULED 2026-08-26, §F-3) — write once, here

Screens render Jack's words; the code keeps its values; the table name stays `pm_watchlist` (renaming is a
migration for no user-visible gain).

- Screen **"Prospect"** = completed-trade basis = code `status='candidate'`.
- Screen **"Watchlist"** = our-paper-trade basis = code `status='pinned'`.
- Screen **"Live"** = live-trade basis (P3).

---

## 4. STANDING BUILD REQUIREMENTS (the ones that keep getting lost)

### R1 — The Stage-1 paper rollup MUST gate `active=1`.  *(recorded 2026-08-26, item 5a)*
The Stage-0 off-funnel flag (`pm_watchlist.active`, migration 008) is enforced by adding `AND active=1` to
every consumer that reads `pm_watchlist`. When Stage 1 builds `pm_paper_category_stats` and its
`paper_rollup()`, that rollup **must also gate `active=1`** (only aggregate paper trades for pairs still in
the funnel). It is currently only noted in the migration-008 code comment; it is recorded here because a
migration comment is not a durable carrier. A deactivated pair must show NOWHERE — including the paper
scoreboard.

### R2 — Stage 4 search needs a CATEGORY-LEVEL exclusion (a row flag is not enough).  *(recorded 2026-08-26, item 5b — do NOT implement yet)*
**The gap:** `active=0` is set **per ROW** — the 22 specific pairs deactivated in Stage 0. But Jack's
exclusion is **per CATEGORY** (`cbb`, `fifwc`, `unknown` show nowhere; he will refine those categories with a
code agent later and re-admit any that prove Kalshi-copyable). The Stage-4 seed writer **defaults new rows to
`active=1`**, so a **newly discovered whale in an excluded category would insert as `active=1` and surface as
a prospect** — a row-level flag cannot stop a pair that did not exist when the 22 were deactivated.
**The requirement:** Stage 4's candidate SELECTION needs a **category-level** exclusion of its own — which
whales SURFACE as prospects / are eligible to pin — e.g. a deactivated-category list the selection filters
against. **This is a SELECTION / presentation concern, NOT an ingest concern: the PULL stays all-categories
(see R5).** **Mechanism NOT chosen; Stage 4 is unauthorized — do not implement.** Recorded so it is not
rediscovered the hard way (a whale leaking into an excluded category on the live board).

### R3 — Resolution comes from the resolution authority (gamma `/markets`), never `/closed-positions`.  *(the §B finding)*
Whether a market resolved and which side won is a fact about the MARKET, not about any wallet's position
records. The Stage-1 adjudicator re-base reads gamma; `/closed-positions` is a **screening source with a
labelled, measured bias** (RULED §F-1), never the system of record. Any future code that needs
"did this resolve / who won" reads gamma, not `/closed-positions`.

### R4 — Every displayed number needs a BASIS test (requirements-as-tests).
For each number a screen shows, a test seeds a pair where the **required-source value ≠ the wrong-source
value** and asserts the UI shows the **required** source. The missing-rollup substitution shipped because the
only test asserted a number was *present*, never what it was *derived from*. This is the deepest anti-drift:
a silent wrong-source fallback breaks the build.

### R5 — INGEST STAYS ALL-CATEGORIES; category exclusion is a PRESENTATION / SELECTION concern, never an INGEST concern.  *(RULED 2026-08-26, item 3)*
Jack raised scoping the whale-activity PULL to tracked categories only. **RULED AGAINST:**
- It contradicts deferred-pending-analysis: `cbb` / `fifwc` / `unknown` are excluded PENDING ANALYSIS, and Jack intends to find copyable categories inside them later. If the pull stops collecting them, the evidence for that analysis stops accumulating.
- `unknown` is a slug-derivation FAILURE, not a category — category-scoped ingestion cannot exclude what it failed to classify, and would hide the derivation gap.
- Scoping ingestion to the live categories was already rejected earlier in this build; reversing it must be deliberate, not a side effect of a query fix.
**Standing principle:** category exclusion lives at the QUERY layer (the `active` gate), the TILE set, and candidate SELECTION (R2) — NEVER at ingest. **The pull stays all-categories.**

### R6 — Stage-2 screens MUST surface OPEN-POSITION COUNTS, not only rolled-up (closed) stats.  *(RULED 2026-08-27, T1 rung 2 — do NOT implement yet)*
**The gap (observed live, T1 rung 2 + rollup):** the `/farm` landing renders generic poll status ("polled") plus pcs-sourced (closed-trade) stats; it does **not** render per-pair *live open-position counts*. Consequence: after a poller run captured **14 new open trades**, `/farm` came back **byte-identical** — the main page could not distinguish *"the poller ran and captured 14 trades"* from *"the poller never ran."* The open positions live in the DB (`pm_paper_trade.status='open'`, and `pm_paper_category_stats.n_open`), but the landing doesn't show them.
**Why it matters:** once the cadence is installed, `/farm` is the page Jack watches daily. A screen that can't tell "captured 14" from "ran nothing" hides the very signal the cadence exists to produce — and hides a silently-broken poller. This is the presentation analogue of the completeness gap T1 just fixed at the data layer.
**Requirement:** Stage-2 screens (the Farm-League hierarchy) must display **open-position counts per pair/category** (and ideally last-poll freshness) as a first-class number, distinct from the closed-trade performance stats. The three-state poll token already exists (`farm.poll_state`) but the landing must render the COUNT, not just the status word. **Reasoning kept so it isn't re-lost:** rolled-up stats are closed-trade performance (correctly), so they are stale/empty for a lane that is mostly open trades — the open count is what shows the lane is alive. Do NOT build now; this is a Stage-2 screen requirement.

### R7 — The per-account EXPOSURE CAP sums PM's JOURNAL, not the venue book.  *(recorded 2026-08-29, Stage 3 R5.5)*
**The property:** PM's per-account open-exposure cap (the Stage-3 execution chokepoint's gate 6, `execution.py`)
is an O(1) counter seeded and accumulated from PM's OWN journal (`pm_subdivision_order`). It does NOT read
Kalshi's actual book. So any position on that Kalshi account PM did not place -- a manual trade, or another
division sharing the same Kalshi keypair -- is INVISIBLE to the cap: PM sizes as though the account held less
than it does, and can over-commit against the real balance.
**Why it is filed (it OUTLIVES any one shutdown):** it is harmless while the account is PM-EXCLUSIVE (the R7
go-live precondition -- no co-tenant division, no manual trading on the KALSHI account). But a FUTURE agent who
adds a SECOND tenant to any PM account (a second division on the same keypair, or returns the account to manual
use) re-introduces the blind spot. Boot-reconcile (R5.5) DETECTS a divergence at boot and latches, but it does
NOT continuously correct the cap -- it is not a fix for this. If a PM account ever becomes shared again, the
exposure cap must be re-based on the venue book (or the sharing refused). Do NOT inherit PM-exclusivity as
permanent. (Raised by the R5.5 co-tenant investigation + Jack's ruling, STAGE3_PLAN sec 19e.)

---

## 5. CATEGORY STATUS (as of 2026-08-26)

- **15 tile categories (IN):** mlb, nba, nfl, nhl, wnba, epl, ucl, soccer, atp, wta, tennis, cs2, golf, ufc, fed.
- **Excluded — three DISTINCT states, never flattened to "not copyable":**
  - `cbb` → **not_probed** (pending analysis; expected to return after a correct-keyword NCAAB probe).
  - `fifwc` → **dormant_calendar** (measured dormant; World Cup concluded; returns next cycle).
  - `unknown` → **structural** (a tier-1 slug-derivation failure, not a subject; permanent).
- Excluded categories are recorded as their state above — never as "rejected." (§F-2.)
