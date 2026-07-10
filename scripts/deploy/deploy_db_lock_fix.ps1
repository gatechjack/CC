# deploy_db_lock_fix.ps1  --  PROPOSAL / operator-run deploy runner (NOT auto-run here).
#
# Ships the DB lock-storm fix (branch db-lock-contention-fix-2026-07-10) to prod:
#   trading_corp/persistence/checkpointer.py   (checkpointer isolated onto its own DB + PRAGMAs)
#   trading_corp/persistence/db.py             (shared conn: synchronous=NORMAL)
#   trading_corp/main.py                        (call site -> checkpoint_db_path(db_path))
#
# Transport: az vm run-command (root) with gzip+base64 payloads (CR stripped -> LF-only on
# prod; this IS the CRLF-trap solution, stronger than autocrlf=false). md5-verified each file.
#
# TWO PHASES (do NOT auto-restart; operator triggers phase 2 on a chosen quiet window):
#   powershell -ep bypass -f scripts\deploy\deploy_db_lock_fix.ps1
#        Phase 1 TRANSFER: pre-flights + backup + upload + md5-verify. NO restart.
#   powershell -ep bypass -f scripts\deploy\deploy_db_lock_fix.ps1 -Restart
#        Phase 2 RESTART: RH-pickle hard gate + re-verify staged + restart + post-restart verify.
#   ... -Restart -SkipPickleGate   (override the pickle gate ONLY if you confirmed it is valid)
#
# Prod is NOT a git repo -> the "dirty-state guard" is a per-file baseline md5 match: prod must be
# at the origin/main baseline (fresh) OR already at target (idempotent re-run); anything else aborts.
#
# Expected content md5 (CR-stripped):
#   baseline (origin/main d9c32de, == prod on 2026-07-10):
#     checkpointer 953ce717d5b5a797ac12ac975002323f
#     db           9cb0f65485b976d6b39f6005d02dfd2d
#     main         b741e95f73f658e6294e94cc3056e644
#   target (this branch HEAD):
#     checkpointer f33b896ea0e6545c84357b7b1a785f71
#     db           bc3df1c8faac8fc395bcd9cadf0f121f
#     main         b80dc6ceb7a241e3ce2ae754f4de231a
# (target md5s are recomputed from the working tree at run time and enforced on prod.)

param(
    [switch]$Restart,
    [switch]$SkipPickleGate,
    [switch]$DryRun      # generate + preview the exact prod bash; do NOT invoke az (no prod contact)
)

$ErrorActionPreference = "Stop"
$VM = "tc-prod-vm"
$RG = "rg-shared-prod"
$BACKDATE = (Get-Date -Format "yyyyMMdd")
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

# repo-relative path (same layout on prod under /home/azureuser/trading_corp/)
$FILES = @(
    "trading_corp/persistence/checkpointer.py",
    "trading_corp/persistence/db.py",
    "trading_corp/main.py"
)
$BASELINE = @{
    "trading_corp/persistence/checkpointer.py" = "953ce717d5b5a797ac12ac975002323f"
    "trading_corp/persistence/db.py"           = "9cb0f65485b976d6b39f6005d02dfd2d"
    "trading_corp/main.py"                      = "b741e95f73f658e6294e94cc3056e644"
}

function Get-LFBytes($path) {
    $bytes = [System.IO.File]::ReadAllBytes($path)
    return [byte[]]@($bytes | Where-Object { $_ -ne 13 })
}
function MD5-LF($path) {
    $lf = Get-LFBytes $path
    $md5 = [System.Security.Cryptography.MD5]::Create()
    $hash = $md5.ComputeHash($lf)
    return ($hash | ForEach-Object { $_.ToString("x2") }) -join ""
}
function GzB64-LF($path) {
    $lf = Get-LFBytes $path
    $ms = New-Object System.IO.MemoryStream
    $gz = New-Object System.IO.Compression.GzipStream($ms, [System.IO.Compression.CompressionMode]::Compress)
    $gz.Write($lf, 0, $lf.Length)
    $gz.Close()
    return [Convert]::ToBase64String($ms.ToArray())
}

# Compute per-file target md5 (+ gz-b64 for phase 1) from the working tree.
$targetMD5 = @{}
$b64 = @{}
foreach ($f in $FILES) {
    $p = Join-Path $RepoRoot ($f -replace "/", "\")
    if (-not (Test-Path $p)) { throw "Missing repo file: $p" }
    $targetMD5[$f] = MD5-LF $p
    if (-not $Restart) { $b64[$f] = GzB64-LF $p }
    Write-Host ("  {0}  target-md5(LF)={1}" -f $f, $targetMD5[$f])
}

if (-not $Restart) {
    # ---------- PHASE 1: TRANSFER ----------
    $blocks = ""
    foreach ($f in $FILES) {
        $tmpl = @'
echo "=== __F__ ==="
CUR=$(tr -d '\r' < "__F__" | md5sum | cut -d' ' -f1)
if [ "$CUR" = "__TGT__" ]; then
  echo "  already at target ($CUR); skipping upload"
else
  if [ "$CUR" != "__BASE__" ]; then
    echo "  ABORT: prod md5 $CUR is neither baseline(__BASE__) nor target(__TGT__) -- prod drifted; investigate before deploy"
    exit 3
  fi
  BK="__F__.pre-db-lock-fix-__DATE__"
  if [ ! -e "$BK" ]; then cp -p "__F__" "$BK" && echo "  backup -> $BK"; else echo "  backup kept (exists): $BK"; fi
  echo "__B64__" | base64 -d | gunzip > /tmp/dbfix_upload.new
  NEW=$(md5sum /tmp/dbfix_upload.new | cut -d' ' -f1)
  if [ "$NEW" != "__TGT__" ]; then echo "  ABORT: uploaded md5 $NEW != target __TGT__"; rm -f /tmp/dbfix_upload.new; exit 4; fi
  mv /tmp/dbfix_upload.new "__F__"
  chown azureuser:azureuser "__F__"
  chmod 644 "__F__"
  echo "  landed ($NEW)"
fi
POST=$(tr -d '\r' < "__F__" | md5sum | cut -d' ' -f1)
if [ "$POST" != "__TGT__" ]; then echo "  ABORT: post-verify $POST != target __TGT__"; exit 5; fi
echo "  VERIFIED __F__ = $POST"
'@
        $tmpl = $tmpl.Replace("__F__", $f).Replace("__TGT__", $targetMD5[$f]).Replace("__BASE__", $BASELINE[$f]).Replace("__DATE__", $BACKDATE).Replace("__B64__", $b64[$f])
        $blocks += $tmpl + "`n"
    }

    $remote = @'
#!/bin/bash
set -e
BASE=/home/azureuser/trading_corp
cd "$BASE"
echo "########## DB-LOCK FIX -- PHASE 1: TRANSFER (no restart) ##########"
echo "host=$(hostname) time=$(date -u) backdate=__DATE__"

echo "=== PRE-FLIGHT: RH pickle age (informational; hard gate is in -Restart) ==="
PK=/home/azureuser/.tokens/robinhood.pickle
if [ -f "$PK" ]; then
  AGE=$(( ( $(date +%s) - $(stat -c %Y "$PK") ) / 3600 ))
  echo "  pickle mtime=$(stat -c %y "$PK") age=${AGE}h (refresh via rh_pickle_refresh.ps1 if >20h BEFORE -Restart)"
else
  echo "  WARN: pickle $PK not found"
fi

echo "=== PRE-FLIGHT: audit-drain cron present (defense-in-depth if the fix regresses) ==="
if crontab -u azureuser -l 2>/dev/null | grep -q replay_audit_event_write_failed; then
  echo "  OK: hourly audit-drain cron installed"
else
  echo "  WARN: audit-drain cron MISSING -- reinstall it; the fallback would silently re-accumulate"
fi

echo "=== PRE-FLIGHT + TRANSFER: dirty-state guard = per-file baseline md5 (prod is not git) ==="
__BLOCKS__
echo ""
echo "########## PHASE 1 COMPLETE: 3 files staged + md5-verified; backups at *.pre-db-lock-fix-__DATE__ ##########"
echo "NOT restarted. Activate on a quiet window AFTER rh_pickle_refresh.ps1 (if pickle >20h):"
echo "   powershell -ep bypass -f scripts\deploy\deploy_db_lock_fix.ps1 -Restart"
'@
    $remote = $remote.Replace("__BLOCKS__", $blocks).Replace("__DATE__", $BACKDATE)
    $phase = "transfer"
}
else {
    # ---------- PHASE 2: RESTART + VERIFY ----------
    $verify = ""
    foreach ($f in $FILES) {
        $vt = @'
V=$(tr -d '\r' < "__F__" | md5sum | cut -d' ' -f1)
if [ "$V" != "__TGT__" ]; then echo "  ABORT: __F__ md5 $V != target __TGT__ -- run phase 1 (transfer) first"; exit 11; fi
echo "  staged OK: __F__ = $V"
'@
        $verify += $vt.Replace("__F__", $f).Replace("__TGT__", $targetMD5[$f]) + "`n"
    }
    $gate = if ($SkipPickleGate) { "0" } else { "1" }

    $remote = @'
#!/bin/bash
set -e
BASE=/home/azureuser/trading_corp
cd "$BASE"
echo "########## DB-LOCK FIX -- PHASE 2: RESTART + VERIFY ##########"
echo "host=$(hostname) time=$(date -u)"

echo "=== HARD GATE: RH pickle age (boot-hang hazard) ==="
PK=/home/azureuser/.tokens/robinhood.pickle
GATE=__GATE__
if [ -f "$PK" ]; then
  AGE=$(( ( $(date +%s) - $(stat -c %Y "$PK") ) / 3600 ))
  echo "  pickle age=${AGE}h (mtime $(stat -c %y "$PK"))"
  if [ "$GATE" = "1" ] && [ "$AGE" -gt 20 ]; then
    echo "  ABORT: pickle is ${AGE}h old (>20h). Run rh_pickle_refresh.ps1 (2FA) first, OR"
    echo "         re-run with -SkipPickleGate if you have confirmed it is still valid."
    exit 10
  fi
else
  echo "  WARN: pickle not found; proceeding (boot may re-auth)"
fi

echo "=== GATE: confirm the 3 files are staged at target md5 (phase 1 ran) ==="
__VERIFY__
echo "=== RESTART trading-corp.service ==="
systemctl is-active trading-corp.service >/dev/null 2>&1 && echo "  (was active)" || echo "  (was not active)"
systemctl restart trading-corp.service
sleep 8
echo "  is-active: $(systemctl is-active trading-corp.service)  at $(date -u)"

echo "=== POST (a): shared-DB PRAGMAs via the APP's own connect() ==="
echo "    (per-connection PRAGMAs like synchronous/busy_timeout are NOT visible to a plain sqlite3 CLI --"
echo "     a CLI would show its own defaults; the app's connect() shows the real values it uses.)"
venv/bin/python - <<'PYEOF'
import sys
sys.path.insert(0, "/home/azureuser/trading_corp")
from trading_corp.persistence import db
with db.connect("sqlite:////home/azureuser/trading_corp/data/trading_corp.db") as c:
    jm = c.execute("PRAGMA journal_mode").fetchone()[0]
    sy = c.execute("PRAGMA synchronous").fetchone()[0]
    bt = c.execute("PRAGMA busy_timeout").fetchone()[0]
print(f"  shared: journal_mode={jm} synchronous={sy}(1=NORMAL) busy_timeout={bt}")
assert str(jm).lower() == "wal", "shared journal_mode not wal"
assert int(sy) == 1, "shared synchronous not NORMAL(1)"
assert int(bt) == 5000, "shared busy_timeout not 5000 (5s is BY DESIGN pending fix #5)"
print("  OK shared: wal / NORMAL / busy_timeout=5000 (5s deliberate)")
PYEOF

echo "=== POST (b/c): checkpointer's OWN file exists, WAL, recent mtime ==="
if [ -f data/checkpoints.db ]; then
  ls -la data/checkpoints.db data/checkpoints.db-wal data/checkpoints.db-shm 2>/dev/null || ls -la data/checkpoints.db
  CKM=$(sqlite3 -readonly data/checkpoints.db "PRAGMA journal_mode;")
  echo "  checkpoints.db journal_mode=$CKM"
  if [ "$CKM" = "wal" ]; then
    echo "  OK: WAL on a fresh file PROVES make_checkpointer's PRAGMA block ran (a default aiosqlite"
    echo "      connection leaves a new file in rollback mode) -> busy_timeout=30000 + synchronous=NORMAL"
    echo "      ran on the same connection (per-connection, not externally readable; also pinned by checkpointer.py md5)."
  else
    echo "  WARN: checkpoints.db not WAL -- investigate make_checkpointer"
  fi
else
  echo "  NOTE: data/checkpoints.db absent right after boot. It is created when make_checkpointer opens"
  echo "        (main.py:1103) -- confirm no import error in the journal, or let one order flow through."
fi

echo "=== POST (d): boot cleanliness (last 90s) ==="
LOCK=$(journalctl -u trading-corp.service --since "90 seconds ago" --no-pager | grep -c "database is locked" || true)
echo "  'database is locked' since boot: $LOCK (expect 0)"
echo "  --- tracebacks/errors since boot (fidelity paper-broker noise filtered; expect none) ---"
journalctl -u trading-corp.service --since "90 seconds ago" --no-pager \
  | grep -iE "Traceback|OperationalError|CRITICAL| ERROR " \
  | grep -viE "broker connect failed for division=fidelity|Fidelity shared session" \
  | head -15 || true
echo "  (if only fidelity paper-broker lines were filtered, boot is clean)"

echo ""
echo "########## PHASE 2 COMPLETE -- see the report for the 72h verification gate ##########"
'@
    $remote = $remote.Replace("__VERIFY__", $verify).Replace("__GATE__", $gate)
    $phase = "restart"
}

# --- invoke via az vm run-command (root), capturing output ---
$deployFile = Join-Path $env:TEMP ("deploy_db_lock_" + $phase + ".sh")
[System.IO.File]::WriteAllText($deployFile, $remote.Replace("`r`n", "`n"))
Write-Host ("Phase={0}  script bytes={1}  ({2})" -f $phase, (Get-Item $deployFile).Length, $deployFile)
if ($DryRun) {
    $leftover = [regex]::Matches($remote, '__[A-Z0-9_]+__') | ForEach-Object { $_.Value } | Sort-Object -Unique
    if ($leftover) { Write-Host "LEFTOVER TOKENS (BUG): $($leftover -join ', ')" } else { Write-Host "token-check OK: no unreplaced placeholders" }
    Write-Host "DRY-RUN: generated $deployFile for phase '$phase'. az was NOT invoked (no prod contact)."
    Write-Host "----- HEAD (22) -----"; Get-Content $deployFile -TotalCount 22 | ForEach-Object { Write-Host $_ }
    Write-Host "----- TAIL (12) -----"; Get-Content $deployFile -Tail 12 | ForEach-Object { Write-Host $_ }
    return
}
Write-Host "Invoking az vm run-command (60-90s)..."
$out = Join-Path $env:TEMP ("deploy_db_lock_" + $phase + "_out.txt")
az vm run-command invoke -n $VM -g $RG --command-id RunShellScript --scripts "@$deployFile" --query "value[0].message" -o tsv | Out-File -FilePath $out -Encoding utf8
Get-Content $out -Encoding utf8

Write-Host ""
if (-not $Restart) {
    Write-Host "NEXT: review output above. If clean, restart on a quiet window (pickle refreshed):"
    Write-Host "   powershell -ep bypass -f scripts\deploy\deploy_db_lock_fix.ps1 -Restart"
} else {
    Write-Host "DONE. Start the 72h fallback-file watch (see report / deploy_log template)."
}
