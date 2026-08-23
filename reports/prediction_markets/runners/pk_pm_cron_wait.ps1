# pk_pm_cron_wait.ps1 -- READ-ONLY: wait on-box (bounded) for the cron refresh to finish, then dump the
# final log + post-refresh idempotency. No mutation. Run: powershell -ep bypass -f .\pk_pm_cron_wait.ps1
$ErrorActionPreference = 'Stop'
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_cwait_box.sh'
$bash = @'
cd /home/azureuser/trading_corp
FIN="no"
for i in $(seq 1 24); do
  if ! pgrep -f 'pm_cli.py refresh' >/dev/null 2>&1; then FIN="yes(~$((i*10))s waited)"; break; fi
  sleep 10
done
echo "-- now UTC --"; date -u
echo "-- refresh finished? --"; echo "$FIN"
pgrep -f 'pm_cli.py refresh' >/dev/null 2>&1 && echo "(STILL RUNNING at wait-timeout)" || echo "(process gone)"
echo "-- MainPID (850993?) --"; systemctl show -p MainPID trading-corp.service 2>/dev/null
echo "-- pm_refresh.log --"; stat -c 'size=%s mtime=%y' /home/azureuser/pm_refresh.log 2>/dev/null
echo "---- FULL LOG ----"; cat /home/azureuser/pm_refresh.log 2>/dev/null; echo "---- end ----"
echo "-- post-refresh integrity --"
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
print("  per-wallet (name,rows,pulled,stored,complete):")
for r in c.execute("SELECT w.user_name,p.wallet,COUNT(*) n,w.last_pulled,w.last_stored,w.backfill_complete FROM pm_closed_position p LEFT JOIN pm_whale w ON p.wallet=w.wallet GROUP BY p.wallet ORDER BY n DESC"):
    print("    %-13s rows=%-6d pulled=%-6s stored=%-6s complete=%s"%((r["user_name"] or "?")[:13],r["n"],r["last_pulled"],r["last_stored"],r["backfill_complete"]))
c.close()
PY
'@
$bash = $bash -replace "`r", ""
[IO.File]::WriteAllText($tf, $bash, $enc)
Write-Host "== WAIT for cron refresh to finish, then verify (read-only) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
