import hashlib, subprocess, difflib
tmp=r"C:\Users\AA Incorporado\Desktop\deploy_tmp_bidir"
wt=r"C:\Users\AA Incorporado\cc-sfp-deploy-wt"

def git_show(path):
    return subprocess.run(["git","-C",wt,"show","79cbbef:"+path],capture_output=True).stdout
def to_crlf(b):
    return b.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')
def md5(b):
    return hashlib.md5(b).hexdigest()
def extract(buf, start_sub, end_sub):
    lines=buf.splitlines(keepends=True)
    i=next(k for k,l in enumerate(lines) if start_sub in l)
    j=next(k for k,l in enumerate(lines) if end_sub in l and k>=i)
    return b"".join(lines[i:j+1])

# ---------- main.py hybrid ----------
prod_main=open(tmp+r"\prod_main.py","rb").read()
base_main=git_show("trading_corp/main.py").replace(b'\r\n',b'\n')
wt_main=open(wt+r"\trading_corp\main.py","rb").read().replace(b'\r\n',b'\n')
start=b"# bitunix_sfp (2026-06-25)"
old_lf=extract(base_main,start,b"max_bars=160)")
new_lf=extract(wt_main,start,b"max_bars=1200)")
old_crlf=to_crlf(old_lf); new_crlf=to_crlf(new_lf)
c=prod_main.count(old_crlf)
print("main old-block occurrences in prod (want 1):",c)
assert c==1
main_hybrid=prod_main.replace(old_crlf,new_crlf)
open(tmp+r"\main_hybrid.py","wb").write(main_hybrid)
print("MAIN_HYBRID_MD5="+md5(main_hybrid),"bytes="+str(len(main_hybrid)),"CR="+str(main_hybrid.count(b'\r')))

# ---------- strategies.yaml hybrid ----------
prod_strat=open(tmp+r"\prod_strat.yaml","rb").read()
wt_strat=open(wt+r"\config\strategies.yaml","rb").read().replace(b'\r\n',b'\n')
si=wt_strat.index(b"\nbitunix_sfp:")+1
my_block_crlf=to_crlf(wt_strat[si:])
pc=prod_strat.count(b"\nbitunix_sfp:")
print("prod block-start occurrences (want 1):",pc)
assert pc==1
pi=prod_strat.index(b"\nbitunix_sfp:")+1
strat_hybrid=prod_strat[:pi]+my_block_crlf
open(tmp+r"\strat_hybrid.yaml","wb").write(strat_hybrid)
print("STRAT_HYBRID_MD5="+md5(strat_hybrid),"bytes="+str(len(strat_hybrid)),"CR="+str(strat_hybrid.count(b'\r')))

# ---------- verification ----------
pm=prod_main.replace(b'\r\n',b'\n').decode('utf-8','replace').splitlines(keepends=True)
hm=main_hybrid.replace(b'\r\n',b'\n').decode('utf-8','replace').splitlines(keepends=True)
print("=== main prod->hybrid diff (must be ONLY the cache hunk) ===")
print("".join(difflib.unified_diff(pm,hm,n=0)))
print("main hybrid CRLF-consistent (CR==LF-lines):", main_hybrid.count(b'\r')==main_hybrid.count(b'\n'))
print("=== strat: pre-block bytes identical to prod? ===", prod_strat[:pi]==strat_hybrid[:pi])
print("strat hybrid CRLF-consistent:", strat_hybrid.count(b'\r')==strat_hybrid.count(b'\n'))
print("prod block bytes:",len(prod_strat[pi:]),"-> my block bytes:",len(my_block_crlf))
# confirm the 4 config knobs are present in the new block
for k in (b"side: regime",b"SOL/USDT.P",b"XRP/USDT.P",b"leverage: 10.0",b"risk_pct_real: 0.05"):
    print("  block contains",k.decode(),":",k in my_block_crlf)
