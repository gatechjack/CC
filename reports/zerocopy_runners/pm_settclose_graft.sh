ROOT=/home/azureuser/trading_corp
REL=trading_corp/prediction_markets/settlement.py
LIVE=$ROOT/$REL
STAGE=/tmp/settclose_settlement.py
BASE=a1abe0e3b04a85e4
TARGET=a0eb5a437166b875
PYC=/home/azureuser/trading_corp/venv/bin/python
csha(){ tr -d '\r' < "$1" | sha256sum | cut -c1-16; }
echo "=== df / ==="; df -h / | tail -1
if [ ! -f "$STAGE" ]; then echo "ABORT: staged file missing ($STAGE)"; exit 9; fi
bl=$(csha "$LIVE"); st=$(csha "$STAGE")
echo "box LIVE  csha=$bl  (expect BASE   $BASE)"
echo "staged    csha=$st  (expect TARGET $TARGET)"
if [ "$bl" != "$BASE" ];   then echo "ABORT: box LIVE != BASE (drift) -- apply NOTHING"; rm -f "$STAGE"; exit 2; fi
if [ "$st" != "$TARGET" ]; then echo "ABORT: staged != TARGET -- apply NOTHING"; rm -f "$STAGE"; exit 3; fi
BK=/home/azureuser/pm_settclose_backup_$(date -u +%Y%m%dT%H%M%SZ).settlement.py
cp "$LIVE" "$BK"; echo "BACKUP: $BK  size=$(stat -c %s "$BK")  (left in place)"
tr -d '\r' < "$STAGE" > "$LIVE"                 # apply LF (box convention)
la=$(csha "$LIVE")
echo "LIVE after apply csha=$la (expect TARGET $TARGET)"
if [ "$la" != "$TARGET" ]; then echo "VERIFY FAIL -> RESTORE backup"; cp "$BK" "$LIVE"; echo "restored csha=$(csha "$LIVE")"; rm -f "$STAGE"; exit 4; fi
cd "$ROOT"
"$PYC" -m py_compile "$LIVE"; rc1=$?
PYTHONPATH=. "$PYC" -c "import trading_corp.prediction_markets.settlement as s; assert s._settlement_close_source('scalar')=='settlement_scalar'; assert s._ABSENT_VALUE_SETTLED==0.0; print('import OK scalar->'+s._settlement_close_source('scalar'))"; rc2=$?
if [ $rc1 -ne 0 ] || [ $rc2 -ne 0 ]; then echo "COMPILE/IMPORT FAIL rc1=$rc1 rc2=$rc2 -> RESTORE backup"; cp "$BK" "$LIVE"; echo "restored csha=$(csha "$LIVE")"; rm -f "$STAGE"; exit 5; fi
echo "APPLIED OK: LIVE==TARGET, py_compile+import clean. NO restart performed (Jack's)."
echo -n "engine now (unchanged until restart): "; systemctl show trading-corp -p MainPID -p ActiveEnterTimestamp --value | tr '\n' ' '; echo
echo -n "settlement.py mtime: "; stat -c '%y' "$LIVE"
rm -f "$STAGE"
echo "=== DONE graft ==="
