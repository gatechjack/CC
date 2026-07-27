# PMCC stale-tile-after-execution — FIX (held for deploy)

**Date:** 2026-07-27 · **Branch:** `claude-pmcc-tilefix-2026-07-27` (off **prod-live** e4219b3)
**Investigation:** `reports/2026-07-27_pmcc_tile_stale_rollshort_investigation.md` (commit a9efeb6 here / 725be49 orig)
**Fix commit:** 8b784b6 · **Scope:** additive, decision-and-render layer only. No order-placement,
`auto_execute`, or halt change; ref_id idempotency kept as the backstop. No re-roll — TSLA position untouched.

---

## Base-ref note (drift-safe)

Task said "base on prod-live (== deployed prod)". Memory says prod actually *runs* `claude-2026-07-26`
(0bdc3e0), 16 ahead of the `prod-live` tag (e4219b3). I verified: among all PMCC-relevant files, **only
`web/data.py` differs** between the two (the PM-whales epoch work) — `routes.py`, `_pmcc_status.py`,
`pmcc_pair.html`, `division.html` are byte-identical. My fix **does not modify `data.py`** (FIX 2 *reuses*
`_build_pmcc_tile_status`), so every file I touch is identical across both bases → basing on `prod-live` is
drift-safe here and deploys cleanly onto what prod runs.

---

## The three fixes

### FIX 1 — execution is now a writer, with a scan-overwritable `executed` source

`execute_pair_orders` (routes.py:1281-1303): on a **terminal `filled`** outcome
(`if any(r["outcome"] == "filled" for r in results)`), it calls
`record_pmcc_decision(sym, status="hold", source="executed", …)`. The standing ROLL SHORT is consumed, so
the next record read renders HOLD (non-actionable → no Approve button).

**The critical precedence (`_pmcc_status.should_write`):** `executed` is asymmetric on purpose —

| | incoming write | as a stored record |
|---|---|---|
| `expert` | always wins | **sticky** for 8h vs scans |
| `executed` | **always wins** (terminal fact — consumes even a fresh expert ROLL SHORT) | **scan-overwritable** (treated like `scan`) |
| `scan` | wins unless a *fresh expert* is present | — |

So a filled roll flips the tile to HOLD immediately, **and** the very next scan (10:30 / 15:00 / …) can
re-raise a signal if the just-rolled position moves — **no 8h blind spot** on the position we just rolled.
This is exactly the failure mode the task called out (roll TSLA at 9:47, it rallies by noon, the short is
threatened → a sticky expert-hold would suppress the 12:xx scan; `executed`-hold does not).

Only writes on a genuine fill — not rest / no-fill / reject. Best-effort (a status-write failure never
breaks the execute response).

### FIX 2 — propagate the decision to the tile with no page reload

New helper `_pmcc_tile_badge_oob(templates, deps, symbol)` (routes.py) renders an
`hx-swap-oob="true"` fragment `<span id="pmcc-badge-{SYMBOL}" …>` from the **same** `_pmcc_badge.html`
partial the row uses (so the OOB copy and the row can't drift). It is appended to **both** responses:
- the **execute** response (routes.py:1308-1311), after the `executed`-hold write, and
- the **Re-analyze** (`?force=1`) response (routes.py:975), after the existing expert write.

HTMX pulls the OOB element out of the response and swaps it into the left-rail `#pmcc-badge-{SYMBOL}` by id
while the main content still swaps into `#pair-analysis` — so the tile badge (and thus the Approve
affordance the row implies) refresh from the just-written record with **no full page reload**.

Template plumbing: badge markup extracted to `partials/_pmcc_badge.html`; `pmcc_pair.html` wraps it in
`<span id="pmcc-badge-{{ pair.underlying }}" class="contents">` (`display:contents` keeps the flex layout
byte-identical to the previous unwrapped badge). The OOB helper is best-effort — returns `''` on any error
or when the row isn't in the DOM (HTMX silently no-ops), so it can never break the response.

### FIX 3 — kill the false "tile & panel in sync" claim

`_pmcc_status_banner` (routes.py:4537-4544): removed the unconditional `tile &amp; panel in sync` string.
The fresh banner now shows only the factual `latest {source} decision · {age}h ago`. (It was an assertion,
never a check; and once FIX 2 refreshes the tile they *are* in sync, so the claim is redundant — a claim
that can be wrong is worse than none.)

---

## Diff (6 files, +279 / −44)

```
trading_corp/agents/divisions/_pmcc_status.py      docstring + should_write: 'executed' precedence
trading_corp/web/routes.py                         FIX1 write, FIX2 helper+2 appends, FIX3 banner
trading_corp/web/templates/partials/_pmcc_badge.html   NEW — shared 3-state badge partial
trading_corp/web/templates/partials/pmcc_pair.html     badge → #pmcc-badge-{sym} container + include
tests/test_pmcc_tile_status.py                     +5 precedence tests
tests/test_pmcc_tile_render.py                     +5 banner/OOB tests
```

---

## Tests (+10; the scan-overwritable precedence is called out explicitly)

Precedence (`test_pmcc_tile_status.py`):
- `test_executed_always_overwrites_as_incoming` — `executed` beats absent / scan / **fresh expert** as an
  incoming write (consumes the acted-on decision).
- **`test_executed_record_is_scan_overwritable_within_window`** + **`test_executed_hold_overwritten_by_later_scan_but_expert_is_not`**
  — THE critical case: a stored `executed`-hold IS overwritten by a scan 1h later (no 8h blind spot),
  while a fresh manual-`expert` hold is NOT overwritten by a scan within 8h (expert stickiness intact).
- `test_executed_write_consumes_fresh_expert_roll_short` — the Re-analyze-then-Approve ordering: a filled
  roll consumes a fresh expert ROLL SHORT → record becomes `hold/executed` (Approve won't re-render).

Render / route (`test_pmcc_tile_render.py`):
- `test_status_banner_fresh_has_no_in_sync_claim` — "in sync" gone; factual line present.
- `test_oob_tile_badge_fragment_reflects_executed_hold` — OOB fragment carries `id="pmcc-badge-TSLA"` +
  `hx-swap-oob="true"` + `HOLD` (proves execute/Re-analyze refresh the tile without reload).
- `test_oob_tile_badge_fragment_no_signal_when_absent`, `test_oob_helper_never_raises_on_bad_deps`,
  `test_badge_partial_renders_stale_state`, `test_pmcc_row_and_badge_templates_compile`.

**Affected files: 31/31 pass** (21 pre-existing + 10 new).

## Regression — apples-to-apples vs prod-live

Full suite on prod-live (e4219b3, before the fix): **61 failed** (pre-existing env/dependency artifacts of
the store-Python-3.14 harness — bitunix / iron_condor / robinhood_multi_leg / prediction_markets_dashboard
/ webhooks / paper_run_tooling / ic_grader; **no** PMCC tile tests). Clean isolated full re-run on the fix
branch: **61 failed — the identical set** (`Compare-Object` = 0 differences), i.e. **zero NEW failures**.
(A first branch run showed one extra failure —
`test_position_state_sanity_poll::test_loop_runs_multiple_ticks_under_normal_state`, an async 0.03s-interval
loop asserting >=3 ticks in 0.15s wallclock — but only because it ran *concurrently* with the baseline
suite; it passes in isolation, touches nothing in this change, and the clean isolated re-run reproduced the
baseline set exactly.)

---

## Broker position — untouched (read-only)

Re-confirmed via Robinhood MCP (acct 461391328): one TSLA short `620e0f68` ($335C, qty 1, exp 2026-07-31,
opened 13:47:30Z) + the intact 2027-01-15 LEAP; order `6a676172` filled once (+$162), `canceled_quantity=0`,
no duplicate. This build placed / modified / cancelled nothing.
