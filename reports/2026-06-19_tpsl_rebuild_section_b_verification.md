# Section-B verification — native `/tpsl/` bracket rebuild (live-trade behaviour)

**Date:** 2026-06-19 · **Branch:** `bitunix-tpsl-rebuild-2026-06-18` · **Mode:** READ-ONLY (§4)
**Disclosure:** agent read-only SSH `82fda13`. NO prod write / NO order action / NO deploy / NO
signed-or-live API call (journal + `audit_event` + `paper_trade_record` only) / NO git stash / NO
touching the live position / NO polymarket.

## Headline

The rebuild's **Position-SL leg works**, but the **TP-ladder placement is BROKEN by a new
response-parse bug** that blocked the whole point of this multi-leg trade. The trade then **stopped
straight out** (no TP ever filled), so the SL-trail-new-path, the auto-reduce, and the sign-bug-on-a-win
could not be positively exercised and remain **PENDING**. **Fail-soft held perfectly** — the B1
entry stop + the Position SL guarded the position, which closed cleanly at the stop, flat, with no
persistent orphan / divergence / halt.

> **FORK for the operator (I stop-and-report, do NOT fix/deploy):** every multi-leg bitunix entry on
> the current build places **zero** tracked TP legs and rides to SL/B1. Decide: halt new live bitunix
> entries until `place_tpsl_order` is patched, or accept SL-only behaviour interim. The fix is a
> one-spot response-shape correction (below).

## The trade

| field | value |
|---|---|
| order_id | `cb6b4d4a-20e4-4c91-b97a-08f09aa76965` |
| entry | BTC/USDT.P **SHORT**, fill **62633.0**, qty **0.0037969 BTC** (PREMIUM, `mc_a_redx`), `execution_mode=live` |
| entry venue id | `2067820598285643776` (`/futures/trade/place_order`, B1 `slPrice` attached) |
| positionId | `6977620864921650468` (captured into `Position.extra` — plumbing works) |
| SL / TP plan | SL **62858.0224**; tp1 62532.039 (0.25) / tp2 62431.578 (0.50) / tp3 62111.744 (0.25) |
| ladder computed | 3 legs (0.0009492 / 0.0018985 / 0.0009492) — qty 0.0037969 ≥ 0.0012 → full 3-leg tier ✓ |
| lifecycle | entered 04:03:42 → filled 04:03:44 → **stopped out 04:18:27** |
| close | `result=loss`, result_price **62858.2** (= SL), net ≈ **−$0.79 … −$0.88**, `exit_method=server_side_sl_B1` |
| engine | PID **2988577** (new rebuild process; `xvfb-run[2988591]`), active/running, NRestarts=0 |

The short was filled at 62633 and the price rose to the 62858 stop in ~14 min — it **never went into
profit**, so no TP leg would have filled even had the ladder placed correctly.

## Per-check verdict

### 1. TP legs rest as native `/tpsl/` orders (partial 0.25/0.50/0.25, no 30038) — ❌ FAIL
`bracket_placed` (audit 1233746): `legs_placed: 0`, `legs_planned: 3`, `tp_order_ids: {}`,
`degrade_note: ""`. `extra_json`: `bracket_tp_order_ids: {}`, `filled_legs: []`.

All three legs raised `bracket_tp_leg_failed` (audits 1233743/4/5, journal 04:03:44–45):
```
ERROR bitunix_observer: bracket tp1 leg place FAILED (SL still protects): 'list' object has no attribute 'get'
```
Root cause: in `BitunixBroker.place_tpsl_order` the line `venue_order_id = (data or {}).get("orderId")`
assumes a **dict**, but `/api/v1/futures/tpsl/place_order` returned a **list**. This is exactly the risk
the code itself flagged: *"VERIFY-ON-LIVE: the exact response shape (`orderId` field) … confirm the
field name on the first real placement."* The dict assumption was wrong.

- **NO 30038** occurred — the "no `TPSL_EXCEEDS_POSITION`" sub-claim holds; but the broader "TP legs
  rest" objective FAILS on a different (code) defect.
- **Orphan-order risk (indeterminate read-only):** the crash is on a *non-empty* list. `(data or {})`
  would no-op on an empty list `[]`; an `AttributeError` only fires on a **truthy** list → `_request`
  returned content → the 3 POSTs **very likely reached the venue and briefly rested untracked**. The
  reconciler is **position-level only** (`orphan_on_broker_count` tracks positions, not TP/SL orders),
  so it would not have surfaced untracked resting orders. Resolving this needs a signed
  `get_pending_orders` — **barred** under §4. **Operator follow-up.**

### 2. Position SL placed via `/tpsl/position/place_order` (auto-reducing, qty-less) — ✅ PASS
Journal 04:03:45:
```
INFO BitUnix tpsl/position/place_order: venue_order_id=6665511022185019736 positionId=6977620864921650468 BTCUSDT slPrice=62858.0224 (auto-reducing, no qty)
```
Recorded in `bracket_placed.position_sl_order_id` and `extra_json.bracket_position_sl_order_id` =
`6665511022185019736`; `bracket_position_id` = `6977620864921650468`. No `bracket_position_sl_failed`.
Qty-less position-level SL placed clean. (Note: `place_position_tpsl` shares the same
`(data).get("orderId")` pattern but the *position* endpoint returned a **dict** → worked. The
list-shape defect is specific to `/tpsl/place_order`.)

### 3. SL-trail uses `/tpsl/position/modify_order` — NO 404 — ⚠️ PASS (regression removed) / PENDING (new path not live-exercised)
- **Old 404 confirmed gone:** the ~22× failures
  `modify_position_sl failed (BTCUSDT -> 63406.6622): … 404 … on /api/v1/futures/tpsl/modify_position_tp_sl_order`
  were all the **OLD** process `xvfb-run[2926413]`, **20:11:19–20:33:33 Jun 18, pre-full-rebuild**,
  on trade `7d1a78dc`. The new process (`2988591`) logged **zero** modify-endpoint 404s. Source
  confirms the fix: `bitunix.py:2103` POSTs `/api/v1/futures/tpsl/position/modify_order` with a
  mandatory `positionId`.
- **New path NOT yet POSTed live:** the only post-rebuild SL-move (audit 1233906, 04:17:26)
  decided *"TP1+TP2 filled → SL to TP1"* but `moved: false` because the position was already flat
  (`current_qty: 0.0`). No `SL moved (price-only)` and no new `modify_position_sl failed` line → the
  endpoint was never actually called. (The reconciler's qty-heuristic also **mislabels a full stop-out
  as "TP1+TP2 filled"** — benign here, `moved:false`, no action — worth noting for #5/telemetry.)
- A positive live no-404 on the new path still awaits a real TP-fill→SL-move. This trade went straight
  to SL, so it could not provide one regardless of the TP bug.

### 4. Position SL auto-reduces on a partial fill — ⏸️ PENDING / NOT-OBSERVED
No partial fill: the position went full → 0 in one step (`close_fill_count: 1`, `current_qty: 0.0`)
via the B1 stop. Compounded by #1 (no TP legs tracked as resting). The never-tested-live auto-reduce
**remains never-tested**.

### 5. Clean fill-tracking + close (+ P2 result-sign bug) — ✅ clean close / ⚠️ sign-bug unresolved
Close booked via the P2 auto-book (audit `auto_book_server_side_close`, 04:18:27). `extra_json`:
`result_source=auto_booked_from_real_fill`, `pnl_basis=real_fill`, `slippage_unreconciled=false`,
`exit_method=server_side_sl_B1`, `close_fill_count=1`, real `exit_fee_usd=0.046515`,
`net_realized_usd=-0.8824`. `paper_trade_record`: `result=loss`, pnl −0.78958, result_price 62858.2.
- The **real-fill** auto-book (not a stop-level estimate; `slippage_unreconciled=false`) = the #1
  signed-fetch improvement working. Close was clean.
- `cb6b4d4a` is a **true loss correctly labelled `loss`** — it cannot test the win-mislabel direction.
- **The P2 result-sign bug is still demonstrable:** prior trade `7d1a78dc` booked `result='loss'` with
  `actual_pnl_dollars=+0.29822` (a **win** mis-signed). That trade ran on **old** code, so the rebuild's
  status is **not yet re-tested on a post-rebuild win**. Flag remains open.

### 6. B1 ↔ Position SL coexistence (no 30038) — ✅ PASS
The B1 entry-attached MARKET stop (`stop_price` 62858.0224, attached at entry) and the separate
`/tpsl/` Position SL (`6665511022185019736`, same price 62858.0224) **coexisted with no 30038** and no
`bracket_position_sl_failed`. They are **distinct orders** (separate venue id) at the same price; the
close was attributed to **B1** (`exit_method=server_side_sl_B1`). Open validation-window question (a)
resolves toward: *separate coexisting orders, benign, fail-soft confirmed* — the B1 stop is what
actually closed the position.

## Fail-soft / safety confirmation
- Reconciler ran every ~60 s (`position_state_reconciled`, `match_count=1`, orphan 0) from 04:04:18
  through 04:16:25; the 04:17:25 `position_state_divergence_detected` (`missing_on_broker_count=1`) was
  the **close itself**, resolved by the 04:18:27 auto-book → final reconcile clean (match 0, orphan 0).
- No halt latched; engine live/flat after close.
- Position never unprotected: B1 MARKET stop + Position SL both at 62858.02.

## Evidence index
- DB `data/trading_corp.db` (read-only): `paper_trade_record` (`cb6b4d4a`, `7d1a78dc`),
  `audit_event` kinds — `bracket_placed` 1233746, `bracket_tp_leg_failed` 1233743/4/5,
  `position_sl_update` 1233906, `position_state_divergence_detected` 1233905,
  `auto_book_server_side_close` 04:18:27, `position_state_reconciled` ×17.
- journald `trading-corp`: entry/fill/leg-fail/position-SL lines (04:03:42–45); 404 storm
  `xvfb-run[2926413]` 20:11–20:33 Jun 18 (old code); zero new-process 404s.

## Recommended follow-ups (operator-owned; NOT done here)
1. **BLOCKER —** fix `place_tpsl_order` response parsing to handle the venue's **list** response
   (extract `orderId` from the list, not `(data or {}).get(...)`). Until then no TP legs ever rest →
   #1/#3/#4 can never validate.
2. Audit the other `/tpsl/` parsers (`get_pending_orders`/`get_history`) for the same list-vs-dict
   assumption. (`place_position_tpsl` returned a dict → already OK.)
3. **Signed check (operator-only, barred for me):** confirm whether the 3 TP POSTs rested on the venue
   for ~14 min and were OCO-cancelled at stop-out, or never landed. Reconciler is position-level and
   won't catch untracked TP/SL orders.
4. P2 result-sign bug (`7d1a78dc` +$0.298 booked `loss`) still open — re-test on the first
   post-rebuild **win**.
5. Reconciler SL-move heuristic mislabels a full stop-out as "TP1+TP2 filled" (benign now; tighten if
   it ever gates a real action).

## Verdict summary
| # | Check | Verdict |
|---|---|---|
| 1 | TP legs rest as `/tpsl/` (no 30038) | ❌ FAIL — `place_tpsl_order` list-vs-dict parse bug; 0/3 placed (no 30038) |
| 2 | Position SL via `/tpsl/position/place_order` | ✅ PASS |
| 3 | SL-trail `/tpsl/position/modify_order` (no 404) | ⚠️ PASS (404 regression removed) / PENDING (new path not yet POSTed live) |
| 4 | Position SL auto-reduces on a partial | ⏸️ PENDING — no partial (stopped out full→0) |
| 5 | Clean close (+ P2 sign bug) | ✅ clean real-fill close / ⚠️ sign-bug unresolved (`7d1a78dc`) |
| 6 | B1 ↔ Position SL coexistence (no 30038) | ✅ PASS |
