"""EXECUTABLE TREATMENT of the mid-window CONTINUATION gap (2026-08-02, on-disk).
READ-ONLY. NO order/placement surface.

The calibration study found a strong, clustering-robust UNDER-reaction: after a
directional move in minutes 1-3, the contract-implied prob is LESS extreme than
the realized outcome (it underprices the continuation). This applies the EV
forensic discipline to ask whether that gap is HARVESTABLE at real fills.

STRATEGY (deterministic, no model): at the FIRST minute m in {1,2,3} where the
underlying (Binance) has moved |move| >= threshold from the window open, BET THE
CONTINUATION -- buy YES if the move is up, buy NO if down -- and hold to
settlement (S1 RTI 60s-avg). One trade per window at most.

EXECUTION (identical conventions to ev_forensic.py):
  Taker (guaranteed fill): buy YES at the entry-minute price_high / buy NO at
    1 - price_low (a real print you could cross to). taker@quote (yes_ask_close /
    1 - yes_bid_close) shown alongside as the stale-quote contrast.
  Maker (approved trade-through + >=1 tick, traded-close rest): rest on the
    continuation side at the entry-minute traded close; fill iff a later real
    trade prints through by >=1 tick. fill_rate beside every maker figure; the
    three adverse-selection views (timing, filled/unfilled win%, per-ATTEMPT EV).
  Fees: kalshi_fee on every fill. Realized P&L/ct = (win?1:0) - fill_price - fee.

Threshold sweep (primary 0.10% + a sensitivity band). CHRONOLOGICAL holdout
split (last 20% by open_ts) reported alongside train (temporal-stability check;
the rule is deterministic so 'train' is just the earlier 80%). Each window
contributes ONE trade, so observations are independent -> SEs are clean (no
clustering needed, unlike the per-minute calibration).

★ T5 BASIS CAVEAT (stated on every table): the qualifying MOVE is measured on
Binance; SETTLEMENT is CF-Benchmarks RTI. A proxy mismatch sits under every row.
Evidence only -- no verdict.
"""
from __future__ import annotations

import math
import os
import sqlite3
import sys

_S4 = os.path.dirname(os.path.abspath(__file__))
if _S4 not in sys.path:
    sys.path.insert(0, _S4)

from ev_forensic import (  # noqa: E402  reuse the exact forensic conventions
    TICK, LAB_DB, taker_price, maker_fill, realized, _agg, _quantiles,
    _new_macc, _finalize_maker, _valid_price,
)

ASSETS = ["BTC", "ETH", "SOL", "XRP"]
THRESHOLDS = [0.0005, 0.0010, 0.0015, 0.0020]   # 0.05% .. 0.20%; 0.10% = primary
PRIMARY = 0.0010
HOLDOUT_FRAC = 0.20


def _ro(db: str = LAB_DB):
    c = sqlite3.connect("file:" + db + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _binance_close(conn, asset: str) -> dict:
    out = {}
    for r in conn.execute("SELECT ts_ms, close FROM lab_bars_binance WHERE asset=?", (asset,)):
        out[r["ts_ms"] // 1000] = r["close"]
    return out


def load_windows(asset: str, conn) -> list[dict]:
    """Settled 15m windows with their minute candles (price OHLC + bid/ask close)."""
    mkts = conn.execute(
        "SELECT market_ticker, open_ts, close_ts, floor_strike, result "
        "FROM lab_kalshi_markets WHERE kind='15m' AND asset=? AND result IN ('yes','no') "
        "ORDER BY open_ts", (asset,)).fetchall()
    bcl = _binance_close(conn, asset)
    out = []
    for mk in mkts:
        ot, ct = mk["open_ts"], mk["close_ts"]
        if ot is None or ct is None:
            continue
        u_open = bcl.get(ot)
        if not u_open:
            continue
        cands = []
        for c in conn.execute(
                "SELECT end_period_ts,yes_bid_close,yes_ask_close,price_open,price_high,"
                "price_low,price_close,price_mean,volume FROM lab_kalshi_candles "
                "WHERE market_ticker=? ORDER BY end_period_ts", (mk["market_ticker"],)):
            cands.append({"ts": c["end_period_ts"], "yes_bid_close": c["yes_bid_close"],
                          "yes_ask_close": c["yes_ask_close"], "price_open": c["price_open"],
                          "price_high": c["price_high"], "price_low": c["price_low"],
                          "price_close": c["price_close"], "price_mean": c["price_mean"],
                          "volume": c["volume"] or 0.0})
        out.append({"ticker": mk["market_ticker"], "open_ts": ot, "close_ts": ct,
                    "y": 1 if mk["result"] == "yes" else 0, "u_open": u_open,
                    "cands": cands, "bcl": bcl})
    return out


def _cand_at(cands: list[dict], ot: int, m: int):
    for c in cands:
        if round((c["ts"] - ot) / 60.0) == m and (c.get("volume") or 0) > 0 \
                and c.get("price_high") is not None and c.get("price_low") is not None:
            return c
    return None


def eval_threshold(windows: list[dict], thr: float) -> dict:
    """Continuation strategy at one threshold: taker + maker legs over the windows."""
    taker_traded, taker_quote = [], []
    mk = _new_macc()
    n_trades = win_taker = 0
    n_up = n_down = 0
    for w in windows:
        ot, ct, cands, bcl, u_open = w["open_ts"], w["close_ts"], w["cands"], w["bcl"], w["u_open"]
        # first qualifying minute in 1..3
        side = ec = None
        for m in (1, 2, 3):
            u_m = bcl.get(ot + m * 60)
            if u_m is None:
                continue
            move = (u_m - u_open) / u_open
            if abs(move) >= thr:
                c = _cand_at(cands, ot, m)
                if c is not None:
                    side = "yes" if move > 0 else "no"
                    ec = c
                    break
        if ec is None:
            continue
        n_trades += 1
        n_up += side == "yes"
        n_down += side == "no"
        y = w["y"]
        won = (y == 1) if side == "yes" else (y == 0)
        if won:
            win_taker += 1
        # taker @ traded (primary) + @ quote (contrast)
        tp = taker_price(side, ec, "traded")
        if _valid_price(tp):
            taker_traded.append(realized(tp, won))
        tq = taker_price(side, ec, "quote")
        if _valid_price(tq):
            taker_quote.append(realized(tq, won))
        # maker (traded-close rest, real trade-through) + adverse-selection views
        later = [c for c in cands if ec["ts"] < c["ts"] <= ct]
        filled, fprice, fts = maker_fill(side, ec, later, rest_kind="traded")
        if _valid_price(fprice):
            mk["n_attempt"] += 1
            if filled:
                mk["n_fill"] += 1
                pnl = realized(fprice, won)
                mk["real"].append(pnl)
                mk["per_attempt"].append(pnl)
                mk["fill_won"].append(1 if won else 0)
                mk["fill_min"].append((fts - ot) / 60.0 if fts is not None else float("nan"))
            else:
                mk["per_attempt"].append(0.0)
                mk["unfill_won"].append(1 if won else 0)
    return {
        "thr": thr, "n_trades": n_trades, "n_up": n_up, "n_down": n_down,
        "taker_win_rate": (win_taker / n_trades) if n_trades else None,
        "taker_traded": _agg(taker_traded),
        "taker_quote": _agg(taker_quote),
        "maker": _finalize_maker(mk),
    }


def run_asset(asset: str, conn) -> dict:
    print(f"\n== {asset} ==", flush=True)
    windows = load_windows(asset, conn)
    windows.sort(key=lambda w: w["open_ts"])
    n = len(windows)
    cut = int(n * (1 - HOLDOUT_FRAC))
    splits = {"train": windows[:cut], "holdout": windows[cut:], "all": windows}
    out = {"asset": asset, "n_windows": n, "by_split": {}}
    for sp, ws in splits.items():
        out["by_split"][sp] = {thr: eval_threshold(ws, thr) for thr in THRESHOLDS}
    # console: primary threshold, holdout
    h = out["by_split"]["holdout"][PRIMARY]
    mkp = h["maker"]
    print(f"  holdout thr=0.10%: n_trades={h['n_trades']} "
          f"taker@traded={_f(h['taker_traded'])} taker@quote={_f(h['taker_quote'])} | "
          f"maker per-attempt={_f(mkp['per_attempt'])} fill={_pct(mkp['fill_rate'])}", flush=True)
    return out


def _f(a):
    if not a or a.get("mean") is None:
        return "n/a"
    se = a.get("se")
    return f"${a['mean']:+.4f}+/-{se:.4f}" if se else f"${a['mean']:+.4f}"


def _pct(x): return "n/a" if x is None else f"{x*100:.1f}%"


def _cell(a):
    if not a or a.get("mean") is None:
        return "n/a | n/a | n/a"
    se = a.get("se")
    t = (a["mean"] / se) if se else float("nan")
    return f"{a['mean']:+.4f} | {('+/-%.4f' % se) if se else 'n/a'} | {('%.1f' % t) if se else 'n/a'}"


def _mini(a):
    if not a or a.get("mean") is None:
        return "n/a"
    se = a.get("se")
    return f"${a['mean']:+.4f} (t={a['mean']/se:+.1f})" if se else f"${a['mean']:+.4f}"


def write_report(results: list[dict], path: str) -> None:
    L = []
    L.append("# S4 Executable Continuation — is the mid-window under-reaction harvestable?")
    L.append("")
    L.append("**Date:** 2026-08-02  ")
    L.append("**Strategy:** at the first minute m in {1,2,3} where the underlying "
             "(Binance) moved |move|>=threshold from open, BUY the CONTINUATION side "
             "(YES if up / NO if down); hold to S1 settlement. One trade per window.  ")
    L.append("**Standing:** read-only; on-disk (no pulls); lab DB only; evidence only "
             "— no verdict. Each window = one trade ⇒ independent obs, clean SEs.")
    L.append("")
    L.append("Taker@traded = buy the side at a real print you could cross to "
             "(`price_high` YES / `1-price_low` NO); taker@quote = the entry ask "
             "(stale-quote contrast). Maker = rest at the entry-minute TRADED CLOSE, "
             "fill on a real >=1-tick trade-through; per-ATTEMPT books no-fills at $0; "
             "fill_rate beside every maker figure. `kalshi_fee` on every fill. "
             "Chronological holdout = last 20% by open_ts (the rule is deterministic; "
             "the split is a temporal-stability check).")
    L.append("")
    L.append("> ★ **T5 BASIS CAVEAT (every table):** the qualifying MOVE is Binance; "
             "SETTLEMENT is CF-Benchmarks RTI. The Binance→RTI proxy mismatch is "
             "unquantified here and sits under every number.")
    L.append("")
    for res in results:
        a = res["asset"]
        L.append(f"## {a}")
        L.append("")
        L.append(f"Settled windows: {res['n_windows']}.")
        L.append("")
        for sp in ("holdout", "train"):
            L.append(f"### {sp.upper()} — threshold sweep")
            L.append("")
            L.append("| Threshold | n trades (up/down) | taker win% | taker@traded (t) "
                     "| taker@quote (t) | maker per-ATTEMPT (t) | maker fill_rate | "
                     "filled/unfilled win% |")
            L.append("|---|---|---|---|---|---|---|---|")
            for thr in THRESHOLDS:
                r = res["by_split"][sp][thr]
                mk = r["maker"]
                star = " ★" if abs(thr - PRIMARY) < 1e-9 else ""
                L.append(
                    f"| {thr*100:.2f}%{star} | {r['n_trades']} "
                    f"({r['n_up']}/{r['n_down']}) | {_pct(r['taker_win_rate'])} | "
                    f"{_mini(r['taker_traded'])} | {_mini(r['taker_quote'])} | "
                    f"{_mini(mk['per_attempt'])} | {_pct(mk['fill_rate'])} | "
                    f"{_pct(mk['filled_win_rate'])}/{_pct(mk['unfilled_win_rate'])} |")
            L.append("")
        # adverse-selection views at the primary threshold (holdout)
        mk = res["by_split"]["holdout"][PRIMARY]["maker"]
        tm = mk["fill_timing"]
        L.append(f"**Maker adverse-selection views — HOLDOUT, threshold 0.10%:**")
        L.append("")
        if tm:
            L.append(f"- *Fill timing:* median {tm['median']:.1f}m (p25 {tm['p25']:.1f} / "
                     f"p75 {tm['p75']:.1f}); early(<=7.5m, n={mk['pnl_early']['n']}) "
                     f"{_mini(mk['pnl_early'])}, late(>7.5m, n={mk['pnl_late']['n']}) "
                     f"{_mini(mk['pnl_late'])}.")
        L.append(f"- *Filled vs unfilled win-rate:* filled {_pct(mk['filled_win_rate'])} "
                 f"(n={mk['n_fill']}), unfilled {_pct(mk['unfilled_win_rate'])} "
                 f"(n={mk['n_unfill']}).")
        L.append(f"- *Per-ATTEMPT vs per-fill:* {_mini(mk['per_attempt'])} "
                 f"(n={mk['per_attempt']['n']}) vs per-fill {_mini(mk['per_fill'])} "
                 f"(n={mk['n_fill']}).")
        L.append("")
    L.append("## Reading this (evidence, not verdict)")
    L.append("")
    L.append("- **Taker@traded <=0 or ~0 ⇒ the continuation gap is NOT harvestable by "
             "crossing the spread** (the calibration gap lived in the traded MEAN; the "
             "executable ask already reflects it). taker@quote >> taker@traded again "
             "sizes the stale-quote effect.")
    L.append("- **Maker per-ATTEMPT** is the honest number (no-fills at $0); judge it "
             "with fill_rate + the filled/unfilled split. A positive per-fill with a "
             "~0 per-attempt is not tradeable.")
    L.append("- **Holdout vs train** stability + the ★0.10% row are the headline; the "
             "sweep shows threshold sensitivity. **T5 basis (Binance move vs RTI "
             "settle) is unquantified and could move all of this.**")
    L.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"\nReport written: {path}", flush=True)


def main() -> int:
    print("=" * 70)
    print("  EXECUTABLE CONTINUATION STUDY — kalshi_crypto_v2 (on-disk, no pulls)")
    print("=" * 70)
    args = sys.argv[1:]
    assets = ASSETS
    if "--assets" in args:
        assets = [a.strip().upper() for a in args[args.index("--assets") + 1].split(",")]
    conn = _ro()
    try:
        results = [run_asset(a, conn) for a in assets]
    finally:
        conn.close()
    reports_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_S4))), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    path = os.path.join(reports_dir, "2026-08-02_kalshi_crypto_v2_continuation_exec.md")
    write_report(results, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
