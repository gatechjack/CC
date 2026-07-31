"""
repro_sanity.py -- MANDATORY STOP-GATE. Re-derive the fractal-degree SFP construct
population from the live detector (bitunix_sfp) via the frozen degree-rerun
machinery, and confirm the canary cell EXACTLY:
    n=13426  sumR=-1.07  long 6718/-84.14  short 6708/+83.08
If it does not reproduce -> STOP (do not run STUDY A/B).

Re-derivation only (calls DR.detect_and_book); overwrites no artifacts; reads the
Binance-perp corpus read-only via the degree-rerun's own loaders.
"""
import sqlite3, sys, time

SFP_DIR = r"C:\Users\AA Incorporado\cc\trading_corp\agents\strategies"
BTC_CORPUS_DIR = r"C:\Users\AA Incorporado\Desktop\backtest_corpus"
for p in (SFP_DIR, BTC_CORPUS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)
import _sfp_degree_rerun as DR

TARGET = dict(n=13426, sumR=-1.07, long_n=6718, long_sumR=-84.14,
              short_n=6708, short_sumR=83.08)

def main():
    t0 = time.time()
    con = sqlite3.connect(DR.DB)
    m45, m60, mic = DR.load_regime_maps(con)
    allt = []
    for coin in DR.COINS:
        tc = time.time()
        b15 = DR.load_bars(con, coin, "15m")
        b3 = DR.load_bars(con, coin, "3m")
        labels, cts = DR.precompute_regime(b15)
        tr, raw = DR.detect_and_book(coin, b15, b3, labels, cts, "fractal", m45, m60, mic)
        allt += tr
        print(f"  {coin} fractal: booked={len(tr)} raw={raw} ({time.time()-tc:.0f}s)", flush=True)
    con.close()

    n = len(allt); sr = sum(t["R"] for t in allt)
    L = [t for t in allt if t["side"] == "long"]; S = [t for t in allt if t["side"] == "short"]
    ln, lsr = len(L), sum(t["R"] for t in L)
    sn, ssr = len(S), sum(t["R"] for t in S)
    print(f"\n  DERIVED : n={n} sumR={sr:+.2f} | long {ln}/{lsr:+.2f} | short {sn}/{ssr:+.2f}")
    print(f"  TARGET  : n={TARGET['n']} sumR={TARGET['sumR']:+.2f} | "
          f"long {TARGET['long_n']}/{TARGET['long_sumR']:+.2f} | "
          f"short {TARGET['short_n']}/{TARGET['short_sumR']:+.2f}")
    ok = (n == TARGET["n"] and abs(sr - TARGET["sumR"]) < 0.02 and
          ln == TARGET["long_n"] and abs(lsr - TARGET["long_sumR"]) < 0.02 and
          sn == TARGET["short_n"] and abs(ssr - TARGET["short_sumR"]) < 0.02)
    print(f"\n  SANITY GATE: {'PASS' if ok else '*** FAIL -- STOP ***'}  ({time.time()-t0:.0f}s)")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
