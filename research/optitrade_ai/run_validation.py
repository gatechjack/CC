"""
run_validation.py -- validation pass on the 3 candidate OptiTrade AI cells.

For each candidate (config chosen on Binance):
  (1) Cross-venue: replay the EXACT config on the Bybit corpus (independent OOS --
      config selection never saw Bybit).
  (2) Neighborhood on Binance: same mode, RR2.5 (vs selected 3.5) and the adjacent
      preset (Normal<->VeryHigh). A real edge should degrade gracefully.
  (3) Shuffled-entry permutation null (200 perms): entry TIMES randomized within
      each window, same per-window count, same bracket (independent brackets) ->
      p-value on total net06 and the family-wise multiple-comparisons baseline.

Same 5-equal-window scheme, WARMUP=400, SL=2.5*ATR, sl-first. GROSS primary;
net06/net04 = 0.06%/0.04% per side. Read-only. Writes VALIDATION.md + CSV (LF).
Run: python run_validation.py
"""
import csv, sqlite3, sys, datetime as dt
import numpy as np
sys.path.insert(0, r"C:\Users\AA Incorporado\cc-2026-07-31b-wt\research\optitrade")
import optitrade_bt as bt
import run_study as R
import optitrade_ai_signals as S

DATA = r"C:\Users\AA Incorporado\cc\data"
WARMUP, NWIN, SLMULT = 400, 5, 2.5
FEES = (0.0006, 0.0004)
MIN_N = 30
NPERM = 200
RNG = np.random.default_rng(12345)

CANDS = [
    dict(coin="SOLUSDT", tf="15m", preset="Normal",   mode="reversal",     RR=3.5,
         db="sol_scalping.db", tbl="bars_15m"),
    dict(coin="ETHUSDT", tf="1h",  preset="Normal",   mode="continuation", RR=3.5,
         db="eth_scalping.db", tbl="bars_1h"),
    dict(coin="SOLUSDT", tf="1h",  preset="VeryHigh", mode="reversal",     RR=3.5,
         db="sol_scalping.db", tbl="bars_1h"),
]
ADJ = {"Normal": "VeryHigh", "VeryHigh": "Normal"}

def load_bybit(db, tbl):
    con = sqlite3.connect(f"file:{DATA}\\{db}?mode=ro", uri=True)
    rows = con.execute(f"select ts,open,high,low,close from {tbl} order by ts").fetchall()
    con.close()
    a = np.array(rows, np.float64)
    return (a[:,0].astype(np.int64),
            np.ascontiguousarray(a[:,1]), np.ascontiguousarray(a[:,2]),
            np.ascontiguousarray(a[:,3]), np.ascontiguousarray(a[:,4]))

def perwin(tr, N):
    eidx, gross = tr[0], tr[2]
    net6 = tr[2] - 0.0006*(tr[3]+tr[4])/tr[5]
    net4 = tr[2] - 0.0004*(tr[3]+tr[4])/tr[5]
    win_len = (N - WARMUP)//NWIN
    out = []
    for k in range(NWIN):
        lo = WARMUP + k*win_len
        hi = (WARMUP + (k+1)*win_len) if k < NWIN-1 else N
        m = (eidx >= lo) & (eidx < hi)
        out.append(dict(n=int(m.sum()), gross=float(gross[m].sum()),
                        net06=float(net6[m].sum()), net04=float(net4[m].sum())))
    return out

def agg(pw):
    return dict(tot_n=sum(w["n"] for w in pw),
                tot_gross=sum(w["gross"] for w in pw),
                tot_net06=sum(w["net06"] for w in pw),
                tot_net04=sum(w["net04"] for w in pw),
                gpos=sum(1 for w in pw if w["gross"] > 0),
                n6pos=sum(1 for w in pw if w["net06"] > 0),
                n4pos=sum(1 for w in pw if w["net04"] > 0),
                n30=sum(1 for w in pw if w["n"] >= MIN_N))

def run_config(o,h,l,c,atr,src,hist,preset,mode,macd,RR,N):
    emas = S.build_emas(src, preset)
    sig = S.gen_signals(h,l,c,preset,mode,macd,WARMUP,N,emas=emas,hist=hist)
    tr = bt.simulate(o,h,l,c,atr,sig,SLMULT,RR,WARMUP,N,True)
    return perwin(tr, N), sig

def perm_null(o,h,l,c,atr,sig,RR,N):
    """Shuffled-entry null through the SAME one-position bracket used for the
    headline. Observed = real config's one-position net06 (matches the table).
    Null = same per-window entry counts + direction multiset, random times.
    Returns (observed_total_net06, observed_wins, p_value_total, null_prob_ge4of5)."""
    idx = np.where(sig != 0)[0]
    dirs = sig[idx].astype(np.int8)
    win_len = (N - WARMUP)//NWIN
    edges = [(WARMUP+k*win_len, (WARMUP+(k+1)*win_len) if k < NWIN-1 else N)
             for k in range(NWIN)]
    def eval_sig(s):
        pw = perwin(bt.simulate(o,h,l,c,atr,s,SLMULT,RR,WARMUP,N,True), N)
        return (sum(w["net06"] for w in pw), sum(1 for w in pw if w["net06"] > 0))
    obs_tot, obs_wins = eval_sig(sig)
    tot_ge = 0; wins_ge4 = 0
    for _ in range(NPERM):
        s = np.zeros(N, np.int8)
        for (lo,hi) in edges:
            mm = (idx >= lo) & (idx < hi); k = int(mm.sum())
            if k == 0: continue
            pos = RNG.choice(np.arange(lo, hi-1), size=k, replace=False)
            s[pos] = dirs[mm]
        nt, nw = eval_sig(s)
        if nt >= obs_tot: tot_ge += 1
        if nw >= 4: wins_ge4 += 1
    return obs_tot, obs_wins, tot_ge/NPERM, wins_ge4/NPERM

def dstr(ts, i):
    unit = 1000 if ts[-1] > 10_000_000_000 else 1
    return dt.datetime.fromtimestamp(ts[i]/unit, dt.UTC).strftime("%Y-%m-%d")

def main():
    rows = []; blocks = []; qs = []
    for cd in CANDS:
        coin, tf = cd["coin"], cd["tf"]
        # ---- Binance ----
        tsB,oB,hB,lB,cB = R.load(coin, tf); NB = len(cB)
        atrB = bt.atr_wilder(hB,lB,cB,14); srcB = S.hlc3(hB,lB,cB); histB = S.macd_hist(cB)
        sel_pw, sel_sig = run_config(oB,hB,lB,cB,atrB,srcB,histB,
                                     cd["preset"],cd["mode"],False,cd["RR"],NB)
        rr25_pw,_ = run_config(oB,hB,lB,cB,atrB,srcB,histB,
                               cd["preset"],cd["mode"],False,2.5,NB)
        adj_pw,_  = run_config(oB,hB,lB,cB,atrB,srcB,histB,
                               ADJ[cd["preset"]],cd["mode"],False,cd["RR"],NB)
        # ---- Bybit replay (exact selected config) ----
        tsY,oY,hY,lY,cY = load_bybit(cd["db"], cd["tbl"]); NY = len(cY)
        atrY = bt.atr_wilder(hY,lY,cY,14); srcY = S.hlc3(hY,lY,cY); histY = S.macd_hist(cY)
        byb_pw,_ = run_config(oY,hY,lY,cY,atrY,srcY,histY,
                              cd["preset"],cd["mode"],False,cd["RR"],NY)
        # ---- permutation null (Binance, selected config) ----
        obs_tot, obs_wins, pval, nullq = perm_null(oB,hB,lB,cB,atrB,sel_sig,cd["RR"],NB)
        qs.append(nullq)

        variants = [
            ("Binance-selected", sel_pw, f"{coin} {tf} Binance 2022-07..2026-06"),
            ("Bybit-replay",     byb_pw, f"{coin} {tf} Bybit {dstr(tsY,WARMUP)}..{dstr(tsY,NY-1)}"),
            (f"nbr RR2.5",       rr25_pw, "Binance same preset/mode, RR2.5"),
            (f"nbr {ADJ[cd['preset']]}", adj_pw, f"Binance {ADJ[cd['preset']]}/{cd['mode']}/RR{cd['RR']}"),
        ]
        label = f"{coin} {tf} {cd['preset']}/{cd['mode']}/RR{cd['RR']}"
        blk = dict(label=label, variants=[], perm=dict(obs=obs_tot, wins=obs_wins,
                    pval=pval, nullq=nullq))
        for vname, pw, note in variants:
            a = agg(pw)
            blk["variants"].append((vname, note, pw, a))
            for k,w in enumerate(pw):
                rows.append(dict(cell=f"{coin} {tf}", variant=vname, window=k,
                                 n=w["n"], gross=round(w["gross"],2),
                                 net06=round(w["net06"],2), net04=round(w["net04"],2)))
        blocks.append(blk)
        print(f"  {label:38s} bybit_net06+={agg(byb_pw)['n6pos']}/5 "
              f"perm_p={pval:.3f} nullq={nullq:.2f}")

    # ---- CSV ----
    with open("validation_results.csv","w",newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(rows[0].keys()), lineterminator="\n")
        w.writeheader()
        for r in rows: w.writerow(r)

    render(blocks, qs)
    print("wrote validation_results.csv + VALIDATION.md")

def render(blocks, qs):
    O = []
    O.append("# OptiTrade AI -- validation pass on the 3 candidate cells\n")
    O.append("Cross-venue (Bybit replay of the exact Binance-selected config), Binance "
             "config-neighborhood (RR2.5 + adjacent preset), and a shuffled-entry permutation "
             "null. Bracket SL=2.5*ATR, sl-first, 5 equal windows, WARMUP=400. GROSS primary; "
             "net06/net04 = 0.06%/0.04% per side. Config selection never saw Bybit, so the "
             "Bybit replay is the true out-of-sample arbiter.\n")
    for blk in blocks:
        O.append(f"## {blk['label']}\n")
        O.append("| variant | note | tot n | tot gross | tot net06 | gross+ | net06+ | net04+ | win n>=30 |")
        O.append("|---|---|--:|--:|--:|--:|--:|--:|--:|")
        for vname, note, pw, a in blk["variants"]:
            O.append(f"| {vname} | {note} | {a['tot_n']} | {a['tot_gross']:+.1f} | "
                     f"{a['tot_net06']:+.1f} | {a['gpos']}/5 | {a['n6pos']}/5 | "
                     f"{a['n4pos']}/5 | {a['n30']}/5 |")
        p = blk["perm"]
        O.append(f"\n_Permutation null (200 shuffled-entry perms, SAME one-position bracket, "
                 f"matched per-window counts): observed total net06={p['obs']:+.1f} "
                 f"({p['wins']}/5 windows+); "
                 f"p(null total net06 >= observed) = **{p['pval']:.3f}**; "
                 f"null P(>=4/5 windows net06+) = {p['nullq']:.2f}._\n")
        # summary line (counts only)
        byb = [a for (vn,_,_,a) in blk["variants"] if vn == "Bybit-replay"][0]
        rr25 = [a for (vn,_,_,a) in blk["variants"] if "RR2.5" in vn][0]
        adj = [a for (vn,_,_,a) in blk["variants"] if vn.startswith("nbr ") and "RR2.5" not in vn][0]
        O.append(f"**Summary (counts, no verdict):** venue transfer -> Bybit net06+ "
                 f"{byb['n6pos']}/5 windows, tot net06 {byb['tot_net06']:+.1f} "
                 f"(win n>=30 {byb['n30']}/5); neighborhood -> RR2.5 net06+ {rr25['n6pos']}/5 "
                 f"(tot {rr25['tot_net06']:+.1f}), adjacent-preset net06+ {adj['n6pos']}/5 "
                 f"(tot {adj['tot_net06']:+.1f}); permutation p={p['pval']:.3f}.\n")

    # multiple comparisons
    qavg = float(np.mean(qs))
    exp_cells = 12 * (1 - (1 - qavg)**24)
    exp_faircoin = 12 * (1 - (1 - (6/32))**24)
    O.append("## Multiple-comparisons baseline\n")
    O.append(f"- **Analytic (fair coin).** Per config-cell, P(>=4 of 5 windows net06-positive) "
             f"= C(5,4)+C(5,5) over 2^5 = 6/32 = 0.1875. Because 'best config per cell' is the "
             f"max over 24 configs, P(the cell's best shows >=4/5) ~ 1-(1-0.1875)^24 ~ 0.99, i.e. "
             f"almost all **{exp_faircoin:.0f} of 12** cells would show the sign-pattern by chance. "
             f"The >=4/5 sign-count is therefore NOT informative after best-of-24 selection.")
    O.append(f"- **Empirical (shuffled-entry null).** Mean per-config P(>=4/5 windows net06+) "
             f"across the 3 candidates = {qavg:.2f} (fee drag pulls it below the fair-coin 0.19). "
             f"Best-of-24 selection -> per-cell P ~ 1-(1-{qavg:.2f})^24; expected cells showing "
             f"the pattern under the null ~ **{exp_cells:.0f} of 12**.")
    O.append(f"- **What this means.** The window-sign pattern is expected under the null; the "
             f"discriminating evidence is (a) the permutation p-value on total net06 magnitude "
             f"and (b) whether the effect survives the Bybit venue transfer. Both are reported "
             f"per cell above.")
    O.append("\n## Reproduce\n`python run_validation.py` -> validation_results.csv + this file.")
    open("VALIDATION.md","w",newline="\n").write("\n".join(O)+"\n")

if __name__ == "__main__":
    main()
