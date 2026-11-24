# secrev_investigate.ps1 -- PHASE 2 READ-ONLY investigation probe (A2/A4/B2/B3/B4).
# ssh-STDIN channel. READ-ONLY. No sudo, no write, no restart. Never reads .env/~/.ssh/Key Vault/Environment= lines
# or any secret VALUE. For the Authelia backend it emits only the backend TYPE key (file/ldap), never contents.
# Output redacted (Conventions.md) before disk. Writes analysis\06_investigation_redacted.txt.
param([string]$OutDir = 'C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis')
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$enc = New-Object System.Text.UTF8Encoding $false
$hostt = 'azureuser@trading.jacksumner.com'

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
echo "===A4_DISK==="
df -h / | tail -1
df -i / | tail -1
echo "[journald]"; journalctl --disk-usage 2>&1
echo "[apt archive cache]"; du -sh /var/cache/apt/archives 2>/dev/null
echo "[var/log journal dir]"; du -sh /var/log/journal 2>/dev/null
echo "[home top 8]"; du -xh --max-depth=1 /home/azureuser 2>/dev/null | sort -rh | head -8
echo "[pm_* backups total]"; du -ch /home/azureuser/pm_*backup* 2>/dev/null | tail -1
echo "===A2_KEYVAULT_MI==="
echo "[azure-identity / keyvault / IMDS symbol references in prod code -- names only, no values]"
grep -rnoE "DefaultAzureCredential|ManagedIdentityCredential|azure[._]identity|SecretClient|azure[._]keyvault|KEY_VAULT_URI|vault\.azure\.net|169\.254\.169\.254|identity/oauth2/token" "$ROOT/trading_corp" 2>/dev/null | grep -viE "test|mock" | head -40
echo "[secrets loader module names]"
ls -1 "$ROOT/trading_corp" 2>/dev/null | grep -iE "secret|vault|config" | head
grep -rlE "DefaultAzureCredential|ManagedIdentityCredential|SecretClient" "$ROOT/trading_corp" 2>/dev/null | head
echo "===B2_AUTHELIA_BACKEND==="
AU=$(systemctl show authelia.service -p ExecStart 2>/dev/null | grep -oE "\-\-config[= ][^ ;]+|-c[= ][^ ;]+" | head -1 | sed -E "s/^(--config|-c)[= ]//")
if [ -z "$AU" ]; then AU=$(systemctl show authelia.service -p ExecStart 2>/dev/null | grep -oE "/[^ ]*\.ya?ml" | head -1); fi
echo "config-path: ${AU:-UNKNOWN}"
if [ -n "${AU:-}" ] && [ -r "$AU" ]; then
  echo "config-readable: YES"
  echo "[authentication_backend TYPE only -- file vs ldap; no values]"
  awk "/^authentication_backend:/{f=1;print \"  (section found)\";next} f&&/^[^[:space:]]/{f=0} f&&/^[[:space:]]+(file|ldap):/{gsub(/[^a-z:]/,\"\");print \"  backend=\" \$0}" "$AU"
  echo "[presence test]"
  grep -qE "^[[:space:]]+ldap:" "$AU" && echo "  has-ldap-key=yes" || echo "  has-ldap-key=no"
  grep -qE "^[[:space:]]+file:" "$AU" && echo "  has-file-key=yes" || echo "  has-file-key=no"
else
  echo "config-readable: NO (root-only or not found) -> need az root reader for B2"
fi
echo "[authelia health -- last lines, redacted]"
journalctl -u authelia.service -n 6 --no-pager 2>/dev/null | tail -6 || echo "(journal not readable for authelia)"
echo "===B3_XRDP==="
echo "[dpkg status]"; dpkg -l xrdp 2>/dev/null | awk "/^ii/{print \$2\" \"\$3}"
echo "[install date from dpkg.log]"; zgrep -hE " install xrdp(:| )" /var/log/dpkg.log* 2>/dev/null | head -3
echo "[apt history -- what command pulled xrdp]"; zgrep -hB1 -A3 "xrdp" /var/log/apt/history.log* 2>/dev/null | grep -iE "Commandline:|Install:|Requested-By:|Start-Date:" | grep -iB1 -A1 xrdp | head -12
echo "[reverse deps installed]"; apt-cache rdepends --installed xrdp 2>/dev/null | head
echo "[xrdp session evidence -- recent log mtimes + any connect lines]"; ls -l --time-style=long-iso /var/log/xrdp*.log 2>/dev/null
grep -ihE "connect|login|session" /var/log/xrdp-sesman.log 2>/dev/null | tail -3 || echo "(no readable xrdp-sesman.log)"
echo "[active xrdp sessions now]"; who 2>/dev/null; ss -tn state established "( dport = :3389 or sport = :3389 )" 2>/dev/null | tail -5
echo "===B4_WEB3==="
echo "[web3 importers in prod code]"
grep -rlnE "^[[:space:]]*(import web3|from web3 )" "$ROOT/trading_corp" 2>/dev/null | head -30
echo "[CCIP-read / eth_call references]"
grep -rnoE "ccip|CCIPRead|ccip_read|OffchainLookup|eth_call|\.call\(" "$ROOT/trading_corp" 2>/dev/null | grep -iE "web3|ccip|offchain" | head
echo "[which modules import web3 -> map to division dirs]"
grep -rlE "^[[:space:]]*(import web3|from web3 )" "$ROOT/trading_corp" 2>/dev/null | sed -E "s#$ROOT/trading_corp/##" | awk -F/ "{print \$1\"/\"\$2}" | sort -u
echo "[live divisions from running ExecStart]"
systemctl show trading-corp.service -p ExecStart 2>/dev/null | grep -oE "\-\-live-divisions[^;]*" | tr ' ' '\n' | grep -vE "^--live-divisions$" | head -12
echo "===END==="
'@
$sh = Join-Path $env:TEMP 'secrev_investigate.sh'
[IO.File]::WriteAllText($sh, $shBody, $enc)

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Write-Output "Running READ-ONLY investigation probe against $hostt ..."
$res = Get-Content $sh -Raw | ssh -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new $hostt "tr -d '\r\357\273\277'|bash"
Remove-Item $sh -ErrorAction SilentlyContinue
if(-not $res){ Write-Output "ERROR: no output from box. Nothing written."; exit 1 }
$r = Redact-All (($res -join "`n"))
if($r.Hits.Count -gt 0){ Write-Output ("Redaction hits: " + (($r.Hits.GetEnumerator() | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join ' ')) } else { Write-Output "Redaction hits: none" }
$out = Join-Path $OutDir '06_investigation_redacted.txt'
[IO.File]::WriteAllText($out, $r.Text, $enc)
Write-Output ("Wrote: " + $out)
Write-Output ("End marker: " + $(if($r.Text -match '===END==='){'PRESENT'}else{'MISSING (truncated)'}))
