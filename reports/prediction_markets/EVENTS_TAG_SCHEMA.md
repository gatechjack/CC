# Gamma /events TAGS schema — tier-2 category source (probe 2026-08-22, read-only)

Runner: `runners/pk_events_probe_ro.ps1` (+ `pm_events_probe.py`). Confirms Option-1 (events-tag-join). `/markets` has NO tags; `/events` does.

## Schema (raw)
`GET gamma-api.polymarket.com/events?slug=<eventSlug>` → `[event]`; `event["tags"]` is a **list of objects**:
```json
"tags": [
  {"id":"100196","label":"Fed Rates","slug":"fed-rates","forceShow":true, ...},
  {"id":"100328","label":"Economy","slug":"economy", ...},
  {"id":"2","label":"Politics","slug":"politics","forceHide":true, ...},
  {"id":"101800","label":"Economic Policy","slug":"economic-policy", ...}
]
```
Fields per tag: `id` (string), `label`, `slug` (+ forceShow/forceHide/updatedAt/…). Events carry MULTIPLE tags (broad + specific).

Observed:
- **Fed** (`fed-interest-rates-*` / `fed-decision-in-*`): tags `fed-rates(100196)`, economy, politics(forceHide), economic-policy. `seriesSlug=fed-interest-rates`, series ticker `fomc`, `negRisk=true`.
- **NBA champion (futures)** (`2026-nba-champion`): tags `sports(1)`, `nba(745)`, `nba-finals(100240)`, `basketball(28)`. `negRisk=true`.
- **Soccer Leagues Cup (game)** (`soccer-lec` / eventSlug `…-lec-…`): tags `sports(1)`, games, `soccer(100350)`, leagues-cup(`lcs`,102449). Has `teams[]`, `gameId`, `gameStartTime`.

## Tag → category mapping (Task 1d)
Match on tag **slug** (robust; ids as corroboration). CATEGORY_TAG map for the 4 live categories:
| tag slug | tag id | -> category |
|---|---|---|
| `fed-rates` | 100196 | **fed** |
| `nba` | 745 | **nba** |
| `mlb` | (mlb) | **mlb** |
| `ufc` | 279 | **ufc** |
- Scout corroboration: scouts used `tag_id=279` (UFC) and `tag_id=100196` (Fed) on `/events` — same tags, same ids.
- **Ambiguous cases:** an event has several tags (e.g. NBA-champion = Sports + NBA + NBA-Finals + Basketball). Resolve by the FIRST tag whose slug is in the map (specific league beats broad `sports`/`basketball`). Broad tags (`sports`, `basketball`, `economy`, `politics`) are NOT mapped to a live category; `politics` on Fed is `forceHide` (Polymarket de-emphasizes it) — do not map fed->politics. Non-live categories (soccer, golf) resolve to their own slug (`soccer`, `golf`), correctly NOT one of the 4.

## (b) market.events[] embedded tags: NO
Every pick: `market.events[0] has tags: False, tags: null`. Tier-2 MUST query `/events?slug=<eventSlug>` (or by tag id). Not available from the `/markets` events[].

## Fixtures (Task 1d) — recorded paths
Real `/events` responses recorded under `tests/prediction_markets/fixtures/gamma_events/` (drive the
offline tier-2 tests, no network):
- `gamma_events/fed.json` — Fed decision event (tags fed-rates/economy/politics/economic-policy; negRisk)
- `gamma_events/nba_champion_futures.json` — `2026-nba-champion` (tags sports/nba/nba-finals/basketball)
- `gamma_events/mlb.json` — MLB game event
- `gamma_events/ufc.json` — UFC event (tag id 279)
- `gamma_events/soccer_lec.json` — Leagues Cup game (tags sports/soccer; has teams/gameId/gameStartTime)
- `gamma_events/README.md` — provenance notes
Closed-position fixtures (parse->ingest->rollup + §3A tests) under `fixtures/closed_positions/`:
`empty_page.json`, `winner_page.json`, `loser_mix_page.json`, `negrisk_event.json`, `clean_binary.json`.

## Tier-2 live tail resolution (Task 1e) — re-run read-only 2026-08-22 (`pm_tier2_live_driver.py`)
Ran the ACTUAL `category.derive_categories_batch` (live gamma `/events` default fetch) over every
tier-1-UNKNOWN distinct `eventSlug` collected from evanng/pako/d1k21 `/closed-positions`:
- **tier-1 UNKNOWN distinct eventSlugs: 504 -> tier-2 RESOLVED 38 (8%).**
- Everything tier-2 resolved was a **sport the slug-prefix can't catch**, all via `gamma_tags`:
  `2026-nba-champion -> nba`, `2026-fedex-st-jude-championship-winner -> golf`,
  `bun-b04-hsv-2026-05-16 -> soccer`, `deebo-samuel-traded -> nfl`.
- The other **92% correctly stay `unknown`** — treasury-yield / inflation / elections / bitcoin /
  venezuela-iran / CFB games / chess: **none are P1 categories**, so unknown is the RIGHT answer, not a
  coverage miss. The 8% is "of the residual unknown tail, the fraction that was actually a trackable
  sport." (Category coverage vs the §12 >=85% bar is measured over ALL rows, where tier-1 prefixes
  already catch the mlb/ufc/nba/fed bulk; tier-2 mops up this sports tail.)
- **Reinforces §13A(d):** `2026-nba-champion` correctly gets category `nba`, but it is a *futures*
  market — the category layer cannot and should not distinguish futures from single-game; that is the
  market-type dimension (P2/P3), discriminator `sportsMarketType=='moneyline'` + `gameStartTime`.

## Task-1 report index (a-g)
- **(a) raw /events tags schema** -> "Schema (raw)" above (list of {id,label,slug,...}; multiple tags/event).
- **(b) tags embedded in market.events[]?** -> **NO** ("(b) market.events[] embedded tags: NO"); tier-2
  must query `/events?slug=` (or by tag id).
- **(c) tag->category mapping + ambiguous cases** -> "Tag -> category mapping" above (match on tag slug;
  first mapped slug wins; broad sports/basketball/economy/politics NOT mapped; fed politics is forceHide).
- **(d) fixtures recorded + paths** -> "Fixtures (Task 1d)" above (concrete paths, both fixture trees).
- **(e) tier-2 resolved tail** -> "Tier-2 live tail resolution" above (38/504=8%; nba/golf/soccer/nfl
  concrete; 92% correctly unknown).
- **(f) futures-vs-single-game discriminator** -> events tags do NOT distinguish them; discriminator is
  MARKET-level `sportsMarketType=='moneyline'` + `gameStartTime` present (single-game) vs both None
  (futures). `negRisk` is NOT a clean discriminator (soccer Leagues Cup games are negRisk+gameStartTime).
  Logged in P1_PLAN **§13A(d)**.
- **(g) open items logged** -> confirmed both present + substantive in P1_PLAN: **§13A(c)** (P2 gamma-
  enrichment trigger; negRisk/negRiskMarketID confirmed reachable on gamma /markets AND /events) and
  **§13A(d)** (market-type dimension; discriminator found).
