"""Step A: precursor mining — which corpus signals reliably PRECEDE the Otter divergence?

For each div fire at bar T (bull_divergence / bear_divergence separately), look at the 1-4 bars
before it. For every candidate precursor signal P report, per fold (train, validate; lockbox
excluded):
  RECALL(1-4)  = P(P fired in T-1..T-4 | div at T)            -> is P usually there before a div?
  PRECISION    = P(a div follows in [i+2,i+4] | P fired at i) -> when P fires, does a div follow?
  LIFT         = PRECISION / base_rate(div in a random 2-4 window)
A real leader needs PRECISION + LIFT (>>1) AND decent recall AND enough fires (constant-firing
signals get high recall but ~base-rate precision -> useless). Cypher precursors are studied
DESCRIPTIVELY and tagged (a tradeable Cypher trigger needs operator approval).
"""
from __future__ import annotations
import json, sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = r"C:\Users\AA Incorporado\cc\data\btc_scalping.db"
TRAIN_END = datetime(2026, 5, 15, tzinfo=timezone.utc).timestamp()
VAL_END = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()   # >= = LOCKBOX (excluded)

OTTER_PRE = ["otter_buy", "otter_sell", "super_buy_high", "super_sell_high", "super_buy_std",
             "super_sell_std", "top_signal", "bottom_signal", "ribbon_buy_cross",
             "ribbon_sell_cross", "cvd_flip_bullish", "cvd_flip_bearish"]
CYPHER_PRE = ["red_diamond", "blood_diamond", "blue_triangle", "red_cross", "yellow_cross",
              "bull_candle", "buy_circle", "sell_circle", "divergence_buy_circle",
              "divergence_sell_circle", "gold_buy_gold_circle", "wt_bearish_divergence",
              "wt_bullish_divergence", "wt_2nd_bearish_divergence", "wt_2nd_bullish_divergence",
              "rsi_bearish_divergence", "rsi_bullish_divergence", "stoch_bearish_divergence",
              "stoch_bullish_divergence", "long_ema_signal", "short_ema_signal",
              "buy_signal", "sell_signal"]
CONT_PRE = ["rsi_exit_os", "rsi_exit_ob", "macd_up", "macd_dn", "ema_up", "ema_dn"]
FAMILY = {**{p: "OTTER" for p in OTTER_PRE}, **{p: "cont" for p in CONT_PRE},
          **{p: "CYPHER" for p in CYPHER_PRE}}


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    base = ["ts", "open", "high", "low", "close", "rsi", "histogram", "ema_8",
            "bull_divergence", "bear_divergence"]
    cols = base + OTTER_PRE + CYPHER_PRE
    rows = con.execute(f"SELECT {','.join(cols)} FROM bars_3m ORDER BY ts").fetchall()
    con.close()
    ci = {c: i for i, c in enumerate(cols)}
    N = len(rows)

    def fnum(i, c):
        v = rows[i][ci[c]]
        return float(v) if v is not None else None

    # fired-bar index sets
    fired = {p: set() for p in OTTER_PRE + CYPHER_PRE + CONT_PRE}
    for p in OTTER_PRE + CYPHER_PRE:
        for i in range(N):
            v = rows[i][ci[p]]
            if v is not None and float(v) != 0.0:
                fired[p].add(i)
    for i in range(1, N):
        r0, r1 = fnum(i - 1, "rsi"), fnum(i, "rsi")
        if r0 is not None and r1 is not None:
            if r0 < 30 <= r1: fired["rsi_exit_os"].add(i)
            if r0 > 70 >= r1: fired["rsi_exit_ob"].add(i)
        h0, h1 = fnum(i - 1, "histogram"), fnum(i, "histogram")
        if h0 is not None and h1 is not None:
            if h0 < 0 <= h1: fired["macd_up"].add(i)
            if h0 > 0 >= h1: fired["macd_dn"].add(i)
        c0, c1 = fnum(i - 1, "close"), fnum(i, "close")
        e0, e1 = fnum(i - 1, "ema_8"), fnum(i, "ema_8")
        if None not in (c0, c1, e0, e1):
            if c0 < e0 and c1 >= e1: fired["ema_up"].add(i)
            if c0 > e0 and c1 <= e1: fired["ema_dn"].add(i)

    out = {}
    for tgt in ("bull_divergence", "bear_divergence"):
        div_all = {i for i in range(N) if rows[i][ci[tgt]] and float(rows[i][ci[tgt]]) != 0.0}
        def fold(lo, hi):
            bars = [i for i in range(N) if lo <= rows[i][ci["ts"]] < hi and i + 4 < N]
            divs = [i for i in bars if i in div_all]
            if not divs or not bars:
                return None, None, None
            base_rate = sum(1 for i in bars if any((i + d) in div_all for d in (2, 3, 4))) / len(bars)
            recs = []
            for p in OTTER_PRE + CONT_PRE + CYPHER_PRE:
                fl = fired[p]
                rec_any = sum(1 for d in divs if any((d - L) in fl for L in (1, 2, 3, 4))) / len(divs)
                fires = [i for i in bars if i in fl]
                if fires:
                    prec = sum(1 for i in fires if any((i + d) in div_all for d in (2, 3, 4))) / len(fires)
                else:
                    prec = 0.0
                lift = prec / base_rate if base_rate > 0 else 0.0
                recs.append({"p": p, "fam": FAMILY[p], "n_fires": len(fires),
                             "recall14": round(rec_any, 3), "prec": round(prec, 3),
                             "lift": round(lift, 2)})
            return base_rate, len(divs), recs
        br_t, nd_t, recs_t = fold(0, TRAIN_END)
        br_v, nd_v, recs_v = fold(TRAIN_END, VAL_END)
        out[tgt] = {"train": {"base_rate": round(br_t, 4), "n_div": nd_t, "rows": recs_t},
                    "validate": {"base_rate": round(br_v, 4), "n_div": nd_v, "rows": recs_v}}

        print(f"\n################ TARGET: {tgt}  (train base_rate={br_t:.3%}, n_div={nd_t}) ################")
        vmap = {r["p"]: r for r in recs_v}
        ranked = sorted(recs_t, key=lambda r: r["lift"] * (1 if r["n_fires"] >= 20 else 0.3), reverse=True)
        print(f"{'precursor':<24}{'fam':<7}{'nFire':<7}{'recall14':<9}{'prec':<7}{'lift':<7} | {'VAL prec/lift/nFire'}")
        for r in ranked[:18]:
            v = vmap.get(r["p"], {})
            vp = f"{v.get('prec','-')}/{v.get('lift','-')}/{v.get('n_fires','-')}"
            print(f"{r['p']:<24}{r['fam']:<7}{r['n_fires']:<7}{r['recall14']:<9}{r['prec']:<7}{r['lift']:<7} | {vp}")

    Path(r"C:\Users\AA Incorporado\cc-precursor-wt\data\precursor").mkdir(parents=True, exist_ok=True)
    Path(r"C:\Users\AA Incorporado\cc-precursor-wt\data\precursor\stepA.json").write_text(json.dumps(out, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
