# PMCC UX-truthfulness investigation (READ-ONLY) — 2026-08-13

Branch `pmcc-ux-truthfulness-2026-08-13` off **prod-live `e113843`** (the live/deployed code).
No code changed, no mutation, `auto_execute:false` + halt untouched. Scope: two UX-truthfulness
behaviors, traced to files/functions/lines with a recommended enhancement each.

> **Base correction (surfaced before tracing).** This session was initially opened on
> `main-wip-2026-08-02` (`5014d5f`), a **stale/divergent** line that predates the entire PMCC
> P1/P2/P3 + tile-unify + `pmcc_preview` stack (merge-base with prod-live = `fd0f490`). All of
> the task's named mechanisms (`_render_pmcc_record_panel`, `pmcc_preview`, the 09:45 scheduled
> scan, P3 LLM-free pricing, consent stash+fingerprint) exist **only on prod-live**. The worktree
> was re-based onto `origin/prod-live e113843` before any findings were drawn. Everything below is
> against the live code.

---

## ISSUE 1 — a scheduled ROLL verdict cannot be Approved without a fresh LLM Re-analyze

### 1.1 What the scheduled scan persists (judgment only — no priced stash)

- The scheduled judgment scans are wired in `main.py`: the **P2 09:45 ET full slot**
  (`main.py:783` `_on_scan` → `pmcc_agent.scan()`, `main.py:816`), the lighter **`judgment_pass`**
  cadence (`main.py:1362`), and the **15:00 ET terminal pass** (`main.py:1268` `_on_terminal_scan`),
  all driven by `_scheduled_pmcc_scan_loop` (`main.py:1388`).
- Each analyzed symbol's verdict is persisted to the **unified decision record** via
  `_pmcc_status.record_pmcc_decision(...)` — from `scan()` at `pmcc_robinhood.py:2580-2589` and from
  `judgment_pass()` at `pmcc_robinhood.py:2186`. The record is one row per underlying in `agent_state`
  under `latest_decision:{SYMBOL}` (`_pmcc_status.py:4,52-54,150-203`) holding:
  `{status, source, computed_at, urgency, confidence, summary, rationale, warnings,
  target_delta_low, target_delta_high, target_dte}` (`_pmcc_status.py:184-197`).
- **Crucially, it persists JUDGMENT + a consent envelope (δ-band + target DTE), but NO price and NO
  consent stash/fingerprint.** The δ-band/DTE were added specifically so a *later* deterministic
  pricing refresh can pick the concrete strike within the band without re-running the LLM
  (`_pmcc_status.py:173-178`). The scan never calls `stash_preview` / `price_and_stash`.

So after the 09:45 scan fires (and its Telegram digest is sent), the panel has a fresh judgment
(`status="roll_short_early"`, δ-band, DTE) but **nothing an Approve can be built from** — the Approve
form's consent token is a `(preview_id, fingerprint)` pair that only a *pricing build* produces.

### 1.2 The stored-verdict panel: rolls are NOT priced; Approve is withheld by design

- `_render_pmcc_record_panel(deps, slug, symbol)` (`routes.py:4917-4984`) renders the Expert panel
  **from the unified decision record with NO LLM call** (`routes.py:4931` `load_decision`).
- **P3 ADDITION 3 (`routes.py:4942-4966`)** gives an LLM-free *priced* Approve — but **only** to
  `close_short` / `open_short`: the guard at **`routes.py:4947`** is
  `if action in ("close_short", "open_short"):`. Those call `pmcc_pricing.price_and_stash(...)`
  (`routes.py:4953`), which reconstructs a `PMCCAnalysis` from the stored record (NO
  `_llm_analyze_position`, `pmcc_pricing.py:75-97,123`), prices live, and writes the consent stash
  (`pmcc_pricing.py:142`) → renders `_render_pair_analysis(..., show_execute_button=True)`.
- **Rolls (and any unbuildable verdict) fall through** to `routes.py:4968-4984`:
  `_render_pair_analysis(..., show_execute_button=False)` (no live pricing, no Approve), then the note
  at **`routes.py:4975-4983`**:
  > *"Approve isn't shown on a stored verdict — it needs a live strike, debit, credit & net first.
  > Re-analyze (above) to build the live estimate, then approve that exact combo."*

There is **no P1 auto-refresh-pricing on the record panel for rolls.** (The interval pricing —
`pmcc_pricing.refresh_division`, `data.py:3491` — *does* price rolls LLM-free into a cache, but only
to feed the tile's pricing sub-badge via `tile_pricing_view`, `data.py:3496`; it is not surfaced as
an Approve on the record panel.)

### 1.3 Root mechanism

The **P3 LLM-free stored-panel pricing was scoped to `close_short`/`open_short` only** (`routes.py:4947`).
Rolls were never wired into it, so a stored ROLL verdict has no live estimate → no consent stash →
Approve is (correctly, given the consent model) withheld, and the operator is told to Re-analyze —
which re-spends an LLM pass purely to rebuild pricing the LLM never influenced.

### 1.4 The pricing/stash path is already roll-capable

`price_and_stash` is **action-generic**: `_analysis_from_record` sets `action = rec["status"]`
(`pmcc_pricing.py:85`) and `price_and_stash` only skips `("", "hold", "watch")` (`pmcc_pricing.py:119`);
for `roll_short`/`roll_short_early` it runs `propose_orders_for_pair(..., preview=True)`
(`pmcc_pricing.py:124`), builds the estimate, and stashes the fingerprint (`pmcc_pricing.py:142`).
Indeed `refresh_division` **already** calls it for every roll symbol on page load
(`data.py:3491` → `pmcc_pricing.py:205`), so during market hours a roll's priced build + consent
stash already exist — they're just consumed for the tile badge, not the Approve.

### 1.5 Recommended enhancement (Issue 1)

**Extend P3 ADDITION 3 to credit rolls.** Add `roll_short` and `roll_short_early` to the guard at
`routes.py:4947` so the record panel prices them LLM-free via the same `price_and_stash` →
`show_execute_button=True` path already used for close/open. Concretely this touches **only
`_render_pmcc_record_panel` (`routes.py:4942-4984`)** — no scan, no order path, no new pricing code.

- Keep **`roll_leap` excluded** — it is advisory/manual (`dispatch="advisory"`, refused by data_exec);
  it should retain the no-auto-place treatment (a priced-but-advisory display is a separate design
  question).
- Free side-benefit for Issue 2's panel side: for an **earnings-blocked** roll, `price_and_stash`
  returns non-buildable with `estimate_reason = last_roll_abort_reason(...)` =
  *"earnings within the buffer — roll suppressed (let the short expire)"* (`pmcc_pricing.py:153`,
  `pmcc_robinhood.py:1126-1127`), so the record panel would show the real suppression reason instead
  of the generic Re-analyze note.

**Consent / safety implications (shown == fires is preserved):**
- **Judgment reused, pricing fresh.** The LLM JUDGMENT (`rec.status` + δ-band + DTE) comes from the
  stored decision — **no Anthropic call**. The PRICE is computed fresh from live quotes at load and
  the concrete strike is chosen *within the persisted δ-band*; the `fingerprint` hashes combo SHAPE
  only (contracts, not price — `pmcc_preview.py:44-62`), so it is the exact same consent token
  machinery close/open already use.
- **Dispatch guards unchanged.** On Approve, `execute_pair_orders` still validates (id, fingerprint,
  TTL), re-checks the **earnings gate at dispatch**, and runs the **reprice consent guard**
  (`assess_combo_reprice_consent`, sign-flip / strike-drift / credit-collapse). A drifted rebuild
  bails with `pmcc_consent_fingerprint_mismatch` (design report `reports/2026-07-30_pmcc_rollcard_preview_consent.md`).
- **No spurious alerts.** The build runs with `preview=True`, which suppresses ABORTED/EARN_UNVERIF
  audit + Telegram emits (Defect-1 invariant), so surfacing the Approve does not ping the operator.
- **Staleness containment (recommended guard).** Offer the priced roll-Approve **only when the record
  is `fresh`** (`classify_freshness` already computed at `routes.py:4933`); do not resurrect a stale
  (>`staleness_hours`, default 8h) judgment into a one-click order. Off-hours is already safe —
  `price_and_stash`/refresh short-circuit to `market_closed_extras` (no stale-quote Approve,
  `pmcc_pricing.py:57-72`).

Net: this is the close/open treatment applied to the roll it was always analogous to — small, isolated,
and consent-preserving.

---

## ISSUE 2 — the tile shows the raw judgment, not the post-gate effective state

### 2.1 How the tile status label is derived (the raw stored judgment `status`)

- The tile pill is now the **unified decision status**: `pmcc_pair.html:55-66`
  (`{% set us = pair.unified_status %}`), commented *"Unified decision status pill: ONE timestamped
  verdict per asset… Replaces the old deterministic recommended_action preview."*
- `unified_status` is built by `_build_pmcc_tile_status` (`data.py:3328-3354`):
  `rec = _pmcc_status.load_decision(...)` (`data.py:3338`) and
  **`out["status_label"] = (rec.get("status") or "—").upper().replace("_", " ")`** (`data.py:3350`).
- So the tile literally shows `rec["status"].upper()` → `"roll_short_early"` → **"ROLL SHORT EARLY"**
  (BULL in the screenshot). **Confirmed: the tile is the raw stored judgment action.**

### 2.2 Where the earnings suppression is applied (scan-time order-build; NOT written back)

- The stored `status` is written as `status=_a.action` (`pmcc_robinhood.py:2581`), where `_a` is the
  LLM analysis **after** the in-composition mutators (terminal-DTE release, LEAP-hard-rule promotion,
  cooldown — `pmcc_robinhood.py:2554-2563`) but **before** the order-build gates.
- The **earnings buffer gate (B9)** fires *later*, inside the order build: `_earnings_gate_state`
  (`pmcc_robinhood.py` ~948-992) is consulted in `_propose_roll_short` (~`3938-3946`), which on
  `blocked` calls `_audit_roll_abort(reason="earnings_window", ...)` and **`return []`** — no order,
  and **no write-back to the decision record.** The human reason
  *"earnings within the buffer — roll suppressed (let the short expire)"* comes from
  `last_roll_abort_reason` (`pmcc_robinhood.py:1126-1127`).
- The Expert panel reflects this because a Re-analyze re-runs `propose_orders_for_pair(preview=True)`
  and surfaces the abort reason; the **tile never re-runs the gate** — it just prints the stored
  `status`.

### 2.3 Root mechanism (the desync)

`record_pmcc_decision` stores the judgment **before** the order-build gates decide the action is
un-actionable. Gates that mutate `_a.action` *before* the write (promotions/cooldown) are reflected;
gates that abort *during* order build (earnings / sparse-chain / net-debit / advisory) are **not**.
Tile = stored raw judgment; Expert panel = live post-gate outcome → they disagree exactly when a
downstream gate suppresses the action.

### 2.4 Other gates that similarly suppress AFTER the judgment is stored

Confirmed via full trace (details verified in code). "Written back?" = does the stored `status` reflect it.

| Gate | file:line | Effect | reason token | Written back to `status`? |
|---|---|---|---|---|
| **Earnings buffer (B9)** | `pmcc_robinhood.py:~948`, applied `~3938` | roll → `[]` | `earnings_window` | **NO** (tile stays raw) |
| **Sparse-chain / no qualifying weekly/LEAP (B4/B7)** | `_propose_roll_short ~3989`, `_find_best_weekly/leap` | roll/open → `[]` | `sparse_chain_*` / `no_*_weekly/leap` | **NO** |
| **Liquidity / OI / spread** | `_passes_liquidity ~878`, `_filter_liquid:1137` | drops contracts → feeds sparse-chain | per-gate buckets | **NO** |
| **Net-debit (B2, roll_leap hard-block)** | `~4010` / roll_leap enforce | roll_leap → `[]` | `net_debit_roll` | **NO** |
| **roll_leap advisory dispatch block** | `~1654` / `~2779` | tagged `dispatch="advisory"`, not placed | (advisory tag) | **NO** (status stays `roll_leap`) |
| Terminal-DTE time release | `_terminal_dte_time_release ~2908` | promotes hold→roll_short / close_short | warnings | **YES** (pre-write) |
| Deep-OTM early release | `_deep_otm_early_release ~3090` | promotes hold→roll_short | warnings | **YES** (pre-write) |
| LEAP hard-rule promotion | `_promote_to_roll_leap_if_hard_rule ~3163` | roll_short→roll_leap | warnings | **YES** (pre-write) |
| Halfway-roll cooldown | `_recent_halfway_roll_cooldown ~3221` | roll→hold | warnings | **YES** (pre-write) |
| LLM HOLD/WATCH deference (B1) | `_deterministic_roll_allowed ~2862` | skips deterministic roll | — | n/a (already `hold`) |
| Auto-exec caps / risk / reprice consent | `ceo_graph.py`, `_pmcc_combo.py` | dispatch-layer only | various | **NO** (dispatch only) |

The four **NO** rows that emit a real ABORT (earnings, sparse-chain, net-debit, advisory) are the ones
that make the tile misleading.

### 2.5 Recommended enhancement (Issue 2)

**Compute a single EFFECTIVE status post-all-gates and drive BOTH the tile and the panel from it.**
Two viable placements (recommend A as the durable fix, B as a cheap interim that already fixes the
reported earnings case):

- **A — scan-time write-back (single source of truth; matches the "one timestamped decision" intent).**
  In `scan()`/`judgment_pass()`, after order build, record the *effective* outcome for each symbol —
  either overwrite `status` with the effective action or (safer, additive) add
  `effective_status` + `suppressed_reason` fields to the decision record and have the tile
  (`data.py:3350`) and panel prefer `effective_status` when present. The scan already knows the
  outcome: it holds the built orders and the abort reason (`_last_roll_abort` / `last_roll_abort_reason`).
  Example effective labels: `EARNINGS WINDOW`, `SUPPRESSED`, `CAN'T PRICE`, `ADVISORY (manual)`.
  This guarantees tile==panel and needs no per-render broker work.

- **B — one shared render-time effective-status helper.** Extract a single function used by both
  `_build_pmcc_tile_status` (tile) and `_render_pmcc_record_panel` (panel) that starts from
  `rec["status"]` and applies the **cheap, pure** gates — foremost the earnings gate
  (`_earnings_gate_state` is a `get_next_earnings` lookup, no broker pull), plus terminal-DTE — and
  downgrades the label to the effective state. Buildability (sparse-chain / liquidity / can't-price)
  is only known after a pricing attempt, so fold in the last `pmcc_pricing.cached(...)` result
  (`buildable=False` + `estimate_reason`) when present — which, once Issue 1's extension runs
  `price_and_stash` for rolls, is exactly the abort reason. This keeps a single computed status so
  tile and panel cannot diverge.

Note the natural convergence: **Issue 1's extension makes the panel truthful for suppressed rolls
(shows the abort reason instead of "Re-analyze"), and Issue 2's shared effective-status makes the tile
truthful (shows EARNINGS WINDOW instead of ROLL SHORT EARLY).** Doing A (or B) so both read the same
computed status is the "can never disagree" guarantee the task asks for.

---

## Deliverable status

Read-only trace complete against live `prod-live e113843`. No code changed. Recommend review of the
two enhancement approaches before any build; the base-correction above should also inform how future
PMCC-division sessions pick their worktree base (prod-live, not main-wip).
