"""mk_report.py -- render RESULTS.md from results_full.csv + results_vendor.json."""
import csv, json

rows = list(csv.DictReader(open("results_full.csv")))
vendor = json.load(open("results_vendor.json"))
TFS = ["3m","15m","1h","4h","1d"]; COINS=["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT"]

def f(x, nd=2):
    if x in ("", "None", None): return "-"
    try:
        v=float(x)
        if v==float("inf"): return "inf"
        return f"{v:.{nd}f}"
    except: return str(x)
def pct(x):
    if x in ("","None",None): return "-"
    return f"{float(x)*100:.1f}%"
def order(r): return (COINS.index(r["coin"]), TFS.index(r["tf"]))

def cfg_table(cfg):
    rs=sorted([r for r in rows if r["config"]==cfg], key=order)
    L=[]
    L.append("| coin | tf | L/sl/RR/bias | IS n | IS sumR | IS PF | OOS n | OOS WR | OOS avgR | OOS sumR(gross) | OOS PF | OOS maxDD | OOS net06 | OOS net04 | OOS TPfirst | flag |")
    L.append("|---|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|")
    for r in rs:
        flags=[]
        if r.get("OOS_insufficient")=="True": flags.append("OOS n<30")
        if r.get("relaxed_IS")=="True": flags.append("IS<30 relaxed")
        p=f"{r['L']}/{f(r['slMult'],1)}/{f(r['RR'],1)}/{r['bias']}"
        L.append("| "+" | ".join([
            r["coin"], r["tf"], p,
            r["IS_n"], f(r["IS_sumR"],1), f(r["IS_PF"],2),
            r["OOS_n"], pct(r["OOS_WR"]), f(r["OOS_avgR"],3),
            f(r["OOS_sumR"],1), f(r["OOS_PF"],2), f(r["OOS_maxDD"],1),
            f(r["OOS_net06"],1), f(r["OOS_net04"],1), f(r["OOS_sumR_TPfirst"],1),
            ", ".join(flags) if flags else ""])+" |")
    return "\n".join(L)

out=[]
out.append("# OptiTrade -- independent replication & honest walk-forward backtest\n")
out.append("**Corpus:** `binance_perp_corpus.db` -- Binance USD-M perp (provenance proven, "
           "see `03_venue_corpus_comparison.md`). 4 coins x 5 TFs, native, 100% contiguous, "
           "IS/OOS windows drawn from 2022-07 .. 2026-06-30 (OOS ends ~31d stale).\n")
out.append("**Protocol:** walk-forward -- grid-optimize on first 70% (IS), freeze, evaluate "
           "on last 30% (OOS). Objective = **max GROSS sum-R s.t. IS n>=30**. Grid: "
           "L in {10..50 step5}, slMult {1.0..4.0 step0.5}, RR {1.0..4.5 step0.5}, "
           "bias {0..10 step2} = 3,024 combos/cell. minSep=6, warmup=120.\n")
out.append("**Intrabar:** SL-FIRST (conservative) is primary; `OOS TPfirst` column is the same "
           "params under TP-first (optimistic) as a sensitivity. **GROSS R is primary**; "
           "`net06`/`net04` subtract Bitunix taker fees at 0.06%/side and 0.04%/side (both "
           "sides), expressed in R per each trade's own risk unit "
           "(`fee_rate*(entry+exit_notional)/risk`).\n")
out.append("**R:** 1R = entry->SL = slMult*ATR(14). Four TP rungs at RR*(i/4) R, each closes "
           "1/4; fixed SL closes remainder (no breakeven move -- spec is silent). "
           "Engine unit-tested 26/26 (`t_unit.py`).\n")
out.append("**Full record:** `results_full.csv` holds every metric -- n, WR, avgR, sumR, PF, "
           "maxDD(R) -- for BOTH the IS and OOS windows and BOTH configs, plus net06/net04 and "
           "the TP-first sensitivity. The tables below summarise the decision-relevant subset "
           "(IS n/sumR/PF for the decay read; full OOS).\n")

out.append("\n## 1. Walk-forward winner (optimized IS -> frozen OOS)\n")
out.append(cfg_table("WF-winner"))
out.append("\n## 2. Fixed-default baseline (L=30, slMult=2.1, RR=3.5, bias=5; no optimization)\n")
out.append(cfg_table("Fixed-default"))

# vendor
vp=vendor["params"]
out.append("\n## 3. Vendor-methodology reproduction vs honest number -- BTC 15m\n")
out.append("Quantifies the inflation from (in-sample + optimistic fills + zero costs) vs "
           "(out-of-sample + conservative fills + real fees), same cell.\n")
out.append("| | Vendor methodology | Honest |")
out.append("|---|---|---|")
out.append(f"| Grid basis | full history (in-sample) | walk-forward OOS (last 30%) |")
out.append(f"| Fills | TP-first (optimistic) | SL-first (conservative) |")
out.append(f"| Fees | zero | gross shown; net06 also |")
out.append(f"| Params | {tuple(vp)} (best) | {tuple(vendor['honest_OOS_params'])} (frozen) |")
out.append(f"| n | {vendor['n']:,} | {vendor['honest_OOS_n']:,} |")
out.append(f"| **sum R** | **+{vendor['sumR_TPfirst_zerofee']:.1f}** | "
           f"**{vendor['honest_OOS_sumR']:+.1f} gross / {vendor['honest_OOS_net06']:+.1f} net06** |")
out.append(f"| WR | {vendor['WR']*100:.1f}% | (see table 1) |")
out.append(f"| PF | {vendor['PF']:.3f} | (see table 1) |")
out.append(f"\n> The marketed **+{vendor['sumR_TPfirst_zerofee']:.0f} R** becomes "
           f"**{vendor['honest_OOS_sumR']:+.0f} R gross** (and "
           f"**{vendor['honest_OOS_net06']:+.0f} R** net of 0.06%/side fees) out-of-sample. "
           f"Even the vendor's own best-case PF is {vendor['PF']:.3f}.\n")

# leads
wf=[r for r in rows if r["config"]=="WF-winner"]
netpos=[r for r in wf if r["OOS_n"] and int(r["OOS_n"])>=30 and float(r["OOS_net06"])>0]
grosspos=[r for r in wf if r["OOS_n"] and int(r["OOS_n"])>=30 and float(r["OOS_sumR"])>0]
insuff=[r for r in wf if r.get("OOS_insufficient")=="True"]
out.append("\n## 4. Leads & observations (evidence, not verdicts)\n")
out.append(f"- **Fee drag scales inversely with stop size.** Fee-in-R per trade: ~0.66-0.93 R "
           "on 3m, ~0.19-0.33 on 15m, ~0.09-0.15 on 1h, ~0.04-0.07 on 4h, ~0.02-0.04 on 1d. "
           "At Bitunix taker (0.06%/side) the high-frequency cells cannot overcome costs: e.g. "
           "BTC 3m OOS gross +159.8 R -> net06 -3,234 R.")
out.append(f"- **Gross vs net divergence is the whole story on low TFs.** {len(grosspos)} of 20 "
           f"cells are OOS gross-positive with n>=30; after 0.06%/side fees only "
           f"**{len(netpos)}** remain net-positive: "
           + ", ".join(f"{r['coin']} {r['tf']} (net06 {float(r['OOS_net06']):+.1f}, n={r['OOS_n']})"
                       for r in netpos) + ".")
out.append("- Those two net-positive cells rest on modest n and a **single 30% OOS window "
           "(one regime slice)** -- leads worth a dedicated look, not established edge. At the "
           "lower 0.04%/side tier a few more (SOL 1h, XRP 4h) tip marginally positive.")
out.append(f"- **All five 1d cells have OOS n<30** ({', '.join(r['coin']+' '+r['tf']+' n='+r['OOS_n'] for r in insuff if r['tf']=='1d')}) "
           "-> flagged insufficient; no daily-timeframe conclusion is supported by this sample.")
out.append("- **IS->OOS decay is common.** The optimizer repeatedly favours grid-edge params "
           "(L=10, bias=0); several strong IS cells go negative OOS (BTC 15m IS +130 -> OOS "
           "-31 gross). Consistent with limited robustness of the optimized parameters.")
out.append("- **The un-optimized baseline is often *less bad* net-of-fees.** The WF optimizer "
           "gravitates to tight stops (slMult 1.0-1.5) that maximise GROSS sum-R but carry the "
           "highest fee-in-R; the fixed default (slMult=2.1, wider stop) has lower fee drag, so "
           "on several cells (e.g. BTC 3m net06 -1,348 vs WF -3,234; ETH 15m net04 +2.8) it "
           "survives fees better than the 'winner'. A lead that GROSS-sum-R is the wrong "
           "objective under taker fees.")
out.append("- **Parked Bybit cross-venue pass** would run only on the cells that survive OOS "
           "here (the net-positive / gross-positive-with-n>=30 set), per your instruction.")
out.append("\n## 5. Reproduce\n`python run_study.py` (25s) -> results_full.csv + results_vendor.json; "
           "`python mk_report.py` -> this file. Engine: `optitrade_bt.py` (`python t_unit.py`).")

open("RESULTS.md","w").write("\n".join(out)+"\n")
print("wrote RESULTS.md")
