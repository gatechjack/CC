# PM RECOVERY SCOPING — Polymarket Data API v1→v2 migration + arm-set rebuild

**Date:** 2026-11-17 · **Mode:** READ-ONLY (plan + authored-not-run runner; nothing written/deployed/armed/
restarted) · **Branch:** `pm-health-stopped-2026-11-16` · Follows `00_PM_STOPPED_REPORT.md`.

Two workstreams, both read-only. **Workstream A** scopes the v1→v2 migration (plan only, no code). **Workstream
B** rebuilds the arm lists, resolves the boot-vs-live roster question, and authors (does not run) the re-arm
runner. Evidence: `analysis/pmh{6,7,8}.{sh,ps1}` (A1 DB-freshness, timers, and the **live v2 field probe**), plus
three sub-agent code/web traces folded in. The live v2 probe hit the real API from the box: **8 calls, all v2
returned HTTP 200, v1 returned HTTP 410** — so the box can reach v2 with no credentials and no geo-block.

---

## 1 — WORKSTREAM A1: BLAST RADIUS (every Polymarket Data API caller)

The whole division reads Polymarket through ONE client: `trading_corp/data/polymarket_data_api_client.py`
(base `_DATA_API_BASE="https://data-api.polymarket.com"`, `polymarket_data_api_client.py:48`). All calls are
**unauthenticated public GETs**. Market *resolutions* use a **different host** `gamma-api.polymarket.com`
(`:49,:601`) and are **unaffected** by the v1 retirement.

**Every v1 caller is failing since 2026-11-02** (v1 retired that day — §2). DB freshness confirms it: last
successful ingest per pipeline (probe 7 §1):

| Pipeline (table) | Last successful data | Since |
|---|---|---|
| live whale signal (`pm_driver_heartbeat` n_signals) | — | 2026-11-02 (fills cliff; now 100% HTTP 410) |
| paper poller (`pm_paper_trade.opened_ts`) | 2026-11-02 22:00 | dead |
| closed-position ingest (`pm_closed_position.ingested_ts`) | 2026-11-02 13:55 | dead |
| loss-grounding (`pm_loss_grounding_cache.grounded_ts`) | 2026-11-02 13:57 | dead |
| open-position recency (`pm_open_position.refreshed_ts`) | 2026-10-26 (manual search) | dead |
| rollups (`pm_category_stats.updated_ts`) | 2026-11-16 | OK (recomputes from stored rows, no API) |

**Caller inventory** (endpoint · purpose · process · failing today):

| Caller `file:line` | v1 endpoint | Purpose | Process → deploy | Failing now? |
|---|---|---|---|---|
| `live_driver.py:1439` | `/positions` | **Live whale entry signal** (~7 s) | engine → **engine restart** | **YES, ~19/sec 410** |
| `live_driver.py:1453` | `/activity` | Live exit confirm (on reduction) | engine → engine restart | YES (when reached) |
| `paper.py:151` | `/positions` | Paper capture (from engine + `pm_cli paper-poll`) | engine / CLI | YES (stopped 11-02) |
| `ingest.py:230` | `/closed-positions` | Whale closed-history ingest/backfill | CLI / pm_web button | YES when invoked |
| `ingest.py:304` | `/positions` | Open-position recency (search) | CLI | YES when invoked |
| `loss_grounding.py:161,171` | `/activity`,`/closed-positions` | Loss-grounding for Analyze | **pm_web** → **pm_web restart** | YES when invoked |
| `loss_grounding.py:183` | gamma `/markets` | Resolutions | pm_web | NO (gamma, unaffected) |
| `search_run.py:78` | `/v1/leaderboard` | Whale discovery | CLI / pm_web-spawned | YES when invoked |
| `web/app.py:472` | activity+closed (Analyze btn) | Analyze pipeline | pm_web → pm_web restart | YES when clicked |
| `web/app.py:1139` | `/closed-positions` (Refresh btn) | Ad-hoc whale refresh | pm_web → pm_web restart | YES when clicked |
| `scripts/pm_cli.py` {backfill,search,paper-poll,g0} | closed/positions/leaderboard | Manual ops | CLI | YES when invoked |
| `scripts/seed_polymarket_watchlist_deep.py` | leaderboard/activity | Standalone seeder | manual | YES when invoked |

**pm_web is dormant** (probe 6 §6: zero 410s in its journal) because its callers are button-driven and Jack is
away — but they *would* 410 if clicked. Only the **engine `/positions` loop** is actively failing continuously.
**Other divisions (MACE/PMCC/PEAD/bitunix): none call the Polymarket Data API** (grep clean).

---

## 2 — WORKSTREAM A2: WHAT v2 IS (primary sources)

Base `https://data-api.polymarket.com/v2`; OpenAPI at `/v2/openapi.json`. Sources: docs.polymarket.com
(migrate guide, overview, rate-limits, changelog) — PRIMARY.

| v1 endpoint | v2 equivalent | Note |
|---|---|---|
| `GET /positions` | `GET /v2/positions` | `status` filter, default OPEN |
| `GET /closed-positions` | `GET /v2/positions?status=CLOSED` | **MERGED** into /v2/positions |
| `GET /activity` | `GET /v2/activity` | cursor pagination |
| `GET /v1/leaderboard` | `GET /v2/leaderboard` | **`offset` removed → cursor**; returns `user_id` |
| resolutions (gamma) | unchanged (`gamma-api`) | not a Data-API v1 route — no change |
| `GET /v1/accounting/snapshot` | **retired, no v2** | not used by us |

- **Auth: NONE required** (public reads). **Geo: not restricted on the Data API** (only *trading* is geoblocked)
  — and **confirmed empirically**: the box's v2 calls all returned 200 (§3). ⇒ **no new credentials, no geo work.**
- **Rate limits** (IP, sliding 10 s): `/v2/positions` **200 req/10 s**, general cap 800/10 s. ⚠️ The engine's
  current /positions fan-out runs ~19 calls/sec ≈ 190/10 s — **just under the 200/10 s per-endpoint cap**. The v2
  cutover must preserve (ideally widen) the existing inter-call throttle/stagger or it will start drawing 429s.
- **Pagination: cursor only** (`pagination.next_cursor`); sending `offset=` returns **HTTP 400**.
- **The 5000-row `/activity` wall is GONE in v2** (cursor paginates to exhaustion) — removes the loss-grounding
  truncation limitation.
- **Timeline:** v2 launched 2026-09-04; **v1 retired 2026-11-02** (= our exact failure date); no v2 sunset
  announced.

---

## 3 — WORKSTREAM A3: FIELD SEMANTICS, PROVEN AGAINST REAL v2 RESPONSES

Live box probe (`analysis/pmh8.*`), 3 held wallets. The engine's open-signal parser `PositionRow.from_api`
(`polymarket_data_api_client.py:234-261`) consumes exactly these v1 keys → mapped to the real v2 keys observed:

| Engine field (`PositionRow`) | v1 key (parsed) | **v2 key (real response)** | Semantic delta |
|---|---|---|---|
| proxy_wallet | `proxyWallet` | `proxy_wallet` | snake_case |
| condition_id | `conditionId` | `condition_id` | snake_case |
| asset (token id) | `asset` | **`token_id`** | renamed |
| size (contracts) | `size` | **`current_size`** | renamed; `total_size` = lifetime |
| avg_price | `avgPrice` | `avg_price` | snake_case |
| initial_value (cost) | `initialValue` | **`entry_cost_usdc`** | renamed (fee-excl); `total_cost_usdc` fee-incl |
| current_value | `currentValue` | `current_value` | snake_case |
| pnl | `cashPnl` | **`unrealized_pnl`** (open) / `realized_pnl` (closed) | renamed + split |
| title / slug / event_slug | `title`/`slug`/`eventSlug` | `title`/`slug`/`event_slug` | snake_case |
| outcome | `outcome` | `outcome` | same (e.g. "Under","Aoi Ito") |
| (not parsed today) | — | `outcome_index`,`opposite_outcome`,`opposite_token_id` | **NEW — useful for leg disambiguation** |

**Verified semantics from the real payloads:**
- **Envelope changed:** v2 wraps every list in `{"data":[...],"pagination":{...}}`; v1 returned a bare array.
  Any code doing `resp.json()` then iterating will break — the parser must read `.data`.
- **CLOSED via `?status=CLOSED`:** confirmed — closed rows carry `current_size:0.0`, `status:"CLOSED"`,
  `realized_pnl` populated, `total_size` = lifetime. The separate `/closed-positions` call collapses into one
  `/v2/positions` call with the status param (ClosedPositionRow remap: `curPrice`→`current_price`,
  `realizedPnl`→`realized_pnl`, `totalBought`→`total_cost_usdc`, `outcomeIndex`→`outcome_index`, etc.).
- **Timestamps are epoch SECONDS** (`last_event_at:1794769463`, `first_entry_at`). v1 `/positions` timestamps
  were ms. ⚠️ `PositionRow` does **not** parse a timestamp today, so open-signal parsing is unaffected — but
  `ClosedPositionRow.timestamp` and `ActivityRow.timestamp` DO carry times; any `/1000` must be removed.
- **Does v2 /positions give everything the matcher + leg-audit read?** **YES.** Every `PositionRow` field has a
  v2 source (table above); v2 additionally supplies `outcome_index`/`opposite_outcome`/`opposite_token_id`
  which the matcher currently derives from the token id — a strict superset. **No field the engine consumes is
  dropped.** (Leg-audit reads the stored journal + Kalshi legs, not Polymarket, so it is unaffected.)
- **Activity** (`/v2/activity`): `{proxy_wallet, timestamp(sec), condition_id, type:"TRADE", size, usdc_size,
  price, token_id, side:"BUY", outcome_index, title, slug, outcome}` — carries everything the exit-detector/
  loss-grounding read; `side`/`usdc_size`/`price` present.
- **Leaderboard** (`/v2/leaderboard`): `{rank, user_id, pnl, volume, user_name}` + `pagination.next_cursor`.
  v1 `proxyWallet` → v2 **`user_id`**; discovery remap is `user_id` + cursor loop.

---

## 4 — WORKSTREAM A4: THE MIGRATION PLAN (build, don't guess)

**Only one source file changes: `trading_corp/data/polymarket_data_api_client.py`.** All callers go through it;
a correct remap there fixes every pipeline in §1 automatically.

**Changes (shape):**
1. **URLs:** `/v1/leaderboard`→`/v2/leaderboard`; `/positions`→`/v2/positions`; `/activity`→`/v2/activity`;
   `/closed-positions`→`/v2/positions?status=CLOSED` (merge). Gamma resolutions unchanged.
2. **Envelope unwrap:** `_get_json` (or each fetch) must read the `data` array from the `{data,pagination}`
   wrapper (was a bare list). *(Field remap, not a rewrite.)*
3. **Pagination:** replace `offset=`/limit loops with **cursor** (`pagination.next_cursor`) — `fetch_positions_book`
   (`:468`), `fetch_closed_positions` (`:544`), `fetch_activity` (`:459`), `fetch_leaderboard` (`:441`). Sending
   `offset` to v2 is a 400.
4. **Field remap** in `PositionRow.from_api` / `ClosedPositionRow.from_api` / `ActivityRow` / `LeaderboardEntry`
   to the v2 snake_case keys (§3 tables). Remove any ms→s `/1000` on timestamps.
5. **410 handling:** add explicit loud handling (today 410 falls into the generic ≥400 raise at `:724` and
   silently skips each whale) — a hard-fail or alarm so a future retirement is caught in hours, not weeks.
6. **Throttle:** keep /positions fan-out under 200 req/10 s (preserve the existing per-account stagger; verify).

**Re-ingest?** Not required for live trading (signals are read fresh each cycle). Stored v1-era rows stay valid
alongside v2 rows; a one-off `pm_cli refresh`/`search` repopulates closed-history under v2 at leisure.

**Tests (fixtures from REAL v2 responses in `analysis/pmh8.*`, never from the code under test):**
- Parser unit tests: v2 `/v2/positions` → `PositionRow` for open and `?status=CLOSED` rows (field + units).
- A side/outcome test per category matcher (the leg must land on the correct side — the one bug class that
  matters), driven by captured v2 payloads for ITF/NBA/soccer rows.
- Envelope + cursor pagination tests (multi-page, `next_cursor=null` terminator).
- A **loud-failure** test: a 410 (or any hard error) on a feed surfaces/alarms, not a silent skip.
- `/v2/leaderboard` discovery parse (`user_id`+cursor).

**Rollout order + restart requirement:**
1. Deploy the client change → **engine restart** (picks up live_driver `/positions`+`/activity`) **and** **pm_web
   restart** (Analyze/Refresh buttons). pm_cli/standalone pick it up on next invocation (no deploy).
2. ⚠️ **Restart precondition (standing):** re-verify zero open positions / zero settled-but-open first
   (`analysis/pmh3.sh` §1 — currently clean). A restart is safe because the book is flat.
3. After v2 is confirmed serving signals, do the arm-set rebuild (§5–7).

**Size/risk:** one file, ~4 mechanical remaps + envelope/cursor + 410-handling. **Risk: medium** — it is the
whole division's signal ingest, and the side/outcome mapping is where a copy lands on the wrong leg; the
real-payload fixtures + per-matcher side tests are the guardrail. No auth/credential/geo work (verified).

---

## 5 — WORKSTREAM B1: LIVE vs RESTART (does re-arming need a restart?)

From code (sub-agent trace, verified against the files):

| Change | Effect | Proof |
|---|---|---|
| **Arm / clear-latch** on a sub already driven | **LIVE** (next order) | arm re-read per order from mode=ro DB, `live_driver.py:1044`; comment `execution.py:892` |
| **Attach / detach** a whale | **LIVE** (next cycle) | roster re-read every cycle, `live_driver.py:1433-1435` |
| **Category set (`cats`)** | **FIXED at task spawn** | derived once at boot `main.py:1466`→`driver_roster.active_driver_subdivisions`; `cats` never re-derived in loop (`live_driver.py:1228`) |
| **New sub-division / a category with ZERO attachments at boot** | **RESTART required** | task spawn is boot-only; a category not in `cats` at boot is never driven until next restart |

**→ CONCLUSION: re-arming marc & trey does NOT need a dedicated restart.** Their traded categories show **fresh
heartbeats** (probe 1 §9, age 2–15 s) → they ARE in the running task's `cats` → the per-order arm re-read picks
up a clear-latch+arm within one cycle (~7 s). (Their 5 never-attached legacy categories — fed/fl1/nba/nhl/sea —
are not in `cats`, but those are not being re-armed.) In practice the re-arm will ride *after* the v2-migration
deploy restart anyway (no signals until then), and that restart harmlessly re-derives `cats` from current
attachments.

**jack:nba anomaly — explained:** nba had its last whale detached 2026-10-23 → dropped from the roster. At the
2026-10-27 engine restart `cats` was re-derived and **excluded nba** (zero attachments then). The two whales
attached 2026-10-28 (after the restart) are NOT in `cats`, so nba stays un-driven until the next restart. (The
v2 deploy restart will bring it back in.) This is the general "new/re-attached category needs a restart" gap.

**trey not re-latched at the 10-27 boot — RESOLVED:** the Kalshi location-attestation blocks **order placement
only, not portfolio reads** (proven: marc/trey balance snapshots read successfully every 5 min, probe 3 §2). So
at the 10-27 boot trey's `get_positions` boot-reconcile READ **succeeded**, trey was flat, `compare()` found no
diffs → `reconcile_account` returned clean and **wrote nothing** (`boot_reconcile.py:226-228`), leaving the
2026-10-22 `auth_failure` latch untouched. (Benign — not indeterminate.) Corollary: a future restart will **not**
latch marc/trey either (reads work, they're flat).

---

## 6 — WORKSTREAM B2: PER-ACCOUNT ARM LISTS (for Jack to choose from — recommend nothing)

Reconstruction method: a FILL proves a category was armed. Per-account fill history (probe 6 §1) is bucketed
against the **2026-10-16 baseline armed/latched counts** (jack 18/8, karen 16/6, marc 11/11, trey 12/10) and the
2026-10-16 health check's stale-latch list (`analysis/03_subdivision_whale_review.md`). **The fill counts match
the baseline armed counts exactly** (marc 11 traded = 11 armed; trey 12 traded = 12 armed), so the reconstruction
is high-confidence. The auth_failure latch (marc 10-28 / trey 10-22) overwrote the exact pre-latch flags, so
"armed" is inferred from "traded in the 30 d before the incident."

### kalshi_marc — all 22 currently `auth_failure` latched (needs attestation + v2 first)
| Bucket | Categories | Evidence |
|---|---|---|
| **1. Traded pre-incident (= was ARMED), 11** | **nfl, itf, cfb, mlb, cs2, mls, ufc, atp, mex, wnba, lal** | all have fills in the 30 d pre-latch; count == baseline "11 armed" |
| **2. Stale 2026-10-06 boot latch / never traded, 11** | bra, bun, epl, ucl, uel, wta, fed, fl1, nba, nhl, sea | 0 fills; the 10-16 health check lists these as stale boot_reconcile latches, later masked by auth_failure |
| 3. Never armed/never traded | (same as bucket 2 for marc) | — |

### kalshi_trey — all 22 currently `auth_failure` latched (needs attestation + v2 first)
| Bucket | Categories | Evidence |
|---|---|---|
| **1. Traded pre-incident (= was ARMED), 12** | **nfl, itf, cfb, mlb, mls, cs2, ufc, atp, wnba, lal, mex, wta** | all have pre-latch fills; count == baseline "12 armed" |
| **2. Stale 2026-10-06 boot latch / never traded, 10** | bra, bun, epl, ucl, uel, fed, fl1, nba, nhl, sea | 0 fills; stale boot latches masked by auth_failure |
| 3. Never armed/never traded | (same as bucket 2 for trey) | — |

### kalshi_jack — NOT auth-latched (blocked only by the dead feed; arm set intact since 10-16)
| Bucket | Categories |
|---|---|
| Armed + traded (17) | atp, boxing, bra, bun, cfb, cs2, epl, itf, lal, mex, mlb, mls, nfl, ucl, ufc, wnba, wta |
| Armed, never filled (1) | f1 |
| **Stale 2026-10-06 boot latch (8)** | fed, fl1, nba, nhl, sea, soccer, tennis, uel |

### kalshi_karen — NOT auth-latched (blocked only by the dead feed; arm set intact since 10-16)
| Bucket | Categories |
|---|---|
| Armed + traded (16) | atp, bra, bun, cfb, cs2, epl, itf, lal, mex, mlb, mls, nfl, ucl, ufc, wnba, wta |
| **Stale 2026-10-06 boot latch (6)** | fed, fl1, nba, nhl, sea, uel |

**Notes:** jack/karen need no re-arm (not auth-latched) — their only latched rows are the stale-10-06 legacy
categories (soccer/tennis/fed/nhl/fl1/sea/uel/nba), several of which are superseded by the current scheme
(epl/ucl/uel, atp/wta/itf). **Recommend nothing on which to arm — arm state is Jack's decision.**

---

## 7 — WORKSTREAM B3: THE RE-ARM RUNNER (authored, committed, NOT run)

`analysis/pm_rearm.{sh,ps1}` (also at repo CWD). It:
- clears the latch + arms **only** the `account:category` pairs Jack lists in `ARM_LIST`, via the sanctioned
  `pm_cli live-arm --clear-latch --by jack` path (writes legacy `agent_state` — a RESERVED action → **Jack runs
  it**, not an agent);
- **refuses to do anything** until Jack flips both precondition flags (`ATTESTATION_RENEWED=1`,
  `V2_MIGRATION_LIVE=1`) — re-arming before either just re-latches on the first order or arms a sub with no
  signal;
- **reads arm state back** (`live-status`) for each listed pair (should show `armed:true latched:false`);
- touches nothing outside the list.

The candidate pre-incident lists (§6) are in the runner's comments for Jack to copy from; `ARM_LIST` ships
**empty**. One-liner (Jack, after R1+R2): `powershell -ep bypass -f "C:\Users\AA Incorporado\cc\pm_rearm.ps1"`.

---

## 8 — WHAT I COULD NOT ESTABLISH

- **jack & karen's current attestation validity** — unverifiable from data (no order attempt since their last
  fill → no 403; reads succeed regardless). Only the Kalshi UI (`api_key_region_expiration_ts`) resolves it. The
  staggered pattern (trey 10-22, marc 10-28) makes a jack/karen lapse plausible; renew all four.
- **Exact pre-latch armed flag for marc/trey's never-traded categories** — overwritten by the auth latch. The
  traded set (buckets §6.1) is certain; whether any *never-traded* category was armed-but-signal-starved vs
  never-armed cannot be separated by fills alone (the baseline count match says the armed set == the traded set).
- **v2 field VALUE reconciliation against the SAME stored positions** — the stored v1 rows are weeks stale
  (positions rolled over), so §3 proves the v2 *schema/units/structure* and that no consumed field is dropped,
  not a value-for-value tie on identical positions.
- **v2 geo policy permanence** — empirically the box got 200s today; Polymarket could change geo policy later
  (the loud 410/err handling in §4.5 is the safety net).

---

*Read-only. No code written beyond the authored-not-run re-arm runner; nothing deployed, restarted, armed or
written. Committed to `pm-health-stopped-2026-11-16`; not pushed. HALT for Jack.*
