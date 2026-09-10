PM TILES REDESIGN -- DATA INVENTORY (READ-ONLY) -- 2026-09-09
=============================================================

Scope: before Jack commissions a Claude-Design redesign of the Live Sub-divisions tile page
(/live), establish for each proposed field whether the data EXISTS / is DERIVABLE / is NOT
AVAILABLE, with box-truth evidence and an effort estimate. INVENTORY ONLY -- no design, no UI
proposal, no engine/DB/deploy/restart. 100% read-only: mode=ro DB reads, unauthenticated GETs
to Kalshi's PUBLIC market endpoint (the same one pm_web already polls), loopback pm_web GETs,
box file sha16, and local code reads. Nothing was written, deployed, or restarted.

Worktree cc-pm-tiles-redesign-inv-wt, branch pm-tiles-redesign-inventory-2026-09-09 (off
pm-tiles-2026-09-07 @ 55bf91f). Box observed 2026-09-10T01:09-02:18Z, host tc-prod-vm.
Runners (cc/, read-only, sanctioned .ps1, board-authorized per step):
  pm_redesign_data_ro.{ps1,sh}    -- state + drift + DB facts + ticker samples + schema
  pm_redesign_code_ro.{ps1,sh}    -- ticker samples + Kalshi endpoint + loopback + app.py auth/route/poller
  pm_redesign_authz_ro.{ps1,sh}   -- authz.py sha + owner_identity/PM_ADMIN_IDENTITIES + item-12 values
Local: matcher + mark-path code read from box-sha-confirmed files; test baseline in .venv-webtest.

METHOD NOTE (fired once, as warned): the Kalshi public endpoint returns `occurrence_datetime`,
which looks like "event start" -- but checking its VALUE against the known ticker HHMM start
proved it is the expected RESOLUTION time (== expected_expiration_time, ~event END), NOT the
start. Reported as resolution, not start. Suspect the measurement first.


================================================================================================
1. THE 18 ITEMS -- STATUS / EVIDENCE / EFFORT
================================================================================================
Legend: EXISTS = a reader/route returns it today, zero work. DERIVABLE = small new pm_web code,
no migration unless noted. NOT AVAILABLE = the engine (or a new source) would have to produce it.
"Effort" is pm_web lines unless it says ENGINE. All money readers are box-sha-confirmed identical
to the deployed Deploy-6 files (section 3), so their source is quoted as box-truth.

  #   Field                                Status        Evidence (box-truth)                         Effort
  --  -----------------------------------  ------------  -------------------------------------------  --------
  A. HEALTH
  1   Arm state per sub + row ts           EXISTS        arm.read_display_all (arm.py b542e9ff);      0
                                                         legacy agent_state pm_live arm:{acct}:{cat}; already on tile
  2   Liveness + heartbeat age + state set EXISTS        heartbeat.read_liveness (57afdcc6); see 2.a  0
  3   Last-cycle summary (signals/placed)  EXISTS        pm_driver_heartbeat ROW cols n_signals/      0
                                                         placed/errors/ceiling_latched (not logs)
  4   Global arm + ts                      EXISTS        arm:global armed=True ts 2026-08-31T02:35Z   0
  B. MONEY  (basis = REALIZED-ONLY + OPEN-AT-COST; MTM deferred -- see 2.b)
  5   All-time realized/booked/W-L/unbooked EXISTS        subdivision.subdivision_pnl_all; already on  0
                                                         tile (R3); net of fees; unbooked split out
  6   Last-24h realized + window def       EXISTS        subdivision.realized_24h_all; SETTLEMENTS    0
                                                         only, keyed settled_ts, rolling 86400s
  7   Last-7d / last-30d realized+settled  DERIVABLE     realized_24h_all ALREADY takes window_sec;   ~5-15
                                                         settled_ts 100% populated per cat (2.c)     (no migration)
  8   Realized "today" (calendar vs 24h)   EXISTS(both)  _realized_today = ET CALENDAR day; tile 24h  0 or ~10
                                                         = ROLLING; ruling needed which to show
  9   Open count/at-cost/value/N-of-M/age  EXISTS        live_positions + value_positions over LIVE   0
                                                         mark cache; Mark.as_of age exists (2.d/add1)
  C. ACTIVITY STATE  (the critical unknown)
  10  Ticker carries START TIME?           MIXED         MLB+CS2 ticker carry HHMM; structural HHMM   see 2.e
                                                         optional; tennis/ufc/soccer/fed = DATE only
  11  MLB feed in-progress/final/preview    EXISTS        feed_mlb GameState.status; join proven on    0 (MLB
                                                         card page /live/kalshi_jack/mlb              only)
  12  Other-cat "event underway" source     PARTIAL       Kalshi public endpoint: status(active/       small
                                                         finalized)+result now; occurrence=resolution (see 2.f)
  13  Activity classification               DERIVABLE     from 9-12 (settled/upcoming-or-underway/     ~20-30
                                                         settling/inactive/unattached); add4 dead rows
  D. EVENTS (60s poll animation/sound)
  14  New trade placed (diff order ids)     DERIVABLE     pm_subdivision_order id+submitted_ts+sub;    ~15-30 JS
                                                         60s poll exists, NO diff logic yet (2.g)
  15  Trade closed with result              DERIVABLE     is_exit=1 close_source/realized_pnl/won/     ~15-30 JS
                                                         settled_ts at booking; latency 2.h
  16  Heartbeat STALE on armed sub          EXISTS        alarm predicate built+tested (armed AND      0
                                                         liveness STALE/NEVER); any_alarm=False now
  E. VIEWER SCOPING
  17  Login -> account mapping              EXISTS+GAP    authz.py b566c96f: identity=Remote-User,     ~10-20
                                                         admin=PM_ADMIN_IDENTITIES(=jack), owner_id;  (SECURITY,
                                                         karen mapped; NOT applied to /live (add5)    see 2.i)
  F. ASSETS
  18  Static path + cache-bust              EXISTS        web/static/ served at /static; ?v=<sha8>     trivial
                                                         manual, CI-enforced (test_asset_cache_bust)


================================================================================================
2. DETAIL + THE SIX ADDITIONS
================================================================================================

2.a  ITEM 2 -- the FULL set of states (confirmed).
  heartbeat.read_liveness CLASSIFIES into: RUNNING, IDLE, CATEGORY_STARVED, STALE, NEVER (plus
  internal PENDING/BOOTING during attach/boot grace). These are DERIVED from ts freshness +
  n_signals, NOT from a stored string. The raw `state` COLUMN the engine writes is a SEPARATE
  thing: on the box it has exactly ONE distinct value today -- 'evaluated' (30/30 rows). The
  other documented column values ('skipped_no_builder','skipped_no_ctx') are POSSIBLE but not
  currently present (every attached sub is evaluating). Box now: 26 RUNNING + 4 IDLE (jack/ucl,
  karen/cs2, karen/epl, karen/ucl -- 0 signals), 0 STALE/NEVER/CATEGORY_STARVED, any_alarm=False.
  task_age 11-13s, evaluated_age 2-18s. The heartbeat row also carries reached_ts/evaluated_ts/
  errors/ceiling_latched -- a full last-cycle summary in the row (item 3), not only in logs.

2.b  ITEM 5-9 -- P&L BASIS IS A RULING, honoured (add #2).
  Every deployed money reader is REALIZED-ONLY (booked terminal closes) or OPEN-AT-COST. NONE
  computes mark-to-market P&L. Box-truth source:
    - subdivision_pnl_all: realized = SUM(realized_pnl) over BOOKED closes (realized_pnl NOT
      NULL); NET of fees (entry fees are in the cost basis, settlement fee=0); wins/losses from
      won=1/0 (settlements); unbooked_closes (realized_pnl NULL) counted SEPARATELY.
    - realized_24h_all: settlements only, keyed settled_ts.
    - live_positions -> at-cost (cost_basis_usd). value_positions -> current value (contracts x
      held-leg BID) as a DISTINCT figure with "N of M priced" coverage; unpriced = None, never $0.
  CONFLATION RISK FLAG: cost and current value live on distinct keys at every layer (cost /
  cost_basis_usd vs current_value / value; open_cost vs open_value on the card). They are never
  merged. The redesign MUST keep them distinct and MUST NOT present current value as realized
  P&L -- open MTM is shown as open value+coverage, never folded into the realized figure. This is
  the platform's hard rule (the empty-shard-behind-a-healthy-balance failure class).

2.c  ITEM 7 -- 7d/30d is a PARAMETER CHANGE, not a new reader.
  realized_24h_all(conn, now_ts, *, window_sec=86400) already parameterises the window. 7d =
  window_sec=604800, 30d = 2592000 (or generalise to realized_window_all + a loop). No migration.
  settled_ts is populated for EVERY settlement in EVERY category (grouped-by-category NULL count,
  box): atp settle_NULL_ts=0, cfb 0, cs2 0, mlb 0, ucl 0, wta 0. The only rows without settled_ts
  are NON-settlement closes (whale-exit/opposed: atp 3, mlb 11) which are correctly EXCLUDED from
  the settlements-only window (they carry response_ts, not settled_ts). Box windowed numbers (now
  2026-09-10T02:18Z), settlements only:
    24h: jack mlb $3.72/10, jack atp $3.74/1, jack cs2 -$1.95/2, jack ucl $2.26/1, karen mlb
         -$4.15/14, karen ucl $2.26/1
    7d:  jack mlb $12.87/41, karen mlb -$0.14/48, jack ucl/karen ucl $7.92/4 each, ... (10 rows)
    30d: == lifetime for every current cat (all closes are within 30d).

2.d  ITEM 9 + ADD #1 -- THE MARK CACHE EXISTS AND IS RUNNING (add #1 fear REFUTED).
  The brief flagged that pricing HELD positions may never have been built. It WAS, and it is live:
    - poller.poll_loop(interval=60) is spawned at @app.on_event("startup") ->
      asyncio.create_task(poller.poll_loop(ui_cache.cache(), series_provider=_held_series_provider)).
    - marks.fetch_marks hits the PUBLIC endpoint api.elections.kalshi.com/trade-api/v2/markets
      (status=open), NO credentials (marks.py: "STANDALONE + CREDENTIAL-FREE by construction").
    - series to price are derived from tickers WE CURRENTLY HOLD (subdivision.traded_series), so
      every category prices, not just MLB.
    - value = contracts x held-leg BID (marks.bid_for_leg).
  RUNTIME PROOF (loopback, 4 open-position subs): /live/kalshi_jack/mlb, /kalshi_jack/lal,
  /kalshi_jack/mls, /kalshi_karen/mlb all HTTP 200 with live current values and "N of N priced"
  (and an honest "no mark" where a leg has no bid).
  AGE BANDING (add #1's second demand): the Mark dataclass carries `as_of: int  # unix seconds
  when fetched -- the mark's own age`; _cache_marks() returns (marks, snap.refreshed_ts). Age is
  ALREADY surfaced on the detail page (the loopback pages showed "42s ago" / "9s ago"). So a stale
  mark can be BANDED and shown as N-seconds-old rather than passed off as current. Current-value,
  "how open trades are doing," and coverage are AVAILABLE, not NOT-AVAILABLE. A tile drawn around
  them is safe -- provided it shows the age (the honest-freshness rule).

2.e  ITEM 10 + ADD #3 -- START TIME BY CATEGORY (matchers already established this; box tickers confirm).
  Each matcher keys on the card-LOCAL date encoded in the ticker, NEVER close_time/occurrence.
  What the ticker encodes (real held/settled tickers from the journal in brackets):
    MLB        KXMLBGAME-{YYMMMDD}{HHMM}{TEAMS}    DATE + START TIME  [KXMLBGAME-26SEP091835CLEBAL-CLE]
    CS2        KXCS2GAME-{YYMONDD}{HHMM}{BLOB}     DATE + START TIME  [KXCS2GAME-26SEP090700ACE3DMAX-3DMAX]
               (matcher captures HHMM inside the blob and does NOT split it out today)
    NFL/NBA/   KX{L}GAME-{YYMMMDD}{HHMM?}{TEAMS}   DATE + OPTIONAL time (nba omits; this cfb omitted)
    NHL/WNBA/CFB                                    [KXNCAAFGAME-26SEP07SMUFSU-SMU  -- no HHMM]
    Tennis     KX(ATP|WTA)MATCH-{YYMONDD}{BLOB6}   DATE ONLY  [KXATPMATCH-26SEP09ZVEVAN-VAN]
    UFC        KXUFCFIGHT-{YYMONDD}{BLOB6}         DATE ONLY  (fight/distance)
    Soccer     KX{LEAGUE}GAME-{YYMONDD}{BLOB}      DATE ONLY  [KXLALIGAGAME-26SEP13GETDEP-GET,
               (epl/ucl/uel/lal/fl1/sea/bun/mls/bra/mex)      KXMLSGAME-26SEP09CHIMIA-CHI, KXUCLGAME-26SEP09NAPARS-ARS]
    FED        KXFEDDECISION-{YYMON}-{BUCKET}      NEITHER (month + H0/H25/H26/C25/C26 bucket)
  So: only MLB has a UI-usable PARSED start today (live_view.game_key_from_ticker). CS2 and the
  structural sports have an HHMM IN the ticker (parseable, ~10-20 lines), but for tennis/ufc/
  soccer/fed there is NO start time in the ticker at all -- DATE (or month) only.
  WARNING CARRIED (add #3): close_time is NOT the event. On the box, the MLB ticker's close_time
  (2026-09-10T01:24Z) is ~3h after first pitch (18:35 ET = 22:35Z) -- it is ~game end / trading
  halt. latest_expiration_time (+2 days for MLB; up to ~2 weeks for UFC) is the administrative
  settlement buffer. Neither is a start time. Do not label either as the event start.

2.f  ITEM 12 + ADD #3 -- KALSHI PUBLIC ENDPOINT (probed read-only for 5 held tickers).
  The endpoint pm_web already polls returns, IDENTICALLY for every category:
    status (active | finalized), result (yes/no/"" ), occurrence_datetime, expected_expiration_time,
    open_time, close_time, latest_expiration_time, market_type, yes_bid/no_bid, title, ...
  Measured values (item-12 probe):
    MLB CLEBAL (past game):  status=finalized result=no   occurrence=2026-09-10T01:35Z (==expected_expiration)
    CS2 ACE3DMAX (past):     status=finalized result=yes  occurrence=2026-09-09T15:00Z (==expected_expiration)
    UCL NAPARS (past):       status=finalized result=yes  occurrence=2026-09-09T22:00Z (==expected_expiration)
    La Liga GETDEP (9/13):   status=active    result=""   occurrence=2026-09-13T22:00Z (==expected_expiration)
    MLS LAFCNYRB (9/9-10):   status=active    result=""   occurrence=2026-09-10T05:30Z (==expected_expiration)
  KEY: occurrence_datetime ALWAYS == expected_expiration_time, and for MLB it is ~3h AFTER the
  ticker HHMM start -> it is the expected RESOLUTION time (~event END), NOT the start. So the
  endpoint gives NO event START for any category (start comes only from the MLB/CS2/structural
  ticker HHMM). What IS cleanly derivable per category, no engine work:
    - SETTLED           = status==finalized (or result set)      -- EVERY category, reliable
    - UPCOMING-or-UNDERWAY = status==active AND occurrence in the future
    - ENDED / SETTLING  = status==active AND occurrence passed (in the settlement lag), or just-finalized
  A TRUE "underway right now" is honest only for MLB (the feed's in_progress) and, with a small
  ticker-HHMM parse, the timed categories (start<=now<occurrence). For tennis/ufc/soccer/fed the
  best honest states are SETTLED / UPCOMING-or-UNDERWAY / SETTLING -- not a live "in play" flag.
  NB: the Mark object pm_web already caches carries `status`, so settled-vs-active is reachable
  from the existing cache; occurrence_datetime is not cached today (a ~10-line poller field-add,
  still pm_web-only).

2.g/2.h  ITEMS 14/15 -- EVENT DETECTION (data present; the diff hook is net-new JS).
  pm_subdivision_order columns (box PRAGMA): id (INTEGER PK), account_id, category (= the
  sub-division), signal_id, client_order_id, broker_order_id, ticker, order_side, outcome_leg,
  is_exit, outcome_status, fill_count/price, fee, dry_run, submitted_ts, response_ts, close_source,
  realized_pnl, won, settled_ts. So a NEW order is a new (id, submitted_ts) for an (account,
  category); a CLOSE-with-result is is_exit=1 with close_source/realized_pnl/won/settled_ts, all
  written AT booking. The page already polls every 60s: pm_live.js poll() re-fetches the page
  (X-Poll:1 header) and does a FULL <main> innerHTML swap + a blanket .flash on all [data-val] --
  there is NO id-diff / compare-between-polls logic. So "a new order appeared" or "this close just
  settled" is DERIVABLE but net-new (a max(id)/since-id diff in JS, optionally a lightweight
  since= endpoint). CLOSE LATENCY (item 15): a close row appears when the ENGINE books the
  settlement -- Kalshi settlement_timer_seconds is 30-120s, plus the engine's ~7s live cycle --
  so seconds-to-a-few-minutes after Kalshi finalises, not instant; realized_pnl/won are present
  the moment the row is written.

2.i  ITEM 17 + ADD #5 -- SCOPING MECHANISM EXISTS AND IS CONFIGURED; THE /live ROUTES DON'T USE IT.
  authz.py (box b566c96f == local) is the login->account layer:
    - identity   = current_identity(request) = first of Remote-User / X-Forwarded-User /
                   X-Remote-User (Authelia forwards it; pm_web is loopback-only behind the proxy).
    - admin      = is_admin(request) = identity in PM_ADMIN_IDENTITIES env. BOX: PM_ADMIN_IDENTITIES=jack
                   (in both /proc/<pid>/environ and the systemd unit Environment=). Fail-closed:
                   unset -> nobody admin.
    - visibility = visible_account_ids(identity, is_admin, accounts): ADMIN sees all; a non-admin
                   sees ONLY accounts whose owner_identity == identity; NULL owner_identity is
                   admin-only; no identity -> nothing.
  BOX pm_account.owner_identity: kalshi_karen='karen', kalshi_jack=None. So TODAY: a 'karen' login
  (non-admin) is correctly scoped to kalshi_karen; 'jack' is admin (PM_ADMIN_IDENTITIES) and sees
  both; kalshi_jack is admin-only until someone sets its owner_identity. This scoping is APPLIED on
  GET / (_load_accounts_overview) and GET /account/{id} (_load_account) -- both call
  visible_account_ids and fail-closed BEFORE reading balances.
  THE GAP (add #5, security): GET /live/{account_id}/{category} (live_subdivision_page, app.py:999)
  has NO authz check -- it strips the slug, loads the sub-division, and renders any account's
  positions to any authenticated user (its own docstring: "SAME template/code path for EVERY
  account -- nothing hardcodes jack"). And the tile page loader _load_live_list (app.py:934) calls
  subdivision.tiles_all with NO scoping -- it shows every account's tiles. So the WHOLE /live
  surface is unscoped today. This is documented-intentional from the single-user era and is gated
  on Karen getting a real login. IMPLICATION FOR THE REDESIGN: scoping the tile page while leaving
  the detail route open is security theatre -- a Karen-scoped /live is meaningless unless
  /live/{acct}/{cat} is scoped too. The fix is to APPLY the existing authz.visible_account_ids to
  both _load_live_list and live_subdivision_page (~10-20 lines, no new plumbing). No change made.

2.j  ITEM 13 + ADD #4 -- DEAD ROWS AND THE ORPHAN (confirmed).
  43 sub-divisions, 30 attached (tiles), 13 attachless (hidden, permanent -- sub-divisions cannot
  be deleted by design). All 30 attached are ARMED (grew from 28/15 at Phase-1: cs2 got attached
  on both accounts). The 13 hidden: jack {fed,fl1,nba,nhl,sea,soccer,uel}, karen {fed,fl1,nba,nhl,
  sea,uel}. THE ORPHAN: kalshi_jack/soccer (att=0, created 1788757634, jack-only) exists from a
  mis-targeted attach; 'soccer' was RETIRED as a category in favour of the ten per-league ones, so
  it has NO matcher and can NEVER trade. It is not a bug. The designer must know the page carries
  permanent dead rows (13 hidden today) plus this one orphan that would surface if the redesign
  ever shows attachless subs.

2.k  ITEM 18 -- STATIC ASSETS (for Jack's per-league logo PNGs, served locally, no CDN).
  Directory: trading_corp/prediction_markets/web/static/ (mounted at /static). Cache-bust: each
  URL carries ?v=<CR-stripped sha8 of the file>, set MANUALLY in the template; test_asset_cache_bust
  fails CI if a changed asset's hash is not bumped (so stale assets can't ship). No far-future
  max-age, no build step. To add logo_epl.png: drop it in static/, compute its sha8, reference
  /static/logo_epl.png?v=<sha8>, add the file+hash to the cache-bust test. Trivial, per asset.


================================================================================================
3. ALSO REPORT -- BOX STATE, DRIFT, TEST BASELINE
================================================================================================
BOX STATE (2026-09-10T01:09Z):
  engine trading-corp    MainPID 282839  NRestarts 0  active
  pm_web prediction-markets-web MainPID 235587 NRestarts 0 active (WorkingDirectory /home/azureuser/trading_corp)
  PM schema head = 20 (next migration = 021; contiguity invariant, drift-check head==20 before any 021)
  box app.py CR-stripped sha16 = eeac337d17a84fc7  is_admin=14  /pm/arm=0   -- MATCHES the expected Deploy-6 ref

DRIFT -- /live Deploy-6 files vs the Phase-2 AFTER shas: NO DRIFT. All box CR-stripped sha16 match
exactly (the box /live surface is precisely Deploy 6):
  subdivision.py 752e244a  arm.py b542e9ff  web/live_view.py e514a47a  pm_live_list.html 78212415
  pm_desk.css 8121e8e0  pm_shell.html d8076b29  heartbeat.py 57afdcc6
  mark path box==branch: poller d9f9f4f5, marks 8cace4e7, ui_cache e116ee8a, feed_mlb 467d5284,
  pm_live.js b4c557fc, pm_live_subdivision.html fff281bf, partials/pm_liveness.html 9c87b830
  authz.py b566c96f == local. matchers all == deploy shas (cs2 e0063823, soccer 78cd53e2, fed
  3c30720b, sports_structural 7a6f08bf, ...). No file on the /live path or its readers has drifted.
  (Only STRUCTURAL change since Phase-1: cs2 attached both accounts -> 30 attached tiles / 13 hidden,
  up from 28/15; all 30 now armed.)

TEST BASELINE (tests/prediction_markets/, .venv-webtest, -p no:pytest_ethereum):
  16 failed, 853 passed, 1 skipped. All 16 = the documented env-gap baseline: 15x pykalshi
  ModuleNotFoundError (engine-driver tests: test_kill_switch_r7d x4, test_live_driver_r7c x7,
  test_shard_gate_r2 x4) + 1x test_search_r1::test_schema_head_is_15 (stale assertion; head is 20).
  ZERO UI/tile failures. Matches the Phase-1/2 baseline of 16 exactly.

FREEZE (add #6): a cross-division git-truth reconcile is being planned with a code freeze. THIS
INVENTORY IS 100% READ-ONLY and touched no file, engine, order path, arm state, or service -- safe
under the freeze. Of the DERIVABLE items, EVERY ONE is pm_web-only (readers, an authz application,
JS, an asset, a ticker parse): NONE requires an engine change or a migration. The only way the
redesign would bite the freeze / shared-file graft rules is if the designer asked the ENGINE to
persist a new per-event start-time or status field -- which is NOT needed: the Kalshi public
endpoint (status/occurrence) plus the ticker HHMM cover the activity signal pm_web-side. So the
redesign is buildable entirely in pm_web under the freeze; flag any engine ask as out-of-scope.


================================================================================================
4. WHAT THE DESIGNER CAN SAFELY ASSUME EXISTS vs WHAT MUST BE BUILT FIRST
================================================================================================
CAN ASSUME EXISTS (live on the box, zero work, already on the tile page unless noted): per-sub arm
state (4-state ARMED/DISARMED/NEVER-ARMED/UNAVAILABLE + row age) and global arm; driver liveness
(RUNNING/IDLE/CATEGORY_STARVED/STALE/NEVER + age + the last-cycle n_signals/placed/errors/latched
from the heartbeat row); the armed-AND-STALE alarm predicate; all-time realized P&L (booked, net of
fees, W-L, with unbooked closes split out); last-24h realized (rolling, settlements-only); open
positions (count, at-cost, current value from the LIVE mark cache, "N of M priced" coverage, and a
mark AGE that is already surfaced); and -- for MLB only -- a real game feed (preview/in_progress/
final) with a working ticker->game join. The login->account scoping mechanism exists and is
configured (karen -> kalshi_karen; jack = admin).

MUST BE BUILT FIRST (do not draw a tile around these as if they exist): (a) any cross-category
"event underway" indicator -- only MLB has a true one; every other category can honestly show at
most SETTLED vs UPCOMING/UNDERWAY-ambiguous vs SETTLING from the Kalshi status/occurrence fields,
and occurrence_datetime is the RESOLUTION time, never the start; (b) 7d/30d realized (trivial --
window_sec already exists); (c) new-trade / trade-closed pulse animation or sound (the data is
there; the between-polls DIFF is net-new JS); (d) per-league logo assets (drop-in + versioned URL);
(e) SCOPING -- the /live tile page AND the /live/{acct}/{cat} detail route are BOTH unscoped today,
so a Karen-scoped tile page is only real if the detail route is scoped in the same pass.

MUST NOT ASSUME: current value / unrealized mark-to-market as a P&L number (basis is realized-only
+ open-at-cost; MTM was deferred -- never fold current value into realized); occurrence_datetime or
close_time as an event START time; a start time for tennis/ufc/soccer/fed (the ticker has none).


================================================================================================
5. BUILD ITEMS RANKED BY EFFORT (ascending) -- all pm_web-only, none needs a migration or the engine
================================================================================================
  0  (exists) 1,2,3,4,5,6,9(value+coverage),11(MLB feed),16(alarm) -- assume present.
  ~2-5 lines   7  last-7d/30d realized       (call realized_24h_all with window_sec=604800/2592000)
  ~2 lines     9b mark AGE on the tile        (as_of already exists; render "Ns ago" like the detail page)
  trivial      18 per-league logo assets      (file in static/ + ?v=<sha8> + cache-bust test entry)
  ~10 lines    8  calendar-day-ET realized    (only if ruled over rolling-24h; mirror realized_24h_all on _et_date)
  ~10-20 lines 17 SCOPE /live + detail route   (SECURITY -- apply existing authz.visible_account_ids; do FIRST if scoping)
  ~10-20 lines 12 settled/active classification (Mark.status already cached; +occurrence poller field if "settling" wanted)
  ~15-30 JS    14 new-trade pulse              (max(id)/since-id diff between polls)
  ~15-30 JS    15 trade-closed pulse           (new is_exit=1 rows with realized_pnl/won between polls)
  ~20-30 lines 13 activity classifier          (compose 9-12 into underway/upcoming/settling/inactive/unattached)
  ~10-20 lines 10 parse HHMM for CS2/structural (PARTIAL value: gives a start ONLY for the timed cats;
                                                 tennis/ufc/soccer/fed have NO ticker start -- do not promise "underway" there)

APPENDIX -- runners (cc/, read-only; board-authorized per step; logged)
  pm_redesign_data_ro.{ps1,sh}   pm_redesign_code_ro.{ps1,sh}   pm_redesign_authz_ro.{ps1,sh}
  local baseline: .venv-webtest -m pytest tests/prediction_markets/ -p no:pytest_ethereum
