# E1·4 — cancel_order(order_id) -> bool

**Date:** 2026-06-13
**Branch:** `polymarket-e1-4-cancel-2026-06-13` (base clean main `8c21705`; E1·4 is independent of
the order-build path, so off main is correct)
**Mode:** BUILD + TEST, mocked/fundless, **UNMERGED**. No real cancel, no funds, no deploy, no prod
write. Disclosure per `82fda13`.
**Increment:** E1·4 of the E1 plan — `cancel_order`. (quote = E1·5, assembly = E1·6, preflight =
E1·7 — NOT here.)

---

## Phase A — the two unknowns (resolved)

1. **`order_id` form → pass-through, no mapping.** Our `FillEvent.order_id` IS the CLOB orderID
   (`place_order` sets it from the `post_order` response, E1·3), and `client.cancel(order_id)`
   takes that id directly (DELETE `/order`, body `{"orderID": order_id}`; SDK `endpoints.py:
   CANCEL="/order"`). No `int()` parse (unlike tastytrade's int ids) and no id translation.
2. **cancel response shape → grounded in the CLOB cancel-orders docs:**
   `{"canceled": [ids], "not_canceled": {id: reason}}`. An order is canceled iff its id is in
   `canceled`. (Corroborated by py-clob-client issue #316 — cancel returns order ids. The SDK is a
   thin wrapper, so the shape comes from the API, not the SDK.)

## Implementation (`trading_corp/brokers/polymarket_live.py`)

`async cancel_order(client, order_id: str) -> bool` — mirrors `tastytrade.py:273-285`:
- empty id → `False` (don't call); `client.cancel(oid)` via `asyncio.to_thread`;
- **conservative success rule:** `True` **only** when `oid` is in the response's `canceled` list;
  a `not_canceled` entry, an unrecognized/empty/non-dict response, or **any exception → `False`**.
  **Never raises** (the `Broker` contract is `-> bool`). Rationale: a live order we *wrongly*
  believe canceled is worse than a needless retry, so we only claim success on the clear signal.
- **No SDK import** (just `client.cancel` + a dict check) → fully box-testable, no `importorskip`.
- **`cancel_all()` deliberately NOT built:** the copy loop cancels no CLOB orders in bulk (its
  `.cancel()` calls are asyncio task lifecycle, not orders — verified by grep); a bulk/kill-switch
  cancel belongs to **E4** if ever needed. (Avoid building unused surface.)

## Tests (mocked, fundless)

- **Box gate: 36 passed, 5 skipped.** The 6 new cancel tests (all box-testable, no SDK): success →
  `True` (+ asserts the CLOB orderID is passed through unchanged), `not_canceled` → `False`, id
  absent from `canceled` → `False`, exception → `False` (**never raises**), unrecognized/`None`
  response → `False`, empty id → `False` without calling `cancel`. (The 5 skips are the
  pre-existing E1·2/E1·3 SDK tests.)
- **Full same-env gate (E1·4 vs pristine main `8c21705`, `git stash`):** `EDITED=31 == PRISTINE=31`,
  **diff empty → zero new regressions** (purely additive: extends the module + test; no other file
  touched).
- **Real `client.cancel` never called against the live CLOB** (mocked only); no funds.

## Carried-forward

- The exact cancel response shape is grounded from docs but **re-confirmed at the operator-gated $1
  shakedown** (alongside E1·3's status strings). The conservative bool (True only on a clear
  `canceled` signal) is safe under shape drift.
- (Standing) E1·1 Linux-OS signing confirmation; E1·2 `activity.asset → extra["token_id"]` (E2);
  E1·3 status/`size_matched` semantics + GTC-vs-FOK for copies.

## Phase D — status

- `polymarket_live.py` (+`cancel_order`) + `tests/test_polymarket_live_broker.py` (+6 tests) + this
  report, committed on the branch. **UNMERGED.** No deploy/merge.
- **Hard stops honored:** real `client.cancel` never called (mocked only); contract is `-> bool`
  and it **never raises**; no funds/cancel; stayed in-slice (no quote/assembly/preflight); response
  shape **grounded, not guessed**; no prod write; zero pytest regressions.

## Next — E1·5

Quote/snapshot consolidation onto the SDK (retire the unverified raw-httpx `/last-trade-price` in
`brokers/polymarket.py:415-486`), read-only/fundless.

---

*E1·4 artifact — committed unmerged on `polymarket-e1-4-cancel-2026-06-13`. Builds on E1·1–3 (main
`8c21705`). Sources: [py-clob-client #316](https://github.com/Polymarket/py-clob-client/issues/316),
Polymarket CLOB cancel-orders docs.*
