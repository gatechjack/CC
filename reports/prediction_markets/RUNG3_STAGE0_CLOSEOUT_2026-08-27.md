# Stage-0 RUNG 3 + LADDER CLOSE-OUT (2026-08-27)

**Authorized rung 3** (the 22-row `active=0` write) executed 2026-08-27 13:10:36Z. **This closes the Stage-0
three-rung ladder** (rung 1 migration 2026-08-27 02:15Z · rung 2 code deploy 12:33–12:49Z · rung 3 row write
now). Unlike rungs 1–2, rung 3 is **supposed** to change `/farm` — it is the visible result of the whole ladder.
**Stage 1 is NOT authorized and NOT prepared.**

## The write
`UPDATE pm_watchlist SET active=0, removal_reason=?, removal_ts=1787836236 WHERE category=? AND status='pinned'
AND active=1`, one per excluded category, in a single committed transaction guarded by a pre-write invariant
(rolls back if the update touches ≠ 22 rows):
- **cbb ×3 → `not_probed`**
- **fifwc ×8 → `dormant_calendar`**
- **unknown ×11 → `structural`**
- `removal_ts = int(time.time())` = **1787836236** (epoch INTEGER), on **every one of the 22 rows**.

**★ Reason-string reconciliation:** the deployed values are the **short** strings above — matching Jack's rung-3
authorization AND `PM_REBUILD_PLAN` line 89 (the rung-3 line). The plan's earlier §Stage-0 prose (line 61) had
longer variants (`pending_analysis_ncaab_not_probed`, `dormant_calendar_returns_next_wc`,
`structural_slug_failure`); those are **superseded** — the LIVE values are `not_probed` / `dormant_calendar` /
`structural`. (Line 61 annotated accordingly.)

## Pre-conditions (reported) — PASSED
- Window clear: UTC 13:07Z, next `03:20` cron **14.21h** away; poller confirmed manual-only (not run).
- Baseline: `/farm` 228,569; 18 cat / 114 pair; active1 114 / active0 0; paper 102; schema 8; engine 89366 /
  pmweb 132990.
- The 22 rows identified and verified **3 / 8 / 11**, all `pinned & active=1`, no out-of-scope row; the 15 IN
  categories = **92** pairs, untouched. 22 + 92 = 114.
- **DB backup (the rung-3 rollback instrument):** `~/pm_stage0_rung3_dbbackup_20260827T130737Z.db`,
  25,137,152 bytes, **sha256 `9066a392bfea47011566435cfe88efe3d4b51c9ccd6f58a2cfd330bf80b9fa78`**,
  **integrity_check ok**, schema 8, active0 0 (pre-write), paper 102.

## Post-checks (reported) — ALL PASSED
- **active0 = 22, active1 = 92, total = 114** — no row deleted.
- All 22 carry the correct `removal_reason` **AND** a NOT-NULL, plausible epoch `removal_ts` (all = 1787836236):
  `not_probed`×3, `dormant_calendar`×8, `structural`×11 (0 NULL ts, 0 NULL reason).
- **`/farm` 200, 228,569 → 182,835 bytes; now 15 categories / 92 pairs.**
- **The 22 appear NOWHERE:** farm_categories = the 15 IN cats only (cbb/fifwc/unknown gone from tiles);
  farm_rows(PINNED) = 92; **`query_scoreboard` 113 → 91, with 0 removed pairs leaked** (the late-added ranker
  gate's first live exercise — verified specifically).
- **Paper history intact:** pm_paper_trade **102** (deactivation preserves the paper rows — reversible; Jack
  intends to re-admit some categories later).
- **Poller sees 92** (not 114); schema still **8**; **engine 89366 unchanged; pmweb 132990 unchanged** (no
  restart — a data write, not a deploy).
- None of the STOP conditions (count ≠ 22, NULL removal_ts, paper moved, `/farm` ≠ 15/92) triggered.

## Reversibility (unchanged design)
`active=1` restores any pair to the funnel **in its prior status** with its paper rows intact. cbb returns
after its NCAAB probe; fifwc next World Cup cycle; unknown is `structural` (never flipped back). The rung-3 DB
backup is the total rollback (Jack's call to restore).

## Stage-0 ladder — COMPLETE
| Rung | What | When | Record |
|---|---|---|---|
| 1 | migration 008 (`active`/`removal_reason`/`removal_ts` + index) | 2026-08-27 02:15Z | plan §Rung-1 |
| 2 | deploy 5 gated files + pm_web restart (40483→132990) | 2026-08-27 12:33–12:49Z | `RUNG2_DEPLOY_COMPLETE_2026-08-27.md` |
| 3 | 22-row `active=0` write (15 cat / 92 pair live) | 2026-08-27 13:10Z | this doc |

**prod-live:** `origin/prod-live` advanced `95e78c4 → c77f618` (fast-forward, 2026-08-27; `95e78c4` an ancestor,
MACE fork base intact). Branch `prediction-markets-stage0-2026-08-26`. **Stage 1 NOT authorized.**
