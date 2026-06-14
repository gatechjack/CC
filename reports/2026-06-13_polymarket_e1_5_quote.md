# E1·5 — quote/snapshot consolidation onto the SDK

**Date:** 2026-06-13
**Branch:** `polymarket-e1-5-quote-2026-06-13` (base clean main `8c21705`)
**Mode:** BUILD + TEST, read-only/fundless, **UNMERGED**. No funds, no live placement, no deploy,
no prod write. Disclosure per `82fda13`.
**Increment:** E1·5 of the E1 plan — consolidate `PolymarketBroker.quote` onto the SDK. (assembly =
E1·6, preflight = E1·7 — NOT here.)
**Topology:** off main `8c21705`; edits `brokers/polymarket.py` (quote), **disjoint** from E1·4's
`polymarket_live.py` cancel — both are unmerged off main and merge cleanly in either order.

---

## Phase A — current quote, consumers, and the price-semantics choice

**Current quote** (`brokers/polymarket.py:421-486`): `quote(symbol="slug:outcome") -> float`.
Step 1 resolves token_id via gamma `/markets?slug=` → `clobTokenIds` parallel to `outcomes`,
matched by outcome label. Step 2 was a **raw-httpx GET** to `{_CLOB_API}/last-trade-price?token_id=`,
parsing `data["price"]`, `0.0` on any error.

**Consumers (contract to preserve):** `data_exec.py:128` (dry-run fill synthesis), `portfolio.py:49`
(mark-to-market), `paper.py:166/192` (PaperBroker delegate), and the copy-trader drift check. All
expect `quote(symbol) -> float`, side-agnostic, `0.0 = unknown`.

**SDK price methods (0.17.5, verified in source):** `get_midpoint(token_id)` and
`get_price(token_id, side)` / `get_last_trade_price(token_id)` — all **Level-0 public reads** (no
`assert_level_2_auth`; the `__init__` docstring: *"Level 0: requires only the clob host url, allows
access to open CLOB endpoints"* → constructible **keyless**, no creds/funds).

**Price-semantics decision: MIDPOINT (`get_midpoint`).** Grounded:
- It's the **live-book fair value** — the right side-agnostic "current price" for all three
  consumers (drift check is directional; mark wants fair value; dry-run wants a reasonable synthetic
  fill), and it **preserves the single-float contract**.
- It **improves over last-trade**, which is a backward-looking single print (stale/thin).
- The **executable side** (`get_price(token_id, BUY)`) is *more precise for entry COST* (the
  operator's point) — but it needs a **side**, which `quote()`'s side-agnostic contract doesn't
  carry. Adding it would **break the contract** (hard stop). So the executable side is a **future
  side-aware method** the live broker (E1·6) / sizing (E2) calls directly — **not** `quote()`.

**Why the raw-httpx version was "unverified" (justifies the change, not churn):** the
`/last-trade-price` endpoint path + the `price` field were hand-rolled and **never verified against
the live API** (flagged in the item-6 plan); a shape mismatch silently returns `0.0`. The SDK
`get_midpoint` is the maintained Level-0 wrapper (the proven `py_clob_client` path family the
2026-05-29 spike used).

## Phase B — implementation (`brokers/polymarket.py`)

- Step 2 replaced with `return await asyncio.to_thread(self._midpoint_via_sdk, token_id)` (sync SDK
  off the event loop). The gamma `slug→token_id` resolution (Step 1) is **kept** — it's gamma
  market metadata (the same `clobTokenIds`-by-label pattern E1·2 grounded), not the unverified clob
  price.
- New `_midpoint_via_sdk(token_id) -> float`: lazily constructs + caches a **Level-0**
  `ClobClient(host=_CLOB_API)` (keyless; lazy SDK import → gate-safe), calls `get_midpoint`,
  **defensive parse** (`mid` → `midpoint` → `price`), `0.0` on any error/non-dict.
- **Contract preserved:** `quote(symbol) -> float`, `0.0` on stub/error; docstring updated
  (last-trade → mid).

## Phase C — tests (mocked, read-only/fundless)

- **Box gate: 8 passed** (`tests/test_polymarket_broker_quote.py`, no SDK/network — the SDK client is
  injected as a mock): `_midpoint_via_sdk` parses `mid`/fallback fields, and returns `0.0` on
  empty/`None`/exception/non-numeric; `quote()` end-to-end resolves the **correct outcome's** token
  (outcomes ordered `No,Yes` → the `Yes` token `t1`) then returns its midpoint; stub → `0.0`;
  no-market → `0.0`.
- **Full same-env gate (vs pristine main `8c21705`, `git stash -u`): `EDITED=31 == PRISTINE=31`,
  diff empty → zero new regressions.** Notably **no existing test asserted the old last-trade quote
  path**, so changing the value source (last-trade → midpoint) broke nothing — the contract holds.
- No live CLOB read in tests; no creds, no funds.

## Carried-forward

- The `/midpoint` response field is grounded as `mid` but parsed defensively; **re-confirmed at the
  operator-gated $1 shakedown** (alongside E1·3 status + E1·4 cancel-shape). Standing E1·1–4
  carry-forwards apply.
- The **executable-side** price (`get_price(token_id, side)`) for precise entry cost is a future
  side-aware method (E1·6/E2) — deliberately NOT folded into `quote()` (would break the contract).
- **positions-shape verification is OP·D** (operator-gated, needs a funded wallet) — out of slice.

## Phase D — status

- `brokers/polymarket.py` (quote → SDK midpoint + `_midpoint_via_sdk`) + new
  `tests/test_polymarket_broker_quote.py`, committed on the branch. **UNMERGED.** No deploy/merge.
- **Hard stops honored:** no funds/placement; positions-shape verify (OP·D) NOT done; stayed
  in-slice (no assembly/preflight); price semantics **grounded, not assumed**; quote() consumer
  contract **preserved**; no prod write; zero pytest regressions.

## Next — E1·6

Assemble `PolymarketLiveBroker(Broker)` wiring E1·2–5 (mapping/sign + place/poll + cancel + the
SDK quote) into the `Broker` contract; unit-tested against the tastytrade pattern.

---

*E1·5 artifact — committed unmerged on `polymarket-e1-5-quote-2026-06-13`. Builds on E1·1–4 (main
`8c21705`). Source: py-clob-client 0.17.5 `client.py` (`get_midpoint`, Level-0 `__init__`).*
