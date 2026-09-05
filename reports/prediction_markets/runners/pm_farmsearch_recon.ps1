$ErrorActionPreference = "Stop"
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$sh = Join-Path $PSScriptRoot "pm_farmsearch_recon.sh"
$boxapp = "C:\Users\AA Incorporado\cc\_box_app_farmsearch.py"
$cands = @("$env:SystemRoot\System32\OpenSSH", "$env:SystemRoot\Sysnative\OpenSSH",
           "$env:ProgramFiles\Git\usr\bin", "${env:ProgramFiles(x86)}\Git\usr\bin",
           "$env:LOCALAPPDATA\Programs\Git\usr\bin")
function Find-Exe($name) {
  $c = Get-Command $name -ErrorAction SilentlyContinue; if ($c) { return $c.Source }
  foreach ($d in $cands) { $p = Join-Path $d "$name.exe"; if (Test-Path $p) { return $p } }
  throw "$name not found (try a 64-bit shell)."
}
$ssh = Find-Exe "ssh"; $scp = Find-Exe "scp"
# pull the LIVE box app.py DOWN (read-only) so the graft can be built + verified locally
$remote = "{0}:/home/azureuser/trading_corp/trading_corp/prediction_markets/web/app.py" -f $h
& $scp -o ConnectTimeout=20 $remote $boxapp
Write-Host ("box app.py -> {0} ({1} bytes)" -f $boxapp, (Get-Item $boxapp).Length)
Write-Host "Streaming recon report (READ-ONLY) from $h ..."
Get-Content -Raw $sh | & $ssh -o ConnectTimeout=20 -o ServerAliveInterval=15 $h "tr -d '\r\357\273\277' | bash"
Write-Host "---- recon exit ----"
