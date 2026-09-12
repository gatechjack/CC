# DEPLOY MANIFEST -- Item A: structural sports milestone fallback (cfb/nfl LIVE) -- 2026-09-12

## ★ CHEAP DEPLOY -- pm_web ONLY, NO ENGINE BOUNCE
This is a pm_web change (`web/live_view.py`). It restarts ONLY `prediction-markets-web.service`. The
engine `trading-corp.service` (PID 370246, 30 armed subs) is NOT touched -- no division bounce, no
arm change, no order-path effect. Materially cheaper than the b1/stagger engine deploy.

## What ships
- **ONE file**: `trading_corp/prediction_markets/web/live_view.py`.
  - base (prod-live 68af1b94 == box) CR-sha256(16) = `ff6c3c03e36b0120`
  - target (this branch)             CR-sha256(16) = `c6901968ae6d5d07`
- **NOT shipped** (repo-only): the tests + this doc.
- NO schema/migration, NO DB write, NO arm change, NO engine file. The milestone sweep (`milestones.py`)
  and poller (`poller.py`) are UNCHANGED -- zero delta to the Kalshi sweep cost / 429-risk.

## The change (see MILESTONE_CFB_GAP_INVESTIGATION_2026-09-12.md)
cfb/nfl/wnba/nba/nhl were LIVE_CAPABLE (ticker-HHMM path) but not milestone-eligible, and their Kalshi
tickers carry no HHMM (0/794 cfb, 0/60 nfl) -> served by neither -> UPCOMING while underway (OKLA@MICH).
Fix: `MILESTONE_START_CATEGORIES` now unions in the STRUCTURAL sports (`LIVE_CAPABLE - _HHMM_AUTHORITATIVE`,
where `_HHMM_AUTHORITATIVE={mlb,cs2}`). `start_ts_for_ticker` stays HHMM-FIRST (a structural game with an
HHMM still uses it; mlb/cs2 never borrow a milestone). Interdependence warning at both lists.

## Pre-deploy proof (read-only box-scratch; deployed code + engine UNTOUCHED)
- py_compile OK; 15 new tests pass (OKLA@MICH + live NFL read LIVE; served cats unchanged; HHMM-first;
  no-milestone -> time-unknown; set membership) + test_milestones membership updated to the corrected
  routing. Full `tests/prediction_markets`: TOTAL_FAILED=21 == deployed baseline, 0 milestone failures ->
  0 new regressions (the 21 are pre-existing pm_web nav-string drift).
- Two adversarial skeptics: no BLOCKER/HIGH. Skeptic-1 (routing) clean; Skeptic-2 (safety) confirms
  pm_web-only (never imported by engine/order path), cannot fabricate a LIVE for a not-underway game
  (category from the DB row, exact event-ticker join, placeholder->None, HHMM-first). One LOW (derived-set
  polarity) closed with a comment; the derivation defaults new sports to milestone-eligible, which is the
  SAFE polarity (a forgotten structural sport still gets milestone; HHMM-first neutralises a mis-added
  HHMM sport).

## DEPLOY SEQUENCE (each box step HALTS for Jack's authorization)
- **STEP 1 -- GRAFT** (`pm_milestone_graft.ps1`): scp the edited live_view.py, drift-gate box==base
  `ff6c3c03`, backup, apply, re-verify post==target `c6901968`, py_compile. Aborts+restores on mismatch.
  Inert until the pm_web restart.
- **STEP 2 -- RESTART pm_web ONLY** (`pm_web_restart_az.ps1` = `az vm run-command ... systemctl restart
  prediction-markets-web`, runs as root -- ssh+sudo has no TTY). Engine NOT touched.
- **STEP 3 -- SMOKE** (`pm_milestone_smoke_ro.ps1`, read-only): new pm_web PID; deployed live_view CR-sha ==
  target; no pm_web tracebacks since restart; engine PID 370246 UNCHANGED (proof the engine was not
  bounced). ★ LIVE-tile proof is the NEXT underway structural game (a cfb/nfl game with a held position +
  a passed milestone start now reads LIVE) -- the routing itself is proven by the 15 unit tests; OKLA@MICH
  has since settled so it can only be re-observed on a fresh live game.
- **STEP 4 -- FOLD**: three-way prove box==prod-live(new)==branch on live_view.py CR-sha; FF
  `git push origin <tip>:prod-live` (linear descendant of 68af1b94); ledger commit. Same session.

## Rollback
- Pre-restart: `cp` the `~/pm_milestone_backup_<ts>_live_view.py` back -> reverts the deployed file (no
  restart needed, pm_web still serving the old in-memory code).
- Post-restart: restore the backup + restart pm_web -> back to `ff6c3c03`.

## Refs
- Branch `pm-milestone-cfb-gap-2026-09-12` @ `7e4a2497` on prod-live `68af1b94`.
- Investigation: `reports/prediction_markets/MILESTONE_CFB_GAP_INVESTIGATION_2026-09-12.md`.
