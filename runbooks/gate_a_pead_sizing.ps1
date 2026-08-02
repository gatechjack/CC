# gate_a_pead_sizing.ps1 -- READ-ONLY Gate-A drift check for the PEAD derived-sizing build.
# Computes prod LF-md5 per touched file and compares to the prod-live (dafe60b) BASELINE.
# SHARED-file drift (base/robinhood/routes) => STOP. Does NOT deploy or restart.
# Run:  powershell -ep bypass -f .\gate_a_pead_sizing.ps1

$h = "azureuser@trading.jacksumner.com"

$baseline = @{
  "config/strategies.yaml" = "ccde3bf75db3";
  "trading_corp/agents/strategies/pead_sizing.py" = "ABSENT";
  "trading_corp/agents/strategies/pead_strategy.py" = "aec3aeadddfe";
  "trading_corp/brokers/base.py" = "46a3266d5ab0";
  "trading_corp/brokers/robinhood.py" = "8263020088aa";
  "trading_corp/data/earnings_provider.py" = "8dca69ced386";
  "trading_corp/web/pead_view.py" = "43c32c022c87";
  "trading_corp/web/routes.py" = "1083551037f1";
  "trading_corp/web/templates/partials/pead_dial.html" = "ABSENT";
  "trading_corp/web/templates/partials/pead_live_sections.html" = "5ab68dc5340c";
  "trading_corp/web/templates/pead_live.html" = "9924dc61642d"
}
$target = @{
  "config/strategies.yaml" = "274b7e348eb2";
  "trading_corp/agents/strategies/pead_sizing.py" = "88ae944cea3d";
  "trading_corp/agents/strategies/pead_strategy.py" = "ac7c465b15a5";
  "trading_corp/brokers/base.py" = "353bbd1d21ec";
  "trading_corp/brokers/robinhood.py" = "5862d2e8f2c6";
  "trading_corp/data/earnings_provider.py" = "cc6c27185001";
  "trading_corp/web/pead_view.py" = "db2c10a48853";
  "trading_corp/web/routes.py" = "96becb83b19a";
  "trading_corp/web/templates/partials/pead_dial.html" = "d31e3f072508";
  "trading_corp/web/templates/partials/pead_live_sections.html" = "c15419662ee5";
  "trading_corp/web/templates/pead_live.html" = "fb4506d0d901"
}
$shared = @("trading_corp/brokers/base.py","trading_corp/brokers/robinhood.py","trading_corp/web/routes.py")

$cmd = @'
d=$(systemctl show -p WorkingDirectory --value trading-corp 2>/dev/null); [ -d "$d" ] || d="$HOME/cc"; cd "$d" || { echo "APPROOT_FAIL $d"; exit 3; }; echo "APPROOT $d"; for f in config/strategies.yaml trading_corp/agents/strategies/pead_sizing.py trading_corp/agents/strategies/pead_strategy.py trading_corp/brokers/base.py trading_corp/brokers/robinhood.py trading_corp/data/earnings_provider.py trading_corp/web/pead_view.py trading_corp/web/routes.py trading_corp/web/templates/partials/pead_dial.html trading_corp/web/templates/partials/pead_live_sections.html trading_corp/web/templates/pead_live.html; do if [ -f "$f" ]; then hh=$(tr -d "\r" < "$f" | md5sum | cut -c1-12); else hh=ABSENT; fi; echo "$hh $f"; done
'@

Write-Host "Gate-A drift check (read-only) -- prod vs prod-live baseline dafe60b"
$out = $cmd | ssh $h "tr -d '\r'|bash"
$stop = 0
foreach ($ln in $out) {
  $sp = $ln.IndexOf(' ')
  if ($sp -lt 1) { continue }
  $md5 = $ln.Substring(0, $sp)
  $path = $ln.Substring($sp + 1)
  if ($md5 -eq "APPROOT") { Write-Host ("prod app root: " + $path); continue }
  if ($md5 -eq "APPROOT_FAIL") { Write-Host ("APP ROOT NOT FOUND: " + $path); $stop = 1; continue }
  if (-not $baseline.ContainsKey($path)) { Write-Host ("?     " + $path + " (not in manifest)"); continue }
  $b = $baseline[$path]
  $t = $target[$path]
  $isShared = $shared -contains $path
  if ($md5 -eq $b) {
    Write-Host ("OK    " + $path + "  (== baseline, pre-deploy)")
  } elseif ($md5 -eq $t) {
    Write-Host ("DONE  " + $path + "  (== target, already deployed)")
  } elseif ($isShared) {
    $stop = 1
    Write-Host ("STOP  " + $path + "  SHARED DRIFT  prod=" + $md5 + " base=" + $b)
  } else {
    Write-Host ("DRIFT " + $path + "  prod=" + $md5 + " base=" + $b)
  }
}
if ($stop -eq 1) {
  Write-Host "RESULT: STOP -- shared-file drift or missing app root; re-apply additive hunks, do NOT overwrite."
} else {
  Write-Host "RESULT: no shared-file drift detected."
}
