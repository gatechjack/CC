# pk_pm_cron_recheck.ps1 -- READ-ONLY re-check of the first cron fire (03:20 UTC). No mutation.
# Run: powershell -ep bypass -f .\pk_pm_cron_recheck.ps1
$ErrorActionPreference = 'Stop'
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_cronrc_box.sh'
$bash = @'
cd /home/azureuser/trading_corp
echo "-- now UTC --"; date -u
echo "-- MainPID (850993?) --"; systemctl show -p MainPID trading-corp.service 2>/dev/null
echo "-- pm_refresh.log --"
if [ -f /home/azureuser/pm_refresh.log ]; then
  stat -c 'log size=%s mtime=%y' /home/azureuser/pm_refresh.log
  echo "---- FULL LOG ----"; cat /home/azureuser/pm_refresh.log; echo "---- end ----"
else echo "pm_refresh.log STILL ABSENT -> has not fired"; fi
echo "-- pm_whale post-cron integrity --"
venv/bin/python - <<'PY'
import sqlite3, datetime
c=sqlite3.connect("data/prediction_markets.db"); c.row_factory=sqlite3.Row
def ts(v):
    try: return datetime.datetime.fromtimestamp(int(v),datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S') if v else '-'
    except Exception: return str(v)
bad=inc=0; latest=0
for r in c.execute("SELECT wallet,backfill_complete,last_pulled,last_stored,last_refresh_ts FROM pm_whale"):
    if r["last_pulled"]!=r["last_stored"]: bad+=1; print("  MISMATCH",r["wallet"][:14],r["last_pulled"],r["last_stored"])
    if not r["backfill_complete"]: inc+=1
    if r["last_refresh_ts"] and r["last_refresh_ts"]>latest: latest=r["last_refresh_ts"]
print("  whales pulled!=stored=%d incomplete=%d  latest last_refresh_ts=%s"%(bad,inc,ts(latest)))
print("  pm_closed_position TOTAL =", c.execute("SELECT COUNT(*) FROM pm_closed_position").fetchone()[0])
c.close()
PY
'@
$bash = $bash -replace "`r", ""
[IO.File]::WriteAllText($tf, $bash, $enc)
Write-Host "== CRON first-fire RE-CHECK (read-only) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
