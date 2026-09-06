set -u
TS=$(date -u +%Y%m%dT%H%M%SZ)
ROOT=/home/azureuser/trading_corp
MAIN=$ROOT/trading_corp/main.py
VENV=$ROOT/venv/bin/python
BLKF=/tmp/m3blk_$TS.bin
GRF=/tmp/m3grf_$TS.py
echo "### PM M3 BOX-SCRATCH (writes ONLY /tmp; live main.py untouched) $TS ###"
echo "engine PID (untouched): $(systemctl show -p MainPID --value trading-corp 2>/dev/null)"
BH=$(tr -d '\r' < "$MAIN" | sha256sum | cut -c1-16)
echo "## drift: box main.py CR-stripped = $BH (expect 236a6be054268278)"
[ "$BH" = 236a6be054268278 ] || { echo "** DRIFT -- ABORT (nothing written)"; exit 3; }
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
[ -f "$GRF" ] || { echo "** splice FAILED -- ABORT"; rm -f "$BLKF"; exit 4; }
echo "## Gate-A py_compile grafted on BOX venv:"
"$VENV" -m py_compile "$GRF" && echo "  py_compile OK" || { echo "** py_compile FAILED"; rm -f "$BLKF" "$GRF"; exit 5; }
echo "## MACE/base marker survival (grep -ic) live vs grafted -- must be EQUAL:"
for m in tastytrade mace KalshiTailPriceArbAgent; do echo "  $m live=$(grep -ic "$m" "$MAIN") grafted=$(grep -ic "$m" "$GRF")"; done
echo "## PM markers on grafted (driver unchanged + M3 restored):"
echo "  driver scheduled_pm_live_loop=$(grep -c scheduled_pm_live_loop "$GRF") WIRED=$(grep -c 'PM LIVE DRIVER WIRED' "$GRF")"
echo "  M3 scheduled_shard_snapshot_loop=$(grep -c scheduled_shard_snapshot_loop "$GRF") writer_WIRED=$(grep -c 'M3 shard-snapshot writer WIRED' "$GRF") pm_imports=$(grep -c trading_corp.prediction_markets "$GRF")"
echo "## PM module imports resolve on box venv (the M3 block runtime deps):"
PYTHONPATH=. "$VENV" -c "import trading_corp.prediction_markets.shard_snapshot_task, trading_corp.prediction_markets.db, trading_corp.brokers.kalshi_live" >/tmp/m3imp_$TS 2>&1 && echo "  PM imports OK" || { echo "  ** PM imports FAILED **"; sed 's/^/    /' /tmp/m3imp_$TS; }
rm -f "$BLKF" "$GRF" /tmp/m3imp_$TS
echo "## live main.py STILL $(tr -d '\r' < "$MAIN" | sha256sum | cut -c1-16) (expect 236a6be054268278 -- untouched)"
echo "### SCRATCH DONE ###"
