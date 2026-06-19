"""PA-validation COST TEST (read-only) — are the PA-killed longs winners or losers?

For every score-CLEARED long (real prod population), reconstruct the PriceContext from the
corpus, run the REAL evaluate_pa_validation to label PASS/REJECT, and trade-walk the entry
REGARDLESS of the PA outcome (reusing the engine's build_v2_plan + walk_v2 + net formulas).
Then compare net-of-cost expectancy of the PA-REJECTED cohort vs the PA-PASSED cohort.

  REJECT mean <= PASS mean (and negative)  -> PA correctly filters losing longs (suppression CORRECT)
  REJECT mean >  PASS mean (profitable)    -> PA is killing winning longs (suppression COSTLY)

PA held as the ONLY variable: population is fixed (the real cleared-longs), bars/walk fixed,
only the PA label splits the cohorts. Uses the LIVE pa_validation config (require_all=false,
min 2 of 3) via PAValidationConfig.from_dict — NOT the engine's stricter None-default.

INTERIM: one mostly-NEUTRAL-daily window (May 11 - Jun 19). NOT a cross-regime verdict.

Usage (PYTHONPATH=worktree root):
  python scripts/bull_bottleneck/pa_cost_test.py --cleared-buy data/bull_bottleneck/cleared_buy.csv \
    --bybit-db /abs/data/btc_scalping.db --config config/strategies.yaml --out data/bull_bottleneck/pa_cost_test.json
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))      # scripts/  (for backtest_bitunix_confluence + _btc_accumulator)
sys.path.insert(0, str(_HERE.parents[2]))      # worktree root (for trading_corp.*)

import backtest_bitunix_confluence as E  # noqa: E402
from trading_corp.agents.strategies.bitunix_pa_validation import (  # noqa: E402
    evaluate_pa_validation, PAValidationDecision,
)


def _parse_ts(s: str) -> datetime:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _load_bars(db: str) -> list[dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT ts, open, high, low, close, volume FROM bars_3m ORDER BY ts"
    ).fetchall()
    con.close()
    return [{"ts": datetime.fromtimestamp(int(t), tz=timezone.utc),
             "open": o, "high": h, "low": l, "close": c, "volume": (v or 0.0)}
            for t, o, h, l, c, v in rows]


def _mean(xs):
    return round(statistics.fmean(xs), 4) if xs else None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--cleared-buy", required=True)
    p.add_argument("--bybit-db", required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    raw = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    bx = raw["bitunix_futures"]
    config = E.BitUnixConfluenceConfig.from_dict(bx)
    pa_config = E.PAValidationConfig.from_dict(bx)          # LIVE config (min 2 of 3)
    ctxcfg = E.ctx_config(config)
    print(f"PA config: enabled={pa_config.enabled} require_all={pa_config.require_all} "
          f"min_validators_passed={getattr(pa_config,'min_validators_passed','?')} "
          f"validators={pa_config.validators}")

    bars = _load_bars(args.bybit_db)
    bar_objs = E._bars_to_objs(bars)
    bars_4h = E._resample_to_4h(bars)
    bars_1h = E._resample_to_1h(bars)

    rows = []
    with open(args.cleared_buy, newline="") as f:
        for r in csv.DictReader(f):
            cb = str(r.get("cooldown_blocked", "")).strip().lower()
            if cb in ("1", "true"):            # never reached PA
                continue
            rows.append(r)

    cohorts = {"PASS": [], "REJECT": []}       # net_taker per WALKED trade
    cohorts_mk = {"PASS": [], "REJECT": []}
    cohorts_gr = {"PASS": [], "REJECT": []}
    wins = {"PASS": 0, "REJECT": 0}; walked = {"PASS": 0, "REJECT": 0}
    n_dec = {"PASS": 0, "REJECT": 0, "DISABLED": 0}
    plan_skip = {"PASS": 0, "REJECT": 0}
    opened = {"PASS": 0, "REJECT": 0}
    reject_failcombo = Counter()
    fid_agree = fid_total = 0
    n_ctx_none = 0

    for r in rows:
        ats = _parse_ts(r["ts"])
        # bars are 3m but find_bar_at assumes 60s windows -> snap to the in-force 3m bar
        # (matches the engine's _bar_idx_at containing-bar convention)
        ats = datetime.fromtimestamp((int(ats.timestamp()) // 180) * 180, tz=timezone.utc)
        ctx = E.build_price_context(bars, ats, ctxcfg, bars_4h=bars_4h, bars_1h=bars_1h)
        if ctx is None:
            n_ctx_none += 1
            continue
        pa = evaluate_pa_validation(side="buy", price_ctx=ctx, config=pa_config)
        dec = pa.decision.name if hasattr(pa.decision, "name") else str(pa.decision)
        key = "REJECT" if dec == "REJECT" else "PASS"   # DISABLED/PASS both = "would not block"
        n_dec[dec] = n_dec.get(dec, 0) + 1
        if dec == "REJECT":
            reject_failcombo["+".join(pa.failed)] += 1

        # fidelity vs live: outcome == skipped_pa_validation => live REJECT
        live_reject = (str(r.get("outcome", "")).strip() == "skipped_pa_validation")
        fid_total += 1
        fid_agree += (live_reject == (dec == "REJECT"))

        # trade-walk regardless of PA
        idx = E._bar_idx_at(bar_objs, ats)
        if idx < 0:
            continue
        entry = bar_objs[idx].close
        tp = E.build_v2_plan("buy", entry, bar_objs, idx)
        if tp is None:
            continue
        if not tp.should_trade:
            plan_skip[key] += 1
            continue
        legs = E._plan_to_legs(tp, entry)
        o, gr, amb, fl = E.walk_v2("buy", entry, legs, bar_objs, idx + 1)
        if gr is None:            # open / no_bars
            opened[key] += 1
            continue
        risk = tp.risk_per_unit
        cohorts[key].append(gr - E._RT_TK * entry / risk)
        cohorts_mk[key].append(gr - E._RT_MK * entry / risk)
        cohorts_gr[key].append(gr)
        walked[key] += 1
        if o == "win":
            wins[key] += 1

    def cohort_summary(k):
        return {
            "pa_decisions": n_dec,
            "walked": walked[k], "plan_skipped": plan_skip[k], "still_open": opened[k],
            "win_rate": (round(100.0 * wins[k] / walked[k], 1) if walked[k] else None),
            "mean_gross_r": _mean(cohorts_gr[k]),
            "mean_net_taker_r": _mean(cohorts[k]),
            "mean_net_maker_r": _mean(cohorts_mk[k]),
        }

    out = {
        "n_cleared_buy_after_cooldown_filter": len(rows),
        "n_ctx_none_skipped": n_ctx_none,
        "pa_decision_counts": n_dec,
        "fidelity_vs_live_pa": {
            "agree": fid_agree, "total": fid_total,
            "pct": round(100.0 * fid_agree / max(1, fid_total), 1),
        },
        "PASS": cohort_summary("PASS"),
        "REJECT": cohort_summary("REJECT"),
        "reject_failed_validator_combos": dict(reject_failcombo.most_common()),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")

    print(f"\ncleared-buy (cooldown-filtered): {len(rows)}  ctx_none_skipped={n_ctx_none}")
    print(f"PA decisions: {n_dec}")
    print(f"fidelity vs live PA: {fid_agree}/{fid_total} = {out['fidelity_vs_live_pa']['pct']}%")
    print(f"\n{'cohort':<8} {'walked':>7} {'win%':>6} {'gross/fire':>11} {'net-taker/fire':>15} {'net-maker/fire':>15} {'planskip':>9} {'open':>5}")
    for k in ("PASS", "REJECT"):
        c = cohort_summary(k)
        print(f"{k:<8} {c['walked']:>7} {str(c['win_rate']):>6} {str(c['mean_gross_r']):>11} "
              f"{str(c['mean_net_taker_r']):>15} {str(c['mean_net_maker_r']):>15} "
              f"{c['plan_skipped']:>9} {c['still_open']:>5}")
    print(f"\nREJECT failed-validator combos: {dict(reject_failcombo.most_common())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
