# MLB RUN-IN-FIRST-INNING (RFI) — BUILD + DEPLOY MANIFEST (2026-09-14)

Built to READY-TO-DEPLOY. **HALT at the deploy line** (deploy/restart/arm/live-DB-write are Jack's). 30+ subs armed
throughout; nothing on the live box was written (box-scratch only; live matcher sha unchanged before==after).

## What this is
The #1-ranked copyable-gap family (COPYABLE_GAP_PLAN_2026-09-14): MLB "run in the first inning". Poly slug `-nrfi`,
Kalshi `KXMLBRFI` (one binary market per game). Ships **INERT** behind a new `market_types` token `first_inning_run`
— off until Jack writes the token into a sub-division's `market_types` (a one-row DB write, no restart to read it).
**NO migration** (the `market_types` column already exists; schema stays 23).

## ★ THE POLARITY FINDING — DIRECT, NOT INVERTED (contradicts the stated premise; proven on 1212 real games)
The brief stated the polarity is an INVERSION (Poly `-nrfi` "No Run" -> Kalshi NO). **The data says otherwise.** The
live Poly market TITLE is uniformly **"Will there be a run scored in the first inning?"** (verified across all 1577
`-nrfi` rows) -> Poly YES = a run. Kalshi `KXMLBRFI` yes = "Over 0.5 runs in the 1st" = a run. **Same event, same
side -> the mapping is DIRECT (Poly Yes -> Kalshi yes).** The slug tag `nrfi` is a false friend; the resolution text
is the truth. This is exactly why the leg is derived from the TITLE (not the slug name), gated on the affirmative
framing, and **fails closed** on anything else. Proven end-to-end by the dry-run below (whale-won <=> our-copy-wins
on 1212 settled games, 0 inversions).

## Files (engine deploy — 3 files + 1 test; requires an ENGINE restart)
- `trading_corp/data/mlb_poly_kalshi_match.py` — `_rfi_leg` (title-gated DIRECT leg, fail-closed), `-nrfi` parse
  branch (exact-slug), `parse_kalshi_rfi_ticker` + `build_kalshi_rfi_index` (stem-keyed), `_match_rfi`
  (resolve game via moneyline index -> join KXMLBRFI by shared stem; doubleheader-safe), `match_bet` dispatch +
  `rfi_index` kwarg, `COPYABLE_MARKET_TYPES += 'first_inning_run'`.
- `trading_corp/prediction_markets/execution.py` — `MarketContext.rfi_index` (new optional field, byte-identical
  prior constructions), `_mlb_parse` passes title, `_mlb_match` passes `rfi_index`, blank-`market_types` fallback
  pinned to `(moneyline,total,spread)` so a blank never auto-enables RFI, exit CopySignal carries `title`.
- `trading_corp/prediction_markets/live_driver.py` — `fetch_market_context` also fetches `KXMLBRFI`
  (open+settled) + includes it in the raw-field merge (gates 3/6b) + builds `rfi_index`; `_audit_leg_independent`
  gains an independent RFI branch keyed off the resolution TITLE (separate code from the matcher) -> `REVIEW` on a
  leg/intent disagreement; snapshot/reduction carry `title` for the whale-exit re-parse.
- `tests/prediction_markets/test_rfi_2026_09_14.py` — 22 tests (parse both directions + fail-closed, ticker/index,
  stem-join, inert gate, no-market safe-skip, independent audit incl. inversion, adapter wiring, regression, +
  the two skeptic-fix tests).

## Test evidence (box-scratch, ISOLATED — live tree never written)
- `py_compile` all 3 files OK. Live matcher sha `3e52394f9ad000d9` before == after (isolation proven).
- RFI test file: **22 passed / 0 failed**.
- Full `tests/prediction_markets/` differential: change **21 failed / 296 passed** vs baseline `8f35f254`
  **21 failed / 274 passed** — the 21 FAILED entries are **BYTE-IDENTICAL** (test_accounts_m2 / test_live_r3 /
  test_stage2_* — all pre-existing env-gap, none from these 3 files). **NEW-fail-on-change = EMPTY.** (+22 passed =
  the RFI tests.)
- **Real-market DRY-RUN** (1577 real whale `-nrfi` bets x live Kalshi, local — public API from a local IP):
  **1212 matched**, misses classified (328 out_of_window, 27 fail=fail-closed, 7 no_kalshi_contract, 3
  doubleheader_ambiguous). **ZERO wrong game / ZERO wrong leg / ZERO wrong market type.** Polarity GROUND TRUTH:
  **1212 consistent, 0 inconsistent** (whale-won <=> our-copy-wins via Kalshi settled result). Non-empty -> a real
  PASS, not INCONCLUSIVE. (MLB is in season, so this genuinely passed.)

## Two adversarial skeptics — findings + resolution
- [MEDIUM] blank `market_types` fallback would have included the new token -> **FIXED** (fallback pinned to
  ml/total/spread; RFI enabled only by an explicit token). Latent anyway (column NOT NULL, non-blank default).
- [HIGH] whale-EXIT re-parse dropped the title -> RFI exit fail-closed (missed exit, safe, never wrong-money) ->
  **FIXED** (title carried through snapshot -> reduction -> exit CopySignal; test added). Opposed-close + settlement
  were already correct.
- [LOW] loose affirmative substring: doubly-mitigated (exact `-nrfi` slug gate + singular/plural: "run scored"
  never matches "runs scored") -> left as-is, documented.
- [MEDIUM, pm_web, deferred] `/live` game card omits an RFI kind slot (`live_view.py KINDS`) -> first RFI fill would
  under-render on the card. NOT in this engine diff; RFI is inert. First-fill read-back is covered by the fill-watch
  runner below. Bundle a `live_view.py` RFI slot with the pm_web work before ENABLING.
- No BLOCKER; no wrong-money path; no regression; no migration.

## First-fill read-back guard (RFI's failure mode = correct game, inverted side)
Runner `cc/pm_rfi_fillwatch_ro.{ps1,sh}` (read-only) names market_type=first_inning_run + leg + whale outcome +
title + the leg_audit verdict for every KXMLBRFI order, and prints the explicit polarity ("we hold <leg> == YES/NO
a run scores in the 1st"). Run it after the FIRST RFI fill; a `REVIEW:rfi_leg` verdict = disarm-first via
`pm_cli live-disarm --global`.

## DEPLOY (reserved — Jack) — TWO SEPARATE deploys, do NOT fold them
1. **RFI = ENGINE deploy** (matcher/driver/execution) -> graft the 3 files + engine restart (boot-verify all
   divisions). Branch `pm-rfi-build-2026-09-14` off prod-live `8f35f254`. Ships INERT; enable later per sub via
   `market_types += ',first_inning_run'` (a one-row DB write, Jack's).
2. **UNBOOKED chip relabel = pm_web deploy** (`pm-unbooked-chip-relabel-2026-09-14`) -> pm_web restart only, engine
   untouched. A no-restart-of-the-engine change; kept SEPARATE from the RFI engine restart (do not bundle a pm_web
   change into an engine-restart deploy).

FF (report both in the deploy session, after a three-way box==commit prove): `git push origin
pm-rfi-build-2026-09-14:prod-live` (engine) then, separately, the chip branch. prod-live is git truth at `8f35f254`.
