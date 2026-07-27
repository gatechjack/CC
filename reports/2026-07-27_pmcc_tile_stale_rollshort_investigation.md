# PMCC TSLA tile stayed "ROLL SHORT" after the roll filled — read-only investigation

**Date:** 2026-07-27 · **Branch:** `claude-2026-07-27` (off `claude-2026-07-26` @ 0bdc3e0, == prod tile-status code)
**Scope:** READ-ONLY. Nothing placed / modified / cancelled / re-rolled. Broker position confirmed correct and untouched.
**Feature under test:** tile/expert status unification (PMCC bundle deployed 2026-07-25, e4219b3).

---

## TL;DR

The roll is correct — **one** TSLA $335C short, one order `6a676172`, +$162, no duplicate. The bug is entirely
in the **status-render layer**, and it is **three compounding defects**, not one:

1. **Execution is not a writer of the unified decision record.** `execute_pair_orders` fills the roll but never
   calls `record_pmcc_decision`. So the persisted record stays `status=roll_short, source=scan` — a filled action
   does not consume its own decision. (Q5)
2. **The tile is a one-shot server render with no re-fetch path.** `#pair-list` is rendered once at full-page load
   and has **no** `hx-trigger` poll and **no** OOB swap. Re-analyze / execute HTMX responses target only
   `#pair-analysis` (the right rail). So a record change made via a panel action never reaches the tile badge
   without a **full page reload**. (Q1 / Q2)
3. **"tile & panel in sync" is a hardcoded string, not a check.** `_pmcc_status_banner` prints it unconditionally
   in the fresh branch; it never compares the tile's rendered value against the panel's record. (Q4)

The deploy's premise ("one record ⇒ they can't disagree") is true at the **data** layer and false at the **render**
layer: same record, but the tile's rendered copy and the panel's rendered copy are produced at different times with
no propagation between them. The feature eliminated *source*-divergence but not *time*-divergence.

---

## Ground truth (Robinhood MCP, re-verified read-only this session, acct 461391328)

- **Order `6a676172`** — `state=filled`, `direction=credit`, `processed_quantity=1`, `canceled_quantity=0`,
  `processed_premium=162`. Legs: SELL open **$335C 2026-07-31 @1.66**, BUY close **$340C 2026-07-27 @0.04**.
  `placed_agent=user`. One order, one fill. **No duplicate.**
- **Positions:** TSLA short `620e0f68` — $335C, **qty 1**, credit −166 (=$1.66/sh), exp 2026-07-31, opened
  2026-07-27T13:47:30Z. TSLA long LEAP `639b5a25` — 2027-01-15 call, qty 1, opened 2025-11-24 (intact).
  ref_id idempotency absorbed the 2nd Approve click exactly as designed. **Position correct and untouched.**

---

## The five questions

### Q1 — Do the tile badge and the Expert panel read the SAME `latest_decision` record?

**Yes — the same record, via the same function.** Both resolve
`agent_state[agent="pmcc_robinhood", key="latest_decision:TSLA"]` through `_pmcc_status.load_decision`:

- Tile: `web/data.py:3321` (`_build_pmcc_tile_status` → `load_decision`), attached per pair at `data.py:3459`.
- Panel: `web/routes.py:4560` (`_render_pmcc_record_panel` → `load_decision`), and the force=1 path.

So the disagreement is **not** two different sources. It is a **stale server-render of the tile that never
re-fetched** after the record changed. The tile badge only renders a coloured "ROLL SHORT" pill when
`us.state == 'fresh'` (`pmcc_pair.html:62`), i.e. the record it read at page-load time said
`status=roll_short` and was < `staleness_hours` (8h) old — which the morning scan verdict was.

### Q2 — Did the Re-analyze (force=1) actually WRITE `source=expert` HOLD to the persisted record?

**Yes.** `routes.py:947-956` (inside the force=1 branch of `division_pair_analysis`) calls
`_pmcc_status.record_pmcc_decision(sym, status=analysis.action, source="expert", …)`. Expert **always** overwrites
(`_pmcc_status.should_write`), so the record was updated to `status=hold, source=expert`. The panel returned that
HOLD verdict with a hardcoded `status_banner(state="fresh", source="expert")`.

**Why the tile didn't pick it up:** the write is correct; the *propagation* is missing. The Re-analyze button is
`hx-get=".../pair-analysis/TSLA?force=1" hx-target="#pair-analysis" hx-swap="innerHTML"` (`routes.py:4479-4480`).
It swaps only the right rail. The tile badge lives in `#pair-list` (`division.html:123`), which is rendered once in
`build_division_view` and has **no** auto-refresh — unlike bitunix/sfp/pead panels, the PMCC position rows carry no
`hx-trigger="every Ns"` and there is no `hx-swap-oob`. So the record is HOLD, the panel shows HOLD, but the tile's
DOM copy is frozen at the pre-Re-analyze render (ROLL SHORT) until a **full page reload**.

### Q3 — Is the tile "ROLL SHORT" the unified status, or a residual (un-retired) `recommended_action`?

**It is the unified status**, not a residual pill. `pmcc_pair.html:55-85` renders `pair.unified_status`
(`us.status_label`, from `rec["status"]` → "roll_short" → "ROLL SHORT"). The old deterministic
`recommended_action` preview pill was **correctly retired from the tile** — the block that used to render it
(`{% set action_label, action_urgency = pair.recommended_action %}`) is gone from the deployed template, replaced
by the `unified_status` block. `recommended_action` still exists as a `PMCCPair` property (`data.py:285` / `:530`)
but is no longer rendered on the tile. So "ROLL SHORT" is a genuine unified **scan** verdict — just a stale in-DOM
copy of it. (The retirement worked; the refresh path is what's missing.)

### Q4 — Why is "tile & panel in sync" shown as true when they disagree?

Because it is an **unconditional assertion, not a comparison.** `_pmcc_status_banner` (`routes.py:4488-4512`) emits
the literal string `tile &amp; panel in sync` (`:4509`) in its `else` (= non-stale/fresh) branch. It takes only
`state`, `age_h`, `source` — it never reads the tile's rendered value and never compares anything. It asserts sync
purely because the **panel's own** record is fresh. When the tile's DOM copy is stale, the claim is simply false —
the code has no way to know, because it never checks.

### Q5 — Why doesn't an executed action consume/clear its own decision + disable its Approve button?

**Because execution never touches the decision record.** `execute_pair_orders` (`routes.py:1022-1278`), on a
successful fill, does exactly two post-fill things: appends `{"outcome": "filled", …}` to results
(`:1196` combo / `:1259` single) and pops the **LLM HTML cache** (`:1274-1276`). It then returns
`_render_execute_results` into `#pair-analysis`. There is **no** `record_pmcc_decision` call anywhere in the
execute path — confirmed: the only two writers repo-wide are the Re-analyze path (`routes.py:950`) and the scan
(`pmcc_robinhood.py:2138`). So after the roll fills:

- the record stays `status=roll_short, source=scan` (fresh) → the tile keeps showing ROLL SHORT, and
- any subsequent **non-force** row click renders `_render_pmcc_record_panel` from that record → the Approve button
  is re-rendered **live** (`actionable = action_raw not in ("", "hold", "watch")`, `routes.py:4413-4414`).

Nothing invalidates the standing ROLL SHORT decision or its button on fill. Only the 8h staleness window aging out,
or a later scan / manual Re-analyze, overwrites it. (The 2nd Approve click was caught downstream by ref_id
idempotency — the correct backstop — but the UI never should have offered it.)

### Side note (reframes one symptom): the short-leg "ROLL SOON" tag is NOT stale.

`pmcc_pair.html:165-167` renders "roll soon" purely from `short.dte <= 7`. The new $335C is **4 DTE**, so the tag
is calendar-accurate — it is a *different mechanism* from the decision record and is not reading stale data. It only
*reads* as contradictory next to a "hold and collect theta" verdict because DTE-proximity and recommended-action are
different axes. Flagging it as a distinct, lower-severity display-semantics item — do **not** lump it in with the
stale-record defect.

---

## Fix proposal (propose only — not built)

Goal: on execution of an approved action **and** on Re-analyze, the tile badge, the leg roll-tag context, the
Approve button, and the "in sync" indicator all refresh from the **same updated record immediately** — no stale
ROLL SHORT + live button after a fill. Keep ref_id idempotency as the backstop.

1. **Make execution a writer (closes Q5 — highest severity).** In `execute_pair_orders`, after a confirmed fill,
   call `_pmcc_status.record_pmcc_decision(sym, status="hold", source="expert", computed_at=now, …)` so a filled
   roll **consumes its own** ROLL SHORT and records the post-action resting state ("roll done → hold / collect
   theta"). Because expert always overwrites and `hold` is non-actionable, the next record-panel read renders **no**
   Approve button — the button self-disables on the next read without any special-casing. (Write it only for the
   `"filled"` outcomes; a risk-rejected / aborted / not-marketable result must leave the record unchanged.)

2. **Propagate the record change to the tile immediately (closes Q1/Q2 render-staleness).** The execute and
   Re-analyze responses should refresh that pair's tile badge, not just `#pair-analysis`. Cleanest with HTMX:
   return an **out-of-band** fragment (`hx-swap-oob`) for the pair's badge/row alongside the panel swap — a tiny
   partial that re-renders `unified_status` from the now-current record. (Alternatives: emit an `HX-Trigger`
   response header that a listener uses to re-GET the pair row, or re-render the whole `#pair-list`. OOB badge swap
   is the least disruptive and matches the "one record drives both" intent.)

3. **Make "in sync" true-by-construction, or stop asserting it (closes Q4).** Once (2) guarantees the tile is
   refreshed from the same record the panel just rendered, the "in sync" claim becomes true by construction — keep
   the label but only emit it *after* the OOB swap is wired. Until then, replace the unconditional
   "tile &amp; panel in sync" with the factual, already-true text it sits next to
   ("latest {source} decision · Xh ago"). Do not display an unconditional truth-claim about a DOM element the code
   does not refresh.

4. **(Optional, separate item) Decouple the "ROLL SOON" leg tag from raw DTE** if the operator wants it to track the
   decision rather than the calendar — gate it on `unified_status.urgency` instead of `short.dte <= 7`. Lower
   priority; it is currently accurate, just semantically orthogonal.

**ref_id idempotency:** unchanged — it stays the backstop that already absorbed the duplicate Approve. The fixes
above remove the *invitation* to double-approve; idempotency remains the guarantee if one still slips through.

---

## Evidence index (file:line, deployed code on this branch)

| Claim | Location |
|---|---|
| Tile reads the record | `web/data.py:3311-3337` (`_build_pmcc_tile_status`), `:3459` (per-pair attach) |
| Panel reads the same record | `web/routes.py:4553-4572` (`_render_pmcc_record_panel`), `:873-874` (non-force branch) |
| Re-analyze WRITES expert record | `web/routes.py:947-956` (force=1 branch) |
| Only two record writers exist | `web/routes.py:950` (expert), `agents/divisions/pmcc_robinhood.py:2138` (scan) |
| Tile badge = unified_status (not recommended_action) | `web/templates/partials/pmcc_pair.html:55-85` |
| recommended_action retired from tile (still a property) | `web/data.py:285`, `:530` (defined, unrendered) |
| "in sync" is a hardcoded string | `web/routes.py:4488-4512` (esp. `:4509`) |
| Execute never writes the record | `web/routes.py:1022-1278` (post-fill = results + `_pair_cache.pop` only, `:1274-1276`) |
| Approve button gate | `web/routes.py:4413-4414` (`actionable`), rendered `:4414-4449` |
| Re-analyze / execute target only `#pair-analysis` | `web/routes.py:4479-4480` (re-analyze), `:4418-4421` (execute form) |
| `#pair-list` has no auto-refresh | `web/templates/division.html:123-127` (no `hx-trigger`/OOB) |
| "roll soon" is DTE-derived, not stale | `web/templates/partials/pmcc_pair.html:165-167` |
