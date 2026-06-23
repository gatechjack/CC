"""Read-only diagnosis: the robinhood_pead paper_trade_record rows in the PROD DB
+ the live position on 680725082. Why did the exit's _open_rows (result IS NULL)
find nothing right after the entry wrote a row?"""
import sqlite3

import robin_stocks.robinhood as rs

DB = "/home/azureuser/trading_corp/data/trading_corp.db"
c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row
print("robinhood_pead paper_trade_record (newest 5):")
q = ("SELECT order_id, symbol, result, result_price, actual_pnl_dollars, "
     "execution_mode, entry_reference_price, ts FROM paper_trade_record "
     "WHERE division='robinhood_pead' ORDER BY ts DESC LIMIT 5")
for r in c.execute(q):
    print("  ", dict(r))
n_null = c.execute("SELECT COUNT(*) FROM paper_trade_record WHERE division='robinhood_pead' AND result IS NULL").fetchone()[0]
print("rows with result IS NULL:", n_null)

rs.login()
pos = rs.account.get_open_stock_positions("680725082") or []
print("positions on 680725082:", [(o.get("quantity"), o.get("average_buy_price")) for o in pos])
