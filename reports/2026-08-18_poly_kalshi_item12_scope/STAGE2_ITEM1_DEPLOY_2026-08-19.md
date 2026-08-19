# Item 1 DEPLOYED LIVE 2026-08-19 10:50 UTC (ATOMIC two-file). Verified. Both items now live.

`[G-conflict]` fail-closed conflict gate. Atomic deploy of poly_kalshi_executor.py + mlb_poly_kalshi_match.py
(interdependent: the executor calls game_key_and_side). Agent-run under Jack "board authorizes atomic
execution". Time ~06:50 ET Wed — outside MACE window, pre-games.

## DEPLOY (atomic, both-or-neither, abort-safe — no abort triggered)
- Local md5s OK (exec 257f6433 / match 7c191e83); uploaded chunked base64 @file.
- **Drift-gate BOTH PASS:** `CUR_EXEC d1f871f9`, `CUR_MATCH 4b2a5c49` (pre-fix pair).
- Backup pair: `.bak_item1_20260819_104959` (both files, same TS).
- **Install BOTH verified:** `INSTALLED_EXEC 257f6433`, `INSTALLED_MATCH 7c191e83`.
- RESTART 10:49:59 UTC; **WIRED 20s**; **engine 785523 -> 786261**.

## VERIFY AFTER RESTART (RO)
- **Re-armed (CRITICAL PASS):** `auto_execute=True -> dry_run=False`, `halted=False`.
- **Atomic pair landed:** box `poly_kalshi_executor.py = 257f6433`, `mlb_poly_kalshi_match.py = 7c191e83`.
- **Byte-locked 3 unchanged:** kalshi_copy_trader `af336db8` · sports_team_mapping `b715f341` · kalshi_live `bbd851a6`.
- **`[G-conflict]` code path present on box:** 5 gate lines, `_opposite_side_on_game` x2, `skip_conflict` x5,
  `skip_gate_error` x3; matcher `def game_key_and_side` x1.
- **roster invariant** `2 live / 4 paper, disjoint`; **0 tracebacks** since ActiveEnter 10:49:59.
- **Gate behavior:** `skip_conflict = 0`, `skip_gate_error = 0` (lifetime audit rows). **Gate is LIVE but has
  not fired yet** — `skip_conflict` only triggers when two whales actually disagree on a game.
  **Stated plainly: gate live, awaiting first real conflict to confirm end-to-end. Not fabricated.**

## prod-live ADVANCE (git-only)
`fc78fc7 -> 7150404` (clean FF): both deployed blobs + deploy_log entry. Temp worktree removed.
★gitignore trap: `git add trading_corp/data/mlb_poly_kalshi_match.py` blocked by a `data/` ignore pattern
(matches any-level `data/`); the file is tracked and `git checkout <rev> -- <path>` had already staged it,
so the commit included it — do not re-`git add` (or use `-f`).

## ROLLBACK (if ever needed)
`powershell -ep bypass -f .\pk_item1_rollback.ps1` — restores BOTH from `.bak_item1_20260819_104959` +
restart (both-or-neither). No cutover/roster change.

## STATUS: both Poly->Kalshi fixes LIVE
- Item 2 (mark quote() fix) — prod-live `fc78fc7`, quote()=0.345 proven.
- Item 1 (conflict gate) — prod-live `7150404`, gate live (awaiting first conflict).
- Engine 786261, armed, roster 2 live / 4 paper. Nothing else to deploy for this workstream.
