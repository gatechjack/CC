set -u
TS=$(date -u +%Y%m%dT%H%M%SZ)
ROOT=/home/azureuser/trading_corp
MAIN=$ROOT/trading_corp/main.py
VENV=$ROOT/venv/bin/python
BLKF=/tmp/m3blk_$TS.bin
GRF=/tmp/m3grf_$TS.py
BK=/home/azureuser/pm_m3_restore_backup_$TS
echo "### PM M3 RESTORE -- APPLY (graft M3 shard-snapshot block onto box CURRENT main.py; NO restart) $TS ###"
echo "engine PID (runs OLD in-memory main.py until Jack restarts; a file write does NOT reload it): $(systemctl show -p MainPID --value trading-corp 2>/dev/null)"
BH=$(tr -d '\r' < "$MAIN" | sha256sum | cut -c1-16)
echo "## drift check: box main.py CR-stripped = $BH (expect 236a6be054268278)"
[ "$BH" = 236a6be054268278 ] || { echo "** DRIFT -- box main.py moved since the graft was built; ABORT, re-graft needed. Nothing changed."; exit 3; }
base64 -d > "$BLKF" <<'B64'
ICAgICAgICAjIOKUgOKUgCBNMyAoMjAyNi0wOS0wMSk6IHBlci1hY2NvdW50IFNIQVJELUJBTEFO
Q0UgU05BUFNIT1RTICg1LW1pbiB0aW1lcikgLT4gcG1fd2ViIHNob3dzIHRoZSBzcGxpdCArIEFH
RSwKICAgICAgICAjIGNyZWRlbnRpYWwtZnJlZS4gREVMSUJFUkFURUxZIHNlcGFyYXRlIGZyb20g
dGhlIGRyaXZlcidzIHBlci1jeWNsZSBmdW5kaW5nLWdhdGUgYmFsYW5jZSByZWFkICh0aGF0IEdB
VEVTCiAgICAgICAgIyBvcmRlcnM7IHRoaXMgSU5GT1JNUyBhIGRpc3BsYXkgKyBhIGhpc3Rvcnkp
LiBSZWFkcyBFQUNIIGFjdGl2ZSBwbV9hY2NvdW50IHdpdGggSVRTIE9XTiBrZXlzCiAgICAgICAg
IyAoc2VjcmV0X3JlZiAtPiBrZXlwYWlyKSwgc28gS2FyZW4ncyBiYWxhbmNlIGlzIGNhcHR1cmVk
IGV2ZW4gdGhvdWdoIGhlciBrZXlzIGFyZSBzZXBhcmF0ZS4gRmFpbC1zYWZlCiAgICAgICAgIyB3
aXJpbmcgKG5ldmVyIGJyZWFrcyBib290KTsgZmFpbC1zb2Z0IHBlciBhY2NvdW50IGluc2lkZSB0
aGUgbG9vcC4gTmVlZHMgcG1fc2hhcmRfYmFsYW5jZV9zbmFwc2hvdAogICAgICAgICMgKG1pZ3Jh
dGlvbiAwMTYpIC0tIHRoZSBkZXBsb3kgYXBwbGllcyAwMTYgQkVGT1JFIHRoaXMgcmVzdGFydCAo
bWlncmF0aW9uIGxlYWRzKTsgdGhlIHdyaXRlciBhbHNvCiAgICAgICAgIyBmYWlsLXNvZnRzIGlm
IHRoZSB0YWJsZSBpcyBzb21laG93IGFic2VudC4KICAgICAgICB0cnk6CiAgICAgICAgICAgIGZy
b20gdHJhZGluZ19jb3JwLmJyb2tlcnMua2Fsc2hpX2xpdmUgaW1wb3J0IEthbHNoaUxpdmVCcm9r
ZXIgYXMgX0tMQl9zbmFwCiAgICAgICAgICAgIGZyb20gdHJhZGluZ19jb3JwLnByZWRpY3Rpb25f
bWFya2V0cyBpbXBvcnQgc2hhcmRfc25hcHNob3RfdGFzayBhcyBfc3N0LCBkYiBhcyBfc25hcF9k
YgogICAgICAgICAgICBfc25hcF9kZW1vID0gb3MuZ2V0ZW52KCJLQUxTSElfVVNFX0RFTU8iLCAi
Iikuc3RyaXAoKSBpbiAoIjEiLCAidHJ1ZSIsICJUcnVlIikKICAgICAgICAgICAgX3NuYXBfYnJv
a2VycyA9IHt9CiAgICAgICAgICAgIHdpdGggX3NuYXBfZGIuY29ubmVjdChfc25hcF9kYi5wbV9k
Yl9wYXRoKCkpIGFzIF9zYzoKICAgICAgICAgICAgICAgIF9oYXNfYWNjdCA9IF9zYy5leGVjdXRl
KAogICAgICAgICAgICAgICAgICAgICJTRUxFQ1QgMSBGUk9NIHNxbGl0ZV9tYXN0ZXIgV0hFUkUg
dHlwZT0ndGFibGUnIEFORCBuYW1lPSdwbV9hY2NvdW50JyIpLmZldGNob25lKCkgaXMgbm90IE5v
bmUKICAgICAgICAgICAgICAgIF9hY2N0X3Jvd3MgPSBfc2MuZXhlY3V0ZSgiU0VMRUNUIGFjY291
bnRfaWQsIHNlY3JldF9yZWYgRlJPTSBwbV9hY2NvdW50IFdIRVJFIGFjdGl2ZT0xIikuZmV0Y2hh
bGwoKSBpZiBfaGFzX2FjY3QgZWxzZSBbXQogICAgICAgICAgICBmb3IgX2FyIGluIF9hY2N0X3Jv
d3M6CiAgICAgICAgICAgICAgICBfYWlkLCBfcmVmID0gX2FyWzBdLCBfYXJbMV0KICAgICAgICAg
ICAgICAgIF9raWQsIF9wZW0gPSBfc3N0LnJlc29sdmVfa2Fsc2hpX2tleXMoX3JlZiwgc2VjcmV0
cykKICAgICAgICAgICAgICAgIGlmIG5vdCBfa2lkIG9yIG5vdCBfcGVtOgogICAgICAgICAgICAg
ICAgICAgIGxvZy53YXJuaW5nKCJNMyBzaGFyZC1zbmFwc2hvdDogbm8ga2V5cyBmb3IgYWNjb3Vu
dCAlcyAoc2VjcmV0X3JlZj0lcykg4oCUIHNraXBwaW5nIiwgX2FpZCwgX3JlZikKICAgICAgICAg
ICAgICAgICAgICBjb250aW51ZQogICAgICAgICAgICAgICAgX3NiciA9IF9LTEJfc25hcChhcGlf
a2V5X2lkPV9raWQsIHByaXZhdGVfa2V5X3BlbT1fcGVtLCBkZW1vPV9zbmFwX2RlbW8sIG9yZGVy
X3R5cGU9ImlvYyIpCiAgICAgICAgICAgICAgICBhd2FpdCBfc2JyLmNvbm5lY3QoKQogICAgICAg
ICAgICAgICAgX3NuYXBfYnJva2Vyc1tfYWlkXSA9IF9zYnIKICAgICAgICAgICAgaWYgX3NuYXBf
YnJva2VyczoKICAgICAgICAgICAgICAgIHNoYXJkX3NuYXBzaG90X3Rhc2tfaGFuZGxlID0gYXN5
bmNpby5jcmVhdGVfdGFzaygKICAgICAgICAgICAgICAgICAgICBfc3N0LnNjaGVkdWxlZF9zaGFy
ZF9zbmFwc2hvdF9sb29wKF9zbmFwX2RiLnBtX2RiX3BhdGgoKSwgX3NuYXBfYnJva2VycykpCiAg
ICAgICAgICAgICAgICBsb2cuaW5mbygiTTMgc2hhcmQtc25hcHNob3Qgd3JpdGVyIFdJUkVEICgl
ZCBhY2NvdW50KHMpOiAlczsgNS1taW4gdGltZXIpIiwKICAgICAgICAgICAgICAgICAgICAgICAg
IGxlbihfc25hcF9icm9rZXJzKSwgc29ydGVkKF9zbmFwX2Jyb2tlcnMpKQogICAgICAgICAgICBl
bHNlOgogICAgICAgICAgICAgICAgbG9nLmluZm8oIk0zIHNoYXJkLXNuYXBzaG90IHdyaXRlcjog
bm8gY3JlZGVudGlhbGVkIHBtX2FjY291bnQg4oCUIG5vdCB3aXJlZCIpCiAgICAgICAgZXhjZXB0
IEV4Y2VwdGlvbiBhcyBfc25hcF9leGM6ICAjIG5vcWE6IEJMRTAwMSDigJQgbmV2ZXIgYnJlYWsg
ZW5naW5lIGJvb3QKICAgICAgICAgICAgbG9nLmV4Y2VwdGlvbigiTTMgc2hhcmQtc25hcHNob3Qg
d2lyaW5nIEZBSUxFRCAoZW5naW5lIGNvbnRpbnVlcyk6ICVzIiwgX3NuYXBfZXhjKQo=
B64
cd "$ROOT"
"$VENV" - "$MAIN" "$BLKF" "$GRF" <<'PY'
import sys, hashlib
MAIN, BLKF, GRF = sys.argv[1], sys.argv[2], sys.argv[3]
box = open(MAIN, "rb").read()
block = open(BLKF, "rb").read()
def s16(b): return hashlib.sha256(b).hexdigest()[:16]
def cs(b): return b.replace(b"\r", b"")
assert s16(cs(box)) == "236a6be054268278", "DRIFT box base"
assert s16(block) == "d3c784e6121574d9", "block hash"
anchor = b"        # --- Phase 2a boot invariant:"
assert box.count(anchor) == 1, "anchor not unique"
pos = box.find(anchor)
assert box[pos-1:pos] == b"\n", "anchor not at line start"
g = box[:pos] + block + b"\n" + box[pos:]
assert g[:pos] == box[:pos] and g[pos+len(block)+1:] == box[pos:], "splice touched other bytes"
assert s16(cs(g)) == "408b2a415a1da18b", "TARGET mismatch"
open(GRF, "wb").write(g)
print("  SPLICE_OK -> TARGET 408b2a415a1da18b reproduced on the live box file")
PY
[ -f "$GRF" ] || { echo "** splice FAILED (hash-gate) -- ABORT, nothing applied"; rm -f "$BLKF"; exit 4; }
PP=$(stat -c '%a' "$MAIN"); PO=$(stat -c '%U:%G' "$MAIN")
mkdir -p "$BK"; cp -a "$MAIN" "$BK/main.py"
echo "## main.py perms=$PP owner=$PO ; backup -> $BK/main.py (the rollback)"
restore(){ echo "  ...RESTORING $BK/main.py"; cp -a "$BK/main.py" "$MAIN"; chmod "$PP" "$MAIN"; }
cp "$GRF" "$MAIN"; chmod "$PP" "$MAIN"
AH=$(tr -d '\r' < "$MAIN" | sha256sum | cut -c1-16)
echo "## applied main.py CR-stripped = $AH (expect 408b2a415a1da18b)"
[ "$AH" = 408b2a415a1da18b ] || { echo "** APPLIED HASH MISMATCH -- RESTORING + ABORT"; restore; rm -f "$BLKF" "$GRF"; exit 5; }
echo "## MACE/base markers survive BY COUNT (grep -ic backup vs applied) -- zero deletions:"
FAIL=0
for m in tastytrade mace KalshiTailPriceArbAgent robinhood dxFeed; do B=$(grep -ic "$m" "$BK/main.py"); A=$(grep -ic "$m" "$MAIN"); S=OK; [ "$B" = "$A" ] || { S="*** DELETED ***"; FAIL=1; }; echo "  $m backup=$B applied=$A $S"; done
echo "  poly_kalshi_WIRED backup=$(grep -c 'Poly->Kalshi MLB copy WIRED' "$BK/main.py") applied=$(grep -c 'Poly->Kalshi MLB copy WIRED' "$MAIN")"
[ "$FAIL" = 0 ] || { echo "** A MACE/base MARKER WAS DELETED -- RESTORING + ABORT"; restore; rm -f "$BLKF" "$GRF"; exit 6; }
echo "## diff backup->applied (added lines only, 0 removed = pure insertion):"
echo "  added=$(diff "$BK/main.py" "$MAIN" | grep -c '^>') removed=$(diff "$BK/main.py" "$MAIN" | grep -c '^<')"
echo "## PM wiring now COMPLETE (driver + M3):"
echo "  driver scheduled_pm_live_loop=$(grep -c scheduled_pm_live_loop "$MAIN")(exp2) WIRED=$(grep -c 'PM LIVE DRIVER WIRED' "$MAIN")(exp1)"
echo "  M3 scheduled_shard_snapshot_loop=$(grep -c scheduled_shard_snapshot_loop "$MAIN")(exp1) writer_WIRED=$(grep -c 'M3 shard-snapshot writer WIRED' "$MAIN")(exp1) pm_imports=$(grep -c trading_corp.prediction_markets "$MAIN")(exp2)"
echo "## Gate-A: py_compile applied main.py on box venv:"
"$VENV" -m py_compile "$MAIN" >/tmp/m3pc_$TS 2>&1 && echo "  py_compile OK" || { echo "** py_compile FAILED -- RESTORING"; sed 's/^/    /' /tmp/m3pc_$TS; restore; rm -f "$BLKF" "$GRF" /tmp/m3pc_$TS; exit 7; }
echo "## Gate-A: PM modules import on box venv:"
PYTHONPATH=. "$VENV" -c "import trading_corp.prediction_markets.shard_snapshot_task, trading_corp.prediction_markets.db" >/tmp/m3pm_$TS 2>&1; RCPM=$?
[ "$RCPM" = 0 ] && echo "  PM modules import OK" || { echo "** PM module import FAILED -- RESTORING"; sed 's/^/    /' /tmp/m3pm_$TS; restore; rm -f "$BLKF" "$GRF" /tmp/m3pm_$TS; exit 8; }
echo "## Gate-A: import trading_corp.main post-graft:"
PYTHONPATH=. "$VENV" -c "import trading_corp.main" >/tmp/m3post_$TS 2>&1; RCPOST=$?
echo "  import trading_corp.main post-graft exit=$RCPOST"
if [ "$RCPOST" != 0 ]; then
  cp -a "$BK/main.py" "$MAIN"; chmod "$PP" "$MAIN"
  PYTHONPATH=. "$VENV" -c "import trading_corp.main" >/tmp/m3pre_$TS 2>&1; RCPRE=$?
  cp "$GRF" "$MAIN" 2>/dev/null; chmod "$PP" "$MAIN"
  if [ "$RCPRE" = 0 ]; then echo "** GRAFT BROKE import trading_corp.main (backup OK, grafted FAIL) -- RESTORING"; sed 's/^/    /' /tmp/m3post_$TS; restore; rm -f "$BLKF" "$GRF" /tmp/m3pc_$TS /tmp/m3pm_$TS /tmp/m3post_$TS /tmp/m3pre_$TS; exit 9;
  else echo "  (import-main also failed on the BACKUP -> ENV limitation of this ssh session, NOT the graft; py_compile + PM-modules ARE the gate, both green)"; fi
fi
rm -f "$BLKF" "$GRF" /tmp/m3pc_$TS /tmp/m3pm_$TS /tmp/m3post_$TS /tmp/m3pre_$TS
echo "## perms now: $(stat -c '%a %U:%G' "$MAIN")"
echo "engine PID (UNCHANGED -- running process untouched; grafted file staged for the NEXT restart): $(systemctl show -p MainPID --value trading-corp 2>/dev/null)"
echo "M3_GRAFT_APPLIED_OK backup=$BK applied=408b2a415a1da18b"
echo "### APPLY DONE -- M3 block grafted onto box main.py, MACE survives by count, Gate-A green. NO restart. ###"
