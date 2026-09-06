import base64, os, textwrap

ST = os.path.dirname(os.path.abspath(__file__))
CC = os.path.dirname(ST)
block = open(os.path.join(ST, "m3_block.txt"), "rb").read().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
b64 = base64.b64encode(block).decode()
wrapped = "\n".join(textwrap.wrap(b64, 76))

BOX = "236a6be054268278"
TGT = "408b2a415a1da18b"
BLK = "d3c784e6121574d9"

def sub(s):
    return s.replace("@BOX@", BOX).replace("@TGT@", TGT).replace("@BLK@", BLK)

SPLICE = sub(r'''"$VENV" - "$MAIN" "$BLKF" "$GRF" <<'PY'
import sys, hashlib
MAIN, BLKF, GRF = sys.argv[1], sys.argv[2], sys.argv[3]
box = open(MAIN, "rb").read()
block = open(BLKF, "rb").read()
def s16(b): return hashlib.sha256(b).hexdigest()[:16]
def cs(b): return b.replace(b"\r", b"")
assert s16(cs(box)) == "@BOX@", "DRIFT box base"
assert s16(block) == "@BLK@", "block hash"
anchor = b"        # --- Phase 2a boot invariant:"
assert box.count(anchor) == 1, "anchor not unique"
pos = box.find(anchor)
assert box[pos-1:pos] == b"\n", "anchor not at line start"
g = box[:pos] + block + b"\n" + box[pos:]
assert g[:pos] == box[:pos] and g[pos+len(block)+1:] == box[pos:], "splice touched other bytes"
assert s16(cs(g)) == "@TGT@", "TARGET mismatch"
open(GRF, "wb").write(g)
print("  SPLICE_OK -> TARGET @TGT@ reproduced on the live box file")
PY''')

HEAD = '''set -u
TS=$(date -u +%Y%m%dT%H%M%SZ)
ROOT=/home/azureuser/trading_corp
MAIN=$ROOT/trading_corp/main.py
VENV=$ROOT/venv/bin/python
BLKF=/tmp/m3blk_$TS.bin
GRF=/tmp/m3grf_$TS.py
'''

B64BLOCK = "base64 -d > \"$BLKF\" <<'B64'\n" + wrapped + "\nB64\n"

scratch = HEAD + sub('''echo "### PM M3 BOX-SCRATCH (writes ONLY /tmp; live main.py untouched) $TS ###"
echo "engine PID (untouched): $(systemctl show -p MainPID --value trading-corp 2>/dev/null)"
BH=$(tr -d '\\r' < "$MAIN" | sha256sum | cut -c1-16)
echo "## drift: box main.py CR-stripped = $BH (expect @BOX@)"
[ "$BH" = @BOX@ ] || { echo "** DRIFT -- ABORT (nothing written)"; exit 3; }
''') + B64BLOCK + 'cd "$ROOT"\n' + SPLICE + sub('''
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
echo "## live main.py STILL $(tr -d '\\r' < "$MAIN" | sha256sum | cut -c1-16) (expect @BOX@ -- untouched)"
echo "### SCRATCH DONE ###"
''')

apply = HEAD + sub('''BK=/home/azureuser/pm_m3_restore_backup_$TS
echo "### PM M3 RESTORE -- APPLY (graft M3 shard-snapshot block onto box CURRENT main.py; NO restart) $TS ###"
echo "engine PID (runs OLD in-memory main.py until Jack restarts; a file write does NOT reload it): $(systemctl show -p MainPID --value trading-corp 2>/dev/null)"
BH=$(tr -d '\\r' < "$MAIN" | sha256sum | cut -c1-16)
echo "## drift check: box main.py CR-stripped = $BH (expect @BOX@)"
[ "$BH" = @BOX@ ] || { echo "** DRIFT -- box main.py moved since the graft was built; ABORT, re-graft needed. Nothing changed."; exit 3; }
''') + B64BLOCK + 'cd "$ROOT"\n' + SPLICE + sub('''
[ -f "$GRF" ] || { echo "** splice FAILED (hash-gate) -- ABORT, nothing applied"; rm -f "$BLKF"; exit 4; }
PP=$(stat -c '%a' "$MAIN"); PO=$(stat -c '%U:%G' "$MAIN")
mkdir -p "$BK"; cp -a "$MAIN" "$BK/main.py"
echo "## main.py perms=$PP owner=$PO ; backup -> $BK/main.py (the rollback)"
restore(){ echo "  ...RESTORING $BK/main.py"; cp -a "$BK/main.py" "$MAIN"; chmod "$PP" "$MAIN"; }
cp "$GRF" "$MAIN"; chmod "$PP" "$MAIN"
AH=$(tr -d '\\r' < "$MAIN" | sha256sum | cut -c1-16)
echo "## applied main.py CR-stripped = $AH (expect @TGT@)"
[ "$AH" = @TGT@ ] || { echo "** APPLIED HASH MISMATCH -- RESTORING + ABORT"; restore; rm -f "$BLKF" "$GRF"; exit 5; }
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
echo "M3_GRAFT_APPLIED_OK backup=$BK applied=@TGT@"
echo "### APPLY DONE -- M3 block grafted onto box main.py, MACE survives by count, Gate-A green. NO restart. ###"
''')

for name, body in [("pm_m3_scratch.sh", scratch), ("pm_m3_apply.sh", apply)]:
    p = os.path.join(CC, name)
    with open(p, "w", newline="\n") as f:
        f.write(body)
    bad = sum(1 for ch in body if ord(ch) > 127)
    print("wrote %s  bytes=%d chars_gt127=%d" % (name, len(body), bad))
print("b64 wrapped lines=%d" % (wrapped.count("\n") + 1))
