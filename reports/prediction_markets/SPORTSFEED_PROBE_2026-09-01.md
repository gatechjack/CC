# Sports Feed Probe — live MLB game state for the sub-division page (FINDINGS + RECOMMENDATION)

**Date:** 2026-09-02 00:28Z. **Mode:** READ-ONLY. Nothing built or deployed. Probes were external public GETs
(ESPN / MLB StatsAPI / Yahoo) — different hosts from the trading engine; zero order-path exposure. JACK-MLB
untouched.
**Runner (cc\ scratch):** `pm_sportsfeed_probe_ro`. **Recon:** `config/data_providers.yaml`,
`TICKET_doubleheader_matcher_ambiguity_2026-08-30.md`, the `kalshi_sports_arb_observer` (the-odds-api).

## ★ RECOMMENDATION (up front)
- **PRIMARY: MLB StatsAPI** (`statsapi.mlb.com`) — MLB's own Gameday backend. Most authoritative + richest +
  fastest, and the ONLY source that carries `doubleHeader` + `gameNumber` **explicitly** (settles the known
  doubleheader ticket for the display join). Reachable from the box: `200`, schedule 19KB/128ms, live feed
  599KB/38ms, no key.
- **FALLBACK: ESPN** (`site.api.espn.com`) — a DIFFERENT vendor (decorrelated failure), also complete, and its
  scoreboard is the single most bandwidth-efficient "all games in one call" summary (score+inning+count+
  runners+lastPlay for every live game in 281KB). Reachable: `200`, 199ms, no key.
- **DEGRADED FLOOR: the-odds-api `/scores`** — we ALREADY pay for this (OddsAPIClient, key present). Score-only,
  no inning/count/runners, but it is a contracted source when both free feeds are down.
- **Yahoo:** reachable (`200`) but the working endpoint returned a thin 5KB payload and the second 404'd — needs
  endpoint discovery before it is a real fallback. Rank it below ESPN.

## (a) Can the box reach ESPN? — YES (tested, not inferred)
`GET site.api.espn.com/.../baseball/mlb/scoreboard?dates=20260901` → **HTTP 200, 281KB, 199ms.** No key, no
cost. The box has open outbound HTTPS to ESPN (as it does to Polymarket/Kalshi/tastytrade). MLB StatsAPI and
Yahoo also returned 200.

## (b) What does it actually return? (real payload, live game NYM @ TB, id 401816759, "Top 7th")
**ESPN scoreboard — ONE call carries the full live-state line for EVERY game:**
```
score: TB 5, NYM 1        status.type.detail: "Top 7th"        start: 2026-09-01T22:40Z
situation: balls=2 strikes=2 outs=2 onFirst=T onSecond=T onThird=T  (+ pitcher, batter)
situation.lastPlay.text: "Pitch 4 : Ball 2"     (pitch-level)
```
ESPN `summary?event=<id>` (754KB) adds `boxscore{teams,players}` (batting/pitching lines) + `plays` (395 rows).

**MLB StatsAPI — `feed/live` (SD @ CIN, pk 824472), richer + display-friendlier last play:**
```
linescore: inning=6 half=Bottom isTop=False outs=3 balls=2 strikes=2   score away=2 home=2
runners: first=F second=F third=F
currentPlay.result.description: "Jose Trevino grounds out, third baseman Manny Machado to first baseman Ty France."
gameData.game: doubleHeader=N gameNumber=1     datetime: 2026-09-01T22:40:00Z     teams: SD @ CIN
```
Everything the ruling asks for — score, inning, half-inning, outs, count, base runners, last-play text — is
present in BOTH. StatsAPI's `currentPlay.result.description` is the better "last play" string for a scoreboard
(completed-play prose); ESPN's `lastPlay` is pitch-level.

## (c) Kalshi ticker -> game join — RELIABLE, and it disambiguates doubleheaders (verified on our held tickers)
Our tickers carry teams+date+**HHMM (ET)**: `KXMLBGAME-26SEP01`**`1845`**`SEABOS-SEA`. Both feeds publish
per-game ISO-UTC start times. The join = (team pair, date, start-time). **Verified against positions we
actually hold right now:**

| held Kalshi ticker | HHMM(ET) | StatsAPI game | gameDate(UTC) | ET | match |
|---|---|---|---|---|---|
| `…1845SEABOS` | 18:45 | SEA @ BOS pk 824716 | 22:45Z | 18:45 | ✓ |
| `…2140PHIAZ` | 21:40 | PHI @ ARI pk 825038 | 01:40Z | 21:40 | ✓ |
| `…1940MILCHC` | 19:40 | MIL @ CHC pk 824633 | 23:40Z | 19:40 | ✓ |
| `…SDCIN` | — | SD @ CIN pk 824472 | 22:40Z | 18:40 | ✓ |

**Doubleheaders:** the backlog ticket is about the *Polymarket-slug→Kalshi* join (slug = date+teams, NO time).
The **display join is Kalshi→feed**, and the Kalshi ticker HAS the HHMM while StatsAPI gives `doubleHeader` +
`gameNumber` + distinct `gameDate` per game. **So the display join disambiguates doubleheaders where the copy
matcher cannot** — match the ticker HHMM to the game's start time (or order the two gameNumbers by start time).
This is strictly easier than the copy-side problem and is not blocked by it.

**How the join fails (name them so the design degrades, not guesses):**
1. **Team-code map** — Kalshi `AZ` vs feed `ARI` (and any other code deltas); `SEABOS` must be split into
   SEA|BOS against a known-codes set (variable length: SD=2, PHI=3). A missed code → no match → show "game
   state unavailable" (SAFE), never the wrong game.
2. **Timezone/DST** — Kalshi HHMM is **ET for all games** (verified: PHI@ARI 21:40 ET = 01:40Z, i.e. ET not
   ballpark-local). ET->UTC must track EDT/EST (−4 now, −5 after Nov). A fixed offset breaks at the DST
   boundary; mitigate by matching primarily on (teams,date) and using time only to pick the doubleheader game.
3. **Postponed/suspended** — both feeds expose status (`abstractGameState`/`detailedState`); the design should
   render those states rather than a frozen score.

## (d) Polling cost @ 1-min refresh (a handful of held games)
- **ESPN scoreboard: ONE call covers ALL games** with full live state (281KB/min, flat regardless of games
  held). Detailed box/plays = `summary` 754KB per game only if the page shows box scores.
- **StatsAPI: schedule 19KB/call** (all games; `?hydrate=linescore` folds inning/score in). Per-held-game
  detail via `feed/live` (599KB) — or the lighter `.../linescore` / `?fields=` filter / `feed/live/diffPatch`
  to shrink it. For N=4 held games: 19KB + ~4×(filtered) per min.
- Either way: **trivial, no key, no quota.** Cheapest full-detail pattern = ESPN scoreboard (1 call/min, all
  games) for the summary line + a per-held-game detail call only where the page needs box/plays.

## (e) Backups — reachability + shape (tested)
| source | box reachability | shape | role |
|---|---|---|---|
| **StatsAPI** | 200, 19KB/599KB, 38-128ms | richest; explicit doubleheader; abbrevs; ISO start | **PRIMARY** |
| **ESPN** | 200, 281KB, 199ms | complete; 1-call-all-games; pitch-level lastPlay | **FALLBACK** (diff vendor) |
| the-odds-api `/scores` | already paid (OddsAPIClient key) | score-only, no inning/count | degraded floor |
| Yahoo | 200 (5KB) on `api-secure…/scoreboard`; other path 404 | thin; needs endpoint work | below ESPN |

Pick StatsAPI primary (authority + richness + doubleheader) with ESPN as the hot fallback because the two are
independent vendors — one going dark does not blank the board. Keep the-odds-api as the contracted last resort.

## (f) What we already pay for — NONE cover live game state
`config/data_providers.yaml`: **tastytrade** (equities/options), **EODHD** + **finnhub** (earnings). Plus
**the-odds-api** via the shelved `kalshi_sports_arb_observer` (sportsbook **lines/odds**, not gamestate; it has
a `/scores` endpoint but only final/live scores, no inning/count/runners). **So no existing paid provider
delivers MLB score/inning/count/runners.** Adding StatsAPI/ESPN is not displacing a capability we already buy.

## ★ Stability risk — flagged plainly
Both ESPN and StatsAPI are **undocumented public endpoints** that can change or close without notice. This is
the SECOND uncontracted dependency (Polymarket data-api is the first). That is not a reason to reject them —
they are free, complete, and StatsAPI backs the official MLB app — but the design must degrade **honestly**:
- **Dual-source** (StatsAPI primary + ESPN fallback) = decorrelated failure.
- **A feed-age band on the scoreboard, exactly like the shard-snapshot age band** — if the last successful poll
  is older than a threshold, render "live data unavailable — last updated Xs ago", never a stale or blank board
  presented as current.
- The join is keyed on our OWN Kalshi ticker (stable); only the game-state ENRICHMENT depends on the external
  feed, so an outage degrades to "no live state" and can never corrupt which game a position sits on.

**Verdict: the data is obtainable, cheap, keyless, and rich; the join is reliable and even solves the
doubleheader case for display. Recommend StatsAPI primary + ESPN fallback, with an age-band honest-degrade.
Do NOT build the integration yet — this unblocks the Claude Design prompt.**
