"""Piece-1 regime parity gate (GROSS/label-only; read-only).

Proves the DEPLOYED engine-native regime (bitunix_sfp_observer.compute_regime_label)
reproduces the RESEARCH regime (regime_filter.regime_series 'ema200_pos_slope'),
label-for-label, on the native 15m bars for all 4 coins.

  (A) FORMULA parity  : compute_regime_label(closes[:i+1]) == research label[i]
      (same first-close-seeded EMA, same input) -> must be exact. Catches any wiring
      bug (em[-33], comparison direction).
  (B) LIVE-260-WINDOW : compute_regime_label(closes[i-259:i+1]) == research label[i]
      -> the real live fidelity. EMA-200 has ~260-bar memory, so a truncated live
      window can diverge from the full-history research EMA near label boundaries.
      Reported per coin with the truncation delta; this is the number that decides
      whether the live regime == the researched regime.
"""
import os, sqlite3, sys

DEPLOY   = r"C:\Users\AA Incorporado\cc-sfp-deploy-wt"
RESEARCH = r"C:\Users\AA Incorporado\cc-sfp-research-wt\spike_pivot_degree"
DATA     = r"C:\Users\AA Incorporado\cc\data"
sys.path.insert(0, RESEARCH)   # regime_filter, bitunix_sfp (SfpBar)
sys.path.insert(0, DEPLOY)     # trading_corp.* (the deployed observer)

from trading_corp.agents.divisions.bitunix_sfp_observer import (
    compute_regime_label, _regime_ema200, REGIME_MIN_BARS,
)
import regime_filter as rf
from bitunix_sfp import SfpBar

COINS = {"BTCUSDT": "btc", "ETHUSDT": "eth", "SOLUSDT": "sol", "XRPUSDT": "xrp"}
WIN = 1200   # == main.py:409 live 15m cache max_bars (EMA-200 converged; see sweep)


def load15(coin):
    con = sqlite3.connect(os.path.join(DATA, f"{COINS[coin]}_scalping.db"))
    rows = con.execute("SELECT ts,open,high,low,close FROM bars_15m "
                       "WHERE close IS NOT NULL ORDER BY ts").fetchall()
    con.close()
    return [SfpBar(int(t) * 1000, float(o), float(h), float(l), float(c))
            for t, o, h, l, c in rows]


def main():
    print(f"REGIME PARITY GATE  (min_bars={REGIME_MIN_BARS}, live_window={WIN})")
    print("=" * 78)
    all_A_ok = True; all_B_pct = []
    for coin in COINS:
        bars = load15(coin)
        closes = [b.close for b in bars]; ts = [b.ts_ms for b in bars]
        gt = rf.regime_series(bars, "ema200_pos_slope")     # {ts_ms: label}, i>=32
        em_full = _regime_ema200(closes)                    # research EMA (once)

        # (A) formula parity on a sample of full-prefix indices (exact expected)
        idxs = list(range(REGIME_MIN_BARS - 1, len(bars)))
        sample = idxs[:: max(1, len(idxs) // 50)][:50]
        aN = aM = 0
        for i in sample:
            g = gt.get(ts[i])
            if g is None:
                continue
            aN += 1; aM += (compute_regime_label(closes[: i + 1]) == g)
        A_ok = (aN > 0 and aM == aN)
        all_A_ok &= A_ok

        # (B) live-260-window sweep vs research full-history label
        bN = bM = tN = tM = fN = fM = 0
        mism = []
        for i in idxs:
            g = gt.get(ts[i])
            if g is None:
                continue
            w = closes[max(0, i - (WIN - 1)): i + 1]
            lab = compute_regime_label(w)
            ok = (lab == g)
            bN += 1; bM += ok
            if i < WIN:                       # window == full prefix (no truncation)
                fN += 1; fM += ok
            else:                             # sliding 260-window (truncation region)
                tN += 1; tM += ok
                if not ok and len(mism) < 6:
                    emw = _regime_ema200(w)[-1]
                    mism.append((ts[i], lab, g, closes[i], emw, em_full[i]))
        Bpct = 100 * bM / bN if bN else float("nan")
        all_B_pct.append((coin, Bpct, tN, tM))

        print(f"\n{coin}: bars={len(bars)}  comparable={bN}")
        print(f"  (A) formula parity  : {aM}/{aN} sampled  {'EXACT' if A_ok else '*** FAIL ***'}")
        print(f"  (B) live-260-window : {bM}/{bN} = {Bpct:.3f}%")
        print(f"      non-truncated(i<260): {fM}/{fN}   truncated(i>=260): {tM}/{tN} "
              f"= {100*tM/tN if tN else float('nan'):.3f}%")
        for ts_, lab, g, c, emw, emf in mism:
            print(f"      MISMATCH ts={ts_} live={lab} research={g} close={c:.4f} "
                  f"em_win={emw:.4f} em_full={emf:.4f} d={emw-emf:+.4f}")

    print("\n" + "=" * 78)
    print(f"(A) FORMULA parity all coins EXACT: {all_A_ok}")
    for coin, p, tN, tM in all_B_pct:
        print(f"(B) {coin} live-window match: {p:.3f}%  (truncated region {tM}/{tN})")


if __name__ == "__main__":
    main()
