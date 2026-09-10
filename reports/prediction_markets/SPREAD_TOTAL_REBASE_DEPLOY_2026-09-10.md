# Spread/Total rung — REBASED onto the live date-join file — DEPLOY MANIFEST (2026-09-10)

**Branch `pm-sprtot-rebased-2026-09-10` @ `703812e`** (rebase of `pm-spread-total-scope-2026-09-09` @ c99353d onto
the DEPLOYED date-join file). Reserved: DEPLOY/RESTART/PUSH/arm/market_types-DB-write.

## The rebase (the one way this could have gone wrong)
The spread/total `sports_structural_match.py` was built on the PRE-date-fix base (`7a6f08bf`). Grafting as-is would
REVERT the now-live date fix. Rebased: added `_prev_iso` + `_resolve_structural_game` verbatim from the deployed
date-join file (box `572b3f9f`) and routed BOTH moneyline (`match_bet`) AND total/spread (`_resolve_unique_game` ->
`_match_total`/`_match_spread` now take `cfg`) through the shared resolver, so total/spread INHERIT the -1-day
night-game recovery. moneyline block is IDENTICAL to the deployed date-join file; mlb (has_doubleheader) EXACT-only
for all types; market_types gate + exact-strike guard intact.

## Proven (local + box, live markets)
- 48 tests pass (3 new rebase tests). Box-scratch: 0 NEW failures (5 pre-existing).
- **Date-fix SURVIVES:** SNF nfl-dal-nyg-2026-09-14 (open) -> KXNFLGAME-26SEP13DALNYG `_via_prevday`.
- **The whale's ACTUAL open nfl legs (two-halves proof):** `spread-home-6pt5 Seahawks` -> KXNFLSPREAD-26SEP09NESEA-SEA7
  (6.5, yes, recovered from a 09-10 slug); `no-det-...-total-49pt5 Over` -> KXNFLTOTAL-26SEP13NODET-50 (49.5, yes);
  and with `market_types='moneyline'` BOTH -> `skip_market_type_excluded` (INERT).
- **Regression:** wnba 72 / cfb 32 matched, 0 wrong game/team/strike/type.

## DEPLOY = 4-file graft (runner `cc/pm_sprtot_graft.ps1`), NO restart in the runner, NO migration, NO pm_web, NO market_types write
| file | box now (pre-graft base) | mine (graft target) |
|---|---|---|
| trading_corp/data/sports_structural_match.py | **572b3f9f** (the DEPLOYED date-join) | 01fd54f0 |
| trading_corp/prediction_markets/execution.py | b33a8d8c | 09c7e275 |
| trading_corp/prediction_markets/live_driver.py | ed9ddd1f | 430a6f70 |
| trading_corp/prediction_markets/shard_balance.py | be6e8bc9 | 298f7201 |

**DROPPED (Option A):** `trading_corp/data/mlb_poly_kalshi_match.py` (3e52394f -> 20ccc660) — box file is
**root:root**, azureuser cannot overwrite it, and the change is **docstring-only** (mlb match functions are
byte-identical; the correct code is already live). Grafting it would fail the writable pre-check and abort the whole
graft, so it is excluded. **The stale docstring stays on the box until an az-root write fixes it** — filed as a
follow-up (see below). `trading_corp/data/` is MIXED-ownership: `sports_structural_match.py` is azureuser-writable,
`mlb_poly_kalshi_match.py` is root:root — the runner's per-file `writable=` pre-check enforces this, never assumes.

Runner: pre-verify-all (box==base + staged==mine + **writable**, ABORT before touching anything on any mismatch) ->
backup-all -> LF-write-all -> post-verify-all==mine -> box-venv import+smoke (execution+live_driver import; ssm
date-fix _via_prevday + total/spread -1 recovery + INERT skip + **base-mlb** unwidened) -> engine PID unchanged.
RESTORE-ALL + abort on any failure.

## AZ-ROOT FOLLOW-UP (not lost, needs a root write — NOT part of this graft)
`trading_corp/data/mlb_poly_kalshi_match.py` on the box (root:root, 3e52394f) still carries the stale docstring
claiming "no KXMLBSPREAD/KXMLBTOTAL". The code is correct; only the comment lies. A comment that contradicts the code
is exactly how the original spreads-dropped defect survived unnoticed for weeks, so this must be corrected — but via
the az-root deploy channel (azureuser cannot write root-owned data/ files; DO NOT chown — standing no-chown ruling).
Target content = 20ccc660 (docstring corrected, functions unchanged). Owner/timing: Jack via az-root.

## INERT after graft+restart (behaviourally)
nfl/wnba/cfb are live at `market_types='moneyline'` -> total/spread route to `skip_market_type_excluded`; moneyline
byte-identical (+ the date-fix already live). So graft+restart changes NOTHING observable until the ENABLE DB write.

## ENABLE (Jack, SEPARATE live-DB write, after post-check green) -- one row (nfl) or two (nfl+wnba):
`UPDATE pm_subdivision SET market_types='moneyline,total,spread' WHERE account_id='kalshi_jack' AND category='nfl';`
(kalshi_karen/nfl + wnba analogously if chosen). No restart needed (market_types read per cycle).

## POST-CHECK (after Jack's restart): reconcile clean both accts, liveness 30/30, 0 PM tb, MACE back, +the two rung
proofs -- a night game still `_via_prevday`; nfl's open spread+total resolve to exact tickers while REFUSED on
market type (inert). THEN on enable: first spread/total fill -> `cc/pm_fill_watch_ro` reads back STRIKE+TYPE+SPREAD
TEAM+GAME-DATE; wrong strike/type/team/day = fire-first `cc/pm_global_disarm`.

## SEQUENCING: date-join already LIVE (prod-live 0b634d4). This is the SECOND, separate deploy. On deploy, prod-live
folds this 5-file set (sports_structural base = 572b3f9f) as its own FF commit + ledger + FF push (Jack).
