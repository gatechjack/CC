# PEAD fractional / notional sizing — SCOPE (build NOT started)

Status: **scope only.** Phase A read-only discovery DONE (empirical, on agentic
acct 680725082, no orders placed). Build gated on operator go. This doc is the
artifact the build is reviewed against.

Branch `robinhood-pead-2026-06-20`. Goal: equal-DOLLAR sizing — same dollars per
qualifying candidate regardless of share price ($400 TSLA and $14 F get the same
dollars); fractional shares to RH's $1 notional minimum. The whole-share `floor()`
was our assumption, not an RH limit.

## LOCKED DECISIONS
- **Equal-dollar sizing**, config-driven. Use **`position_pct × equity` as the
  dollar notional** (already wired at 0.10; auto-scales with funding; same $ for all
  candidates in a scan; `max_concurrent × position_pct ≤ 1.0` keeps the book within
  equity). Optional **`position_notional`** fixed-$ override for plumbing tests.
  Floor at **$1** (RH minimum).
- **Place by dollars; record REALIZED qty from the fill, never the requested qty.**
  (Recording requested-but-unfilled = the phantom bug in new clothes — forbidden.)
- **Fractional-ineligible name → SKIP** (no whole-share fallback).
- **Cash-account GFV discipline unchanged** — settled funds only, T+1.
- **#2 partial fill → (a) ACCEPT + manage the realized partial position** (operator-
  recommended, agent-decided). Equal-weight makes a slight under-fill harmless;
  fewer orders = less GFV surface. Record realized qty; **log/flag a warning when
  realized notional < ~90% of requested** (observability only, no top-up).
- **#4 exit price = REALIZED average fill price** (poll the sell), not the quote-at-
  decision. This also **fixes an existing inaccuracy in the whole-share path** —
  today `_close_record` books the quote as exit price, so current prod P&L records
  are approximations. Worth doing for its own sake.
- **#3 broker change ADDITIVE + ISOLATED** — whole-share / limit / option paths
  unchanged **byte-for-byte**; regression tests prove no behavior change for the
  other live divisions (PMCC / robinhood_joint / IC), same bar as the multi_leg ==
  iron_condor baseline. Highest-risk review surface.
- **#1 poll-until-filled on BOTH entry and exit**, with a timeout; queued-past-
  timeout → don't hang, don't assume a fill, **cancel + surface** (no record).
- **#5** skip a candidate when settled BP is exhausted. **#6** cache the per-
  candidate fractional-eligibility lookup.

## PHASE A — DISCOVERY (empirical, read-only, acct 680725082)
**Functions exist in our prod robin_stocks** (with `account_number`):
- `order_buy_fractional_by_price(symbol, amountInDollars, account_number, timeInForce='gfd', extendedHours=False, market_hours='regular_hours')`
- `order_sell_fractional_by_quantity(symbol, quantity, account_number, timeInForce='gfd', priceType='bid_price', market_hours='regular_hours')`

**KEY (source-confirmed): `order_buy_fractional_by_price` converts $→shares CLIENT-
SIDE** — it fetches `get_latest_price(symbol,'ask_price')`, computes
`fractional_shares = round_price(amountInDollars/price)` (6 dp), then places a
QUANTITY order via the generic `order()`. It is NOT a true RH server-side notional
order. It **hard-enforces ≥ $1** (returns `None` below) and returns `None` if the
price fetch fails. `order_sell_fractional_by_quantity` passes our qty straight to
`order()`.
→ Decision: **Option A** (use `_by_price`). The core invariant is preserved because
we **discard the client-computed request qty and record `cumulative_quantity` from
the fill**. (Option C = true RH dollar order via custom `dollar_based_amount`
payload — more work, only if exact-dollar fidelity is ever required. Not now.)

**Eligibility** (read-only verified): `instrument['fractional_tradability']` —
`F`/`TSLA`/`RKLB` all `'tradable'`. Skip rule for entry: `!= 'tradable'`. A
`'position_closing_only'` value exists (sellable, not buyable) — exits still allowed.

**Fill→qty** (confirmed from a real order's `get_stock_order_info`): placement is
**queued** (`cumulative_quantity:"0"`, `average_price:null`, `executed_notional:null`)
→ polling mandatory. Realized truth fields: **`cumulative_quantity`** (realized
shares), **`average_price`** (avg fill), **`executed_notional`** ($ filled), plus
`requested_notional_amount`/`total_notional` (requested), `state`, `fees`. Partial =
`cumulative_quantity < quantity` (state `partially_filled` then terminal).

**READ-ONLY LIMIT (flagged):** that a notional order actually FILLS, that
`cumulative_quantity`/`average_price` populate as expected, and real partial-fill
behavior — **cannot be confirmed read-only** (a fill is real money + market-open).
That empirical confirmation IS the Phase C real-API proof (tiny notional round-trip,
watched, market-open). The broker path is BUILT + unit-tested against the read-only-
confirmed shape, then PROVEN live before trust/deploy (same pattern as Gates 3-4).

## PHASE B — SCOPE (changes, against confirmed reality)
1. **`ProposedOrder`**: add `notional_usd: float | None` (additive, default None).
   Entry sets it; entry no longer sets a pre-computed `qty`.
2. **`pead_strategy.scan` sizing** (~:320): replace `qty = floor(position_pct·equity/
   price)` + `qty<1` skip with `notional = clamp(position_pct·equity, ≥1)` (or the
   override). Skip if fractional-ineligible (#6, cached) or settled BP exhausted (#5).
3. **Broker — new ISOLATED notional branch** in `_place_stock_order` (#3 — do NOT
   touch the existing `order_buy_market`/limit/option calls):
   - buy + `notional_usd` set → `order_buy_fractional_by_price(sym, notional_usd,
     account_number=acct, timeInForce='gfd')`; handle `None` return (<$1 / price-fail)
     → raise/skip (no fake fill — Bug-1 discipline).
   - sell + fractional qty → `order_sell_fractional_by_quantity(sym, qty, account_number=acct)`
     (the current whole-share `order_sell_market(int(qty))` would floor 0.347→0 — the
     fractional exit MUST use `_by_quantity` with the float qty).
   - **`_fill_or_raise` id-check unchanged** (raise on no-id). Then **poll until filled**
     → set `FillEvent.qty = cumulative_quantity`, `price = average_price` (realized,
     not request). Add `FillEvent.executed_notional: float | None` (additive audit).
4. **Poll-until-filled helper** (entry + exit): `get_stock_order_info(id)` until
   terminal; on `filled`/`partially_filled→terminal` read realized; on timeout →
   cancel + surface + no record. (Mirror the harness guard now in gate34.)
5. **Position record / P&L**: store realized fractional qty. Math already fractional-
   clean — `_close_record` `pnl=(exit−entry)*qty`, cost basis `entry*qty` (floats).
   **Exit price = realized `average_price`** (#4), not `last`.
6. **Pressures: UNCHANGED** — `pead_pressures.py` has ZERO qty refs (verified);
   `compute_pressures(prim, last, held, d2n)` is pure price/time. Fractional qty
   never touches pressure math.

Round-to-zero skip: gone (any name affordable ≥$1). Remaining skips: notional<$1
(only if equity<~$10), ineligible, settled-BP-exhausted, price-fetch-fail.

## PHASE C — TEST PLAN
**Adversarial unit tests:** notional sizing (same $ across $400 & $14 names; ≥$1;
override); poll-until-filled reads `cumulative_quantity` as realized qty; FillEvent
carries realized qty+avg price (not request); fractional qty round-trips through
record + P&L + cost basis; ineligible-name skip; broker RAISES on failed notional
(Bug-1 holds; `None` return handled); partial-fill records realized cumulative_qty +
trips the <90% flag; **exit sells stored fractional qty via `_by_quantity` (proves
the int()-floor bug gone)**; **#3 regression: whole-share/limit/option paths
byte-for-byte unchanged, other-division placement behavior identical.**

**Real-API proof (Phase C, gated/watched, real $):** tiny notional buy (~$5 F) in a
confirmed-open session → poll → realized ~0.35 sh → `/telemetry/pead` renders the
fractional position → deliberate stop → `manage()` sells the fractional qty via
`_by_quantity` → clean prod close with realized P&L. Re-proves the live execution
path (which changed). GFV: settled funds, market-open guard (already in gate34).

## RISKS / FLAGS
1. **#3 shared broker (HIGHEST):** the notional branch sits in the code we just
   fixed+deployed (fake-fill) and shared by live PMCC/joint/IC. Additive + isolated;
   prove the other paths byte-identical.
2. **Mandatory polling on the critical path** (entry + exit) — the realized qty is
   unknowable without it. Timeout → cancel + surface, never assume.
3. **Partial fills are real** — record realized cumulative_quantity; flag <90%.
4. **`order_buy_fractional_by_price` is client-side $→shares** (ask-based, $1-min,
   `None` on fail) — Option A accepted; invariant preserved via realized read-back.
5. **Cash-account settled-BP guard** at capacity (notional spends settled; T+1).
6. **Existing whole-share P&L is approximate** (quote-as-exit); #4 fixes it for all.
