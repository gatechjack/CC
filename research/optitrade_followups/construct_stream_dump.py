"""
construct_stream_dump.py -- dump the SFP construct's ARMED trade stream (the
live-deployed RD-trend gate) with timestamps, for STUDY B's overlap check.

Reuses the frozen base_candidates + walk_r@3R + RD-trend arm + one-position
booking, exactly as STUDY A / the tiebreaker. Per booked trade writes:
coin, side, entry_ts, exit_ts, R, day. Binance-perp corpus, read-only.
Output: construct_rd_trades.csv  (armed = RD-trend, the deployed gate; n should be 634).
"""
import sys, sqlite3, csv, time
SFP_DIR = r"C:\Users\AA Incorporado\cc\trading_corp\agents\strategies"
HERE = r"C:\Users\AA Incorporado\Desktop\backtest_corpus"
for p in (SFP_DIR, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
import _sfp_causal_macro60 as M
import _sfp_head_to_head as H2H
import _sfp_degree_rerun as DR
import _inst_levels as IL
import _sfp_trend_gate_bakeoff as BK

OUT = r"C:\Users\AA Incorporado\cc-2026-07-31b-wt\research\optitrade_followups\construct_rd_trades.csv"
ARM = "RD-trend"

def main():
    t0 = time.time()
    con = sqlite3.connect(M.DB)
    m60 = {(s, d): r for s, d, r in con.execute("SELECT symbol,day_ts_ms,regime FROM macro_regime_60d")}
    rows = []
    for coin in M.COINS:
        tc = time.time()
        b15 = DR.load_bars(con, coin, "15m"); b3 = DR.load_bars(con, coin, "3m")
        b1d = DR.load_bars(con, coin, "1d"); b1h = DR.load_bars(con, coin, "1h")
        labels, cts = DR.precompute_regime(b15)
        il = IL.InstLevels(coin, b15, b1d)
        rd_lookup = BK.rd_os_lookup_builder(b1h)
        cands, ng = M.base_candidates(coin, b1h, M.TF_1H, b3, labels, cts, il, m60, rd_lookup)
        for c in cands:
            R, ex = H2H.walk_r(b3, c["ei"], c["entry"], c["stop"], c["r_unit"],
                               c["entry_ts"], c["side"], 3.0)
            c["_R"], c["_exit_ts"] = R, ex
        sub = [c for c in cands if M.arm_passes(ARM, c, {})]   # RD uses candidate rd_os (no gate_maps needed)
        bk, opp = M.book_and_pool_cached(sub)
        for t in bk:
            rows.append(dict(coin=coin, side=t["side"], entry_ts=t["entry_ts"],
                             exit_ts=t["_exit_ts"], R=round(t["R"], 6),
                             day=t["entry_ts"] - t["entry_ts"] % M.DAY_MS))
        print(f"  [{coin}] RD-armed booked={len(bk)} ({time.time()-tc:.0f}s)", flush=True)
        del b15, b1d, il, labels, cts
    con.close()
    with open(OUT, "w", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=["coin", "side", "entry_ts", "exit_ts", "R", "day"],
                           lineterminator="\n")
        w.writeheader()
        for r in rows: w.writerow(r)
    print(f"\n  wrote {len(rows)} RD-armed construct trades -> {OUT}  ({time.time()-t0:.0f}s)")

if __name__ == "__main__":
    main()
