"""Piece-1 regime SEED re-gate (GROSS/label-only; read-only).

Proves the DEPLOYED engine-native regime (bitunix_sfp_observer.compute_regime_label
over the seeded closes buffer) reproduces the RESEARCH regime (regime_filter
'ema200_pos_slope'), label-for-label, per coin.

Seed design (Option A, gate 800): at boot the buffer is seeded from the append-only
`bitunix_bar_history` 15m capture (venue-independent), gated OFF (None) below
REGIME_SEED_MIN, capped at REGIME_SEED_BARS, extended inline by live closes.

  (A) FORMULA parity : compute_regime_label(closes[:i+1]) == research label[i] — EXACT.
  (B) SEED parity    : compute_regime_label over the last-N buffer vs research, at
      N = REGIME_SEED_MIN (the gate FLOOR — the worst case we ever trade at) and
      N = REGIME_SEED_BARS (CONVERGED working set). The live buffer rides between the
      two as it deepens from the current seed depth toward the cap.
"""
import os, sqlite3, sys

DEPLOY   = r"C:\Users\AA Incorporado\cc-sfp-deploy-wt"
RESEARCH = r"C:\Users\AA Incorporado\cc-sfp-research-wt\spike_pivot_degree"
DATA     = r"C:\Users\AA Incorporado\cc\data"
sys.path.insert(0, RESEARCH)
sys.path.insert(0, DEPLOY)

from trading_corp.agents.divisions.bitunix_sfp_observer import (
    compute_regime_label, REGIME_MIN_BARS, REGIME_SEED_MIN, REGIME_SEED_BARS,
)
import regime_filter as rf
from bitunix_sfp import SfpBar

COINS = {"BTCUSDT": "btc", "ETHUSDT": "eth", "SOLUSDT": "sol", "XRPUSDT": "xrp"}
CURRENT_SEED_DEPTH = 788   # bitunix_bar_history 15m rows/coin today (SSH-confirmed)


def load15(coin):
    con = sqlite3.connect(os.path.join(DATA, f"{COINS[coin]}_scalping.db"))
    rows = con.execute("SELECT ts,open,high,low,close FROM bars_15m "
                       "WHERE close IS NOT NULL ORDER BY ts").fetchall()
    con.close()
    return [SfpBar(int(t) * 1000, float(o), float(h), float(l), float(c))
            for t, o, h, l, c in rows]


def parity_at(closes, ts, gt, win):
    n = m = 0
    for i in range(win - 1, len(closes)):
        g = gt.get(ts[i])
        if g is None:
            continue
        lab = compute_regime_label(closes[max(0, i - (win - 1)): i + 1])
        n += 1; m += (lab == g)
    return m, n


def main():
    print(f"REGIME SEED RE-GATE  (gate={REGIME_SEED_MIN}, cap={REGIME_SEED_BARS}, "
          f"formula_min={REGIME_MIN_BARS})")
    print(f"Current bitunix_bar_history seed depth/coin: {CURRENT_SEED_DEPTH} "
          f"({'BELOW' if CURRENT_SEED_DEPTH < REGIME_SEED_MIN else 'ABOVE'} gate "
          f"{REGIME_SEED_MIN} -> {'~3h live-warmup then trades' if CURRENT_SEED_DEPTH < REGIME_SEED_MIN else 'trades at boot'}; append-only, self-heals)")
    print("=" * 80)
    aok = True
    for coin in COINS:
        bars = load15(coin); closes = [b.close for b in bars]; ts = [b.ts_ms for b in bars]
        gt = rf.regime_series(bars, "ema200_pos_slope")
        # (A) formula sample (exact expected)
        idxs = list(range(REGIME_MIN_BARS - 1, len(bars)))
        aN = aM = 0
        for i in idxs[:: max(1, len(idxs) // 50)][:50]:
            g = gt.get(ts[i])
            if g is not None:
                aN += 1; aM += (compute_regime_label(closes[: i + 1]) == g)
        A_ok = (aN > 0 and aM == aN); aok &= A_ok
        # (B) seed parity at gate floor + converged
        fM, fN = parity_at(closes, ts, gt, REGIME_SEED_MIN)
        cM, cN = parity_at(closes, ts, gt, REGIME_SEED_BARS)
        print(f"{coin}: (A) formula {aM}/{aN} {'EXACT' if A_ok else '*FAIL*'}  |  "
              f"(B) gate-floor({REGIME_SEED_MIN}): {100*fM/fN:.3f}%  "
              f"converged({REGIME_SEED_BARS}): {100*cM/cN:.3f}%")
    print("=" * 80)
    print(f"(A) FORMULA parity all coins EXACT: {aok}")
    print("(B) live buffer rides gate-floor -> converged as it deepens from the "
          f"{CURRENT_SEED_DEPTH}-bar seed toward the {REGIME_SEED_BARS} cap.")


if __name__ == "__main__":
    main()
