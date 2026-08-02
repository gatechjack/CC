"""MAKER RESOLUTION MINI-STUDY (2026-08-02, on-disk, NO pulls). READ-ONLY.

The EV forensic left ONE thing unruled: the maker per-ATTEMPT positive on BTC
(~+0.04) and ETH (~+0.07) under the approved traded-close resting level. This
study resolves it cheaply, per the operator's spec:

  1a NULL CONTROLS at the SAME resting levels (entry-minute traded close):
     side rules = {model, random, always-YES, always-NO}. If the controls earn
     ~= the model's maker EV, the positive is SIGNAL-INDEPENDENT SPREAD CAPTURE
     (label it so), not the SFP/model signal. (random side = deterministic per
     market_ticker md5 parity -> reproducible, no RNG.)

  1b PESSIMISM SENSITIVITIES (model side), the EV surface across:
     - baseline (through 1 tick, no offset, entry from min 1);
     - require trade-through by 2 TICKS;
     - fill 1 TICK WORSE than the traded close (a pure fill-PRICE slippage haircut
       on the bet's own side; the resting level / fill condition are unchanged, so
       it strictly lowers EV -- NOT a resting-level shift, which would just fill
       more and capture the ~100%-winner unfilled tail);
     - SKIP entry minutes 1-2 (enter no earlier than minute 3);
     - ALL combined (the most pessimistic).

  1c the SAME three adverse-selection views (fill-timing + early/late P&L;
     filled-vs-unfilled would-have-won; per-ATTEMPT EV) on every variant.

Realized P&L/contract = (1 if win else 0) - fill_price - kalshi_fee, per the
forensic. Maker fill = a later REAL trade prints THROUGH the resting level by
>= `through` ticks (optimistic: no queue/partial). Reuses the forensic's
train/split/calibrate so model_p == the Brier-tested holdout. NO order surface.
"""
from __future__ import annotations

import hashlib
import math
import os
import sys

_S4 = os.path.dirname(os.path.abspath(__file__))
if _S4 not in sys.path:
    sys.path.insert(0, _S4)

from ev_forensic import (  # noqa: E402  (reuse the forensic building blocks)
    ASSETS, TICK, reproduce_holdout, load_candles_ohlc, _traded, realized,
    model_ev, _new_macc, _finalize_maker, _valid_price, LAB_DB,
)

# side rule -> label; pessimism configs (label, through_ticks, rest_offset, min_min)
CONTROLS = [("model", "model side"), ("random", "random side"),
            ("yes", "always-YES"), ("no", "always-NO")]
PESSIMISM = [
    ("baseline", 1, 0, 1),
    ("through 2 ticks", 2, 0, 1),
    ("fill 1 tick worse", 1, 1, 1),
    ("skip entry min 1-2", 1, 0, 3),
    ("ALL combined", 2, 1, 3),
]


def _side(rule: str, p: float, ticker: str) -> str:
    if rule == "model":
        return "yes" if p >= 0.5 else "no"
    if rule == "yes":
        return "yes"
    if rule == "no":
        return "no"
    # deterministic pseudo-random per market (reproducible; no RNG)
    h = int(hashlib.md5(ticker.encode()).hexdigest(), 16)
    return "yes" if (h & 1) == 0 else "no"


def _entry(cands, ot: int, ct: int, variant: str, min_min: float):
    inwin = sorted((c for c in cands
                    if ot < c["ts"] < ct and _traded(c) and (c["ts"] - ot) / 60.0 >= min_min),
                   key=lambda c: c["ts"])
    if variant == "A":
        return inwin[0] if inwin else None
    return inwin[1] if len(inwin) > 1 else None


def _maker_once(side: str, ec: dict, later: list[dict], through: int, slippage: int):
    """(filled, fill_price, fill_ts). Resting level = entry-minute TRADED CLOSE
    (unchanged); fill on a real >= `through`-tick trade-through; the realized fill
    PRICE is `slippage` ticks WORSE on the bet's own side (a pure pessimism
    haircut that does NOT change the fill condition -- so it strictly lowers EV,
    unlike shifting the resting level, which would just fill more). None if
    unusable."""
    pc = ec.get("price_close")
    if pc is None:
        return None
    if side == "yes":
        fp = pc + slippage * TICK                 # pay 1 tick more per fill
        if not (_valid_price(pc) and _valid_price(fp)):
            return None
        thr = pc - through * TICK
        for c in later:
            if (c.get("volume") or 0) > 0 and c.get("price_low") is not None and c["price_low"] <= thr:
                return True, fp, c["ts"]
        return False, fp, None
    n = 1.0 - pc
    fp = n + slippage * TICK                       # NO cost 1 tick more per fill
    if not (_valid_price(n) and _valid_price(fp)):
        return None
    thr = pc + through * TICK
    for c in later:
        if (c.get("volume") or 0) > 0 and c.get("price_high") is not None and c["price_high"] >= thr:
            return True, fp, c["ts"]
    return False, fp, None


def eval_config(df_holdout, model_p, cand, variant, rule, through, slippage, min_min) -> dict:
    m = _new_macc()
    for i, row in df_holdout.reset_index(drop=True).iterrows():
        cs = cand.get(row["market_ticker"]) or []
        if not cs:
            continue
        ot, ct = int(row["open_ts"]), int(row["close_ts"])
        ec = _entry(cs, ot, ct, variant, min_min)
        if ec is None:
            continue
        p = float(model_p[i])
        side = _side(rule, p, row["market_ticker"])
        y = int(row["y"])
        won = (y == 1) if side == "yes" else (y == 0)
        later = [c for c in cs if ec["ts"] < c["ts"] <= ct]
        res = _maker_once(side, ec, later, through, slippage)
        if res is None:
            continue
        filled, fprice, fts = res
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
            m["per_attempt"].append(0.0)
            m["unfill_won"].append(1 if won else 0)
    return _finalize_maker(m)


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------

def _c(a: dict) -> str:
    if not a or a.get("mean") is None:
        return "n/a"
    se = a.get("se")
    return f"{a['mean']:+.4f} (t={a['mean']/se:+.1f})" if se else f"{a['mean']:+.4f}"


def _pct(x): return "n/a" if x is None else f"{x*100:.1f}%"


def run_asset(asset: str) -> dict:
    print(f"\n{'='*60}\n  MAKER RESOLUTION: {asset}\n{'='*60}", flush=True)
    df, mp = reproduce_holdout(asset)
    cand = load_candles_ohlc(df["market_ticker"].tolist(), LAB_DB)
    out = {"asset": asset, "controls": {}, "pessimism": {}}
    # 1a null controls (baseline resting level), both variants
    for v in ("A", "B"):
        out["controls"][v] = {}
        for rule, _lbl in CONTROLS:
            out["controls"][v][rule] = eval_config(df, mp, cand, v, rule, 1, 0, 1)
    # 1b/1c pessimism surface (model side), both variants + the three views
    for v in ("A", "B"):
        out["pessimism"][v] = {}
        for label, thru, off, mn in PESSIMISM:
            out["pessimism"][v][label] = eval_config(df, mp, cand, v, "model", thru, off, mn)
    # console snapshot
    for v in ("A", "B"):
        mo = out["controls"][v]["model"]["per_attempt"]
        rn = out["controls"][v]["random"]["per_attempt"]
        ys = out["controls"][v]["yes"]["per_attempt"]
        no = out["controls"][v]["no"]["per_attempt"]
        print(f"  [{v}] 1a per-attempt: model={_c(mo)} random={_c(rn)} "
              f"yes={_c(ys)} no={_c(no)}", flush=True)
        allc = out["pessimism"][v]["ALL combined"]
        base = out["pessimism"][v]["baseline"]
        print(f"       1b model: baseline={_c(base['per_attempt'])} "
              f"ALL-pessimism={_c(allc['per_attempt'])} "
              f"(fill {_pct(base['fill_rate'])}->{_pct(allc['fill_rate'])})", flush=True)
    return out


def write_report(results: list[dict], path: str) -> None:
    L = []
    L.append("# S4 EV Forensic — Maker Resolution Mini-Study")
    L.append("")
    L.append("**Date:** 2026-08-02  ")
    L.append("**Question:** is the maker per-ATTEMPT positive (BTC ~+0.04, ETH ~+0.07 "
             "at traded-close rest) a real executable edge, or signal-independent "
             "spread capture riding an optimistic fill?  ")
    L.append("**Standing:** read-only; on-disk (no pulls); lab DB only; evidence only "
             "— no verdict.")
    L.append("")
    L.append("All numbers are maker **per-ATTEMPT** $/contract (fills@realized, "
             "no-fills@$0) unless noted; t = mean/SE (|t|<~2 ~ zero). Resting level = "
             "entry-minute TRADED CLOSE. Fill = a later REAL trade prints THROUGH by "
             ">= `through` ticks (OPTIMISTIC: no queue/partial fills).")
    L.append("")
    for res in results:
        a = res["asset"]
        L.append(f"## {a}")
        L.append("")
        # 1a null controls
        L.append("### 1a — Null controls (same traded-close rest, baseline fill)")
        L.append("")
        L.append("| Variant | model side | random side | always-YES | always-NO | "
                 "model − mean(controls) |")
        L.append("|---|---|---|---|---|---|")
        for v in ("A", "B"):
            cc = res["controls"][v]
            mo = cc["model"]["per_attempt"]["mean"]
            ctrl = [cc["random"]["per_attempt"]["mean"], cc["yes"]["per_attempt"]["mean"],
                    cc["no"]["per_attempt"]["mean"]]
            ctrl = [x for x in ctrl if x is not None]
            gap = (mo - sum(ctrl) / len(ctrl)) if (mo is not None and ctrl) else None
            L.append(f"| {v} | {_c(cc['model']['per_attempt'])} | "
                     f"{_c(cc['random']['per_attempt'])} | {_c(cc['yes']['per_attempt'])} | "
                     f"{_c(cc['no']['per_attempt'])} | "
                     f"{('%+.4f' % gap) if gap is not None else 'n/a'} |")
        L.append("")
        L.append("_If the controls earn ~= model side, the positive is signal-INDEPENDENT "
                 "spread capture (the model's side choice adds ~nothing over always-YES / "
                 "always-NO / random)._")
        L.append("")
        # 1b pessimism surface + 1c views
        L.append("### 1b/1c — Pessimism surface (model side) + adverse-selection views")
        L.append("")
        L.append("| Variant | Config | per-ATTEMPT (t) | fill_rate | filled/unfilled win% "
                 "| median fill-min | late-half P&L |")
        L.append("|---|---|---|---|---|---|---|")
        for v in ("A", "B"):
            for label, _t, _o, _m in PESSIMISM:
                r = res["pessimism"][v][label]
                tm = r["fill_timing"]
                med = f"{tm['median']:.1f}" if tm else "n/a"
                late = r["pnl_late"]
                late_s = (f"{late['mean']:+.4f} (n={late['n']})"
                          if late and late.get("mean") is not None else f"n/a (n=0)")
                L.append(f"| {v} | {label} | {_c(r['per_attempt'])} | "
                         f"{_pct(r['fill_rate'])} | "
                         f"{_pct(r['filled_win_rate'])}/{_pct(r['unfilled_win_rate'])} | "
                         f"{med} | {late_s} |")
        L.append("")
    L.append("## Reading this (evidence, not verdict)")
    L.append("")
    L.append("- **1a:** model ≈ controls ⇒ spread capture (signal-independent); model ≫ "
             "controls ⇒ a directional-signal component. always-YES vs always-NO also "
             "shows any structural long/short bias in the fill model.")
    L.append("- **1b:** the pessimism knobs each remove a slice of the optimism. If the "
             "per-ATTEMPT positive collapses to ~0 (within ~2 SE) under 2-tick / "
             "rest-worse / skip-1-2 / ALL, it did not survive executable frictions.")
    L.append("- **1c:** unfilled ~100% winners persists ⇒ the fill model keeps missing "
             "the winners; per-ATTEMPT already books those at $0, so it is the honest "
             "number to judge.")
    L.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"\nReport written: {path}", flush=True)


def main() -> int:
    print("=" * 70)
    print("  MAKER RESOLUTION MINI-STUDY — kalshi_crypto_v2 (on-disk, no pulls)")
    print("=" * 70)
    args = sys.argv[1:]
    assets = ASSETS
    if "--assets" in args:
        assets = [a.strip().upper() for a in args[args.index("--assets") + 1].split(",")]
    results = [run_asset(a) for a in assets]
    reports_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_S4))), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    path = os.path.join(reports_dir, "2026-08-02_kalshi_crypto_v2_EV_forensic_maker_resolution.md")
    write_report(results, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
