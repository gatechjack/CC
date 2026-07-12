#!/usr/bin/env python3
"""READ-ONLY Bitunix venue reconciliation for the bitunix_futures TP-coverage audit (v2).

STRICTLY GET-ONLY. Constructs BitunixBroker, connect() (opens httpx client + a
read-only account snapshot), and calls ONLY:
    get_pending_positions   GET /futures/position/get_pending_positions
    get_history_trades      GET /futures/trade/get_history_trades   (symbol-scoped)
It NEVER calls any POST. Every known write method is monkeypatched to RAISE.

v2 change: get_order_detail is the ACTIVE-orders endpoint -> returns 20007
"order not found" for every CLOSED order, and the stored broker_order_id is the
internal uuid, not a venue orderId. So we pull the SYMBOL fill history ONCE and
attribute fills to trades by TIME WINDOW + side (entries = SELL, closes = BUY),
which is independent of order-id mapping and closed-order retention.
"""
import asyncio
import sys
import json
import sqlite3
from datetime import datetime

ROOT = "/home/azureuser/trading_corp"
DB = f"{ROOT}/data/trading_corp.db"
SINCE = "2026-07-06"
SYMBOL = "BTC/USDT.P"
sys.path.insert(0, ROOT)

from trading_corp.utils.secrets import load_secrets            # noqa: E402
from trading_corp.brokers.bitunix import BitunixBroker         # noqa: E402

FORBIDDEN = [
    "place_order", "place_tpsl_order", "place_position_tpsl",
    "place_resting_reduce_only_limit", "cancel_order", "cancel_all_orders",
    "flash_close_position", "close_all_position", "modify_position_sl",
    "modify_position_tp_sl_order", "change_position_mode", "change_leverage",
    "_observe_fill",
]


def _f(x):
    try:
        return float(x)
    except Exception:
        return 0.0


def _side(f):
    return str(f.get("side") or f.get("tradeSide") or "").upper()


def _q(f):
    return _f(f.get("qty") or f.get("tradeQty"))


def _px(f):
    return _f(f.get("price"))


def _real(f):
    return _f(f.get("realizedPNL") or f.get("realizedPnl") or f.get("realizedPnL"))


def _fee(f):
    return _f(f.get("fee"))


def _ct(f):
    return _f(f.get("ctime") or f.get("time") or f.get("ts") or f.get("createTime"))


def _to_ms(iso):
    try:
        return int(datetime.fromisoformat(iso).timestamp() * 1000)
    except Exception:
        return 0


def load_trades():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute(
        """SELECT order_id, side, qty, ts, result, actual_pnl_dollars,
                  json_extract(extra_json,'$.bracket_tp_order_ids')
           FROM paper_trade_record
           WHERE division='bitunix_futures' AND execution_mode='live' AND ts>=?
           ORDER BY ts""",
        (SINCE,),
    ).fetchall()
    con.close()
    return rows


async def main():
    sec = load_secrets()
    if not (sec.bitunix_futures_api_key and sec.bitunix_futures_api_secret):
        print("ABORT: no Bitunix creds loaded. Broker would be STUB - refusing.")
        return
    broker = BitunixBroker(api_key=sec.bitunix_futures_api_key,
                           api_secret=sec.bitunix_futures_api_secret)
    for name in FORBIDDEN:
        if hasattr(broker, name):
            def _blocked(*a, _n=name, **k):
                raise RuntimeError(f"read-only audit BLOCKED write method: {_n}")
            setattr(broker, name, _blocked)

    await broker.connect()
    try:
        pend = await broker.get_pending_positions()
        print(f"=== OPEN POSITIONS NOW: {len(pend)} (expect 0) ===")
        for p in pend:
            print("   ", p)

        print("\n=== SYMBOL FILL HISTORY (one GET, read-only) ===")
        allf = []
        try:
            allf = await broker.get_history_trades(symbol=SYMBOL)
        except Exception as e:
            print("get_history_trades(symbol) ERR:", e)
        print(f"fills returned: {len(allf)}")
        if allf:
            print("raw sample [0]:", json.dumps(allf[0])[:500])
            cts = [_ct(f) for f in allf if _ct(f)]
            if cts:
                lo, hi = min(cts), max(cts)
                print("coverage:", datetime.utcfromtimestamp(lo / 1000).isoformat(),
                      "->", datetime.utcfromtimestamp(hi / 1000).isoformat(),
                      f"(need back to {SINCE})")
            sells = [f for f in allf if _side(f) == "SELL"]
            buys = [f for f in allf if _side(f) == "BUY"]
            print(f"SELL(entry) fills: n={len(sells)} qty={sum(_q(f) for f in sells):.8f}")
            print(f"BUY (close) fills: n={len(buys)} qty={sum(_q(f) for f in buys):.8f}")
            print(f"realizedPNL sum (all fills): {sum(_real(f) for f in allf):.4f}  "
                  f"fee sum: {sum(_fee(f) for f in allf):.4f}")
            sidecodes = sorted({_side(f) or '(blank)' for f in allf})
            print("distinct side codes seen:", sidecodes)

        # ---- attribute fills to trades by [ts, next_ts) window ----
        trades = load_trades()
        wins = []
        for i, t in enumerate(trades):
            start = _to_ms(t[3])
            end = _to_ms(trades[i + 1][3]) if i + 1 < len(trades) else 10 ** 15
            wins.append((start, end))
        print(f"\n=== PER-TRADE DB vs VENUE ({len(trades)} trades, time-window attribution) ===")
        print("%-9s %5s %12s %12s %8s %11s %10s %9s %6s" %
              ("oid", "side", "DBqty", "vEntryQty", "qtyMatch",
               "vCloseQty", "vRealPnL", "DBpnl", "nFills"))
        for i, (oid, side, dbqty, ts, result, dbpnl, tpids) in enumerate(trades):
            s, e = wins[i]
            win = [f for f in allf if s <= _ct(f) < e]
            en = [f for f in win if _side(f) == "SELL"]
            cl = [f for f in win if _side(f) == "BUY"]
            veqty = sum(_q(f) for f in en)
            vcqty = sum(_q(f) for f in cl)
            vreal = sum(_real(f) for f in cl) + sum(_real(f) for f in en)
            match = "OK" if abs(veqty - _f(dbqty)) <= max(1e-9, 0.02 * _f(dbqty)) else "DIFF"
            print("%-9s %5s %12.8f %12.8f %8s %11.8f %10.4f %9s %6d" %
                  (oid[:8], side, _f(dbqty), veqty, match, vcqty, vreal,
                   ("" if dbpnl is None else "%.3f" % _f(dbpnl)), len(win)))
        print("\n(If coverage does not reach %s, early trades show 0 venue fills = "
              "history window limit, not a real mismatch.)" % SINCE)
    finally:
        await broker.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
