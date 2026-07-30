true
echo "=== /tmp b64 files ==="
ls -la /tmp/r1r2_*.b64 2>&1
echo "=== first 60 bytes of kct b64 (od, to spot CR) ==="
head -c 60 /tmp/r1r2_kct.b64 | od -c | head -4
echo "=== decode attempt A: plain base64 -d ==="
base64 -d /tmp/r1r2_kct.b64 > /tmp/a.out 2>/tmp/a.err; echo "exit=$? errbytes=$(wc -c </tmp/a.err)"; head -c 200 /tmp/a.err
echo "=== decode attempt B: strip CR then base64 -d ==="
tr -d '\r' < /tmp/r1r2_kct.b64 | base64 -d > /tmp/b.out 2>/tmp/b.err; echo "exit=$? md5=$(tr -d '\r' </tmp/b.out|md5sum|cut -d' ' -f1)"; head -c 200 /tmp/b.err
echo "expected kct md5 = af336db8498c3543b8d824f471a43173"
echo "=== DONE diag ==="
