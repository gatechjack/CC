# FARM SEARCH BUTTON — build handoff (2026-09-05). KEEP CURRENT AS RUNGS LAND.

**Task:** a Search button so Jack can refresh Prospects from the UI. Today the only trigger is the CLI
`pm_cli search`, and there is no control anywhere in pm_web. pm_web-only work → deploys with a **pm_web
restart, no engine restart, no interruption to trading**. Nothing here reaches the order path (verified:
`search_run` is imported by 0 engine files — live_driver / execution / main.py).

**Branch** `pm-farm-search-2026-09-05` (worktree `cc-pm-farm-search-wt`), **base `3f498d4`** =
`pm-multicategory-2026-09-02` local tip. **Autonomy (Jack, 2026-09-05):** build → test → box-scratch →
adversarial review → commit/push → stage the deploy, WITHOUT per-step check-in. HALT ONLY for: the deploy
itself, any restart, any live DB write, anything touching the order path or arm state, any prod-live advance.

---

## ★ THE RECORD — corrections Jack asked to be stated loudly

1. **NO SEARCH TRIGGER HAS EVER EXISTED IN THE UI.** Not a route, not a button, not a form — on any branch,
   and not on the box. pm_web's mutating routes are promote / demote / refresh (per-whale) / attach / analyze;
   there is no `/search`. The Prospects empty-state literally says *"populated by Search"* yet ships no way to
   run it (`pm_prospects_rows.html:18`). Jack was told a trigger existed; that was assumption, not knowledge.
   The correction stands: the only trigger is `pm_cli search`.

2. **THE SCOPING ANSWER IS STRUCTURAL, NOT A LIMITATION TO WORK AROUND.** Polymarket's `/v1/leaderboard`
   knows five COARSE buckets only — `Politics, Sports, Crypto, Tech, Mentions`
   (`polymarket_data_api_client.POLYMARKET_LEADERBOARD_CATEGORIES`). There is no "mlb whales" query. Category
   is knowable ONLY after slug-parsing the positions you already fetched. So discovery is coarse-bucket-only,
   backfill is all-categories by rule (R5), and the 15-category filter applies only at the final candidate
   WRITE. The ~92-minute cost (discover ~50 Sports whales + full-page-backfill each) is category-independent.
   **Per-category scoping would be exactly the fake Jack warned against** — 92 minutes to "refresh mlb," silently.
   → **The button goes on the FARM LEAGUE MAIN PAGE and searches everything.**

3. **★ THE `--category` DECOY — a flag that silently does nothing while appearing to work.** `pm_cli search
   --category mlb` calls `fetch_leaderboard("mlb")`, which returns zero rows → **0 discovered → 0 candidates →
   a run that looks clean and did nothing.** Same class as "a safety check that silently stops checking."
   **FIX CHOSEN: REJECT a fine category outright (fail-loud), not merely document.** `search_run.discover_wallets`
   now validates the bucket against `POLYMARKET_LEADERBOARD_CATEGORIES` (None = the global leaderboard is still
   allowed) and raises `ValueError` naming the five valid buckets; `pm_cli search` surfaces it as a clean error
   before any run opens. [rung R1/R2]

## ★ LAST RUN — confirmed against the record (STAGE4_SEARCH_PLAN §11B)

`run_id=1` (Sports bucket): **92m36s**; discovered=50, backfilled=47 (40 complete / 7 partial-capped / 2
PK-collision-failed); **134 candidates written** (135 selected, 1 no-clobber) across **all 15 allowlist
categories, 0 empty** (54 cleared N≥50, 80 via the thin-sample top-10 fallback). Estimate model for next time:
mass (median ~14 calls/whale) + a right-tail of cap-hitting whales (~160 pages each) at ~2.9 s/call under 429
backoff → ~1500–2000 calls, ~75–100 min for ~50 Sports whales.

## ★ WHAT A RUN DOES TO THE LIVE SYSTEM (unquantified risk — NOT proven-safe)

Both consumers hit `data-api.polymarket.com` from the same prod IP: the engine polls whale `/positions`
(`positions_client.fetch_positions_book`) every ~7s per attached whale; Search issues ~1900 calls over ~92
min. The client's rate limiter is a **per-instance `asyncio.Semaphore`** (`poly_client.py:412`), and Search
(pm_web process) and the engine (`trading-corp` process) hold **separate instances in separate processes** — a
shared in-process limiter is impossible. The only shared throttle is Polymarket's server-side per-IP limit
(the client demonstrably trips it — explicit 429 + Cloudflare-403 handling). **A sweep can push the engine
into 429 backoff → whale positions read late → copies placed late or missed.** run_id=1's "no hit" evidence
covered DB contention + the daily refresh with **one** armed sub; there are now **eight**. This is why the
button carries an honest on-page warning, is admin-only, and is single-flighted.

---

## BUILD PLAN (pm_web-only manifest; no migration; no shared CSS/JS)

- **Executor = detached subprocess.** The button spawns `pm_cli search` detached (setsid) — the path
  run_id=1 already used and survived; crash-isolated from the web loop; survives a pm_web restart/deploy;
  progress lands in a log. (An in-process asyncio task would have a deploy kill a 92-min sweep mid-flight.)
- **Placement:** Farm League main page, a "Prospect discovery" panel above the tile grid.
- **Access:** admin-only via `_forbid_if_not_admin` — Refresh is the precedent (Jack ruled it admin-only
  2026-09-01 precisely because it spends shared API budget); Search is the same concern at ~50× scale.
- **★ Single-flight guard (the part that matters most): heartbeat-based, in one atomic place.**
  - `search_run.acquire_search_lock(conn, ...)` runs a `BEGIN IMMEDIATE` transaction that CHECK-and-INSERTs
    the `pm_search_run(status='running')` lock row atomically — SQLite's write lock serialises two racing
    presses so the second sees the first's row and is refused. Returns `run_id` or `None` (already running).
  - The running sweep **heartbeats** (updates a `heartbeat_ts` in `pm_search_run.params_json`) between
    wallets. Staleness = *heartbeat older than a stale-window*, NOT a fixed run-length ceiling.
  - **★ Why a heartbeat, not a 3h ceiling:** a fixed ceiling that expires mid-sweep permits exactly the
    concurrency it exists to prevent (Jack's point). A genuine long run keeps heartbeating → it is NEVER
    considered stale, so the guard cannot expire under it. A CRASHED sweep stops heartbeating → the lock is
    reclaimable within the stale-window. Stale-window is sized to exceed the worst single-wallet time
    comfortably: §11B's cap-hitting whales take ~8 min each (≈160 pages × ~2.9s), so between-wallet
    heartbeats can be ~8 min apart → stale-window = **30 min** (≈3–4× margin) reclaims a dead lock without
    ever expiring a live one.
  - **No migration:** `pm_search_run` (migration 013) already carries `status`, `started_ts`, `finished_ts`,
    `params_json` (the extensibility seam — heartbeat lives here). Live schema head is 19; 018/019 are claimed
    by multicategory, so any future migration would be 020+ — but this needs none.
  - **Tested by a DIRECT POST while a run is marked running** (not by checking the button is disabled) — the
    boundary is the gate, not the hidden button.
- **Feedback — both halves:** immediate "underway" acknowledgement on press (htmx `hx-disabled-elt` +
  swapped panel) so nobody presses twice, AND a completion signal — the panel polls `GET /farm/search/status`
  (reads the latest `pm_search_run` row) and reports **running / done: N candidates / error**.
- **Warning text (not decoration):** on the page, plain: ~90 min; hits Polymarket from the same IP the live
  engine polls; **MAY COMPETE WITH LIVE COPYING** (a sweep can push the engine into 429 backoff → copies late
  or missed); measured with one armed sub, there are now eight.
- **Files touched (deploy manifest, all pm_web; app.py GRAFTED never wholesale):**
  `web/app.py` (2 routes + glue), `search_run.py` (guard helpers + bucket allowlist), `scripts/pm_cli.py`
  (--run-id adopt + heartbeat + bucket-reject + manual-path lock), `web/templates/pm_farm_league.html`
  (panel), a small new `web/templates/partials/pm_search_status.html`. **NOT touched:** `pm.css` / `pm_desk.css`
  (owned + actively changing on `pm-ui-rewrite-2026-09-02`; I style via existing classes to avoid dragging
  their work backward or forward).

## BASE-BRANCH CHOICE (box-is-truth) — chose multicategory, NOT the UI branch

`pm-ui-rewrite-2026-09-02` belongs to another agent actively working toward a UI-rewrite deploy — branching
from it risks my deploy dragging their unshipped work in. So I branched off **`pm-multicategory-2026-09-02`**:
(1) it is NOT that branch; (2) its `web/app.py` is the exact superset the live box graft derives from (box M4
`c2e4ddef` = multicat app.py − M5 admin plumbing), so my additive farm routes graft coherently and M5 stays
out exactly as DEPLOY 4 did; (3) `search_run.py`, `pm_cli.py`, `pm_farm_league.html` are byte-identical
(CR-stripped) between multicat, the UI branch, and the box (Stage-4, unchanged) — verified. The only file that
drifts with the UI agent is `pm.css` (multicat `bd74f8c8` vs UI `204d9051`), which is why I do not touch it.
**app.py graft note for the UI agent:** I add farm routes (`/farm/search`, `/farm/search/status`) + a guard
helper to `web/app.py`; coordinate the graft against the box at deploy, file-by-file, CR-stripped.

---

## RUNG STATUS (update as they land)

- **R0 — worktree + this handoff + record corrections.** DONE (this commit).
- **R1 — `search_run` guard core + bucket allowlist + offline tests.** DONE. Added `acquire_search_lock`
  (atomic BEGIN IMMEDIATE check-and-insert), `heartbeat_search_run`, `running_lock`, `latest_search_status`,
  `assert_valid_bucket` + `SEARCH_STALE_SEC=1800`. `test_search_lock.py` (11 tests) green locally
  (.venv-webtest): second-run-refused, second-connection-refused, stale-reclaim, **heartbeat-keeps-long-run-alive
  (the fixed-ceiling failure it prevents)**, close-releases, status idle/running/done/error/stale, bucket reject.
- **R2 — `pm_cli search` wiring (--run-id, heartbeat, bucket-reject, manual lock) + tests.** pending.
- **R3 — pm_web route + template + status + tests.** pending.
- **R4 — box-scratch (full suites, `-p no:pytest_ethereum`) + adversarial review of the guard + graft.** pending.
- **R5 — stage the deploy manifest (CR-stripped box reconcile, backups, Gate-A transitive imports,
  post-checks, stop conditions, rollback). Push. HALT for authorization.** pending.

## OPEN RULINGS (write here if any surface; keep building around them)
- none yet.
