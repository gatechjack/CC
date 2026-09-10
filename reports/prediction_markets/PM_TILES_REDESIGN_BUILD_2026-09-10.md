PM TILES REDESIGN -- BUILD -- 2026-09-10
========================================

Board-authorized build of the Claude-Design "Live sub-divisions" redesign (GET /live). pm_web-only:
no engine import, no order path, no migration, no restart, no deploy, no push. Worktree
cc-pm-tiles-redesign-wt, branch pm-tiles-redesign-2026-09-10 (off pm-tiles-redesign-inventory-2026-09-09).
Graft base for app.py = box eeac337d (captured read-only, commit 4d6bf15). All other pm_web files were
confirmed box-identical in the 2026-09-09 inventory.

Design source: the 5 handoff bundles (jack/karen/alarm/events/phone) -> project/subdivisions-jack.html (the
primary; the other four differ only by the VIEW constant + one datum: karen=viewer_role account, alarm=MLS
liveness STALE, events=the animation page, phone=iframe at 374px). Notes: project/subdivisions-notes.md.


1. FIELD MAP -- every MOCK key -> its source
=============================================
Legend: EXISTING = a deployed reader/const already returns it. NEW(reader) = a new pm_web reader (named).
COMPUTED = derived in the assembler/template from other fields. ASSET = a static file. Ages are passed as
`*_age_seconds` and tick client-side (as the design does), so the server sends age-at-render.

  MOCK path                         Source
  --------------------------------  -------------------------------------------------------------------
  meta.global_arm                   EXISTING  arm.read_display() global_state (via _arm/read_display_all)
  meta.global_arm_age_seconds       EXISTING  live_view._ts_age(global ts, now)
  meta.poll_interval_seconds        EXISTING  live_view.POLL_INTERVAL_SECONDS (60)
  meta.generated_age_seconds        COMPUTED  0 at render (now_ts); JS ticks "updated Ns ago / next poll"
  meta.viewer_role                  NEW-graft authz.is_admin(request) -> "admin" | "account" (R6)
  meta.viewer_account               NEW-graft the single visible account for a non-admin, else null (R6)
  meta.tz_note                      COMPUTED  static string (US Eastern calendar day/week/month)
  meta.thin_threshold               EXISTING  search.DEFAULT_MIN_RESOLVED_FLOOR (50)
  accounts[]{id,name,venue,slug}    EXISTING  subdivision.accounts_overview / active_accounts, SCOPED by
                                              authz.visible_account_ids (R6). name=label, venue=venue.
  sports{CODE:{family,logo}}        NEW-const live_view.SPORTS (category -> family) + logo existence in
                                              static/logos/<CODE>.png. Display metadata (design requires it).
  live_capable[]                    NEW-const live_view.LIVE_CAPABLE = MLB,CS2,NFL,NBA,NHL,WNBA,CFB
                                              (MLB feed + the 6 whose ticker carries an HHMM start; item 10)
  subdivisions[].code               EXISTING  tiles_all category (upper-cased)
  subdivisions[].account            EXISTING  tiles_all account_id
  subdivisions[].activity           NEW       classify_activity() -> LIVE|UPCOMING|SETTLED|INACTIVE|UNATTACHED
  subdivisions[].arm{state,age}     EXISTING  arm.read_display_all + _ts_age
  subdivisions[].liveness{...}      EXISTING  heartbeat.read_liveness SubLiveness (state, age_sec, n_signals,
                                              placed, errors) -> {state, heartbeat_age_seconds, signals, orders, errors}
  subdivisions[].whales             EXISTING  tiles_all n_whales (count only -- identities stay on detail page)
  subdivisions[].realized.today     NEW(reader) realized_windows_all -> ET-calendar today (booked closes)
  subdivisions[].realized.week      NEW(reader) realized_windows_all -> ET-calendar week (booked closes)
  subdivisions[].realized.month     NEW(reader) realized_windows_all -> ET-calendar month (booked closes)
  subdivisions[].realized.all_time  EXISTING  subdivision_pnl_all.realized (== the account page; ties out)
  subdivisions[].realized.booked_closes/wins/losses/unbooked  EXISTING  subdivision_pnl_all
  subdivisions[].open{count,cost,value,priced,of}  EXISTING  live_positions + value_positions
  subdivisions[].open.mark_age_seconds  EXISTING  _cache_marks() refreshed_ts age
  subdivisions[].event{...}         NEW       LIVE only. MLB: feed_mlb scoreboard (game_key_from_ticker +
                                              match_in_slate + _feed_block) + positions named via R2; non-MLB
                                              live-capable: label + positions, no scoreboard (degrades honestly)
  subdivisions[].next_event{label,starts_in_seconds}  NEW  UPCOMING: soonest held event, label via R2;
                                              starts_in only for LIVE_CAPABLE (ticker HHMM); else null
  subdivisions[].last_trade         NEW(reader) journal max submitted_ts (in context; NOT rendered -- notes 74)
  subdivisions[].last_close{age,result,realized}  NEW(reader) journal newest booked is_exit close
  subdivisions[].orphan             NEW       category in the retired set of live_view.SPORTS (soccer). R7.

  Naming (R2), one helper name_market(ticker, leg, mark, feed_game, category), priority:
    feed team names (MLB) -> Mark.title (cached) -> market_describe.describe_market (MLB) ->
    "<CATEGORY> <market_type_from_ticker>" (never the raw ticker; the exception is logged in the report).
