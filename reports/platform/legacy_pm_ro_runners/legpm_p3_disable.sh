set -u
ROOT=/home/azureuser/trading_corp
CFG="$ROOT/config/strategies.yaml"
PY="$ROOT/venv/bin/python3"
TS=$(date -u +%Y%m%dT%H%M%SZ)
BAK="$CFG.bak_phase3disable_$TS"
echo "### PHASE-3 CONFIG DISABLE (WRITE) $TS ###"
echo "cfg=$CFG"
echo "backup=$BAK"
echo "flip_time_utc=$(date -u +%FT%TZ)"
echo
echo "## BACKUP ##"
cp -p "$CFG" "$BAK" && echo "backup_ok bytes=$(wc -c < "$BAK")" || { echo "BACKUP FAILED -- ABORT"; exit 2; }
echo
echo "## EDIT: flip (block,field) true->false; validate parse + fence; atomic replace ##"
"$PY" - "$CFG" <<'PYEOF'
import re,sys,os,yaml
path=sys.argv[1]
raw=open(path,encoding="utf-8").read()
lines=raw.split("\n")
# (block, field) targets
targets=[("kalshi_llm_arbitrage","enabled"),("kalshi_copy_trader","enabled"),("kalshi_copy_trader","auto_execute")]
changed=[];already=[];notfound=[]
def flip(name,field):
    hdr=None
    for i,l in enumerate(lines):
        if re.match(r'^'+re.escape(name)+r':',l): hdr=i;break
    if hdr is None: notfound.append((name,field,"no_header"));return
    for i in range(hdr+1,len(lines)):
        l=lines[i]
        if re.match(r'^[A-Za-z_][\w]*:',l): notfound.append((name,field,"no_field_in_block"));return
        m=re.match(r'^(  '+re.escape(field)+r':\s*)(true|True)(\b.*)?$',l)
        if m:
            lines[i]=m.group(1)+"false"+(m.group(3) or "")
            changed.append((name,field,i+1,l.strip(),lines[i].strip()));return
        m2=re.match(r'^(  '+re.escape(field)+r':\s*)(false|False)(\b.*)?$',l)
        if m2: already.append((name,field,i+1));return
    notfound.append((name,field,"scan_end"))
for n,f in targets: flip(n,f)
new="\n".join(lines)
try:
    y=yaml.safe_load(new)
    assert isinstance(y,dict)
    assert (y.get("pm_live_driver") or {}).get("enabled") is True, "FENCE: pm_live_driver.enabled must stay true"
    assert (y.get("kalshi_llm_arbitrage") or {}).get("enabled") is False
    assert (y.get("kalshi_copy_trader") or {}).get("enabled") is False
    assert (y.get("kalshi_copy_trader") or {}).get("auto_execute") is False
    # verify P2 flips still false (no regression)
    for b in ("polymarket_copy_trader","kalshi_tail_price_arb","kalshi_temporal_bucket_arb","kalshi_sports_scout","poly_kalshi_mlb"):
        assert (y.get(b) or {}).get("enabled") is False, "P2 regression: %s enabled not false"%b
except Exception as e:
    print("VALIDATE FAILED -- NOT WRITTEN:",e); print("notfound=",notfound); sys.exit(3)
if notfound:
    print("NOTFOUND -- NOT WRITTEN:",notfound); sys.exit(3)
tmp=path+".tmp_p3"
open(tmp,"w",encoding="utf-8",newline="").write(new)
os.replace(tmp,path)
print("WROTE ok  changed=%d already=%d"%(len(changed),len(already)))
for c in changed: print("  FLIP %-24s.%-14s L%d: [%s] -> [%s]"%(c[0],c[1],c[2],c[3],c[4]))
for a in already: print("  ALREADY-FALSE %-24s.%s L%d"%(a[0],a[1],a[2]))
PYEOF
echo "edit_rc=$?"
echo
echo "## POST: re-read the 3 flags + fence ##"
"$PY" - "$CFG" <<'PYEOF'
import yaml,sys
y=yaml.safe_load(open(sys.argv[1],encoding="utf-8"))
print("  kalshi_llm_arbitrage.enabled     =", (y.get("kalshi_llm_arbitrage") or {}).get("enabled"))
print("  kalshi_copy_trader.enabled       =", (y.get("kalshi_copy_trader") or {}).get("enabled"))
print("  kalshi_copy_trader.auto_execute  =", (y.get("kalshi_copy_trader") or {}).get("auto_execute"))
print("  pm_live_driver.enabled (FENCE)   =", (y.get("pm_live_driver") or {}).get("enabled"))
PYEOF
echo "### DONE ###"
