"""S4 market-implied benchmark + dual EV (READ-ONLY from lab_kalshi_candles).

For each holdout 15m window:
  market_p (implied P(up) at window OPEN) = price_mean of the candle nearest
    open_ts if in (0,1), else the yes bid/ask mid; skip if unusable or the
    spread is degenerate (>0.9 -> no two-sided market yet). Coverage reported.
  Brier_market = brier(market_p, y) over covered windows; skill via
    calibration.compare_to_market(model_p, market_p, y).
  Dual EV on windows whose edge |model_p - market_p| clears fees + half-spread:
    taker at the yes/no ASK; maker resting at the bid, filled per ev.maker_filled
    using post-open candles (approx: yes_low<-yes_bid_low, no_low<-1-yes_ask_high,
    since only bid/ask OHLC + price_mean are stored, not traded-price OHLC).
    Maker EV ALWAYS reported WITH its fill_rate (ev.aggregate_maker enforces).

★★ EV INTERPRETATION CAVEAT (do NOT read the dual-EV as a real tradeable edge):
   The first-pass run shows model-implied AND realized taker/maker P&L that are
   POSITIVE (~+$0.03-0.09/contract) while Brier_model ~= Brier_market (skill in
   +/-0.02 noise). Those two facts are INCONSISTENT: a genuine +5-9%/contract
   edge would beat the market's Brier substantially; it does not. The +P&L is
   therefore almost certainly a NON-EXECUTABLE ARTIFACT of the entry proxy:
   the "window-open" candle sits ~1min into the window (thin/stale first-minute
   pricing), and maker fill_rate=1.0 is definitionally bogus (bid_low drift is
   not a real trade-through; traded-price OHLC was not stored). Treat the EV as
   UNVERIFIED / to-be-ruled-out (operator rules things out). The trustworthy S4
   result is the Brier comparison (model ~= market, no robust skill). FORENSIC
   FOLLOW-UP: store traded-price OHLC, use an executable entry timestamp, and a
   fill model grounded in real prints before any EV claim.

NO order/placement code. NO DB writes.
"""
from __future__ import annotations

import os
import sqlite3
import sys

_LAB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lab")
if _LAB not in sys.path:
    sys.path.insert(0, _LAB)
from calibration import brier, compare_to_market  # noqa: E402
from ev import taker_ev, maker_ev, aggregate_taker, aggregate_maker  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))))  # repo root
from trading_corp.agents.strategies._sports_math import kalshi_fee  # noqa: E402

MAX_SPREAD = 0.90   # skip degenerate no-two-sided-market opens
TICK = 0.01


def _load_candles(tickers: list[str], db_path: str) -> dict:
    """market_ticker -> list of candle dicts (ascending end_period_ts)."""
    out: dict[str, list] = {}
    conn = sqlite3.connect("file:" + db_path + "?mode=ro", uri=True)
    try:
        q = ("SELECT market_ticker,end_period_ts,yes_bid_close,yes_ask_close,"
             "yes_bid_low,yes_ask_high,price_mean,volume FROM lab_kalshi_candles "
             "WHERE market_ticker IN (%s) ORDER BY market_ticker,end_period_ts")
        for i in range(0, len(tickers), 800):
            chunk = tickers[i:i + 800]
            ph = ",".join("?" * len(chunk))
            for r in conn.execute(q % ph, chunk):
                out.setdefault(r[0], []).append({
                    "ts": r[1], "yes_bid": r[2], "yes_ask": r[3],
                    "yes_bid_low": r[4], "yes_ask_high": r[5],
                    "price_mean": r[6], "volume": r[7] or 0.0})
    finally:
        conn.close()
    return out


def _market_p(open_c: dict) -> float | None:
    pm = open_c.get("price_mean")
    if pm is not None and 0.0 < pm < 1.0:
        return pm
    yb, ya = open_c.get("yes_bid"), open_c.get("yes_ask")
    if yb is not None and ya is not None and ya > yb and (ya - yb) <= MAX_SPREAD:
        return (yb + ya) / 2.0
    return None


def benchmark_asset(df_holdout, model_p, db_path: str) -> dict:
    """df_holdout rows carry market_ticker/open_ts/close_ts/y. model_p aligned by row."""
    tickers = df_holdout["market_ticker"].tolist()
    cand = _load_candles(tickers, db_path)
    mp_list, mo_list, y_list = [], [], []   # market_p, model_p, y over covered
    taker_res, maker_res = [], []           # harness (model-implied) EV
    realized_taker, realized_maker = [], []  # REALIZED P&L from actual outcomes ($/contract)
    n_edge = 0
    for i, row in df_holdout.reset_index(drop=True).iterrows():
        tkr = row["market_ticker"]
        open_ts, close_ts = int(row["open_ts"]), int(row["close_ts"])
        p = float(model_p[i])
        cs = cand.get(tkr) or []
        if not cs:
            continue
        open_c = min(cs, key=lambda c: abs(c["ts"] - open_ts))
        mp = _market_p(open_c)
        if mp is None:
            continue
        y = int(row["y"])
        mp_list.append(mp); mo_list.append(p); y_list.append(y)
        # edge gate: |model - market| must clear half-spread + fee
        yb, ya = open_c.get("yes_bid"), open_c.get("yes_ask")
        half_spread = ((ya - yb) / 2.0) if (yb is not None and ya is not None and ya > yb) else 0.0
        fee = kalshi_fee(1.0, mp)
        if abs(p - mp) <= half_spread + fee:
            continue
        n_edge += 1
        side = "yes" if p >= 0.5 else "no"
        won = (y == 1) if side == "yes" else (y == 0)     # realized win of the bet
        no_ask = (1.0 - yb) if yb is not None else None
        taker_res.append(taker_ev(p, side, ya, no_ask, qty=1.0))
        ask_paid = ya if side == "yes" else no_ask
        if ask_paid is not None and 0.0 < ask_paid <= 1.0:
            realized_taker.append((1.0 if won else 0.0) - ask_paid - kalshi_fee(1.0, ask_paid))
        bid = yb if side == "yes" else (1.0 - ya if ya is not None else None)
        post = [{"ts": c["ts"],
                 "yes_low": c.get("yes_bid_low"),
                 "no_low": (1.0 - c["yes_ask_high"]) if c.get("yes_ask_high") is not None else None,
                 "volume": c.get("volume", 0.0)}
                for c in cs if open_ts < c["ts"] < close_ts]
        mev = maker_ev(p, side, bid, post, close_ts, tick=TICK, qty=1.0)
        maker_res.append(mev)
        if mev.get("filled") and bid is not None and 0.0 < bid <= 1.0:
            realized_maker.append((1.0 if won else 0.0) - bid - kalshi_fee(1.0, bid))
    cmp = compare_to_market(mo_list, mp_list, y_list) if y_list else {}
    return {
        "n_windows": len(df_holdout),
        "n_covered": len(y_list),
        "coverage": (len(y_list) / len(df_holdout)) if len(df_holdout) else 0.0,
        "brier_market": cmp.get("brier_market"),
        "brier_model_on_covered": cmp.get("brier_model"),
        "skill_score_vs_market": cmp.get("skill_score_vs_market"),
        "n_edge": n_edge,
        "taker_model_implied": aggregate_taker(taker_res),
        "maker_model_implied": aggregate_maker(maker_res),
        # REALIZED P&L from actual outcomes ($/contract) - the honest acid test:
        "realized_taker_mean": (sum(realized_taker) / len(realized_taker)) if realized_taker else None,
        "realized_taker_n": len(realized_taker),
        "realized_maker_mean_on_fills": (sum(realized_maker) / len(realized_maker)) if realized_maker else None,
        "realized_maker_n_fills": len(realized_maker),
    }
