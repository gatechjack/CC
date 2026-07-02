# apply.ps1 - bidirectional SFP deploy: drift-gate -> LF-blob install (md5-gated) ->
# config-diff gate + detector assert. Backs up every prod file first (rollback source).
# Aborts on any red gate. Operator paste: powershell -ep bypass -f .\apply.ps1
$ErrorActionPreference = "Stop"
$H = "azureuser@trading.jacksumner.com"
$D = "/home/azureuser/trading_corp"
$TAG = "bak-pre-bidir-2026-07-01"

# touched files -> target LF md5 (installed prod file, CR-stripped, must equal these)
$files = [ordered]@{
  "trading_corp/main.py"                                        = "fda60c98864cd58b6fc75ee215a46e53"
  "trading_corp/agents/divisions/bitunix_sfp_observer.py"       = "1eb85d572674e6a05a6b0fd1ff93ab1f"
  "trading_corp/agents/divisions/bitunix_position_reconciler.py"= "f54665e8335bb76fd28171c94e3a6dc1"
  "trading_corp/agents/divisions/bitunix_sfp_research_log.py"   = "b6b1b4469d11e35d5d2d6a42379b878d"
  "trading_corp/web/sfp_cockpit_view.py"                        = "143773b74ad60818311e9511aa9cecc9"
  "trading_corp/web/templates/sfp_cockpit/_state_board.html"    = "1cce2d72c38902db3f6b9543b2cd95be"
  "config/strategies.yaml"                                      = "a916ade03f13d76a9e168845e86357bc"
}

Write-Host "=== APPLY (bidirectional SFP) ==="
if (-not (Test-Path .\preflight_prod_snapshot.txt)) { Write-Host "ABORT: run preflight.ps1 first"; exit 1 }

# 0) DRIFT-GATE: prod md5s of the EXISTING touched files must equal the preflight
#    snapshot. Exclude the NEW file (research_log -- not on prod yet; md5-gated at
#    install below) so md5sum does not error, AND exclude the detector line from the
#    snapshot filter (the "  (trading_corp|config)/" pattern also matches the detector
#    path bitunix_sfp.py -> would read as phantom drift). Compare 6-vs-6.
$new = "trading_corp/agents/divisions/bitunix_sfp_research_log.py"
$det = "trading_corp/agents/strategies/bitunix_sfp.py"
$existing = @($files.Keys | Where-Object { $_ -ne $new })
$now = (@("cd $D; md5sum $($existing -join ' ')") | ssh $H "tr -d '\r'|bash")
$base = (Get-Content .\preflight_prod_snapshot.txt | Select-String "  (trading_corp|config)/" | ForEach-Object { $_.ToString().Trim() } | Where-Object { $_ -notmatch [regex]::Escape($det) -and $_ -notmatch [regex]::Escape($new) })
$nowN = ($now | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Sort-Object)
$baseN = ($base | Sort-Object)
if (Compare-Object $nowN $baseN) { Write-Host "ABORT: prod DRIFTED since preflight:"; Compare-Object $nowN $baseN; exit 1 }
Write-Host "OK  drift-gate: prod == preflight snapshot (6 existing touched files)"

# 1) INSTALL: backup + scp byte-copy + tr -d CR (LF) + md5-gate == target
foreach ($f in $files.GetEnumerator()) {
  $p = $f.Key; $t = $f.Value
  # source blobs live at the WORKTREE ROOT (one level up from this deploy folder);
  # keep the local path RELATIVE + colon-free ('..\') so Windows scp treats it as a
  # local file (an absolute 'C:\' path would be misread as host:path).
  scp "..\$($p.Replace('/','\'))" "${H}:/tmp/dep_blob"
  $inst = "cd $D; [ -f '$p' ] && cp '$p' '$p.$TAG'; tr -d '\r' < /tmp/dep_blob > '$p'; md5sum '$p' | cut -d' ' -f1"
  $got = ((@($inst) | ssh $H "tr -d '\r'|bash") | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Select-Object -Last 1)
  if ($got -ne $t) { Write-Host "ABORT: $p installed md5 $got != target $t (RUN rollback.ps1)"; exit 1 }
  Write-Host "  OK  installed $p == $t"
}

# 2) CONFIG-DIFF GATE 1: only the bitunix_sfp block changed (lines 1..1928 identical to
#    the .bak) + detector byte-unchanged 91fd7672.
$gate = "cd $D; echo other-divisions:; diff <(sed -n '1,1928p' config/strategies.yaml.$TAG) <(sed -n '1,1928p' config/strategies.yaml) >/dev/null && echo IDENTICAL || echo CHANGED; echo detector:; md5sum trading_corp/agents/strategies/bitunix_sfp.py | cut -d' ' -f1"
$g = (@($gate) | ssh $H "tr -d '\r'|bash")
Write-Host $g
if (($g -join "`n") -notmatch "IDENTICAL") { Write-Host "ABORT: strategies.yaml changed OUTSIDE the bitunix_sfp block"; exit 1 }
if (($g -join "`n") -notmatch "91fd76726364331c8083aaaa68fce199") { Write-Host "ABORT: detector md5 changed"; exit 1 }
Write-Host "=== APPLY COMPLETE (all md5-gated). Config: ONLY bitunix_sfp block changed; detector 91fd7672. Next: restart.ps1 ==="
