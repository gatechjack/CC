# secrev_batch0_baseline.ps1 -- BATCH 0 baselines (rollback record). READ-ONLY ssh-STDIN.
# Captures dpkg -l and the prod-venv pip freeze + current Caddy/Authelia versions as the pre-change baseline.
# No sudo, no write, no restart. Saved into the vault evidence dir.
param([string]$OutDir = 'C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis')
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$enc = New-Object System.Text.UTF8Encoding $false
$hostt = 'azureuser@trading.jacksumner.com'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$shBody = @'
set -u
ROOT=/home/azureuser/trading_corp
echo "===STAMP==="
date -u +"%Y-%m-%dT%H:%M:%SZ"
echo "===VERSIONS==="
dpkg -l caddy 2>/dev/null | awk "/^ii/{print \"deb:caddy \"\$3}"
/usr/bin/caddy version 2>/dev/null | head -1
/usr/local/bin/authelia --version 2>/dev/null | head -1
echo "===DPKG_BASELINE==="
dpkg -l 2>/dev/null | awk "/^ii/{print \$2\" \"\$3}"
echo "===PIP_BASELINE==="
"$ROOT/venv/bin/pip" freeze --disable-pip-version-check 2>/dev/null
echo "===END==="
'@
$sh = Join-Path $env:TEMP 'secrev_batch0_baseline.sh'
[IO.File]::WriteAllText($sh, $shBody, $enc)
Write-Output "Capturing READ-ONLY baselines from $hostt ..."
$res = Get-Content $sh -Raw | ssh -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new $hostt "tr -d '\r\357\273\277'|bash"
Remove-Item $sh -ErrorAction SilentlyContinue
if(-not $res){ Write-Output "ERROR: no output from box."; exit 1 }
$text = ($res -join "`n")

# split sections to files
function Section([string]$all,[string]$begin,[string]$end){
  $lines = $all -split "`n"; $in=$false; $acc=New-Object System.Collections.Generic.List[string]
  foreach($l in $lines){ if($l -match ('^' + [regex]::Escape($begin) + '$')){ $in=$true; continue }; if($l -match ('^' + [regex]::Escape($end) + '$')){ $in=$false; continue }; if($in){ $acc.Add($l.TrimEnd()) } }
  return ($acc -join "`n")
}
$stamp = (Section $text '===STAMP===' '===VERSIONS===').Trim()
$vers  = (Section $text '===VERSIONS===' '===DPKG_BASELINE===').Trim()
$dpkg  = Section $text '===DPKG_BASELINE===' '===PIP_BASELINE==='
$pip   = Section $text '===PIP_BASELINE===' '===END==='
[IO.File]::WriteAllText((Join-Path $OutDir '10_dpkg_baseline.txt'), ("# baseline " + $stamp + "`n" + $dpkg + "`n"), $enc)
[IO.File]::WriteAllText((Join-Path $OutDir '11_pip_baseline.txt'), ("# baseline " + $stamp + "`n" + $pip + "`n"), $enc)
Write-Output ("baseline stamp (box UTC): " + $stamp)
Write-Output "current versions:"
Write-Output $vers
$dpkgN = ($dpkg -split "`n" | Where-Object { $_.Trim() }).Count
$pipN  = ($pip  -split "`n" | Where-Object { $_.Trim() }).Count
Write-Output ("wrote 10_dpkg_baseline.txt ($dpkgN pkgs), 11_pip_baseline.txt ($pipN pkgs)")
Write-Output ("end marker: " + $(if($text -match '===END==='){'PRESENT'}else{'MISSING'}))
