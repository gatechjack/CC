# Remaining-Categories Plan — LIVE HANDOFF (read this instead of scrollback)

**Task:** research-only consolidated plan for the 10 remaining Kalshi-copyable categories
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
- [ ] G. stage sub-division CREATE runner (DISARMED, no arm rows) + deploy graft runner -> HALT for Jack (deploy line)

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
