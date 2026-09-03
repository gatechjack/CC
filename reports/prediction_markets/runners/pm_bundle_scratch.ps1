$ErrorActionPreference = "Stop"
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$wt = "C:\Users\AA Incorporado\cc-pm-multicat-wt"
$tar = "C:\Users\AA Incorporado\cc\pm_bundle_overlay.tar"
$sh = Join-Path $PSScriptRoot "pm_bundle_scratch.sh"
$cands = @("$env:SystemRoot\System32\OpenSSH", "$env:SystemRoot\Sysnative\OpenSSH",
           "$env:ProgramFiles\Git\usr\bin", "${env:ProgramFiles(x86)}\Git\usr\bin",
           "$env:LOCALAPPDATA\Programs\Git\usr\bin")
function Find-Exe($name) {
  $c = Get-Command $name -ErrorAction SilentlyContinue; if ($c) { return $c.Source }
  foreach ($d in $cands) { $p = Join-Path $d "$name.exe"; if (Test-Path $p) { return $p } }
  throw "$name not found (STANDING BOX QUIRK #4 -- 32-bit process? try a 64-bit shell)."
}
$ssh = Find-Exe "ssh"; $scp = Find-Exe "scp"
if (Test-Path $tar) { Remove-Item $tar -Force }
# git archive HEAD -> LF tar of the overlay files (archive uses stored blobs = LF, not the autocrlf worktree)
& git -C $wt archive -o $tar HEAD trading_corp/prediction_markets/db.py trading_corp/prediction_markets/execution.py trading_corp/prediction_markets/live_driver.py trading_corp/prediction_markets/driver_roster.py trading_corp/data/ufc_poly_kalshi_match.py tests/prediction_markets
if (-not (Test-Path $tar)) { throw "git archive produced no tar" }
Write-Host ("overlay tar: {0} bytes -> scp to box" -f (Get-Item $tar).Length)
& $scp -o ConnectTimeout=20 $tar ("{0}:/home/azureuser/pm_bundle_overlay.tar" -f $h)
Write-Host "Streaming bundle box-scratch (READ-ONLY; scratch overlay, live tree untouched) to $h ..."
Get-Content -Raw $sh | & $ssh -o ConnectTimeout=20 -o ServerAliveInterval=15 $h "tr -d '\r\357\273\277' | bash"
Remove-Item $tar -Force -ErrorAction SilentlyContinue
Write-Host "---- runner exit ----"
