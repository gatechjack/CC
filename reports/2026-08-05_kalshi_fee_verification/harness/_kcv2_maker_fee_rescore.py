#!/usr/bin/env python3
# TASK 3.2 -- RE-SCORE the kcv2 maker-resolution + ETH realism gate under the CORRECT maker fee.
# The harness charged makers the TAKER schedule kalshi_fee=ceil(0.07*C*P*(1-P)*100)/100 at qty=1.
# Kalshi's actual MAKER fee = 25% of taker = ceil(maker_mult*C*P*(1-P)*100)/100 with maker_mult =
# 0.25 * crypto_taker_mult. We BRACKET maker_mult over the plausible range (crypto multiplier
# unknown -- official PDF bot-blocked) + a maker-free bound, and reproduce the OLD (0.07) column as
# a parity gate. ONLY the maker fee changes; fills are fee-independent so cached once per asset.
# Same per-attempt mean/SE t-stat the originals use (no clustering in these two studies). READ-ONLY
# on the lab DB. Evidence only -- no verdict language.
import os, sys, math
sys.path.insert(0, r"C:\Users\AA Incorporado\cc-2026-08-02-wt")                         # repo root (trading_corp)
sys.path.insert(0, r"C:\Users\AA Incorporado\cc-2026-08-02-wt\research\kalshi_crypto_v2\s4")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import ev_forensic as EVF
import maker_resolution as MR
import maker_realism as MRz

# maker-fee multipliers to bracket (maker_mult = 0.25 * crypto_taker_mult):
#   0.07   = OLD harness (taker-as-maker) -> PARITY column, must reproduce the originals
#   0.0175 = 25% of base 0.07            (if crypto taker mult == base 0.07)
#   0.035  = 25% of 0.14 (2x premium)    (if crypto is a "premium" category ~2x)
#   0.0    = maker-free bound
MULTS = [("OLD 0.07 (taker-as-maker)", 0.07),
         ("maker 0.0175 (25%x0.07)", 0.0175),
         ("maker 0.035 (25%x0.14)", 0.035),
         ("maker 0.0 (free)", 0.0)]
ASSETS = ["BTC", "ETH", "SOL", "XRP"]


def set_mult(mult):
    EVF.kalshi_fee = lambda q, p, _m=mult: math.ceil(_m * q * p * (1.0 - p) * 100.0) / 100.0


def cell(res):
    pa = res.get("per_attempt") or {}
    m, se = pa.get("mean"), pa.get("se")
    t = (m / se) if (m is not None and se) else None
    fr = res.get("fill_rate")
    return m, t, fr


def fmt(m, t, fr=None):
    if m is None:
        return "n/a"
    s = "%+.4f(t%+.1f)" % (m, t) if t is not None else "%+.4f" % m
    if fr is not None:
        s += " f%.0f%%" % (fr * 100)
    return s


def main():
    print("=" * 100)
    print("TASK 3.2 -- kcv2 MAKER-FEE RE-SCORE (maker-resolution + ETH realism gate), per-attempt $/ct")
    print("Fee model: ceil(mult*C*P*(1-P)*100)/100 at qty=1. OLD=0.07 is the parity gate.")
    print("=" * 100)
    # cache the fee-INDEPENDENT holdout (train/split + candles) once per asset
    cache = {}
    for a in ASSETS:
        print("  [load] reproduce_holdout(%s) + candles ..." % a, flush=True)
        df, mp = MR.reproduce_holdout(a)
        cand = MR.load_candles_ohlc(df["market_ticker"].tolist(), MR.LAB_DB)
        cache[a] = (df, mp, cand)

    # ---- maker-resolution: 1a model side (A) + 1b ALL-combined (A) ----
    print("")
    print("### A. MAKER RESOLUTION -- per-attempt EV, variant A (model side)")
    print("  1a = baseline(thru1,slip0,min1) ; 1b_ALL = ALL-combined(thru2,slip1,min3)")
    hdr = "  %-6s %-26s | %-22s | %-22s" % ("asset", "fee", "1a model", "1b ALL-combined")
    print(hdr); print("  " + "-" * 96)
    for a in ASSETS:
        df, mp, cand = cache[a]
        for label, mult in MULTS:
            set_mult(mult)
            r1a = MR.eval_config(df, mp, cand, "A", "model", 1, 0, 1)
            r1b = MR.eval_config(df, mp, cand, "A", "model", 2, 1, 3)
            m1, t1, f1 = cell(r1a); m2, t2, f2 = cell(r1b)
            print("  %-6s %-26s | %-22s | %-22s" % (a, label, fmt(m1, t1, f1), fmt(m2, t2, f2)), flush=True)
        print("  " + "-" * 96)

    # ---- ETH realism gate: a+b+c (thru2,slip1,min3), placement delays 0/1/2 ----
    print("")
    print("### B. ETH REALISM GATE -- realism+full-pessimism (a+b+c), per-attempt EV by placement delay")
    print("  the GATE is ETH; BTC/SOL/XRP are controls")
    print("  %-6s %-26s | %-20s %-20s %-20s" % ("asset", "fee", "m+0", "m+1", "m+2"))
    print("  " + "-" * 96)
    for a in ASSETS:
        df, mp, cand = cache[a]
        for label, mult in MULTS:
            set_mult(mult)
            cells = []
            for d in (0, 1, 2):
                r = MRz.eval_config(df, mp, cand, d, 2, 1, 3)
                m, t, fr = cell(r)
                cells.append(fmt(m, t, fr))
            print("  %-6s %-26s | %-20s %-20s %-20s" % (a, label, cells[0], cells[1], cells[2]), flush=True)
        print("  " + "-" * 96)
    print("")
    print("[PARITY CHECK] OLD 0.07 rows should reproduce the 2026-08-02 originals:")
    print("  ETH maker-resolution 1b ALL ~ +0.030 (t~2.5) ; ETH realism a+b+c m+0 ~+0.030 -> m+2 ~-0.002")
    print("=" * 100)


if __name__ == "__main__":
    main()
