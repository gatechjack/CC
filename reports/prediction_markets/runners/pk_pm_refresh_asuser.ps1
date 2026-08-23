# pk_pm_refresh_asuser.ps1 -- ITEM 1b: re-run the refresh AS AZUREUSER (runuser; the exact cron identity, NOT
# root) to PROVE the cron write path now works, then snapshot AFTER + idempotency. Read-only after the refresh.
# Run: powershell -ep bypass -f .\pk_pm_refresh_asuser.ps1   (intended to run in background; ~15-18 min)
$ErrorActionPreference = 'Stop'
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_refasuser_box.sh'
$bash = @'
cd /home/azureuser/trading_corp
echo "=== MainPID before ==="; systemctl show -p MainPID trading-corp.service 2>/dev/null
echo "=== refresh AS azureuser (runuser -u azureuser; mimics the cron) START ==="; date -u
runuser -u azureuser -- bash -c 'cd /home/azureuser/trading_corp && PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py refresh --cap 50000'
RC=$?
echo "REFRESH_EXIT=$RC"; date -u
echo "=== ownership AFTER refresh (db + any sidecars must be azureuser) ==="
ls -l data/prediction_markets.db data/prediction_markets.db-wal data/prediction_markets.db-shm 2>&1
echo "=== AFTER snapshot + idempotency (BEFORE was: total=28303, cs_updated_ts=1787452231, refresh_ts=1787452230) ==="
venv/bin/python - <<'PY'
import sqlite3
BEFORE={"0xa6a856a8c8a7":17056,"0xc3e550fae1c9":2673,"0x6dd6314d1670":2651,"0xd1acd3925d89":1803,
 "0x2fb0f88ef5ba":1675,"0x1f7105a18d9f":759,"0x16bb9951a36f":512,"0x71edffd0d70a":369,
 "0x52f454c43b23":304,"0x75e091ca3f8e":215,"0x2dc13c6bda81":201,"0x99b1b05948d6":85}
c=sqlite3.connect("file:data/prediction_markets.db?mode=ro",uri=True); c.row_factory=sqlite3.Row
print("closed_total AFTER =", c.execute("SELECT COUNT(*) FROM pm_closed_position").fetchone()[0], "(BEFORE 28303)")
print("category_stats_MAX_updated_ts AFTER =", c.execute("SELECT MAX(updated_ts) FROM pm_category_stats").fetchone()[0], "(BEFORE 1787452231 -- MUST ADVANCE)")
print("whale_MAX_last_refresh_ts AFTER =", c.execute("SELECT MAX(last_refresh_ts) FROM pm_whale").fetchone()[0], "(BEFORE 1787452230 -- MUST ADVANCE)")
bad=inc=0
for r in c.execute("SELECT wallet,backfill_complete,last_pulled,last_stored FROM pm_whale"):
    if r["last_pulled"]!=r["last_stored"]: bad+=1; print("  PULLED!=STORED",r["wallet"][:14],r["last_pulled"],r["last_stored"])
    if not r["backfill_complete"]: inc+=1
print("  pulled!=stored=%d  incomplete=%d"%(bad,inc))
print("per-wallet AFTER vs BEFORE (delta; live MLB 0x16bb/0x2dc1 may drift, rest MUST match):")
for r in c.execute("SELECT wallet,COUNT(*) n FROM pm_closed_position GROUP BY wallet ORDER BY n DESC"):
    w=r["wallet"][:14]; b=BEFORE.get(w,"?"); d=(r["n"]-b) if isinstance(b,int) else "?"
    print("   %-14s after=%-6d before=%-6s delta=%s"%(w,r["n"],b,("%+d"%d if isinstance(d,int) else d)))
c.close()
PY
echo "=== MainPID after (850993) ==="; systemctl show -p MainPID trading-corp.service 2>/dev/null
echo "=== legacy DB untouched ==="; ls -l data/trading_corp.db
echo "ITEM1B_DONE"
'@
$bash = $bash -replace "`r", ""
[IO.File]::WriteAllText($tf, $bash, $enc)
Write-Host "== ITEM 1b: refresh AS AZUREUSER + AFTER snapshot (long-running) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
