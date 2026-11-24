# secrev_box_probe.ps1 -- PHASE 1 READ-ONLY security assessment probe of tc-prod-vm.
# Runs ONE ssh-STDIN probe (proven vm_check_manual.ps1 channel): no sudo, no write, no restart,
# no apt-get update, no pip install. Never reads .env, ~/.ssh keys, Key Vault, or Environment= lines.
# Output is REDACTED (00 Meta/Conventions.md rule set) BEFORE being written to disk.
# Writes: <OutDir>\01_box_probe_redacted.txt and <OutDir>\02_requirements_box.txt (pip freeze block).
param([string]$OutDir = 'C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis')
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$enc = New-Object System.Text.UTF8Encoding $false
$hostt = 'azureuser@trading.jacksumner.com'

# ----- FULL redaction (copied from 00 Meta/vm_check_manual.ps1; Conventions.md). Never records a value. -----
function Redact-All([string]$t){
  $hits = @{}
  $pemRe = '-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----'
  $pm = [regex]::Matches($t, $pemRe, [System.Text.RegularExpressions.RegexOptions]::Singleline)
  if($pm.Count){ $hits['pem'] = $pm.Count; $t = [regex]::Replace($t, $pemRe, '[REDACTED:pem]', [System.Text.RegularExpressions.RegexOptions]::Singleline) }
  $simple = @(
    @{t='anthropic_key'; re='sk-ant-[A-Za-z0-9_\-]{12,}'},
    @{t='openai_key';    re='sk-(proj-)?[A-Za-z0-9]{32,}'},
    @{t='xai_key';       re='xai-[A-Za-z0-9]{16,}'},
    @{t='jwt';           re='eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{6,}'},
    @{t='bearer';        re='(?i)bearer\s+[A-Za-z0-9._\-]{20,}'},
    @{t='conn';          re='(?i)(postgres(ql)?|mysql|mongodb|redis|amqp)://[^:/\s]+:[^@/\s]+@'},
    @{t='account';       re='\b(116637293063|680725082|934310442|461391328)\b'},
    @{t='wallet';        re='0x[a-fA-F0-9]{40}\b'}
  )
  foreach($p in $simple){
    $mm = [regex]::Matches($t, $p.re)
    if($mm.Count){ $hits[$p.t] = ([int]$hits[$p.t]) + $mm.Count; $t = [regex]::Replace($t, $p.re, ('[REDACTED:' + $p.t + ']')) }
  }
  $assignRe = '(?i)\b(password|passwd|secret|api[_-]?key|apikey|access[_-]?token|auth[_-]?token|client[_-]?secret|private[_-]?key|token|key)\b\s*[:=]\s*["'']?([^\s"''<>]{8,})'
  $seen = New-Object System.Collections.Generic.HashSet[string]
  $ac = 0
  foreach($m in [regex]::Matches($t, $assignRe)){
    $val = $m.Groups[2].Value
    if($val -match '^\[REDACTED:'){ continue }
    if($val -cmatch '^[A-Z][A-Z0-9_]+$'){ continue }
    if($val -match '^[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)+$'){ continue }
    if($val -match '^(your|example|xxx|changeme|placeholder|none|null|true|false)$'){ continue }
    if($val -match '^[/\\~]'){ continue }
    if($val -notmatch '[A-Za-z]'){ continue }
    if($seen.Contains($m.Value)){ continue }
    [void]$seen.Add($m.Value)
    $t = $t.Replace($m.Value, ($m.Groups[1].Value + '=[REDACTED:secret]'))
    $ac++
  }
  if($ac){ $hits['assign_secret'] = $ac }
  return [pscustomobject]@{ Text = $t; Hits = $hits }
}

$shBody = @'
set -u
ROOT=/home/azureuser/trading_corp
echo "===HOST==="
hostname; date -u +"%Y-%m-%dT%H:%M:%SZ"; uptime | sed 's/^ *//'; uptime -s
last reboot 2>/dev/null | head -3
echo "===OS_RELEASE==="
cat /etc/os-release 2>/dev/null
echo "===KERNEL==="
echo "[running]"; uname -r; uname -v
echo "[installed kernel packages]"
dpkg -l 2>/dev/null | awk '/^ii +linux-image/ {print $2" "$3}'
ls -1 /boot/vmlinuz-* 2>/dev/null
echo "[reboot-required]"
if [ -f /var/run/reboot-required ]; then cat /var/run/reboot-required; cat /var/run/reboot-required.pkgs 2>/dev/null; else echo "no reboot-required flag"; fi
echo "===APT_CACHE_AGE==="
ls -lt --time-style=long-iso /var/lib/apt/lists/ 2>/dev/null | grep -v '^total' | head -3
stat -c 'update-success-stamp: %y' /var/lib/apt/periodic/update-success-stamp 2>/dev/null || echo "no update-success-stamp"
echo "===APT_UPGRADABLE==="
apt list --upgradable 2>/dev/null
echo "===APT_SIM==="
apt-get -s -o Debug::NoLocking=1 upgrade 2>/dev/null | tail -8
echo "===SECURITY_STATUS==="
if command -v pro >/dev/null 2>&1; then pro security-status 2>&1 | head -70; elif command -v ubuntu-security-status >/dev/null 2>&1; then ubuntu-security-status 2>&1 | head -70; else echo "no pro/ubuntu-security-status tool"; fi
echo "===UNATTENDED==="
cat /etc/apt/apt.conf.d/20auto-upgrades 2>/dev/null || echo "(no 20auto-upgrades)"
cat /etc/apt/apt.conf.d/50unattended-upgrades 2>/dev/null | grep -vE '^[[:space:]]*(//|$)' | head -30 || echo "(no 50unattended-upgrades readable)"
ls -l --time-style=long-iso /var/log/unattended-upgrades/ 2>/dev/null | head -8 || echo "(unattended-upgrades log unreadable or absent)"
systemctl list-timers apt-daily.timer apt-daily-upgrade.timer --all --no-pager --no-legend 2>/dev/null
echo "===PORTS==="
echo "[tcp listeners]"
ss -tlnp 2>/dev/null
echo "[udp listeners]"
ss -ulnp 2>/dev/null | head -20
echo "===SERVICE_VERSIONS==="
for b in /usr/bin/caddy /usr/local/bin/caddy /opt/caddy/caddy; do [ -x "$b" ] && echo "caddy at $b: $($b version 2>&1 | head -1)"; done
for b in /usr/bin/authelia /usr/local/bin/authelia /opt/authelia/authelia; do [ -x "$b" ] && echo "authelia at $b: $($b --version 2>&1 | head -1)"; done
command -v caddy >/dev/null 2>&1 && echo "caddy PATH: $(caddy version 2>&1 | head -1)"
command -v authelia >/dev/null 2>&1 && echo "authelia PATH: $(authelia --version 2>&1 | head -1)"
docker ps --format '{{.Names}} {{.Image}}' 2>&1 | head -10
echo "[related services]"
systemctl list-units --type=service --all --no-pager --no-legend 2>/dev/null | grep -iE 'caddy|authelia|fail2ban|docker|ssh|chrony|timesyncd|walinuxagent|unattended' | sed 's/^[ \t]*//'
echo "[dpkg versions of interest]"
dpkg -l 2>/dev/null | awk '/^ii/ {print $2" "$3}' | grep -iE '^(caddy|authelia|openssh-server|openssh-client|openssl|ca-certificates|fail2ban|ufw|sqlite3|libsqlite3-0|walinuxagent|xvfb|chrony|systemd |systemd:|libssl[0-9]|sudo|git |git:|curl|wget|libc6|libc6:|libexpat1|libxml2|python3 |python3:|python3\.[0-9]+ |python3\.[0-9]+:)'
openssl version 2>&1
sqlite3 --version 2>&1 | cut -c1-70
echo "===SNAPS==="
snap list 2>/dev/null || echo "(no snap or none installed)"
echo "===SSHD_CONFIG==="
grep -vE '^[[:space:]]*#|^[[:space:]]*$' /etc/ssh/sshd_config 2>/dev/null || echo "(sshd_config unreadable as azureuser)"
for f in /etc/ssh/sshd_config.d/*.conf; do [ -f "$f" ] && echo "--- $f ---" && grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$f" 2>/dev/null; done
echo "===PYTHON==="
"$ROOT/venv/bin/python" -V 2>&1
python3 -V 2>&1
"$ROOT/venv/bin/pip" --version 2>&1
echo "[other venvs under /home/azureuser]"
ls -d /home/azureuser/*/venv /home/azureuser/venv* 2>/dev/null || echo "(none besides trading_corp)"
echo "===PIP_FREEZE_BEGIN==="
"$ROOT/venv/bin/pip" freeze --disable-pip-version-check 2>/dev/null
echo "===PIP_FREEZE_END==="
echo "===RUNTIMES==="
echo "[azure vm agent]"
dpkg -l walinuxagent 2>/dev/null | awk '/^ii/{print $2" "$3}'
command -v waagent >/dev/null 2>&1 && waagent --version 2>&1 | head -2
echo "[node/npm]"
command -v node >/dev/null 2>&1 && node --version || echo "no node"
command -v npm >/dev/null 2>&1 && npm --version 2>/dev/null || echo "no npm"
echo "[xvfb]"
command -v Xvfb >/dev/null 2>&1 && Xvfb -version 2>&1 | head -2 || echo "no Xvfb on PATH"
echo "[playwright browser cache]"
ls -1 /home/azureuser/.cache/ms-playwright 2>/dev/null || echo "(no ms-playwright cache)"
echo "===HYGIENE==="
echo "[disk]"
df -h 2>/dev/null | awk 'NR==1 || $1 ~ /dev/'
df -i / 2>/dev/null | tail -1
journalctl --disk-usage 2>&1
echo "[home top dirs]"
du -xh --max-depth=1 /home/azureuser 2>/dev/null | sort -rh | head -12
echo "[var log top]"
du -xh --max-depth=1 /var/log 2>/dev/null | sort -rh | head -8
echo "[firewall]"
systemctl is-enabled ufw 2>&1; systemctl is-active ufw 2>&1
dpkg -l ufw fail2ban 2>/dev/null | awk '/^(ii|un)/{print $1" "$2" "$3}'
systemctl is-active fail2ban 2>&1
echo "[time sync]"
timedatectl 2>&1 | head -8
echo "[tls cert expiry via local handshake]"
echo | timeout 6 openssl s_client -connect 127.0.0.1:443 -servername trading.jacksumner.com 2>/dev/null | openssl x509 -noout -subject -enddate 2>&1 || echo "(no local 443 answer)"
echo "===END==="
'@
$sh = Join-Path $env:TEMP 'secrev_box_probe.sh'
[IO.File]::WriteAllText($sh, $shBody, $enc)

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Write-Output "Running READ-ONLY security probe against $hostt ..."
$res = Get-Content $sh -Raw | ssh -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new $hostt "tr -d '\r\357\273\277'|bash"
Remove-Item $sh -ErrorAction SilentlyContinue
if(-not $res){ Write-Output "ERROR: no output from box (ssh/auth failed?). Nothing written."; exit 1 }

$r = Redact-All (($res -join "`n"))
$clean = $r.Text
if($r.Hits.Count -gt 0){
  Write-Output ("Redaction hits: " + (($r.Hits.GetEnumerator() | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join ' '))
} else { Write-Output "Redaction hits: none" }

$outMain = Join-Path $OutDir '01_box_probe_redacted.txt'
[IO.File]::WriteAllText($outMain, $clean, $enc)

# extract pip freeze block (already redacted text) to a requirements file for LOCAL pip-audit
$lines = $clean -split "`n"
$inFreeze = $false
$frz = New-Object System.Collections.Generic.List[string]
foreach($ln in $lines){
  if($ln -match '^===PIP_FREEZE_BEGIN==='){ $inFreeze = $true; continue }
  if($ln -match '^===PIP_FREEZE_END==='){ $inFreeze = $false; continue }
  if($inFreeze -and $ln.Trim().Length){ $frz.Add($ln.TrimEnd()) }
}
$outReq = Join-Path $OutDir '02_requirements_box.txt'
[IO.File]::WriteAllText($outReq, (($frz -join "`n") + "`n"), $enc)

Write-Output "Wrote: $outMain ($($lines.Count) lines)"
Write-Output "Wrote: $outReq ($($frz.Count) packages)"
$endOk = ($clean -match '===END===')
Write-Output ("Probe end marker: " + $(if($endOk){'PRESENT (complete)'}else{'MISSING (TRUNCATED - do not trust)'}))
