# ITEM A -- MILESTONE START TIMES DO NOT REACH cfb -- INVESTIGATION (READ-ONLY) 2026-09-12

Branch `pm-milestone-cfb-gap-2026-09-12` off `origin/prod-live` @ `68af1b94` (git truth; box matches).
Symptom: `/live/kalshi_karen/cfb` read "start time unavailable" for OKLA@MICH WHILE UNDERWAY.

## VERDICT -- it is A3 (the pm_web CONSUMER), NOT A1/A2 (the sweep/index). pm_web-only, NO engine restart.
The milestone sweep indexes cfb correctly; the pm_web tile classifier simply never asks the index for
cfb, because cfb is routed to the ticker-HHMM path and CFB tickers carry no HHMM. Both the sweep and
the consumer live in `trading_corp/prediction_markets/web/` (pm_web) -> the fix is a pm_web deploy with
NO engine bounce.

═══════════════════════════════════════════════════════════════════════════════════════════════
## THE THREE CANDIDATE GAPS -- distinguished with file:line + evidence
═══════════════════════════════════════════════════════════════════════════════════════════════

### A1 -- does the deployed sweep INDEX cfb? -> YES (ruled out)
- The sweep is BY CATEGORY, not competition: `CATALOG_CATEGORIES = ("Sports","Esports")`
  (milestones.py:57); the URL filters `category=%s` (milestones.py:51). There is NO competition
  filter in our code, so the football_game/competition hypothesis does not apply -- category=Sports
  is broader and carries both NFL and CFB.
- EVIDENCE (public Kalshi API, replaying the DEPLOYED bounds now-36h -> now+3d, 200/page, 30-page cap,
  ascending-sort early-stop, at the observation instant 2026-09-12T21:00Z):
  `HIT_30PAGE_CAP=False` (whole window = 12 pages / 2400 milestones, well under the 6000 cap);
  **119 distinct CFB games + 14 NFL games reached**. The table-tennis/ITF/MLB-inning-prop flood
  (KXTTELITEMATCH 825, KXITFMATCH 373, KXMLBINNINGWIN 297, ... in the first pages) does NOT starve
  CFB within the tight 36h window. => the sweep DOES index cfb.

### A2 -- is OKLA@MICH in the index, real UTC or T00:00:00Z placeholder? -> IN, REAL (ruled out)
- EVIDENCE: in the deployed-bounds replay, **OKLA@MICH FOUND on page 6**, related_event_tickers
  `KXNCAAF*-26SEP12OKLAMICH`, **start_date = 2026-09-12T16:00:00Z -- a REAL UTC instant** (noon ET
  kickoff), NOT a placeholder. `start_instant` (milestones.py:98) would return a real ts for it.
  (Placeholders are ~0.5%: 13 of 2400 in the wider probe.) => a real start is available for OKLA@MICH.

### A3 -- does the pm_web CONSUMER consult the index for cfb? -> NO (THIS IS THE GAP)
- `LIVE_CAPABLE = {"mlb","cs2","nfl","nba","nhl","wnba","cfb"}` (live_view.py:926) -- cfb is here.
- `MILESTONE_START_CATEGORIES = ({"atp","wta","ufc"} | soccer) - LIVE_CAPABLE` (live_view.py:933) --
  cfb is NOT here (it is subtracted out via LIVE_CAPABLE).
- `start_ts_for_ticker(category, ticker, starts)` (live_view.py:983-995): FIRST `parse_ticker_start`
  (the ticker HHMM); THEN the milestone index ONLY `if category in MILESTONE_START_CATEGORIES`. For
  cfb: HHMM path is tried, milestone path is skipped (cfb not in the set).
- `parse_ticker_start` (live_view.py:960) uses `_START_RE = ^KX[A-Z0-9]+-(\d{2}[A-Z]{3}\d{2})(\d{4})`
  (live_view.py:957) which REQUIRES a 4-digit HHMM immediately after the YYMONDD date; it returns None
  for any LIVE_CAPABLE category whose ticker omits the HHMM.
- EVIDENCE (public Kalshi /markets): **CFB tickers carry NO HHMM -- 0 of 794 KXNCAAFGAME (566 open +
  228 settled), 0 of thousands of KXNCAAFTOTAL/SPREAD.** OKLA@MICH is exactly this:
  `KXNCAAFGAME-26SEP12OKLAMICH-OKLA`, `KXNCAAFTOTAL-26SEP12OKLAMICH-68`,
  `KXNCAAFSPREAD-26SEP12OKLAMICH-OKLA8` -- HHMM absent in every one.
- => For cfb, `parse_ticker_start` returns None (no HHMM) AND the milestone fallback is skipped (cfb
  not in MILESTONE_START_CATEGORIES) -> `start_ts_for_ticker` returns None -> start UNKNOWN -> the tile
  stays UPCOMING / "start time unavailable" even while underway. **cfb is routed to a source it does
  not have, and excluded from the one it does.**

## WHY THE POST-CHECK READ GREEN WHILE cfb WAS UNSERVED (the brief's real point)
The 11/12 held-ticker post-check tested only what was held at that moment -- and everything held was in
MILESTONE_START_CATEGORIES (tennis/ufc/soccer), the categories that DO consult the index. cfb/nfl are
LIVE_CAPABLE, so the milestone post-check never touched them, and their HHMM path was never exercised
for the no-HHMM case. The coverage proof was NARROW (milestone-routed categories only), not wrong.

═══════════════════════════════════════════════════════════════════════════════════════════════
## ARE OTHER ARMED CATEGORIES SILENTLY UNSERVED TOO? -- YES: nfl now; wnba/nba/nhl by construction
═══════════════════════════════════════════════════════════════════════════════════════════════
Routing of every ARMED category (from the WIRED log: jack 17 / karen 15):
- **HHMM path (LIVE_CAPABLE) -- SERVED iff the ticker carries HHMM:**
  - mlb -- SERVED (0 issue: HHMM present 74/74 + a game feed).
  - cs2 -- SERVED (HHMM present 92/92, e.g. `KXCS2GAME-26SEP131130THETIT`).
  - **cfb -- UNSERVED (0/794 HHMM). The reported gap.**
  - **nfl -- UNSERVED RIGHT NOW (0/60 KXNFLGAME HHMM, e.g. `KXNFLGAME-26SEP21NYGLAR-NYG`; in-season).**
  - wnba -- structural, same builder/format; 0 open markets now (off-season) so not currently firing,
    but AFFECTED in-season (its tickers will be date+teams, no HHMM). nba/nhl same (LIVE_CAPABLE,
    structural, off-season, NOT currently armed).
- **Milestone path (MILESTONE_START_CATEGORIES) -- SERVED (consult the index):** atp, wta, ufc, and the
  soccer leagues epl/ucl/lal/fl1/sea/bun/mls/bra/**mex**.
- ★ CORRECTION to the brief's guess: **mex and the soccer leagues are milestone-ROUTED, so they are
  SERVED, not at risk.** The silently-unserved set is the STRUCTURAL LIVE_CAPABLE sports
  (cfb/nfl now; wnba/nba/nhl in-season), NOT soccer/mex. Root cause is the same for all of them:
  LIVE_CAPABLE assumes an HHMM the structural tickers don't carry, and LIVE_CAPABLE excludes the
  milestone fallback.

Why mlb/cs2 differ: MLB carries HHMM (doubleheader disambiguation) + a feed; cs2 carries HHMM. The
STRUCTURAL family does not (the structural parser `_kalshi_re` at sports_structural_match.py:187 makes
the HHMM group OPTIONAL `(?P<time>\d{4})?` -- and empirically Kalshi omits it for cfb/nfl).

═══════════════════════════════════════════════════════════════════════════════════════════════
## HAND-OFF (per the brief: A3 -> the UI workstream, do NOT fix across the boundary)
═══════════════════════════════════════════════════════════════════════════════════════════════
- **Where:** `trading_corp/prediction_markets/web/live_view.py` (pm_web / UI). Function
  `start_ts_for_ticker` (983) + the constants `LIVE_CAPABLE` (926) / `MILESTONE_START_CATEGORIES` (933).
  The sweep (`milestones.py`) and poller (`poller.py`) are CORRECT and need no change.
- **Expected shape of the fix:** the structural sports (cfb/nfl/wnba/nba/nhl) must be able to fall back
  to the milestone index when their ticker has no HHMM. `start_ts_for_ticker` already tries HHMM FIRST
  then milestone -- so the minimal change is to make the structural categories milestone-ELIGIBLE (add
  them to the milestone fallback set) WHILE keeping HHMM-first (so a structural game that DOES carry an
  HHMM still uses it, and cs2/mlb stay HHMM/feed-authoritative). Do NOT simply drop them from
  LIVE_CAPABLE without adding the milestone route -- that would leave them unserved by BOTH.
  Guard preserved: a T00:00:00Z placeholder is already None (milestones.py:98), and the underway test
  stays a pure clock-compare on start_date + our own settlement (details.status remains ignored).
- **Restart:** NONE. pm_web-only (both the milestone module and the consumer are under web/). A pm_web
  restart, not an engine bounce -- materially cheaper than the last three engine deploys.

## Research traps carried (not rediscovered)
- The join is a CATALOG SWEEP on related_event_tickers; `?related_event_ticker=` filter 400s unauth --
  confirmed still the design (milestones.py:16-20).
- Event ticker = market ticker minus final `-suffix`; related_event_tickers is a SUPERSET across market
  types -- so whichever cfb series is held (GAME/TOTAL/SPREAD) resolves once cfb consults the index.
- details.status LIES and is deliberately ignored (milestones.py:26); underway = clock-compare, no status.
- fed absent by construction (no Economics category) -- correct, and fed is not armed anyway.

## Evidence runners (local, public Kalshi API -- no box, no auth; box shares this same source)
- ticker HHMM presence + OKLA@MICH: `/markets?series_ticker=KX{NCAAF,NFL,WNBA,CS2,MLB}GAME` probes.
- deployed-bounds milestone replay (now-36h -> now+3d, 30-page cap): OKLA@MICH on page 6,
  start 2026-09-12T16:00:00Z, 119 CFB games reached, not capped.
