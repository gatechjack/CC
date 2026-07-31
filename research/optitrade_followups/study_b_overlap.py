"""
STUDY B item 4 -- construct-overlap check (counts only).

Question: is the wide-stop trend-cross an INDEPENDENT return stream, or the same
trend exposure the SFP construct already has? Compares STUDY B trades (Binance 1h)
vs the SFP construct's ARMED stream (RD-trend, construct_rd_trades.csv) over the
common corpus period:
  (a) time-in-market overlap %  (share of STUDY B position-time that coincides
      with a construct position-time, same coin)
  (b) same-day same-coin same-direction collision rate
  (c) correlation of daily R series (per coin + pooled portfolio)

Run AFTER construct_stream_dump.py produces construct_rd_trades.csv.
"""
import sys, csv, os
import numpy as np
sys.path.insert(0, r"C:\Users\AA Incorporado\cc-2026-07-31b-wt\research\optitrade")
import optitrade_bt as bt, run_study as R
import study_b_widestop as B

CONSTRUCT_CSV = "construct_rd_trades.csv"
DAY_MS = 86_400_000

def studyb_trades(coin):
    """Recompute STUDY B trades with timestamps: (entry_ts, exit_ts, side, R, day)."""
    ts, o, h, l, c = R.load(coin, "1h"); N = len(c)
    atr = bt.atr_wilder(h, l, c, 14); sig = B.signals(o, h, l, c, N)
    out = []; i = B.WARMUP
    while i < N:
        d = sig[i]
        if d == 0: i += 1; continue
        a = atr[i]
        if not (a > 0): i += 1; continue
        entry = c[i]; Rd = B.SLMULT * a; SL = entry - d*Rd; TP = entry + d*B.RR*Rd
        j = i + 1; R_ = None; xidx = N - 1
        while j < N:
            hi, lo = h[j], l[j]
            stop = (lo <= SL) if d > 0 else (hi >= SL)
            tp = (hi >= TP) if d > 0 else (lo <= TP)
            if stop: R_ = -1.0; xidx = j; break
            if tp: R_ = B.RR; xidx = j; break
            j += 1
        if R_ is None:
            R_ = ((c[N-1] - entry)/Rd)*d; xidx = N-1
        et = int(ts[i]); xt = int(ts[xidx])
        out.append((et, xt, "long" if d > 0 else "short", float(R_), et - et % DAY_MS))
        i = xidx + 1
    return out

def load_construct():
    rows = list(csv.DictReader(open(CONSTRUCT_CSV)))
    by = {}
    for r in rows:
        by.setdefault(r["coin"], []).append(
            (int(r["entry_ts"]), int(r["exit_ts"]), r["side"], float(r["R"]), int(r["day"])))
    return by

def interval_overlap(a, b):
    """total overlap ms between two lists of (start,end) intervals (each internally sorted, ~disjoint)."""
    a = sorted((s, e) for s, e, *_ in a); b = sorted((s, e) for s, e, *_ in b)
    i = j = 0; ov = 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0]); hi = min(a[i][1], b[j][1])
        if hi > lo: ov += hi - lo
        if a[i][1] < b[j][1]: i += 1
        else: j += 1
    return ov

def inmarket(tr):
    return sum(max(0, e - s) for s, e, *_ in tr)

def daily_series(tr, days):
    d = {day: 0.0 for day in days}
    for _, _, _, R_, day in tr:
        if day in d: d[day] += R_
    return np.array([d[day] for day in days])

def pearson(x, y):
    if x.std() == 0 or y.std() == 0: return None
    return float(np.corrcoef(x, y)[0, 1])

def main():
    if not os.path.exists(CONSTRUCT_CSV):
        print("WAITING: construct_rd_trades.csv not present yet."); return
    con_by = load_construct()
    COINS = ["SOLUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT"]

    rows = []
    O = []
    O.append("# STUDY B item 4 -- construct-overlap check (counts only)\n")
    O.append("STUDY B (wide-stop trend-cross, Binance 1h) vs the SFP construct ARMED stream "
             "(RD-trend gate, the deployed arm), common corpus period. (a) position-time overlap, "
             "(b) same-day same-coin same-direction collisions, (c) daily-R correlation. "
             "Independent stream -> low overlap / low collisions / near-zero correlation.\n")
    O.append("| coin | B trades | C(RD) trades | (a) overlap% of B time | (b) collision% of B | (c) daily-R corr |")
    O.append("|---|--:|--:|--:|--:|--:|")

    all_b_daily = {}; all_c_daily = {}; all_days = set()
    for coin in COINS:
        bt_tr = studyb_trades(coin)
        c_tr = con_by.get(coin, [])
        # common day window
        if bt_tr and c_tr:
            lo = max(min(t[4] for t in bt_tr), min(t[4] for t in c_tr))
            hi = min(max(t[4] for t in bt_tr), max(t[4] for t in c_tr))
        else:
            lo, hi = 0, -1
        bt_c = [t for t in bt_tr if lo <= t[4] <= hi]
        c_c = [t for t in c_tr if lo <= t[4] <= hi]
        # (a)
        ov = interval_overlap(bt_c, c_c); bim = inmarket(bt_c)
        ov_pct = 100.0 * ov / bim if bim else 0.0
        # (b) same-day same-coin same-dir collision
        cset = set((t[4], t[2]) for t in c_c)   # (day, side)
        coll = sum(1 for t in bt_c if (t[4], t[2]) in cset)
        coll_pct = 100.0 * coll / len(bt_c) if bt_c else 0.0
        # (c) daily R corr over union of days in window
        days = sorted(set(t[4] for t in bt_c) | set(t[4] for t in c_c))
        bser = daily_series(bt_c, days); cser = daily_series(c_c, days)
        corr = pearson(bser, cser)
        for day, bv, cv in zip(days, bser, cser):
            all_days.add(day); all_b_daily[day] = all_b_daily.get(day, 0.0) + bv
            all_c_daily[day] = all_c_daily.get(day, 0.0) + cv
        O.append(f"| {coin.replace('USDT','')} | {len(bt_c)} | {len(c_c)} | {ov_pct:.1f}% | "
                 f"{coll_pct:.1f}% | {'n/a' if corr is None else f'{corr:+.2f}'} |")
        rows.append(dict(coin=coin.replace("USDT",""), b_trades=len(bt_c), c_trades=len(c_c),
                         overlap_pct=round(ov_pct,1), collision_pct=round(coll_pct,1),
                         daily_R_corr=("" if corr is None else round(corr,3))))
    # pooled portfolio daily-R corr
    pdays = sorted(all_days)
    pb = np.array([all_b_daily[d] for d in pdays]); pc = np.array([all_c_daily[d] for d in pdays])
    pcorr = pearson(pb, pc)
    O.append(f"\n**Pooled portfolio daily-R correlation (sum across coins per day, n_days={len(pdays)}):** "
             f"{'n/a' if pcorr is None else f'{pcorr:+.3f}'}")
    O.append("\n_Counts only. (a) is % of STUDY B position-time that coincides with a construct "
             "position on the same coin; (b) is % of STUDY B trades sharing a construct trade's "
             "day+direction; (c) is Pearson on aligned daily summed-R.\n")
    O.append("## Reproduce\n`python study_b_overlap.py` -> STUDY_B_OVERLAP.md (needs construct_rd_trades.csv).")

    with open("study_b_overlap_results.csv","w",newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(rows[0].keys()), lineterminator="\n")
        w.writeheader()
        for r in rows: w.writerow(r)
    open("STUDY_B_OVERLAP.md","w",newline="\n").write("\n".join(O)+"\n")
    print("\n".join(O))

if __name__ == "__main__":
    main()
