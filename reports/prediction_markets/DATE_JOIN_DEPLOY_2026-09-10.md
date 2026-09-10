# DATE-JOIN FIX — DEPLOYED LIVE 2026-09-10 (prod-live ledger)

**Deployed artifact:** `trading_corp/data/sports_structural_match.py` — the structural moneyline date-join gains a
`(date-1, teams)` night-game fallback (Polymarket dates by UTC kickoff, Kalshi by US-local; night games differ +1).
Fixes the LIVE moneyline join for nfl/nba/nhl/wnba/cfb (mlb EXACT-only, untouched). Detail: `DATE_JOIN_FIX_2026-09-10.md`.

## Deploy (board-authorized, executed 2026-09-10)
- **Graft:** ONE file, wholesale, zero-drift (pre-graft box CR-sha `7a6f08bf…` == this base; post-graft ==
  `572b3f9f158c7b83035377ca5d038fd71799805d0433b1a848b75e6ddc485335`). No migration, no main.py/app.py, no pm_web,
  no market_types write. Backup `~/pm_datejoin_backup_20260910T120304Z/sports_structural_match.py`.
  Pre-restart box-venv import + smoke GREEN (NE@SEA -> KXNFLGAME-26SEP09NESEA-SEA via `_via_prevday`).
- **Restart (Jack, `restart_tc.ps1`):** engine MainPID 292771 -> **299882**, boot 2026-09-10 12:07:21Z, NRestarts 0.
- **POST-CHECK GREEN:** boot-reconcile reconciled=True BOTH accounts (latched=False); 0 PM tracebacks (0 any-division);
  MACE co-tenant back; 30 armed / 0 latched; **liveness 30/30 RUNNING-or-IDLE** (an initial boot-warmup
  CATEGORY_STARVED cleared by ~t+6min, confirmed stable over 90s); **FIX-IS-LIVE: 3/3 night games recover
  `_via_prevday`** — SNF DAL@NYG (open) -> KXNFLGAME-26SEP13DALNYG, MNF DEN@KC (open) -> 26SEP14, NE@SEA -> 26SEP09.

## Three-way custody (CR-stripped sha256)
box(grafted) == prod-live(this commit) == branch pm-date-join-fix-2026-09-10 == **572b3f9f158c7b83035377ca5d038fd71799805d0433b1a848b75e6ddc485335**.

## Rollback
`cp ~/pm_datejoin_backup_20260910T120304Z/sports_structural_match.py <target>` + restart -> back to base `7a6f08bf`.
prod-live: reset to `6a5ed04`.

## Standing notes
- ★ `_game_key` is `(date, frozenset{names})` -- VENUE-AGNOSTIC on purpose: it is what makes the uniqueness guard
  also catch a home-and-home reversal on consecutive days. Do NOT "simplify" it to an ordered tuple.
- nfl moneyline now MATCHES night games; first `would_place` awaits the whale holding a current-window moneyline bet
  (its current legs are spread/total). The spread/total rung (branch pm-spread-total-scope-2026-09-09 @ c99353d)
  WAITS and, when deployed, rebases its sports_structural_match.py onto THIS file + routes total/spread game
  resolution through `_resolve_structural_game` to inherit the recovery. Do NOT fold the two deploys.
