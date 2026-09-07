# Remaining-Categories Plan — LIVE HANDOFF (read this instead of scrollback)

## ★★★ PROGRAM WRAP (FIRST READ) — all 10 remaining categories resolved, 2026-09-07
**What was delivered:** the 10 remaining Kalshi-copyable categories taken from research to LIVE-AND-DISARMED
(golf on hold), across **4 matcher families**, **5 new matchers**, **30+ sub-divisions**, with **ZERO wrong
picks across every dry-run**.
- **Matcher families (4):** (1) STRUCTURAL ticker-join — mlb (own module) + the shared sports_structural_match
  for nfl/nba/nhl/wnba/cfb; (2) TITLE/NAME pair-key — ufc + tennis(atp/wta) + cs2 (exact-normalized, never fuzzy);
  (3) SOCCER 3-way (team-win + draw->TIE), per-league, 10 leagues; (4) fed EVENT+BUCKET (no teams/date).
- **5 new matchers:** sports_structural_match (rung 1), cs2 (rung 2), soccer (rung 3), fed (rung 4), + the
  per-league soccer CLASSIFIER change (retire coarse 'soccer'). golf = scoped, on hold (name-matchable, no code table).
- **LIVE + DISARMED now:** cs2, nfl/nba/nhl/wnba/cfb, epl/ucl/lal/fl1/uel/mls/sea/bun/bra/mex (10 soccer), fed --
  all deployed, engine-restarted, sub-divisions created disarmed+unattached (dormant). The original 8 (atp/mlb/ufc/
  wta x2) traded UNTOUCHED throughout; nfl attached (jack+karen), in the roster, disarmed.
- **Discipline that held every time:** box-is-truth grafts (drift-check base -> sha-VERIFY placed==COMMITTED
  artifact, never the overlay) -> restore-on-mismatch; disarmed dry-run as the gate; created_ts + content-SHA
  proofs that pre-existing rows never moved; and -- the sharpest lesson -- **a gate cannot validate its own
  transform** (cs2 alias caveat -> PSG near-miss, the first that would've placed a real order on the wrong club;
  auto-generated maps validated against INDEPENDENT EVIDENCE, never the gate that uses them).

### THREE THINGS THE NEXT AGENT INHERITS
1. ★ **THE STANDING ARM-GATE (a gate, not a nice-to-have):** nba, nhl, wnba AND mex each need a real-market
   dry-run (cc/build_dryrun.py: box-RO Poly bets + off-box KX{X}GAME fetch + local match; gate = 0 wrong team,
   0 wrong market-type, misses classified) BEFORE arming. nfl + cfb are ALREADY dry-run-proven (both surfaced
   real bugs ONLY against live data -- nfl LA/WSH/LAS, cfb the SDST collision). Do NOT skip this. cs2 + the
   soccer leagues (epl/ucl/lal/fl1/uel/mls/sea/bun/bra/mex) + fed are dry-run-proven at build.
2. ★ **GOLF ON HOLD (not dropped):** Jack rules every Kalshi-copyable category goes live; golf qualifies;
   buildable when he says. ★ KEY finding to preserve: NO season code table needed -- golf is NAME-MATCHABLE from
   the live Kalshi feed (see the GOLF section + GOLF_SCOPING_2026-09-07.md). Build scope: accent-fold golfer names,
   a tournament-alias table (cs2 shape), esports-'Masters' exclusion, field/futures settlement, a loud unmapped guard.
3. ★ **WHAT IS JACK'S, IN ORDER -- ★ THE STANDING ORDER IS: SWEEP -> ROLLUP -> PIN -> ATTACH -> ARM:**
   (a) SEARCH SWEEP (`pm_cli search`, a fresh subprocess -- NO restart; reclassifies the 24k coarse-'soccer' rows
       per-wallet too). ★ IT DOES NOT SCORE. `_cmd_search` calls `stats.rollup` (pm_category_stats) but OMITS
       `stats.compute_scores` (pm_score_snapshot) -- unlike `_cmd_backfill`/`_cmd_rollup` which call BOTH. So a
       sweep ALONE leaves the RANKED leaderboard STALE (candidates + raw stats are fresh; scores are not). This is
       the trap the 2026-09-07 sweep hit: the 8 leagues had candidates + stats but 0 scored entries.
   (b) ★ ROLLUP to score them. EXACT: `cd /home/azureuser/trading_corp && PYTHONPATH=. venv/bin/python
       trading_corp/scripts/pm_cli.py rollup` (verified `rollup --help` exit 0). Recomputes pm_category_stats +
       pm_category_onesided_stats + pm_score_snapshot from pm_closed_position -- WRITES ONLY those 3 leaderboard
       tables, READS pm_closed_position. ★ CANNOT reach the order path (no pm_subdivision/attachment/arm/order/
       journal writes -- grep-verified in stats.py) -> the 8 armed subs are unreachable. Local recompute, NO
       network, seconds-to-~2min. DB is WAL (busy_timeout 5000ms) -> readers never block; writers serialize (a
       collision = a <=5s wait, never corruption). SAFE WINDOW: off the :00/:30 (*/30 paper-poll) marks + outside
       the 05:00-05:50 nightly block (refresh/adjudicate/paper-rollup). Verify after: the 8 leagues appear in
       pm_score_snapshot with plausible counts (>= min_resolved 50), epl/ucl intact, live division untouched.
   (c) PIN whales per league; (d) ATTACH per league (farm Promote); (e) ARM per category (respecting the arm-gate).
   Plus: pm_web restart AFTER the sweep (surfaces the 8 farm tiles); an engine restart bundled with a future arm
   restart (running poller categorizes per-league; low urgency, no live effect since nothing soccer-armed).
   ★ SMALL RUNG FILED (fix the trap): add `stats.compute_scores(conn, now_ts=_now())` after the `stats.rollup`
   call in `_cmd_search` (pm_cli.py, ~line 77) -- one line, matching backfill/rollup -- so a sweep is
   self-complete (candidates + stats + scores) and SWEEP->ROLLUP collapses to one step. No good reason they are
   separate; it is an omission, not a design.

**Task (original):** research-only consolidated plan for the 10 remaining Kalshi-copyable categories
(cs2, epl, fed, golf, nba, nfl, nhl, soccer, ucl, wnba). Viability + whale supply are SETTLED
(not re-asked). Output = ONE consolidated document + this handoff, then HALT for Jack's ruling on
build order. Build NOTHING. HALT for deploy/restart/live-DB-write/arm/cap/prod-advance.
**Branch:** `pm-remaining-categories-plan-2026-09-06` (worktree `cc-pm-remaining-cats-wt`, base
`pm-cfb-category-2026-09-06` 159c765). Consolidated doc = `REMAINING_CATEGORIES_PLAN_2026-09-06.md`.

## STATUS (updating as I go)
- [x] Orient: SW11, PM_REQUIREMENTS, the 3 matcher shapes (execution.py dispatch, mlb/ufc/tennis matchers), ctx-builder boilerplate (live_driver.py), sports_team_mapping.
- [x] Kalshi series inventory pulled (LOCAL IP, off-box — 0 cost to the engine poller): 3653 Sports + 793 Economics series -> `cc/_kalshi_series_raw.json`. Per-league copyable series identified + sample markets probed.
- [x] Polymarket slug/outcome/title samples for all 10 (read-only box DB query, `cc/pm_remaincat_poly_ro.*`). Liveness GREEN (8 RUNNING, any_alarm=False) at 2026-09-06 21:00Z.
- [x] 3 sub-agents returned (soccer / golf / cs2+fed) — findings integrated below.
- [x] Consolidated document written: `REMAINING_CATEGORIES_PLAN_2026-09-06.md` (v1 committed d711bb0).
- [x] Jack RULED (moneyline-1st, soccer incl tier-2 + draw, golf last, sub-divs w/ caps but NO arm rows,
      wants cap arithmetic) + asked fed/cs2 research + added cfb as 11th (Kalshi DOES carry it).
- [x] POST-RULING RESOLUTIONS written into the plan doc (bottom section) + memory corrected re cfb.
- [~] RUNG 1 IN PROGRESS (Jack ruled BUILD it; family = FIVE incl cfb; moneyline-only; sub-divs DISARMED
      no arm rows; build up to but NOT through the deploy line; cycle order RULED volume-first). Building on
      this worktree (cc-pm-remaining-cats-wt). Caps: Jack raises at arm — do NOT propose/gate.

## ★ RUNG 1 BUILD — design + sub-rung status
DESIGN (lowest-risk, satisfies "prove mlb byte-identical"): mlb keeps its OWN module untouched (live
moneyline+total+spread path byte-identical by construction, like ufc stayed separate when atp/wta got
tennis). A NEW shared `data/sports_structural_match.py` carries nfl/nba/nhl/wnba/cfb (MONEYLINE-ONLY per
ruling), parameterized by (poly_prefix, game_series, team_map), DH-aware-but-inert. ACCEPTANCE TEST (B2
shape): assert generic(mlb-config) == frozen mlb_poly_kalshi_match.match_poly_to_kalshi over real mlb data.
Sub-rungs:
- [x] A. `data/sports_structural_match.py` (moneyline-only, config-driven, DH-aware-inert) + tests.
      ★ ACCEPTANCE GREEN (local p2venv, -p no:pytest_ethereum): test_mlb_equivalence asserts
      generic(mlb-config) == frozen mlb.match_bet field-for-field over a real-shaped battery (both sides,
      DH-ambiguous, side-unresolved, out-of-window, no-contract, unrecognized, total/spread/prop skips).
      46 tests green incl the existing test_b2_dispatch (mlb byte-identical) + test_mlb_match_r2 + test_category
      -> NO regression to the live mlb path. Collisions stay SAFE MISS (neither-team->side_unresolved no ticker;
      unmapped->fail; TIE ticker never indexed).
- [x] B. WNBA_TEAMS added to sports_team_mapping.py (both-venue code aliases GSV/GS, POR/PDX, etc.);
      wnba registered in LEAGUES; cross-venue-alias test green. (nfl/nba/nhl maps already existed.)
      ★ local offline venv = C:\Users\AA Incorporado\p2venv (has the SAME broken pytest_ethereum plugin ->
      ALWAYS run local pytest with -p no:pytest_ethereum too, not just the box).
- [x] C. cfb team map DONE (the hard part). Built from REAL two-venue codes (297 live Kalshi KXNCAAFGAME
      yes_sub_titles via cc/_kalshi_ncaaf_codes.json + 240 Poly cfb slug codes via cc/pm_cfb_polycodes_ro.*),
      joined by a school-identity key -> `trading_corp/data/cfb_teams.py` CFB_TEAMS = 269 codes / 151 schools.
      Builder cc/build_cfb_map.py (regen each season). cfb registered in LEAGUES. test_cfb_match.py GREEN (6).
      ★ COLLISION PROOF (the acceptance test) PASSES: every Jack-named pair -> DISTINCT school -- Miami (MIA)
      vs Miami OH (MIAOH/MOH); Ole Miss (MISS)/Miss St (MSST)/Miss Valley (MVSU)/Southern Miss (USM); Ohio
      (OSU)/Oklahoma (OKST)/Oregon (ORST) St; Michigan (MSU)/Missouri (MSRST) St; Kansas St (KSU)/Kansas
      (KU)/Kentucky (UK); Washington/Washington St; Colorado/Colorado St; San Diego (SDSU)/San Jose
      (SJSU)/South Dakota (SDKST). Verified EVERY scary shared-initials code on Kalshi (OSU=Ohio St,
      MSU=Michigan St, USC=USC, MIA=Miami FL, MISS=Ole Miss -- all same school both venues).
      ★ NAMED UNRESOLVABLE / SAFE MISSES (not silently dropped):
        - SDST = genuine cross-venue collision (Poly=San Diego St, Kalshi=South Dakota St) -> DROPPED; San
          Diego St stays reachable via `sdsu`; a bet spelled `sdst` -> fail (safe), proven in test.
        - KSU/WSU/CSU = Kalshi reuses these for a non-FBS school (Kentucky St/Winona/Central St OH) -> mapped
          to the FBS meaning (=Poly); the non-FBS meaning is a safe miss (no Poly bet + both-team join key
          can't wrong-match; proven in test_ambiguous_kalshi_code_cannot_wrong_match).
        - a NEW/unseen code next season = safe miss until added (regen from live codes).
- [x] D. `fetch_structural_market_context(client,now_ts,cfg)` (moneyline-only, one game series; mirrors the tennis
      builder) + `_structural_ctx_builder` closures registered in CATEGORY_CTX_BUILDERS for nfl/nba/nhl/wnba/cfb.
      execution.py: `_structural_adapter(cfg)` registered in MATCHER_ADAPTERS for the 5; new `MarketContext
      .structural_index` field (DEFAULTED None -> mlb/ufc/tennis constructions BYTE-IDENTICAL). ctx builder reads
      cfg.game_series from SS.LEAGUES (no new SERIES constants needed). Adapters + registry tested locally
      (test_structural_wiring.py); test_b2_dispatch still green -> mlb dispatch unchanged. The ctx builder itself
      needs pykalshi -> its real-fetch test is in sub-rung F (box-scratch).
- [x] E. `category_volume_order(conn, account_id, cats, now_ts, window_days=30)` -> PROVEN-VOLUME-FIRST (MEASURED,
      not static) replacing alphabetical; wired into scheduled_pm_live_loop ONCE at task start (read-only ro conn,
      fail-safe alphabetical). ★ POLICY WRITTEN in the docstring: measured per-account committed-$ over 30d, desc,
      alpha tiebreak, NEW category ranks last; STALENESS = lags real-time by window + restart cadence (deliberate:
      'proven' is slow to change); ★ the STARVATION of late/quiet categories when the cap binds is the ACCEPTED
      TRADE, not a side effect. Tested (proven-first-new-last, noise excluded, fail-safe). mlb/ufc/atp/wta cycle
      order now changes from alphabetical to volume-first (Jack ruled) -- verified order-independent for
      boot-reconcile (cats[0]) + dict inits; only the `for c in cats` claim loop is reordered.
- [x] F1. BOX-SCRATCH GREEN on the BOX VENV (cc/pm_rung1_scratch.* + cc/_rung1_overlay.b64, tar overlay onto a
      scratch copy; live tree/engine UNTOUCHED). 42 tests pass incl test_mlb_equivalence + test_b2_dispatch +
      test_mlb_match_r2 -> ★ mlb BYTE-IDENTICAL confirmed on the box venv; box-venv import OK (5 adapters + 5 ctx
      builders, structural_index default None, live 4 intact). (b64 delivery needed the BOM strip tr -d '\r\357\273\277'.)
- [x] F2. DISARMED DRY-RUN vs REAL markets (harness cc/build_dryrun.py; Poly bets from box read-only sqlite = 0
      Kalshi load, Kalshi tickers fetched off-box local IP). ★ GATE PASSES BOTH: wrong_game=0, wrong_market_type=0.
      - nfl (3427 bets vs 162 KXNFLGAME): matched 192, in-window 62% (preseason coverage), skip_non_moneyline 1632,
        out_of_window 1484 (old games), no_kalshi_contract 119 (preseason games Kalshi didn't list), fail 0. The
        dry-run DROVE 3 safe cross-venue team-map fixes: NFL_TEAMS += LA->Rams, WSH->Commanders, LAS->Raiders
        (Poly spellings; Kalshi uses LAR/WAS/LV). Regression: 26 tests still green after.
      - cfb (2635 bets vs 898 KXNCAAFGAME, IN SEASON): matched 77, in-window 99%, out_of_window 1488, skip 1054,
        fail 15 = ALL San Diego State spelled `sdst` = the deliberately-dropped SDST collision (safe miss; SD St via
        `sdsu` matches). Confirms the cross-venue aliasing (emich/EMU, ncar/UNC, jaxst/JVST, ndkst/NDSU, hawaii/HAW)
        AND the collision drop on REAL data -> never a wrong pick.
      - nba/nhl/wnba: same shared matcher + maps (nba/nhl exist, wnba built); a dry-run is a pre-arm verification
        item WHEN IN-SEASON (nba/nhl open Oct; wnba ending) -- currently mostly out_of_window. NOT a blocker.
- [x] G. STAGED + HELD at the deploy line (both are HALT items -- NOT run). Box-truth confirmed the 3 modified
      box files == my base (execution 09647842 / live_driver 732fcaab / sports_team_mapping ba23801d; the 2 new
      absent). Two runners authored + validated (ASCII, parse OK):
      - cc/pm_rung1_deploy.* : delivers the 5-file overlay (_rung1_overlay.b64) + grafts ONLY the 5 code files onto
        the LIVE tree -- drift-check base, backup, extract, SHA-VERIFY each == committed (execution 8894c6d4 /
        live_driver 784c04d9 / sports_team_mapping a2a08dab / sports_structural_match 7a6f08bf / cfb_teams 5f3ebd25),
        restore-on-mismatch, additive-diff, box-venv import-check. NO restart in the runner.
      - cc/pm_rung1_create.* : creates 10 sub-divisions (kalshi_jack+karen x cfb/nba/nfl/nhl/wnba) DISARMED,
        market_types='moneyline', standard (default) caps, NO arm rows, NO attachments (dormant); self-verifies
        arm-count + attachment-count UNCHANGED + 10 created. LIVE DB WRITE.
      ★ SEQUENCE for Jack (all HALT, his auth): (1) pm_rung1_deploy (graft 5 files) -> (2) restart_tc.ps1 (engine
      restart to load new live_driver/execution + the volume-first cycle order; bounces EVERY division, warn
      co-tenants MACE/bitunix/PEAD/coinbase) -> (3) pm_rung1_create (10 disarmed subs). THEN later, his: attach
      whales (farm Promote) + arm (CLI) + raise caps. New categories stay DORMANT until attached+armed.

## ★★ ARM-GATE (Jack's ruling 2026-09-07): nba/nhl/wnba MUST get a real-market dry-run BEFORE arming.
nfl and cfb EACH surfaced real bugs ONLY against live data (nfl: LA/WSH/LAS team-code gaps; cfb: confirmed the
SDST collision-drop + aliasing). nba/nhl/wnba maps are proven by TEST but were out-of-season at build, so NOT yet
proven against real bets. **This is a GATE on arming, not a nice-to-have:** before arming ANY of nba/nhl/wnba,
run the disarmed dry-run (cc/build_dryrun.py: box read-only Poly bets for the category + off-box KXNBAGAME/
KXNHLGAME/KXWNBAGAME fetch + local match) and confirm the GATE (wrong_game=0, wrong_market_type=0) + classify
misses + FIX any team-code gaps it surfaces (as nfl's LA/WSH/LAS were fixed). nba/nhl open Oct, wnba ending.
Whoever is here in October: do NOT skip this.

## ★ RUNG-1 CODE DEPLOYED 2026-09-07 00:17Z (cc/pm_rung1_deploy.*): 5-file graft, drift-check PASS (box==base
09647842/732fcaab/ba23801d), backup ~/pm_rung1_deploy_backup_20260907T001732Z, SHA-VERIFY all 5 == committed
(8894c6d4/784c04d9/a2a08dab/7a6f08bf/5f3ebd25), additive (exec +23/-0, live_driver +89/-0, team_mapping +24/-1
[the -1 = the modified NFL line the LA alias appended to]), box-venv import OK (5 adapters + 5 ctx builders,
structural_index default None). NO restart (engine 219962 UNCHANGED by the graft). ★ FIRST RUN ABORTED
fail-closed (sha-verify caught a STALE overlay: it predated the F2 LA/WSH/LAS aliases -> sports_team_mapping
mismatch e5e0cf0a vs committed a2a08dab); box auto-restored; rebuilt overlay from current committed files;
re-ran clean. The sha-verify discipline working. NEXT (Jack): restart_tc.ps1 (load new code + volume-first
order; bounces ALL divisions, warn co-tenants) -> then run cc/pm_rung1_postcheck_ro.* (roster still 4/account
= new cats invisible; 9 arm rows unchanged; liveness 8 RUNNING; boot-reconcile clean; volume-first order took
!= alphabetical) -> then pm_rung1_create.* (separate auth).

## ★ POST-DEPLOY POST-CHECK GREEN 2026-09-07 00:41Z (cc/pm_rung1_postcheck_ro.*, boot-aware, after Jack's restart):
engine PID 219962->222109 (NRestarts=0, since 00:40:04). [1] roster only atp/mlb/ufc/wta both accounts, 0 new
sub-divs -> NEW CATEGORIES INVISIBLE; [2] 9 arm rows all armed latched=0, timestamps UNCHANGED from persisted;
[3] liveness all 8 RUNNING any_alarm=False; [4] boot-reconcile both reconciled=True latched=False; [5] ★
VOLUME-FIRST ORDER TOOK: engine LOGGED cycle order = ['mlb','atp','wta','ufc'] BOTH accounts (recompute matches;
changed=True vs alphabetical ['atp','mlb','ufc','wta']) -> mlb (highest 30d volume) first, ufc (quiet) last.
The 5 registry entries + reordered claim loop changed NOTHING for the live four except the intended ordering.

## ★ SUB-DIVISIONS CREATED 2026-09-07 00:47Z (cc/pm_rung1_create.*): 10 disarmed subs (jack+karen x cfb/nba/nfl/
nhl/wnba), market_types='moneyline', standard default caps, NO arm rows, NO attachments. pm_subdivision 8->18.
POST-VERIFY GREEN (cc/pm_rung1_createverify_ro.*): the 10 new all active/moneyline/unattached; ★ the ORIGINAL 8
BYTE-UNCHANGED (all carry OLD created_ts, none == this run's 1788742063 -> INSERT OR IGNORE re-inserted/modified
nothing; 8-row content sha 198f6135 recorded); arm still 9 armed latched=0 ts-unchanged; attachments 17->17;
liveness 8 RUNNING; engine 222109 unchanged. ★ ROSTER UNCHANGED: driver_roster.active_driver_subdivisions
returns ONLY the 8 attached subs (0 of the 10 new -> unattached -> dormant -> NO task). The 10 new are INERT
until Jack: attach whale (farm Promote) + engine restart (re-read roster) + arm.

## ★ THE SHA-VERIFY NEAR-MISS (record it -- a guardrail earning its cost, not a formality): the FIRST deploy run
ABORTED fail-closed because the overlay was STALE (built in F1, before F2's LA/WSH/LAS team-map fixes). Had the
runner trusted the overlay, it would have shipped the matcher WITHOUT the three fixes the dry-run found -- SILENTLY,
surfacing only as unexplained NFL misses in October. The sha-verify (placed==committed) caught it, restored the
box, refused to proceed; overlay rebuilt from current committed files, re-ran clean. LESSON: a deploy overlay
built before later edits is stale -- the byte-level sha-verify-vs-committed is what makes a multi-file graft safe.

## ★ RUNG 1 LANDED (A-G): code deployed + engine restarted + 10 disarmed subs created, all post-checks GREEN.
NEXT (Jack's): attach whales (farm Promote) + arm (CLI) + raise caps, per category. ARM-GATE STANDS: nba/nhl/wnba
each get a real-market dry-run (cc/build_dryrun.py) BEFORE arming. nfl + cfb are dry-run-proven now; when a whale
is attached to one of the 10 and the engine restarts, that (account,cat) enters the roster and trades once armed.
Branch pm-remaining-categories-plan-2026-09-06 (commits ...0e81eea + handoff). 42 tests green on the box venv
(mlb byte-identical) + 26 local. Disarmed dry-run gate PASSES (nfl + cfb: 0 wrong game, 0 wrong market-type).

## ★★ RUNG 2 (cs2) — LANDED LIVE 2026-09-07 ~03:25Z (deployed + engine restarted + 2 disarmed subs created; all post-checks GREEN)
- **DEPLOY 03:10Z** (cc/pm_cs2_deploy.*): drift-check box==rung-1 base (exec 8894c6d4 / live_driver 784c04d9),
  backup ~/pm_cs2_deploy_backup_20260907T031048Z, SHA-VERIFY placed==COMMITTED (exec 4ad71e1a / live_driver
  91d218a3 / cs2 e0063823), additive (exec +19/-0, live_driver +30/-0), box-venv import OK. NO restart in runner.
- **RESTART** by Jack (engine 222109->224045, NRestarts=0, since 03:13:41Z). **POST-CHECK GREEN** (cc/pm_cs2_postcheck_ro.*,
  boot-aware): cs2 adapter+ctx loaded (series KXCS2GAME, alias_n=6, cs2_index default None); ★ 4 live matchers
  byte-identical (additive-only diff + cs2_index None + 8 live tasks RUNNING); 9 arm rows 9-armed 0-latched; boot-
  reconcile both clean; liveness 8 RUNNING; ★ engine-logged cycle order = ['mlb','atp','wta','ufc'] BOTH accounts
  (unchanged); MACE back (config_hash c382c9370f9b, aux loops online); 0 tracebacks. cs2 INVISIBLE (0 subs).
  ★ QUERY-ARTIFACT NAMED: the recompute over `active=1` showed 9 cats because the 10 dormant rung-1 subs carry
  active=1 -> use the ROSTER (driver_roster.active_driver_subdivisions), NOT active=1 (see [[active-flag-not-a-proxy-for-trades]]).
- **CREATE 03:25Z** (cc/pm_cs2_create.*): 2 cs2 subs (jack+karen) DISARMED, moneyline, NO arm rows, NO attachments.
  pm_subdivision 18->20. **POST-VERIFY GREEN** (cc/pm_cs2_createverify_ro.*): original-8 content sha == rung-1
  baseline 198f61354e17187f (UNCHANGED); rung1-10 intact; arm 9/9/0 (no cs2 arm key); attachments 17->17 (cs2=0);
  liveness 8 RUNNING; roster 8 entries (cs2 NOT in roster -> dormant); engine 224045 unchanged. full 20-row
  content sha = 7f6a83f98423d31e (record for the next family's create diff). cs2 INERT until attach+arm+restart.
- **REMAINING (Jack's):** attach whale (farm Promote) + arm (CLI) + set caps. cs2 is already dry-run-proven on
  real data (unlike nba/nhl/wnba, which still owe a real-market dry-run before arming per the ARM-GATE).

## ★★ RUNG 2 (cs2) — build detail (was: BUILT + BOX-SCRATCHED GREEN + STAGED) 2026-09-07 ~01:30Z
**Commit 1f5768f (pushed, local==origin). Branch pm-remaining-categories-plan-2026-09-06.** Reuses the tennis
PAIR-KEY construct but with EXACT-normalized org matching, NEVER fuzzy (its own module, tennis untouched/byte-
identical). Report detail = `CS2_MATCH_2026-09-07.md`; runners cc/pm_cs2_*.
- **Matcher `trading_corp/data/cs2_poly_kalshi_match.py`** (NEW, committed CR-stripped sha e0063823e2dba1e4):
  pair-key on the Poly title "A vs B" + KXCS2GAME two-side markets (both YES tickers share a (date,blob)),
  joined by EXACT normalize (accent-fold + lowercase + strip-punct + collapse-ws; reuses ufc `_norm` +
  `kalshi_to_iso_date`) + a 6-entry data-verified alias table + a ±1 day window (AP matches straddle UTC
  midnight). ★ EXACT never fuzzy = cs2's Cerundolo case: `ENCE`≠`ENCE Academy`, `FURIA`≠`FURIA fe`,
  `MOUZ`≠`MOUZ NXT`, `G2`≠`G2 Ares`, `ex-<Org>`≠`<Org>` (distinct rosters). MONEYLINE only: an outcome that
  is not one of the two title sides (map handicap / spread / total) is classified non_moneyline and NEVER placed.
- **Wired** (execution.py `MATCHER_ADAPTERS["cs2"]`, sha 4ad71e1a5b7c95e2; live_driver.py `CATEGORY_CTX_BUILDERS
  ["cs2"]=fetch_cs2_market_context` + `CS2_SERIES="KXCS2GAME"`, sha 91d218a315fe4f88). New `MarketContext.cs2_index`
  field DEFAULTED None -> mlb/ufc/tennis/structural constructions BYTE-IDENTICAL (proven in test + box-scratch).
- **ALIAS TABLE = 6, small + mostly stable** (Jack's "how small / how volatile"): 322/514 real moneyline orgs
  join with ZERO aliases (both venues publish full display names). The 6 are same-team UNIFICATIONS built from
  the REAL two-venue data (not typed): `b8 esports→b8`, `betboom team→betboom`, `themongolz→the mongolz`,
  `liquid→team liquid`, `sinners→sinners esports`, `kaleido gaming→kaleido`. Each key is an EXACT full string,
  so an academy/junior variant is NEVER touched (verified no collision). **DEFERRED as accepted safe-misses**
  (the volatile/fragile class): `BET-M 33→33` (sponsor prefix — sponsors change) + `Honvéd` (Kalshi stores the
  name with a corrupt byte → normalizes to `honv d`). A miss is acceptable; a wrong pick is not.
- **DRY-RUN GATE PASSED (the gate) — 4182 real Poly moneyline bets vs the real KXCS2GAME index** (Poly bets from
  box read-only sqlite = 0 Kalshi load; Kalshi fetched off-box local IP; cc/cs2_dryrun.py):
  ★ **wrong_team=0, wrong_market_type=0** on 1725 real matches. **In-window match = 95.8% (1725/1800)** — the
  honest live number (Kalshi exposes only 2026-06-30..09-09; older Poly bets are out_of_window fetch artifacts,
  irrelevant to near-real-time copying). In-window misses all classified SAFE: 53 one-org-absent (small orgs
  Kalshi doesn't list + the 2 deferred aliases), 19 rematch-in-±1d (ambiguous → safe miss), 3 both-absent.
  ★ NOTE: the dry-run's wrong_team check CANNOT validate an alias (a bad alias corrupts both sides consistently);
  each alias was verified as an unambiguous same-team rename against the data (2 forms, no academy collision) —
  NOT inferred from a shared opponent (that gives false positives, e.g. "100 Thieves opp=OG").
- **TESTS**: test_cs2_match.py (Cerundolo parametrized across 8 parent/variant pairs both directions + the
  strongest case: both parent AND variant listed → each routes to its OWN ticker; map/handicap/total gating;
  ±1d window; rematch-ambiguous; alias unifies but never bleeds into academy) + test_cs2_wiring.py (registry +
  cs2_index byte-identity + dispatch + fail-safe). 24 local green.
- **BOX-SCRATCH GREEN** (cc/pm_cs2_scratch.* + cc/_cs2_overlay.b64; box venv; live tree/engine UNTOUCHED): import
  OK, cs2 adapter+ctx present, cs2_index default None, all 10 live categories intact, alias_n=6; **133 tests**
  incl mlb/ufc/tennis/structural byte-identity regressions; engine 222109 + pm_web 218797 UNTOUCHED; scratch cleaned.
- **STAGED + HELD at the deploy line** (all HALT items — NOT run). ★ READ-ONLY PRE-CHECK GREEN (cc/pm_cs2_precheck_ro.*):
  box execution.py==8894c6d4 + live_driver.py==784c04d9 (rung-1 base, no drift), cs2 module absent, 0 cs2 subs,
  engine 222109 — so the deploy will NOT drift-abort. Runners:
  - cc/pm_cs2_deploy.* : delivers the overlay (_cs2_overlay.b64) + grafts ONLY the 3 code files (1 new + 2
    modified) — drift-check box==rung-1 base, backup, extract, **SHA-VERIFY each == COMMITTED artifact**
    (execution 4ad71e1a / live_driver 91d218a3 / cs2 e0063823), restore-on-mismatch, additive-diff, box-venv
    import-check. NO restart in the runner.
  - cc/pm_cs2_create.* : creates 2 sub-divisions (kalshi_jack+karen × cs2) DISARMED, market_types='moneyline',
    default caps, NO arm rows, NO attachments (dormant). LIVE DB WRITE. Self-proves: 2 created, arm+attachment
    counts unchanged, ORIGINAL-8 content sha unchanged (== rung-1 baseline 198f6135), full post-create content sha.
  - cc/pm_cs2_postcheck_ro.* (after Jack's restart): cs2 code loaded + cs2 invisible pre-create; 9 arm rows; 8
    RUNNING; boot-reconcile clean; volume-first order unchanged (cs2 not attached). Boot-aware (waits for reconcile).
  - cc/pm_cs2_createverify_ro.* (after create): 2 cs2 disarmed/unattached; original-8 == 198f6135; rung1-10
    present; arm 9; attachments unchanged; 8 RUNNING; engine PID unchanged; roster excludes cs2 (dormant).
  ★ SEQUENCE for Jack (all HALT, his auth): (1) pm_cs2_deploy (graft 3 files) → (2) engine restart (load cs2
  matcher+ctx; bounces EVERY division, warn co-tenants MACE/bitunix/PEAD/coinbase) → pm_cs2_postcheck_ro →
  (3) pm_cs2_create (2 disarmed subs) → pm_cs2_createverify_ro. THEN later, his: attach whale (farm Promote) +
  arm (CLI) + set caps. cs2 stays DORMANT until attached+armed+restart. **ARM-GATE note:** cs2 is already
  dry-run-proven on real data (unlike nba/nhl/wnba which still owe a real-market dry-run before arming).
- **NEXT FAMILY = RUNG 3 (soccer: epl+ucl+tier-2 as one variant build; draw→-TIE; 90-min settlement; order
  leagues by whale volume; any skipped league = a LISTED deferral). Then fed, then golf.** Not started (cs2
  is staged, awaiting the deploy authorization).

## ★★ RUNG 3 (soccer) — LANDED LIVE 2026-09-07 ~04:38Z (deployed + engine restarted + 20 disarmed subs created; all post-checks GREEN)
- **DEPLOY 04:27Z** (cc/pm_soccer_deploy.*): drift-check box==rung-2 base, backup ~/pm_soccer_deploy_backup_20260907T042734Z,
  SHA-VERIFY placed==COMMITTED (exec 0fff5e7a / live_driver 34fcc8fe / soccer_match 78cd53e2 / soccer_teams 86f6daf7),
  additive (exec +22/-0, live_driver +38/-0), box import OK. NO restart in runner.
- **RESTART** by Jack (engine 224045->225544). **POST-CHECK GREEN** (cc/pm_soccer_postcheck_ro.*): 10 soccer adapters+ctx
  loaded, soccer_index default None (5 live matchers byte-identical); soccer INVISIBLE (0 subs, 0 in roster); arm 9/9/0;
  boot-reconcile clean; ★ liveness now 10 RUNNING (nfl entered roster: jack+karen × atp/mlb/nfl/ufc/wta) any_alarm=False;
  ★ volume order = ['mlb','atp','wta','nfl','ufc'] both accts (nfl 4th not last: zero-volume tiebreak is alphabetical,
  nfl<ufc; nfl disarmed places nothing); MACE back (config_hash c382c9370f9b), 0 tracebacks.
  ★ RECONCILED (Jack's arithmetic, I flagged): arm rows are 9 NOT 10 (attach != arm; nfl attached w/ no arm row).
- **CREATE 04:38Z** (cc/pm_soccer_create.*): 20 subs (10 leagues × jack+karen) DISARMED, moneyline, NO arm/attach.
  pm_subdivision 20->40. **POST-VERIFY GREEN** (cc/pm_soccer_createverify_ro.* + a 12-row supplementary check):
  ORIGINAL-8 sha == baseline 198f61354e17187f (UNCHANGED); ★ the 12 created-since (10 rung1 ts=1788742063 + 2 cs2
  ts=1788751501) all OLD created_ts, 12-row sha ae40fa801f929345; EXACTLY 20 rows carry this run's ts=1788755887 ->
  the 20->40 write touched ONLY the 20 soccer rows, the 20 pre-existing byte-untouched. arm 9/9/0 (no soccer arm keys);
  attachments 19->19 (the 19 = 17 + nfl×2); soccer NOT in roster (dormant); full 40-row sha 3834e44867d7929b.
- ★ **ufc VOLUME-FIRST SELF-REINFORCING (Jack named it):** ufc is last because it has NEVER FIRED, and volume-first
  keeps a zero-volume category last indefinitely UNLESS it trades -> self-reinforcing. Fine while nothing binds; if
  ufc ever seems mysteriously quiet once account caps ($150/50-orders) start BINDING, this is why (starvation of the
  tail is the accepted trade). Same applies to any never-fired category.
- **REMAINING (Jack's):** attach whale + arm + set caps, per league in volume order. ARM-GATE: epl/ucl/lal/fl1/uel/mls/
  sea/bun/bra dry-run-proven; ★ mex needs a real-market dry-run before arming (snapshot gap; like nba/nhl/wnba).

## ★★ RUNG 3 (soccer) — build detail (was: BUILT + BOX-SCRATCHED GREEN + STAGED) 2026-09-07 ~04:05Z
**Commit 001bff0 (pushed). 10 leagues by whale volume; the tail is a LISTED DEFERRAL.** Report =
`SOCCER_MATCH_2026-09-07.md`; runners cc/pm_soccer_*.
- **Matcher `trading_corp/data/soccer_poly_kalshi_match.py`** (NEW, committed sha 78cd53e24a70701b) +
  `soccer_teams.py` (NEW, 86f6daf7b5e1bd65, 328 alias entries). 3-WAY: Poly per-team Yes/No (`Will {T} win?`
  → Kalshi `{T} wins` yes/no leg; No=draw-or-lose=NO leg) + draw (`Will {A} vs {B} end in a draw?` → Kalshi
  TIE yes/no leg). Kalshi KX{LG}GAME = 3 markets/game ({A}/{B}/Tie) sharing (date,blob). ★ 90-MIN both venues:
  UCL/UEL knockouts use Kalshi `Reg Time: {Club}` (stripped + treated uniformly); each game listed once
  (plain OR reg-time) so no dual-market to disambiguate → knockout divergence DISSOLVES. Club join = EXACT
  base-normalize + per-league alias table, HARD collision-checked (no 2 different clubs → 1 target).
- ★ **NAMED COLLISIONS kept distinct:** Ligue 1 PSG/Paris/Paris FC, MLS Los Angeles F(LAFC)/G(Galaxy), UCL
  Inter Milan/IC Escaldes. ★★ THE PSG NEAR-MISS: the auto-generator prefix-mapped `Paris Saint-Germain FC`→
  Kalshi `Paris` (WRONG); NEITHER the dry-run wrong-team check NOR the collision-check caught it (the
  alias-caveat — a bad alias corrupts both sides consistently, [[gate-cannot-validate-its-own-transform]]).
  Caught by DOMAIN verification (real opponent+date: PSG plays Rennes/Lille/Monaco; Paris FC plays Nice/Lyon)
  → fixed to PSG. Lesson reinforced: audit alias transforms independently, not via the gate.
- **Wiring:** MATCHER_ADAPTERS[cat] + CATEGORY_CTX_BUILDERS[cat]=fetch_soccer_market_context (per-league) +
  new MarketContext.soccer_index DEFAULTED None → all prior constructions BYTE-IDENTICAL.
- **DRY-RUN GATE (the gate) — ~9500 real moneyline bets vs real KX{LG}GAME (cc/soccer_dryrun.py):** ★ 0 wrong
  team, 0 wrong leg, 0 wrong market-type on 3013 matches; **93.3% in-window (3013/3228)**. Per-league: lal/sea/
  bun/bra 100%, mls 99.9%, epl 93.9%, fl1 79.4%, ucl 76.9%, uel 68.1% (UCL/UEL lower = obscure qualifier
  minnows unaliased = safe misses). ★ mex = 0 in-window (Poly dates don't overlap the Kalshi snapshot) →
  BUILT but NOT validated → **mex ARM-GATE: real-market dry-run before arming** (like nba/nhl/wnba).
- **LISTED DEFERRALS (never a silent miss; all series EXIST on Kalshi — buildable later, same matcher):**
  col/UECL ($1.3M, 299-club breadth), elc/EFL-Champ ($1.3M), efl/Carabao ($1.7M/106 bets), efa/FA-Cup ($1.7M,
  ★ Kalshi 0 markets in window — can't validate now), nor/tur/arg/spl/sud/por/ere/lib/dfb/chi/es2/kor (each
  <$2.2M, below the volume line, each needs its own alias table; sud/lib/arg have SA same-name clubs → careful
  per-league scoping), uef/Nations-League ($0.75M, ★ NATIONAL TEAMS not clubs → country map, own sub-type).
- **BOX-SCRATCH GREEN** (cc/pm_soccer_scratch.* + _soccer_overlay.b64; box venv): import OK, 10 adapters+ctx,
  soccer_index default None, all prior categories intact, 328 aliases; **149 tests** incl mlb/ufc/tennis/cs2/
  structural byte-identity; engine 224045 + pm_web 218797 UNTOUCHED.
- **STAGED + HELD** (all HALT). ★ RO PRE-CHECK GREEN (cc/pm_soccer_precheck_ro.*): box==rung-2 base (exec
  4ad71e1a/live_driver 91d218a3), soccer modules absent, 0 soccer subs → no drift-abort. Runners:
  - cc/pm_soccer_deploy.* : 4-file graft, SHA-VERIFY placed==COMMITTED (exec 0fff5e7a / live_driver 34fcc8fe /
    soccer_match 78cd53e2 / soccer_teams 86f6daf7), restore-on-mismatch, NO restart.
  - cc/pm_soccer_create.* : 20 disarmed subs (10 leagues × jack+karen), NO arm/attach; proves original-8 sha
    198f61354e17187f unchanged. LIVE DB WRITE.
  - cc/pm_soccer_postcheck_ro.* (after restart): soccer loaded + invisible pre-create; arm 9; ★ liveness now
    expects 10 RUNNING (nfl attached → enters roster) — 0 alarm is the gate; MACE back; volume order logged.
  - cc/pm_soccer_createverify_ro.* (after create): 20 soccer disarmed/unattached; original-8 unchanged;
    soccer NOT in roster (dormant); arm 9.
  ★ SEQUENCE (all HALT, Jack auth): pm_soccer_deploy → engine restart (warn co-tenants) → pm_soccer_postcheck_ro
  → pm_soccer_create → pm_soccer_createverify_ro. Then per-league attach+arm+caps in volume order.
- ★ **nfl NOW ATTACHED** (Jack attached whale 0x226bf122…cad6 to jack-nfl + karen-nfl 2026-09-07): at the NEXT
  restart nfl enters the roster (liveness 8→10, volume order includes nfl); nfl stays DISARMED (no placement)
  until armed. nfl is dry-run-proven → arm-eligible. This is the new "expected" baseline for post-checks.
- **NEXT: fed, then golf** (the last two categories). fed = KXFEDDECISION event+bucket (cut/no-change 1:1, HIKE
  = wrong-bucket gate); golf = KXPGATOUR/KXLIVTOUR field/futures (messiest, do last).

## ★★ RUNG 4 (fed) — LANDED LIVE 2026-09-07 ~05:24Z (deployed + engine restarted + 2 disarmed subs created; post-checks GREEN)
- **DEPLOY 05:01Z** (cc/pm_fed_deploy.*): drift-check box==rung-3 base, backup ~/pm_fed_deploy_backup_20260907T050148Z,
  SHA-VERIFY placed==COMMITTED (exec b33a8d8c / live_driver ed9ddd1f / fed_match 3c30720b), additive (exec +20, live_driver +28), import OK. NO restart in runner.
- **RESTART** by Jack (engine 225544->226641). **POST-CHECK GREEN** (cc/pm_fed_postcheck_ro.*): fed adapter+ctx loaded,
  fed_index default None (6 live matchers byte-identical); fed INVISIBLE (0 subs, 0 in roster); arm 9/9/0; boot-reconcile
  clean; volume order ['mlb','atp','wta','nfl','ufc']; MACE back (config_hash c382c9370f9b), 0 tracebacks.
- **CREATE 05:23Z** (cc/pm_fed_create.*): 2 fed subs (jack+karen) DISARMED, moneyline, NO arm/attach. pm_subdivision
  41->43 (base 41 = 40 + the orphan 'soccer' sub, see below). **POST-VERIFY GREEN** (cc/pm_fed_createverify_ro.* +
  supplementary): ORIGINAL-8 sha == 198f61354e17187f UNCHANGED; EXACTLY 2 rows carry this run's ts=1788758610 (jack/fed,
  karen/fed); the 33 created-since (10 rung1 + 2 cs2 + 20 soccer + 1 orphan) all OLD created_ts (untouched); arm 9/9/0
  (no fed arm key); fed NOT in roster; full 43-row sha 8508325113394ebb, created-since sha 4150da4d705243b2.
- **REMAINING (Jack's):** attach whale + arm + caps. fed dry-run-proven (100% in-window, 0 wrong bucket) -> arm-eligible.

## ★★ SOCCER MIS-ATTACH REVERSED + THE ORPHAN 'soccer' SUB-DIVISION 2026-09-07 ~05:23Z
Jack attached whale 0x7ad71d79a3bb90d0a87a06500fa0fe11663842aa to a coarse **category='soccer'** sub-division on
kalshi_jack (created ~05:07Z by the farm promote-to-live flow, market_types='moneyline,total,spread'). ★ There is NO
'soccer' matcher/ctx-builder (the driver copies PER-LEAGUE epl/lal/.../uel), so it read CATEGORY_STARVED ("task alive,
category not evaluating, no catalog") -- SAFE (disarmed + no builder -> nothing placed, any_alarm=False). Jack RULED
KEEP PER-LEAGUE (whales specialise by league; an umbrella hides that). REVERSED via farm_actions.detach_from_live
(cc/pm_soccer_detach.*, active=0 + removed_ts; reversible). ★ VERIFIED the CATEGORY_STARVED reading CLEARED WITHOUT A
RESTART (read_liveness iterates the attachment-gated roster -> detach removes soccer from `expected` -> no row):
liveness back to 10 RUNNING, any_alarm=False. ★ THE ORPHAN: pm_subdivision rows are PERMANENT by design, so the
kalshi_jack/'soccer' row SURVIVES with 0 active attachments + no matcher -- it is an ORPHAN from a mis-targeted attach,
NOT a category meant to work. Do not read it as a real category.

## ★★ FARM ↔ PER-LEAGUE SOCCER GAP (scoped READ-ONLY 2026-09-07; report=FARM_SOCCER_SCOPING_2026-09-07.md; DO NOT BUILD)
The mis-attach exposed a 4-lane mismatch. ★ EMPIRICALLY ESTABLISHED (not assumed): only **epl** (2829 rows/$43.0M) and
**ucl** (1777/$37.4M) are classified PER-LEAGUE in the DB (slug_prefix); the other 8 built leagues (lal/fl1/uel/mls/sea/
bun/bra/mex) + the whole tail = **23,977 rows / $132.9M lumped under coarse 'soccer'** (gamma_tags). So the per-league
WHALE RECORDS for those 8 DO NOT EXIST YET -- Jack cannot see a La Liga whale to decide to attach them. Cause:
category.py SLUG_PREFIX_MAP maps only epl/ucl; the rest fall to the tier-2 'soccer' gamma tag. search.py
CATEGORY_ALLOWLIST = {epl, ucl, soccer, ...} (not the 8). The DRIVER (rung 3) is the ONLY per-league lane. TO EXPOSE
the 10 in the farm (4 steps, scoped not built): (1) add the 8 to SLUG_PREFIX_MAP [LOW]; (2) re-classify the 24k coarse
rows per-league [MEDIUM, DETERMINISTIC from slug prefix, its own auth + box-scratch]; (3) add to CATEGORY_ALLOWLIST +
discovery sweep [LOW code, records need the sweep]; (4) farm UI tiles/promote per-league [UI]. ★ Steps 1-3 MUST precede
4 (a farm tile with no leaderboard behind it is useless). ★ epl+ucl are ALREADY end-to-end per-league (attachable
today if the farm renders their tiles). ★ Maintenance is LOW (slug-prefix keyed, stable per league, not season-stale
like golf); one-time cost = the 24k re-classify + the sweep. HELD for Jack's ruling on whether/when to do Steps 1-3.

## ★★ PER-LEAGUE SOCCER CLASSIFICATION — BUILT + BOX-SCRATCHED GREEN + STAGED, HELD AT DEPLOY 2026-09-07 ~05:58Z
**Jack RULED per-league (keep the 20 subs, DO NOT build the umbrella) + the CHEAP path (NO 24k re-classify, no
bulk write).** Steps 1-3 built (classifier + allowlist + retire); farm UI (Step 4) NOT built (Jack's, post-sweep).
Commit on branch; deploy shas category.py 0c8d2820599dade6 / search.py b01306742f2d2d06. Runners cc/pm_perleague_*.
- **category.py:** SLUG_PREFIX_MAP += lal/fl1/uel/mls/sea/bun/bra/mex (each->itself; per-league via slug prefix).
  TAG_SLUG_TO_CATEGORY -= 'soccer' (RETIRED). epl/ucl unchanged.
- **search.py:** CATEGORY_ALLOWLIST += the 8, -= 'soccer' (16->23).
- ★ **ANSWER to Jack's Q-A (the 24k coarse-'soccer' rows' fate, ESTABLISHED not assumed):** backfill_wallet
  RE-DERIVES category from the slug + INSERT OR REPLACE (ingest.py:193/271), so the 24k rows RECLASSIFY LAZILY
  per-wallet on the NEXT backfill (Jack's sweep) -> self-heal, deterministic, no bulk write by us. Wallets never
  re-swept sit as dead 'soccer' history -- harmless: after retirement NOTHING live queries category='soccer'
  (search drops it; adjudicate/rollup are category-agnostic).
- ★ **ANSWER to Jack's Q-B (what 'retire' touches, VERIFIED):** exactly the 2 lines above. Nothing else keys off
  the 'soccer' category string. The two OTHER 'soccer' refs are SEPARATE subsystems, UNTOUCHED: brokers/
  polymarket.py `_SPORTS_SERIES` (the arbitrage broker's sport keyword set) + data/kalshi_matchable.py (a legacy
  title-keyword taxonomy, MATCHABLE_CATEGORIES={'mlb'}). ★ epl/ucl CANNOT break: separate SLUG_PREFIX_MAP +
  allowlist entries; nothing groups them under 'soccer' (grep-verified).
- ★ **STEP 4 (farm UI) is essentially FREE + AUTO:** farm.py `league_categories()`/`is_league_category` derive
  the tile/page set FROM `search.CATEGORY_ALLOWLIST`. So the 8 leagues' tiles+pages appear (EMPTY until the sweep)
  and the 'soccer' tile disappears on the next PM_WEB RESTART -- there is NO separate tile code to build. So
  "Step 4" = a pm_web restart, deferred until AFTER Jack's Search sweep (so a leaderboard sits behind each tile).
- **BOX-SCRATCH GREEN** (cc/pm_perleague_scratch.* + _perleague_overlay.b64; box venv): per-league classify True,
  soccer retired True, allowlist 23, farm tiles 10-present/soccer-absent, epl/ucl unchanged; test_category/farm/
  search + new test_soccer_per_league_class all pass (only test_schema_head_pin 20==19 fails = PRE-EXISTING
  driver-liveness migration-020 stale pin, db.py untouched here). ★ box-scratch harness note: the box has NO
  tests/ dir, so the overlay must ship the gamma_events FIXTURES for the tier-2 category tests (added).
- **STAGED + HELD** (deploy is a HALT). RO PRE-CHECK GREEN (cc/pm_perleague_precheck_ro.*): box==base
  (category a294d229 / search 2e45f343; allowlist 16 w/ soccer). Runner cc/pm_perleague_deploy.* (2-file graft,
  drift-check, SHA-VERIFY placed==committed 0c8d2820/b01306742f, NO restart). ★ ACTIVATION: the Search SWEEP (fresh
  subprocess) classifies per-league IMMEDIATELY on the on-disk code; the running poller picks it up on its next
  restart (not urgent, no soccer armed); farm tiles on the next pm_web restart (post-sweep).
- **THE MAINTENANCE CONTRAST (Jack's argument for doing this):** keys on the STABLE Poly slug prefix already in the
  data -> adding a league = one map line + one allowlist line, nothing goes stale each season, a new league is a
  SAFE MISS until added. The OPPOSITE of golf's season lookup table (that honesty carries to the golf scope next).

## ★★ RUNG 4 (fed) — build detail (was: BUILT + BOX-SCRATCHED GREEN + STAGED) 2026-09-07 ~04:56Z
**The ODD one: event+bucket, no teams/players/date. Commit 3c30720b-family (pushed).** Report =
`FED_MATCH_2026-09-07.md`; runners cc/pm_fed_*.
- **Matcher `trading_corp/data/fed_poly_kalshi_match.py`** (NEW, committed sha 3c30720b80365986). Join =
  FOMC MEETING + rate-change BUCKET. Kalshi KXFEDDECISION-{YYMON}-{H0|H25|H26|C25|C26} (5 buckets/meeting,
  from yes_sub_title). Transform = SEMANTIC parse of the Poly title (direction + bps magnitude + '+'/'>'):
  no-change/0bps->H0, hike/cut 25->H25/C25, hike/cut 50/50+/75+ (>25)->H26/C26. Yes/No -> yes/no leg.
- ★ **COARSE GATE (a wrong bucket is a STOP):** "25+ bps" (spans =25 and >25), no-magnitude, count-markets
  ("cut 3 times in 2026"), multi-meeting parlays -> NEVER placed. Key subtlety: because buckets are =25 and
  >25, a 50/50+/75+ phrasing maps CLEANLY to >25; only "25+" (or no magnitude) is genuinely coarse.
  Political/chair/nominate/dissent EXCLUDED. CPI deferred.
- ★ **THE ALIAS LESSON WITH NO TEAMS (Jack):** the phrasing->bucket map is a transform, so (a) parse reads
  the EXPLICIT bps+direction (no inference); (b) coarse gated aggressively; (c) build_bucket_index validates
  each Kalshi bucket-CODE against its OWN yes_sub_title (independent evidence) -> a code/label mismatch is
  SKIPPED, not trusted. [[gate-cannot-validate-its-own-transform]] applies.
- **Wiring:** MATCHER_ADAPTERS["fed"] + CATEGORY_CTX_BUILDERS["fed"]=fetch_fed_market_context + new
  MarketContext.fed_index DEFAULTED None -> all prior constructions BYTE-IDENTICAL.
- **DRY-RUN GATE (367 real fed rows vs real KXFEDDECISION; cc/fed_dryrun.py):** ★ 0 wrong bucket, 0 wrong
  leg, 0 wrong market-type on 35 matches; **100% in-window (35/35)**. ★ the INDEPENDENT-EVIDENCE bucket
  check (re-derive direction+magnitude from BOTH the Poly title AND the Kalshi yes_sub, compare, bypassing
  my code) = 0 mismatch. Classification: 181 bucket, political 92, coarse 61, non_fed 25, unparseable 8.
  Misses: 118 out_of_window (historical meetings), 28 no_meeting (title lacks a year -> safe skip).
- **BOX-SCRATCH GREEN** (cc/pm_fed_scratch.* + _fed_overlay.b64; box venv): import OK, fed adapter+ctx,
  fed_index default None, all 20 prior categories intact, 21 adapters; **166 tests** incl mlb/ufc/tennis/
  cs2/structural/soccer byte-identity; engine 225544 + pm_web 218797 UNTOUCHED.
- **STAGED + HELD** (all HALT). ★ RO PRE-CHECK GREEN (cc/pm_fed_precheck_ro.*): box==rung-3 base (exec
  0fff5e7a/live_driver 34fcc8fe), fed module absent, 0 fed subs -> no drift-abort. Runners:
  - cc/pm_fed_deploy.* : 3-file graft, SHA-VERIFY placed==COMMITTED (exec b33a8d8c / live_driver ed9ddd1f /
    fed_match 3c30720b), restore-on-mismatch, NO restart.
  - cc/pm_fed_create.* : 2 disarmed subs (jack+karen × fed), NO arm/attach; proves original-8 sha
    198f61354e17187f unchanged. LIVE DB WRITE (pm_subdivision 40->42).
  - cc/pm_fed_postcheck_ro.* (after restart): fed loaded + invisible; arm 9; liveness 10 RUNNING (nfl
    disarmed); MACE back. cc/pm_fed_createverify_ro.* (after create): 2 fed disarmed/unattached, pre-existing
    untouched, arm 9, fed not in roster.
  ★ SEQUENCE (HALT, Jack auth): pm_fed_deploy -> restart -> pm_fed_postcheck_ro -> pm_fed_create ->
  pm_fed_createverify_ro. fed is dry-run-proven (100% in-window, 0 wrong bucket) -> arm-eligible.
- **NEXT: golf (LAST, as ruled)** -- KXPGATOUR/KXLIVTOUR field/futures, season lookup table + diacritic name
  normalization (Åberg/Muñoz/Højgaard). The messiest; the final category.

## ★★ GOLF (the LAST category) — ON HOLD, NOT DROPPED. Scoped read-only (report GOLF_SCOPING_2026-09-07.md).
★ JACK RULED (2026-09-07): golf is ON HOLD, buildable when he says -- NOT dropped. His direction is that EVERY
Kalshi-copyable category goes live, and golf qualifies; ROI is NOT his criterion (my recommend-against was on ROI,
which he overrode). The value of the scoping was DISSOLVING the premise, not answering it:
- ★★ THE HEADLINE FINDING (preserve this so nobody reintroduces a code table): a hardcoded tournament->event-CODE
  SEASON TABLE is NOT NECESSARY. The tournament NAME is in BOTH venues' titles, so golf is NAME-MATCHABLE from the
  LIVE Kalshi feed -> NO hardcoded codes, NO annual rot, NO silent decay. This removed the only thing that would
  have made golf droppable. DO NOT build a season code table when golf is built.
- Kalshi KXPGATOUR (1387)+KXLIVTOUR (179): `KX{TOUR}-{EVENTCODE}{YY}-{GOLFER}`, opaque codes (TOC26/IND26), yes_sub
  = golfer full name, title "Will {G} win the {Tournament}?". Poly: 738 rows -> 535 WIN-copyable ($2.26M); rest =
  top-N/H2H/FRL/props (out of scope). 36 win tournaments / 139 golfers; golfer name-match 124/139 (accent-fold).
- **BUILD SCOPE for later (the familiar costs, all cs2/soccer-shaped -- NOT a season table):** (1) accent-fold on
  golfer names (124/139 already clean); (2) a TOURNAMENT-ALIAS table for sponsor renames = the cs2 alias shape,
  data-derived + collision-checked, NOT a season code table; (3) an explicit ESPORTS EXCLUSION for "Masters"/
  "tournament" contaminants (Valorant/StarCraft "masters" share the word); (4) FIELD/FUTURES settlement to
  understand (~150 golfers/event, futures open for weeks -- a separate validation from the game dry-runs);
  (5) a LOUD unmapped-tournament guard (never a silent miss). Same shape at the end: box-scratch, stage, hold.

## ★★ THIN-LEAGUE EVIDENCE QUALITY (2026-09-07, post-sweep+rollup) — a thin roster is NOT equivalent to epl's
After the sweep + rollup, all 10 soccer leagues are PINNABLE (every league got watchlist candidates -- the
thin-sample floor fires). BUT the evidence THINS sharply per league -- the honest cost of splitting one deep
coarse-'soccer' bucket into 8 shallow per-league ones. The funnel (cands=pinnable / scored=ranked / grounded):
  epl 33/32/2 ; ucl 31/21/2 ; lal 10/6/0 ; bun 10/3/0 ; fl1 10/3/0 ; mls 8/7/0 ; sea 8/2/0 ; mex 7/3/0 ;
  bra 5/3/0 ; uel 2/1/0.
- ★ SCORED << CANDS on the new leagues: the thin-sample FLOOR gets whales ONTO the list, but scoring's own
  min_resolved=10 keeps most OFF the ranking (a thin league = ~10 names of which ~3 have enough history to score).
- ★ GROUNDED = 0 for ALL 8 new leagues (only epl/ucl have 2 each): NO /activity loss-grounding pass has run for
  the new per-league categories, so every new-league candidate's loss figure is an UNMEASURED loss-omission
  UPPER BOUND (onesided is_upper_bound), not a measured-and-fine one.
- ★★ THE COMBINATION TO NAME: the ~32 PINNABLE-BUT-UNSCORED candidates across the 8 new leagues are EACH BOTH
  unscored (thin evidence) AND unknown-loss (unmeasured bias) -- pinning them is pinning on thin evidence with an
  unmeasured loss-omission bias on top. The farm DOES carry the caveats (farm.py displays n_resolved per candidate
  + reuses the scoreboard's caveat columns incl onesided_is_upper_bound -- "the farm page and the scoreboard can
  never diverge on a caveat"; NO n_resolved gate), so a thin candidate is never RENDERED as equal to a deep one --
  but the reader must heed them. epl/ucl are the deep, better-grounded exception; the other 8 are thin by nature.
- FOLLOW-UPS to strengthen the new leagues (like rollup did for scoring): an /activity loss-grounding pass for the
  new-league candidates (pm_analyze / the grounding step) would replace UNKNOWN with grounded figures; more sweep
  rounds add candidates + deepen histories. Neither is required to pin -- they raise the evidence quality.

## ★ POST-RULING RESOLUTIONS (2026-09-06) — see the plan doc's bottom section for full detail
- **cfb = 11th, STRUCTURAL.** Kalshi carries it (KXNCAAFGAME/SPREAD/TOTAL); the 09-06 non-Kalshi conclusion
  was wrong (premise never probed). Heaviest team map in the batch (272 Poly codes / ~130 FBS, State/Miami/
  Ole Miss collisions). Paper work runs AHEAD, not wasted. Record corrected in memory.
- **fed:** hikes mostly PRECISE (36 vs 13 coarse). Clean 1:1 = no-change->H0, 25-exact->H25/C25, 50+coarse->
  H26/C26. GATE the coarse "25+"/exact-50/parlays/political (66 of 263) -- detectable at match time. Most fed
  markets copyable. 50 rows misclassified in unknown. CPI deferred.
- **cs2:** 420 orgs; small alias table (full names align), EXACT match never fuzzy (Academy/Junior/NXT/ex-
  are distinct = the collision case); reuse tennis matcher + ±1d. Verify Tier-1 names pre-arm.
- **soccer tier-2:** Kalshi covers ~100+ leagues incl the "absent" ones (sub-agent guessed tickers = wrong).
  Limiter = per-league team maps by whale volume; nothing unmatchable for lack of a series.
- **CAP ARITHMETIC:** account caps $150/50-orders bind at ~3-4 concurrent active cats; nfl+mlb already 2;
  arming more = competition not capacity; alphabetical cycle starves late cats. Jack rules before arming nfl.

## ★ AGENT FINDINGS (integrated)
- SOCCER: Poly `-draw` market EXISTS -> Kalshi `-TIE` (clean). BOTH venues settle 90-min regulation (Kalshi rule verbatim "does not include extra time or penalties") -> UCL-knockout divergence DISSOLVES. Two-legged = per-leg markets. 9 Kalshi league series (epl/ucl/lal/sea/fl1/bundes/mls/uel/uecl); tier-2 leagues (Eredivisie/Scottish/Brazil/LigaMX) = coverage gap. Strip "Reg Time:" title prefix (UCL/UECL).
- GOLF: series = KXPGATOUR (majors+PGA, codes MAST26/PGC26/USO26/THOC26...) + KXLIVTOUR (LIV). Event codes NOT derivable -> season lookup table. Golfer names need NFD ASCII normalization (Åberg/Muñoz/Højgaard). MESSY build.
- CS2: reuse tennis title-matcher; match on Kalshi `yes_sub_title` (full org name), NOT ticker blob. Small esports alias table (NaVi/Natus Vincere, 100T/100 Thieves, FaZe/FaZe Clan) — VERIFY Tier-1 names pre-arm. Map markets (Poly `-gameN`) are separate -> skip. Use +/-1 day window.
- FED: 5 buckets (H26/H25/H0/C25/C26). Cut + no-change map clean. ★ HIKE side is a WRONG-BUCKET kill shot (Poly "25+ bps increase" spans Kalshi H25+H26) -> GATE hikes. Meeting code KXFEDDECISION-{YY}{MON}; exclude Fed-political slugs. CPI = separate later workstream.

## ★ THE HEADLINE (hard collapse — 10 categories do NOT need 10 matchers)
- **The matcher dispatch is already category-agnostic after the match** (execution.py:422 MATCHER_ADAPTERS = {cat: (parse, match)}; CATEGORY_CTX_BUILDERS keyed by category). Adding a category = a (parse, match) pair + a ctx builder + registry entries + sub-division rows/caps. The ctx-builder is ~90% boilerplate (fetch series -> _market_quote_dict -> _merge_raw_market_fields -> a category index builder).
- **Three live matcher shapes:** MLB = structural ticker join (moneyline+total+spread, exact strike, team maps); UFC = Kalshi-TITLE join (binary, name canon); TENNIS = pair-key on Poly title "A vs B" +/-1 day (moneyline, 2-way, name canon; atp+wta SHARE it).
- **A generic `parse_sports_ticker` + team maps for MLB/NBA/NHL/NFL/MLS ALREADY EXIST** (sports_team_mapping.py). WNBA + non-MLS soccer clubs are the gaps.
- **Provisional grouping (verifying):**
  - **STRUCTURAL team-to-win (reuse/generalize MLB):** nba, nfl, nhl, wnba — Poly `{lg}-{away}-{home}-{date}[-suffix]`, Kalshi `KX{X}GAME/TOTAL/SPREAD`, both confirmed. Team maps exist except wnba. Moneyline CONFIRMED; total/spread series exist but were unpopulated at probe time (pre-season) -> strike-encoding to confirm in-season (NOT absence).
  - **SOCCER (epl, ucl, soccer):** ALSO structural team-to-win — Poly `{lg}-{a}-{h}-{date}-{teamcode}` outcome Yes/No -> Kalshi `KX{league}GAME` "{team} wins" yes/no leg. Draw is implicit (draw=NO both sides). Cost = league breadth (soccer = fl1/sea/lal/uel/... many leagues, each a team map) + ★ UCL-knockout settlement (ET/penalties vs 90-min) = the disproving case.
  - **cs2:** title/name join (ufc/tennis-shape); org-name canon; per-match (BO3) winner only (half the Poly volume is tournament futures).
  - **golf:** NEW field/futures shape — "Will {golfer} win {tournament}?" -> Kalshi KXGOLFTOURN/KXMASTERS/KXPGA/... (fragmented). tournament-map + golfer-name.
  - **fed:** NEW event+bucket shape — Poly bucket binary -> Kalshi `KXFEDDECISION-{mtg}-{bucket}`. No competitors. Exclude Fed-political noise.
- **Build count (provisional):** 1 MLB generalization (covers nba/nfl/nhl/wnba, maybe soccer) + 1 soccer front-end (if not folded into structural) + cs2 reuse + 1 golf (new) + 1 fed (new). i.e. ~2-3 NEW matchers, the rest config/team-maps. THE US TEAM SPORTS ARE NEARLY FREE.

## KEY FILES
- Matchers: `trading_corp/data/{mlb,ufc,tennis}_poly_kalshi_match.py`; dispatch `prediction_markets/execution.py:376-471`; ctx builders `prediction_markets/live_driver.py:80-241`, registry :599; team maps `trading_corp/data/sports_team_mapping.py`.
- Probes (LOCAL, off-box): `cc/_kalshi_series_raw.json`. Box read-only: `cc/pm_remaincat_poly_ro.*`.

## OPEN QUESTIONS FOR JACK (gathered — see consolidated doc for full list)
1. Market types per new category (moneyline-only vs +total+spread)? (drives MLB-generalize vs tennis-reuse)
2. Soccer: fold into structural team-to-win, or a dedicated matcher? UCL knockout settlement gate.
3. Golf/fed: build now or defer (new shapes, more work, lower whale overlap)?
4. cs2: title-join (tennis-shape) vs structural — org-name canon tolerance.
