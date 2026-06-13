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

import json
import logging
from collections.abc import Callable

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
