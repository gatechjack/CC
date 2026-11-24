# secrev_batch1_verify.ps1 -- BATCH 1 post-verify. READ-ONLY (ssh health + PC-side curl). No writes.
param([string]$OutDir = 'C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis')
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$enc = New-Object System.Text.UTF8Encoding $false
$hostt = 'azureuser@trading.jacksumner.com'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$log = New-Object System.Collections.Generic.List[string]
function L([string]$s){ $log.Add($s); Write-Output $s }

L "=== box: versions + service health (read-only) ==="
$sh = @'
echo "caddy=$(/usr/bin/caddy version 2>/dev/null | head -1)"
echo "authelia=$(/usr/local/bin/authelia --version 2>/dev/null | head -1)"
echo "svc caddy=$(systemctl is-active caddy) authelia=$(systemctl is-active authelia) trading-corp=$(systemctl is-active trading-corp)"
echo "authelia errors(last200)=$(journalctl -u authelia -n 200 --no-pager 2>/dev/null | grep -c 'level=error')"
echo "newest batch1 backup: $(ls -dt /home/azureuser/secrev_batch1_backup_* 2>/dev/null | head -1)"
'@
$res = $sh | ssh -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new $hostt "tr -d '\r\357\273\277'|bash"
foreach($line in ($res -split "`n")){ if($line.Trim()){ L ("  " + $line.Trim()) } }

$curl = (Get-Command curl.exe -ErrorAction SilentlyContinue).Source
if($curl){
  L "=== PC curl: dashboard redirects to Authelia when unauthenticated (expect 302) ==="
  $d = & $curl -sS -o NUL -D - --max-time 12 https://trading.jacksumner.com/ 2>&1 | Select-Object -First 1
  L ("  / -> " + ($d -join ' '))
  $loc = & $curl -sS -o NUL -D - --max-time 12 https://trading.jacksumner.com/ 2>&1 | Select-String -Pattern '^location:' | Select-Object -First 1
  if($loc){ L ("  " + ($loc.ToString().Trim())) }
  L "=== PC curl: webhook path still reachable (GET -> expect 405/404, NOT 502/refused) ==="
  $w = & $curl -sS -o NUL -D - --max-time 12 https://trading.jacksumner.com/webhook/tradingview/lord-otter 2>&1 | Select-Object -First 1
  L ("  GET /webhook/tradingview/lord-otter -> " + ($w -join ' '))
  L "=== PC curl: malformed Host still rejected at edge (expect 400) ==="
  $h = & $curl -sS -o NUL -D - --max-time 12 -H "Host: trading.jacksumner.com/x" https://trading.jacksumner.com/ 2>&1 | Select-Object -First 1
  L ("  malformed-Host -> " + ($h -join ' '))
} else { L "curl.exe not found -- skip PC-side HTTP checks" }

[IO.File]::WriteAllText((Join-Path $OutDir '12_batch1_verify.txt'), (($log -join "`n") + "`n"), $enc)
L ""
L "NEXT: Jack logs in once at https://trading.jacksumner.com to confirm password+TOTP (2FA) works end-to-end."
