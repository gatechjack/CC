# CP2 — Kalshi execution layer (dry-run)

**Status:** complete. **Dry-run only — zero real orders, no live money.** Holding at the CP2 gate;
CP3 not started. Auth to Kalshi is live/read-only (from CP1); the placement path defaults to
`dry_run=True` and stops before the network POST.

Every claim below traces to a paste or a `file:line`. No order object is synthesized — all come
from real CP1-matched whale actions routed through the executor in simulation.

---

## 1. Duplicated placement (not shared, not imported from kalshi_copy_trader)

New module: `trading_corp/agents/strategies/poly_kalshi_executor.py`. Per the ratified DUPLICATE
decision, the pure placement helpers are **copied byte-for-byte** from `trading_corp/brokers/kalshi_live.py`
(`round_to_cent`, `usd_to_contracts`, `client_order_id`, `v2_side_and_price`, `build_v2_event_order`).
`kalshi_copy_trader` is neither imported nor referenced.

Proof (`cp2_01_dup_diff.py`, via `inspect.getsource`):
```
IDENTICAL  round_to_cent
IDENTICAL  usd_to_contracts
IDENTICAL  client_order_id
IDENTICAL  v2_side_and_price
IDENTICAL  build_v2_event_order
OK  const _COID_NAMESPACE ... _TIF ... _V2_ORDERS_PATH  (all equal)
ALL COPIED HELPERS BYTE-IDENTICAL: True
```
The live V2 POST is duplicated too — `await self._broker._client().post(_V2_ORDERS_PATH, order.body)`
in `PolyKalshiExecutor.submit()` — gated behind `dry_run` (default True; not exercised in CP2).

## 2. Whale action → Kalshi order (explicit side mapping)

**Only TRADE BUY/SELL are copy signals.** `translate_whale_action()` maps:
- whale **BUY → entry**, V2 side **`bid`** (buy YES), `reduce_only=False`
- whale **SELL → exit**, V2 side **`ask`** (sell YES), `reduce_only=True`

**YES/NO logic (explicit):** the CP1 matcher resolves the KXMLBGAME ticker whose YES side *is* the
club the whale bet, so this strategy **always trades the YES leg** and **never places NO**. A bet on
the opponent resolves to the opponent's own YES ticker instead. Hand-checked on real data:
```
[HOME club bet]  slug=mlb-col-sf-2026-08-16 (away=COL, home=SF)
   outcome='San Francisco Giants' = HOME club
   ticker KXMLBGAME-26AUG161605COLSF-SF ; '-SF' == "SF wins == YES"
   => BID (BUY YES), outcome=yes, never NO

[AWAY club bet]  slug=mlb-tex-oak-2026-08-16 (away=TEX, home=OAK)
   outcome='Texas Rangers' = AWAY club
   ticker KXMLBGAME-26AUG161605TEXATH-TEX ; '-TEX' == "TEX wins == YES"
   => BID (BUY YES), outcome=yes, never NO
```
Exit path is built + unit-tested (`test_exit_sell_is_yes_ask_reduce_only`: SELL → `ask` + `reduce_only=True`).
See §6 for why exits don't fire in the live sample.

## 3. Idempotency (one whale action → at most one order)

UUID5 `client_order_id` over `division | whale | ticker | outcome | action` (action ∈ entry/exit),
fixed namespace. Replaying the same action returns the same key → suppressed. Distinct entry vs exit
keys (`test_entry_and_exit_are_distinct_keys`). Multiple partial fills of the same entry collapse to
ONE order (desired: copy the position once, not once per fill).

## 4. Dry-run proof (real data, `cp2_00_dryrun.py`)

```
matched>=0.97 whale actions produced : 337    (2 whales, recent-500 activity each)
distinct idempotency keys (routed)   : 116     <- 221 partial-fill repeats deduped in-batch
DRY_RUN_would_place                  : 116
suppressed_duplicate (within batch)  : 221
```
5 fully-formed order objects (whale action in → order out):
```
BUY 'San Francisco Giants' mlb-col-sf-2026-08-16  -> {ticker KXMLBGAME-26AUG161605COLSF-SF, side bid, count 3, tif ioc, limit 0.5700, reduce_only false, key 137f08ba-…}
BUY 'Texas Rangers'        mlb-tex-oak-2026-08-16  -> {ticker KXMLBGAME-26AUG161605TEXATH-TEX, side bid, count 3, tif ioc, limit 0.5800, key dff3bffc-…}
BUY 'Boston Red Sox'       mlb-bos-pit-2026-08-16  -> {ticker KXMLBGAME-26AUG161335BOSPIT-BOS, side bid, count 4, tif ioc, limit 0.5200, key 10b65513-…}
BUY 'Arizona Diamondbacks' mlb-ari-atl-2026-08-16  -> {ticker KXMLBGAME-26AUG161335AZATL-AZ,   side bid, count 4, tif ioc, limit 0.4700, key e5bf3bb5-…}
BUY 'Baltimore Orioles'    mlb-bal-tb-2026-08-16   -> {ticker KXMLBGAME-26AUG161215BALTB-BAL,  side bid, count 4, tif ioc, limit 0.4400, key f98c5735-…}
```
Replay the same 5 → all `suppressed_duplicate`, **0 new placements** (`placements before=116 after=116`).

(`stake_usd=2.00` and `max_slippage=2c` are **dry-run placeholders**; the real fixed stake is a CP5
operator gate, the slippage cap is CP3. `count = floor(stake / base_price)`; `base_price` in the
dry-run is the whale's Poly fill price as a proxy — live will size off the fetched Kalshi YES quote.)

## 5. Guardrail insertion points (mapped, NOT implemented — that's CP3)

All in `poly_kalshi_executor.py` `PolyKalshiExecutor.submit()` (grep the markers):
| guardrail | marker | state |
|---|---|---|
| daily-loss auto-halt (RiskAgent / StrategyState.persist_halt) | `[G-halt]` | CP3 |
| per-trade size cap (fixed stake) | `[G-size]` | CP3 |
| auto-execute threshold (>= 0.97) | `[G-conf]` | **active (this CP)** |
| idempotency / dedup | `[G-idem]` | **active (this CP)** |
| in-memory daily deployment cap (NOT audit_event query) | `[G-daily]` | CP3 |
| max-slippage on market order | `[G-slip]` | cap applied in build; live-book check CP3 |
The dry-run path passes THROUGH these gates (threshold + idempotency enforced); it does not bypass
them. Order of checks: halt → size → threshold → idempotency → daily-cap → slippage → (POST).

## 6. Finding: these whales are hold-to-resolution (exit-copy rarely fires)

Recent-500 activity `type`/`side` distribution (`cp2` side-dist probe):
```
SDTrading      : TRADE=370 (side: BUY 370, SELL 0), REDEEM 104, TAKER_REBATE 21, MAKER_REBATE 5
0x0x23kjookhai : TRADE=454 (side: BUY 454, SELL 0), REDEEM 42
```
**Zero SELL trades.** These whales exit by holding to game resolution (REDEEM), not by selling — so
the whale-SELL exit-copy trigger essentially never fires for them; the Kalshi YES position likewise
self-settles at game end. The exit path is built and unit-tested, but **for this whale set, Phase 1 is
effectively entry-copy + natural resolution.** (Decision input for you — not something I resolved.)

**Bug fixed as a result:** REDEEM/rebate rows carry an empty `side`; the first cut mapped "not BUY" →
"exit", which would mis-copy a REDEEM as an exit-sell. `translate_whale_action` now **rejects any
`whale_side` ∉ {BUY, SELL}** (`test_non_trade_side_rejected_not_treated_as_exit`), and the loop filters
`type=='TRADE'`. Without this, resolution events would have generated phantom sell orders.

## 7. Tests
`tests/test_poly_kalshi_executor.py` — 9 tests: entry→bid, exit→ask+reduce_only, away/home both YES,
idempotency replay suppression, distinct entry/exit keys, below-threshold skip, non-TRADE rejection,
dry-run needs no broker. All green (with `-p no:pytest_ethereum`).

## 8. Unchanged-files proof
`git diff` shows **no change** to `kalshi_copy_trader.py`, `sports_team_mapping.py`, or
`kalshi_live.py` (source of the copy). Verified below at commit time.

## 9. What I did NOT do (gate discipline)
- No live orders, no live money (dry_run default; POST gated).
- Guardrails NOT wired (only threshold + idempotency active; halt/size/daily-cap/slippage are CP3).
- No config / divisions / main.py wiring, no scheduled loop (CP4).
- `kalshi_copy_trader.py` / `sports_team_mapping.py` / `kalshi_live.py` untouched.

## 10. Open for CP3
Wire + test the five guardrails at the mapped insertion points; the in-memory daily cap MUST be a
running counter, never the `audit_event` aggregate query (froze the engine 2026-06-16).
Decision to carry: exit-copy handling given hold-to-resolution whales (§6).
