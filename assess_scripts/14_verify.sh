set -u
cd /home/azureuser/trading_corp || exit 2
cat > /tmp/pct_verify.py <<'PYEOF'
from trading_corp.persistence.db import load_agent_state
AG="polymarket_copy_trader"
def wal(x): return str(x.get("wallet") or x.get("proxy_wallet") or "").lower()
sel=[e for e in (load_agent_state(AG,"selected_whales") or ([],))[0] if isinstance(e,dict)]
pin=[e for e in (load_agent_state(AG,"pinned_whales") or ([],))[0] if isinstance(e,dict)]
wat=[e for e in (load_agent_state(AG,"watch_only_whales") or ([],))[0] if isinstance(e,dict)]
S={wal(e):e for e in sel}; P={wal(e):e for e in pin}; W={wal(e):e for e in wat}
print(f"COUNTS selected={len(sel)} pinned={len(pin)} watch={len(wat)}")
W_ = {
 "rollobravado":"0x3b62c64ebaee15478e8b21765b9f940458655cc8",
 "Kosherlocks":"0x82398835fe16616214d928ba87127e28fc1cd9a3",
 "GreatestTrader":"0x258c8a3ab3f9dd5c1e3bb05f54a9187247b77c23",
 "olddirtyfighter":"0x48898de94e70ca0c06c62c57483b3a8d5890e2d8",
 "llllllII":"0x7714c16f86bcfdba47bfcb161dc39a2a1ff2b814",
 "potatobrahh":"0xf192501abae4c453cc15ddbe9543ed11e99a6ee2",
 "ChadStarmer":"0x1f9f03e7ce52979b658b0bb75b483ff923fda025",
 "Hakei":"0x97ead83eb0e6b7f142e63284791882f05ddf3363",
 "CVCM":"0x27d2812fa0d04ca1b874cffedff7a15beb4ab0f8",
 "ox1star84":"0x4a1b8e8d38aecdc9687bb0f601801d59fa44724f",
 "DegenKingBetter":"0xb56db5215443706244b0af76b3daaad3066ad621",
 "digitalnomad85":"0x300b4292912c123066a380a344b505b0f353f636",
}
def chk(cond): return "PASS" if cond else "FAIL"
print("--- PROMOTES (want: in selected AND pinned) ---")
for n in ("rollobravado","Kosherlocks","GreatestTrader","olddirtyfighter"):
    w=W_[n]; extra=""
    if n=="GreatestTrader":
        extra=f" note_sel={'note' in S.get(w,{})} note_pin={'note' in P.get(w,{})}"
    print(f"  {chk(w in S and w in P)} {n}: selected={w in S} pinned={w in P}{extra}")
print("--- DEMOTE llllllII (want: NOT selected, NOT pinned, IN watch w/ note) ---")
w=W_["llllllII"]; we=W.get(w,{})
print(f"  {chk(w not in S and w not in P and w in W and 'note' in we)} llllllII: selected={w in S} pinned={w in P} watch={w in W} watch_note={'note' in we}")
print("--- REMOVE (want: NOT selected, NOT pinned) ---")
for n in ("potatobrahh","ChadStarmer"):
    w=W_[n]; print(f"  {chk(w not in S and w not in P)} {n}: selected={w in S} pinned={w in P}")
print("--- KEEP unchanged (want: still in selected; pinned too) ---")
for n in ("Hakei","CVCM","ox1star84","DegenKingBetter"):
    w=W_[n]; print(f"  {chk(w in S and w in P)} {n}: selected={w in S} pinned={w in P}")
print("--- digitalnomad85 unchanged (want: pinned, NOT selected) ---")
w=W_["digitalnomad85"]; print(f"  {chk(w in P and w not in S)} digitalnomad85: selected={w in S} pinned={w in P}")
PYEOF
PYTHONPATH=/home/azureuser/trading_corp venv/bin/python /tmp/pct_verify.py
rm -f /tmp/pct_verify.py
DB=/home/azureuser/trading_corp/data/trading_corp.db
RO="sqlite3 -readonly $DB"
echo "--- audit_event log (last 30 min): promoted/demoted ---"
$RO "SELECT kind||' n='||COUNT(*)||' last='||substr(MAX(ts),1,19) FROM audit_event WHERE actor='polymarket_copy_trader' AND kind IN ('polymarket_whale_promoted','polymarket_whale_demoted') AND ts>=datetime('now','-30 minutes') GROUP BY kind;"
echo "=== DONE ==="
