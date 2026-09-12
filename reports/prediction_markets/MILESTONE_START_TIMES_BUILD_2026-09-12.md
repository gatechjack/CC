# PM /live — MILESTONE START-TIME FEED (BUILD + STAGED DEPLOY) — 2026-09-12

**STATUS: BUILT + TESTED + BOX-SCRATCH-PENDING + REVIEWED + COMMITTED. NOT DEPLOYED / NOT PUSHED.**
Branch `pm-live-milestone-starts-2026-09-12` off `origin/prod-live` @ `93b5e908` (git truth). Worktree
`C:\Users\AA Incorporado\cc-pm-live-milestone-starts-wt`. DEPLOY / PUSH / RESTART are Jack's reserved actions.

--------------------------------------------------------------------------------
## 1. WHAT + WHY

The `/live` tile page classifies each sub-division LIVE / UPCOMING / SETTLED / INACTIVE / UNATTACHED. Until now only
**MLB** (a game feed) and the **ticker-HHMM sports** (cs2/nfl/nba/nhl/wnba/cfb) could ever read LIVE; **tennis
(atp/wta), UFC and the soccer leagues** have date-only tickers, so they stayed UPCOMING even once underway. This
change lands the **first real cross-category feed: EVENT START TIMES from Kalshi's public `milestones` catalog**,
plugged into the classifier's existing per-category feed hook (`live_view._event_underway`). The tile's activity
state becomes an honest clock compare (`start <= now AND our position still open`).

**START TIMES ONLY.** Live scores / play-by-play are explicitly OUT OF SCOPE (a separate per-event cost, Jack's
later decision). We do not build them, and we do not read the milestone `details.status` field — the probe proved it
LIES (finished matches read `not_started`), and we don't need it: "finished" is something we already know from our
own settlement.

**pm_web-ONLY.** Public, unauthenticated, stdlib-only reader. No engine, no restart of `trading-corp`, no shared
file, no schema, no migration. The standalone / credential-free / can-never-place-an-order invariant is preserved
(`milestones.py` imports only `json`/`logging`/`urllib`/`dataclasses`/`datetime`).

Research this builds on (DO NOT re-probe): `[[pm-milestone-start-time-2026-09-12]]` (72-GET probe, filed). The join,
the `T00:00:00Z` placeholder trap, the `details.status`-lies trap, the two-catalog (Sports+Esports) coverage, and
the cost model all come from there and from the proven runner `cc/pm_milestone_heldtickers_ro.ps1`.

--------------------------------------------------------------------------------
## 2. HOW IT WORKS

- **`web/milestones.py` (NEW).** `fetch_starts()` sweeps the milestone catalog for categories `("Sports","Esports")`,
  cursor-paginated, `minimum_start_date = now-36h` (sent LITERALLY, exactly as the proven probe runner does), and
  indexes `related_event_tickers -> start_date`. The join key is EXACT and NON-FUZZY: a held **market** ticker minus
  its final `-<suffix>` == the **event** ticker (`KXATPMATCH-...ABCDEF-XYZ` -> `KXATPMATCH-...ABCDEF`), and
  `related_event_tickers` is a superset across market types, so whichever series we hold resolves. `start_date` is a
  real UTC kickoff instant; a `T00:00:00Z` date-only PLACEHOLDER folds to UNKNOWN (never read as midnight).
- **`web/ui_cache.py`.** `CacheSnapshot` gains `starts` + `starts_as_of` / `starts_attempt_ts` / `starts_ok` /
  `starts_error`. The index PERSISTS across the 60s marks/feed polls (start times don't change).
- **`web/poller.py`.** The existing 60s poller sweeps milestones **only when a cadence gate is DUE** — REFRESH **6h**
  after a good sweep, RETRY **20m** after a failure or an empty first boot — and reuses the cached index every other
  cycle. A transient failure or an empty window NEVER blanks a good index (immutable start times -> a stale index is
  safe). Every sweep logs its cost (event-ticker count + page count, LOUD if the page cap is hit).
- **`web/live_view.py`.** `start_ts_for_ticker()` returns the ticker-HHMM start (LIVE_CAPABLE) ELSE, for a
  `MILESTONE_START_CATEGORIES` category only, the milestone start. `_event_underway` / `_live_event` / `_next_event`
  all consume it; `build_subdivisions_context` threads `starts` through. **`starts=None` reproduces the exact
  pre-feed behaviour.**
- **`web/app.py`.** `_load_live_list` passes `starts=snap.starts` into `build_subdivisions_context` (one-line change).

**MLB feed authority preserved.** `_event_underway`'s MLB branch is unchanged and authoritative — a milestone can
NEVER override the MLB feed (the feed knows in-progress vs final; a start time alone cannot). MLB / cs2 / nfl / cfb /
nba / nhl / wnba keep their ticker-HHMM/feed source; the milestone is used ONLY for the explicit allowlist
`MILESTONE_START_CATEGORIES = {atp, wta, ufc, epl, ucl, uel, lal, fl1, sea, bun, mls, bra, mex}`. **fed is excluded**
(no start state; also absent from the catalog by construction).

**Honest fallback everywhere.** A category or event with no milestone, or a placeholder start, reads exactly as it
does today (UPCOMING, "start time unavailable") — never a guessed time, never a fabricated LIVE.

--------------------------------------------------------------------------------
## 3. COST (the shared-IP caveat — say what we spend)

pm_web runs on the box and shares the engine's Kalshi source IP; a 429 storm there costs real copies. So the sweep is
**bounded on every axis**:
- **Cadence:** at most one sweep per **6h** normally (4/day). A persistent failure retries at 20m -> **worst case
  ~144 GETs/day**; the normal path is **~24-60 GETs/day** (a few pages x 2 catalogs x 4-ish sweeps). Both are
  negligible next to the existing 60s marks poll to the same host (~thousands of GETs/day).
- **Window:** `minimum_start_date = now-36h` starts the paginated window RECENTLY (never from epoch through months of
  finished games — the probe's pagination-window near-miss), and an ascending-sort HORIZON early-stop (`now+3d`)
  ends it.
- **Backstop:** a hard `_MAX_PAGES = 30` per catalog; hitting it is logged LOUD (no silent truncation).
- **Never every 60s.** The 60s poller reuses the cached index between sweeps.

--------------------------------------------------------------------------------
## 4. DEPLOY SHAPE (pm_web-only; ONE `prediction-markets-web` restart; engine `trading-corp` NEVER touched)

Files (CR-stripped sha16 BEFORE = prod-live/box @ 93b5e908, AFTER = this branch). **1 NEW + 4 edits. No template, no
CSS, no JS, no logo, no migration, no shared-trio, no main.py.**

| file | BEFORE (box == prod-live) | AFTER (branch) |
|---|---|---|
| `web/milestones.py` | ABSENT (new) | `c05082569b9e1fb4` |
| `web/ui_cache.py` | `e116ee8ae07e8112` | `dee87281883f7036` |
| `web/poller.py` | `d9f9f4f518b29869` | `e5016c5de162f1a3` |
| `web/live_view.py` | `583aac901c5984bb` | `51f916960e7c1174` |
| `web/app.py` | `de5f39ed21f301ef` | `b8b512ea6f9bcea9` |

`web/app.py` BEFORE = `de5f39ed21f301ef` == the handoff's documented DEPLOY-8 prod-live app.py (verified). Gate the
deploy on **/pm/arm route decorators = 0** + these BEFORE shas (NOT a hardcoded is_admin count). Also ships to git
(NOT to the box): `tests/prediction_markets/test_milestones.py`, this report.

**Staging channel (multi-file -> scp+tar, per `[[command-paste-rule]]` 5c):** a single ASCII `.ps1` runner writes a
tar of the 5 files, scp's it, drift-gates box==BEFORE for ALL 5, backs up (backup is a gate), copies, re-verifies
AFTER sha16 + `py_compile`, aborts-before-the-rest on any mismatch, and rolls back a partial graft to a consistent
box. Restart via the canonical `az vm run-command ... systemctl restart prediction-markets-web` (NOT the engine).

**Ordering (each its own board authorization; no batching):**
1. `pm_milestone_boxscratch_ro.ps1` — RO: confirm the milestone JSON shape live (1 small page), DRY-RUN the classifier
   join against real held tickers from the box DB, and print the measured sweep cost. (No box writes.)
2. `pm_milestone_precheck_ro.ps1` — RO: assert box == prod-live for all 5 files (any drift = RECONCILE FINDING, STOP);
   engine PID + NRestarts; /pm/arm=0; schema head unchanged.
3. `pm_milestone_apply.ps1` — graft the 5 files (scp+tar, drift-gated, backup-gated, sha-verified).
4. `pm_milestone_restart_az.ps1` — ONE `prediction-markets-web` restart via az (engine untouched).
5. `pm_milestone_postcheck_ro.ps1` — see §5.

--------------------------------------------------------------------------------
## 5. POST-CHECK (after the ONE pm_web restart)

- New pm_web PID (restart recycled) + NRestarts 0; **engine `trading-corp` PID + NRestarts UNCHANGED** before/after.
- All 5 files' CR-stripped sha16 on the box == the AFTER column.
- `GET /live` 200 for every visible account; `/pm/arm` 404; static 200.
- **First-poll milestone log present:** `pm poller: milestone sweep OK -- N event tickers, P pages` with P small
  (no `PAGE-CAP HIT`).
- **Classifier honest:** an atp/wta/ufc/soccer sub holding a STARTED match reads LIVE with an event block (label,
  no scoreboard, no raw ticker); one holding a not-yet-started or milestone-missing match reads UPCOMING. MLB tiles
  unchanged (feed-driven). No tile shows a fabricated LIVE.
- `journalctl` 0 tracebacks; no 429 spike on the shared IP after the sweep.

--------------------------------------------------------------------------------
## 6. STOP CONDITIONS / ROLLBACK

- **box != prod-live for ANY of the 5 files at precheck** -> RECONCILE FINDING, STOP, do not deploy over it.
- **partial graft** (a later file fails its sha gate) -> the runner rolls back the applied files from the backup and
  leaves the box consistent; re-verify all 5 == BEFORE before any retry.
- **milestone sweep 429s / errors after restart** -> the feed degrades to UPCOMING (honest); if the shared IP shows a
  429 spike attributable to the sweep, `az ... systemctl restart prediction-markets-web` off the backup file set to
  revert, and reconsider the cadence (it is a single constant).
- **any wrong LIVE observed** -> pm_web display-only, no order path; revert via the backup restart. (Global engine
  kill, if ever needed for the trading side, remains `pm_cli.py live-disarm --global` — unrelated to this display.)

--------------------------------------------------------------------------------
## 7. PROD-LIVE ADVANCE (a deploy is not complete until prod-live carries it)

After post-check GREEN, fold + three-way prove + ledger commit + FF-push in THIS session:
`git push origin pm-live-milestone-starts-2026-09-12:prod-live` (FF-only; non-FF -> STOP) + tag
`pm-milestone-starts-deploy-2026-09-12`; then re-verify box == prod-live for the 5 files. `main` untouched;
`95e78c4` stays reachable.

--------------------------------------------------------------------------------
## 8. VERIFICATION DONE OFFLINE

- **Baseline (prod-live @ 93b5e908, `.venv-webtest`, `-p no:pytest_ethereum`):** `tests/prediction_markets/` = **117
  tests, 22 failing** (measured, not inherited). The 22 are pre-existing test-vs-code debt (tests written against a
  newer UI shell / admin-identity fixtures not folded into prod-live's git tree by the 2026-09-12 git-only reconcile);
  NONE touch this feed. Set saved in `_baseline_fails.txt`.
- **With this change:** **155 tests, 22 failing** — the **38 new tests all pass**, and the differential of NEW
  failures (green-on-base -> red-with-change) is **EMPTY**. Zero regressions.
- **Standalone invariant:** `milestones.py` imports stdlib only; full app import closure resolves.
- **TWO adversarial skeptic reviews** (correctness/honesty + cost/robustness). Real defects found and FIXED:
  1. (HIGH) MLB feed-authority hole — when the MLB feed was down, a HHMM-less MLB ticker could borrow a milestone and
     read LIVE. FIXED with the explicit `MILESTONE_START_CATEGORIES` allowlist (milestone is used ONLY for the 13
     date-only sports; MLB/cs2/nfl/cfb keep HHMM/feed authority; fed excluded).
  2. (BLOCKER) `fetch_starts` "never raises" was broken by a top-level JSON list (`AttributeError` escaped the narrow
     except). FIXED: per-catalog `except Exception` + non-dict page coerced to empty.
  3. (HIGH) empty-first-boot 6h blackout — an ok-but-empty first sweep set the long REFRESH cadence with no data.
     FIXED: `have_index` now also requires `bool(starts)`, so an empty boot uses the 20m RETRY.
  4. (LOW) `start_instant` tidy (return from the UTC instant; check microsecond on the placeholder).
  - NOT-a-defect (verified against the proven runner): `minimum_start_date` is sent on cursor pages — the working
    `cc/pm_milestone_heldtickers_ro.ps1` does exactly that and paginated correctly.

--------------------------------------------------------------------------------
## 9. PRE-DEPLOY RO GATES — RUN 2026-09-12, ALL GREEN (evidence)

All three are READ-ONLY (no box writes, no restart), run on my own authority under the autonomy addendum. Outputs
saved under `cc/_orient_s4/`.

- **Box-scratch** (`cc/pm_milestone_boxscratch_ro.*`): live milestone JSON shape CONFIRMED (`milestones`+`cursor`
  keys, `start_date` a real UTC instant, `details.status` seen lying `not_started` on a finished 9/11 game -> we
  ignore it). One full bounded sweep = **14 GETs** (Sports 12 + Esports 2, NEITHER capped), **9583** event tickers
  indexed, 36 placeholders skipped -- matches the "~8-15 GETs/day" cost model. Join vs REAL held tickers: **11 of 12**
  milestone-fed held tickers (lal/mex/mls/ufc) resolved to a real start; the 1 miss is a UFC fight a week out
  (beyond the 3-day horizon) -> honest UPCOMING. 0 started-now (so nothing flips LIVE this instant; the joins carry
  correct start data for when the games begin). Box python 3.12.13.
- **Precheck** (`cc/pm_milestone_precheck_ro.*`): **box == prod-live for ALL 5 files** (milestones.py ABSENT-ok; the 4
  edits match their BEFORE shas) -> NO drift, NO reconcile finding. `/pm/arm` routes = 0. Engine `trading-corp`
  MainPID 351422 / NRestarts 0; pm_web 332976 / NRestarts 0. (`schema_head` line = a cosmetic wrong-table-name in the
  RO probe; informational only -- this deploy adds no migration.)
- **Graft verify** (`cc/pm_milestone_graft.* -Mode verify`): local CR-sha16 chain-of-custody all match; 5 files staged;
  drift-gate clean; staged files py_compile on the box venv. NO write.

### Staged runners (ordering)
1. `pm_milestone_boxscratch_ro.ps1` — RO, DONE/green.
2. `pm_milestone_precheck_ro.ps1` — RO, DONE/green.
3. `pm_milestone_graft.ps1 -Mode verify` — RO, DONE/green.
4. **`pm_milestone_graft.ps1 -Mode graft`** — DEPLOY (writes 5 files; backup-gated, drift-gated, sha+compile+import
   verified, rolls back ALL on any failure). RESERVED.
5. **`pm_milestone_restart_az.ps1`** — RESTART pm_web only via az (engine untouched). RESERVED.
6. `pm_milestone_postcheck_ro.ps1` — RO, run after the restart.

### After post-check green (same session): prod-live advance
`git push origin pm-live-milestone-starts-2026-09-12:prod-live` (FF-only; non-FF -> STOP) + tag
`pm-milestone-starts-deploy-2026-09-12`; re-verify box == prod-live for the 5 files. `main` untouched.
