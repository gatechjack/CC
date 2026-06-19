"""Fidelity check: does the reconstructed composite (a)=1h/4h/1d regime reproduce the
LIVE htf_gate_decision regime at the same timestamps? Validates the permit-sweep harness
before trusting its (counterintuitive) result. Read-only.

Compares my compute_regime() composite-(a) classification (from Bybit corpus bars) against
the live `regime` field in the prod htf_gate_decision audit events. Expect high-but-not-
perfect agreement (live used BitUnix bars + live funding; I use Bybit + funding=None), with
near-boundary flips. Reports overall + buy-only agreement and a confusion matrix.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))          # scripts/htf_sweep
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))      # worktree root (trading_corp)

from htf_regime_permit_sweep import TF, _load_tf, _resample, _parse_ts, IV  # noqa: E402
from trading_corp.agents.strategies.bitunix_htf_regime import (  # noqa: E402
    HTFContext, HTFRegimeConfig, compute_regime,
)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--htf-gate", required=True)
    p.add_argument("--bybit-db", required=True)
    args = p.parse_args(argv)
    cfg = HTFRegimeConfig.defaults()

    con = sqlite3.connect(f"file:{args.bybit_db}?mode=ro", uri=True)
    tfs = {t: TF(t, _load_tf(con, f"bars_{t}")) for t in ("3m", "15m", "30m", "1h")}
    h1 = _load_tf(con, "bars_1h")
    tfs["4h"] = TF("4h", _resample(h1, IV["4h"]))
    tfs["1d"] = TF("1d", _resample(h1, IV["1d"]))
    con.close()
    px, d1 = tfs["3m"], tfs["1d"]
    s1, s4, sd = "1h", "4h", "1d"   # composite (a)

    n = n_agree = 0
    n_buy = nb_agree = 0
    confusion = Counter()           # (live_regime, my_regime)
    forbid_agree = forbid_total = 0  # buy: live regime_forbids_side vs my long-forbidden
    LONG_FORBID = {"BEAR", "STRONG_BEAR", "SAFE_MODE"}
    with open(args.htf_gate, newline="") as f:
        for r in csv.DictReader(f):
            live_reg = (r.get("regime") or "").strip().upper()
            side = (r.get("score_side") or "").strip().lower()
            if not live_reg:
                continue
            ats = _parse_ts(r["ts"])
            tb1, _ = tfs[s1].as_of(ats)
            tb4, _ = tfs[s4].as_of(ats)
            tbd, _ = tfs[sd].as_of(ats)
            tbpx, _ = px.as_of(ats)
            tbpd, _ = d1.as_of(ats)
            ctx = HTFContext(
                h1=tb1, h4=tb4, d1=tbd,
                current_price=(tbpx.closes[-1] if tbpx else (tb1.closes[-1] if tb1 else 0.0)),
                prior_day_high=(tbpd.highs[-1] if tbpd else None),
                prior_day_low=(tbpd.lows[-1] if tbpd else None),
                funding_rate=None, ts=datetime.fromtimestamp(ats, tz=timezone.utc),
            )
            my_reg = compute_regime(ctx, cfg).regime.value
            n += 1
            n_agree += (my_reg == live_reg)
            confusion[(live_reg, my_reg)] += 1
            if side == "buy":
                n_buy += 1
                nb_agree += (my_reg == live_reg)
                live_forbids = (r.get("hard_zero_reason") or "").strip() == "regime_forbids_side"
                my_forbids = my_reg in LONG_FORBID
                forbid_total += 1
                forbid_agree += (live_forbids == my_forbids)

    print(f"FIDELITY: composite(a)=1h/4h/1d vs LIVE htf_gate regime")
    print(f"  events compared: {n}")
    print(f"  regime agreement: {n_agree}/{n} = {100.0*n_agree/max(1,n):.1f}%")
    print(f"  buy-only regime agreement: {nb_agree}/{n_buy} = {100.0*nb_agree/max(1,n_buy):.1f}%")
    print(f"  buy long-forbidden agreement (live regime_forbids_side vs my BEAR/STRONG_BEAR): "
          f"{forbid_agree}/{forbid_total} = {100.0*forbid_agree/max(1,forbid_total):.1f}%")
    print(f"  confusion (live -> mine), top mismatches:")
    for (lv, mn), c in confusion.most_common():
        tag = "  OK" if lv == mn else "  X"
        print(f"    {lv:>12} -> {mn:<12} {c}{tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
