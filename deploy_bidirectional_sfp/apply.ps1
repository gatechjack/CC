# apply.ps1 - bidirectional SFP deploy (TARGETED-HUNK edition). Prod DIVERGES from the
# branch base, so file-copy is WRONG for two files:
#   * main.py       - prod has a Kalshi leg_priced fix (not in base) + is CRLF
#   * strategies.yaml - prod has polymarket lines (not in base) + is CRLF
# For those two we install PREBUILT CRLF HYBRIDS = prod's exact bytes with ONLY the
# bitunix_sfp hunk swapped in (built by build_hybrids.py, verified by verify_hybrids.py),
# byte-exact (NO tr) to preserve prod's CRLF + prod-only content. The other 5 files are
# prod==base, installed from the worktree CR-stripped to LF.
# Flow: drift-gate -> per-file install (md5-gated) -> config-diff + detector gate. Backs up
# every prod file first. Operator paste: powershell -ep bypass -f .\apply.ps1
$ErrorActionPreference = "Stop"
$H = "azureuser@trading.jacksumner.com"
$D = "/home/azureuser/trading_corp"
$TAG = "bak-pre-bidir-2026-07-01"

# prod path -> @{ src=<local source>; md5=<installed target md5>; mode='lf'|'raw' }
#   lf  = worktree file, CR-stripped to LF on install (prod==base for these)
#   raw = prebuilt CRLF hybrid, byte-exact install (prod diverged; preserve CRLF+content)
$files = [ordered]@{
  "trading_corp/main.py"                                         = @{ src=".\hybrids\main_hybrid.py"; md5="d0d382cbffcb6ebfbf50372fcc9175dd"; mode="raw" }
  "trading_corp/agents/divisions/bitunix_sfp_observer.py"        = @{ src="..\trading_corp\agents\divisions\bitunix_sfp_observer.py"; md5="1eb85d572674e6a05a6b0fd1ff93ab1f"; mode="lf" }
  "trading_corp/agents/divisions/bitunix_position_reconciler.py" = @{ src="..\trading_corp\agents\divisions\bitunix_position_reconciler.py"; md5="f54665e8335bb76fd28171c94e3a6dc1"; mode="lf" }
  "trading_corp/agents/divisions/bitunix_sfp_research_log.py"    = @{ src="..\trading_corp\agents\divisions\bitunix_sfp_research_log.py"; md5="b6b1b4469d11e35d5d2d6a42379b878d"; mode="lf" }
  "trading_corp/web/sfp_cockpit_view.py"                         = @{ src="..\trading_corp\web\sfp_cockpit_view.py"; md5="143773b74ad60818311e9511aa9cecc9"; mode="lf" }
  "trading_corp/web/templates/sfp_cockpit/_state_board.html"     = @{ src="..\trading_corp\web\templates\sfp_cockpit\_state_board.html"; md5="1cce2d72c38902db3f6b9543b2cd95be"; mode="lf" }
  "config/strategies.yaml"                                       = @{ src=".\hybrids\strat_hybrid.yaml"; md5="12fd6c3f67fe2ec48a59009c7d855679"; mode="raw" }
}

Write-Host "=== APPLY (bidirectional SFP, targeted-hunk) ==="
if (-not (Test-Path .\preflight_prod_snapshot.txt)) { Write-Host "ABORT: run preflight.ps1 first"; exit 1 }

# 0) DRIFT-GATE: prod md5s of the EXISTING touched files must equal the preflight snapshot.
#    Exclude the NEW file (research_log, not on prod) so md5sum does not error, and exclude
#    the detector line from $base (the snapshot filter also matches it). Compare 6-vs-6.
$new = "trading_corp/agents/divisions/bitunix_sfp_research_log.py"
$det = "trading_corp/agents/strategies/bitunix_sfp.py"
$existing = @($files.Keys | Where-Object { $_ -ne $new })
$now = (@("cd $D; md5sum $($existing -join ' ')") | ssh $H "tr -d '\r'|bash")
$base = (Get-Content .\preflight_prod_snapshot.txt | Select-String "  (trading_corp|config)/" | ForEach-Object { $_.ToString().Trim() } | Where-Object { $_ -notmatch [regex]::Escape($det) -and $_ -notmatch [regex]::Escape($new) })
$nowN = ($now | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Sort-Object)
$baseN = ($base | Sort-Object)
if (Compare-Object $nowN $baseN) { Write-Host "ABORT: prod DRIFTED since preflight:"; Compare-Object $nowN $baseN; exit 1 }
Write-Host "OK  drift-gate: prod == preflight snapshot (6 existing touched files)"

# 1) INSTALL each file: backup + scp + (LF-strip | byte-exact) + md5-gate == target
foreach ($f in $files.GetEnumerator()) {
  $p = $f.Key; $t = $f.Value.md5; $src = $f.Value.src; $mode = $f.Value.mode
  if (-not (Test-Path $src)) { Write-Host "ABORT: local source missing: $src (run build_hybrids.py?)"; exit 1 }
  scp $src "${H}:/tmp/dep_blob"
  if ($mode -eq "raw") {
    $inst = "cd $D; [ -f '$p' ] && cp '$p' '$p.$TAG'; cp /tmp/dep_blob '$p'; md5sum '$p' | cut -d' ' -f1"
  } else {
    $inst = "cd $D; [ -f '$p' ] && cp '$p' '$p.$TAG'; tr -d '\r' < /tmp/dep_blob > '$p'; md5sum '$p' | cut -d' ' -f1"
  }
  $got = ((@($inst) | ssh $H "tr -d '\r'|bash") | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Select-Object -Last 1)
  if ($got -ne $t) { Write-Host "ABORT: $p installed md5 $got != target $t (RUN restore_noreboot.ps1)"; exit 1 }
  Write-Host "  OK  installed $p ($mode) == $t"
}

# 2) CONFIG-DIFF GATE: only the bitunix_sfp block changed (lines 1..1928 identical to the
#    .bak) + detector byte-unchanged. The CRLF hybrid preserves prod's pre-block bytes, so
#    lines 1..1928 are byte-identical to the .bak (block sits at ~1935 on prod).
$gate = "cd $D; echo other-divisions:; diff <(sed -n '1,1928p' config/strategies.yaml.$TAG) <(sed -n '1,1928p' config/strategies.yaml) >/dev/null && echo IDENTICAL || echo CHANGED; echo detector:; md5sum trading_corp/agents/strategies/bitunix_sfp.py | cut -d' ' -f1"
$g = (@($gate) | ssh $H "tr -d '\r'|bash")
Write-Host $g
if (($g -join "`n") -notmatch "IDENTICAL") { Write-Host "ABORT: strategies.yaml changed OUTSIDE the bitunix_sfp block"; exit 1 }
if (($g -join "`n") -notmatch "91fd76726364331c8083aaaa68fce199") { Write-Host "ABORT: detector md5 changed"; exit 1 }
Write-Host "=== APPLY COMPLETE (all md5-gated). main.py+strategies.yaml = targeted CRLF hybrids; only the bitunix_sfp block changed; detector 91fd7672. Next: restart.ps1 ==="
