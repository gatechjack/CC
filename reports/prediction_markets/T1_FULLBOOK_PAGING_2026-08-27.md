# T1 — /positions FULL-BOOK PAGING — BUILD (branch, box-scratch green) 2026-08-27

**Authorization (Jack):** "T1 FULL-BOOK PAGING — BUILD ONLY. Branch work, prove on box-scratch." NO live deploy /
restart / poller / adjudicator / rollup / cadence. Step 4 stays unauthorized; prod-live stays c77f618.
Built on `prediction-markets-stage0-2026-08-26` @ c22a82d. **Box-scratch GREEN, live untouched.**

## The fix
The `/positions` 100-row cap is a **default parameter, not a hard limit** (Q3, verified live: `limit=500` returns
500; BetMechanic 1311 positions over 3 pages; no offset cap like /activity's 5000). The client now pages the
**full open book** and reports whether it saw the whole thing.

- **`PolymarketDataAPIClient.fetch_positions_book(wallet, *, page_size=500, max_pages=40) -> PositionBook`** (new):
  pages `limit=page_size` + increasing `offset` until a **short page (< page_size)** marks the terminal ⇒
  `complete=True`. De-dupes by `(conditionId, asset)` so a book that shifts between pages can't double-count.
  `max_pages` (40 = 20,000 positions) is an infinite-loop backstop; hitting it while pages stay full ⇒
  `complete=False`. Returns `PositionBook{rows, complete, pages, n}`.
- **`fetch_positions(wallet, *, page_size=500, max_pages=40) -> list[PositionRow]`** (refactored): now returns the
  COMPLETE book, and **raises `PolymarketIncompletePositionsError` rather than returning a silent partial** if
  completeness can't be confirmed.
- **Page size = 500, why:** the largest `limit` I verified honored end-to-end; keeps BetMechanic (1311) to 3 pages;
  bounds response size. Termination is proven on a >1000 book (BetMechanic, 3 pages) AND a small one (MadeiraIsland,
  157, 1 page) — box-scratch covers both plus the exact-multiple boundary.

## Call sites (enumerated) — which changed
`PolymarketDataAPIClient.fetch_positions` has exactly **two** production callers (the `brokers/polymarket.py` and
`brokers/kalshi.py` `_fetch_positions` are a **different** broker method — on-chain, live engine — NOT this client;
out of scope):
1. **`prediction_markets/paper.py` `poll_pinned` — CHANGED.** Now calls `fetch_positions_book` and **gates on
   `complete`**: an unconfirmed book leaves the whale **un-polled** (recorded in a new `incomplete` list, `continue`,
   `last_polled_ts` stays NULL — a partial is not a poll, Ruling G). Removed the obsolete `cap_suspect` round-count
   heuristic (superseded by the real completeness signal); `totals.cap_suspects`→`totals.incomplete`; per-pair now
   carries `book_pages`.
2. **`prediction_markets/ingest.py` `refresh_open_positions` — NOT edited (gets the fix for free + safe).** It calls
   `fetch_positions`, so it now receives the full book automatically. It's safe by construction: the fetch happens
   **before** its `DELETE ... pm_open_position`, so a `PolymarketIncompletePositionsError` propagates *before* any
   delete — it can never wipe the stored open set with a partial read. (ingest.py stays off-limits, unedited.)

## Completeness signal (Jack's item 2)
The old code could not tell a full book from a truncated one — the exact blind spot that hid the under-count. The
new signal is a **terminal short page**: paging stops when a page returns `< page_size` rows, which *is* the proof we
reached the end. If no short page appears within `max_pages`, `complete=False` and the poller **does not proceed** —
the whale is left un-polled (visible in `incomplete`), never silently captured partial.

## Tests (box-scratch: full PM suite PYTEST_EXIT=0; T1 focused 11 passed)
- **`tests/prediction_markets/test_positions_paging.py`** (new, client-level, mocks `_get_json`): single-page
  (incl. 0 and 1), **multi-page** (1311→3 pages), **exact-boundary** (1000 = 500+500+trailing-empty), **cross-page
  de-dup**, **incomplete** (max_pages hit ⇒ `complete=False` and `fetch_positions` raises), and the list contract
  returning the full book. A test that only exercised a small book would have missed the original bug — these don't.
- **`test_paper.py`**: replaced the cap-suspect test with `test_incomplete_book_leaves_whale_unpolled` (unconfirmed
  book ⇒ 0 captures, `last_polled_ts` NULL, reported in `incomplete`); stubs `_Client`/`_ErrClient` now expose
  `fetch_positions_book`. **`test_removal_gate.py`** `_RecordingClient` records the `fetch_positions_book` call.

## Cost (Jack's item 4) — measured read-only across all 14 active whales
Full-book paging = **17 requests / 4.12 s** vs the old first-page-only **14 req / 2.94 s** (**+3 req, +1.2 s**). Only
2 whales need >1 page (BetMechanic 1311/3pg, SDTrading 515/2pg). At 4.12 s against the `*/30` = 1800 s window
(~0.23% duty cycle, 0 errors), **the cadence arithmetic is unchanged — the `*/30` ruling does not need revisiting.**

## ★ Finding to REPORT (per Jack): the fix DOES change what would get paper-traded
Full-book paging reveals **2745 open positions across the 14 whales vs the ~1400 the default page showed** (~1345
previously-hidden, concentrated in the 6 capped whales). Most are futures/novelty (skipped as `unknown`), but the
**in-pinned-category** ones among them will be **captured on the next poll** — that is the paper-completeness gain,
and it is a real change to what gets paper-traded. **This is the intended effect of the ticket, surfaced here before
it lands** (it lands only when the poller next runs live — which is Rung/Step-gated, not part of this build).

## §H CHECKPOINT — which list, and did the three bases stay separate?
This touched **no list's data BASIS** — it is an **observation-completeness fix upstream of all three**. It makes the
poller's raw `/positions` read complete, which feeds the **WATCHLIST (paper)** lane's *inputs* (`pm_paper_trade`
captures, and `pm_open_position` via ingest) — but the paper **basis** is unchanged (still `pm_paper_trade` →
`pm_paper_category_stats` via rollup). **PROSPECT** (`pm_category_stats`) and **LIVE** (P3) are untouched. The three
bases (completed / paper / live) stay separate; this changes *how completely we observe*, not *what any list reads*.

## RUNG LADDER (deploy — NOT authorized; build-only complete)
Same shape as Stage-0/Stage-1, **including the standing forced-644 step** (the tar-664 drift is a mechanism property
— PM_REBUILD_PLAN §D deploy note):
- **Rung 0 — box-scratch gate: DONE GREEN** (this doc; live untouched, PIDs unchanged, pytest_ethereum disabled).
- **Rung 1 — deploy 2 files + restart pm_web.** Deploy set = **`trading_corp/data/polymarket_data_api_client.py`**
  + **`trading_corp/prediction_markets/paper.py`** (NOT ingest.py, NOT persistence/db.py). ★ **Shared-module note:**
  the data client is a shared data-layer file — Gate the deploy against the BOX's current copy (re-hash), and
  confirm on-box that no non-PM division calls the changed methods (`grep -rl fetch_positions`); the change is
  additive (new symbols) + backward-compatible (fetch_positions returns full list or raises), so it is behaviour-
  neutral for any importer that doesn't page. Custody → manifest-assert → per-file CODE backup → place → **chmod 644
  + re-hash gate asserts blob==branch AND perms==644** → single `pm_web` restart. POST: /healthz+/farm 200, pm_web
  PID changed, engine PID unchanged, no DB write.
- **Rung 2 — one manual poller run** to realize the completeness gain (this is where the previously-hidden captures
  land; expect `incomplete`=0, `book_pages`>1 for BetMechanic/SDTrading, and MORE captures than the last run). Then
  adjudicate/rollup as already ruled. **Separate authorization; also step-4 cadence still separate.**
- **prod-live ledger** authored post-deploy from the manifest (fresh box re-hash), NOT pushed, ff-only over c77f618.

Runners: `cc\pm_exitdetect_probe*.sh`, `cc\pm_positions_terminate_probe.sh`, `cc\pm_t1_cost_probe.sh`,
`cc\pm_t1_boxscratch.*`.
