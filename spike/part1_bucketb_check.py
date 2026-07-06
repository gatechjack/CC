"""PART 1 - Bucket-B direct categorical check (read-only, GROSS).

For each forensic Bucket-B setup x pivot_len in {5,10,20,50}, drive the CERTIFIED
SfpModeBDetector (byte-identical, md5 91fd7672) on prod Bitunix 3m->15m data and
report, for LONG and SHORT (M2=0 reflection = prod), whether the detector fires:
  sweep threshold (bar.low<swing / bar.high>swing), reclaim (close vs swing),
  BOS confirm (3m). If any check fails, show the numbers vs the threshold.
NOT a statistical run - categorical evidence only.
"""
from __future__ import annotations
import datetime as dt
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from bitunix_sfp import SfpBar, SfpModeBDetector, MODE_REAL
import backtest as bt

PIVOTS = [5, 10, 20, 50]
DATA = os.path.join(os.path.dirname(__file__), "data")

def load_prod_3m(coin):
    bars = []
    with open(os.path.join(DATA, f"prod_{coin}_3m.csv")) as f:
        for row in f:
            p = row.strip().split(",")
            if len(p) < 5 or not p[0].isdigit():
                continue
            bars.append(SfpBar(int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4])))
    return bars

def reflect_neg(bars):
    # M2=0 (prod): reflected=-real, high/low swap. refl.high=-low, refl.low=-high.
    return [SfpBar(b.ts_ms, -b.open, -b.low, -b.high, -b.close) for b in bars]

def ts_ms(y, mo, d, h, mi):
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone.utc).timestamp() * 1000)

# (label, coin, 15m-bar ts_ms)
SETUPS = [
    ("BTC 07-02 13:15", "BTCUSDT", ts_ms(2026, 7, 2, 13, 15)),
    ("SOL 06-29 17:15", "SOLUSDT", ts_ms(2026, 6, 29, 17, 15)),
    ("SOL 06-28 19:45", "SOLUSDT", ts_ms(2026, 6, 28, 19, 45)),
]

def run_side(bars15, bars3, pivot_len, setup_ts):
    """Return dict: swing at setup bar, permit, armed@bar, confirmed, and the
    fire nearest to setup_ts (within +/-2 15m bars)."""
    det = SfpModeBDetector(mode=MODE_REAL, pivot_len=pivot_len)
    swing_at = {}      # 15m ts -> (swing_low_of_fire_engine, permit)
    armed = {}         # fired_bar_ts_ms -> True
    for b in bars15:
        det.on_closed_15m_bar(b)
        swing_at[b.ts_ms] = (det._fire._swing_low, det._fire._permit)
        for t in det.drain_transitions():
            if t.status == "ARMED":
                armed[int(t.fired_bar_ts_ms)] = True
    confirmed = {}     # fired_bar_ts_ms -> True
    for b in bars3:
        for _sig in det.on_closed_3m_bar(b):
            pass
        for t in det.drain_transitions():
            if t.status == "CONFIRMED":
                confirmed[int(t.fired_bar_ts_ms)] = True
    return swing_at, armed, confirmed

def main():
    for label, coin, s_ts in SETUPS:
        bars3 = load_prod_3m(coin)
        bars15 = bt.resample_15m(bars3)
        by15 = {b.ts_ms: b for b in bars15}
        print(f"\n{'='*74}\nSETUP: {label}  (15m bar ts_ms={s_ts})")
        bar = by15.get(s_ts)
        if bar is None:
            print(f"  !! setup 15m bar MISSING from resampled data (gap) - cannot trace")
            continue
        print(f"  REAL 15m bar: O={bar.open} H={bar.high} L={bar.low} C={bar.close}")
        rbars3 = reflect_neg(bars3)
        rbars15 = bt.resample_15m(rbars3)
        fires = []  # (pivot_len, side)
        for pl in PIVOTS:
            sa_l, arm_l, cf_l = run_side(bars15, bars3, pl, s_ts)
            sw_l, pm_l = sa_l.get(s_ts, (None, None))
            sa_s, arm_s, cf_s = run_side(rbars15, rbars3, pl, s_ts)
            swr, pm_s = sa_s.get(s_ts, (None, None))
            sw_s = (-swr) if swr is not None else None   # real swing_high = -reflected_swing_low
            def yn(x): return "YES" if x else "no "
            # LONG checks
            l_sweep = (sw_l is not None) and (bar.low < sw_l)
            l_recl  = (sw_l is not None) and (bar.close > sw_l)
            l_arm, l_cf = arm_l.get(s_ts, False), cf_l.get(s_ts, False)
            # SHORT checks
            s_sweep = (sw_s is not None) and (bar.high > sw_s)
            s_recl  = (sw_s is not None) and (bar.close < sw_s)
            s_arm, s_cf = arm_s.get(s_ts, False), cf_s.get(s_ts, False)
            swl = f"{sw_l:.4f}" if sw_l is not None else "None"
            sws = f"{sw_s:.4f}" if sw_s is not None else "None"
            print(f"  pivot_len={pl:2d}")
            print(f"    LONG : swing_low={swl} permit={pm_l} | sweep(L<sw)={yn(l_sweep)}"
                  f"(L={bar.low} vs {swl}) reclaim(C>sw)={yn(l_recl)} | ARM@bar={yn(l_arm)} BOS={yn(l_cf)}")
            print(f"    SHORT: swing_hi ={sws} permit={pm_s} | sweep(H>sw)={yn(s_sweep)}"
                  f"(H={bar.high} vs {sws}) reclaim(C<sw)={yn(s_recl)} | ARM@bar={yn(s_arm)} BOS={yn(s_cf)}")
            if l_arm and l_cf: fires.append((pl, "LONG"))
            if s_arm and s_cf: fires.append((pl, "SHORT"))
        if fires:
            sm = min(f[0] for f in fires)
            print(f"  >> VERDICT: detector FIRES (arm+BOS) at: {fires}; smallest pivot_len={sm}")
        else:
            print(f"  >> VERDICT: detector does NOT fire (arm+BOS) at ANY pivot_len in {PIVOTS}")

if __name__ == "__main__":
    main()
