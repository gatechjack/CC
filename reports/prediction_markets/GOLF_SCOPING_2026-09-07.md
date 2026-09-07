# Golf scoping — honesty-first (report before building) — 2026-09-07

**Jack's rule:** if golf decays every season into SILENT MISSES with a maintenance burden nobody owns,
RECOMMEND AGAINST building it — a category that quietly stops matching is worse than one we never had.
This scopes golf against that bar. **Nothing built.**

## The shape
- **Kalshi** `KXPGATOUR-{EVENT}{YY}-{GOLFER}` (1387 mkts) + `KXLIVTOUR-{EVENT}{YY}-{GOLFER}` (179). yes_sub_title
  = full golfer name; title = "Will {Golfer} win the {Tournament}?". EVENT is an OPAQUE code (`TOC26`=Tour
  Championship, `IND26`=LIV India). The dedicated major series (KXMASTERS/KXUSOPEN/…) are EMPTY — majors ride
  KXPGATOUR event codes. A field/futures market: ~150 golfer-binaries per tournament, open for weeks.
- **Polymarket**: per-golfer Yes/No, title "Will {Golfer} win the {Tournament}?", event_slug = the tournament
  NAME (`2026-us-open-winner`, `the-masters-winner-2026`).

## Quantified (real data)
- 738 Poly golf rows → **535 WIN-market (copyable) bets = $2.26M**. The rest are OUT of scope: 177 top-N /
  H2H / first-round-leader / props, + esports "masters"/"tournament" contaminants, + 15 other.
- 36 distinct WIN tournaments (Poly) / 139 golfers. Kalshi live feed: 14 tournaments (rolling window) / 494 golfers.
- **Golfer name-match: 124/139** (accent-folded) — solid; the 15 are out-of-current-field or minor diffs.
- **Tournament name-match to the LIVE feed: 8/36** — but the 28 "misses" are overwhelmingly OUT-OF-WINDOW
  (past 2026 events Kalshi no longer lists: "masters tournament" 139 bets, "players", "u s"=US Open, "travelers",
  "genesis invitational", …), NOT genuine name mismatches. In-window majors/big-events DO name-match.

## ★ Does it decay into silent misses? — the premise is AVOIDABLE
The research assumed a hardcoded **tournament→Kalshi-event-CODE season table** (TOC26/MAST26/…). THAT would rot
every season (schedule changes, sponsor renames, new/dropped events) and fail silently. **But it is avoidable:**
the tournament NAME is in BOTH venues' titles, so golf can be matched like every other category —
- **golfer** by accent-folded name (stable, no table, no decay);
- **tournament** by name-normalization against the LIVE Kalshi feed (the ctx builder already fetches the current
  KXPGATOUR/KXLIVTOUR events) — so a new tournament matches by name the moment Kalshi lists it, with NO hardcoded
  code table to maintain;
- a Poly bet whose tournament matches no live Kalshi event → a **LOUD "unmapped golf tournament" skip**, never a
  silent miss.
So golf need NOT rot into silent misses. The residual maintenance is a small tournament-name ALIAS set for
sponsor renames (data-derived + collision-checked, like the soccer club map) + esports-contaminant exclusion —
not an opaque-code table.

## The honest weighing (why I still recommend AGAINST, for now)
Even with the decay avoided, golf is the **weakest-ROI category of the program**:
1. **Lowest volume + smallest surface** — $2.26M copyable (win-only), vs epl alone $43M; only 535 win bets.
2. **Messiest normalization** — sponsor-prefixed tournament names ("Memorial presented by Workday", "AT&T Pebble
   Beach Pro-Am", "Cognizant Classic in the Palm Beaches") + a tournament-name alias burden for yearly renames.
3. **Contaminant risk** — esports "Masters"/"tournament" events share the word; the matcher must exclude non-golf.
4. **Field/futures dynamics** — ~150 golfers/event, futures open for weeks; different copy/settlement behaviour
   than the game-based categories (a separate validation from the game dry-runs).
5. **No owner for the alias/contaminant upkeep** — smaller than a code table, but still unowned; Jack: "I would
   rather drop golf than carry a maintenance burden nobody owns."

## Recommendation
**RECOMMEND AGAINST building golf now (defer/drop).** Not because it must decay silently — it need not (name-based
+ live-feed + a loud unmapped guard avoids that) — but because it is the lowest-ROI, messiest category, carrying a
tournament-name-alias + contaminant-exclusion burden with no owner, for the smallest copyable surface. The
remaining-categories program is better closed at the categories already built (mlb/ufc/atp/wta + nfl/nba/nhl/wnba/
cfb + cs2 + the 10 soccer leagues + fed), which cover the volume.

**If Jack wants golf anyway**, the safe build is: golfer accent-fold name-match + tournament name-match from the
LIVE Kalshi feed (NO hardcoded code table) + a LOUD unmapped-tournament guard + explicit esports-contaminant
exclusion + an owner for the tournament-name alias refresh. That is buildable — but I would not build it without
Jack's explicit "yes, despite the ROI," and an owner named for the upkeep.
