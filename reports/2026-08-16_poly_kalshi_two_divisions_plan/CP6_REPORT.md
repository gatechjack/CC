# CP6 report — stage the poly_kalshi division epoch (operator-run)

**Status: DONE (operator-run prod write, verified). Checkpoint STOP — awaiting review before CP7.**
Branch `poly-kalshi-mlb-phase1-2026-08-15`.

## ★ THE SPLIT INSTANT (record for CP7)
```
2026-08-16T20:29:25+00:00
```
Set as `agent_state[poly_kalshi_mlb/metrics_epoch]`. **CP7 must set the polymarket epoch to
this SAME instant** (`agent_state[polymarket_copy_trader/metrics_epoch]`) so both dashboards
share one split date and go fresh together at deploy.

## Live-money / live-loop status (lead)
- **Zero live effect.** The write set only the `poly_kalshi_mlb` epoch, which is **INERT until CP7**
  (the CP5 read code is not deployed). The live PCT paper dashboard is unchanged — the
  `polymarket_copy_trader` epoch was NOT touched (deferred to CP7 per operator).
- **Live loop untouched** — no restart, no code change; a single `agent_state` row written as
  `azureuser` (via `runuser`, so DB/journal ownership matches the engine).

## What CP6 did
Per operator decisions: split date = **now-UTC at run time**; **stage only poly_kalshi now**
(defer the polymarket reset to CP7). Operator ran `cc\pk_epoch_reset.ps1` (az `@`-pattern
`RunShellScript` on RG-SHARED-PROD/tc-prod-vm; write executed as `azureuser`):
- Set `agent_state[poly_kalshi_mlb/metrics_epoch] = 2026-08-16T20:29:25+00:00`.
- Did NOT touch `agent_state[polymarket_copy_trader/metrics_epoch]`.
- Deleted nothing (history retained on disk).

## Evidence (operator-run output, verbatim)
```
BEFORE pk_epoch None
BEFORE pct_epoch_UNTOUCHED 2026-07-07T20:00:54.855571+00:00
SPLIT_EPOCH 2026-08-16T20:29:25+00:00
AFTER pk_epoch 2026-08-16T20:29:25+00:00
AFTER pct_epoch_UNTOUCHED 2026-07-07T20:00:54.855571+00:00
WRITE_OK True
ON_DISK poly_kalshi audit_rows=3 round_trips=0 (retained; nothing deleted)
```
Verification:
- **Epoch landed:** `AFTER pk_epoch` == `SPLIT_EPOCH` == `2026-08-16T20:29:25+00:00`; `WRITE_OK True`
  (read-back via a fresh connection → committed, cross-connection).
- **Polymarket untouched:** `BEFORE pct_epoch` == `AFTER pct_epoch` == `2026-07-07T20:00:54.855571+00:00`.
- **On-disk retained / reversible:** 3 poly_kalshi audit rows kept, 0 round-trips (resolver
  undeployed — expected). Revert = delete the one agent_state row.

## Notes for CP7
- **Fold in the polymarket epoch reset** at deploy: set `agent_state[polymarket_copy_trader/metrics_epoch]`
  = `2026-08-16T20:29:25+00:00` (the split instant above), so the PCT paper dashboard goes fresh
  together with poly_kalshi.
- **Verify BOTH dashboards read 0 from the epoch AFTER the CP7 restart** (the poly_kalshi
  verification could not run at CP6 — the CP5 read code isn't deployed until CP7): resolved
  tiles / History / OPEN / badge all 0 for post-split, on-disk history retained.
- The split instant (20:29:25 UTC) is AFTER the 3 pre-CP3 fills (placed ~17:40-18:00 UTC) — so
  they are pre-epoch AND already excluded by missing `division`/`order_id`; they stay off the
  fresh dashboard either way.

## NOT done (do not proceed without your go)
- **CP7** (deploy: drift-gate + prod-live advance + restart; set the polymarket epoch to the split
  instant; verify both dashboards read 0 + the live loop comes back ARMED/unhalted + no equity
  double-count; real-data gross-vs-net confirmation) — not started, operator-run.
- **Phase 2** — not started.

## Ops
- Runner: `cc\pk_epoch_reset.ps1` (operator machine; not committed — matches the `pk_*.ps1`
  convention). Reversible: `delete from agent_state where agent='poly_kalshi_mlb' and key='metrics_epoch'`.
