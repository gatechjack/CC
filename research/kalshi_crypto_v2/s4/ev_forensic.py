"""EV FORENSIC (2026-08-02) — does the flagged positive dual-EV SURVIVE an
executable entry + real traded-price fills?  READ-ONLY. NO order/placement code.

WHY:
  S4 v1's benchmark reported +$0.03-0.09/contract taker/maker P&L at fill_rate
  =1.0 while Brier_model ~= Brier_market (no skill). That EV was flagged a
  NON-EXECUTABLE ARTIFACT. The probe (2026-08-02) shows the mechanism directly:
  the first in-window 1m candle's OPEN quote band is degenerate -- yes_ask opens
  ~0.999 and yes_bid opens ~0.000 (NO two-sided market at the open tick) -- while
  REAL trades printed inside a normal range. The old EV leg (a) used that
  ~1-min-in candle, (b) "bought at the ask" of a stale/thin quote, and (c)
  counted a maker fill whenever the bid QUOTE wiggled down a tick (not a real
  trade) -> fill_rate 1.0. This module rebuilds the EV leg on TRADED-price OHLC
  (price.{open,high,low,close}), an executable entry, and a real trade-through
  fill model. Evidence only; the operator rules.

ENTRY RULE (stated + justified):
  The DECISION is fixed as-of T0-60s (the exact model already Brier-tested;
  unchanged). You cannot transact at the T0 open tick -- the quotes are
  degenerate there -- so the FILL is modeled at the first in-window 1m candle
  that actually TRADED (volume>0 and a real price_high>=price_low range).
    Variant A = enter on that first tradeable minute.
    Variant B = SKIP the first tradeable minute, enter on the SECOND
                (stricter: you cannot assume a fair fill in the chaotic first
                minute of a 15m market). Both variants are reported side by side.

TAKER (guaranteed fill, conservative, real prints only):
  buy YES -> pay price_high of the entry minute (a real print you could have
             crossed up to); buy NO -> pay 1 - price_low. Fee on every fill.
  For contrast we ALSO report taker@quote (yes_ask_close / 1-yes_bid_close) at
  the SAME executable entry candle -- the gap taker@quote vs taker@traded is the
  stale-quote artifact magnitude.

MAKER (operator-approved trade-through + >=1 tick model, REAL trades):
  rest on the model's side at the entry-minute TRADED CLOSE (price_close, a real
  print -- operator ruling 2026-08-02; NOT the stale first-minute bid quote).
  FILLED iff a LATER in-window minute trades THROUGH by >= 1 tick on TRADED
  price: YES fill iff some later price_low <= rest - tick (vol>0); NO fill iff
  some later price_high >= rest + tick (vol>0). Fill price = the resting level on
  the bet's own side. fill_rate = fills/attempts is reported BESIDE every maker
  figure (a maker number without its fill rate is not a reportable result). The
  bid/ask-QUOTE resting level is computed too but only as a non-executable
  resting-level sensitivity contrast (same stale-quote family as taker@quote).
  Three adverse-selection views make this the make-or-break maker scrutiny:
    1. fill-timing distribution + early/late conditional P&L;
    2. filled-vs-unfilled would-have-won rates (a resting bid that NEVER fills
       means price ran away in your favor -> unfilled skew to winners);
    3. per-ATTEMPT EV (fills@realized, no-fills@$0) = what a strategy earns.

FEES: kalshi_fee(contracts, price) = ceil(0.07*c*p*(1-p)) per fill, applied to
  BOTH taker and maker entries (Kalshi charges the same schedule; conservative).
  No fee on settlement. Realized P&L/contract = (1 if win else 0) - price - fee.

The Brier evidence (model ~= market, skill +/-0.02 noise) is SETTLED and does
NOT depend on this forensic.
"""
from __future__ import annotations

import math
import os
import sqlite3
import sys

import numpy as np

_S4 = os.path.dirname(os.path.abspath(__file__))
_KCV2 = os.path.dirname(_S4)
_LAB = os.path.join(_KCV2, "lab")
for _p in (_LAB, _S4):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Reuse the EXACT v1 train/split/calibrate pipeline so model_p matches the
# Brier-tested holdout (no re-implementation of the model).
from dataset import build_dataset, LAB_DB  # noqa: E402
from split import chronological_split  # noqa: E402
import run_s4  # noqa: E402  (helpers: _train_catboost, calibrate_platt, _prep_X)

sys.path.insert(0, os.path.dirname(os.path.dirname(_KCV2)))  # repo root (== worktree)
from trading_corp.agents.strategies._sports_math import (  # noqa: E402
    LegFill, compute_ev_at_fill_b_directional, kalshi_fee,
)

ASSETS = ["BTC", "ETH", "SOL", "XRP"]
TICK = 0.01


# ---------------------------------------------------------------------------
# Reproduce the v1 holdout predictions (same seed/split/calibration as run_s4)
# ---------------------------------------------------------------------------

def reproduce_holdout(asset: str) -> tuple:
    """Return (df_holdout, cal_probs) reproducing run_s4's v1 holdout exactly."""
    data = build_dataset(asset, db_path=LAB_DB, include_rider_b=False)
    df = data["df_v1"]
    feats = data["feature_cols_v1"]
    ts_sorted = df["open_ts_ms"].tolist()
    sp = chronological_split(ts_sorted, holdout_frac=0.2)
    df_train = df.iloc[sp["train"]].reset_index(drop=True)
    df_holdout = df.iloc[sp["holdout"]].reset_index(drop=True)
    n_cal_start = int(len(df_train) * 0.8)
    df_core = df_train.iloc[:n_cal_start].reset_index(drop=True)
    df_cal = df_train.iloc[n_cal_start:].reset_index(drop=True)
    X_core = run_s4._prep_X(df_core, feats)
    y_core = df_core["y"].values.astype(int)
    X_cal = run_s4._prep_X(df_cal, feats)
    y_cal = df_cal["y"].values.astype(int)
    X_hold = run_s4._prep_X(df_holdout, feats)
    model, _ = run_s4._train_catboost(X_core, y_core, X_val=X_cal, y_val=y_cal)
    raw_cal = model.predict_proba(X_cal)[:, 1]
    raw_hold = model.predict_proba(X_hold)[:, 1]
    cal_probs = run_s4.calibrate_platt(raw_cal, y_cal, raw_hold)
    return df_holdout, cal_probs


# ---------------------------------------------------------------------------
# Candle loading (WITH traded-price OHLC)
# ---------------------------------------------------------------------------

def load_candles_ohlc(tickers: list[str], db_path: str) -> dict:
    out: dict[str, list] = {}
    conn = sqlite3.connect("file:" + db_path + "?mode=ro", uri=True)
    try:
        q = ("SELECT market_ticker,end_period_ts,yes_bid_close,yes_ask_close,"
             "price_open,price_high,price_low,price_close,price_mean,volume "
             "FROM lab_kalshi_candles WHERE market_ticker IN (%s) "
             "ORDER BY market_ticker,end_period_ts")
        for i in range(0, len(tickers), 800):
            chunk = tickers[i:i + 800]
            ph = ",".join("?" * len(chunk))
            for r in conn.execute(q % ph, chunk):
                out.setdefault(r[0], []).append({
                    "ts": r[1], "yes_bid_close": r[2], "yes_ask_close": r[3],
                    "price_open": r[4], "price_high": r[5], "price_low": r[6],
                    "price_close": r[7], "price_mean": r[8], "volume": r[9] or 0.0})
    finally:
        conn.close()
    return out


# ---------------------------------------------------------------------------
# Per-window primitives
# ---------------------------------------------------------------------------

def _traded(c: dict) -> bool:
    return ((c.get("volume") or 0) > 0 and c.get("price_high") is not None
            and c.get("price_low") is not None and c["price_high"] >= c["price_low"])


def entry_candle(cands: list[dict], open_ts: int, close_ts: int, variant: str):
    """First (A) / second (B) in-window minute that actually traded."""
    inwin = sorted((c for c in cands if open_ts < c["ts"] < close_ts and _traded(c)),
                   key=lambda c: c["ts"])
    if variant == "A":
        return inwin[0] if inwin else None
    return inwin[1] if len(inwin) > 1 else None


def _valid_price(p) -> bool:
    return p is not None and 0.0 < p <= 1.0


def model_ev(model_p: float, side: str, price: float) -> float | None:
    """Model-implied EV/contract at a given fill price (fee included)."""
    if not _valid_price(price):
        return None
    leg = LegFill(venue="kalshi", side=side, qty=1.0, price_per_unit=price,
                  fee=kalshi_fee(1.0, price))
    p_win = model_p if side == "yes" else 1.0 - model_p
    return compute_ev_at_fill_b_directional(leg, p_win).ev_dollars


def realized(price: float, won: bool) -> float:
    """Realized P&L/contract at a fill price, fee included."""
    return (1.0 if won else 0.0) - price - kalshi_fee(1.0, price)


def taker_price(side: str, entry_c: dict, kind: str) -> float | None:
    """kind='traded' -> worst real print; kind='quote' -> the entry-minute ask."""
    if kind == "traded":
        if side == "yes":
            return entry_c.get("price_high")
        pl = entry_c.get("price_low")
        return (1.0 - pl) if pl is not None else None
    # quote (reproduces the original artifact at the executable entry candle)
    if side == "yes":
        return entry_c.get("yes_ask_close")
    yb = entry_c.get("yes_bid_close")
    return (1.0 - yb) if yb is not None else None


def _rest_level(side: str, entry_c: dict, rest_kind: str) -> float | None:
    """YES-side resting price level for a maker order.
      'traded' (PRIMARY, approved spec): the entry-minute TRADED close
               (price_close) — a real print, both sides rest on it.
      'quote'  (contrast only): the entry-minute bid/ask QUOTE — same
               stale-first-minute-quote family that inflates taker@quote;
               reported as a resting-level sensitivity, never primary."""
    if rest_kind == "traded":
        return entry_c.get("price_close")
    return entry_c.get("yes_bid_close") if side == "yes" else entry_c.get("yes_ask_close")


def maker_fill(side: str, entry_c: dict, later: list[dict],
               tick: float = TICK, rest_kind: str = "traded"):
    """Returns (filled, fill_price, fill_ts). Rest on the model's side at the
    YES-side resting level (see _rest_level); FILLED iff a later REAL trade
    prints THROUGH by >= 1 tick. fill_price is on the bet's own side; fill_ts =
    end_period_ts of the trade-through candle (for the fill-timing analysis)."""
    lvl = _rest_level(side, entry_c, rest_kind)
    if side == "yes":                       # rest a YES bid at lvl; fill on down-through
        if not _valid_price(lvl):
            return False, None, None
        thr = lvl - tick
        for c in later:
            if (c.get("volume") or 0) > 0 and c.get("price_low") is not None and c["price_low"] <= thr:
                return True, lvl, c["ts"]
        return False, lvl, None
    # no side: rest a YES ask at lvl -> buy NO at 1-lvl; fill on up-through
    if lvl is None or not (0.0 <= lvl < 1.0):
        return False, None, None
    n = 1.0 - lvl
    thr = lvl + tick
    for c in later:
        if (c.get("volume") or 0) > 0 and c.get("price_high") is not None and c["price_high"] >= thr:
            return True, n, c["ts"]
    return False, n, None


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _agg(xs: list[float]) -> dict:
    n = len(xs)
    if n == 0:
        return {"n": 0, "mean": None, "se": None}
    m = sum(xs) / n
    if n > 1:
        var = sum((x - m) ** 2 for x in xs) / (n - 1)
        se = math.sqrt(var / n)
    else:
        se = None
    return {"n": n, "mean": m, "se": se}


def _quantiles(xs: list[float]) -> dict | None:
    if not xs:
        return None
    s = sorted(xs)

    def q(p: float) -> float:
        if len(s) == 1:
            return s[0]
        i = p * (len(s) - 1)
        lo = int(i)
        if lo + 1 >= len(s):
            return s[-1]
        return s[lo] * (1 - (i - lo)) + s[lo + 1] * (i - lo)

    return {"n": len(s), "mean": sum(s) / len(s), "p25": q(0.25),
            "median": q(0.5), "p75": q(0.75), "min": s[0], "max": s[-1]}


def _new_macc() -> dict:
    return {"real": [], "ev": [], "per_attempt": [], "fill_min": [],
            "fill_won": [], "unfill_won": [], "n_attempt": 0, "n_fill": 0}


def _acc_maker(m: dict, side: str, ec: dict, later: list[dict], won: bool,
               p: float, ot: int, rest_kind: str) -> None:
    filled, fprice, fts = maker_fill(side, ec, later, rest_kind=rest_kind)
    if not _valid_price(fprice):            # no valid resting level == not a real attempt
        return
    m["n_attempt"] += 1
    if filled:
        m["n_fill"] += 1
        pnl = realized(fprice, won)
        m["real"].append(pnl)
        m["per_attempt"].append(pnl)
        m["fill_won"].append(1 if won else 0)
        m["fill_min"].append((fts - ot) / 60.0 if fts is not None else float("nan"))
        ev = model_ev(p, side, fprice)
        if ev is not None:
            m["ev"].append(ev)
    else:
        m["per_attempt"].append(0.0)        # no-fill booked at $0 (per-attempt EV)
        m["unfill_won"].append(1 if won else 0)


def _finalize_maker(m: dict) -> dict:
    HALF = 7.5
    early = [pnl for pnl, mm in zip(m["real"], m["fill_min"])
             if not math.isnan(mm) and mm <= HALF]
    late = [pnl for pnl, mm in zip(m["real"], m["fill_min"])
            if not math.isnan(mm) and mm > HALF]
    fwr = (sum(m["fill_won"]) / len(m["fill_won"])) if m["fill_won"] else None
    uwr = (sum(m["unfill_won"]) / len(m["unfill_won"])) if m["unfill_won"] else None
    return {
        "n_attempt": m["n_attempt"], "n_fill": m["n_fill"],
        "fill_rate": (m["n_fill"] / m["n_attempt"]) if m["n_attempt"] else None,
        "per_fill": _agg(m["real"]), "per_attempt": _agg(m["per_attempt"]),
        "model_ev_on_fills": _agg(m["ev"]),
        "fill_timing": _quantiles([x for x in m["fill_min"] if not math.isnan(x)]),
        "pnl_early": _agg(early), "pnl_late": _agg(late),
        "filled_win_rate": fwr, "unfilled_win_rate": uwr,
        "n_unfill": len(m["unfill_won"]),
    }


def run_variant(df_holdout, model_p, cand: dict, variant: str) -> dict:
    """One entry variant across the holdout. Bet the model's side per window."""
    taker_traded, taker_quote = [], []       # realized P&L, ALL covered windows
    taker_traded_ev = []                     # model-implied EV @ traded price
    taker_gated = []                         # realized, model-implied +EV subset
    mk_traded = _new_macc()                  # PRIMARY maker: rest at traded close
    mk_quote = _new_macc()                   # contrast maker: rest at bid/ask quote
    edges, mps, mkts = [], [], []            # |model-market|, model_p, market_p
    win_taker = 0
    for i, row in df_holdout.reset_index(drop=True).iterrows():
        cs = cand.get(row["market_ticker"]) or []
        if not cs:
            continue
        ot, ct = int(row["open_ts"]), int(row["close_ts"])
        ec = entry_candle(cs, ot, ct, variant)
        if ec is None:
            continue
        p = float(model_p[i])
        side = "yes" if p >= 0.5 else "no"
        y = int(row["y"])
        won = (y == 1) if side == "yes" else (y == 0)
        mp = ec.get("price_mean")
        if _valid_price(mp):
            mps.append(p); mkts.append(mp); edges.append(abs(p - mp))
        # --- taker @ traded (primary) ---
        tp = taker_price(side, ec, "traded")
        if _valid_price(tp):
            taker_traded.append(realized(tp, won))
            ev = model_ev(p, side, tp)
            if ev is not None:
                taker_traded_ev.append(ev)
                if ev > 0:                       # model-implied +EV gate
                    taker_gated.append(realized(tp, won))
            if won:
                win_taker += 1
        # --- taker @ quote (artifact contrast) ---
        tq = taker_price(side, ec, "quote")
        if _valid_price(tq):
            taker_quote.append(realized(tq, won))
        # --- maker: both resting levels (traded-close PRIMARY, quote CONTRAST) ---
        later = [c for c in cs if ec["ts"] < c["ts"] <= ct]
        _acc_maker(mk_traded, side, ec, later, won, p, ot, "traded")
        _acc_maker(mk_quote, side, ec, later, won, p, ot, "quote")
    return {
        "variant": variant,
        "n_covered": len(taker_traded),
        "win_rate_taker": (win_taker / len(taker_traded)) if taker_traded else None,
        "mean_model_p": (sum(mps) / len(mps)) if mps else None,
        "mean_market_p": (sum(mkts) / len(mkts)) if mkts else None,
        "mean_abs_edge": (sum(edges) / len(edges)) if edges else None,
        "taker_traded": _agg(taker_traded),
        "taker_traded_model_ev": _agg(taker_traded_ev),
        "taker_quote": _agg(taker_quote),
        "taker_gated_posEV": _agg(taker_gated),
        "maker": _finalize_maker(mk_traded),        # PRIMARY: traded-close resting level
        "maker_quote": _finalize_maker(mk_quote),   # CONTRAST: bid/ask-quote resting level
    }


def run_asset(asset: str) -> dict:
    print(f"\n{'='*60}\n  EV FORENSIC: {asset}\n{'='*60}", flush=True)
    df_holdout, cal_probs = reproduce_holdout(asset)
    tickers = df_holdout["market_ticker"].tolist()
    cand = load_candles_ohlc(tickers, LAB_DB)
    # coverage: how many holdout markets have re-pulled traded-price OHLC
    has_ohlc = sum(1 for t in tickers
                   if any(c.get("price_high") is not None for c in cand.get(t, [])))
    print(f"  holdout windows={len(df_holdout)}  markets_with_traded_OHLC={has_ohlc}", flush=True)
    out = {"asset": asset, "n_holdout": len(df_holdout), "n_with_ohlc": has_ohlc,
           "variants": {}}
    for v in ("A", "B"):
        r = run_variant(df_holdout, cal_probs, cand, v)
        out["variants"][v] = r
        tt, tq = r["taker_traded"], r["taker_quote"]
        print(f"  [{v}] covered={r['n_covered']} "
              f"taker@traded={_fmt(tt)} taker@quote={_fmt(tq)}", flush=True)
        mk = r["maker"]                        # PRIMARY (traded-close resting level)
        tm = mk["fill_timing"]
        med = f"{tm['median']:.1f}" if tm else "n/a"
        print(f"        maker(traded-close) fill_rate={mk['fill_rate']:.3f} "
              f"per-fill={_fmt(mk['per_fill'])} per-attempt={_fmt(mk['per_attempt'])} | "
              f"win% filled={_pct(mk['filled_win_rate'])} "
              f"unfilled={_pct(mk['unfilled_win_rate'])} | fill-min median={med}", flush=True)
        mq = r["maker_quote"]                  # CONTRAST (bid/ask-quote resting level)
        print(f"        maker(quote,contrast) fill_rate={mq['fill_rate']:.3f} "
              f"per-attempt={_fmt(mq['per_attempt'])}", flush=True)
    return out


def _fmt(a: dict) -> str:
    if not a or a.get("mean") is None:
        return "n/a"
    se = a.get("se")
    return f"${a['mean']:+.4f}+/-{se:.4f}" if se is not None else f"${a['mean']:+.4f}"


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _row(a: dict) -> str:
    if not a or a.get("mean") is None:
        return "n/a | n/a | n/a"
    se = a.get("se")
    t = (a["mean"] / se) if se else float("nan")
    return (f"{a['mean']:+.4f} | "
            f"{('+/-%.4f' % se) if se is not None else 'n/a'} | "
            f"{('%.2f' % t) if se else 'n/a'}")


def write_report(results: list[dict], path: str) -> None:
    L = []
    L.append("# S4 EV Forensic — does the flagged positive dual-EV survive?")
    L.append("")
    L.append("**Date:** 2026-08-02  ")
    L.append("**Scope:** Kalshi 15m up/down binaries, BTC/ETH/SOL/XRP, v1 holdout "
             "(last 20% by open_ts).  ")
    L.append("**Standing:** read-only research; no order/placement surface; lab DB "
             "only; evidence only — no verdict.")
    L.append("")
    L.append("## What changed vs the v1 benchmark")
    L.append("")
    L.append("- **Traded-price OHLC** (`price.{open,high,low,close}`) re-pulled for "
             "the whole 15m corpus (only `price_mean` was stored before).")
    L.append("- **Executable entry** — the first in-window 1m candle that actually "
             "TRADED (vol>0, real range), NOT the ~1-min-in open candle whose quote "
             "band is degenerate (probe: `yes_ask` open ~0.999 / `yes_bid` open "
             "~0.000 — no two-sided market at the open tick). Variant B enters on the "
             "SECOND tradeable minute (stricter).")
    L.append("- **Taker** buys the model's side at a real print you could cross to "
             "(`price_high` for YES / `1-price_low` for NO), fee included. "
             "`taker@quote` (the entry ask) is shown alongside — the gap is the "
             "stale-quote artifact.")
    L.append("- **Maker** rests at the entry-minute **TRADED CLOSE** (`price_close`, "
             "approved spec — a real print, not the stale first-minute bid quote) and "
             "fills ONLY on a real trade-through by >=1 tick (traded "
             "`price_low<=rest-tick` for YES / `price_high>=rest+tick` for NO), so "
             "**fill_rate is realistic (<1)** and is reported beside every maker "
             "figure. (The v1 fill_rate=1.0 came from counting bid-QUOTE wiggles as "
             "fills.) The bid/ask-quote resting level is shown only as a non-executable "
             "resting-level sensitivity contrast.")
    L.append("")
    L.append("Realized P&L/contract = `(1 if win else 0) - fill_price - kalshi_fee`. "
             "`kalshi_fee = ceil(0.07*p*(1-p))` applied to every entry (taker AND "
             "maker; no settlement fee). SE = standard error of the mean; t = "
             "mean/SE (|t|<~2 is indistinguishable from zero).")
    L.append("")
    # --- at-a-glance summary ------------------------------------------------
    L.append("## Summary — taker@traded (executable, guaranteed fill) vs "
             "maker per-ATTEMPT (traded-close rest)")
    L.append("")
    L.append("| Asset | Var | Taker@traded $/ct (t) | Maker per-ATTEMPT $/ct (t) | "
             "maker fill_rate | filled/unfilled win% |")
    L.append("|---|---|---|---|---|---|")
    for res in results:
        for v in ("A", "B"):
            r = res["variants"][v]
            mk = r["maker"]
            L.append(f"| {res['asset']} | {v} | {_cell(r['taker_traded'])} | "
                     f"{_cell(mk['per_attempt'])} | {_pct(mk['fill_rate'])} | "
                     f"{_pct(mk['filled_win_rate'])} / {_pct(mk['unfilled_win_rate'])} |")
    L.append("")
    L.append("_Maker per-ATTEMPT rides an OPTIMISTIC fill assumption (you fill at your "
             "resting price whenever a later trade prints >=1 tick through it — no "
             "queue position, no partial fills). Read it against that and the "
             "adverse-selection views below; a realistic queue model is the obvious "
             "next test._")
    L.append("")
    for res in results:
        a = res["asset"]
        L.append(f"## {a}")
        L.append("")
        L.append(f"Holdout windows: {res['n_holdout']} | markets with re-pulled "
                 f"traded OHLC: {res['n_with_ohlc']}")
        L.append("")
        for v in ("A", "B"):
            r = res["variants"][v]
            vlabel = ("A = first tradeable minute" if v == "A"
                      else "B = second tradeable minute (stricter)")
            L.append(f"### Variant {vlabel}")
            L.append("")
            L.append(f"- Covered windows (valid entry + fill): **{r['n_covered']}**; "
                     f"taker win rate {_pct(r['win_rate_taker'])}; "
                     f"mean model_p {_num(r['mean_model_p'])}, mean market_p "
                     f"(entry price_mean) {_num(r['mean_market_p'])}, mean |edge| "
                     f"{_num(r['mean_abs_edge'])}.")
            L.append("")
            mk = r["maker"]            # PRIMARY maker (traded-close resting level)
            mq = r["maker_quote"]      # CONTRAST maker (bid/ask-quote resting level)
            L.append("| Leg | mean $/contract | SE | t |")
            L.append("|---|---|---|---|")
            L.append(f"| **Taker @ traded** (primary, all covered) | {_row(r['taker_traded'])} |")
            L.append(f"| Taker @ quote (artifact contrast) | {_row(r['taker_quote'])} |")
            L.append(f"| Taker @ traded, model-implied +EV subset (n={r['taker_gated_posEV']['n']}) "
                     f"| {_row(r['taker_gated_posEV'])} |")
            L.append(f"| **Maker per-ATTEMPT** (traded-close rest, fill_rate "
                     f"{_pct(mk['fill_rate'])}, {mk['n_fill']}/{mk['n_attempt']}) "
                     f"| {_row(mk['per_attempt'])} |")
            L.append(f"| Maker per-fill (traded-close rest, fills only) | {_row(mk['per_fill'])} |")
            L.append("")
            L.append(f"_Model-implied EV (not realized): taker@traded "
                     f"{_money(r['taker_traded_model_ev'])}, maker@fills "
                     f"{_money(mk['model_ev_on_fills'])}._")
            L.append("")
            # --- maker adverse-selection views (PRIMARY = traded-close rest) ---
            L.append("**Maker adverse-selection views** (resting level = entry-minute "
                     "TRADED CLOSE, approved spec)")
            L.append("")
            tm = mk["fill_timing"]
            if tm:
                L.append(f"- *View 1 — fill timing:* minutes-into-window at fill "
                         f"(15m window) p25 **{tm['p25']:.1f}** / median "
                         f"**{tm['median']:.1f}** / p75 **{tm['p75']:.1f}** "
                         f"(mean {tm['mean']:.1f}, range {tm['min']:.1f}-{tm['max']:.1f}). "
                         f"P&L by fill half: early (<=7.5m, n={mk['pnl_early']['n']}) "
                         f"{_rowmini(mk['pnl_early'])}; late (>7.5m, "
                         f"n={mk['pnl_late']['n']}) {_rowmini(mk['pnl_late'])}.")
            else:
                L.append("- *View 1 — fill timing:* no fills.")
            L.append(f"- *View 2 — filled vs unfilled win-rate:* filled attempts "
                     f"won **{_pct(mk['filled_win_rate'])}** "
                     f"(n={mk['n_fill']}); unfilled attempts would have won "
                     f"**{_pct(mk['unfilled_win_rate'])}** (n={mk['n_unfill']}). "
                     f"If unfilled >> filled, the per-fill P&L is selection, not edge.")
            L.append(f"- *View 3 — per-ATTEMPT EV* (fills@realized, no-fills@$0, the "
                     f"number a strategy actually earns): **{_rowmini(mk['per_attempt'])}** "
                     f"(n={mk['per_attempt']['n']} attempts) vs per-FILL "
                     f"{_rowmini(mk['per_fill'])} (n={mk['n_fill']}).")
            L.append(f"- *Resting-level sensitivity (contrast, non-executable):* resting "
                     f"at the entry-minute BID/ASK QUOTE instead (stale-first-minute-quote "
                     f"family) gives fill_rate {_pct(mq['fill_rate'])}, per-ATTEMPT "
                     f"{_rowmini(mq['per_attempt'])}, per-fill {_rowmini(mq['per_fill'])} — "
                     f"shown only to size how much the resting-level choice moves the result.")
            L.append("")
    L.append("## Reading this (evidence, not verdict)")
    L.append("")
    L.append("- If **taker@traded** mean P&L is <= 0 or within ~2 SE of zero, the "
             "positive EV does NOT survive an executable entry — it was the "
             "stale-quote artifact (visible as taker@quote >> taker@traded).")
    L.append("- **Maker: judge on the adverse-selection views, not per-fill P&L.** "
             "(1) If fills cluster LATE (near settlement) and late-fill P&L is worse, "
             "fills are convergence-driven adverse selection. (2) If UNFILLED attempts "
             "have a higher would-have-won rate than FILLED, the per-fill P&L is "
             "selection — you miss precisely the winners. (3) **Per-ATTEMPT EV** "
             "(no-fills booked at $0) is what a resting-maker strategy actually earns; "
             "a positive per-fill number with a negative/near-zero per-attempt number "
             "is not a tradeable edge.")
    L.append("- Consistent with the settled Brier result (model ~= market, skill "
             "+/-0.02 noise): a real directional 5-9%/contract edge would beat the "
             "market Brier; it does not. So any positive maker number is NOT the "
             "model's directional skill.")
    L.append("- **What a positive maker per-attempt would be, if real:** spread/range "
             "capture — the maker enters at the traded CLOSE while the taker pays the "
             "HIGH; the gap is the intra-minute range. The model only picks which side "
             "to rest on, and at ~coin-flip skill that side is ~random. **Open "
             "diagnostic (not run):** re-run the maker with a fixed/random side — if "
             "the positive persists, it is signal-INDEPENDENT microstructure capture, "
             "not the SFP/model signal this study set out to test.")
    L.append("- **Load-bearing assumption — the maker fill is OPTIMISTIC:** it books a "
             "fill at your resting price whenever a later trade prints >=1 tick through "
             "it, with NO queue position and NO partial fills. fill_rate 0.93-0.97 is "
             "very high; a realistic queue/size model would lower fills (and the missed "
             "ones are ~100% winners, View 2) and could erase the maker positive. This "
             "is the make-or-break follow-up before any maker EV claim.")
    L.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"\nReport written: {path}", flush=True)


def _pct(x): return "n/a" if x is None else f"{x*100:.1f}%"
def _num(x): return "n/a" if x is None else f"{x:.4f}"
def _money(a): return "n/a" if not a or a.get("mean") is None else f"${a['mean']:+.4f}"


def _cell(a: dict) -> str:
    """Compact table cell: 'mean (t=..)'."""
    if not a or a.get("mean") is None:
        return "n/a"
    se = a.get("se")
    return f"{a['mean']:+.4f} (t={a['mean']/se:+.1f})" if se else f"{a['mean']:+.4f}"


def _rowmini(a: dict) -> str:
    """Compact inline 'mean +/- SE (t=..)' for prose."""
    if not a or a.get("mean") is None:
        return "n/a"
    se = a.get("se")
    if se:
        return f"${a['mean']:+.4f}+/-{se:.4f} (t={a['mean']/se:+.1f})"
    return f"${a['mean']:+.4f}"


def main() -> int:
    args = sys.argv[1:]
    assets = ASSETS
    if "--assets" in args:
        assets = [a.strip().upper() for a in args[args.index("--assets") + 1].split(",")]
    preview = "--preview" in args
    print("=" * 70)
    print("  EV FORENSIC — kalshi_crypto_v2 (executable entry + traded fills)")
    if preview:
        print("  *** PREVIEW: mechanics only (taker@traded vs taker@quote, maker")
        print("      fill rates). NO CONCLUSIONS until all 4 assets are in. ***")
    print("=" * 70)
    results = [run_asset(a) for a in assets]
    if preview:
        print("\n[PREVIEW] mechanics only — report NOT written; per-asset conclusions "
              "are deferred to the full 4-asset run.")
        return 0
    reports_dir = os.path.join(os.path.dirname(os.path.dirname(_KCV2)), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    path = os.path.join(reports_dir, "2026-08-02_kalshi_crypto_v2_EV_forensic.md")
    write_report(results, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
