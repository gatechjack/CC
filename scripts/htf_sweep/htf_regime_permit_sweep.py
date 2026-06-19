"""HTF-regime PERMIT sweep — read-only analysis tooling (nothing applied to prod).

Tests the hypothesis: the regime gate (1d/4h/1h) is too high-TF for a 3m scalper,
over-suppressing intraday counter-daily-trend longs. We REUSE the tested classifier
(compute_regime / get_trade_permissions from bitunix_htf_regime.py) and only swap the
THREE timeframe slots fed into HTFContext, recomputing how many score-cleared bull/bear
entries each composite would PERMIT.

PA is held FIXED (not re-applied): the population is the real prod score-CLEARED events
(audit_event bitunix_score_decided, tier in STANDARD/PREMIUM). We vary ONLY the HTF
composite, so the regime contribution is isolated. (PA's own kill rate is reported once
elsewhere, from the diagnostic — not varied here.)

Composites swept (slot mapping low->high = h1/h4/d1):
  (a) current : 1h / 4h / 1d        <- must reproduce the known "bull ~0" result (fidelity check)
  (b)         : 30m / 1h / 4h
  (c)         : 15m / 30m / 1h
  (d)         : 3m  / 15m / 1h

Primary metric = REGIME-level permit (_matrix_base allow+mult>0) — pure regime effect,
independent of current_price/levels/funding. Secondary = full get_trade_permissions
(layers proximity/vol/funding hard-zeros) for realism, with the block-reason breakdown.

Inputs are all corpus-reconstructable (1h->4h/1d resample; lower TFs from the corpus
tables; funding=None is safe). NO network, NO prod write, NO scoring/regime config change.

Usage (PYTHONPATH must include the worktree root so `trading_corp` imports):
  python scripts/htf_sweep/htf_regime_permit_sweep.py \
    --score-events data/htf_sweep/score_decided.csv \
    --bybit-db /abs/data/btc_scalping.db --out data/htf_sweep/permit_sweep_summary.json
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from trading_corp.agents.strategies.bitunix_htf_regime import (  # noqa: E402
    HTFContext, HTFRegimeConfig, Regime, TimeframeBars,
    _matrix_base, compute_regime, get_trade_permissions,
)

IV = {"3m": 180, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}
LOOKBACK = 250          # closed bars per slot (>=200 for EMA200)
LONG_OK_REGIMES = {Regime.STRONG_BULL, Regime.BULL, Regime.NEUTRAL}
SHORT_OK_REGIMES = {Regime.STRONG_BEAR, Regime.BEAR, Regime.NEUTRAL}

# composite -> (h1_slot_tf, h4_slot_tf, d1_slot_tf)
COMPOSITES = {
    "a_1h_4h_1d":  ("1h", "4h", "1d"),
    "b_30m_1h_4h": ("30m", "1h", "4h"),
    "c_15m_30m_1h": ("15m", "30m", "1h"),
    "d_3m_15m_1h": ("3m", "15m", "1h"),
}


def _parse_ts(s: str) -> int:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    return int(dt.timestamp())


def _load_tf(con, table) -> list[tuple]:
    return con.execute(
        f"SELECT ts, open, high, low, close, volume FROM {table} ORDER BY ts"
    ).fetchall()


def _resample(base_rows: list[tuple], target_iv: int) -> list[tuple]:
    """Aggregate base bars into target_iv buckets (UTC-aligned via epoch floor)."""
    agg: dict[int, list] = {}
    order: list[int] = []
    for ts, o, h, l, c, v in base_rows:
        b = (int(ts) // target_iv) * target_iv
        cur = agg.get(b)
        if cur is None:
            agg[b] = [o, h, l, c, v or 0.0]
            order.append(b)
        else:
            cur[1] = max(cur[1], h)
            cur[2] = min(cur[2], l)
            cur[3] = c
            cur[4] += (v or 0.0)
    order.sort()
    return [(b, agg[b][0], agg[b][1], agg[b][2], agg[b][3], agg[b][4]) for b in order]


class TF:
    """One timeframe's bars + as-of slicing."""
    def __init__(self, label: str, rows: list[tuple]):
        self.label = label
        self.iv = IV[label]
        self.opens = [int(r[0]) for r in rows]
        self.rows = rows

    def as_of(self, alert_ts: int):
        """Return a TimeframeBars of the last LOOKBACK CLOSED bars <= alert_ts, or None."""
        # a bar (open ot) is closed iff ot + iv <= alert_ts  <=>  ot <= alert_ts - iv
        idx = bisect_right(self.opens, alert_ts - self.iv) - 1
        if idx < 0:
            return None, None
        lo = max(0, idx - LOOKBACK + 1)
        sl = self.rows[lo:idx + 1]
        last_open = int(sl[-1][0])
        tb = TimeframeBars(
            timeframe=self.label,
            opens=tuple(r[1] for r in sl),
            highs=tuple(r[2] for r in sl),
            lows=tuple(r[3] for r in sl),
            closes=tuple(r[4] for r in sl),
            volumes=tuple((r[5] or 0.0) for r in sl),
            last_bar_close_ts=datetime.fromtimestamp(last_open + self.iv, tz=timezone.utc),
        )
        return tb, last_open


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--score-events", required=True, help="CSV: ts,side,tier,net_score,outcome")
    p.add_argument("--bybit-db", required=True)
    p.add_argument("--out", required=True, help="JSON summary output path")
    args = p.parse_args(argv)

    cfg = HTFRegimeConfig.defaults()  # default weights {d1:.5,h4:.3,h1:.2}, default thresholds

    con = sqlite3.connect(f"file:{args.bybit_db}?mode=ro", uri=True)
    tfs: dict[str, TF] = {}
    for t in ("3m", "15m", "30m", "1h"):
        tfs[t] = TF(t, _load_tf(con, f"bars_{t}"))
    h1_rows = _load_tf(con, "bars_1h")
    tfs["4h"] = TF("4h", _resample(h1_rows, IV["4h"]))
    tfs["1d"] = TF("1d", _resample(h1_rows, IV["1d"]))
    con.close()
    px = tfs["3m"]  # current_price + prior-day source
    d1 = tfs["1d"]

    # score-CLEARED events (PA held fixed: we take the real cleared population, vary only HTF)
    events = []
    with open(args.score_events, newline="") as f:
        for r in csv.DictReader(f):
            side = (r.get("side") or "").strip().lower()
            tier = (r.get("tier") or "").strip().upper()
            if side not in ("buy", "sell"):
                continue
            if tier not in ("STANDARD", "PREMIUM"):   # cleared the score gate only
                continue
            events.append((_parse_ts(r["ts"]), side))
    events.sort()

    n_buy = sum(1 for _, s in events if s == "buy")
    n_sell = len(events) - n_buy

    # per-composite verdict cache keyed on (h1_open,h4_open,d1_open)
    summary = {}
    for comp, (s1, s4, sd) in COMPOSITES.items():
        cache: dict[tuple, object] = {}
        regime_dist = Counter()
        permit = {"buy": {"regime": 0, "full": 0, "blocks": Counter(), "total": n_buy},
                  "sell": {"regime": 0, "full": 0, "blocks": Counter(), "total": n_sell}}
        long_allowed_events = 0  # regime allows long (NEUTRAL-or-better) over ALL cleared events
        insufficient = 0
        for ats, side in events:
            tb1, o1 = tfs[s1].as_of(ats)
            tb4, o4 = tfs[s4].as_of(ats)
            tbd, od = tfs[sd].as_of(ats)
            key = (o1, o4, od)
            verdict = cache.get(key)
            if verdict is None:
                tb_px, _ = px.as_of(ats)
                cur_price = tb_px.closes[-1] if tb_px else (tb1.closes[-1] if tb1 else 0.0)
                tbd_pd, _ = d1.as_of(ats)
                pdh = tbd_pd.highs[-1] if tbd_pd else None
                pdl = tbd_pd.lows[-1] if tbd_pd else None
                ctx = HTFContext(
                    h1=tb1, h4=tb4, d1=tbd, current_price=cur_price,
                    prior_day_high=pdh, prior_day_low=pdl,
                    funding_rate=None, ts=datetime.fromtimestamp(ats, tz=timezone.utc),
                )
                verdict = compute_regime(ctx, cfg)
                cache[key] = verdict
            regime_dist[verdict.regime.value] += 1
            if verdict.regime == Regime.SAFE_MODE:
                insufficient += 1
            if verdict.regime in LONG_OK_REGIMES:
                long_allowed_events += 1
            # pure regime permit (matrix base; independent of price/levels)
            al, asho, mlong, mshort, _ = _matrix_base(verdict.regime, verdict.h1.regime)
            regime_ok = (al and mlong > 0) if side == "buy" else (asho and mshort > 0)
            if regime_ok:
                permit[side]["regime"] += 1
            # full permit (with proximity/vol/funding hard-zeros)
            tp = get_trade_permissions(verdict, side, cfg)
            if tp.size_multiplier > 0:
                permit[side]["full"] += 1
            else:
                permit[side]["blocks"][tp.hard_zero_reason or "?"] += 1
        summary[comp] = {
            "slots_low_to_high": [s1, s4, sd],
            "regime_dist": dict(regime_dist),
            "neutral_or_better_for_long_pct": round(100.0 * long_allowed_events / max(1, len(events)), 1),
            "buy": {"total": n_buy, "regime_permitted": permit["buy"]["regime"],
                    "full_permitted": permit["buy"]["full"], "blocks": dict(permit["buy"]["blocks"])},
            "sell": {"total": n_sell, "regime_permitted": permit["sell"]["regime"],
                     "full_permitted": permit["sell"]["full"], "blocks": dict(permit["sell"]["blocks"])},
        }

    out = {"n_score_cleared_buy": n_buy, "n_score_cleared_sell": n_sell,
           "window": [datetime.fromtimestamp(events[0][0], tz=timezone.utc).isoformat(),
                      datetime.fromtimestamp(events[-1][0], tz=timezone.utc).isoformat()],
           "composites": summary}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")

    # human summary
    print(f"score-cleared population: buy={n_buy}  sell={n_sell}  "
          f"window {out['window'][0]} -> {out['window'][1]}")
    print(f"{'composite':<14} {'slots(lo>hi)':<16} {'L-permit(regime/full)':<22} "
          f"{'S-permit(regime/full)':<22} {'NEUTRAL+forLong%':<16} bull:bear(regime)")
    for comp, d in summary.items():
        b, s = d["buy"], d["sell"]
        ratio = f"{b['regime_permitted']}:{s['regime_permitted']}"
        print(f"{comp:<14} {'/'.join(d['slots_low_to_high']):<16} "
              f"{str(b['regime_permitted'])+'/'+str(b['full_permitted']):<22} "
              f"{str(s['regime_permitted'])+'/'+str(s['full_permitted']):<22} "
              f"{d['neutral_or_better_for_long_pct']:<16} {ratio}")
    print(f"\nregime distribution per composite:")
    for comp, d in summary.items():
        print(f"  {comp}: {d['regime_dist']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
