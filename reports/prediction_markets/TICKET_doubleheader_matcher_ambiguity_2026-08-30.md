# TICKET — MLB doubleheader matcher ambiguity (WRONG-GAME COPY risk). Fix BEFORE R8, not in R4.

**Status: FILED 2026-08-30. Not a defect today (nothing places, R7.f gated). A latent WRONG-GAME copy once live.**

## Observed (2026-08-30 R7.f read-only MLB check, `cc\pm_r7f_index_match_ro.*`)
SDTrading held a genuinely-open MLB position on **ARI @ SF, slug `mlb-ari-sf-2026-08-29`**. The matcher returned:
```
status=skip:doubleheader_ambiguous  reason=2_games_same_teams_same_date  ticker=None leg=None
```
The Kalshi index carried **TWO** `KXMLBGAME` entries for ARI@SF on 2026-08-29 (a doubleheader), and the matcher
cannot say which game the Polymarket position refers to. It SAFELY skips rather than guessing.

## Why it is NOT a defect today
Nothing places (R7.f is gated on a clean would-place, still 0). A safe-skip on ambiguity is exactly right for a
read-only check — the matcher refuses to guess. No wrong order can occur while the driver is disarmed.

## Why it is a TICKET (belongs before R8 widens live copying)
- **Correctness risk:** once live, if the matcher ever *guessed* a doubleheader game it would be a WRONG-GAME COPY
  — copying the whale's position onto the wrong game of the pair. MLB doubleheaders are **routine in September**.
- **Coverage gap:** the current safe-skip means we MISS copying a whale's doubleheader positions entirely. Both the
  correctness risk (guess wrong) and the coverage gap (skip) matter once R8 widens the live set.

## What it would take to fix (investigation, then a matcher change)
- **Kalshi DOES distinguish doubleheader games** — its ticker carries the start time as HHMM:
  `KXMLBGAME-26AUG29**2205**SEATOR-...` = 22:05. So the two games of a doubleheader are separable Kalshi-side.
- **Polymarket's SLUG does NOT** — the observed slugs are `mlb-ari-sf-2026-08-29` (moneyline) /
  `mlb-bal-oak-2026-08-29-total-9pt5` (total): **date + teams only, NO time, NO game number.** The slug alone
  cannot disambiguate a doubleheader — which is exactly why the matcher (keyed off the slug) has to skip.
- **The fix requires a Polymarket-side discriminator BEYOND the slug.** Investigate, read-only, whether the
  Polymarket MARKET METADATA carries a game-start-time or game-number: (a) the gamma `/markets?condition_ids=...`
  response may expose `gameStartTime` / `eventStartTime`; (b) a doubleheader may be TWO distinct condition_ids with
  distinguishable event metadata; (c) the `/positions` row's event fields. **If a start-time is available, match it
  to the Kalshi ticker's HHMM** (the matcher already parses HHMM out of the Kalshi ticker). **If NOT, doubleheaders
  stay a documented safe-skip coverage gap** until Polymarket exposes the distinction — never a guess.
- **Scope:** a `mlb_poly_kalshi_match` change + a read-only Polymarket-metadata probe. **Do this BEFORE R8**, NOT
  inside R4 (the prospects screen). No code change now.
