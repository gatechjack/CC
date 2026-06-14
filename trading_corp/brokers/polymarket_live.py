"""Polymarket live broker — order mapping + sign-only create_order (E1·2 slice).

E1 builds a live Polymarket order path by WIRING the official `py_clob_client` SDK
(EIP-712 signing is internal to the SDK — proven by the 2026-05-29 spike +
re-proven on the pinned/py3.12 set in E1·1). This module is the **E1·2** slice only:
the `ProposedOrder -> OrderArgs` mapping (incl. token_id resolution) and a
**sign-only** `create_order` call. The full `PolymarketLiveBroker(Broker)` class is
assembled in E1·6; the place/poll path is E1·3, cancel is E1·4, quote is E1·5.

**token_id sourcing (E1·2 Phase A finding).** The copy strategy's `ProposedOrder.extra`
carries `condition_id` + `outcome_index` + `outcome` but NOT the token_id
(`polymarket_copy_trader.py:429-451`); `symbol` is `f"{condition_id}:{outcome}"`. The
whale's `ActivityRow.asset` IS the ERC-1155 token id
(`polymarket_data_api_client.py:149`) but is not propagated today. So token_id is
resolved **direct-then-lookup**:
  1. `extra["token_id"]` / `extra["asset"]` if present (authoritative, no network), else
  2. a gamma `/markets` lookup: `clobTokenIds` (JSON list) parallel to `outcomes`,
     matched by the outcome **LABEL** (order-independent — mirrors
     `brokers/polymarket.py` `quote()`), cross-checked against `outcome_index`.
     **Raises on not-found / ambiguous / label-vs-index disagreement** — a wrong
     token_id is the wrong side of a real market, so we fail loud, never guess.

**E2 follow-on (NOT built here):** the copy strategy should set
`extra["token_id"] = activity.asset` so the direct path is the production norm and the
gamma lookup is a rarely-fired fallback. Tracked in BACKLOG P2.

`py_clob_client` is imported **lazily** inside the SDK-touching functions, so this
module and the pure mapping/resolution functions import without the SDK on the box's
pytest env (the SDK lives only in the py3.12/Linux lockfile). SDK-dependent tests
`importorskip` and run on the pinned py3.12 venv. **Sign-only: `post_order` is NEVER
called in this module.**
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone

log = logging.getLogger(__name__)


class TokenIdResolutionError(ValueError):
    """Raised when the CLOB token_id for an order cannot be resolved
    unambiguously. Fail loud — a wrong token_id is the wrong side of a real
    market, so we never guess."""


def _parse_list_field(value: object) -> list | None:
    """gamma returns `clobTokenIds`/`outcomes` as either a JSON-encoded string
    or a list. Normalize to a list (or None if unparseable)."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    return value if isinstance(value, list) else None


def resolve_token_id_from_market(
    market: dict, *, outcome_index: int | None, outcome: str | None,
) -> str:
    """Pick the correct outcome's ERC-1155 token_id from a gamma market dict.

    `clobTokenIds[i]` is parallel to `outcomes[i]`. We match by the outcome
    LABEL (order-independent), because the `outcome_index` on our side is not
    guaranteed to share gamma's `clobTokenIds` ordering. `outcome_index`, when
    present, is a CROSS-CHECK: if the label is found at a different position
    than `outcome_index` claims, that is an ordering mismatch and we RAISE
    rather than risk the wrong side.
    """
    token_ids = _parse_list_field(market.get("clobTokenIds"))
    outcomes = _parse_list_field(market.get("outcomes"))
    key = market.get("conditionId") or market.get("condition_id") or market.get("slug")
    if not token_ids or outcomes is None:
        raise TokenIdResolutionError(
            f"market missing clobTokenIds/outcomes (key={key!r})"
        )
    if len(token_ids) != len(outcomes):
        raise TokenIdResolutionError(
            f"clobTokenIds/outcomes length mismatch "
            f"({len(token_ids)} vs {len(outcomes)}, key={key!r})"
        )

    idx: int | None = None
    if outcome is not None and str(outcome) != "":
        matches = [
            i for i, lbl in enumerate(outcomes)
            if str(lbl).strip().lower() == str(outcome).strip().lower()
        ]
        if len(matches) > 1:
            raise TokenIdResolutionError(
                f"ambiguous outcome label {outcome!r} in {outcomes} (key={key!r})"
            )
        if matches:
            idx = matches[0]

    if idx is None:
        # No usable label match — fall back to the index, bounds-checked.
        if outcome_index is None:
            raise TokenIdResolutionError(
                f"outcome {outcome!r} not found in {outcomes} and no "
                f"outcome_index given (key={key!r})"
            )
        if not (0 <= int(outcome_index) < len(token_ids)):
            raise TokenIdResolutionError(
                f"outcome_index {outcome_index} out of range for "
                f"{len(token_ids)} outcomes (key={key!r})"
            )
        idx = int(outcome_index)
    elif outcome_index is not None and int(outcome_index) != idx:
        # Label found, but at a DIFFERENT position than outcome_index claims.
        raise TokenIdResolutionError(
            f"ordering mismatch: outcome label {outcome!r} is at index {idx} "
            f"but outcome_index={outcome_index} (key={key!r}); refusing to "
            f"guess token_id"
        )

    token_id = str(token_ids[idx] or "")
    if not token_id:
        raise TokenIdResolutionError(
            f"empty token_id at index {idx} (key={key!r})"
        )
    return token_id


def resolve_token_id(
    extra: dict | None,
    *,
    market_fetcher: Callable[[str], dict | None] | None = None,
) -> str:
    """Resolve the CLOB token_id for an order. Direct-then-lookup:
    `extra["token_id"]`/`["asset"]` if present, else a gamma `/markets` lookup
    by `condition_id` (+ `outcome_index`/`outcome`) via `market_fetcher`.
    """
    extra = extra or {}
    direct = extra.get("token_id") or extra.get("asset")
    if direct:
        return str(direct)
    condition_id = extra.get("condition_id")
    if not condition_id:
        raise TokenIdResolutionError(
            "no token_id/asset and no condition_id in order.extra"
        )
    if market_fetcher is None:
        raise TokenIdResolutionError(
            "token_id absent from extra and no market_fetcher provided for the "
            "gamma lookup"
        )
    market = market_fetcher(str(condition_id))
    if not market:
        raise TokenIdResolutionError(
            f"gamma lookup returned no market for condition_id={condition_id!r}"
        )
    return resolve_token_id_from_market(
        market,
        outcome_index=extra.get("outcome_index"),
        outcome=extra.get("outcome"),
    )


def map_proposed_to_clob(
    order, *, market_fetcher: Callable[[str], dict | None] | None = None,
) -> dict:
    """Map a copy-trader `ProposedOrder` to CLOB order fields.

    Returns `{token_id, price, size, side}` where `price` is the 0-1 probability
    (== `ProposedOrder.limit_price`), `size` is share/contract count (== `qty`),
    and `side` is `"buy"`/`"sell"`. Pure (no SDK); the SDK `OrderArgs` is built by
    `build_clob_order_args`.
    """
    extra = getattr(order, "extra", None) or {}
    token_id = resolve_token_id(extra, market_fetcher=market_fetcher)

    price = order.limit_price
    if price is None or not (0.0 < float(price) < 1.0):
        raise ValueError(
            f"polymarket order price must be a probability in (0,1); got {price!r}"
        )
    size = float(order.qty)
    if size <= 0.0:
        raise ValueError(f"polymarket order size must be > 0; got {order.qty!r}")
    side = str(order.side).strip().lower()
    if side not in ("buy", "sell"):
        raise ValueError(f"unsupported order side {order.side!r} (expected buy/sell)")

    return {"token_id": token_id, "price": float(price), "size": size, "side": side}


def build_clob_order_args(mapped: dict):
    """`{token_id, price, size, side}` -> py_clob_client `OrderArgs` (lazy SDK
    import). Kwargs construction (constructor arg order is version-sensitive)."""
    from py_clob_client.clob_types import OrderArgs
    from py_clob_client.order_builder.constants import BUY, SELL

    return OrderArgs(
        token_id=mapped["token_id"],
        price=mapped["price"],
        size=mapped["size"],
        side=BUY if mapped["side"] == "buy" else SELL,
    )


def create_signed_order(
    client, order, *, market_fetcher: Callable[[str], dict | None] | None = None,
):
    """Map a `ProposedOrder` -> `OrderArgs` and SIGN it via `client.create_order`.

    **SIGN-ONLY** — this never calls `post_order` (the place/poll path is E1·3).
    `client.create_order` performs the EIP-712 signing internally and returns the
    signed order object.
    """
    mapped = map_proposed_to_clob(order, market_fetcher=market_fetcher)
    args = build_clob_order_args(mapped)
    return client.create_order(args)


# ── E1·3: place -> poll -> FillEvent ────────────────────────────────────────
#
# Mirrors the tastytrade place->poll-to-terminal->FillEvent template
# (`brokers/tastytrade.py:398-465`). py_clob_client is a thin REST wrapper:
# `post_order` returns the raw placement dict and `get_order` the raw order dict
# — the SDK defines NO order-status constants, so the status strings come from
# the live Polymarket CLOB API:
#   - placement status (post_order): live | matched | delayed | unmatched
#     (https://docs.polymarket.com/developers/CLOB/orders/create-order)
#   - order fields (get_order): status, size_matched, original_size, price, side
#     (https://docs.polymarket.com/developers/CLOB/orders/get-order)
# We **normalize status to lowercase** and treat **`size_matched` as the
# authoritative filled quantity** (not the status label), to minimize reliance
# on the exact status strings. CARRY-FORWARD: the real status strings +
# `size_matched` semantics (incl. the issue-#245 caveat that size_matched can
# overstate tokens actually received vs on-chain truth — reconciliation is E5)
# are confirmed only at the operator-gated $1 shakedown.
#
# SIGN-ONLY in tests: `post_order` is exercised via a mocked client; the real
# CLOB is hit only with a live client. The --dry-run skip lives at
# `data_exec.place()` (E2), NOT here (Polymarket has no venue validate); the
# broker's paper/live gating is E1·6. py_clob_client (sync) is run via
# `asyncio.to_thread` so the live path never blocks the loop.

_NONTERMINAL_STATUSES = frozenset({"live", "delayed"})  # keep polling
_DEFAULT_POLL_TIMEOUT_SEC = 10.0
_DEFAULT_POLL_INTERVAL_SEC = 0.75


class OrderPlacementError(RuntimeError):
    """post_order was rejected, or the order terminated with no fill. We never
    fabricate a phantom FillEvent for an unfilled order."""


def _is_fully_filled(size_matched: float, original_size: float) -> bool:
    return original_size > 0 and size_matched >= original_size


async def _poll_order_to_fill(
    client, order_id, *, symbol, side, price, original_size,
    timeout: float = _DEFAULT_POLL_TIMEOUT_SEC,
    interval: float = _DEFAULT_POLL_INTERVAL_SEC,
):
    """Poll `client.get_order(order_id)` until terminal or `timeout`, then map to
    a `FillEvent`. `size_matched` is the source of truth for filled qty:
      - filled (>0) at terminal or at timeout -> FillEvent for the FILLED portion
        (full or partial);
      - zero fill at a terminal non-fill status (cancelled/unmatched/expired) ->
        OrderPlacementError;
      - zero fill still resting (live/delayed) at timeout -> TimeoutError.
    No phantom FillEvent for an unfilled order.
    """
    from trading_corp.persistence.models import FillEvent

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last_status = ""
    size_matched = 0.0
    fill_price = float(price)
    while True:
        order = (await asyncio.to_thread(client.get_order, order_id)) or {}
        last_status = str(order.get("status") or "").strip().lower()
        try:
            size_matched = float(order.get("size_matched") or 0.0)
        except (TypeError, ValueError):
            size_matched = 0.0
        try:
            orig = float(order.get("original_size") or original_size)
        except (TypeError, ValueError):
            orig = float(original_size)
        try:
            if order.get("price") is not None:
                fill_price = float(order["price"])
        except (TypeError, ValueError):
            pass

        if last_status not in _NONTERMINAL_STATUSES or _is_fully_filled(size_matched, orig):
            break  # terminal
        if loop.time() >= deadline:
            if size_matched > 0:
                break  # partial fill captured at the deadline
            raise TimeoutError(
                f"polymarket order {order_id} did not reach a terminal state "
                f"within {timeout}s (last status={last_status!r}, size_matched=0)"
            )
        await asyncio.sleep(interval)

    if size_matched <= 0:
        raise OrderPlacementError(
            f"polymarket order {order_id} terminal status={last_status!r} with no "
            f"fill (size_matched=0); no FillEvent recorded"
        )
    return FillEvent(
        order_id=str(order_id),
        symbol=symbol,
        side=side,
        qty=float(size_matched),
        price=float(fill_price),
        ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        venue="polymarket",
    )


async def place_order(
    client, order, *, market_fetcher: Callable[[str], dict | None] | None = None,
    timeout: float = _DEFAULT_POLL_TIMEOUT_SEC,
    interval: float = _DEFAULT_POLL_INTERVAL_SEC,
):
    """Map -> sign (`create_order`) -> `post_order(signed, GTC)` -> poll -> FillEvent.

    Mirrors `tastytrade.place_order`. **REAL-when-live**: `post_order` reaches the
    live CLOB only with a live client (here/in tests it is mocked). Raises
    `OrderPlacementError` on a rejected/unmatched placement and `TimeoutError` if
    a resting order never fills within `timeout` — never a phantom FillEvent.
    """
    from py_clob_client.clob_types import OrderType

    mapped = map_proposed_to_clob(order, market_fetcher=market_fetcher)
    args = build_clob_order_args(mapped)
    signed = await asyncio.to_thread(client.create_order, args)
    resp = (await asyncio.to_thread(client.post_order, signed, OrderType.GTC)) or {}

    if not resp.get("success", False):
        raise OrderPlacementError(
            f"polymarket post_order failed: {resp.get('errorMsg') or resp!r}"
        )
    order_id = resp.get("orderID")
    if not order_id:
        raise OrderPlacementError(f"polymarket post_order returned no orderID: {resp!r}")
    if str(resp.get("status") or "").strip().lower() == "unmatched":
        raise OrderPlacementError(
            f"polymarket order {order_id} placement status='unmatched' "
            f"(marketable but did not match); no fill recorded"
        )

    return await _poll_order_to_fill(
        client, order_id,
        symbol=getattr(order, "symbol", ""),
        side=mapped["side"],
        price=mapped["price"],
        original_size=mapped["size"],
        timeout=timeout, interval=interval,
    )
