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

## Fixtures
Real `/events` responses recorded under `tests/prediction_markets/fixtures/gamma_events/` (offline tier-2 tests).
