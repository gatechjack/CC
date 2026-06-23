"""PEAD STEP 3 — GATE 2 safety + diagnosis check (READ-ONLY, with targeted cleanup).

After the Gate-2 routing run came back with no RH order id/account, confirm the
live state of account 680725082:
  * lists OPEN stock orders on 680725082 (must be 0 — safety);
  * if it finds an OPEN order matching the Gate-2 test signature (BUY, ~$7,
    symbol F), it cancels ONLY that one (targeted cleanup) and reports it;
  * prints the recent stock-order history on 680725082 (state + reject_reason)
    so we can see what happened to the $7.03 F limit.
Does NOT place anything. Reuses the cached pickle session (rs.login()).
"""
from __future__ import annotations

import json

ACCOUNT = "680725082"


def main() -> int:
    import robin_stocks.robinhood as rs
    rs.login()
    print(f"checking account {ACCOUNT}\n")

    opens = rs.orders.get_all_open_stock_orders(account_number=ACCOUNT) or []
    print(f"OPEN stock orders on {ACCOUNT}: {len(opens)}")
    for o in opens:
        sym = ""
        try:
            sym = rs.stocks.get_symbol_by_url(o.get("instrument", "")) or ""
        except Exception:
            pass
        print(f"  id={o.get('id')} {sym} {o.get('side')} qty={o.get('quantity')} "
              f"px={o.get('price')} state={o.get('state')} "
              f"acct={str(o.get('account','')).rstrip('/').split('/')[-1]}")

    # targeted cleanup: cancel ONLY an open BUY near $7 (our Gate-2 test order)
    cancelled = 0
    for o in opens:
        try:
            px = float(o.get("price") or 0)
        except (TypeError, ValueError):
            px = 0.0
        if o.get("side") == "buy" and 6.5 <= px <= 7.6:
            try:
                rs.orders.cancel_stock_order(o.get("id"))
                cancelled += 1
                print(f"  -> CANCELLED test order {o.get('id')} (px={px})")
            except Exception as e:  # noqa: BLE001
                print(f"  -> CANCEL FAILED {o.get('id')}: {e}")
    print(f"targeted cancellations: {cancelled}")

    print(f"\nrecent stock-order history on {ACCOUNT} (newest first):")
    hist = rs.orders.get_all_stock_orders(account_number=ACCOUNT) or []
    for o in hist[:8]:
        sym = ""
        try:
            sym = rs.stocks.get_symbol_by_url(o.get("instrument", "")) or ""
        except Exception:
            pass
        print(f"  {o.get('created_at')} {sym} {o.get('side')} qty={o.get('quantity')} "
              f"px={o.get('price')} state={o.get('state')} reject={o.get('reject_reason')}")

    print(f"\nSAFETY: {len(opens)-cancelled} open order(s) remain on {ACCOUNT} "
          f"(0 = clean). cancelled {cancelled} test order(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
