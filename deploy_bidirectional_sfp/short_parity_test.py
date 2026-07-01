"""Piece-2 OFFLINE SHORT-PARITY GATE (GROSS/detection-only; read-only).

Proves — BEFORE any real sell — that SHORT detection via the live M2=0 reflection
reproduces the RESEARCH reflection (short_sfp_sweep, M2=max(high)+min(low)) using the
SAME byte-identical SfpModeBDetector:
  * ts-for-ts    : fire/bos/entry indices + bos ts + mode identical, per coin;
  * level-for-level : the un-reflected real swept_high and bos_ref match (float tol);
  * (doubles as the empirical affine-invariance proof: identical fires ⇒ M2 doesn't
    matter, so M2=0 is sound and cannot drift).
Plus a per-fire GEOMETRY proof: geometry_short gives stop > entry > tp and r_unit > 0.

If any fire or level diverges → STOP, no real sell until green.
"""
import os, sqlite3, sys

DEPLOY   = r"C:\Users\AA Incorporado\cc-sfp-deploy-wt"
RESEARCH = r"C:\Users\AA Incorporado\cc-sfp-research-wt\spike_pivot_degree"
DATA     = r"C:\Users\AA Incorporado\cc\data"
sys.path.insert(0, RESEARCH)
sys.path.insert(0, DEPLOY)

from trading_corp.agents.divisions.bitunix_sfp_observer import reflect_neg, geometry_short
import backtest as bt
import short_sfp_sweep as ss        # research reflect: M2 = max(high) + min(low)
from bitunix_sfp import SfpBar

COINS = {"BTCUSDT": "btc", "ETHUSDT": "eth", "SOLUSDT": "sol", "XRPUSDT": "xrp"}
PIVOTS = [5, 8, 10]
STOP_BUF, TP_R, TOL = 0.001, 2.0, 1e-6


def load3(coin):
    con = sqlite3.connect(os.path.join(DATA, f"{COINS[coin]}_scalping.db"))
    rows = con.execute("SELECT ts,open,high,low,close FROM bars_3m "
                       "WHERE close IS NOT NULL ORDER BY ts").fetchall()
    con.close()
    return [SfpBar(int(t) * 1000, float(o), float(h), float(l), float(c))
            for t, o, h, l, c in rows]


def main():
    print("OFFLINE SHORT-PARITY GATE  (M2=0 live reflection vs research M2=max+min)")
    print("=" * 82)
    all_ok = True
    for coin in COINS:
        n3 = load3(coin)
        m2 = max(b.high for b in n3) + min(b.low for b in n3)
        r3_res, r3_neg = ss.reflect(n3), reflect_neg(n3)
        r15_res, r15_neg = bt.resample_15m(r3_res), bt.resample_15m(r3_neg)
        fires = 0; ts_ok = lvl_ok = geo_ok = True; degen = 0
        for pl in PIVOTS:
            sr = sorted(bt.get_signals(r15_res, r3_res, pl),
                        key=lambda s: (s.entry_bar_index, s.sfp_mode))
            sn = sorted(bt.get_signals(r15_neg, r3_neg, pl),
                        key=lambda s: (s.entry_bar_index, s.sfp_mode))
            if len(sr) != len(sn):
                ts_ok = False; continue
            for a, b in zip(sr, sn):
                fires += 1
                if (a.entry_bar_index != b.entry_bar_index or
                        a.bos_bar_ts_ms != b.bos_bar_ts_ms or a.sfp_mode != b.sfp_mode):
                    ts_ok = False
                sh_res, sh_neg = m2 - a.swept_low, -b.swept_low        # real swept high
                bo_res, bo_neg = m2 - a.bos_ref_high, -b.bos_ref_high  # real bos ref
                if (abs(sh_res - sh_neg) > TOL * max(abs(sh_res), 1) or
                        abs(bo_res - bo_neg) > TOL * max(abs(bo_res), 1)):
                    lvl_ok = False
                entry_ref = n3[b.bos_bar_index].close                 # real BOS bar close
                geo = geometry_short(entry_ref, sh_neg, stop_buffer_pct=STOP_BUF, tp_r=TP_R)
                if geo is None:
                    degen += 1
                else:
                    stop, tp, r = geo
                    if not (stop > entry_ref > tp and r > 0):
                        geo_ok = False
        ok = ts_ok and lvl_ok and geo_ok
        all_ok &= ok
        print(f"{coin}: short_fires={fires:4d}  ts_match={ts_ok}  level_match={lvl_ok}  "
              f"geom(stop>entry>tp,r>0)={geo_ok}  degenerate_skip={degen}  "
              f"-> {'PASS' if ok else '*** FAIL ***'}")
    print("=" * 82)
    print(f"OFFLINE SHORT-PARITY GATE: {'ALL PASS' if all_ok else '*** FAIL — NO REAL SELL ***'}")


if __name__ == "__main__":
    main()
