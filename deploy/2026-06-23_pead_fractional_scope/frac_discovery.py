import inspect, json
import robin_stocks.robinhood as r
import robin_stocks
r.login()
print("robin_stocks version:", getattr(robin_stocks, "__version__", "unknown"))
print("\n=== fractional/notional order function signatures + docstring head ===")
for name in ["order_buy_fractional_by_price","order_buy_fractional_by_quantity",
             "order_sell_fractional_by_quantity","order_sell_fractional_by_price",
             "order_buy_market","order_sell_market","order"]:
    fn = getattr(r.orders, name, None) or getattr(r, name, None)
    if not fn:
        print(f"  {name}: NOT FOUND"); continue
    try: sig = str(inspect.signature(fn))
    except Exception as e: sig = f"(sig err {e})"
    doc = (inspect.getdoc(fn) or "").splitlines()
    print(f"  {name}{sig}")
    if doc: print(f"      doc: {doc[0]}")
print("\n=== instrument fractional eligibility (read-only) ===")
for sym in ["F","TSLA","RKLB"]:
    try:
        inst = r.stocks.get_instruments_by_symbols(sym)
        d = inst[0] if inst else {}
        print(f"  {sym}: fractional_tradability={d.get('fractional_tradability')!r} "
              f"tradability={d.get('tradability')!r} min_tick_size={d.get('min_tick_size')!r} "
              f"name={(d.get('simple_name') or d.get('name'))!r}")
    except Exception as e:
        print(f"  {sym}: ERR {e}")
print("\n=== full order-info field set (cancelled routing-proof order) ===")
info = r.orders.get_stock_order_info("6a3aeaef-258f-4df4-b52c-33d293689b10") or {}
print("  all keys:", sorted(info.keys()))
for k in ["state","quantity","cumulative_quantity","average_price","price",
          "executed_notional","dollar_based_amount","fees","total_notional","type","side","account"]:
    print(f"    {k}: {json.dumps(info.get(k))}")
