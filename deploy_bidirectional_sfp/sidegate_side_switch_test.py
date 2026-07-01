"""Piece-6b config.side KILL-SWITCH trace (GROSS/routing-only; read-only).

Proves the config.side gate (a pure SUPPRESSION layer on top of the regime gate):
  side=regime -> bidirectional (identical to the Piece-3 trace: NO side_disabled skips;
                 long up/range ALLOW + down SKIP counter_trend; short down/range ALLOW +
                 up SKIP counter_trend).
  side=long   -> LONG-ONLY: every short -> SKIP side_disabled; longs unchanged.
  side=short  -> SHORT-ONLY (mirror): every long -> SKIP side_disabled; shorts unchanged.
"""
import os, sqlite3, sys

DEPLOY   = r"C:\Users\AA Incorporado\cc-sfp-deploy-wt"
RESEARCH = r"C:\Users\AA Incorporado\cc-sfp-research-wt\spike_pivot_degree"
DATA     = r"C:\Users\AA Incorporado\cc\data"
sys.path.insert(0, RESEARCH)
sys.path.insert(0, DEPLOY)

from trading_corp.agents.divisions.bitunix_sfp_observer import reflect_neg
import backtest as bt
import regime_filter as rf
from bitunix_sfp import SfpBar

COINS = {"BTCUSDT": "btc", "ETHUSDT": "eth", "SOLUSDT": "sol", "XRPUSDT": "xrp"}
PIVOTS = [5, 8, 10]
_15M = 900_000


def load(coin, table):
    con = sqlite3.connect(os.path.join(DATA, f"{COINS[coin]}_scalping.db"))
    rows = con.execute(f"SELECT ts,open,high,low,close FROM {table} "
                       "WHERE close IS NOT NULL ORDER BY ts").fetchall()
    con.close()
    return [SfpBar(int(t) * 1000, float(o), float(h), float(l), float(c))
            for t, o, h, l, c in rows]


def decide(cfg_side, side, regime):
    """Mirror of the observer: config.side suppression THEN the regime gate."""
    allowed = {"regime": ("long", "short"), "long": ("long",),
               "short": ("short",)}[cfg_side]
    if side not in allowed:
        return "SKIP", "side_disabled"
    if regime is None:
        return "SKIP", "regime_warmup"
    aligned = (regime in ("up", "range")) if side == "long" else (regime in ("down", "range"))
    return ("ALLOW", "") if aligned else ("SKIP", "counter_trend")


def main():
    print("config.side KILL-SWITCH trace  (regime = Piece-3; long = long-only; short = short-only)")
    print("=" * 90)
    all_ok = True
    for coin in COINS:
        n15, n3 = load(coin, "bars_15m"), load(coin, "bars_3m")
        gt = rf.regime_series(n15, "ema200_pos_slope")
        win15 = [b for b in n15 if n3[0].ts_ms <= b.ts_ms <= n3[-1].ts_ms]
        r15, r3 = reflect_neg(win15), reflect_neg(n3)
        tagged = []
        for pl in PIVOTS:
            for s in bt.get_signals(win15, n3, pl):
                if s.entry_bar_index < len(n3):
                    ets = n3[s.entry_bar_index].ts_ms
                    tagged.append(("long", gt.get((ets - ets % _15M) - _15M)))
            for s in bt.get_signals(r15, r3, pl):
                if s.entry_bar_index < len(r3):
                    ets = r3[s.entry_bar_index].ts_ms
                    tagged.append(("short", gt.get((ets - ets % _15M) - _15M)))

        n_long = sum(1 for sd, _ in tagged if sd == "long")
        n_short = sum(1 for sd, _ in tagged if sd == "short")
        checks = {}
        print(f"\n{coin}: {n_long} long / {n_short} short signals")
        for cfg in ("regime", "long", "short"):
            dec = [(sd, decide(cfg, sd, rg)) for sd, rg in tagged]
            sh_dis = sum(1 for sd, (d, r) in dec if sd == "short" and r == "side_disabled")
            sh_oth = sum(1 for sd, (d, r) in dec if sd == "short" and r != "side_disabled")
            lo_dis = sum(1 for sd, (d, r) in dec if sd == "long" and r == "side_disabled")
            lo_oth = sum(1 for sd, (d, r) in dec if sd == "long" and r != "side_disabled")
            print(f"  side={cfg:6s}: SHORT[disabled={sh_dis:3d} regime-gated={sh_oth:3d}]  "
                  f"LONG[disabled={lo_dis:3d} regime-gated={lo_oth:3d}]")
            if cfg == "regime":
                checks["regime: 0 side_disabled (== Piece-3)"] = (sh_dis == 0 and lo_dis == 0)
            elif cfg == "long":
                checks["long: ALL shorts disabled"] = (sh_dis == n_short and sh_oth == 0)
                checks["long: NO long disabled"] = (lo_dis == 0 and lo_oth == n_long)
            else:
                checks["short: ALL longs disabled"] = (lo_dis == n_long and lo_oth == 0)
                checks["short: NO short disabled"] = (sh_dis == 0 and sh_oth == n_short)
        ok = all(checks.values())
        all_ok &= ok
        for k, v in checks.items():
            print(f"    {'OK  ' if v else 'FAIL'} {k}")
        print(f"  -> {'PASS' if ok else '*** FAIL ***'}")
    print("\n" + "=" * 90)
    print(f"config.side KILL-SWITCH: {'ALL PASS' if all_ok else '*** FAIL ***'}")


if __name__ == "__main__":
    main()
