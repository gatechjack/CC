# PM UI — HANDOFF for the next UI code agent (2026-09-03)

Workstream: the Prediction Markets `pm_web` UI rewrite. This doc is the starting point for the next UI pass.
Branch `pm-ui-rewrite-2026-09-02` (pushed to origin, tip `3cb1bd9`), worktree `C:\Users\AA Incorporado\cc-pm-ui-rewrite-wt`.
Deployed code tag `pm-ui-deploy3-2026-09-03` -> `431ec76`. Full deploy narrative: `PM_UI_REWRITE_REPORT_2026-09-02.md`
(PLAN / FIX PASS / DEPLOY / POST-DEPLOY PASS / PRE-DEPLOY ADDITIONS / DEPLOY 2 / CARD POLISH / DEPLOY 3).

--------------------------------------------------------------------------------
## 1. WHAT IS LIVE ON PROD

Deployed code = **`431ec76`** (DEPLOY 3, 2026-09-02/03). pm_web is a STANDALONE FastAPI+Jinja app: it imports only
`trading_corp.data.*` + the PM package + stdlib; NO engine/main/agents/brokers; holds NO Kalshi credentials; can
never place an order. It runs as a SINGLE uvicorn worker (loopback :8081, behind Authelia which sets Remote-User).

Deploy history (all pm_web-only, engine `trading-corp` PID 163519 never touched):
  DEPLOY 1 = `9c2eeb3` (the rewrite + entity fix) -> DEPLOY 2 = `cafb132` -> DEPLOY 3 = `431ec76` (current LIVE).

The box matches branch `431ec76` (CR-stripped) for every shipped pm_web file EXCEPT two, which are the standing
deploy rules:

  * **app.py is GRAFTED, never wholesale-copied.** The box runs the M4 version:
    box app.py CR-stripped sha16 = **`8b7d35ca88432603`**, `grep -c is_admin` = **10**, `grep -c /pm/arm` = **0**.
    The BRANCH app.py (`431ec76:web/app.py` = `2a1c341d2e855ee2`) is the M5 version (is_admin=12, /pm/arm=1) — the
    admin arm-control surface that has NOT shipped to prod yet. A wholesale app.py copy would LEAK M5 early. Until
    M5 ships, graft your app.py hunks onto the box's M4 file; verify is_admin=10 / /pm/arm=0 post-graft.
  * **main.py NEVER ships from this branch.** main.py on the box carries the engine's per-account driver wiring
    (`driver_roster`, Karen). This branch's main.py must never be deployed; a wholesale main.py copy would delete
    that wiring. UI deploys are pm_web-only.

DEPLOY 3 shipped-file set (box == these, CR-stripped sha16 @ `431ec76`):
    467d528460421a31  web/feed_mlb.py
    2c7c8875cd80e768  web/live_view.py
    1b3b8ccc6dff50cc  web/static/pm_desk.css
    90e7357b62e6d87c  web/templates/pm_live_subdivision.html
    d5a29a20d5407781  web/templates/pm_shell.html
Other UI-rewrite files also live on the box at their `431ec76` shas (arm.py 60f44720, marks.py 46ca99a8,
ui_cache.py e116ee8a, poller.py 44a6b51d, pm_arm_badge.html 6bb01984, pm_trade_drawer.html 48d579db,
pm_accounts.html 014c03ba, pm_account.html a5f39df0, pm.css 204d9051, pm_live.js b4c557fc, htmx.min.js 491955cd).
Reconcile the box the same way every deploy did: `git show <sha>:file | tr -d '\r' | sha256sum`.

--------------------------------------------------------------------------------
## 2. ARCHITECTURE (what was built)

* **Feed adapter** `web/feed_mlb.py` — StatsAPI primary, ESPN fallback (ESPN 403s a browser UA; use `curl/8.4.0`).
  Pure parse funcs separated from a thin urllib fetch. Join key = (ET calendar date, doubleheader#, frozenset of
  canonical team NAMES) — never a raw abbr (Kalshi/StatsAPI/ESPN codes differ; `MLB_TEAMS` canonicalizes). Kalshi
  tickers encode ET; feed reports UTC -> converted to ET before keying (`zoneinfo('America/New_York')`). Any
  fetch/parse failure or missing game -> ABSENT (card degrades to "feed unavailable"), never a wrong game.
  `match_in_slate` is tolerant: exact key, else the single non-DH game for (date, team-set), else DH by game_no.

* **Mark poller** `web/poller.py` + `web/marks.py` + `web/ui_cache.py` — pm_web OWNS the marks (Scope E resolved:
  Kalshi's market-data endpoint is PUBLIC/unauth). `marks.fetch_marks` reads it with stdlib (no creds, no broker
  import). One background task (started with the app) writes an IN-PROCESS cache (`ui_cache`, single worker, no DB
  table/schema) every ~60s; renders read the cache and never block on the network. Values = contracts x held-leg
  **BID** (the conservative, marketable side), labelled `bid`.

* **live_view assembly** `web/live_view.py` — pure `build_live_context(orders, open_positions,
  open_positions_by_whale, slate, marks_result, now_ts)` (no DB, no network; unit-testable). Joins the DB order
  JOURNAL (`subdivision.live_orders`/`live_positions`) + cached slate + cached marks into game cards: a card per
  held game with box score + three fixed bet slots (ML/TOT/SPR) + a trade drawer. Ticker parsing
  (`game_key_from_ticker`, `_short_label`, `_settled_leg`) reuses `data/mlb_poly_kalshi_match` + `market_describe`.

* **Arm badge** — read-only, from PERSISTED `agent_state` rows (legacy DB `data/trading_corp.db`, agent `pm_live`,
  keys `arm:global` / `arm:kalshi_<acct>:<cat>`) via `arm.read_display`. pm_web only DISPLAYS; the CLI is the
  authoritative arm/disarm path. The badge is a plain `<span>`, never a control.

* **Shell + cache-busting** `web/templates/pm_shell.html` — loads `pm.css` then `pm_desk.css` (desk wins ties;
  bare h1/h2/a scoped under `.desk`). Static asset URLs carry `?v=<content-sha8>` so a changed CSS/JS re-fetches;
  `test_asset_cache_bust_hashes_match_files` fails CI if a change forgets to bump the shell hash.

--------------------------------------------------------------------------------
## 3. HONESTY RULES (as implemented — preserve these)

* **No-mark state** — a position with no resting bid renders `no mark` / `unavailable`, NEVER $0 and never the
  cost basis standing in for value.
* **Coverage label is UNCONDITIONAL** — `N of M priced` appears under every current-value figure (accounts
  overview, account page, division strip): neutral at full coverage (N==M), amber `partial: N of M` when partial,
  `no mark (0 of N)` when none priced, `$0.00 (0 positions)` when M==0. Never a silent full/partial.
* **Feed age bands** — every feed value carries its own age chip; a final game never goes stale; a live value
  past its band shows `stale`. A stale value is not a current value.
* **Arm states are three, not two** — ARMED / DISARMED (a row exists and says off) / STATE UNAVAILABLE (an
  indeterminate mode=ro read, e.g. near a restart — NEVER shown as a disarm) / NEVER ARMED (no row ever written,
  no age chip). `read_display` distinguishes them; the gate/`read_status` fail-safe (collapse-to-off) is unchanged.
* **Opposed close = "not booked"** — the engine does not compute realized P&L on an opposed close, so the drawer
  shows "— not booked", never a guessed number.
* **Game states** — pre-game (preview) shows "not started" with NO score digits (a 0-0 pre-game is blank, not
  0-0); postponed/suspended/delayed are their own amber-labelled states, not pre-game and not over; final shows
  "game over". At an inning break (StatsAPI inningState Middle/End) the count pips render empty and runners clear
  ("MID"/"END" label kept). Card border by GAME state (LIVE blue / NOT STARTED subtle / COMPLETE grey /
  feed-unavailable none); the MIXED green TOP accent is POSITION state and coexists with the border.
* **Directional bet slots** — TOTAL `+8.5`/`-8.5` (over/under from the held leg), SPREAD `-1.5 TEAM`/`+1.5 OTHER`
  (sign + team). Settled slots carry the SAME direction (held side from the filled entry leg); if the leg is
  genuinely unrecorded/ambiguous, the line shows without a sign — never guessed.
* **Retention** — a completed card drops 24h after the game ends OR its last settlement (whichever later); the
  anchor is the max settled_at (feed carries no reliable end ts).
* **Whale attribution** — each held/ledger row and each trade-drawer row names the copied whale (user_name or the
  wallet); the per-whale LIVE-copy record sums to the account's live figure.

--------------------------------------------------------------------------------
## 4. OPERATIONAL LESSONS

* **CR-strip BOTH sides before hashing** any box-vs-git comparison. The box is LF; Windows checkouts are CRLF
  under autocrlf. `git show <c>:file | tr -d '\r' | sha256sum` vs `tr -d '\r' < boxfile | sha256sum`. Unstripped
  hashes never match and every file looks drifted (a recurring measurement artifact).
* **pm_web restarts go through `az vm run-command`** (runs as root):
  `az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts
  'systemctl restart prediction-markets-web'`. ssh+sudo has NO TTY on this box and fails-SAFE (changes nothing).
  NEVER restart/reload `trading-corp` (the engine) — it is ARMED and trading two accounts with real money.
* **Browser-cached CSS masquerades as a rendering bug.** DEPLOY 2 shipped the correct `.toggle .tgl` CSS but prod
  showed run-together text — the browser held a stale `pm_desk.css` (the static server sends no cache-busting).
  Fixed by `?v=` content hashes on the shell assets. LESSON: if prod looks unchanged after a CSS/JS deploy,
  suspect CACHE first (verify the served file's sha over HTTP, and that the page link carries the new `?v=`).
* **The box is NOT a git repo** — deploys are base64-embedded graft scripts streamed over ssh (see `cc/gen_deploy*.py`,
  `cc/pm_deploy*_apply.sh`). Files written as azureuser (under its home); restart as root via az.
* **Test baseline: 19 pre-existing env-gap/schema failures** in `tests/prediction_markets/` (offline). They are
  NOT caused by UI work; a green UI change leaves them at exactly 19:
    - 17x `ModuleNotFoundError: No module named 'pykalshi'` — the engine live-driver dep, absent in the pm_web
      test venv (test_idempotency_r7h x2, test_kill_switch_r7d x4, test_live_driver_r7c x7, test_shard_gate_r2 x4).
    - 1x `test_search_r1::test_schema_head_is_15` — stale assertion; schema head is 17 (migrations 016/017 landed).
    - 1x `test_web_healthz::test_pm_web_imports_no_engine` — spawns `python -c "import trading_corp..."` in a bare
      subprocess with no package on sys.path locally; PASSES on the box (import-closure guard is real there).
  Run with `-p no:pytest_ethereum`. The pm_web venv is `C:\Users\AA Incorporado\CC\.venv-webtest` (has fastapi,
  jinja2, tzdata, playwright); use it for renders.

--------------------------------------------------------------------------------
## 5. DELIBERATELY NOT BUILT

* **"Settled during a live game" note** — needs a settlement-ts <-> game-state-history join that does not exist;
  settled positions render note=None. Do NOT fabricate it.
* **Opposed-close realized P&L** — the engine does not book it; the UI shows "not booked". Computing it is ENGINE
  work, not a UI change.

--------------------------------------------------------------------------------
## 6. BACKLOG — the next UI pass ("Copies these whales" + DEMOTE)

Target: replace the flat "Copies these whales" line on the sub-division page with an EXPANDABLE per-whale panel:
  * per-whale LIVE real-money copy record (dollars FIRST, with per-whale sample-size caveats — a thin record tells
    you the plumbing works, not that the whale wins);
  * grouped ON-ROSTER vs FORMERLY-LIVE, each with dates;
  * placed / booked / unbooked counts per whale;
  * drill-through to the matching rows in the trade drawer;
  * a per-sub-division **DEMOTE** button with a confirmation that OPEN copied positions RUN TO SETTLEMENT (demote
    stops new copies; it does not flatten).
  REJECTED by Jack: a paper-and-live side-by-side per whale.

  **SEQUENCING RULE (Jack, hard gate):** the DEMOTE button is NOT buildable until the engine can read the live
  roster from a per-sub-division `subdivision.yaml` applied WITHOUT a restart (today the engine loads the roster
  from `strategy.yaml` at BOOT). The UI button must not exist until that hot-reload is PROVEN by an observed
  engine cycle. This is engine/reconciliation work that precedes the UI pass — do not build the button first.

--------------------------------------------------------------------------------
## 7. NOT YET OBSERVED IN PROD

* Inning-break hollow pips (item 3, DEPLOY 3): proven by unit test + local render only. No game was at an inning
  break during the DEPLOY 3 verification window, so the live behavior is not yet eyeballed on prod. Confirm on the
  next live-baseball check that a MID/END card shows empty pips + cleared runners.
