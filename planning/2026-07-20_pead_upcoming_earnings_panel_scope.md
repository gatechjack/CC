# PEAD "Upcoming Earnings" candidate-anticipation panel — SCOPE (discovery only; build on operator go)

Filed 2026-07-20. Read-only discovery per operator scope request. No prod/config/git changes were made
during discovery. All EODHD facts are EMPIRICALLY verified via live read-only calls (key pulled from
KeyVault `kv-tc-vtwbowt3wtkpy` secret `EODHD-API-KEY`, never printed). Scratch probe files removed.
Forks #1-#4 RESOLVED by operator 2026-07-20 (see bottom). Merge with `scan_evaluation` funnel CONFIRMED.

---

## PHASE A — DISCOVERY (verified on real data)

### A1. The Calendar add-on IS enabled — real per-row schema

`GET https://eodhd.com/api/calendar/earnings?api_token=KEY&fmt=json&from=YYYY-MM-DD&to=YYYY-MM-DD`
-> HTTP 200, 617 KB for a 7-day window. Envelope: `{type, description, from, to, earnings:[...]}`.

NOTE: the current codebase has NO cross-symbol calendar path — it only calls `/api/fundamentals/{SYM}.US`
per name, and `get_recent_announcements()` returns `[]` claiming "EODHD has no cross-symbol calendar
endpoint." That claim is FALSE (see the BACKLOG line filed alongside this doc). The Calendar add-on works;
it is simply unwired. This panel introduces a new (cheap) endpoint, it does not reuse an existing call.

Per-row schema (9 fields — CONFIRMED field-by-field, not assumed):

| field | type | meaning | notes |
|---|---|---|---|
| `code` | str | `TICKER.EXCHANGE` e.g. `AAPL.US` | ALL global exchanges returned; filter `.endswith('.US')` |
| `report_date` | str `YYYY-MM-DD` | announcement (calendar) date | the entry-clock anchor |
| `date` | str `YYYY-MM-DD` | fiscal period end | not the report date |
| `before_after_market` | str/null | **`BeforeMarket` \| `AfterMarket` \| null** | the BMO/AMC field the per-symbol path LACKS |
| `currency` | str/null | often null, sometimes `USD` | |
| `actual` | float/null | actual EPS | null pre-report, populates after |
| `estimate` | float/null | consensus EPS estimate | single number, present ~80% of in-universe names |
| `difference` | float | actual - estimate | 0 when either side null |
| `percent` | float/null | surprise % | null pre-report / when no estimate |

**Fields NOT present (do not design around them):**
- NO # of analysts / analyst count. NO revision data. NO distinct "prior-quarter actual" (the row carries
  only THIS period's `actual` + fiscal `date`; prior actuals come from the fundamentals endpoint or an
  earlier calendar window).

**Fields present that are NEW value vs current code:**
- `before_after_market` (BMO/AMC) — current code (`market_data.py:172-177`) explicitly FLAGS missing
  report-time as a known gap. The calendar supplies it; the fundamentals endpoint does not.
- `estimate` consensus EPS.
- `actual`/`difference`/`percent` populate AFTER the print — PROVEN on the past week
  (`PENG.US 2026-07-07 actual 0.71 vs estimate 0.49 -> percent +44.898`). This powers POST-ANNOUNCEMENT
  TRACKING (show actual-vs-estimate + computed SUE the moment it prints, before the entry fires).

### A2. Cost / limits (empirically measured)

- **A 7-day pull is ONE call.** `apiRequests` moved `32062 -> 32066` across intervening requests -> ~1 API
  unit per calendar request, and that ONE request returns the entire window (all 831 US rows in a single
  617 KB response). No pagination, no per-symbol fan-out.
- **Account:** `subscriptionType: monthly (paid)`, `dailyRateLimit: 100000`, used today `~32066`. A 1-2x/day
  7-day pull is a rounding error.
- **`symbols=` filter works** but returns only names actually reporting in-window (a 3-name test -> 0 rows).
  3,207 names will not fit a URL -> design = ONE all-US pull, intersect the universe locally.
- Efficiency note (out of scope; BACKLOG-filed): the current next-earnings-date derivation costs up to
  ~3,207 per-symbol fundamentals calls (24h-cached); the calendar gets the whole universe in ONE call and
  could pre-filter/replace that sweep — the same sweep that caused the 6/26 event-loop freeze.

### A3. SUE-profile feasibility for not-yet-reported names — FEASIBLE, no new data

- SUE model (`pead_signal.py:105-138`) is a **Seasonal Random Walk**: `UE(q)=actual(q)-actual(q-4)`;
  `SUE = UE[-1] / stdev(UE[-9:-1])` over `lookback=8`; needs >=13 quarters of actuals; **actuals only,
  not estimates**, all from `get_quarterly_eps(sym)` (EODHD `Earnings.History`, 24h-cached).
- The trailing UE series and its rolling `stdev(UE,8q)` are computable NOW for any name with >=13 quarters
  from data we already pull. `standardized_ue()` is pure.
- SEMANTIC PRECISION (see fork #4): pre-report you CANNOT know the real SUE. The overlay is a
  **plausibility** signal (own-noise stdev + recent UE) -> "plausible SUE>1.5?". Exact SUE only at/after
  the print. UI MUST label it "SUE plausibility", never "SUE".

---

## PHASE B — PANEL SCOPE

**Measured size:** calendar-US ∩ authoritative 3,207-name universe = **256 names / 7 days** (204/256 have
`estimate`; BMO/AMC populated 195/256 = 76%). Most of the 256 are regional banks the screen EXCLUDES ->
expect **~30-80 real candidates/week** after the screen. Right-sized.

**Rolling 7 days forward, one row per (in-universe) reporter. Columns / overlays:**
- Base: `symbol`, `report_date`, **report TIME (BMO/AMC)**, `estimate` (consensus EPS), prior actual
  (from fundamentals), `currency`.
- **IN UNIVERSE?** — in `config/nasdaq_composite.txt` (3,207). Watcher runs on prod and reads the IDENTICAL
  file the scan reads (fork #3).
- **PASSES SCREEN?** — reuse `pead_signal.ScreenParams`: min price $5, min vol30d 200k, min mktcap $100M,
  exclude financials/utilities, next-earnings >=65td. Show WHICH filter fails (screen returns a
  `reason_code`, e.g. `below-min-cap`, `financial/utility`).
- **SUE PLAUSIBILITY** — trailing UE history + rolling `stdev(UE,8q)` -> plausibility of SUE>1.5 (fork #4).
- **ALREADY HELD?** — suppress/flag held names: `paper_trade_record WHERE division='robinhood_pead' AND
  result IS NULL` (ledger-based; PEP/JBHT not hardcoded).
- FRACTIONAL-ELIGIBLE? — OMITTED (fork #1). Handled at entry-time by the existing skip.
- **POST-ANNOUNCEMENT TRACKING** — keep a name 1-2 days AFTER `report_date` showing `actual` vs `estimate`
  + the COMPUTED (exact) SUE, so the signal is visible BEFORE the entry fires. Fed by a small backward
  calendar window (past ~3d) where actuals populate.

---

## CACHE DESIGN (approved) — no synchronous HTTP on render (the 6/26 lesson)

Canonical **isolated side-process -> own DB -> dashboard reads mode=ro** (same as `sfp-card-watcher` and
`market-context-recorder`; both NOT git-tracked, deployed to `~/...`). Kills the 6/26 ~51-56 min
event-loop freeze BY CONSTRUCTION: the fetch runs in a SEPARATE process (can't block the engine loop even
while fetching), render only reads a local SQLite DB, and the unit sidesteps the `main.py` drift.

- **Side-process:** `pead-earnings-watcher` (systemd unit, `User=azureuser`, `Restart=on-failure`).
  Source `Desktop\pead_earnings\box\`, deployed to `~/pead_earnings/`. Imports only the PURE modules
  (`pead_signal` = screen+SUE math, no IO; `earnings_provider` = stdlib/urllib fetch, 24h cache) — NOT the
  engine/graph.
- **Cadence:** 2x/day — pre-market (~07:00 ET, fresh estimates + BMO/AMC) and post-close (~17:00 ET,
  captures the day's actuals for post-announcement tracking).
- **Per refresh:** 1 forward calendar call (7d) + 1 backward calendar call (~3d) + per screen-PASSING name a
  `get_quarterly_eps` call for the SUE plausibility (24h-cached; ~30-80 names/day). Total <~100 units/day
  vs 100k limit.
- **Storage:** own DB `~/pead_earnings/earnings_watch.db`, single table e.g.
  `earnings_watch(code, report_date, report_time, estimate, actual, difference, percent, in_universe,
  screen_ok, screen_fail_reason, sue_stdev, sue_recent_ue, sue_plausible, computed_sue,
  phase[upcoming|reported], already_held, fetched_ts)` (no `fractional_eligible` per fork #1).
- **Read path:** dashboard reads `sqlite3.connect("file:...?mode=ro", uri=True)` inside `asyncio.to_thread`
  (identical to `sfp_llm_analysis_view.py`). Panel added to `/telemetry/pead` via `web/pead_view.py` (new
  dict key) + `partials/pead_live_sections.html` (rides the existing 15s HTMX poll — no new route strictly
  required).

---

## MERGE WITH scan_evaluation FUNNEL — CONFIRMED (one build)

BACKLOG.md P3 (~line 1488): `scan_evaluation` table exists (`db.py:385-394`) + reader is wired
(`pead_view.py:238` -> `scan_rejection_tally`), but the WRITE-PATH is DARK (0 rows) — `pead_strategy.scan()`
never calls `insert_scan_evaluation`. Intended funnel: `universe -> screened -> SUE>1.5 -> top-quintile ->
entered`.

Two halves of ONE funnel, same page, same screen/SUE code, same table concept:
- **Upcoming panel = FRONT half** (reporting -> in-universe -> passed-screen -> SUE-plausibility -> pending)
  — side-process + dashboard read, forward-looking.
- **scan_evaluation = BACK half** (the live scan's entered/rejected verdicts + wave size + quintile cutoff)
  — engine write-path change, forward-only.
- **Post-announcement tracking is the bridge** (actual + computed SUE connects "we saw it coming" to "here's
  why we did/didn't enter").

Scope as ONE "PEAD signal observability" deliverable, ONE page, ONE coordinated restart (BACKLOG.md already
asks to batch scan_evaluation with the other deferred PEAD/dashboard fixes into one restart).
FORWARD-ONLY — never reconstruct historical waves (that sweep caused the 6/26 freeze).

---

## RESTART-GATED vs HOT

| Component | Gating |
|---|---|
| `pead-earnings-watcher` side-process + its DB + systemd unit | **HOT** — separate process; deploy/restart without bouncing the engine (like sfp-card-watcher). |
| Dashboard PANEL (`web/pead_view.py`, `partials/pead_live_sections.html`, any `routes.py`/template edit) | **RESTART-GATED** — web is IN-PROCESS with the engine (uvicorn asyncio task, `main.py:2682`). Flat-guarded restart; refresh RH pickle first ([[prod-restart-rh-pickle-hazard]]). |
| `scan_evaluation` write-path (`pead_strategy.scan()`) | **RESTART-GATED** (`.py`). |
| Screen thresholds / cadence knobs if surfaced in `config/strategies.yaml` | **HOT** (config reload), if wired that way. |

-> Batch the two restart-gated pieces (panel + scan_evaluation) into ONE engine restart.

---

## PROD-vs-GIT DRIFT ACCOUNTING (verified 2026-07-20; prod source is AHEAD of git main)

Prod `/home/azureuser/trading_corp` is NOT a git checkout (deploy = scp file-copy).
`WorkingDirectory=/home/azureuser/trading_corp` (confirmed via `systemctl show`). Confirmed prod paths:
`trading_corp/web/routes.py`, `trading_corp/web/pead_view.py`, `config/nasdaq_composite.txt`,
`config/strategies.yaml`.

**Measured drift (md5, local `main`/root vs prod):**

| file | local md5 | prod md5 | verdict |
|---|---|---|---|
| `web/routes.py` | `fc0d3389...` | `bacec02b...` | **DRIFTED** (07-18 RH-auth). Edit FROM prod copy. |
| `web/pead_view.py` | `081e2805...` | `bc7e1b58...` | **DRIFTED**. Edit FROM prod copy. |
| `config/nasdaq_composite.txt` | `60993af9...` (root, 3207 CRLF) | `38b82853...` (3207 LF) | **CRLF-cosmetic ONLY** — parsed set+order IDENTICAL; md5 delta = 3207 stripped CRs. "IN UNIVERSE?" will not lie. |

- Both dashboard files I'd edit are prod-ahead -> pull the PROD copies before editing (do NOT edit git-main
  versions). `main.py` is also prod-ahead (RH-auth); the side-process rides its OWN unit, avoiding main.py.
- Universe fork (#3) RESOLVED: content is identical; the watcher runs on prod and reads the exact file the
  scan reads, so no divergence is possible even if the universe is later updated on prod.

---

## RESOLVED DECISIONS (operator, 2026-07-20)

1. **FRACTIONAL-ELIGIBILITY: OMIT** from the panel; surface at entry-time. No engine-side lazy enrichment
   (would need an RH session / write-back, coupling the deliberately-pure side-process). Rare (zero
   ineligible hits so far) and already handled by the entry-time skip. Revisit only if it bites.
2. **MERGE: CONFIRMED** — one build (front upcoming + back scan_evaluation), one page, one coordinated
   restart, FORWARD-ONLY.
3. **UNIVERSE PATH: RESOLVED** — prod `@config/nasdaq_composite.txt` -> `/home/azureuser/trading_corp/
   config/nasdaq_composite.txt`; content byte-identical to local (CRLF-only md5 diff). Watcher (on prod)
   reads that IDENTICAL file.
4. **SUE SEMANTICS: CONFIRMED** — pre-report = PLAUSIBILITY (own-noise stdev + recent UE), not a predicted
   SUE. LABEL the UI "SUE plausibility". Exact SUE appears only on the post-announcement row.

## OPEN / NEXT (on operator go)
- Build the `pead-earnings-watcher` side-process + `earnings_watch.db` (HOT deploy).
- Add the dashboard panel + wire the `scan_evaluation` write-path in `pead_strategy.scan()` (ONE
  restart-gated batch; edit dashboard files FROM prod copies; refresh RH pickle pre-restart).
- Build workspace: worktree `cc-wt-pead-scope` on branch `pead-earnings-panel-scope`.
