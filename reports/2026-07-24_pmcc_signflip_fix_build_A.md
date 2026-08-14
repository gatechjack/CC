# PMCC sign-flip fix — Build A (Stage-1, local; NOT deployed)

Follow-up build to `reports/2026-07-24_pmcc_first_live_morning_healthcheck.md`. Branch
`pmcc-phase-a-atomic-rollshort-2026-07-22`, on top of `440bf3e`. **Nothing deployed, nothing
restarted, no prod DB touched.** Stage-2 (operator-authorized) applies the code + prod-write scripts.

Commits: `2d3d436` (item 1) · `abc47eb` (item 2) · `2a3665f` (item 4) · Stage-2 SQL artifacts.
**Item 3 (opening-settle window) DROPPED — superseded by the addendum's pre-open/post-settle scan
split, which is a separate build (Build B) branched off this commit.**

---

## Gate investigations (done before building)

### Gate 1 — is the sign-flip really cosmetic? (net_actual / leg-fill consumers)
Traced every consumer. **No risk gate, halt, or retry path reads the sign** — confirmed cosmetic *in
outcome* today:
- `net_actual` → **`ic_telemetry` only** (display).
- per-leg `fill_price` → `_query_prior_rolls[_detailed]` → `prior_credit_total` → **approval-card
  rationale** (`approval_format.py:404`) — display, not a gate.
- `_persist_combo_positions` → `position` journal `avg_price` → dashboard/reconciliation (PMCC
  re-derives positions from the broker each scan).
- halfway-roll **cooldown** (`pmcc_robinhood.py:2838`) uses strikes/dates, **not** fills — unaffected.

**Surfaced finding (forward-looking):** the 2 persisted swapped rows (OPEN `5c9e347f`, RKLB
`360f4b92`) *will* feed `_query_prior_rolls_detailed` on the next OPEN/RKLB roll → **the LLM ROLL
HISTORY prompt block** (`pmcc_robinhood.py:2887` `"Net credit collected from rolls: $X"`) *and* the
approval card, both showing a falsely-**negative** prior credit (off by 2× the true credit/pair). Soft
decision-influence on the roll LLM + a re-run of the alarming display — **not** a hard gate. → fixed by
the Stage-2 data script (below), per operator direction.

### Gate 2 — RKLB "$70/+$271 → $75/+$117" reconstruction
From the audit trail: pre-open queued **$85C @ +$0.13** (combo `cb0c428d`, 12:32) → aborted at open
(13:33–34) → **13:41:06 `roll_gates mark_net 2.71`** (+$271, a transient near-ATM ~$70C re-eval, never
persisted) → **13:42:29 re-selected $75C** (δ0.25, mark 1.25 → combo `360f4b92`, +1.22) →
**13:42:31 Approved** (the $75C) → +$117 fill. Root: **the engine re-selects the roll target every scan
against a moving spot and does NOT freeze the rendered card**; RKLB was crashing so the strike walked
$85→~$70→$75 in ~80s. The card changed under the operator; the Approve bound to the current registry
entry ($75C). This is a **card-staleness/consent** problem → the real fix is Build B's scan split
(price cards off live post-open data); item-2's consent guard is defense-in-depth.

---

## What was built (items 1, 2, 4) + prepared (5, data-fix)

**Item 1 — leg-fill attribution by option identity** (`brokers/robinhood.py`, `2d3d436`)
`place_multi_leg` now matches each Robinhood result leg to the submitted order by
`(option_type, expiration, strike)` instead of list index (`_match_result_legs_to_orders`), with a
positional fallback only when RH omits identity fields (offline doubles). Fixes per-leg `fill_price`,
`net_actual` sign, slippage, the dashboard, and the FILLED alert together. Tests: reversed-order →
correct attribution + positive credit; order-independent; positional fallback.

**Item 2 — reprice sanity + consent bail** (`_pmcc_combo.py`, `pmcc_robinhood.py`,
`_ic_orchestration.py`, `abc47eb`)
- `reprice_combo_from_quotes` HOLDs (keeps proposal tag, sets `extra.reprice_hold`) on a zero-bid sell
  leg or an implausibly wide spread (`> % of mid` **and** `> $ abs` floor, so cheap 1-tick legs don't
  false-trip).
- `snapshot_combo_for_consent` + `assess_combo_reprice_consent` compare the dispatch-repriced combo to
  the operator-approved snapshot; `dispatch_approved_ic_combo` **bails (books nothing, calm ABORTED
  alert, next scan re-proposes)** on sign-flip / credit-collapse / strike-drift / stale-wide quotes.
  Scoped to PMCC via the `assess_combo_consent` capability (IC untouched). All thresholds configurable
  under `robinhood_pmcc.combo` (`reprice_max_spread_pct` 0.60, `reprice_min_sell_bid` 0.0,
  `reprice_min_spread_abs` 0.10, `max_adverse_net_deviation_dollars` 0.25).

**Item 4 — abort wording + sub-gate breakdown** (`pmcc_robinhood.py`, `2a3665f`)
ABORTED alerts now read *"no action - no order sent, position unchanged; will retry next scan"* with a
short diagnostic tail, instead of the panic-triggering "sparse_chain_no_weekly ... missing new_short".
`_filter_liquid` classifies each rejection (liveness/volume/spread/no_ask) → surfaced in
`_last_weekly_diag` + the alert (`failed_by=...`), so the opening-rotation volume/spread cause is
visible rather than a blanket "all failed liquidity gate".

**Item 5 + data-fix — Stage-2 prod-write scripts (PREPARED, NOT RUN)** (`deploy/stage2_pmcc_20260724/`)
- `01_fix_signflip_fills.sql`: `proposed_order.fill_price` on the 2 swapped pairs → broker-authoritative
  (OPEN 5C@0.03/4C@0.29, RKLB 74C@0.03/75C@1.20); idempotent, id+symbol+side guarded, prints
  before/after + net verify (OPEN +0.26 / RKLB +1.17). Card + LLM feed **only** — not the position book,
  not the audit log.
- `02_void_zombies.sql`: void the 4 stale board_approved zombies (3 ASTS, 1 CIFR) → board_rejected.
- **Blast radius READ-ONLY scan confirmed = exactly these rows** (4 live filled combo legs = 2 pairs;
  all 18 other filled combo legs are `execution_mode='paper'`, never swapped).

---

## Tests

Targeted (all green, in isolation): `test_robinhood_multi_leg` **35/35** (3 new identity + existing) ·
combo/dispatch/orchestration bundle **112/112** · `test_pmcc_reprice_consent` + abort + logic + phase-a
**158/158** · new-files-don't-pollute check **50/50**. New tests added: 3 (item 1) + 12 (item 2) +
2 (item 4).

**Full-suite note (important):** the repo's full suite is **NOT green at baseline** — a pre-existing
test-isolation problem (an early test file patches `robin_stocks`/global state and pollutes later files).
At the parent commit `440bf3e`, *before any of my code*, the full run has **45 total (44 FAILED +
1 ERROR)**, including **19 `robinhood_multi_leg` failures** (its original tests fail under the pollution;
they pass in isolation). My branch: **48 total** (22 in `robinhood_multi_leg`). The delta is **exactly
+3 = my 3 new identity tests**, which fail only in the full run under the same pre-existing pollution and
pass in isolation (35/35) and alongside my files (50/50). **Regression diff vs baseline, excluding the
pollution-victim `robinhood_multi_leg`: EMPTY** → zero new failures introduced anywhere else. (The
pre-existing full-suite isolation problem is a separate test-health issue, out of this build's scope.)

---

## GO / NO-GO

**GO for Build A code (items 1, 2, 4)** — correct, isolated, tested; no new regressions; IC path
untouched; `auto_execute` unaffected. Item 1 is the root fix for the panic.

**Stage-2 (operator-authorized) still required for:**
1. Deploy items 1/2/4 (Gate-A drift → `.bak` → stage → atomic mv → flat-guarded restart).
2. Run the 2 prepared SQL scripts against prod `trading_corp.db` (data-fix + zombie prune) — take a
   `.bak` of the DB first; no restart needed.

**Deploy-with question:** Build A code + Build B (scan split) are independent files and can deploy in
one Stage-2 or separately. Recommendation: deploy **Build A first** (it's the money/observability fix and
is self-contained), then Build B after its own review. Build B is a larger orchestration change and
benefits from a separate stage. The Stage-2 SQL can run with either.

**Next:** Build B — the addendum's pre-open-triage / post-settle-actionable scan split (its own item-0
schedule investigation → split → reconcile), branched from this commit. Not started (per instruction to
finish + commit Build A first).
