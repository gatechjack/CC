# Stage 2 · Phase 2 — Per-Category Content (BUILD ONLY, box-scratch PENDING, NOTHING LIVE)

**Date:** 2026-08-28 · **Branch:** `prediction-markets-stage2-phase2-2026-08-28` (worktree `cc-pm-stage2-phase2-wt`,
off phase-1 record tip `839f452`) · **Mode:** BUILD ONLY. No live deploy, no restart, no DB write, no
poller/adjudicate/rollup, no migration. Phase 3 and Stage 3 NOT authorized.
**Restate the three lists + three bases (anti-drift first line):** **Prospect** = completed trades
(`pm_closed_position` → `pm_category_stats`, code `candidate`); **Watchlist** = our paper trades
(`pm_paper_trade` → `pm_paper_category_stats`, code `pinned`); **Live** = live trades (P3, not built).

**Branch choice:** a NEW branch off `839f452` (not the phase-1 branch) — phase 1 is already deployed
(prod-live ledger `7ca932a`), so the phase-2 diff stays isolated for its own review + deploy.

---

## 1. WHAT WAS BUILT (fills the two sections of `/farm-league/{category}`)

**TOP — Watchlist** (PAPER basis, `pm_paper_category_stats` via `farm.farm_rows(status=PINNED)`): one row per
pinned pair — **live OPEN count (R6, first-class)**, closed count, **`stale` + `void` beside closed** (survivorship
never invisible), then win% / roi(cost) / net paper pnl / cost over CLOSED trades (honest-empty `—` when a pair
is all-open). Actions: **Analyze WIRED** (POSTs to the existing `/farm/analyze` route + `#pm-analyze-panel`),
**Demote / Promote rendered but DISABLED** (Stage 3 / P3). The whale name → the **paper detail**.

**BOTTOM — Prospects** (COMPLETED basis, `pm_category_stats` via the **F-4 repurposed `query_scoreboard` ranker**
filtered to this category's candidates): ranked rows with the caveat columns (two-sided% / single-game% / avg win
px / data-quality flags) — caveats travel with the number; every aggregate drills through to its rows via the
**existing** `/whale/{w}/{cat}` completed detail (reuses `pm_position_rows.html`). **Promote-to-watchlist rendered
DISABLED** (Stage 3). Today the list is **honest-EMPTY** ("no search yet") — Search has never run.

**NEW paper detail** `GET /watchlist/{wallet}/{category}` (pinned whale → ALL its paper trades + paper stats).
This is a **necessary new build**: a pinned whale's detail must show PAPER trades, and no paper reader/renderer
existed (`paper.py` has no display fn; `pm_position_rows.html` is `pm_closed_position`-shaped). Wiring a pinned row
to the completed `/whale` detail would be the exact basis violation this stage guards. The paper reader lives in
`positions.py` (the display module) — **`paper.py` (rollup/adjudicator/poller) is UNTOUCHED**.

**Deploy artifact set = 8 PM files** (the box==branch gate re-hashes these):

| sha256 (branch) | path (under `trading_corp/prediction_markets/`) | change vs box (phase-1 = `7ca932a`) |
|---|---|---|
| `53ae5ff2ad87f51b370fc8fe8033c0e495741531f14ad5d755efd5718ad6b1bb` | `positions.py` | MODIFIED (+2 paper readers) |
| `d304fbe0e2c161bce7d8cd9155e1f3d32000475bfb36c7a4742d92c9d7edbbb1` | `web/app.py` | MODIFIED (ranked prospects + paper-detail route) |
| `16e30c73fa835e6648432cfdaf872c0310e7d46438522ccdd7e3f4d099b54ae1` | `web/templates/pm_farm_category.html` | MODIFIED (skeleton → filled) |
| `7191f8d4d6499cd19d1cddbdf4e371bca794b3217a2597b05c1390cc5c5355aa` | `web/static/pm.css` | MODIFIED (+phase-2 block) |
| `88201a5ed564595014c8fc3ea35cf133856dcbf8a94651b03a5eb895262027ab` | `web/templates/pm_watchlist_whale.html` | NEW |
| `8a52cb7ef7248e576e999adb350cc6ee409561a26a53287ff5da26d81b487789` | `web/templates/partials/pm_watchlist_rows.html` | NEW |
| `a589ad9401be4157d3300e9151adbbb7fdebd3f0871f74d33c6febb89b4cc98a` | `web/templates/partials/pm_prospects_rows.html` | NEW |
| `2c6f136bd062acb809b285186722ba77d1a751ea53e0c3062fc77ad16e55f89b` | `web/templates/partials/pm_paper_trade_rows.html` | NEW |

**NOT deployed (branch-only):** `tests/prediction_markets/test_stage2_phase2.py`, the edited `test_stage2_nav.py`,
this report, the plan edit. **NOT touched:** `paper.py`, `stats.py` (query_scoreboard reused as-is — the candidate
scoping is a loader-level filter, no data-layer change), `farm.py`, `db.py`, `pm_macros.html` (so **legacy `/farm`
stays byte-identical** — the Analyze button is inlined on the new rows only, to render the category UPPERCASE in the
tooltip without editing the shared macro), any migration, the engine, poly_kalshi_mlb, MACE, PEAD, bitunix.

## 2. ★ WHAT THE FILLED PAGE WILL LOOK LIKE WITH REAL LIVE DATA

Live state (schema 9): ~92 active pinned pairs across 15 categories; the paper lane is young — **2 closed paper
trades total, both ufc** (evanng +11.39, Kh4mz4t +89.54), the rest open/pending. So:
- **Most category pages** (mlb, nba, atp, …): the **Watchlist** lists that category's pinned whales, each showing a
  real **OPEN count** and **honest-empty performance** (`—` for win%/roi/net — 0 closed yet). E.g. an mlb pinned
  whale with SDTrading's game-line captures shows `open = N`, `closed = 0`, win% `—`. That is INFORMATION (the lane
  is alive), not missing data. **Prospects: empty** ("No prospects yet") on every page — Search hasn't run.
- **The ufc page is the one with real closed numbers:** the two adjudicated ufc paper trades render as `closed = 1`,
  win% `100%`, a positive roi, and net paper pnl `+11` / `+90` for evanng / Kh4mz4t respectively — the first real
  paper performance on the platform. Their `open`/`stale`/`void` counts render beside it.
- **Clicking a Watchlist whale** → its paper detail: the paper-stats panel (open/closed/stale/void/win%/roi/net) +
  a live-first table of ALL its paper trades (status badge, market, entry px, size, cost, pnl, won, dates).
- **Exact per-category pairs + numbers are surfaced by the box-scratch render smoke** (§3 render, against a WAL-safe
  `.backup` copy of live) — it picks a category with open paper trades and prints the counts.

## 3. §H CHECKPOINT ANSWER

*Which of the three lists did this touch, and did the three bases stay separate?* It filled **WATCHLIST** (paper,
`pm_paper_category_stats`) and **PROSPECTS** (completed, `pm_category_stats` via the ranker) on the category page,
and built the pinned-whale **paper detail**. **LIVE** is P3, untouched. The two bases meet on one page but never
share a query: Watchlist reads `farm_rows(PINNED)`→`pm_paper_category_stats`; Prospects reads
`query_scoreboard`→`pm_category_stats`; the paper detail reads `pm_paper_trade`; the prospect detail reads
`pm_closed_position`. **The BASIS test proves the DISPLAYED VALUES:** a pair seeded with paper-40% and completed-89%
renders **40% in the Watchlist and never 89%**, and the candidate's **60% completed** shows in Prospects — neither
list leaks the other's pair.

## 4. SANITY-CHECK FINDING — "/farm 88 polled, 4 with open" vs "pm_paper_trade 118 open" IS RECONCILABLE (NOT a bug)

From the SQL (`farm.py` + `farm_summary`): the two numbers count **different populations**.
- **"4 with open positions"** = active pinned **PAIRS** (`WHERE wl.status='pinned' AND wl.active=1`) that have ≥1
  open `pm_paper_trade` row. A **pair count**, active-gated. ("88 polled" = active pinned pairs with a
  `last_polled_ts` — i.e. 88 polled-nothing-open + 4 polled-with-open = the 92 active pairs.)
- **"118 open"** = `COUNT(*) FROM pm_paper_trade WHERE status='open'` across **ALL rows, no gate** — the bulk sit in
  the **22 DEACTIVATED (`active=0`) pairs** (Stage-0 removal preserves their `pm_paper_trade` rows; R1) and never
  appear on `/farm`.
So `A (all open rows) = B (active-pair open) + C (deactivated-pair open) + D (orphan)`, while `/farm`'s "M with open"
is a count of active PAIRS, not trades. **No defect — the active gate is working exactly as designed (R1/R5).**
**Box-scratch §2 confirms the arithmetic with the live numbers** before we build further on it.

## 5. PHASE-2 RUNG LADDER (Stage-0/1/phase-1 shape; NOTHING below is authorized — HALT here)

- **Rung 0 — BOX-SCRATCH GREEN (the gate).** `cc\pm_stage2_p2_boxscratch.ps1` (READ-ONLY): archives the committed
  branch → `/tmp` scratch → `venv/bin/python -m pytest tests/prediction_markets/ -p no:pytest_ethereum -q` (full
  suite incl. `test_stage2_phase2.py` + the updated `test_stage2_nav.py`); **§2 reconciliation** query on a
  `.backup` copy of live (confirms A=B+C+D and the pair-vs-trade populations); **§3 render smoke** vs the copy
  (a real category page + a real paper detail, vocab-leak check, legacy pages still 200). Live DB byte-untouched;
  engine/pm_web PIDs asserted unchanged. **STOP if any test fails.** *(Status: PENDING Jack's run — no local Python.)*
- **Rung 1 — deploy the 8 code files + restart pm_web** (the phase-1 four-step shape: PRE-baseline → deploy
  (custody + backup + forced-644 + re-hash gate; NO restart) → root `az` restart → POST). **Modified-vs-new custody
  split (computed at deploy against the box = phase-1 state `7ca932a`):** 4 MODIFIED (`positions.py`, `web/app.py`,
  `pm_farm_category.html`, `pm.css`) get a pre-place `box==BASE` custody check + a per-file CODE backup; 4 NEW
  templates assert absent pre-place (nothing to back up; rollback = remove them). **★ tar lands 664 → force `chmod
  644` + assert perms==644** (standing). **POST is two-sided** like phase 1: legacy `/`, `/scoreboard`, `/farm`
  **byte-identical** to baseline (nothing bled — `pm_macros.html` untouched is why this holds); `/farm-league/{cat}`
  now renders the FILLED sections; `/watchlist/{w}/{cat}` 200; casing uppercase on live; DB untouched (schema 9);
  engine PID unchanged. prod-live ledger advances by a fast-forward additive commit; `95e78c4` stays reachable.
- **Activation path:** all 8 activate on the **pm_web restart** — `app.py` is the entrypoint, the templates are
  pm_web-rendered, `pm.css` is pm_web-served, and **`positions.py` is imported by the pm_web request path**
  (`web → … → positions`, via the whale/watchlist loaders). **No `pm_cli`-loaded file changed** (paper.py / db.py /
  the client untouched); **no migration** (schema stays 9). One restart loads everything.

**HALT. Phase 3 and Stage 3 are not authorized.** Runner: `cc\pm_stage2_p2_boxscratch.*`.
