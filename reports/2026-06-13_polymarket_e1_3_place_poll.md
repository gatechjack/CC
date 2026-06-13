# E1·3 — place → poll → FillEvent path

**Date:** 2026-06-13
**Branch:** `polymarket-e1-3-place-poll-2026-06-13` (base **E1·2** `236f43b`, NOT bare main — keeps
`polymarket_live.py`)
**Mode:** BUILD + TEST, mocked/fundless, **UNMERGED**. No funds, **no real `post_order`** (mocked
client only), no deploy, no prod write. Disclosure per `82fda13`.
**Increment:** E1·3 of the E1 plan — the `place_order` body. (cancel = E1·4, quote = E1·5, broker
assembly = E1·6 — NOT here.)

---

## The genuine unknown (resolved via docs): the CLOB status/fill model

`py_clob_client` 0.17.5 is a **thin REST wrapper** — `post_order`/`get_order` return raw API
dicts and the SDK defines **no order-status constants** (grep of the SDK = nothing). So the
status strings + fill field come from the live Polymarket CLOB API, grounded here from the public
docs (not guessed):
- **post_order placement status:** `live` (resting), `matched` (matched immediately), `delayed`
  (async match), `unmatched` (marketable but no match). [create-order doc]
- **get_order fields:** `status`, `size_matched` (filled amount), `original_size`, `price`, `side`.
  [get-order doc]

**Defensive design** (minimizes reliance on the exact strings): status is **normalized to
lowercase**, and **`size_matched` is the source of truth for filled qty** (not the status label).

## Implementation (`trading_corp/brokers/polymarket_live.py`)

Mirrors the tastytrade place→poll-to-terminal→FillEvent template (`tastytrade.py:398-465`):
- `place_order(client, order, *, market_fetcher, timeout, interval)` — map (E1·2) → `create_order`
  (sign) → `post_order(signed, OrderType.GTC)` → poll → `FillEvent`. Raises `OrderPlacementError`
  on a rejected (`success=false`) or `unmatched` placement (never polls a rejected order).
- `_poll_order_to_fill(...)` — **pure** (no SDK; `get_order` injected/mocked; lazy `FillEvent`
  import): polls until terminal (status ∉ {live,delayed}) or fully filled (`size_matched >=
  original_size`) or `timeout`. Honest terminal mapping:
  - filled (full **or** partial, `size_matched > 0`) at terminal or at timeout → `FillEvent` for
    the **filled portion** (`qty = size_matched`, `price`, `side`, `venue="polymarket"`);
  - zero fill at a terminal non-fill status (cancelled/unmatched/expired) → `OrderPlacementError`;
  - zero fill still resting (live/delayed) at `timeout` → `TimeoutError`.
  - **No phantom FillEvent** for an unfilled order.
- Sync `py_clob_client` calls run via `asyncio.to_thread` (the live path never blocks the loop).
- `OrderType`/`FillEvent` imported lazily → the module + the pure poll logic import without the
  SDK (gate-safe). **`post_order` is real-when-live; here it is exercised only via a mocked client.**
  The `--dry-run` skip lives at `data_exec.place()` (E2); the broker's paper/live gating is E1·6.

## Tests (mocked, fundless)

- **Box gate (py3.14, no SDK): 30 passed, 5 skipped.** The 7 new poll tests pass and assert
  **actual** `FillEvent` values (`qty == size_matched`, `price`, `side`, `venue`, `order_id`):
  full fill, **partial-at-terminal** (cancelled w/ partial → filled portion), **partial-at-timeout**,
  zero-fill cancelled → `OrderPlacementError`, zero-fill timeout → `TimeoutError`, status-casing
  (`MATCHED`), and waits-through-`live`-then-fills (3 polls). The 5 SDK tests `importorskip` cleanly.
- **py3.12 venv (pinned SDK):** `place_order` success → `FillEvent` (post_order called with
  **`OrderType.GTC`**, `create_order` signed), rejected → `OrderPlacementError` (never polled),
  unmatched → `OrderPlacementError`. **Real `post_order` never called** (mocked client).
- **Full same-env gate (E1·3 vs pristine E1·2 `236f43b`, `git stash`):** `EDITED=31 == PRISTINE=31`,
  **diff empty → zero new regressions** (purely additive: extends the module + test file; no other
  file touched).

## Carried-forward (do NOT drop)

1. **CLOB status strings + `size_matched` semantics are confirmed only at the operator-gated $1
   shakedown.** The SDK doesn't define them; the design is grounded in the public docs + made
   defensive (lowercase-normalized status, `size_matched` as fill-qty truth). The real-order
   observation closes this.
2. **`size_matched` may overstate tokens actually received** (rounding/fees — py-clob-client
   issue #245): `size_matched` ≠ guaranteed on-chain balance. Broker-truth **reconciliation is E5**;
   E1·3's `FillEvent.qty` reflects the reported `size_matched`.
3. **GTC copy semantics:** a GTC limit rests; a copy that doesn't fill within `timeout` →
   `TimeoutError` → **no fill** (honest). Whether copies should use a marketable order type (FOK/FAK)
   instead of GTC is an **E2/strategy** design decision — noted, not built here.
4. (Standing) E1·1's Linux-OS signing confirmation + E1·2's `activity.asset → extra["token_id"]`
   E2 follow-on still apply.

## Phase D — status

- `polymarket_live.py` (extended) + `tests/test_polymarket_live_broker.py` (extended) + this report,
  committed on the branch. **UNMERGED.** No deploy/merge.
- **Hard stops honored:** real `post_order` never called (mocked only); ephemeral/mock keys; no
  funds/placement; stayed in-slice (no cancel/quote/assembly); branched off E1·2 (not bare main);
  no prod write; zero pytest regressions.

## Next — E1·4

`cancel_order(order_id) -> bool` wrapping `client.cancel(order_id)` (mocked/fundless), mirroring
`tastytrade.py:273`.

---

*E1·3 artifact — committed unmerged on `polymarket-e1-3-place-poll-2026-06-13`. Builds on E1·2
(`236f43b`) + E1·1. Sources:*
*[create-order](https://docs.polymarket.com/developers/CLOB/orders/create-order),
[get-order](https://docs.polymarket.com/developers/CLOB/orders/get-order),
[py-clob-client #245](https://github.com/Polymarket/py-clob-client/issues/245).*
