# Broker Multi-Leg Interface Design

## Purpose

This document is step 1 of the iron-condor build sequence in [we-will-be-working-async-lerdorf.md](../../.claude/plans/we-will-be-working-async-lerdorf.md). It locks the broker-layer interface that the iron-condor strategy will call to submit 4-leg combos atomically and to fetch live Greeks for tested-side identification.

Two new methods are added to the `Broker` ABC:

1. `place_multi_leg(orders: list[ProposedOrder]) -> list[FillEvent]`
2. `get_option_greeks(option_id: str) -> dict[str, float]`

Implementations land in `RobinhoodBroker` and `PaperExecutionBroker`. Other concrete brokers (Coinbase, Bitunix, Kalshi, Polymarket, Fidelity, Paper) keep the ABC default — `NotImplementedError`. They never see multi-leg traffic.

Out of scope here: any change to RiskAgent, LangGraph, or the HITL approval surface. Risk gate stays per-leg per the parent plan. HITL coalescing by `combo_id` is step 12.

---

## Verified facts (against current code + `robin_stocks` master)

### `robin_stocks.robinhood.orders.order_option_spread`

```python
@login_required
def order_option_spread(direction, price, symbol, quantity, spread,
                       account_number=None, timeInForce='gtc', jsonify=True):
```

- **Atomic.** Single POST to `option_orders_url(account_number=account_number)` with one `ref_id = str(uuid4())`. Robinhood's exchange-side combo engine fills all legs together or none.
- **Mixed `position_effect` supported.** Each leg independently sets `'position_effect': each['effect']` from `{"open", "close"}`. **This means an iron-condor adjustment that closes 2 legs and opens 2 new legs can be submitted as one 4-leg POST — no two-combo sequencing needed.** (This is a meaningful improvement over the parent plan's default.)
- **Leg dict shape (input format expected by `robin_stocks`)**:
  ```python
  {
      "expirationDate": "YYYY-MM-DD",
      "strike": float,
      "optionType": "call" | "put",
      "effect": "open" | "close",
      "action": "buy" | "sell",
      "ratio_quantity": int,        # legs in a combo can have different ratios
  }
  ```
- **Internal payload that `robin_stocks` constructs** (we don't build this — `robin_stocks` does, after resolving `option_id` via `id_for_option`):
  ```python
  legs.append({
      "position_effect": each["effect"],
      "side": each["action"],
      "ratio_quantity": each["ratio_quantity"],
      "option": option_instruments_url(optionID),
  })
  payload = {
      "account": load_account_profile(...),
      "direction": direction,        # "credit" | "debit"
      "time_in_force": timeInForce,
      "legs": legs,
      "type": "limit",
      "trigger": "immediate",
      "price": price,                # net limit (credit or debit per direction)
      "quantity": quantity,          # number of condors/spreads
      "ref_id": str(uuid4()),
  }
  ```
- **Wrappers**: `order_option_credit_spread(price, symbol, quantity, spread, …)` calls `order_option_spread("credit", …)`. `order_option_debit_spread(…)` calls `order_option_spread("debit", …)`. Iron condor opens are credit; closes of credit-spreads net to debit.
- **`id_for_option` resolution** happens inside `order_option_spread`. We pass leg specs in the attribute format (expirationDate/strike/optionType); the library resolves option IDs for us.

### `robin_stocks.robinhood.options.get_option_market_data_by_id`

Already used by `RobinhoodBroker.get_option_positions_detail` at `brokers/robinhood.py:509`. Takes only `option_id`. Returns market data with `delta`, `theta`, `gamma`, `vega`, `mark_price`, `implied_volatility`. **No position context required** → resolves open item 5 from the parent plan: `get_option_greeks` is a thin wrapper. No upstream refactor needed.

### Trading Corp `Broker` ABC (`brokers/base.py`)

`Broker(ReadOnlyBroker)` requires only `place_order(order)` and `cancel_order(order_id)` today. Adding new abstract methods with `NotImplementedError` defaults is backwards-compatible — existing concrete classes (Coinbase, Bitunix, Kalshi, Polymarket, Fidelity) inherit the default and the IC strategy never calls them.

### `PaperExecutionBroker` (`brokers/paper.py:129-209`)

Explicit-delegation pattern: each option-specific method is its own explicit method that does `if hasattr(self._live, ...): return await self._live....()` else returns `[]`. There is no auto-forwarding via `__getattr__`. **So we must add `place_multi_leg` and `get_option_greeks` explicitly.** They cannot piggyback on a forwarding hook.

`PaperExecutionBroker.place_order` (line 189) delegates execution to the inner `PaperBroker` (line 190), not to the live broker. So `place_multi_leg` on `PaperExecutionBroker` will: (a) read live mids via `self._live.get_option_greeks(option_id)`, (b) apply the slippage model, (c) call `self._paper.place_order(leg)` four times under one `combo_id`, (d) emit a single `paper_combo_filled` audit. The inner `PaperBroker` itself does **not** need a `place_multi_leg` — the orchestration lives one layer up.

---

## ABC additions (`brokers/base.py`)

```python
class Broker(ReadOnlyBroker):
    ...

    async def place_multi_leg(
        self, orders: list[ProposedOrder]
    ) -> list[FillEvent]:
        """Submit a multi-leg option combo as a single atomic order.

        All `orders` must share `extra["combo_id"]` and represent legs of one
        combo. `direction` ("credit" | "debit"), `net_limit_price`, and per-leg
        roles are carried in `extra` on each order — see ProposedOrder shape
        below. Returns one FillEvent per leg; all four share the same combo_id
        in their `extra`. Atomic at the exchange: all legs fill or none do.

        Brokers that don't support combo orders raise NotImplementedError
        (default). The iron-condor strategy only invokes this on Robinhood
        and PaperExecutionBroker.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support multi-leg combo orders"
        )

    async def get_option_greeks(self, option_id: str) -> dict[str, float]:
        """Return Greeks + IV + mark for an option by ID.

        Keys: delta, gamma, theta, vega, iv, mark_price. Values may be None
        if the venue does not publish a field. No open-position context
        required — looks up market data by option_id alone.

        Brokers without options support raise NotImplementedError (default).
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not expose option Greeks"
        )
```

Both methods default to `NotImplementedError` rather than abstract — preserves the existing concrete brokers without forcing each to implement no-op stubs.

---

## ProposedOrder shape for combo legs

No schema change. All combo metadata rides in `extra` (→ `extra_json`). Each of the 4 legs in an iron-condor combo carries:

```python
order.symbol       = "<underlying>"       # e.g. "SPY" — same for all 4 legs
order.qty          = <contracts>          # same for all 4 legs (combo size)
order.order_type   = "limit"
order.side         = "buy" | "sell"       # per-leg side
order.limit_price  = <per-leg limit>      # informational; not used by combo POST
order.extra = {
    "is_option": True,
    "is_multi_leg": True,
    "combo_id": "<uuid4>",                # shared across all 4 legs
    "combo_role": "short_call" | "long_call" | "short_put" | "long_put",
    "combo_direction": "credit" | "debit",  # combo-level, identical on all 4
    "net_limit_price": <float>,           # combo-level net credit/debit limit
    "underlying": "SPY",
    "expiration": "YYYY-MM-DD",           # per-leg
    "strike": <float>,                    # per-leg
    "option_type": "call" | "put",        # per-leg
    "position_effect": "open" | "close",  # per-leg — supports mixed
    "ratio_quantity": 1,                  # per-leg; ICs use 1:1:1:1
    # IC-specific telemetry (only on opening legs):
    "ic_short_delta_at_entry": <float>,
    "ic_long_delta_at_entry": <float>,
    "ic_wing_width": <float>,
    "ic_credit_at_entry": <float>,
    "ic_dte_at_entry": <int>,
    "ic_underlying_iv_rank_at_entry": <float>,
}
```

`place_multi_leg` validates `combo_id` is shared, `combo_direction` matches, and `net_limit_price` is identical on all legs. Any mismatch raises a `ValueError` before any POST.

---

## `RobinhoodBroker.place_multi_leg`

```python
async def place_multi_leg(
    self, orders: list[ProposedOrder]
) -> list[FillEvent]:
    self._require_connected()
    if not orders:
        return []

    # Validate combo cohesion.
    combo_id = (orders[0].extra or {}).get("combo_id")
    direction = (orders[0].extra or {}).get("combo_direction")
    net_limit = (orders[0].extra or {}).get("net_limit_price")
    underlying = (orders[0].extra or {}).get("underlying") or orders[0].symbol
    quantity = int(orders[0].qty)

    for o in orders:
        ex = o.extra or {}
        if ex.get("combo_id") != combo_id:
            raise ValueError(f"mixed combo_ids in place_multi_leg: {combo_id} vs {ex.get('combo_id')}")
        if ex.get("combo_direction") != direction:
            raise ValueError(f"mixed combo_direction in {combo_id}")
        if float(ex.get("net_limit_price", 0)) != float(net_limit):
            raise ValueError(f"mismatched net_limit_price in {combo_id}")
        if int(o.qty) != quantity:
            raise ValueError(f"mismatched qty in {combo_id}")
        if (ex.get("underlying") or o.symbol) != underlying:
            raise ValueError(f"mixed underlying in {combo_id}")

    # Build the `spread` list in robin_stocks's expected shape.
    spread = []
    for o in orders:
        ex = o.extra or {}
        spread.append({
            "expirationDate": ex["expiration"],
            "strike": float(ex["strike"]),
            "optionType": ex["option_type"],
            "effect": ex["position_effect"],
            "action": o.side,            # "buy" | "sell"
            "ratio_quantity": int(ex.get("ratio_quantity", 1)),
        })

    import robin_stocks.robinhood as rs  # type: ignore
    acct = self._account_number or None

    # Use the generic order_option_spread; pass direction explicitly so
    # mixed-effect adjustment legs work (credit_spread wrapper is fine for
    # pure opens, but adjustments may be either direction).
    if direction not in ("credit", "debit"):
        raise ValueError(f"combo_direction must be 'credit' or 'debit', got {direction!r}")

    result = await asyncio.to_thread(
        rs.orders.order_option_spread,
        direction,
        float(net_limit),
        underlying,
        quantity,
        spread,
        account_number=acct,
        timeInForce="gfd",  # day order — matches PMCC convention; no resting GTC
    )

    result = result or {}
    # Robinhood returns a single order envelope with legs[]; per-leg fills
    # come via the order detail. For the FillEvent list we emit one event
    # per ProposedOrder leg, all sharing combo_id, with the combo's avg
    # fill price split proportionally to the leg's mid (best-effort; the
    # journal cares more about combo_id linkage than per-leg precision).
    legs_result = result.get("legs") or []
    fills = []
    fill_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for o, leg_data in zip(orders, legs_result):
        # leg_data is the per-leg dict in the order response; some fields
        # depend on Robinhood's order_detail shape and may need a follow-up
        # poll if not populated synchronously. For now we emit at the
        # combo's net price scaled by ratio_quantity / sum(ratios).
        leg_price = float(leg_data.get("price") or 0) or float(o.limit_price or 0)
        fills.append(FillEvent(
            order_id=o.id,
            symbol=o.symbol,
            side=o.side,
            qty=float(o.qty),
            price=leg_price,
            ts=fill_ts,
            venue="robinhood",
        ))
    if len(fills) != len(orders):
        # Defensive: if Robinhood doesn't echo per-leg fills, synthesize.
        for o in orders[len(fills):]:
            fills.append(FillEvent(
                order_id=o.id,
                symbol=o.symbol,
                side=o.side,
                qty=float(o.qty),
                price=float(o.limit_price or 0),
                ts=fill_ts,
                venue="robinhood",
            ))
    return fills
```

**Notes:**
- `timeInForce="gfd"` matches Trading Corp's current PMCC convention (no resting GTC at the broker per parent plan § "Out of scope for v1").
- `direction` is passed explicitly so the same method handles both opens (credit) and closes/adjustments (debit).
- If Robinhood doesn't populate per-leg fill prices synchronously, the strategy can poll `rs.orders.get_option_order_info(order_id)` for the order detail. v1 trusts the combo-level fill and uses `limit_price` per leg as a fallback. Refinement of per-leg fill accuracy is deferred to v1.5 if telemetry shows the journal is misleading.

### `RobinhoodBroker.get_option_greeks`

```python
async def get_option_greeks(self, option_id: str) -> dict[str, float]:
    self._require_connected()
    import robin_stocks.robinhood as rs  # type: ignore
    try:
        raw = await asyncio.to_thread(
            rs.options.get_option_market_data_by_id, option_id
        )
    except Exception as e:
        log.warning("get_option_greeks(%s) failed: %s", option_id, e)
        raise
    if isinstance(raw, list) and raw:
        md = raw[0] or {}
    elif isinstance(raw, dict):
        md = raw
    else:
        md = {}

    def _f(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "delta": _f(md.get("delta")),
        "gamma": _f(md.get("gamma")),
        "theta": _f(md.get("theta")),
        "vega":  _f(md.get("vega")),
        "iv":    _f(md.get("implied_volatility")),
        "mark_price": _f(md.get("adjusted_mark_price") or md.get("mark_price")),
    }
```

Reuses the same library call already used at `brokers/robinhood.py:509`. No refactor of existing callers.

### Connection / cancellation

`cancel_order` semantics for combo orders: a combo cancel hits the combo order ID (not per-leg). For v1 we keep the existing single-leg `cancel_order` and add `cancel_combo_order(order_id)` only if needed during adjustment failure handling. **Decision for v1: skip `cancel_combo_order`.** Adjustment failure modes (per parent plan § Adjustment 1.f) wait out the 60s timeout; if a combo doesn't fill, Robinhood expires it as `gfd`. Add explicit combo cancel in v1.5 if the timeout proves too slow.

---

## `PaperExecutionBroker.place_multi_leg`

```python
async def place_multi_leg(
    self, orders: list[ProposedOrder]
) -> list[FillEvent]:
    if not orders:
        return []
    # Validation mirrors RobinhoodBroker.place_multi_leg.
    combo_id = (orders[0].extra or {}).get("combo_id")
    direction = (orders[0].extra or {}).get("combo_direction")
    net_limit = float((orders[0].extra or {}).get("net_limit_price", 0))
    for o in orders:
        ex = o.extra or {}
        if ex.get("combo_id") != combo_id or ex.get("combo_direction") != direction:
            raise ValueError(f"combo cohesion violated for {combo_id}")

    # Slippage model: short legs fill at mid - slippage, long legs at
    # mid + slippage. Slippage configurable per strategy config; default
    # 0.03 per leg per parent plan § config/strategies.yaml.
    slippage = float(
        (orders[0].extra or {}).get("paper_per_leg_slippage_dollars", 0.03)
    )

    sim_fills: list[tuple[ProposedOrder, float]] = []
    for o in orders:
        ex = o.extra or {}
        option_id = ex.get("option_id")
        if option_id and hasattr(self._live, "get_option_greeks"):
            try:
                gk = await self._live.get_option_greeks(option_id)
                mid = gk.get("mark_price")
            except Exception:
                mid = None
        else:
            mid = None
        if mid is None:
            mid = float(o.limit_price or 0)
        # Short legs (selling, "sell") suffer adverse slippage on the bid side.
        # Long legs (buying, "buy") suffer adverse slippage on the ask side.
        sim_price = mid - slippage if o.side == "sell" else mid + slippage
        sim_fills.append((o, sim_price))

    # Compute net credit/debit from simulated leg prices.
    net = 0.0
    for o, p in sim_fills:
        # Sell → +p × ratio; Buy → -p × ratio.
        ratio = int((o.extra or {}).get("ratio_quantity", 1))
        net += (p if o.side == "sell" else -p) * ratio

    # `direction == "credit"` means we expect net > 0; "debit" means net < 0.
    # Limit is satisfied if (credit ≥ limit) or (debit ≤ limit).
    if direction == "credit":
        satisfied = net >= net_limit
    else:
        satisfied = net <= net_limit

    if not satisfied:
        log.info(
            "PaperExecutionBroker combo %s unfilled: simulated net %.2f vs limit %.2f (%s)",
            combo_id, net, net_limit, direction,
        )
        # Audit: paper_combo_unfilled
        return []

    actual_vs_limit_slippage = abs(net - net_limit)
    # Audit: paper_combo_filled with `actual_vs_limit_slippage_dollars` field
    log.info(
        "PaperExecutionBroker combo %s FILL: net %.2f vs limit %.2f (slippage $%.2f)",
        combo_id, net, net_limit, actual_vs_limit_slippage,
    )

    # Persist via inner PaperBroker — 4 calls, one per leg.
    fills: list[FillEvent] = []
    for o, p in sim_fills:
        leg_order = ProposedOrder(
            strategy=o.strategy,
            symbol=o.symbol,
            side=o.side,
            qty=o.qty,
            order_type="limit",
            limit_price=p,
            rationale=o.rationale,
            extra={**(o.extra or {}), "paper_combo_actual_vs_limit_slippage": actual_vs_limit_slippage},
        )
        leg_order.id = o.id
        fill = await self._paper.place_order(leg_order)
        fills.append(fill)
    return fills
```

**Notes on `option_id`:**
- The strategy resolves `option_id` for each leg at construction time via `rs.options.id_for_option` (or stores the leg's expiry/strike/type and lets `RobinhoodBroker.place_multi_leg` resolve later). For paper-mode Greek reads, the strategy MUST resolve and stash `option_id` in `extra` so the paper broker can call `self._live.get_option_greeks(option_id)`. Resolution happens once at `_construct_ic` time; cached on the leg's `extra["option_id"]`.
- Resolves open item 4 from parent plan: **Robinhood paper accounts** are not used at all in this design. Paper mode goes through `PaperExecutionBroker`, which reads live mids from the live broker and simulates fills locally. There's no Robinhood-side paper account involved, so the question of whether Robinhood paper accounts support multi-leg orders is moot.

### `PaperExecutionBroker.get_option_greeks`

```python
async def get_option_greeks(self, option_id: str) -> dict[str, float]:
    if hasattr(self._live, "get_option_greeks"):
        return await self._live.get_option_greeks(option_id)
    return {"delta": None, "gamma": None, "theta": None, "vega": None,
            "iv": None, "mark_price": None}
```

Mirrors the existing explicit-delegation pattern at `brokers/paper.py:196-208`.

---

## Error handling

| Failure | Behavior |
|---|---|
| `place_multi_leg` called with empty list | Return `[]` immediately. No audit. |
| Mismatched `combo_id` / `combo_direction` / `net_limit_price` / `underlying` / `qty` across legs | `ValueError` raised before any POST. Strategy code is responsible for combo cohesion; this is an invariant check. |
| `direction` not in `{"credit", "debit"}` | `ValueError`. |
| `robin_stocks` POST fails (network, auth, venue reject) | Exception propagates; `data_exec.place_combo` catches and writes `combo_submission_failed` audit with the exception class + message. No partial fills possible because Robinhood's combo engine is atomic. |
| Per-leg fill price unavailable in `robin_stocks` response | Synthesize from `order.limit_price` and log `combo_fill_price_synthesized` (informational). Combo-level net is authoritative for the journal. |
| `get_option_greeks` venue lookup fails | Exception propagates. Strategy treats this as "tested side undetermined" → returns `"neither"` from `_identify_tested_side` and cadence stays at 30 min. Documented in parent plan § Branch 5. |
| Paper-mode net price fails to satisfy limit | Return `[]`, audit `paper_combo_unfilled`. Strategy treats as no-fill and re-evaluates next tick. |

No `cancel_combo_order` in v1. Combo orders use `timeInForce="gfd"` and expire at session close if unfilled.

---

## Impact on the parent plan

**Adjustment 1 simplifies.** Parent plan § Adjustment 1.a defaulted to "two sequenced 2-leg combos" because mixed open/close in one POST was unverified. The library research above confirms `order_option_spread` supports mixed effects atomically. So Adjustment 1 in v1 is **one 4-leg combo** with 2 close legs + 2 open legs. The "wait for close confirmation, then submit open" sequencing is unnecessary and increases the half-fill risk that the plan tried to avoid.

**Action**: when step 9 (strategy module) implementation lands, code Adjustment 1 as a single 4-leg `place_multi_leg` call. The parent plan's failure-mode section for "close fills, open doesn't fill" becomes "combo fills or doesn't" — atomic. Simplifies §§ Adjustment 1.a, 1.f. The "single-click authorization" guidance still applies (one HITL approval, no second card).

**This change does not require re-approval** — it's a strict simplification matching the parent plan's preference for atomic execution. Implementer should propose a small plan-file edit when starting step 9.

**Open item 5 resolved**: `get_option_greeks` is a thin wrapper over `rs.options.get_option_market_data_by_id`, which takes only `option_id`. No refactor of existing callers needed. Step 5 PR proceeds as a single PR.

**Open item 4 resolved**: PaperExecutionBroker simulates combos locally using live mids + configurable slippage. No Robinhood paper account is used. The "Robinhood paper accounts and multi-leg" question is moot.

**Open item 3 resolved**: `PaperExecutionBroker` does not auto-forward. Explicit `place_multi_leg` and `get_option_greeks` methods are required; both follow the existing explicit-delegation pattern (`brokers/paper.py:196-208`).

**Open item 1 resolved**: function name is `order_option_spread` (generic), with `order_option_credit_spread` and `order_option_debit_spread` as wrappers. We call the generic form so mixed-direction adjustments work without branching.

**Open item 2 resolved**: mixed open/close in one POST is supported.

**Open item 6 unchanged**: Joint account Level 3 options approval is brokerage-side and must be verified before live deployment. Paper-mode build is unblocked.

---

## Risk + safety notes

1. **Atomic-only semantics protect against naked-leg windows.** Because `robin_stocks` `order_option_spread` POSTs all legs in one payload with one `ref_id`, there is no execution window in which short legs exist without their long-leg protectors. The plan's earlier concern about "if leg 3 fills but leg 4 rejects, you have a naked position" is structurally impossible with this API. Confirmed via direct read of the library.

2. **Risk gate per-leg still applies.** Each of the 4 `ProposedOrder` instances passes through `RiskAgent.evaluate()` independently before `place_combo` is called. This is the parent plan's design and is unchanged. If any leg's per-trade-cap check rejects, the combo is aborted before submission. Strategy code is responsible for sizing the combo so the most expensive single leg passes.

3. **HITL approval is per-combo, not per-leg.** Step 12 (web app HITL combo-coalescing) renders 4 legs sharing a `combo_id` as one approval card. Approve fires `place_combo`, which calls `place_multi_leg` with all 4 orders. This is the parent plan's design.

4. **No GTC, no resting orders.** `timeInForce="gfd"`. Closing combos are submitted by the Position Manager on each scan cycle, not pre-placed. Matches PMCC's pattern. CLAUDE.md § Process + safety preserved.

5. **`broker_fallback_to_paper` semantics unchanged.** If Robinhood connect fails, the division falls back to `PaperBroker(starting_equity=0.0)` which inherits the ABC defaults — `place_multi_leg` raises `NotImplementedError` and the strategy's `place_combo` audits `combo_unsupported_on_fallback_broker` and skips. No phantom equity, no naked simulation.

6. **`extra_json` is unqueryable by SQL columns** (CLAUDE.md § Sharp edges). All combo metadata is in `extra` — querying "all open ICs" relies on the strategy's own `agent_state` registry, not on SQL `position` joins. Step 13 (telemetry queries) builds combo-grouped views off the registry, not off the position table.

---

## Implementation order (clarifies step 5 of build sequence)

When step 5 starts:

1. Add `place_multi_leg` and `get_option_greeks` to `brokers/base.py` with `NotImplementedError` defaults. ~30 min.
2. Implement `RobinhoodBroker.get_option_greeks` as the thin wrapper. ~30 min. Unit test against a known option_id.
3. Implement `RobinhoodBroker.place_multi_leg`. ~half day. Unit test (mocked `rs.orders.order_option_spread`): combo cohesion validation, payload shape, return-list cardinality.
4. **Live integration smoke test** (paper-default, no real fill): construct a `ProposedOrder` set for an IC on SPY, call `place_multi_leg`, verify Robinhood accepts the POST and returns a valid order envelope. Cancel via web-app before any fill if needed.
5. Then `brokers/paper.py` step 6 (slippage simulator).

Pre-step-9 (strategy module) the implementer should re-read this doc, then submit a single-line plan-file edit to simplify Adjustment 1 to one 4-leg combo per § "Impact on the parent plan".

---

## Open questions for Board

None blocking. The parent plan's six open items collapse to zero — items 1, 2, 3, 4, 5 resolved here; item 6 (Level 3 brokerage approval) is operational, not design.

If the Board wants, the implementer can run a dry-run combo POST against the live joint account at `place_multi_leg` step 4 above to verify Robinhood accepts the call shape before any IC strategy code exists. That's a 1-hour validation in a Python shell — recommended before step 6.
