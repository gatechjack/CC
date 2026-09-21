# PM FARM LEAGUE — WATCHLIST SPLITS PAGE (per category) — 2026-09-21

**STATUS: BUILT + TESTED (25 new, full-suite differential 24==24) + RENDERED (1600/1280/phone, all views/states) +
BOX-VERIFIED (read-only, MLB+NFL tie out to the cent) + COMMITTED + BRANCH PUSHED. NOT DEPLOYED / NOT RESTARTED.**
Branch `pm-watchlist-splits-2026-09-21` off `origin/prod-live` @ **`5b8df49a`** (git truth = box), worktree
`C:\Users\AA Incorporado\cc-watchlist-splits-wt`. DEPLOY / RESTART are Jack's (Board actions).

A new **read-only** page `/farm/{category}/splits`: the category's pinned-watchlist whales' OPEN paper positions,
decoded into game × market-type × side and drawn as a stake-vs-headcount split (splits table + whale grid + heatmap).
Nothing on it places, sizes or cancels; no route it adds reaches the order path; pm_web-only, no engine/broker import.

--------------------------------------------------------------------------------
## 1. DISCOVERY (D1–D6) — verified from the code + the box, not from notes

| # | Question | Finding (file:line / box evidence) |
|---|---|---|
| **D1** | Where open positions live | **`pm_paper_trade`** (migration 005, `db.py:361`) is the source — NOT `pm_open_position`. `paper.poll_pinned` (`paper.py:121`) polls `/positions` for `pm_watchlist status='pinned' AND active=1` (`paper.py:142`) and writes open paper entries (`INSERT`, `paper.py:260`). Columns used: `wallet, category, slug, outcome, whale_size_at_observation` (whale shares), **`entry_price_avg_at_observation`** (whale entry avg price), `cost_basis` (= `size_basis`×px = OUR paper notional, NOT the whale's), `last_observed_ts` (bumped every poll → the real read age), `status`. **Cadence (box crontab):** `*/30 * * * * pm_cli paper-poll` writes `pm_paper_trade` — **30 min, confirmed** (the brief's "believed 30 min" is correct for THIS table). `pm_cli refresh` (daily 05:00) writes the *other* table `pm_open_position` — a whale-discovery pull that is currently 3.8+ days stale and is **not** used here. Box now: MLB 28 pinned / 18 open-pinned; NFL 16 / 10; `max(last_observed_ts)` age **0.3 h** (fresh). |
| **D2** | parse_poly_bet coverage | `data/sports_structural_match.parse_poly_bet` (`:125`), imported by pm_web as `live_view._parse_poly_bet` (`live_view.py:36`) — **stdlib+data only, no broker**. Returns `ParsedBet(market_type, away/home_code+name, side, line, leg, anchor_side)`. **Structural categories = `cfb, mlb, nba, nfl, nhl, wnba`** (`LEAGUES` keys, box-confirmed). Market types that parse: **moneyline / total / spread** (box: MLB open-pinned = 8 ML / 7 TOT / 2 SPR after the pinned filter). Tennis/UFC/CS2/soccer/fed have **no** `LEAGUES` entry → the page shows an honest "no structural decode" state. Props / futures (`-nrfi`, `-worldseries-…`, team-totals, first-half) return non_moneyline/non_sport → **omitted + counted** (R7). |
| **D3** | pm_whale_score tier | `scoring.py:22` — the ONLY tiers are `INSUFFICIENT_DATA / PROMOTE / WATCH / PASS` (box `SELECT DISTINCT tier` = exactly those). **No FADE / NEUTRAL anywhere.** No score row → `_score_cell(None)` (`app.py:385`) → `analyzed:False` → **"not analyzed"**, never a 0/low tier. |
| **D4** | Attachment read (R3 trusted) | Pinned set = `farm.farm_rows(conn, status=PINNED, category=)` (`farm.py:151`, LEFT JOIN from `pm_watchlist` → a pinned whale renders even with no stats/name). Trusted = `SELECT wallet, account_id FROM pm_subdivision_attachment WHERE category=? AND active=1` (the SAME source the Farm live-whale badges use, `app.py:578`) → `{wallet: [account…]}`; account label = `kalshi_jack`→`Jack`. |
| **D5** | Kalshi flag / start time | **Kalshi flag: DROPPED (R8).** No pm_web index enumerates *which markets exist*: `milestones.py` stores start-times keyed by Kalshi *event ticker* (`StartsResult.starts`, `milestones.py:66`), not a presence catalog; `marks.py` catalogs only *held/open* tickers (`marks.py:37`). Absence ≠ non-existence → per R8 the flag can only be approximated, so it is dropped entirely. **Start time: "start time unavailable" (R6).** A Poly slug is `mlb-away-home-YYYY-MM-DD` (date only, no HHMM); the milestone `starts` key includes HHMM the Poly side can't reconstruct, so no clean join. The page shows the game DATE (from the slug) and "start time unavailable" — honest, never invented. |
| **D6** | Farm page reuse | Route `/farm/{category}` (`app.py:773`) → `_load_farm_category` → `pm_farm_category.html` (extends `pm_shell.html`, `pm.css` family). **Sort must be server-side URL params, NOT `pm_sort.js`** (confirmed ascending-first trap, `pm_sort.js:21`; the Prospects table negates sort values to compensate) — so this page renders mode/group/sort from the URL (JS-off safe), matching R3 + the Deploy-12 roster precedent. `/farm` nav auto-highlights `/farm/{cat}/splits` (`pm_shell.html`, `path.startswith('/farm')`). Farm route imports are PM-only (no broker). |

--------------------------------------------------------------------------------
## 2. TWO FLAGS THE BRIEF ASKED ME TO SURFACE (before any deploy)

- **R4 price semantics — CONFIRMED entry cost, not a mark.** `entry_price_avg_at_observation` is the whale's blended
  avg cost at observation. Box proof: for the parsed open-pinned rows, `Σ(size×avg)` = **$54,295.43 (MLB) / $32,856.07
  (NFL)**, which matches the whale's committed dollars; the paper `cost_basis` sum is only **$779.99 / $206.99** (that
  is `size_basis`=100×px, our fixed paper notional). The page uses **shares × entry price** (R4), never `cost_basis`,
  never notional. The column is labelled "at cost".
- **R5 cadence — 30 min CONFIRMED, but freshness is per-row and can lag.** `pm_paper_trade.last_observed_ts` is bumped
  each 30-min `paper-poll`. The page shows the **last-refresh date/time in ET + the real age**, and labels any read
  past 30 min **STALE** (whole-page banner + dimmed bars). At build time the box read was fresh (0.3 h); an earlier
  discovery window caught a stale gap (the poll had not run for ~19 h on some rows) — the page renders that honestly as
  STALE. No faster polling was added.

--------------------------------------------------------------------------------
## 3. WHAT CHANGED (pm_web only; NO subdivision.py, NO db.py, NO migration, NO engine file)

- **`live_view.py` (additive, pure):** `build_watchlist_splits` (the reader), `splits_frame` (side→A/B canonical
  frame), `_split_stats` / `_splits_shape`, `splits_shown` / `splits_sort_key` / `splits_ordered` (server-side
  mode/sort/group), `splits_treemap` (server-side squarified heatmap), constants `SPLITS_THIN_WHALES=4`,
  `SPLITS_DIVERGENCE_POINTS=18`, `SPLITS_STALE_AFTER_SEC=1800`, `SPLITS_CONSENSUS_PCT=70`, `SPLITS_LEAN_PCT=56` (R9).
  **No new import** — reuses the in-module `_parse_poly_bet` / `_STRUCT_LEAGUES` (standalone invariant preserved).
- **`app.py` (additive):** loader `_load_watchlist_splits` + route `GET /farm/{category}/splits` (mode/sort/group/view
  in the URL). Reuses existing imports (`farm`, `live_view`, `_score_cell`, `_load_whale_score_map`) — no engine/broker.
- **`pm_farm_splits.html` (NEW):** the page — scoped `.wls` styles (no shell/cache-bust change; loaded only on this
  page), splits (default, JS-off) + whale grid + heatmap; `<details>` expanders (native, no JS); phone stacks the
  bars and hides the heatmap (R10).
- **`pm_farm_category.html`:** a link to `/farm/{category}/splits` beside the sections (R1).

**File diff vs `origin/prod-live` (CR-stripped sha16 BEFORE → AFTER):**

| file | BEFORE | AFTER |
|---|---|---|
| `web/app.py` | `bf9895b03ed2a7b9` | `bcece565e3cb0693` |
| `web/live_view.py` | `2cba1fe691bffba2` | `cd5e69f220ea8178` |
| `web/templates/pm_farm_category.html` | `fb3b891cf87705c1` | `678c9bf5c6668c94` |
| `web/templates/pm_farm_splits.html` | *(new)* | `f2a756ad13ae8aa0` |

Git-only (NOT deployed): `tests/prediction_markets/test_watchlist_splits.py` (25 tests), this report.
**subdivision.py / db.py UNTOUCHED; no migration.** `farm.py` / `farm_actions.py` reused unchanged (`23f91d44`).

--------------------------------------------------------------------------------
## 4. VERIFICATION

- **Tests:** full-suite differential = **24 FAILED after == 24 baseline (origin/prod-live), 0 new failures**
  (proven by running the suite in a throwaway base worktree and diffing the fail lists). `test_watchlist_splits.py`
  (25) all pass: `splits_frame` side→A/B (ML/TOT/SPR) + fail-closed props; shapes SINGLE/UNANIMOUS/CONSENSUS@70/
  LEAN@56/SPLIT; divergence@18 and thin<4 boundaries exact; **stake = shares×entry, never notional**; trusted filter +
  guard; unparsed omission+count; **stale at 29 vs 31 min**; refresh-ET present; treemap area∝stake; sort; served page
  (no raw slug/ticker as a label, **Kalshi flag absent**, refresh-ET + read-only + GET-only [POST 405], not-analyzed,
  phone hides heatmap, stale banner, vocab clean).
- **Renders (`cc/renders_wlsplits/`, viewed):** splits by-game (all shapes + thin + divergence + guard), trusted,
  compare, by-consensus (shape buckets), whale grid, heatmap treemap — at 1600/1280/phone; plus an NFL **stale** page,
  an NHL **zero-pinned** honest-empty page, and an ATP **no-structural-decode** page. Phone stacks the rows with the
  bars intact and hides the heatmap (R10).
- **Box tie-out (read-only, `cc/pm_wlsplits_verify_ro.py` on the box → `cc/pm_wlsplits_verify_local.py` runs the real
  reader on the real rows):** for **MLB and NFL**, the reader's `total_stake`, every per-(game×market) stake, the
  `unparsed_count`, and `refresh_ts == max(last_observed_ts)` **all tie to the cent**: MLB $54,295.43 / 10 markets /
  1 unparsed; NFL $32,856.07 / 5 markets / 5 unparsed. R4 re-proven on box (stake ≠ `cost_basis`).

--------------------------------------------------------------------------------
## 5. RULINGS COMPLIANCE (R1–R10)

R1 `/farm/{category}/splits`, one template every category, linked from the category page ✓ · R2 pinned watchlist
whales only (pinned filter on `pm_paper_trade`) ✓ · R3 trusted = live-attached active=1; tiers PROMOTE/WATCH/PASS/
INSUFFICIENT_DATA + "not analyzed"; mode/group/sort in the URL, JS-off safe ✓ · R4 stake = shares×entry (cost),
flagged + confirmed ✓ · R5 30-min poller, last-refresh ET + real age, stale ≥30 min, confirmed + flagged ✓ · R6
positions-only, date from the slug, "start time unavailable", no scores/state ✓ · R7 group game×market×side via
parse_poly_bet; unparsed omitted + counted; per-category coverage reported ✓ · R8 Kalshi flag **dropped** (not
answerable) ✓ · R9 thresholds as constants + tests ✓ · R10 phone hides heatmap, splits stack with bars intact ✓.

--------------------------------------------------------------------------------
## 6. DEPLOY SHAPE (for Jack — reserved)

**pm_web-only. 4 deploy-surface files (2 modified + 1 new template + 1 modified template). NO subdivision.py, NO
db.py, NO migration, NO engine-shared file. Engine `trading-corp` NEVER restarted.**
- Model: branch off `origin/prod-live` = box = truth. Plain scp+tar diff of the 4 files, drift-gated (box == BEFORE
  for the 3 modified; the new template absent on box), backup-is-a-gate (dated dir, each sha == box before any write),
  post-write CR-sha16 == AFTER + `py_compile app.py live_view.py` + import/standalone gate (`/pm/arm`=0, 0 engine/
  broker/execution modules), rollback-all (restore modified + `rm` the new template) on any mismatch.
- **ONE `prediction-markets-web` restart** under the standing restart-authority grant (verify by MainPID +
  ActiveEnterTimestamp, never the az exit code); confirm `trading-corp` PID + NRestarts UNCHANGED before + after.
- No new static asset / no `pm_shell.html` change → **no cache-bust bump** (the splits CSS is inline-scoped, served
  only on this page).
- prod-live advance (SAME session, post-check green): FF `origin/prod-live` to the deployed commit + tag; re-verify
  box == prod-live. FF-only; non-FF → STOP.

--------------------------------------------------------------------------------
## 7. RUNNERS (cc/, read-only)
`pm_wlsplits_discover_ro` + `pm_wlsplits_discover2_ro` (D1–D6 box discovery: STRUCT coverage, tiers, cadence, price
semantics, parse coverage), `pm_wlsplits_render.py` (the seeded all-shapes/stale/empty/unsupported renders in
`cc/renders_wlsplits/`), `pm_wlsplits_verify_ro.py` (box ground-truth dump) + `pm_wlsplits_verify_local.py` (runs the
real reader on the real rows and ties out to the cent). No deploy runner authored (deploy is Jack's).
