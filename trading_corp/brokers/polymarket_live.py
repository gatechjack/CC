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

from trading_corp.brokers.base import Broker

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


# ── E2·2: order_type config + synthesized FAK ───────────────────────────────
#
# py_clob_client 0.17.5 exposes only GTC/FOK/GTD OrderTypes — there is NO native
# FAK/IOC (D2, confirmed). v1 therefore SYNTHESIZES FAK ("fill-and-kill") over the
# GTC path (`place_order_fak_synth`, defined after E1·4 cancel below): post GTC,
# poll a short window, cancel the unfilled remainder, and emit a FillEvent for
# ONLY the filled portion (never a phantom fill). gtc/fok/gtd pass straight
# through the native `place_order` path with no synthesis. order_type is
# config-driven (copy-strategy config -> broker ctor) so a later GTD/FOK swap is a
# YAML edit, not a rebuild. The order_type STRING is resolved to a py_clob_client
# OrderType lazily inside the SDK-touching functions, so the broker can dispatch
# on the string without importing the SDK (it isn't on the box's pytest env).

_DEFAULT_FAK_POLL_SECONDS = 5.0  # short FAK-synth poll window (seconds); configurable
_SYNTH_FAK = "fak_synth"
_NATIVE_ORDER_TYPES = frozenset({"gtc", "fok", "gtd"})
_VALID_ORDER_TYPES = _NATIVE_ORDER_TYPES | {_SYNTH_FAK}


def _native_order_type(order_type: str):
    """Map an order_type config string to a py_clob_client `OrderType` (lazy SDK
    import). Only the NATIVE types (gtc/fok/gtd) map here; `fak_synth` is
    synthesized over GTC and never reaches this function. Raises on an unknown
    string — fail loud rather than silently posting the wrong tif."""
    from py_clob_client.clob_types import OrderType

    key = str(order_type).strip().lower()
    mapping = {"gtc": OrderType.GTC, "fok": OrderType.FOK, "gtd": OrderType.GTD}
    if key not in mapping:
        raise ValueError(
            f"{order_type!r} is not a native py_clob_client OrderType "
            f"(expected one of {sorted(_NATIVE_ORDER_TYPES)})"
        )
    return mapping[key]


async def _post_signed_order(
    client, order, native_order_type, *,
    market_fetcher: Callable[[str], dict | None] | None = None,
):
    """Map -> sign (`create_order`) -> `post_order(signed, native_order_type)` ->
    validate the placement. Returns `(order_id, mapped)`.

    Shared by `place_order` (native) and `place_order_fak_synth` (which always
    posts GTC). Raises `OrderPlacementError` on a rejected, orderID-less, or
    `unmatched` placement. **REAL-when-live**: `post_order` reaches the live CLOB
    only with a live client (here/in tests it is mocked)."""
    mapped = map_proposed_to_clob(order, market_fetcher=market_fetcher)
    args = build_clob_order_args(mapped)
    signed = await asyncio.to_thread(client.create_order, args)
    resp = (await asyncio.to_thread(client.post_order, signed, native_order_type)) or {}

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
    return order_id, mapped


async def place_order(
    client, order, *, market_fetcher: Callable[[str], dict | None] | None = None,
    order_type: str = "gtc",
    timeout: float = _DEFAULT_POLL_TIMEOUT_SEC,
    interval: float = _DEFAULT_POLL_INTERVAL_SEC,
):
    """Map -> sign -> `post_order(signed, <native tif>)` -> poll -> FillEvent.

    The NATIVE place path for the gtc/fok/gtd order types (`order_type` is the
    config string; resolved to a py_clob_client `OrderType` here). Mirrors
    `tastytrade.place_order`. Raises `OrderPlacementError` on a rejected/unmatched
    placement and `TimeoutError` if a resting order never fills within `timeout` —
    never a phantom FillEvent. The synthesized FAK path is `place_order_fak_synth`;
    it never routes here.
    """
    native = _native_order_type(order_type)
    order_id, mapped = await _post_signed_order(
        client, order, native, market_fetcher=market_fetcher,
    )
    return await _poll_order_to_fill(
        client, order_id,
        symbol=getattr(order, "symbol", ""),
        side=mapped["side"],
        price=mapped["price"],
        original_size=mapped["size"],
        timeout=timeout, interval=interval,
    )


# ── E1·4: cancel ────────────────────────────────────────────────────────────


async def cancel_order(client, order_id: str) -> bool:
    """Cancel a resting CLOB order by id; return True iff it was canceled, else
    False. **Never raises** (the Broker contract is `-> bool`) — mirrors
    `tastytrade.py:273-285`.

    No id mapping: our `FillEvent.order_id` IS the CLOB orderID (`place_order`
    sets it from the `post_order` response), and `client.cancel` takes that id
    directly (DELETE `/order`, body `{"orderID": order_id}`).

    Success determination (grounded in the CLOB cancel-orders docs): the response
    is `{"canceled": [ids], "not_canceled": {id: reason}}`; the order is canceled
    iff its id is in `canceled`. **Conservative:** True only on that clear signal —
    a `not_canceled` entry, an unrecognized/empty/non-dict response, or any
    exception → False (a live order we *wrongly* believe canceled is worse than a
    needless retry). The exact shape is re-confirmed at the operator-gated $1
    shakedown (carry-forward). `client.cancel` (sync) runs via `asyncio.to_thread`.

    `cancel_all()` is intentionally NOT built: the copy loop cancels no CLOB orders
    in bulk (its `.cancel()` calls are asyncio task lifecycle, not orders); a
    bulk/kill-switch cancel belongs to E4 if ever needed.
    """
    oid = str(order_id or "")
    if not oid:
        return False
    try:
        resp = await asyncio.to_thread(client.cancel, oid)
    except Exception as e:
        log.warning("polymarket cancel_order(%s) raised: %s", oid[:14], e)
        return False
    if not isinstance(resp, dict):
        return False
    canceled = resp.get("canceled")
    if isinstance(canceled, list) and oid in [str(c) for c in canceled]:
        return True
    return False


# ── E2·2: synthesized FAK (post GTC -> poll -> cancel remainder) ─────────────


async def place_order_fak_synth(
    client, order, *, market_fetcher: Callable[[str], dict | None] | None = None,
    poll_seconds: float = _DEFAULT_FAK_POLL_SECONDS,
    interval: float = _DEFAULT_POLL_INTERVAL_SEC,
):
    """Synthesize FAK (fill-and-kill) over the native GTC path.

    py_clob_client 0.17.5 has no native FAK/IOC, so v1 FAK is:
      1. map -> sign -> `post_order(GTC)`  (reuses the E1·3 placement path);
      2. poll `<= poll_seconds`, capturing `size_matched` each poll
         (reuses E1·3 `_poll_order_to_fill`);
      3. cancel the unfilled remainder (reuses E1·4 `cancel_order`);
      4. emit a FillEvent for ONLY the filled portion.

    Fill semantics — NEVER a phantom fill:
      - full fill      -> FillEvent(qty=filled); no remainder to cancel.
      - partial fill   -> FillEvent(qty=filled, < posted size); resting remainder
                          cancelled (FAK = kill the unfilled balance).
      - zero fill      -> cancel + raise `OrderPlacementError`; no FillEvent.

    Mirrors the native path's "raise, never fabricate" convention for the no-fill
    case: a terminal cancelled/unmatched order with 0 matched already raises
    `OrderPlacementError` from `_poll_order_to_fill` (nothing left to cancel); an
    order still RESTING with 0 matched at the window's expiry raises `TimeoutError`
    there, which we convert here — cancel the remainder, then raise
    `OrderPlacementError`.
    """
    from py_clob_client.clob_types import OrderType

    order_id, mapped = await _post_signed_order(
        client, order, OrderType.GTC, market_fetcher=market_fetcher,
    )
    try:
        fill = await _poll_order_to_fill(
            client, order_id,
            symbol=getattr(order, "symbol", ""),
            side=mapped["side"],
            price=mapped["price"],
            original_size=mapped["size"],
            timeout=poll_seconds, interval=interval,
        )
    except TimeoutError:
        # Window expired with the order still resting and NOTHING matched: kill the
        # remainder and signal no-fill the way the native path does (raise, never a
        # phantom FillEvent).
        await cancel_order(client, order_id)
        raise OrderPlacementError(
            f"polymarket FAK-synth order {order_id} did not fill within "
            f"{poll_seconds}s; remainder cancelled, no fill recorded"
        )
    # Filled (full or partial). A partial fill (filled < posted size) leaves an
    # unfilled remainder resting — kill it per FAK semantics. Full fill: nothing
    # to cancel. cancel_order never raises (Broker contract is -> bool); on an
    # already-terminal order it is a harmless no-op returning False.
    if fill.qty < float(mapped["size"]):
        await cancel_order(client, order_id)
    return fill


# ── E1·6: PolymarketLiveBroker(Broker) — assembly ───────────────────────────

_CLOB_HOST = "https://clob.polymarket.com"
_POLYGON_CHAIN_ID = 137

# Aliases so the same-named Broker methods below delegate to these module-level
# functions without name-shadow confusion.
_place_order_fn = place_order
_place_order_fak_synth_fn = place_order_fak_synth  # E2·2 synth dispatch target
_cancel_order_fn = cancel_order

# ── E1·7: on-chain live-readiness preflight (read-only) ─────────────────────
# Live CLOB contracts on Polygon, dumped from py_clob_client 0.17.5
# get_contract_config(137) in the 2026-05-29 spike (Track 1b):
_USDC_E = "0x2791Bca1f2de4661eD88A30C99A7a9449Aa84174"          # collateral (USDC.e, NOT native USDC); == brokers.polymarket._USDC_CONTRACT
_STD_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"     # CTF Exchange (standard)
_NEG_RISK_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"  # NegRisk CTF Exchange
_CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"      # Conditional Tokens (ERC-1155)
# Standard read selectors (keccak256(sig)[:4]):
_ALLOWANCE_SELECTOR = "0xdd62ed3e"            # ERC-20 allowance(address owner, address spender)
_IS_APPROVED_FOR_ALL_SELECTOR = "0xe985e9c5"  # ERC-1155 isApprovedForAll(address owner, address operator)


def _pad_addr(addr: str) -> str:
    """Left-pad a 20-byte address to 32 bytes (64 hex) for eth_call ABI args."""
    a = str(addr).lower().removeprefix("0x")
    if len(a) != 40:
        raise ValueError(f"expected 20-byte address, got {addr!r}")
    return ("0" * 24) + a


def _allowance_calldata(owner: str, spender: str) -> str:
    return _ALLOWANCE_SELECTOR + _pad_addr(owner) + _pad_addr(spender)


def _is_approved_for_all_calldata(owner: str, operator: str) -> str:
    return _IS_APPROVED_FOR_ALL_SELECTOR + _pad_addr(owner) + _pad_addr(operator)


class PolymarketLiveBroker(Broker):
    """Live Polymarket order broker — assembles E1·2–5 into the `Broker` contract.

    A **placement-legal `Broker`** (NOT a `ReadOnlyBroker`): it has
    `place_order`/`cancel_order`. Reads (connect/disconnect/snapshot/quote) are
    delegated to the read adapter `PolymarketBroker` (incl. E1·5's SDK-midpoint
    quote); placement/cancel go through the **L2-authed** py_clob_client.

    - `connect()`: connect the read adapter + build the placement client and
      **L2-authorize** it (`create_or_derive_api_creds` → `set_api_creds`) so
      `post_order` (L2 auth) is allowed; `paper = False` (live).
    - `place_order(order) -> FillEvent`  (E1·2/3 map→sign→post→poll; E2·2
      dispatches on `order_type`: `fak_synth` synthesizes FAK over GTC, else
      gtc/fok/gtd native pass-through)
    - `cancel_order(order_id) -> bool`   (E1·4)
    - `snapshot()` / `quote()`           (read adapter; E1·5 quote)

    `place_multi_leg`/`get_option_greeks` inherit `Broker`'s NotImplementedError
    (Polymarket never sees multi-leg). The PCT live-vs-paper resolution lives in
    the main.py factory (`_build_broker_for_division`, polymarket `is_live_family`).

    MOCKED/FUNDLESS in tests; the real CLOB is hit only with a live, funded,
    L2-authed client. `place_order`'s token_id uses the direct path
    (`extra["token_id"]`) — E2 propagates `activity.asset`; a gamma fallback
    fetcher can be wired later.
    """

    name = "polymarket-live"
    paper = False

    def __init__(
        self, private_key=None, funder_address=None, polygon_rpc_url=None,
        order_type: str = _SYNTH_FAK, fak_poll_seconds: float = _DEFAULT_FAK_POLL_SECONDS,
    ):
        from trading_corp.brokers.polymarket import PolymarketBroker

        self._private_key = private_key
        self._funder = funder_address
        self._rpc_url = polygon_rpc_url
        # E2·2: execution discipline (config-driven; E2·6 sources these from the
        # copy-strategy config). order_type dispatches place_order: fak_synth
        # (default) synthesizes FAK over GTC; gtc/fok/gtd pass through native.
        ot = str(order_type).strip().lower()
        if ot not in _VALID_ORDER_TYPES:
            raise ValueError(
                f"unsupported order_type {order_type!r}; expected one of "
                f"{sorted(_VALID_ORDER_TYPES)}"
            )
        self._order_type = ot
        self._fak_poll_seconds = float(fak_poll_seconds)
        if self._fak_poll_seconds < 0:
            raise ValueError(
                f"fak_poll_seconds must be >= 0; got {fak_poll_seconds!r}"
            )
        # Reuse the read adapter for connect/disconnect/snapshot/quote.
        self._read = PolymarketBroker(
            private_key=private_key,
            funder_address=funder_address,
            polygon_rpc_url=polygon_rpc_url,
        )
        self._clob = None        # L2-authed placement client, set on connect()
        self._connected = False

    def _build_clob_client(self):
        """Construct the L1 placement client (host + chain_id + signer key).
        Isolated for testability (override to inject a mock)."""
        from py_clob_client.client import ClobClient

        return ClobClient(
            host=_CLOB_HOST, chain_id=_POLYGON_CHAIN_ID, key=self._private_key,
        )

    async def connect(self) -> None:
        await self._read.connect()
        # E1·7: read-only on-chain live-readiness preflight — abort LOUDLY if the
        # funder isn't funded (USDC.e) or the exchange approvals aren't set, so a
        # misconfigured wallet fails HERE, not mid-trade on an unsettling order.
        await self._assert_funded_and_approved()
        clob = self._build_clob_client()
        # L2 authorize: create (or derive) API creds, then set them so post_order
        # (L2 auth) is permitted. Sync SDK -> to_thread.
        creds = await asyncio.to_thread(clob.create_or_derive_api_creds)
        await asyncio.to_thread(clob.set_api_creds, creds)
        self._clob = clob
        self._connected = True
        log.info(
            "PolymarketLiveBroker connected (L2 creds set; funder=%s)", self._funder,
        )

    async def disconnect(self) -> None:
        await self._read.disconnect()
        self._clob = None
        self._connected = False

    async def _eth_call(self, to: str, data: str) -> int:
        """Read-only Polygon RPC eth_call (reusing the read adapter's httpx
        client + RPC URL); returns the result as an int. No signing, no funds,
        no on-chain write."""
        client = self._read._client
        rpc = self._read._rpc_url
        if client is None or not rpc:
            raise RuntimeError("polymarket preflight: no RPC client (wallet stub?)")
        payload = {
            "jsonrpc": "2.0", "method": "eth_call",
            "params": [{"to": to, "data": data}, "latest"], "id": 1,
        }
        r = await client.post(
            rpc, json=payload, headers={"Content-Type": "application/json"},
        )
        r.raise_for_status()
        body = r.json() or {}
        if "error" in body:
            raise RuntimeError(f"polymarket preflight eth_call error: {body['error']}")
        return int(body.get("result", "0x0") or "0x0", 16)

    async def _assert_funded_and_approved(self) -> None:
        """Read-only on-chain check that the funder EOA can actually place — it
        holds USDC.e (OP·B) and has set the ERC-20 + ERC-1155 exchange approvals
        (OP·C). Aborts LIVE `connect()` LOUDLY on any gap, so a misconfigured
        wallet fails at startup, not mid-trade on a placed-but-unsettling order.
        The agent only CHECKS (read-only) — the operator provisions/funds/approves
        (OP·A–C are operator-only).
        """
        funder = self._read._funder
        if not funder or self._read._client is None or not self._read._rpc_url:
            raise RuntimeError(
                "PolymarketLiveBroker preflight: wallet not provisioned "
                "(no funder/RPC) — cannot go live"
            )
        # OP·B — funded in USDC.e (the CLOB collateral, NOT native USDC).
        usdc_e = await self._read._fetch_usdc_balance()
        if usdc_e <= 0.0:
            raise RuntimeError(
                f"PolymarketLiveBroker preflight: funder {funder[:10]}… holds 0 "
                f"USDC.e — fund the wallet in USDC.e (NOT native USDC) before live"
            )
        # OP·C — ERC-20 allowance(USDC.e → exchange) set for both exchanges.
        for label, spender in (("std", _STD_EXCHANGE), ("negRisk", _NEG_RISK_EXCHANGE)):
            allowance = await self._eth_call(_USDC_E, _allowance_calldata(funder, spender))
            if allowance <= 0:
                raise RuntimeError(
                    f"PolymarketLiveBroker preflight: USDC.e allowance to the "
                    f"{label} exchange is 0 — run the one-time approvals before live"
                )
        # OP·C — ERC-1155 isApprovedForAll(CTF → exchange) for both exchanges.
        for label, operator in (("std", _STD_EXCHANGE), ("negRisk", _NEG_RISK_EXCHANGE)):
            approved = await self._eth_call(
                _CTF_ADDRESS, _is_approved_for_all_calldata(funder, operator),
            )
            if approved != 1:
                raise RuntimeError(
                    f"PolymarketLiveBroker preflight: CTF approval-for-all to the "
                    f"{label} exchange not set — run the one-time approvals before live"
                )
        # NOTE (carry-forward): the NegRisk Adapter (0x7876…f29e) MAY also need
        # approval for neg-risk markets — flagged UNCERTAIN in the 05-29 spike
        # (not in the 0.17.5 ContractConfig). Confirm against the live approval
        # set at the operator shakedown; not asserted here to avoid a false-abort
        # on an unconfirmed spender.

    async def snapshot(self):
        return await self._read.snapshot()

    async def quote(self, symbol: str) -> float:
        return await self._read.quote(symbol)

    def _require_connected(self) -> None:
        if not self._connected or self._clob is None:
            raise RuntimeError(
                "PolymarketLiveBroker not connected — call connect() first"
            )

    async def place_order(self, order):
        self._require_connected()
        # E2·2: dispatch on the configured order_type. fak_synth synthesizes FAK
        # over GTC (poll-then-cancel-remainder); gtc/fok/gtd pass through native.
        if self._order_type == _SYNTH_FAK:
            return await _place_order_fak_synth_fn(
                self._clob, order, poll_seconds=self._fak_poll_seconds,
            )
        return await _place_order_fn(
            self._clob, order, order_type=self._order_type,
        )

    async def cancel_order(self, order_id: str) -> bool:
        self._require_connected()
        return await _cancel_order_fn(self._clob, order_id)
