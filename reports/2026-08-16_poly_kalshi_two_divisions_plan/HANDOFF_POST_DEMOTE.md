# HANDOFF — post 2-whale demote (2026-08-18)

Addendum to `HANDOFF_POST_2A.md` (which still holds for the broader forward work). Memory anchor
`poly-kalshi-mlb` is authoritative. Do not re-open closed work.

## STATE (confirmed read-only at wrap)
- **live_whales = {SDTrading `0x16bb…8492`, xifutloong3 `0x2dc1…b33c`}** (2). Demoted
  monkeymashingkeyboard `0x684b…8409` + 0x0x23kj `0x9c3c…e8c9` to PCT paper.
- **PCT paper `selected_whales` = 4**: the 2 demoted + 2 pre-existing watchlist promotions
  (`0x3833…`, `0x8239…`). `live ∩ paper == ∅` (live=2 / paper=4 disjoint).
- Engine **PID 775659**, `poly_kalshi_mlb` **ARMED** (auto_execute=true / dry_run=false / halted=false).
  (PID is 775659 after a concurrent MACE deploy 2026-08-18 ~12:40 UTC restarted the engine; the demote
  itself was agent_state-only, no restart.)
- monkeymashingkeyboard's **3 open live positions** (2× SF@CLE, 1× DET@PIT) ride to settlement — still
  live-owned, poller-tracked (pre-game so unmarked until game time), book to the LIVE division.
- Division is **actively copying**: 17 placed / 15 settled / 9–6, **+$1.96 GROSS** (fees NOT modeled;
  likely net-negative once Kalshi taker fees are applied).

## REVERSIBILITY
- Undo THIS demote: `powershell -ep bypass -f .\pk_demote_2whales.ps1 -Reverse` (moves both back to
  live_whales, exact inverse).
- Re-promote a single whale later (if its paper record argues for it): the promote endpoint /
  `promote_whale_to_live` (flatten-on-promote; live history retained). Wallet-keyed, reversible by design.

## OPEN THREADS (carry forward — DO NOT build here)
1. **Net the fee into realized P&L.** Both the Kalshi resolver and the Polymarket scorer report GROSS
   (`fill_fee` is journaled on the poly_kalshi_order row but never netted; `kalshi_round_trips` has no fee
   column). This is the exact trap that misfired the sibling Kalshi copy division. Fold a fee-netting
   term into the dashboard-rebuild so future whale-eval is honest.
2. **Persist the poly trigger timestamp.** poly→kalshi lag is currently unrecoverable — `seen_ts /
   action_ts / latency_s` live only in the in-memory `shadow_log`; the persisted `trigger` dict has no
   poly timestamp. Add it to the trigger payload going forward (small executor change).
3. **Canonical sell-aware whale records need `whale_screening` + `build_audit_report` on the box.** The
   box scorer is drifted (older) — `trading_corp.data.whale_screening` isn't deployed, so SET-1 whale
   records had to be recomputed as a per-fill held-to-resolution PROXY (GROSS, not sell-aware). Deploy the
   current scorer (or fold the canonical audit into the dashboard) for exact numbers.
4. **From HANDOFF_POST_2A (unchanged):** Claude Design prompt (gated on watching a game populate the live
   mark + sparkline), live-whales roster TAB on the poly_kalshi_mlb dashboard, live-equity wiring decision
   (KAREN attribution, avoid double-count), shared `secrets.py` RedactingFilter fix (`log("%s", dict)`
   TypeError, core-scoped). Plus the flagged (separate-session) `main`-vs-prod-live reconcile (main behind
   prod-live by the Phase-2a commits).

## OPS
Read-only status: `pk_wrap_status_ro.ps1`. Roster moves: `pk_demote_2whales.ps1` (DRY/-Apply/-Reverse).
Rollback of the CP6 deploy: `pk_cp6_rollback.ps1 -BackupSuffix .bak_cp6_20260817_043609 -CutoverWasApplied`.
Every prod mutation is an operator-run `.ps1` (agent read-only on prod). Shared byte-locked files
(`kalshi_copy_trader.py`, `sports_team_mapping.py`, `kalshi_live.py`) unchanged throughout.
