# B-ARM Phase 1 bundle — build-through (local; joins TODAY's after-close Stage-2)

Worktree `cc-pmcc-wt` on `pmcc-scan-split-2026-07-24`, on top of Build A+B (`a703905`). Additive/
fail-closed only; `auto_execute` stays FALSE; **nothing deployed**. Three items, per-item commits.

| # | Item | Commit | Live path? | Tests |
|---|---|---|---|---|
| 6 | Startup options-tier check | `5c46493` | No | 6 |
| 5 | LIVE/PAPER badge (division + combo card) | `7c52789` | No | 9 |
| 2 | 401/429 fail-closed reconcile on the live submit | `71cb57a` | Yes (order path) | 8 |

## #6 — options-tier check (additive)
`RobinhoodBroker` captures `option_level` at bind + exposes it. `PMCCAgent._check_options_tier_once`
runs once (after the first `detect_existing_legs` in triage/scan): a LIVE broker below `option_level_3`
(roll_short is a spread) — or unverifiable — logs + audits `pmcc_options_tier_check`, so the gap shows at
startup instead of a live order reject. Never blocks/raises; paper handles skipped.

## #5 — LIVE/PAPER badge (observability)
`DivisionViewSnapshot.is_live` + `_division_is_live(broker)=not broker.paper`; a red "● LIVE — real
money" vs neutral "PAPER" badge on the division header and the PMCC combo-approval card, so an Approve
visibly shows real money. No order path touched.

## #2 — 401/429 fail-closed reconcile (live order path)
On a **no-id** submit result, `place_multi_leg` now distinguishes:
- **401** (session dead) → `_attempt_reauth(force=True)`, **then** reconcile.
- **429** (rate limited) → back off (`_RATE_LIMIT_BACKOFF_S`), **then** reconcile.
- **genuine reject** (session alive, not throttled) → raise `RobinhoodOrderError` as before.

**Reconcile** (`_reconcile_after_submit_failure`): match a recent RH option order by our deterministic
`ref_id` (verified against the payload; **fallback** = leg-identity set + qty + 120 s created-at window),
poll it to terminal, and **book ONLY a confirmed `filled`** via the shared `_build_fills_from_result`
(same identity attribution as the normal path — the item-1 fix is now single-sourced). Otherwise raise
`RobinhoodComboPending` (book nothing + alert). **NEVER synthesizes a fill; NEVER blind-retries the
submit (no double-place).** Fail-closed: a missed order is picked up by the next scan's broker
re-derivation. The `_place_option_order` single-leg guard and the atomic combo path remain intact.

Tests (`test_robinhood_order_reconcile.py`): 401→reauth→reconcile-finds→book; not-found→book-nothing+
pending; 429 backoff→book; **no-double-place (submit fires exactly once)**; genuine-reject regression;
ref_id-absent fallback match; found-but-not-filled→book-nothing; rate-limit signature parse. The existing
`test_place_multi_leg_handles_none_response` was made auth-explicit (a None response with a *live* session
is still a `RobinhoodOrderError` reject).

## Tests + regression
Per-item unit tests all green in isolation. **Apples-to-apples regression diff** (Phase-1 tip vs
`a703905`, same worktree): **51 failures each, `comm` diff EMPTY → zero new failures**. The 51 are the
known pre-existing set (22 `robinhood_multi_leg` `robin_stocks` isolation pollution + 4 empty-DB readiness
+ 25 other), identical at both revisions.

## GO / NO-GO
**GO** for all three — additive/fail-closed, tested, zero regressions, `auto_execute` untouched, IC path
unaffected. **#2 is on the live order path** but is strictly fail-closed (no synthesize, no double-place)
with a robin_stocks assumption to validate in boot-smoke: that `get_all_option_orders` surfaces `ref_id`
(the fallback covers the case it doesn't) — worth a one-time check at deploy.

**Deploy:** these three join **today's after-close Stage-2 bundle** — the deploy target moves to this
branch tip (report the final SHA below to the deploy prompt). Files: `brokers/robinhood.py`,
`agents/divisions/pmcc_robinhood.py`, `web/data.py`, `web/routes.py`, 2 templates.

Still HELD (separate gated deploys): Phase 2 (B-AE assignment monitoring — design first), Phase 3
(#1+#3 live-path enablement — confirm first), Phase 4 (#4 real-BP risk gate — build-through, own deploy).
