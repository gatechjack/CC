# Bitunix dashboard consolidation proposal — 2026-05-27

**Status:** APPROVED by operator with Section F decisions (see end of doc)
**Author session:** 2026-05-27 (post the 23:18 UTC `pa_validation.require_all: false` + `min_validators_passed: 2` deploy)
**Related:** `scripts/replay_pa_validation_alt.py` + `reports/2026-05-27_bitunix_pa_replay.txt` + `reports/2026-05-27_bitunix_pa_replay_synthesis.md`; deploy_log 2026-05-27 23:18 UTC entry

## Context

The bitunix_futures detail page at `https://trading.jacksumner.com/division/bitunix_futures` has six large polling panels (Pending PA · HTF Regime · PA Validators · Decision Flow · Trade Plan v2 · Confluence Score), each rendering sub-tables of 5–50 rows. Operator says they scroll ~7 screenshots to assemble one day's picture, and the decisions they care about (Are we firing? Why aren't we firing? Did the 2026-05-27 PA loosening work?) require reconstructing a funnel mentally from three different panels.

This proposal recommends a 5-panel main page tuned for operator decisions, with the diagnostic granularity preserved on a `/division/bitunix_futures/debug` route. The implementation lands in two steps:
1. **Small standalone PR FIRST** (this session, per Section F item 6): cut the obvious clutter (Phase 3.2 label, Recent Evaluations duplicate, bar-cache aggregate).
2. **Full 5-panel rebuild** (separate session): everything else in Sections B/C/D.

## Phase 0 — Verifications performed (read-only against prod)

### V1 — `paper_trade_record` bitunix rows

**Result: 76 rows total. 3 rows since 2026-05-23 anchor. All 3 post-deploy fires are WINS.**

| order_id (8) | ts | tier | side | source_signal | result | R-mult | $PnL |
|---|---|---|---|---|---|---|---|
| `cb19b9ad` | 2026-05-27 16:07:08 | PREMIUM | sell | mc_a_blood_diamond | win | +0.13 | **$0.00** |
| `28f43f1e` | 2026-05-27 18:00:18 | STANDARD | sell | cvd_bear_flip | win | +0.92 | **$0.00** |
| `0b118801` | 2026-05-27 22:13:44 | STANDARD | sell | otter_buy | win | +0.81 | **$0.00** |

**Updates a wrong premise from the prior-session funnel diagnostic** (`reports/2026-05-27_bitunix_funnel_diagnostic.md` claimed bitunix paper trades aren't in `paper_trade_record`). They ARE — keyed by `division='bitunix_futures'`. **`actual_pnl_dollars = 0.00` on every row** is the real persistence gap (filed BACKLOG MEDIUM; column exists, value never computed). Win-rate cell IS buildable.

**Note (don't over-weight):** 3/3 post-deploy wins, R-avg +0.62 on a tiny sample. The 2-of-3 deploy is showing the shape the replay predicted. Wait for the full 1-week observation window before declaring anything.

### V2 — `bitunix_signal_ledger` + view-builder ledger logic

**Result: ledger has 3,117 rows; view-builder ledger logic is correct.**

`build_bitunix_score_view` at `data.py:2483-2497` uses `cutoff = (now - timedelta(hours=24)).isoformat()` which produces ISO with `T` separator — matches the ledger's `T`-separated `ts` for correct string comparison. `oldest_live_signal_age_sec` computes cleanly. (Initial probe used `datetime('now','-1 day')` with space-separator and would have falsely matched per `[[sqlite-iso-datetime-comparison]]` — the view-builder doesn't have that bug.)

### V3 — `recent_activity` for bitunix

**Result: shared cross-division panel with explicit kind whitelist; bitunix-matching kinds in the rail today are `would_have_placed`, `webhook_received`, `alert_ignored`, `webhook_rejected`, `agent_error`.**

`_query_division_activity` at `data.py:4896` over-fetches `limit × 5 = 100` rows of whitelist-kinds ordered by id DESC, then filters by `payload.get("strategy") == strategy or payload.get("division") == slug`. Bitunix's high-volume kinds (`bitunix_score_decided`, `pa_validation_decision`, `htf_gate_decision`) are NOT in the whitelist, so the rail under-shows bitunix activity even when bitunix is busy. With polymarket/kalshi audits dominating, the 100-row over-fetch frequently contains 0 bitunix rows — hence operator's "0 events" observation.

Real bitunix activity exists: today 1,159 bitunix-division audit rows in 24h. **Verdict: keep + fix whitelist** (add bitunix-specific kinds), don't delete.

### V4 — HTF gate PASS/REJECT inference

**Result: predicate `(size_multiplier > 0) AND (hard_zero_reason IS NULL)` correctly identifies HTF PASS.** Spot-checked 5 recent rows; all consistent.

**Today 2026-05-27 funnel (computed at ~23:43 UTC, ~30 min post-deploy):**
- 540 score evals
- 12 PA passes (2.2%) — note: ~30 min of post-deploy data already lifting the rate above the pre-deploy 0.94% baseline
- 3 HTF passes (25% of PA passes; `proximity_to_support` hard-zeroes 9 of 12)
- 3 placed (100% of HTF passes)

Inference works; Panel 2 is buildable.

## Section A — Current state inventory

| Panel | Partial | Poll | View-builder | Data source |
|---|---|---|---|---|
| Pending PA | `bitunix_pending_pa_panel.html` | 15s | `build_bitunix_pending_pa_view` (data.py:1907) | observer in-memory cache + 50-row `pa_validation_decision` enrichment |
| HTF Regime | `bitunix_htf_panel.html` | 30s | `build_bitunix_htf_view` (data.py:1529) | `deps.bitunix_htf_provider` (in-memory) |
| PA Validators | `bitunix_pa_panel.html` | 30s | `build_bitunix_pa_view` (data.py:1999) | `pa_validation_decision/redeem/expired` (last 10 + 24h + recent 5 of each) |
| Decision Flow | `bitunix_decision_flow.html` | 30s | `build_bitunix_decision_flow_view` (data.py:2150) | score+pa+htf+redeem (5 + 100×3 audit scans, 60s Python join) |
| Trade Plan v2 | `bitunix_trade_plan_panel.html` | 30s | `build_bitunix_trade_plan_view` (data.py:1677) | `trade_plan_decision`, `position_sl_update`, `audit_reality_run` |
| Confluence Score | `bitunix_score_panel.html` | 30s | `build_bitunix_score_view` (data.py:2315) | `bitunix_score_decided` (last 20), `paper_trade_record`, `bitunix_score_cooldown`, `bitunix_signal_ledger`, live bar_cache + price-ctx |

All six panels poll the SAME route (`/division/bitunix_futures`) with `hx-select="#<panel-id>"`. Re-rendering the whole view six times per 30s is a quiet perf concern (not in scope).

### Audit kinds WRITTEN but never DISPLAYED today

- `flip_opportunity_detected` (PR 3.1 detector — 0 fires since deploy)
- `bitunix_observer_classified` (Phase 3.1 legacy — 46 SKIP rows, superseded)
- `bitunix_decided` (Phase 3.1 legacy — superseded by `bitunix_score_decided`)
- `htf_regime_snapshot` (periodic; trend not surfaced)

## Section B — Proposed 5-panel layout

```
+------------------------------------------------------------+
|  [1] STATUS HEADER                                         |
|     Account: $X equity / $Y cash / +Z% today P&L           |
|     Regime: NEUTRAL  composite=+0.12  vol=normal           |
|     ATR 3m: $95.34   Fee floor (1R round-trip): $0.60      |
|     1R clears floor: YES (95.34 >> 0.60)                   |
+------------------------------------------------------------+
|  [2] TODAY'S FUNNEL (UTC day, single horizontal row)       |
|     540 evals -> 12 PA-pass (2.2%) -> 3 HTF-pass (25.0%)   |
|       -> 3 placed (100%)                                   |
|     <delta vs prev-day in small text>                      |
+------------------------------------------------------------+
|  [3] PA VALIDATOR-PAIR DISTRIBUTION (last 50 PA passes)    |
|       <- promoted to position 3 per Section F item 2 >     |
|     volume + vwap:        X passes  ##########             |
|     vwap + structure:     Y passes  ####                   |
|     volume + structure:   Z passes  ##                     |
|     [if structure_alignment never appears in any pair      |
|      after 50 passes, the 4h-horizon-check is dead-weight  |
|      and the next structural fix is 4h -> 15m/30m]         |
+------------------------------------------------------------+
|  [4] OBSERVATION WINDOW (PA 2-of-3 deploy)                 |
|     Started: 2026-05-27 23:18 UTC  Closes: 2026-06-03      |
|     Fires today: 3   |   avg fires/day since deploy: 3/d   |
|     Win-rate (closed trades): 3/3 (100%)  R-avg +0.62      |
|     Open: 0                                                |
|     $PnL: not wired (see BACKLOG MEDIUM)                   |
|     Rollback: see deploy_log entry's backup-tag recipe     |
+------------------------------------------------------------+
|  [5] RECENT PAPER FIRES + OUTCOMES (last 10)               |
|     ts | tier | side | entry | SL | TP1/2/3                |
|       sl_state | result | R-mult                           |
+------------------------------------------------------------+

+-- /division/bitunix_futures/debug (separate page, on-demand) --+
|  Pending PA cache details                                     |
|  Per-TF HTF regime breakdown                                  |
|  Last 24h PA decisions table                                  |
|  Decision Flow last-5 chain view                              |
|  Score factor contributions + cooldown + bar cache per-TF     |
|  Trade Plan v2 + SL lifecycle full detail                     |
|  Redeemed / Expired Waits full tables                         |
+---------------------------------------------------------------+
```

## Section C — Per-panel detail

### Panel 1 — Status Header (combined)

**Purpose:** Answer "is the system healthy right now."
**Data source:**
- equity/cash/P&L: existing division header (no change)
- Regime + composite + vol: `bitunix_htf_view.regime/composite_score/volatility_tier`
- ATR 3m: `bar_cache.atr_14` (in-memory observer)
- Fee floor (1R round-trip $): `fee_config.round_trip_cost_pct()` × `bar_cache.last_close`
- 1R clears floor predicate: `atr_14 > fee_floor_dollars`

**HTMX cadence:** 30s
**New instrumentation:** None; all data already in `build_bitunix_score_view` + `build_bitunix_htf_view`. Need ONE new view-builder that combines.

### Panel 2 — Today's Funnel (single row)

**Purpose:** Replace mental funnel reconstruction.
**Data source:** Five `SELECT COUNT(*)` filters scoped to `ts >= UTC_day_start`:
- evals: `kind='bitunix_score_decided'`
- PA pass: `kind='pa_validation_decision' AND json_extract(payload_json,'$.decision') = 'pass'`
- HTF pass: `kind='htf_gate_decision' AND json_extract(payload_json,'$.size_multiplier') > 0 AND json_extract(payload_json,'$.hard_zero_reason') IS NULL` (validated by V4)
- placed: `kind='would_have_placed' AND payload LIKE '%bitunix%'` OR `paper_trade_record WHERE division='bitunix_futures' AND ts >= …`

**HTMX cadence:** 30s
**New instrumentation:** None.

### Panel 3 — PA Validator-Pair Distribution (last 50 PA passes) — PROMOTED

**Purpose:** Answer "is `structure_alignment` ever contributing post-2-of-3?" Drives the NEXT structural decision (4h check → 15m/30m).
**Data source:** `SELECT json_extract(payload_json,'$.passed') FROM audit_event WHERE kind='pa_validation_decision' AND json_extract(payload_json,'$.decision')='pass' ORDER BY id DESC LIMIT 50`; aggregate tuples on Python side.
**HTMX cadence:** 60s
**New instrumentation:** None — `passed` already in `pa_validation_decision` payload.

### Panel 4 — Observation Window status (PA 2-of-3 deploy)

**Purpose:** Show operator "is the 2-of-3 change working."
**Data source:**
- Fires today: count from `paper_trade_record WHERE division='bitunix_futures' AND ts >= UTC_day_start`
- Avg fires/day since 2026-05-27 23:18 UTC: same query, group by date, average
- Win-rate (closed trades): `SELECT COUNT(*) ... AND result='win'` / total — V1 confirmed data exists
- R-avg: `AVG(actual_r_multiple)` over the same window
- Open: rows with `result IS NULL OR result='open'`
- $PnL cell: **show "not wired (see BACKLOG MEDIUM)"** per Section F item 3
- Rollback recipe: static text from deploy_log

**HTMX cadence:** 60s
**New instrumentation:** None for win-rate + R-avg. $PnL cell blocked on persistence (BACKLOG MEDIUM).

### Panel 5 — Recent Paper Fires + Outcomes (last 10)

**Purpose:** The actual trade record.
**Data source:** `paper_trade_record WHERE division='bitunix_futures' ORDER BY ts DESC LIMIT 10`; lifecycle column from latest `position_sl_update` audit joined by `order_id`.
**HTMX cadence:** 30s
**New instrumentation:** None.

## Section D — Cut list (full rebuild scope)

| Item | Verdict | Reason |
|---|---|---|
| Pending PA panel (main page) | **MOVE TO DEBUG** | Live cache, analytic value but not decision-grade tile |
| HTF Regime full panel (main page) | **MOVE TO DEBUG** | Composite/regime now in Panel 1 header; per-TF detail is debug-grade |
| PA Validators panel (main page) | **MOVE TO DEBUG** | Pair-Distribution (Panel 3) answers the active question; full table is for diagnosis |
| Decision Flow panel (main page) | **MOVE TO DEBUG** | Funnel (Panel 2) gives the headline; chain view is for diagnosis |
| Trade Plan v2 panel (main page) | **CONSOLIDATE** | Inline lifecycle state in Panel 5; full detail moves to debug |
| Score panel — "Recent Evaluations (20) · Ledger 24h" block | **CUT (SMALL PR FIRST)** | Duplicate of Decision Flow's chain view |
| Score panel — bar-cache aggregate stat card | **CUT (SMALL PR FIRST)** | Per-TF version in HTF panel (moved to debug) is canonical |
| Score panel — "Phase 3.2" header label | **CUT (SMALL PR FIRST)** | Internal versioning; dashboard isn't release notes |
| Redeemed / Expired Waits separate tables | **COLLAPSE** | One-line counter on main + full tables in debug |
| Recent Activity panel | **KEEP + FIX WHITELIST** | V3 found it's a real cross-division panel underfed by whitelist; add bitunix-specific kinds (Section F item 4) |

## Section E — Implementation notes (next session, post small-PR cleanup)

- **Single PR scope** for the full rebuild: new templates + new view-builder functions + new `/division/bitunix_futures/debug` route. Existing partials `git mv`'d into a `debug/` subdirectory so blame survives.
- **Polling:** Panels 1, 2, 5 at 30s; Panels 3 + 4 at 60s. **Debug page on-demand only, no background polling** (Section F item 5).
- **Mobile-friendly:** Panels 1+2+3 stack on phone width (decision-grade); 4+5 below.
- **Risk:** presentation-only. Doesn't touch observer / risk gate / order placement. CLAUDE.md §4 doesn't apply.
- **Backwards compatibility:** route at `/division/bitunix_futures` stays the same; debug route is additive.

## Section F — Operator-decision checkpoint (APPROVED 2026-05-27)

1. **5-panel layout (Section B):** **APPROVED as drawn.**
2. **Pair-Distribution at position 3 (above Observation Window):** **APPROVED.** V1 confirms outcomes tracked; Panel 4 isn't half-blocked. Pair-Distribution still ranks above because it answers a forward question (4h structure_alignment fix) rather than current validation.
3. **Win-rate persistence gap:** **(a) ship Panel 4 with `$PnL` cell marked "not wired (BACKLOG MEDIUM)".** R-multiple is the load-bearing edge measure; dollar PnL is presentation. Don't gate the rebuild on it.
4. **Recent Activity panel underfetch:** **add bitunix-specific kinds** (`bitunix_score_decided`, `pa_validation_decision`, etc.) to `_query_division_activity`'s whitelist. Whitelist fix is correct; over-fetch multiplier is a workaround.
5. **Debug page polling:** **on-demand only** — no polling when route is closed. Same-cadence polling would defeat the consolidation.
6. **Small standalone PR FIRST (this session):** **APPROVED.** Cut "Phase 3.2" + "Recent Evaluations" duplicate + bar-cache aggregate from `bitunix_score_panel.html`. Cheap, low-risk, visible cleanup. Full 5-panel rebuild deferred to its own session.

## Constraints honored this session

- Read-only verifications + proposal markdown + BACKLOG entry + the approved small PR
- All underlying audit-row data preserved (consolidation is presentation)
- New tiles use only data the audit already captures (Panel 4's $PnL cell explicitly blocked, not silently faked)
- Tripwire boundary unchanged (presentation, not strategy logic)
- Other divisions' dashboards untouched
- Full 5-panel rebuild deferred to a separate session per this spec
