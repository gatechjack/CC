# PM UI — HANDOFF for the next UI code agent

**STATUS: CURRENT — last updated 2026-09-04 (folds in DEPLOY 4 + the BET-SLOT PASS). This is the current handoff;
the filename keeps its original date so existing references resolve.**

Workstream: the Prediction Markets `pm_web` UI rewrite. This doc is the starting point for the next UI pass.
Branch `pm-ui-rewrite-2026-09-02` (pushed to origin), worktree `C:\Users\AA Incorporado\cc-pm-ui-rewrite-wt`.
Deployed code = **`86744ac`** (DEPLOY 4, tag `pm-ui-deploy4-2026-09-04`). Full narrative:
`PM_UI_REWRITE_REPORT_2026-09-02.md` (PLAN / FIX PASS / DEPLOY 1-3 / CARD POLISH / MULTI-CATEGORY FIX / DEPLOY 4 /
BET-SLOT PASS).

--------------------------------------------------------------------------------
## 1. WHAT IS LIVE ON PROD

Deployed code = **`86744ac`** (DEPLOY 4, 2026-09-04). pm_web is a STANDALONE FastAPI+Jinja app: it imports only
`trading_corp.data.*` + the PM package + stdlib; NO engine/main/agents/brokers; holds NO Kalshi credentials; can
never place an order. SINGLE uvicorn worker (loopback :8081, behind Authelia which sets Remote-User). Restarts go
through `az vm run-command` (root); the engine `trading-corp` (ARMED, 8 sub-divisions across two accounts) is NEVER
touched.

Deploy history (all pm_web-only, engine never restarted):
  DEPLOY 1 `9c2eeb3` -> 2 `cafb132` -> 3 `431ec76` -> **4 `86744ac`** (current LIVE; the multi-category fix).

Post-DEPLOY-4 box shipped-file set (box == these, CR-stripped sha16 @ `86744ac`):
    863af1d1522fb364  prediction_markets/subdivision.py
    d3cfbeb9549a36b5  web/live_view.py
    8cace4e71d8140a0  web/marks.py
    d9f9f4f518b29869  web/poller.py
    825861cd1f6c6b0d  web/static/pm_desk.css
    b2cf33e2a7289dc1  web/templates/pm_live_subdivision.html
    9253801d48466156  web/templates/pm_shell.html
Older UI-rewrite files still live at earlier shas (feed_mlb.py 467d5284, arm.py 60f44720, ui_cache.py e116ee8a,
pm.css 204d9051, pm_live.js b4c557fc, htmx.min.js 491955cd, pm_trade_drawer.html 48d579db, etc.). Reconcile every
deploy the same way: `git show <sha>:file | tr -d '\r' | sha256sum` vs `tr -d '\r' < boxfile | sha256sum`.

**Two standing deploy rules (unchanged):**
  * **app.py is GRAFTED, never wholesale-copied.** The box runs M4 + the DEPLOY-4 multi-category hunks. **Current
    box app.py CR-stripped sha16 = `c2e4ddef85b4460b`** (was M4 `8b7d35ca88432603`), `grep -c is_admin` = **10**,
    `grep -c /pm/arm` = **0**. The BRANCH app.py (`86744ac:web/app.py` = `7edce7a5a8164256`) is the M5 version
    (is_admin=12, /pm/arm=1 — the admin arm-control surface, NOT shipped). A wholesale app.py copy would LEAK M5
    early. Graft your app.py hunks onto the box's current file; verify is_admin=10 / /pm/arm=0 + a `c2e4ddef` base.
  * **main.py NEVER ships from this branch** (it carries the engine's per-account driver wiring). UI deploys are
    pm_web-only. `git diff --name-only <deployed> <target> -- .../main.py` must be empty before any deploy.

--------------------------------------------------------------------------------
## 2. ARCHITECTURE (what was built)

* **Feed adapter** `web/feed_mlb.py` — StatsAPI primary, ESPN fallback (ESPN 403s a browser UA; use `curl/8.4.0`).
  Join key = (ET calendar date, doubleheader#, frozenset of canonical team NAMES) — never a raw abbr. Kalshi
  tickers encode ET; feed reports UTC -> converted to ET before keying. Fetch/parse failure -> ABSENT (card
  degrades to "feed unavailable"), never a wrong game. `match_in_slate` is tolerant (exact key, else lone game,
  else DH by game_no).

* **Mark poller** `web/poller.py` + `web/marks.py` + `web/ui_cache.py` — pm_web OWNS the marks (Kalshi's
  market-data endpoint is PUBLIC/unauth; read with stdlib, no creds/broker). One background task writes an
  in-process cache (`ui_cache`, single worker, no DB/schema) every ~60s; renders read the cache, never block on the
  network. Value = contracts x held-leg **BID**. **★ MULTI-CATEGORY (DEPLOY 4):** the poller no longer hardcodes
  the three MLB series — `poll_loop(..., series_provider=)` calls `app._held_series_provider` ->
  `subdivision.traded_series(conn)`, which derives the distinct Kalshi series from the tickers we CURRENTLY HOLD
  across all sub-divisions (both accounts). Fail-safe: any DB blip / empty result -> the MLB default (so a cold
  start still primes the slate). `marks.Mark` carries a `title` (the Kalshi market title) for the non-MLB rows.

* **live_view assembly** `web/live_view.py` — pure `build_live_context(orders, open_positions,
  open_positions_by_whale, slate, marks_result, now_ts, category=None)` (no DB/network; unit-testable). **★
  MULTI-CATEGORY (DEPLOY 4) — totals NEVER depend on the sport parser:**
    - **MLB** (`category=='mlb'` or detected from tickers) renders GAME CARDS with the EXACT original card-based
      summary (byte-identical; `realized_today`/`settled_today` keyed on the card's game date). Locked by
      `test_mlb_summary_strip_values_locked` + `test_mlb_context_byte_identical_with_and_without_category`.
    - **Non-MLB** (atp/ufc/wta/…) renders a POSITIONS TABLE (`_positions_view`) with a JOURNAL summary
      (`_journal_summary`): at-cost / count / value+coverage / realized-today all from `live_positions` /
      `value_positions`, never `game_key_from_ticker`. The "games held" cell reads "No game feed for <CAT> · N open
      positions". No diamond / inning / count / "game over" / baseball legend on a feed-less category.
  This fixed the DEPLOY-3 defect where a non-MLB sub-division showed 0 games / $0.00 while its drawer held a real
  trade (root cause: `game_key_from_ticker` returns None for non-MLB, so the card-derived strip went 0).

* **Bet slots + whale attribution** `_build_slot` / `_whale_tag` — each MLB card has three fixed slots (ML/TOT/SPR);
  **★ BET-SLOT PASS (2026-09-04, built not yet deployed):** slots widened to fill the home-plate area (below the
  diamond), and each HELD slot shows the whale it was copied from (first label + `+N` extras, journal-sourced from
  `open_positions_by_whale`, right-truncated by CSS so the slot never changes shape). Every slot reserves the whale
  row so held/unheld slots keep identical height (card height constant). Same tag in the non-MLB positions table's
  whale column. Settled/unheld slots show no whale; the full untruncated list stays in the trade drawer.

* **Account pages** — `subdivision.account_pnl` iterates every active category; `_account_open_value` sums
  `live_positions` across all sub-divisions; rows list all four categories. Category-agnostic already; DEPLOY 4
  gave them non-MLB marks via the held-series poller.

* **Arm badge** — read-only from PERSISTED `agent_state` (legacy DB `data/trading_corp.db`, agent `pm_live`, keys
  `arm:global` / `arm:kalshi_<acct>:<cat>`). pm_web only DISPLAYS; the CLI is authoritative. Arming is restart-free
  (read each cycle) — a pm_web restart never changes arm state.

* **Shell + cache-busting** `web/templates/pm_shell.html` — loads `pm.css` then `pm_desk.css`. Static URLs carry
  `?v=<CR-stripped-content-sha8>`; `test_asset_cache_bust_hashes_match_files` fails CI if a CSS/JS change forgets to
  bump the shell hash. Current pm_desk.css shell tag = `?v=a40ab798` (branch; box serves `825861cd` until this pass
  deploys).

--------------------------------------------------------------------------------
## 3. HONESTY RULES (as implemented — preserve these)

No-mark -> `no mark` / `unavailable`, never $0 / never cost-as-value. Coverage label `N of M priced` UNCONDITIONAL
under every current-value figure (every category). Feed age bands (a final never goes stale). Arm states are
FOUR: ARMED / DISARMED / STATE UNAVAILABLE (indeterminate mode=ro read, never shown as disarm) / NEVER ARMED.
Opposed close = "— not booked" (engine books no realized there). Game states: pre-game "not started" (blank, not
0-0); postponed/suspended/delayed are their own amber states; final "game over"; inning break -> empty count pips +
cleared runners. Directional slots: TOTAL `+8.5/-8.5`, SPREAD `-1.5 TEAM/+1.5 OTHER`; settled slots keep the held
direction, never guessed. Retention 24h after game end OR last settlement. **Whale attribution now on the card
slots too** (Jack reversed the drawer-only ruling 2026-09-04) — journal-sourced, never inferred; full list in the
drawer.

--------------------------------------------------------------------------------
## 4. OPERATIONAL LESSONS

* **CR-strip BOTH sides before hashing** any box-vs-git comparison (box is LF; Windows checkout is CRLF). Unstripped
  hashes never match and every file looks drifted.
* **pm_web restarts via `az vm run-command`** (root): `az vm run-command invoke -g rg-shared-prod -n tc-prod-vm
  --command-id RunShellScript --scripts 'systemctl restart prediction-markets-web'`. ssh+sudo has no TTY and
  fails-SAFE. NEVER restart/reload `trading-corp` (the engine).
* **Browser-cached CSS masquerades as a rendering bug** — always bump the `?v=` shell hash on a CSS/JS change and
  verify the served file's sha over HTTP + the page link's new `?v=`.
* **The box is NOT a git repo** — deploys are base64-embedded graft scripts streamed over ssh (`cc/gen_deploy*.py`,
  `cc/pm_deploy*_apply.sh`); write files as azureuser, restart as root via az. DEPLOY 4 pattern:
  `cc/pm_deploy4_precheck_ro.sh`, `cc/gen_deploy4.py` -> `cc/pm_deploy4_apply.sh` (temp -> CR-strip -> sha16 gate ->
  mv), `cc/deploy4_box_app.py` (the graft), `cc/pm_deploy4_restart_az.ps1`, `cc/pm_deploy4_postcheck_ro.sh`.
* **Test baseline: 16 pre-existing env-gap failures** in `tests/prediction_markets/` (offline, `.venv-webtest`,
  `-p no:pytest_ethereum`). All engine-dep, none from UI work; a green UI change leaves them at exactly 16:
  `test_kill_switch_r7d` x4, `test_live_driver_r7c` x7, `test_shard_gate_r2` x4 (`ModuleNotFoundError: pykalshi`) +
  `test_search_r1::test_schema_head_is_15` (stale assertion; schema head is 19). **Measure the baseline yourself by
  stashing your changes and running the committed base** — this number has drifted across sessions (earlier docs
  said 18/19; the authoritative current value on this venv is 16). The pm_web venv is
  `C:\Users\AA Incorporado\CC\.venv-webtest` (fastapi, jinja2, tzdata, playwright); use it for renders.
* **/farm/cs 404 is PRE-EXISTING**, not this branch — 'cs' appears as a non-tile tag on `/farm` but has no
  live-copyable category page. No farm code has shipped from this branch. Do not "fix" it as a regression.

--------------------------------------------------------------------------------
## 5. DELIBERATELY NOT BUILT / DESIGN DECISIONS OPEN

* **"Settled during a live game" note** — needs a settlement-ts <-> game-state-history join that does not exist.
* **Opposed-close realized P&L** — engine work, not UI; the UI shows "not booked".
* **Sport-specific non-MLB cards (court / octagon)** — DEFERRED as a DESIGN DECISION. Non-MLB currently renders an
  honest positions TABLE; whether ATP/UFC/WTA get bespoke scoreboards (and where the data would come from) is Jack's
  call, not assumed. Do not build sport cards without a ruling.
* **Farm League redesign** — the `/farm` + `/farm/{category}` pages are a DESIGN DECISION pending, not part of the
  live-copy UI rewrite. Leave them unless Jack scopes a redesign.

--------------------------------------------------------------------------------
## 6. BACKLOG — gated behind engine work

**Roster / DEMOTE panel** (the next UI pass): replace the flat "Copies these whales" line with an expandable
per-whale panel — per-whale LIVE real-money copy record (dollars first, thin-sample caveats), ON-ROSTER vs
FORMERLY-LIVE with dates, placed/booked/unbooked counts, drill-through to the drawer rows, and a per-sub-division
**DEMOTE** button (confirmation states OPEN copies RUN TO SETTLEMENT — demote stops new copies, does not flatten).
REJECTED by Jack: a paper-vs-live side-by-side per whale.

**SEQUENCING RULE (Jack, hard gate):** the DEMOTE button is NOT buildable until the ENGINE can read the live roster
from a per-sub-division `subdivision.yaml` applied WITHOUT a restart (today the engine loads the roster at BOOT).
The UI button must not exist until that hot-reload is PROVEN by an observed engine cycle. Engine/reconciliation work
precedes the UI pass — do not build the button first.

--------------------------------------------------------------------------------
## 7. NOT YET OBSERVED IN PROD

* **Non-MLB mark path with a LIVE non-MLB position.** DEPLOY 4's non-MLB positions view is proven on prod for the
  header/view change (all 6 non-MLB pages) and for a SETTLED position (the Halys loss on `/live/kalshi_jack/atp?tab=
  complete`), but at deploy time no non-MLB sub-division held an OPEN position, so the live current-value/coverage
  path for a non-MLB open position (contracts x bid + "N of M priced" from a KXATPMATCH/KXUFCFIGHT/KXWTAMATCH mark)
  has not been eyeballed on prod. Confirm when a whale next opens a non-MLB position: `traded_series` should include
  that series and the row should price (or read "no mark", never $0). The full value path IS proven off-prod
  (render harness `cc/pm_multicat_render.py` + `cc/pm_betslot_render.py`).
* **Inning-break hollow pips** (DEPLOY 3): proven by unit test + local render only; no game was at an inning break
  during a verification window. Confirm on the next live-baseball check (a MID/END card shows empty pips + cleared
  runners).
* **BET-SLOT PASS (this branch) not deployed** — the widened slots + copied-from whale tags are built and rendered
  (`cc/renders/betslot_v3_*`) but await a Board deploy decision; nothing about them is on prod yet.
