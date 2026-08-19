# ITEM 2 — probe result (READ-ONLY, Board-authorized, ran 2026-08-18)

Ran `pk_markprobe_ro.ps1` against prod (az run-command, read-only; no order, no mutation). First run went stub (KAREN not in `.env`); fixed by hydrating `KEY_VAULT_URI` from the running engine env so `load_secrets()` pulls KAREN from KeyVault via managed identity (secrets.py:375-377). Second run succeeded.

## VERDICT: attribute-mismatch (NOT empty book). Definitive.

- **pykalshi `__version__` = 1.0.6** (matches the `brokers/kalshi.py:245` comment; the memory trap's "2.0.0" was wrong).
- Probe ticker: `KXMLBGAME-26AUG212210PITLAD-PIT`, `market.status = MarketStatus.ACTIVE`.
- **The book is FULL**, so this is not a data problem:
  - depth=1: `OrderbookResponse(orderbook=Orderbook(yes_dollars=[('0.2600','911.00')], no_dollars=[('0.6400','1131.35')]))`
  - depth=32: 14 yes levels + 13 no levels populated.
- **The attributes `quote()` reads do not exist on the object:**
  - `ob.yes_bids` = `<MISSING>`, `ob.yes_asks` = `<MISSING>`, `ob.no_bids`/`ob.no_asks` = `<MISSING>`.
  - Real object = `pykalshi.models.OrderbookResponse`, `__dict__ keys = ['orderbook']`. The book lives under `ob.orderbook` = `Orderbook(yes_dollars=[(price,size)], no_dollars=[(price,size)])` — dollar-string prices.
- **Live bug reproduced:** `KalshiBroker.quote(tk) = 0.0`.
- **Working reference path (slippage guard's source) returns real prices:** `market.yes_ask_dollars='0.3600'`, `market.yes_bid_dollars='0.2600'` — top-of-book, matches the orderbook best (best_yes_bid 0.26; best_yes_ask = 1 - best_no_bid 0.64 = 0.36).

### Kalshi 1.0.6 orderbook convention (why the old code was doubly wrong)
`Orderbook` exposes only **bids on each side**: `yes_dollars` (YES bids) and `no_dollars` (NO bids). There is **no** `yes_asks` array — the YES **ask** is derived as `1 - best_no_bid`. The old `getattr(ob,"yes_asks")` could never have worked even with the right container. `OrderbookResponse` also exposes computed helpers in `dir()`: `best_yes_bid`, `best_yes_ask`, `best_no_bid`, `mid`, `spread`, `imbalance`, `yes_depth`, `no_depth`.

## EXACT FIX (finalized) — `brokers/kalshi.py` `quote()` (276-306), MarketModel top-of-book
`quote()` already fetches `market = await self._client.get_market(symbol)` (line 288). Read the proven `yes_bid_dollars`/`yes_ask_dollars` dollar-strings off it and return the mid — the **identical source** `main._pk_quote_fn` uses in production. Drop the broken `get_orderbook()` + `yes_bids/yes_asks` parse. Sketch:

```python
        try:
            market = await self._client.get_market(symbol)
        except Exception as e:
            log.warning("Kalshi quote failed for %s: %s", symbol, e)
            return 0.0
        # pykalshi 1.0.6: MarketModel exposes top-of-book as dollar strings
        # yes_bid_dollars / yes_ask_dollars (yes-ask derived from the no-bid side).
        # The prior get_orderbook().yes_bids/yes_asks parse was wrong for this version
        # (OrderbookResponse wraps Orderbook(yes_dollars=..., no_dollars=...) and exposes
        # NO yes_bids/yes_asks) -> getattr None -> 0.0 every call. Same source proven live
        # by main._pk_quote_fn.
        yes_bid = _dollar_or_none(getattr(market, "yes_bid_dollars", None))
        yes_ask = _dollar_or_none(getattr(market, "yes_ask_dollars", None))
        if yes_bid is not None and yes_ask is not None:
            return (yes_bid + yes_ask) / 2
        if yes_bid is not None:
            return yes_bid
        if yes_ask is not None:
            return yes_ask
        return 0.0
```
`_dollar_or_none(s)` = `float(s)` when it parses to a price in (0,1), else None. `_best_price` (kalshi.py:479) becomes unused by `quote()` (remove or leave; only `quote()` referenced it).

**Alternative (if we prefer to keep the orderbook call):** use `OrderbookResponse.best_yes_bid` / `best_yes_ask` / `mid` (present in `dir()`). Rejected as primary: extra API call + unverified units/None-semantics, whereas the MarketModel fields are proven live with known dollar-string values.

## Callers — re-confirmed against the fix
- `poly_kalshi_marks.py:113` — **fixed** (real yes-mid → marks/sparkline work).
- `kalshi_copy_trader.py:619-623` (byte-locked *caller*, unchanged) — 0.0→real mid on the still-trading exit fallback; guarded `if yes_mid>0`; resolution-first path unchanged. **Helps, can't break.**
- `portfolio.py:49` — Kalshi position marks 0→real. **Helps.**
- `kalshi_live.py:344` (place_order base-price fallback) — poly_kalshi never hits it. **FLAG: confirm kalshi_copy live status before deploy** (real base_price where it previously skipped).
- Slippage guard `_pk_quote_fn` — **does not call `quote()`; unaffected.** No live-placement risk.

## Item 2 checkpoint (SEPARATE from Item 1) — awaiting build ratification
1. `brokers/kalshi.py` `quote()` fix above + `_dollar_or_none` helper; drop broken orderbook parse.
2. Tests: `KalshiBroker.quote` mocking the real `AsyncMarket.yes_bid_dollars/yes_ask_dollars` → assert mid; one-sided → that side; missing → 0.0. Plus `test_poly_kalshi_marks.py` end-to-end with a stubbed quote → mark rows written.
3. Diff the 3 byte-locked files = unchanged. Full-suite base-vs-branch diff empty.
4. Post-deploy confirm: `poly_kalshi mark tick` shows `marked>0` (vs historical all-`quote_miss`).
