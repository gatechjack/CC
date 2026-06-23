"""READ-ONLY ground-truth check of Robinhood agentic cash sub-account 680725082.

Calls only GET endpoints (positions / account profile / portfolio / open orders).
Places or modifies NOTHING. Uses the existing prod robin_stocks pickle session.
"""
import robin_stocks.robinhood as r

ACCT = "680725082"


def safe(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception as e:
        return f"ERR {type(e).__name__}: {e}"


print("=== login (existing pickle session) ===")
try:
    r.login()
    print("login OK")
except Exception as e:
    print(f"login ERR {type(e).__name__}: {e}")
    raise SystemExit(2)

print("\n=== OPEN STOCK POSITIONS on %s ===" % ACCT)
pos = safe(r.account.get_open_stock_positions, account_number=ACCT)
if isinstance(pos, str):
    print("  ", pos)
    pos = []
held = []
for p in (pos or []):
    try:
        q = float(p.get("quantity") or 0)
    except Exception:
        q = 0.0
    acct_url = p.get("account") or p.get("account_url") or ""
    if q > 0:
        sym = safe(r.stocks.get_symbol_by_url, p.get("instrument"))
        held.append((sym, q, p.get("average_buy_price"), acct_url))
if not held:
    print("  FLAT — no open stock positions with qty>0 on", ACCT)
else:
    for sym, q, avg, acct_url in held:
        print(f"  HELD: {sym}  qty={q}  avg_buy_price={avg}  account_url={acct_url}")

print("\n=== ACCOUNT PROFILE (cash / buying power) — %s ===" % ACCT)
ap = safe(r.profiles.load_account_profile, account_number=ACCT)
if isinstance(ap, dict):
    print("  account_number:", ap.get("account_number"))
    print("  type:", ap.get("type"), " brokerage_account_type:", ap.get("brokerage_account_type"))
    print("  buying_power:", ap.get("buying_power"))
    print("  cash:", ap.get("cash"), " portfolio_cash:", ap.get("portfolio_cash"))
    print("  only_position_closing_trades:", ap.get("only_position_closing_trades"))
else:
    print("  ", ap)

print("\n=== PORTFOLIO (equity / market value) — %s ===" % ACCT)
pp = safe(r.profiles.load_portfolio_profile, account_number=ACCT)
if isinstance(pp, dict):
    print("  equity:", pp.get("equity"))
    print("  market_value:", pp.get("market_value"))
    print("  extended_hours_equity:", pp.get("extended_hours_equity"))
else:
    print("  ", pp)

print("\n=== OPEN / RESTING ORDERS — %s ===" % ACCT)
oo = safe(r.orders.get_all_open_stock_orders, account_number=ACCT)
if isinstance(oo, str):
    print("  ", oo)
elif not oo:
    print("  NONE — no resting/open orders")
else:
    for o in oo:
        sym = safe(r.stocks.get_symbol_by_url, o.get("instrument"))
        print(f"  {o.get('side')} {sym} qty={o.get('quantity')} state={o.get('state')} "
              f"type={o.get('type')} price={o.get('price')} acct={o.get('account')}")

print("\n=== DONE (read-only; nothing placed or modified) ===")
