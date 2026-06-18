# BitUnix native multi-TP bracket — API research (read-only) — 2026-06-17

**Question:** the bot places each TP as a standalone reduce-only LIMIT (via `/futures/trade/place_order`) —
messy: broke the SL-trail (404), weak fill tracking (relies on reconciler/auto-book). The operator routinely
sets **3 TPs + an auto-reducing SL on one position in the UI**, so the venue clearly supports a native multi-TP
bracket. Is the bot's separate-limit approach the ONLY way to get 3 TPs, or is there a NATIVE multi-TP mechanism?

## ANSWER: a native multi-TP bracket EXISTS. The separate-limit approach was an implementation artifact.

BitUnix has a dedicated **TP/SL order family** (`/api/v1/futures/tpsl/...`) — entirely distinct from the
standalone reduce-only LIMIT orders the bot places via `/futures/trade/place_order`. These are **venue-managed,
position-tied TP/SL orders** (what the UI uses) with native OCO + auto-reduce.

### The TP/SL order family (the native mechanism)
| Endpoint | Method | Purpose | Multi-TP? |
|---|---|---|---|
| `/api/v1/futures/tpsl/place_order` | POST | **TP/SL order with PARTIAL `tpQty`/`slQty`** (qty in base coin, independent of full position) | **YES — call N times → N partial TP legs on one position** |
| `/api/v1/futures/tpsl/position/place_order` | POST | **Position TP/SL** — ONE per position, **no qty** → "closes based on position quantity *at that time*" | the **auto-reducing** full-position SL |
| `/api/v1/futures/tpsl/position/modify_order` | POST | **Modify the Position TP/SL** (the SL-trail target) — symbol + **positionId (required)** + tpPrice/slPrice | (the corrected SL-move endpoint) |
| `/api/v1/futures/tpsl/modify_order` | POST | Modify a TP/SL *order* by `orderId` (move/resize a single TP leg) | per-leg edits |
| `/api/v1/futures/tpsl/get_pending_orders` | GET | List the active TP/SL orders on a position | **clean fill tracking** |
| `/api/v1/futures/tpsl/get_history_orders` | GET | Historical TP/SL orders | post-fill booking |

### How a native 3-TP + auto-reducing-SL bracket is built (the UI's mechanism)
- **3 TP legs:** three `POST /api/v1/futures/tpsl/place_order` calls, each `{positionId, tpPrice, tpQty=partial, tpStopType, tpOrderType, tpOrderPrice?}` — `tpQty` = the 0.25 / 0.50 / 0.25 split of the position. Each is a venue-native TP order that closes only its partial qty when hit.
- **Auto-reducing SL:** one `POST /api/v1/futures/tpsl/position/place_order` with `slPrice` only (no qty). The Position TP/SL "closes based on the position quantity **at that time**" → it automatically covers the *remaining* position as the TP legs fill. **This is the native auto-reduce the operator sees in the UI** — confirmed by the doc's own wording, not inferred.
- **SL trail-to-breakeven:** `POST /api/v1/futures/tpsl/position/modify_order` (price-only). This is the **correct** path for the bot's `modify_position_sl` — the bot 404'd because it used the wrong path `/tpsl/modify_position_tp_sl_order` (does not exist).
- **Fill tracking / OCO:** `get_pending_orders` shows which legs remain; native OCO cancels the SL when the position closes (no stale). This **replaces** the reconciler-divergence + P2 auto-book hack the validation exposed.

## Why the bot's current approach is fragile (root cause)
The bracket uses `place_resting_reduce_only_limit` → `/futures/trade/place_order` with `reduceOnly=true` — a
**standalone LIMIT order**, NOT a TP/SL order. Standalone reduce-only limits are not part of the position's
TP/SL config, so: (a) no native OCO with the attached SL, (b) the SL doesn't see them as TP fills (the
"auto-reduce" assumption rode on the *position-attached* SL, which works, but the trail was broken), (c) the bot
gets no clean TP-fill callback → reconciler/auto-book. All three dissolve if the bracket is rebuilt on the
**tpsl order family**.

## REVISED fix recommendation (supersedes the "native = single TP, tradeoff" conclusion)
**Rebuild the bracket on the native TP/SL order family — ladder AND robustness, no tradeoff:**
1. On entry fill: place the **auto-reducing SL** via `/tpsl/position/place_order` (slPrice only). *(Or keep the
   B1 entry-attached slPrice — but the Position TP/SL is the trail-able, modifiable one.)*
2. Place the **TP ladder** as N × `/tpsl/place_order` with partial `tpQty` (0.25/0.50/0.25) — native, position-tied.
3. **SL-trail:** `/tpsl/position/modify_order` (fix the 404 path + always send `positionId`).
4. **Track fills** via `get_pending_orders` (drop the reconciler-divergence/auto-book crutch for the managed path).
This is a `bitunix.py` + bracket-module change → a future drift-gated redeploy. NOT done here (research only).

## VERIFY-ON-LIVE (small, before/with the rebuild — do NOT guess these)
- **Exact UI call sequence:** capture the BitUnix UI's network tab when the operator sets 3 TPs + SL — confirm it's `tpsl/place_order` ×3 (partial qty) + `tpsl/position/place_order` (SL), and the order/coexistence. (Docs confirm the *capability*; the live capture pins the *exact* flow.)
- **SL auto-reduce with coexisting TP/SL orders:** confirm the Position-TP/SL SL auto-reduces while N `tpsl/place_order` TP legs are also resting (no 30038, qty tracks down per fill).
- **positionId source:** all `tpsl/*` calls need `positionId` (from `get_pending_positions`), which the bot must thread through (the modify 404 also stemmed from conditional positionId).

## Bottom line
The "3 separate reduce-only limits → fragility" was **the wrong implementation, not a venue limit.** BitUnix
natively supports a multi-TP bracket (partial-qty TP/SL orders + an auto-reducing Position SL + OCO + lifecycle
queries) — exactly what the operator does manually. The rebuild gets the full ladder with manual-UI robustness.

Sources: [Place TP/SL Order](https://www.bitunix.com/api-docs/futures/tp_sl/place_tp_sl_order.html) · [Place Position TP/SL Order](https://www.bitunix.com/api-docs/futures/tp_sl/place_position_tp_sl_order.html) · [Modify Position TP/SL Order](https://www.bitunix.com/api-docs/futures/tp_sl/modify_position_tp_sl_order.html) · [Modify TP/SL Order](https://openapidoc.bitunix.com/doc/tp_sl/modify_tp_sl_order.html) · [Get Pending TP/SL Order](https://openapidoc.bitunix.com/doc/tp_sl/get_pending_tp_sl_order.html) · [Get History TP/SL Order](https://openapidoc.bitunix.com/doc/tp_sl/get_history_tp_sl_order.html) · [Place Order](https://www.bitunix.com/api-docs/futures/trade/place_order.html)
