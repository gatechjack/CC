PM TILES PHASE-1 INVENTORY (READ-ONLY) -- 2026-09-07
====================================================

Scope: establish, for each field of the proposed "Live Sub-divisions" tile page, whether
the data is already reachable by pm_web and exactly where. INVENTORY ONLY -- no design, no
template change, no engine/DB/deploy/restart. Read-only throughout (code reads + mode=ro DB
reads + loopback HTTP GETs). One report; committed, NOT pushed (push reserved for Jack).

Worktree: cc-pm-tiles-phase1-wt, branch pm-tiles-phase1-inventory-2026-09-07 (based at
aad4dea, the pm-ui-rewrite-2026-09-02 tip). Code citations are against aad4dea unless noted
"(liveness @0eb93a1)" for driver-liveness code, which is NOT on aad4dea (see DRIFT).

Box observed 2026-09-07T16:18-16:22Z, host tc-prod-vm: engine trading-corp MainPID 232440
NRestarts=0 active; pm_web MainPID 232084 NRestarts=0 active. PM DB
/home/azureuser/trading_corp/data/prediction_markets.db; legacy (arm) DB .../data/trading_corp.db.
PM schema head = 20.

Runners (in cc/, read-only, streamed via the sanctioned .ps1; logged):
  pm_tiles_inv_data_ro.{ps1,sh}   -- mode=ro DB facts + box readers (items 1,2,4,5,7,9-data)
  pm_tiles_inv_drift_ro.{ps1,sh}  -- box file sha16 drift + loopback coverage (items 3,drift)
Local: full tests/prediction_markets/ suite in .venv-webtest (test baseline).


1. SUMMARY -- the nine items
============================

  #  Field (per tile)                       Verdict            Where / note
  -- -------------------------------------- ------------------ ----------------------------------
  1  Lifetime realized P&L + settled count  AVAILABLE          subdivision.subdivision_pnl (subdivision.py:403)
  1b Settlement rows carry ts every category AVAILABLE (proven) settled_ts; atp/mlb/wta all 100%
  2  Last-24h realized P&L                  NEEDS PLUMBING     trivial: same table + a ts filter (query below)
  3  Open count / at-cost / value+coverage  AVAILABLE          live_positions + value_positions (live_view.py:656)
  4  Arm state per sub-division             AVAILABLE          arm.read_display (arm.py:198); 20 tiles have no row
  5  Liveness (armed-but-not-evaluating)    AVAILABLE          heartbeat.read_liveness (@0eb93a1); table on box, live
  6  Attached whales per sub-division       AVAILABLE          subdivision.attached_whales (subdivision.py:115)
  7  Tile page route/template/context       AVAILABLE (sparse) GET /live -> pm_live_list.html (app.py:834)
  8  Migration needed?                      YES if 24h/tiles   MUST be exactly 021 (contiguity invariant)
     want a rollup table; NONE if computed live per request
  9  app.py change for Phase 2?             YES -> GRAFT       graft onto box-current 069d7a25, NOT c2e4ddef/branch

"AVAILABLE" = pm_web already has a reader/route that returns it (or can call one) with no new
plumbing. "NEEDS PLUMBING" = a small new query/context field, no schema change required unless
a persisted rollup is wanted. Nothing is "unknown".


2. ITEM 1 -- lifetime realized P&L + settled count per (account, category)
=========================================================================
READER: subdivision.subdivision_pnl(conn, account_id, category)  [subdivision.py:403-426]
Category-agnostic; no MLB special-case. Verbatim SQL:

    SELECT COALESCE(SUM(realized_pnl), 0) rp,
           SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END) wins,
           SUM(CASE WHEN won = 0 THEN 1 ELSE 0 END) losses, COUNT(*) n
    FROM pm_subdivision_order
    WHERE account_id = ? AND category = ? AND dry_run = 0 AND is_exit = 1
          AND outcome_status = 'filled'

  rp = lifetime realized P&L (no time filter). n = lifetime terminal-close count. Rolled up
  by account_pnl (subdivision.py:429) and accounts_overview (subdivision.py:482); already
  wired to /  and /account/{id} (app.py _load_accounts_overview:549, _load_account:575).

TABLE: pm_subdivision_order (migration 010; realized_pnl/won/settled_ts added migration 015).
BOX EVIDENCE (mode=ro, live):
    kalshi_jack  atp  realized=$-5.32  closed=14  W/L=3/9
    kalshi_jack  mlb  realized=$10.34  closed=76  W/L=38/32
    kalshi_jack  wta  realized=$4.66   closed=2   W/L=2/0
    kalshi_karen atp  realized=$-0.61  closed=9   W/L=4/5
    kalshi_karen mlb  realized=$8.83   closed=31  W/L=18/12
    kalshi_karen wta  realized=$4.66   closed=2   W/L=2/0
  (ufc + the 20 disarmed tiles have 0 terminal closes.) NOTE: `closed` counts ALL is_exit=1
  filled rows; W+L < closed because non-settlement closes (whale-exit / opposed) have won=NULL
  (jack/mlb 76 closed, 70 W+L -> 6 non-settlement closes). This matters for the "settled count"
  definition -- see section 15.

1b. SETTLEMENT ROWS CARRY A TIMESTAMP FOR EVERY CATEGORY (not just MLB) -- CONFIRMED.
settled_ts is written by settlement.book_settlements (settlement.py:181-187),
`settled_ts = rec.settled_ts if not None else int(now_ts)`, called per (account, category) with
NO MLB branch. Empirical proof (is_exit=1 filled dry_run=0, grouped by category):
    cat=atp  closed=23  settlement_rows=21  settle_with_settled_ts=21  with_response_ts=23
    cat=mlb  closed=107 settlement_rows=100 settle_with_settled_ts=100 with_response_ts=107
    cat=wta  closed=4   settlement_rows=4   settle_with_settled_ts=4   with_response_ts=4
  Every SETTLEMENT row in every category that has one carries settled_ts (100%). The handful of
  non-settlement closes (atp 23-21=2) have no settled_ts but do have response_ts. So a
  timestamp is always available: COALESCE(settled_ts, response_ts).


3. ITEM 2 -- last-24h realized P&L
==================================
NOT PRESENT as a query today. The only "recent" concept is realized-TODAY (ET calendar day),
computed in Python: live_view._realized_today (live_view.py:477) filters
close_source in ('settlement','settlement_void') and _et_date(settled_ts or response_ts)==today;
the MLB card path keys realized_today on the card game date (live_view.py:625). Neither is a
rolling 24h window.

DERIVABLE FROM ITEM 1's TABLE -- yes, trivially, no schema change. The 24h query (proven live):

    SELECT account_id, category, COALESCE(SUM(realized_pnl),0) rp, COUNT(*) n
    FROM pm_subdivision_order
    WHERE dry_run=0 AND is_exit=1 AND outcome_status='filled'
          AND COALESCE(settled_ts, response_ts) >= ?        -- now_ts - 86400
    GROUP BY account_id, category

  BOX EVIDENCE (now-86400):
    kalshi_jack  atp  realized_24h=$-7.08  closed_24h=4
    kalshi_jack  mlb  realized_24h=$17.73  closed_24h=9
    kalshi_karen atp  realized_24h=$-5.50  closed_24h=3
    kalshi_karen mlb  realized_24h=$15.82  closed_24h=12
  Anchor = COALESCE(settled_ts, response_ts): settled_ts for settlements, response_ts for
  whale-exit/opposed closes that carry no settled_ts. This is a NEW reader (~10 lines in
  subdivision.py); no migration needed if computed per request.


4. ITEM 3 -- open count / at-cost / current value + coverage
============================================================
value_positions(positions, marks) [live_view.py:656-671] returns
{value, n_priced, n_total, complete, known}: value = sum(contracts x held-leg BID) over PRICED
positions only (None if none priced -- never $0 for unpriced); coverage label "N of M priced"
= n_priced/n_total. It is category-agnostic (marks fetched for every held series via
subdivision.traded_series -> marks.series_from_tickers, not a hardcoded MLB list).
  - open count + at-cost come from subdivision.live_positions (subdivision.py:225): net signed
    contracts per ticker (dropping flats), fields ticker/market_type/held_leg/contracts/
    cost_basis_usd/avg_price/fees_usd. at-cost = sum(cost_basis_usd).
  - current VALUE marks come from the pm_web mark cache (poller.py -> marks.fetch_marks public
    Kalshi endpoint -> ui_cache; live_view.build_from_cache).

BOX EVIDENCE -- journal half (live_positions, mode=ro):
    kalshi_jack  atp  open=2  at_cost=$6.20
    kalshi_jack  mlb  open=2  at_cost=$5.05
    kalshi_karen atp  open=2  at_cost=$6.20
    kalshi_karen mlb  open=5  at_cost=$14.75   (only these 4 tiles hold open positions now)
BOX EVIDENCE -- coverage half (running pm_web page label, loopback GET, the actual cache):
    /live/kalshi_jack/atp   -> 2 of 2 priced
    /live/kalshi_jack/mlb   -> 2 of 2 priced
    /live/kalshi_karen/atp  -> 2 of 2 priced
    /live/kalshi_karen/mlb  -> 5 of 5 priced
  NOTE: this is the FIRST live observation of the non-MLB (ATP) current-value/coverage path on
  prod -- the DEPLOY-5 handoff listed it "not yet observed" (no non-MLB open position existed
  then). It now prices correctly (2 of 2). value_positions serves per-sub open/value/coverage.


5. ITEM 4 -- arm state per sub-division
=======================================
arm.read_display(account_id, category) [arm.py:198-237] reads the LEGACY DB
(resolve_legacy_db_path = $PM_LEGACY_DB_PATH else data/trading_corp.db; NOT the PM DB):
    SELECT value_json FROM agent_state WHERE agent='pm_live' AND key=?
keys arm:global (GLOBAL_KEY) and arm:{account_id}:{category} (sub_key). Returns four
DISTINCT states -- armed / disarmed / absent (row read cleanly, none present -> never-armed) /
unavailable (indeterminate mode=ro read; never collapsed to disarmed). effective_state is
'armed' only if BOTH global and sub read OK+armed.

COVERAGE: read_display is called PER (account, category) and returns a well-defined state even
when there is no row (absent -> effective disarmed), so it "covers" all 28 tiles without error.
BOX EVIDENCE: arm:global armed=True latched=False. Only 9 agent_state arm rows exist
(global + 8 subs: atp/mlb/ufc/wta x {jack,karen}, all armed=True latched=False). Therefore of
the 28 tiles, 8 are armed and 20 have NO arm row -> render NEVER ARMED (absent). The 20:
  jack:  {bra,bun,cfb,epl,lal,mex,mls,nfl,ucl,wnba}
  karen: {bra,bun,cfb,epl,lal,mex,mls,nfl,ucl,wnba}
(The 15 attachment-less HIDDEN subs -- see section 8/13 -- also have no arm row, but they are
not tiles.) read_display faithful output per tile matches: 8 armed/effective-armed, 20
absent/effective-disarmed.


6. ITEM 5 -- LIVENESS (heartbeat)
=================================
The driver-liveness subsystem landed 2026-09-06 on branch pm-driver-liveness (@0eb93a1) and is
DEPLOYED on the box (NOT on aad4dea). Files: db.py migration 020, heartbeat.py, the live_driver
writer, web/templates/partials/pm_liveness.html + app.py panel loaders.

TABLE/FILE: migration 020 (db.py:891-910) creates TWO tables. VERBATIM DDL:

    CREATE TABLE IF NOT EXISTS pm_driver_task_heartbeat (
     account_id    TEXT    NOT NULL PRIMARY KEY,
     last_cycle_ts INTEGER NOT NULL,            -- bumped at the TOP of the while-loop = task alive
     updated_ts    INTEGER
    )
    CREATE TABLE IF NOT EXISTS pm_driver_heartbeat (
     account_id      TEXT    NOT NULL,
     category        TEXT    NOT NULL,
     reached_ts      INTEGER,                   -- loop reached this category
     evaluated_ts    INTEGER,                   -- category fully evaluated (arm-gated cycle returned)
     n_signals       INTEGER,                   -- IDLE (0) vs PLACING (placed>0)
     placed          INTEGER,
     errors          INTEGER,
     ceiling_latched INTEGER,                   -- alive-but-intentionally-not-placing = NOT a fault
     state           TEXT,                      -- 'evaluated'|'skipped_no_builder'|'skipped_no_ctx'
     updated_ts      INTEGER,
     PRIMARY KEY (account_id, category)
    )
  Box confirms both tables present with exactly these columns.

WRITER / CADENCE: live_driver.scheduled_pm_live_loop, poll_sec default 7.0 (line 604 sig;
await _sleep at line 890). Three grains, all via heartbeat.safe_beat (swallows errors -> a
liveness write can never kill a trading cycle):
  - task_alive  (per account)          live_driver.py:712  -- TOP of while-loop, OUTSIDE the
                                        cycle try; unconditional every cycle. -> pm_driver_task_heartbeat.
  - reached     (per acct, category)   live_driver.py:750  -- first thing in the category loop.
  - evaluated   (per acct, category)   live_driver.py:872  -- after run_live_arm_gated_cycle;
                                        writes n_signals/placed/errors/ceiling_latched/state='evaluated'.
  So a healthy sub updates every ~7-30s.

"ARMED BUT NOT EVALUATING" -- the exact definition (heartbeat.read_liveness, heartbeat.py:207-264;
do-not-paraphrase predicate). The panel classifies over the EXPECTED SET = active sub-divisions
with >=1 active attachment on an active account (heartbeat.py:171-176, mirrors
driver_roster.active_driver_subdivisions) -- it does NOT key on arm state. Per expected sub:
    task_fresh = task_ts present AND liveness_band(now-task_ts) == 'fresh'   (fresh = |age|<90s)
    ev_fresh   = evaluated_ts present AND liveness_band(now-evaluated_ts)=='fresh'
  branches (heartbeat.py:225-260):
    c is None and task_ts is None      -> PENDING (attach < 20min) | BOOTING (acct cycled <10min) | NEVER
    not task_fresh and boot_recent     -> BOOTING   (restart in progress; acct newest beat <10min)
    not task_fresh                     -> STALE     (driver dead/hung)                [RED ALARM]
    ev_fresh                           -> RUNNING (placed/signals) | IDLE (0 signals / latched)
    else (task_fresh AND not ev_fresh) -> CATEGORY_STARVED "task alive but this category has not
                                          evaluated recently (no catalog?)"           [amber, NOT alarm]
  any_alarm() (heartbeat.py:267) = any sub in STALE or NEVER only. So:
    * The RED "driver NOT running" alarm = STALE (task beat stale past boot grace) or NEVER (no
      heartbeat ever, past attach/boot grace).
    * The closest thing to "alive but a category isn't evaluating" = CATEGORY_STARVED (amber,
      informational, task_fresh & !ev_fresh).
  KEY SUBTLETY: arm state is ORTHOGONAL. A DISARMED-but-attached sub still gets a driver task
  (roster is attachment-gated, not arm-gated) so it cycles and reads RUNNING/IDLE, not an alarm;
  an ARMED sub whose task died reads STALE regardless of arm. Thresholds: FRESH_MAX_SEC=90,
  STALE_MAX_SEC=300, ATTACH_GRACE_SEC=1200, BOOT_GRACE_SEC=600 (heartbeat.py:29-44).
  liveness_band reads a FUTURE ts as 'dead' (never fresh-forever).

pm_web PANEL: partials/pm_liveness.html, loaded on GET / , /account/{id}, /live/{acct}/{cat}
(app.py _load_accounts_overview / _load_account / _load_live_subdivision). liveness_alarm gates
on heartbeat.table_present(conn) AND any_alarm(rows) -> if migration 020 is absent the panel
reads "monitor not deployed" (neutral), never a false red. Box render on /account/kalshi_jack
confirms the panel is live (markers pm-lv-panel/ok/absent/alarm/boot/state/grid...).
NOTE: the panel is NOT on the tile page (/live -> pm_live_list.html) today; Phase 2 adds it.

LIVE HEARTBEAT AGE + STATE, every expected sub (2026-09-07T16:18Z; table_present=True):
  All 28 tiles are cycling. 25 RUNNING, 3 IDLE (jack/ucl, karen/cfb, karen/ucl -- 0 signals),
  0 STALE/NEVER/CATEGORY_STARVED. task_age 0-9s, evaluated_age 0-15s. (The engine cycles the 20
  disarmed tiles too -- consistent with arm-independence above; placed=0 everywhere this cycle.)
  Full per-sub ages/states are in section 13.


7. ITEM 6 -- attached whales per sub-division (UI source vs engine source)
=========================================================================
UI: subdivision.attached_whales(conn, account_id, category) [subdivision.py:115-126]:
    SELECT at.wallet, at.category, at.added_ts, w.user_name
    FROM pm_subdivision_attachment at LEFT JOIN pm_whale w ON w.wallet = at.wallet
    WHERE at.account_id=? AND at.category=? AND at.active=1
    ORDER BY (w.user_name IS NULL), w.user_name COLLATE NOCASE, at.wallet
  Table: pm_subdivision_attachment (active=1), joined to pm_whale for display names. This is the
  "Copies these whales" panel source. (list_subdivisions also exposes n_whales = active
  attachment count per tile.)

ENGINE: the live roster is the DATABASE, NOT a yaml (this CORRECTS the brief's premise "loaded
from strategy.yaml at boot"). driver_roster.py:14-16 (comment attributed to Jack): "THE ROSTER
IS THE DATABASE, NOT CONFIG ... no engine edit, no yaml." Two grains:
  - WHICH (account,category) tasks exist is decided at engine BOOT by
    driver_roster.active_driver_subdivisions (driver_roster.py:43-69) -- same
    active + active-account + >=1-active-attachment gate as the UI's expected set. Adding a NEW
    sub-division (or its first attachment) needs a restart to spawn its task.
  - WHICH whales a running task copies is re-read EVERY ~7s cycle directly from the same table
    (live_driver.py:782-783): SELECT wallet FROM pm_subdivision_attachment WHERE
    account_id=? AND category=? AND active=1. So adding/removing a whale on an already-running
    sub is picked up live, no restart.
  (rosters._load_seed_yaml exists but is the P1 seed-roster for scoring/search, NOT the live driver.)

CAN THEY DISAGREE? No, on the whale CONTENT: UI attached_whales and the engine per-cycle read
hit the identical pm_subdivision_attachment WHERE active=1 filter -> always in sync. The ONLY
divergence is "is a driver task spawned yet" for a freshly-attached sub before the next restart
-- and that is exactly what the liveness panel surfaces (PENDING "attached; awaiting the next
engine restart to spawn", then NEVER past grace). So the tile's whale list is authoritative;
whether the engine is ACTING on it is answered by the arm badge (armed?) + liveness (spawned &
cycling?), not by the whale list.


8. ITEM 7 -- which route/template serve the tile page, and its context
======================================================================
ROUTE: GET /live -> live_list_page (app.py:834-839). Handler:
    data = await asyncio.to_thread(_load_live_list)
    return templates.TemplateResponse(request, "pm_live_list.html", {"request": request, **data})
LOADER: _load_live_list (app.py:799-803): {"subdivisions": subdivision.list_subdivisions(conn)}.
TEMPLATE: pm_live_list.html (breadcrumb literally "Dashboard > Live sub-divisions"). Each tile
today shows ONLY: `{account_label} . {CATEGORY}` and an n_live_trades hint ("N live trades" or
"created . no live trades yet"), linking to /live/{account_id}/{category}. It is a dedicated
route -- NOT /, /account/{id}, /live/{acct}/{cat}, or /farm.

CONTEXT the loader passes today (list_subdivisions rows, subdivision.py:44-76): per tile
  account_id, category, sub_label, market_types, sizing_mode, fixed_stake_usd, n_whales
  (active attachment count), n_live_trades, created_ts, account_label, venue.
  -> it already carries n_whales; it does NOT carry P&L / arm / liveness / open-value.

VISIBILITY GATE (the tile set): list_subdivisions returns active sub-divisions WITH >=1 active
attachment (subdivision.py:64 `AND COALESCE(at.n,0) > 0`). There is NO static 30+ roster
anywhere (no yaml/config/hardcode) -- the enumerable set is DB-driven. BOX: 43 pm_subdivision
rows (all active), 28 have >=1 active attachment (= the 28 tiles GET /live renders; confirmed
grep class="pm-tile" = 28), 15 are attachment-less and HIDDEN (rows persist -- ruling 2,
sub-divisions are permanent; do NOT garbage-collect). A tile appears for a DISARMED/never-armed
sub as long as it has an attachment (arm state is not part of the gate). If Phase 2 must show
the 15 attachment-less subs too, that needs a NEW query dropping the COALESCE(at.n,0)>0 filter.


9. ITEM 8 -- migration
======================
NONE is required if the new tile fields are computed live per request (all readers above are
runtime SQL over existing tables). A migration is only needed if Phase 2 wants a PERSISTED
rollup table (e.g. a materialised per-sub 24h/lifetime cache) -- not recommended for 28 tiles.

If a migration IS added it MUST be numbered exactly 021 (NOT a gapped "021+"). db.py migrations
are CONTIGUOUS by a tested invariant (test_schema_head_tracks_migrations: applied versions ==
range(1, HEAD+1)); a gap breaks it. Box schema head = 20 (migration 020 = liveness, applied).
init_db upgrades via a single MAX(version) counter (db.py:945-971: `if version <= current:
skip`), so a migration numbered <= the box head SILENTLY SKIPS its DDL -- the exact hazard class
behind the 28h clobber. Any 021 deploy must drift-check the live box head == 20 immediately
before applying (migration-020 banner, db.py:872-884).


10. ITEM 9 -- app.py change for Phase 2 (graft)
===============================================
YES -- enriching each tile with P&L / 24h / open-value+coverage / arm / liveness / whales means
_load_live_list (app.py:799) must gather those per sub and pass them in context. So app.py
changes -> the standing GRAFT RULE applies: graft the additive hunks onto the BOX-CURRENT
app.py, never wholesale-copy the branch app.py.
  - Box-current app.py = 069d7a255a9f4ebc, is_admin=14, /pm/arm=0. Provenance: c2e4ddef
    (Deploy-5, is_admin=10) + the farm-search admin button graft (2026-09-05, is_admin 10->14,
    the POST /farm/search route) + the liveness panel loaders (2026-09-06). /pm/arm is STILL 0
    (the M5 arm-control route was never leaked to prod). THIS is the Phase-2 graft base -- NOT
    c2e4ddef (the stale Deploy-5 reference) and NOT the branch app.py.
  - Branch (aad4dea) app.py = 7edce7a5a8164256, is_admin=12, /pm/arm=1 -- the M5 version with
    the arm-control surface; must never ship wholesale (would leak /pm/arm to prod).
  main.py is not involved (pm_web-only).


11. DRIFT CHECK -- box pm_web vs 8978a2c / c2e4ddef
===================================================
Method: box file CR-stripped sha16 (tr -d '\r' | sha256sum) vs git aad4dea (== the 8978a2c
tree for the shipped set). RESULT: the brief's reference points are STALE -- the box has moved
past Deploy-5 via later deployed workstreams (liveness 09-06, cfb 09-06, farm/whale/search/paper
earlier). Reporting only; NOT reconciled.

  UNCHANGED vs 8978a2c/aad4dea (7):
    web/live_view.py             8fb7db158e4a5af8   web/static/pm_desk.css  18454d5690a316ed
    web/templates/pm_shell.html  934c258ce953b18d   web/templates/pm_live_list.html 6fd8aad7a1b25d71
    web/marks.py 8cace4e71d8140a0  web/poller.py d9f9f4f518b29869  web/ui_cache.py e116ee8ae07e8112
    web/feed_mlb.py 467d528460421a31  arm.py 60f447207d52694a  subdivision.py 863af1d1522fb364
    (pm_live_list.html -- the tile template -- is UNCHANGED, i.e. the Phase-2 target is the
     sparse aad4dea version.)
  DRIFTED (3 templates + app.py):
    web/templates/pm_live_subdivision.html  box fff281bf65acea7c  (8978a2c db9cb08c3b831b35)
    web/templates/pm_accounts.html          box 4a77352f14685260  (aad4dea 014c03bafe3005e5)
    web/templates/pm_account.html           box d827a560870c5f32  (aad4dea a5f39df0b53dedb4)
    web/app.py                              box 069d7a255a9f4ebc is_admin=14 /pm/arm=0
                                            (Deploy-5 ref c2e4ddef is_admin=10; branch 7edce7a5 is_admin=12)
    (the 3 templates drifted because the liveness panel was added to their routes.)
  NEW on box, ABSENT from aad4dea (liveness + other workstreams):
    trading_corp/prediction_markets/heartbeat.py                 57afdcc6c61055e2
    web/templates/partials/pm_liveness.html                      9c87b8309271d773
    pkg modules: analyze, category, farm, farm_actions, loss_grounding, paper, positions,
      search, search_run, stats, venue_exposure (+ heartbeat)
    web/templates: pm_farm_category, pm_farm_league, pm_macros, pm_whale, pm_whale_overview,
      pm_watchlist_whale, pm_*_404
    partials: pm_analyze_result, pm_paper_trade_rows, pm_position_rows, pm_prospects_rows,
      pm_search_status, pm_watchlist_rows
IMPLICATION FOR PHASE 2: the pm-ui-rewrite branch (aad4dea) is a narrow, stale subset of box
truth. Phase 2 must be built/grafted against box-current (per the box-is-truth discipline used
in every prior deploy), using the box hashes above as the graft baseline -- especially app.py
069d7a25 and the 3 drifted templates. Reconciliation is a Phase-2 deploy concern, not done here.


12. TEST BASELINE (tests/prediction_markets/, local .venv-webtest, -p no:pytest_ethereum)
=========================================================================================
16 failed, 1 skipped, all others passed -- byte-identical to the DEPLOY-4/5 env-gap baseline
(the brief's reference of 16). None touch pm_web/UI code. Breakdown:
  15x pykalshi ModuleNotFoundError (engine-driver tests; broker lib absent in the web venv):
      test_kill_switch_r7d (4), test_live_driver_r7c (7), test_shard_gate_r2 (4)
   1x test_search_r1::test_schema_head_is_15 -- STALE assertion (asserts head 15; box/branch
      head is now 20). Not a real failure; an engine-side test to update, not a UI concern.
This is the Phase-2 reference: a green Phase-2 change should leave this at exactly 16.


13. ENUMERATION REFERENCE -- 43 subs / 28 tiles / 15 hidden, per-sub state
=========================================================================
Accounts: kalshi_jack, kalshi_karen (both active). arm:global armed=True. The 28 TILES with
their live state (att = active attachments; arm from read_display; hb = liveness state @16:18Z):

  account       cat   att  arm       liveness  task_age  eval_age  open  at_cost
  ------------  ----  ---  --------  --------  --------  --------  ----  -------
  kalshi_jack   atp    2   armed     RUNNING     0s        15s      2    $6.20
  kalshi_jack   bra    1   NEVER     RUNNING     0s        13s      0
  kalshi_jack   bun    1   NEVER     RUNNING     0s        13s      0
  kalshi_jack   cfb    1   NEVER     RUNNING     0s        11s      0
  kalshi_jack   epl    1   NEVER     RUNNING     0s        11s      0
  kalshi_jack   lal    1   NEVER     RUNNING     0s        10s      0
  kalshi_jack   mex    1   NEVER     RUNNING     0s        10s      0
  kalshi_jack   mlb    3   armed     RUNNING     0s        15s      2    $5.05
  kalshi_jack   mls    1   NEVER     RUNNING     0s         9s      0
  kalshi_jack   nfl    1   NEVER     RUNNING     0s         9s      0
  kalshi_jack   ucl    1   NEVER     IDLE        0s         9s      0
  kalshi_jack   ufc    3   armed     RUNNING     0s         8s      0
  kalshi_jack   wnba   1   NEVER     RUNNING     0s         7s      0
  kalshi_jack   wta    1   armed     RUNNING     0s        14s      0
  kalshi_karen  atp    1   armed     RUNNING     9s         6s      2    $6.20
  kalshi_karen  bra    1   NEVER     RUNNING     9s         6s      0
  kalshi_karen  bun    1   NEVER     RUNNING     9s         5s      0
  kalshi_karen  cfb    1   NEVER     IDLE        9s         5s      0
  kalshi_karen  epl    2   NEVER     RUNNING     9s         4s      0
  kalshi_karen  lal    1   NEVER     RUNNING     9s         2s      0
  kalshi_karen  mex    1   NEVER     RUNNING     9s         2s      0
  kalshi_karen  mlb    3   armed     RUNNING     9s         6s      5    $14.75
  kalshi_karen  mls    1   NEVER     RUNNING     9s         1s      0
  kalshi_karen  nfl    1   NEVER     RUNNING     9s         1s      0
  kalshi_karen  ucl    1   NEVER     IDLE        9s         1s      0
  kalshi_karen  ufc    2   armed     RUNNING     9s         0s      0
  kalshi_karen  wnba   1   NEVER     RUNNING     9s         0s      0
  kalshi_karen  wta    1   armed     RUNNING     9s         6s      0
  (NEVER = no agent_state arm row -> read_display 'absent' -> effective disarmed. 8 armed total.)

  15 HIDDEN (active, 0 active attachments; NOT tiles; rows persist):
    jack:  cs2, fed, fl1, nba, nhl, sea, soccer, uel
    karen: cs2, fed, fl1, nba, nhl, sea, uel


14. PROPOSED PHASE-2 SCOPE (one paragraph)
==========================================
UI-ONLY (no new plumbing): the tile page's fields are all reachable today -- lifetime realized
P&L + settled count (subdivision.subdivision_pnl / account_pnl), open count + at-cost
(live_positions), current value + "N of M priced" coverage (value_positions over the mark
cache), arm state (arm.read_display, the four states incl. NEVER ARMED for the 20 rowless
tiles), liveness (heartbeat.read_liveness -- already deployed on the box), and attached whales
(subdivision.attached_whales / n_whales). Phase 2 is mostly: (a) a new pm_live_list.html tile
design segmented by account, 28 tiles scannable at 1280px, each tile calling the existing
readers; (b) enriching _load_live_list to gather those per sub and pass them in context
(app.py GRAFT onto box-current 069d7a25); (c) reusing the existing partials/pm_liveness.html +
pm_arm_badge.html macros on the tile. NEEDS NEW PLUMBING, but small and schema-free: ONE new
last-24h realized-P&L reader (~10 lines, the COALESCE(settled_ts,response_ts) >= now-86400 query
in section 3) -- no migration unless a persisted rollup is desired (then exactly 021). The only
structural decisions for Jack: whether the tile page shows just the 28 attached tiles or also
the 15 hidden attachment-less subs (needs a filter-dropping query); and the "lifetime P&L"
definition below. Everything is pm_web-only; the engine/order path is untouched.

15. RECOMMENDED "LIFETIME P&L" DEFINITION (for Jack to rule)
===========================================================
RECOMMEND: lifetime realized P&L = subdivision_pnl's SUM(realized_pnl) over ALL terminal closes
(is_exit=1, outcome_status='filled', dry_run=0) per (account, category) -- the number already
shown on the account pages, category-agnostic, = net realized dollars booked (settlements +
whale-exits). Rationale: it is the existing, tested reader; it needs no new query; it is the
honest "realized $ this sub has produced". Two rulings to confirm:
  (a) SETTLED COUNT displayed on the tile: subdivision_pnl.n counts ALL terminal closes,
      including non-settlement (whale-exit/opposed) rows where won is NULL, so n can exceed
      wins+losses. Decide whether the tile's count is "closes" (n, current) or "settlements"
      (close_source LIKE 'settlement%'); and whether the W-L record shows wins/losses (won
      non-NULL) with the non-won closes excluded. RECOMMEND: label it "settled W-L" from
      wins/losses and show realized $ separately, so the two never look inconsistent.
  (b) FEES / OPEN MTM: confirm whether realized_pnl is net of fees (settlement.book_settlements
      writes `realized`; verify gross vs net before labelling), and note the tile's lifetime
      figure is REALIZED only -- open positions' unrealized mark-to-market is shown separately
      as the open current-value/coverage, never folded into lifetime P&L.

APPENDIX -- runners (cc/, read-only; logged)
  pm_tiles_inv_data_ro.{ps1,sh}   pm_tiles_inv_drift_ro.{ps1,sh}
  local baseline: .venv-webtest -m pytest tests/prediction_markets/ -p no:pytest_ethereum
