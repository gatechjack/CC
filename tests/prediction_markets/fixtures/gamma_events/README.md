# gamma /events fixtures — real tags recorded 2026-08-22 (read-only probe)

Each file is a minimal `/events?slug=<eventSlug>&closed=true` response: a JSON array with one
event carrying its **real captured `tags`** (from the live box probe, `pk_events_probe_ro.ps1`).
`markets[]` and timestamp-noise fields are elided (tier-2 keys on `tags`; kept id/label/slug/
forceHide). Used by `test_category.py` tier-2 tests via an injected `fetch_events` — offline, no network.

| file | eventSlug | category-determining tag | tier-2 -> |
|---|---|---|---|
| mlb.json | mlb-mia-nym-2026-05-29 | mlb (100381) | mlb |
| ufc.json | ufc-kin-ter1-2026-07-11 | ufc (279) | ufc |
| nba_champion_futures.json | 2026-nba-champion | nba (745) | nba (futures — category only; market-type is §13A(d), not P1) |
| fed.json | fed-interest-rates-may-2025 | fed-rates (100196) | fed |
| soccer_lec.json | soccer-lec-rsl-atlante-2026-08-08 | soccer (100350) | soccer (NOT one of the 4 live — correctly non-live) |
