import inspect
import robin_stocks.robinhood as r
for name in ["order_buy_fractional_by_price", "order_sell_fractional_by_quantity"]:
    fn = getattr(r.orders, name)
    print(f"\n===== {name} SOURCE =====")
    try:
        print(inspect.getsource(fn))
    except Exception as e:
        print("  getsource err:", e)
