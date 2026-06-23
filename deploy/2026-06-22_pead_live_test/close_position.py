"""One-off recovery: market-SELL the open PEAD test position on 680725082 to flat.
Confirms the held qty first, sells, confirms routing (account==680725082), polls
the fill, verifies flat. Env: PEAD_RT_SYMBOL (default F), PEAD_RT_QTY (default 1)."""
import json
import os
import time

import robin_stocks.robinhood as rs

ACC = "680725082"
SYM = os.environ.get("PEAD_RT_SYMBOL", "F").upper()
QTY = int(os.environ.get("PEAD_RT_QTY", "1"))


def held_qty():
    pos = rs.account.get_open_stock_positions(ACC) or []
    q = 0.0
    for o in pos:
        try:
            s = rs.stocks.get_symbol_by_url(o.get("instrument", ""))
        except Exception:
            s = ""
        if s == SYM:
            q = float(o.get("quantity") or 0)
    return q


rs.login()
before = held_qty()
print(f"{SYM} qty on {ACC} before = {before}")
if before < QTY:
    print(f"only {before} {SYM} held (< {QTY}) — NOT selling; verify manually.")
    raise SystemExit(0)

res = rs.orders.order_sell_market(SYM, QTY, account_number=ACC, timeInForce="gfd") or {}
acct = str(res.get("account", "")).rstrip("/").rsplit("/", 1)[-1]
oid = res.get("id")
print(f"SELL placed: id={oid} account={acct!r} state={res.get('state')!r}  (account must be {ACC})")
if not oid:
    print("no order id in response:", json.dumps(res, default=str)[:600])
    raise SystemExit(1)

for _ in range(20):
    info = rs.orders.get_stock_order_info(oid) or {}
    st = info.get("state")
    if st == "filled" and info.get("average_price"):
        print(f"SOLD: filled at ${info.get('average_price')}")
        break
    if st in ("cancelled", "rejected", "failed"):
        print(f"sell ended {st}: {info.get('reject_reason')}")
        break
    time.sleep(1.5)

after = held_qty()
print(f"{SYM} qty on {ACC} after = {after}   {'FLAT' if after < 1e-4 else 'STILL HOLDING — VERIFY MANUALLY'}")
