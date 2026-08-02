"""ETH MAKER latency/realism GATE for the shadow build (2026-08-02, on-disk).
READ-ONLY. NO order/placement surface.

The maker-resolution survivor (ETH, model side, traded-close rest) still had two
optimisms the shadow would inherit. This re-runs it with them removed and reports
the decay -- if the ETH positive dies here, the maker-shadow soaks nothing.

REALISM FIXES (operator-specified):
  (a) RESTING LEVEL = the PRIOR minute's traded close (price_close of minute p-1),
      i.e. information known BEFORE you place at minute p -- no same-minute-close
      look-ahead (the resolution study rested at the entry minute's own close).
  (b) FILLS counted only from the minute AFTER placement (candles at minute >= p+1);
      a resting order placed at p cannot fill on trades that already happened.
  (c) the FULL 1b PESSIMISM STACK on top: trade-through by 2 ticks, fill 1 tick
      worse (slippage), and skip entry minutes 1-2 (first tradeable minute >= 3).

DECAY: placement minute p = e + place_delay for place_delay in {0,1,2}, where e =
the first tradeable in-window minute >= min_min. side = the S4 model side (known
as-of open-60s; not look-ahead). Reported like the continuation-latency study.

per-ATTEMPT $/contract (fills@realized, no-fills@$0) is the honest number; fill
rate beside every maker figure. ★ standing caveats carry (optimistic queue-free
fill remains -- the shadow is the live arbiter; T5 Binance/RTI basis untouched
here since side is the model, not a move). Evidence only -- no verdict.
"""
from __future__ import annotations

import os
import sys

_S4 = os.path.dirname(os.path.abspath(__file__))
if _S4 not in sys.path:
    sys.path.insert(0, _S4)

from ev_forensic import (  # noqa: E402
    ASSETS, TICK, reproduce_holdout, load_candles_ohlc, _traded, realized,
    _new_macc, _finalize_maker, _valid_price, LAB_DB,
)

# (label, through_ticks, slippage_ticks, min_min)
CONFIGS = [
    ("realism only (a+b)", 1, 0, 1),
    ("realism + full pessimism (a+b+c)", 2, 1, 3),
]
PLACE_DELAYS = [0, 1, 2]


def _fill(side: str, rest_level: float, later: list[dict], through: int, slippage: int):
    """(filled, fill_price, fill_ts). Rest on the bet's side at rest_level (the
    PRIOR minute's traded close); fill on a real >=`through`-tick trade-through in
    `later` (minute-after-placement candles); fill price `slippage` ticks worse."""
    if side == "yes":
        fp = rest_level + slippage * TICK
        if not (_valid_price(rest_level) and _valid_price(fp)):
            return None
        thr = rest_level - through * TICK
        for c in later:
            if (c.get("volume") or 0) > 0 and c.get("price_low") is not None and c["price_low"] <= thr:
                return True, fp, c["ts"]
        return False, fp, None
    n = 1.0 - rest_level
    fp = n + slippage * TICK
    if not (_valid_price(n) and _valid_price(fp)):
        return None
    thr = rest_level + through * TICK
    for c in later:
        if (c.get("volume") or 0) > 0 and c.get("price_high") is not None and c["price_high"] >= thr:
            return True, fp, c["ts"]
    return False, fp, None


def eval_config(df_holdout, model_p, cand, place_delay, through, slippage, min_min) -> dict:
    mk = _new_macc()
    for i, row in df_holdout.reset_index(drop=True).iterrows():
        cs = cand.get(row["market_ticker"]) or []
        if not cs:
            continue
        ot, ct = int(row["open_ts"]), int(row["close_ts"])
        # minute -> tradeable candle
        bym = {}
        for c in cs:
            m = round((c["ts"] - ot) / 60.0)
            if 1 <= m <= 15 and _traded(c):
                bym[m] = c
        e = min((m for m in bym if m >= min_min), default=None)   # first tradeable >= min_min
        if e is None:
            continue
        p = e + place_delay                                       # placement minute
        rest_c = bym.get(p - 1)                                   # PRIOR minute (known before placing)
        if rest_c is None or rest_c.get("price_close") is None:
            continue
        rest_level = rest_c["price_close"]
        later = [c for c in cs if round((c["ts"] - ot) / 60.0) >= p + 1]   # fills AFTER placement
        p_ = float(model_p[i])
        side = "yes" if p_ >= 0.5 else "no"
        y = int(row["y"])
        won = (y == 1) if side == "yes" else (y == 0)
        res = _fill(side, rest_level, later, through, slippage)
        if res is None:
            continue
        filled, fprice, fts = res
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
    return _finalize_maker(mk)


def run_asset(asset: str) -> dict:
    print(f"\n== {asset} ==", flush=True)
    df, mp = reproduce_holdout(asset)
    cand = load_candles_ohlc(df["market_ticker"].tolist(), LAB_DB)
    out = {"asset": asset, "configs": {}}
    for label, thru, slip, mn in CONFIGS:
        out["configs"][label] = {d: eval_config(df, mp, cand, d, thru, slip, mn)
                                 for d in PLACE_DELAYS}
    # console: the gate = realism+full-pessimism per-attempt decay
    row = out["configs"]["realism + full pessimism (a+b+c)"]
    s = " ".join(f"m+{d}={_m(row[d]['per_attempt'])}(fill{_pct(row[d]['fill_rate'])})"
                 for d in PLACE_DELAYS)
    print(f"  {asset} realism+pessimism per-ATTEMPT decay: {s}", flush=True)
    return out


def _m(a):
    if not a or a.get("mean") is None:
        return "n/a"
    se = a.get("se")
    return f"{a['mean']:+.3f}(t{a['mean']/se:+.1f})" if se else f"{a['mean']:+.3f}"


def _pct(x): return "n/a" if x is None else f"{x*100:.0f}%"


def _cell(a):
    if not a or a.get("mean") is None:
        return "n/a"
    se = a.get("se")
    return f"{a['mean']:+.4f} (t={a['mean']/se:+.1f})" if se else f"{a['mean']:+.4f}"


def write_report(results: list[dict], path: str) -> None:
    L = []
    L.append("# S4 ETH Maker — Latency / Realism Gate (shadow-build gate)")
    L.append("")
    L.append("**Date:** 2026-08-02 · **Standing:** read-only; on-disk; lab DB only; "
             "evidence only — no verdict.")
    L.append("")
    L.append("The maker-resolution survivor (model side, traded-close rest) re-run "
             "with the two optimisms removed: **(a)** resting level = the PRIOR "
             "minute's traded close (no same-minute-close look-ahead); **(b)** fills "
             "only from the minute AFTER placement; **(c)** the full 1b pessimism "
             "stack (2-tick through + fill 1 tick worse + skip entry min 1-2). "
             "Placement minute p = first-tradeable-minute + delay. **per-ATTEMPT** "
             "$/ct (no-fills@$0) with fill_rate beside it; the optimistic queue-free "
             "fill still stands (the shadow is the live arbiter).")
    L.append("")
    L.append("> **Gate:** if the ETH per-ATTEMPT dies here (≤0 or within ~2 SE), the "
             "maker-shadow would soak a non-edge. Note: realism-only m+0 drops windows "
             "whose first tradeable minute is 1 (no prior-minute close exists) — n is "
             "smaller there by construction.")
    L.append("")
    for res in results:
        a = res["asset"]
        L.append(f"## {a}")
        L.append("")
        for label, _t, _s, _mn in CONFIGS:
            L.append(f"### {label}")
            L.append("")
            L.append("| placement | n attempts | per-ATTEMPT (t) | per-fill (t) | "
                     "fill_rate | filled/unfilled win% |")
            L.append("|---|---|---|---|---|---|")
            for d in PLACE_DELAYS:
                r = res["configs"][label][d]
                L.append(f"| m+{d} | {r['n_attempt']} | {_cell(r['per_attempt'])} | "
                         f"{_cell(r['per_fill'])} | {_pct(r['fill_rate'])} | "
                         f"{_pct(r['filled_win_rate'])}/{_pct(r['unfilled_win_rate'])} |")
            L.append("")
    L.append("## Reading this (evidence, not verdict)")
    L.append("")
    L.append("- **ETH per-ATTEMPT under realism + full pessimism is the gate.** "
             "Positive & |t|≥2 across placement delays ⇒ the survivor tolerates the "
             "realism fixes and the shadow is worth soaking. ≤0 / within noise / "
             "decaying with delay ⇒ it was optimistic-fill or same-minute look-ahead, "
             "and the shadow soaks nothing.")
    L.append("- BTC/SOL/XRP shown as controls (were ~0 / negative in resolution).")
    L.append("- The queue-free fill remains the one optimism this on-disk test cannot "
             "remove — that is exactly what the live shadow measures.")
    L.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"\nReport written: {path}", flush=True)


def main() -> int:
    print("=" * 70)
    print("  ETH MAKER LATENCY/REALISM GATE — kalshi_crypto_v2 (on-disk, no pulls)")
    print("=" * 70)
    args = sys.argv[1:]
    assets = ASSETS
    if "--assets" in args:
        assets = [a.strip().upper() for a in args[args.index("--assets") + 1].split(",")]
    results = [run_asset(a) for a in assets]
    reports_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_S4))), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    path = os.path.join(reports_dir, "2026-08-02_kalshi_crypto_v2_maker_realism_gate.md")
    write_report(results, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
