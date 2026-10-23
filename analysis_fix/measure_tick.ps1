# RO: verify box md5s == base, and measure real manage-tick sweep duration (rung_live ts spread).
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$sh = @'
echo "=== running MACE file md5s (CR-stripped) vs expected ==="
cd /home/azureuser/trading_corp
for f in strategy manager domain execution config; do
  printf "%s  trading_corp/mace/%s.py\n" "$(tr -d '\r' < trading_corp/mace/$f.py | md5sum | cut -c1-8)" "$f"
done
echo "(expected: strategy b30db63f / manager dc04eb60 / domain 8522dc3a / execution 9cc1c165 / config d441957f)"
echo "=== manage-tick sweep measurement (mace_rung_live ts across open rungs) ==="
python3 - <<'PYEOF'
import sqlite3, datetime
con=sqlite3.connect("file:/home/azureuser/trading_corp/data/trading_corp.db?mode=ro",uri=True)
con.row_factory=sqlite3.Row
rows=con.execute("select rung_id,ts from mace_rung_live order by ts").fetchall()
ts=[r["ts"] for r in rows]
print("rung_live rows:", len(rows))
print("earliest ts:", ts[0] if ts else None)
print("latest   ts:", ts[-1] if ts else None)
# parse to datetimes
def p(s):
    return datetime.datetime.fromisoformat(s.replace("Z","+00:00"))
if ts:
    dts=sorted(p(x) for x in ts)
    span=(dts[-1]-dts[0]).total_seconds()
    print("full span (all rungs) sec:", span)
    # cluster the most-recent tick: rungs written within 120s of the latest
    latest=dts[-1]
    recent=[d for d in dts if (latest-d).total_seconds()<=120]
    if recent:
        sweep=(recent[-1]-recent[0]).total_seconds()
        print("most-recent-tick cluster: %d rungs, sweep span %.1f sec" % (len(recent), sweep))
    now=datetime.datetime.now(datetime.timezone.utc)
    print("now UTC:", now.isoformat(), "| since latest write sec:", (now-latest).total_seconds())
# gaps between consecutive ts (to spot tick boundaries ~300s apart)
if len(dts)>1:
    gaps=sorted(round((dts[i+1]-dts[i]).total_seconds(),1) for i in range(len(dts)-1))
    print("consecutive-ts gaps (sorted):", gaps)
PYEOF
echo "DONE"
'@
$sh | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"
