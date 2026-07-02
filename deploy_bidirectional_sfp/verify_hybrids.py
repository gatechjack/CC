import sys, difflib
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
tmp=r"C:\Users\AA Incorporado\Desktop\deploy_tmp_bidir"

prod_main=open(tmp+r"\prod_main.py","rb").read()
main_hybrid=open(tmp+r"\main_hybrid.py","rb").read()
prod_strat=open(tmp+r"\prod_strat.yaml","rb").read()
strat_hybrid=open(tmp+r"\strat_hybrid.yaml","rb").read()

pm=prod_main.replace(b'\r\n',b'\n').decode('utf-8','replace').splitlines(keepends=True)
hm=main_hybrid.replace(b'\r\n',b'\n').decode('utf-8','replace').splitlines(keepends=True)
print("=== main.py  prod -> hybrid diff (must be ONLY the bitunix_sfp cache hunk) ===")
print("".join(difflib.unified_diff(pm,hm,fromfile="prod",tofile="hybrid",n=1)))
print("main hybrid CRLF-consistent (CR==LF):", main_hybrid.count(b'\r')==main_hybrid.count(b'\n'))

pi=prod_strat.index(b"\nbitunix_sfp:")+1
print("\n=== strategies.yaml ===")
print("pre-block bytes byte-identical to prod:", prod_strat[:pi]==strat_hybrid[:pi])
print("strat hybrid CRLF-consistent (CR==LF):", strat_hybrid.count(b'\r')==strat_hybrid.count(b'\n'))
ps=prod_strat.replace(b'\r\n',b'\n').decode('utf-8','replace').splitlines(keepends=True)
hs=strat_hybrid.replace(b'\r\n',b'\n').decode('utf-8','replace').splitlines(keepends=True)
print("--- strat prod -> hybrid diff (must be ONLY the bitunix_sfp block) ---")
print("".join(difflib.unified_diff(ps,hs,fromfile="prod",tofile="hybrid",n=0)))
for k in (b"side: regime",b'"SOL/USDT.P"',b'"XRP/USDT.P"',b"leverage: 10.0",b"risk_pct_real: 0.05",b"risk_pct_considerable: 0.10"):
    print("  block has",k.decode(),":",k in strat_hybrid[pi:])
# confirm polymarket line preserved outside the block
print("  polymarket min_minutes_to_resolution preserved:", b"min_minutes_to_resolution" in strat_hybrid[:pi])
