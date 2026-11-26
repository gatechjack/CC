# secrev_b2c_probe.ps1 -- BATCH 2c READ-ONLY box probe. No writes to box, no root.
#   A) current post-2b prod freeze (scratch baseline)   B) reverse-deps aiohttp/cryptography/yarl
#   C) /etc/caddy/Caddyfile REDACTED on-box (secrets masked before leaving host)
#   D) trading-corp auth-line journal: POST-deploy window vs equal-length PRE-deploy window
param([string]$OutDir='C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis')
$ErrorActionPreference='Continue'
$enc=New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding=$enc; $OutputEncoding=$enc
$hostt='azureuser@trading.jacksumner.com'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$log=New-Object System.Collections.Generic.List[string]
function L([string]$s){ $log.Add($s); Write-Output $s }
$sh=@'
set -u
ROOT=/home/azureuser/trading_corp
VENV="$ROOT/venv/bin"

echo "=====SECTION_A current prod freeze (post-2b)====="
"$VENV/python" -V 2>&1
"$VENV/pip" freeze 2>/dev/null

echo "=====SECTION_B reverse-deps (who Requires / Required-by)====="
for p in aiohttp cryptography yarl ccxt msal azure-identity azure-core fastapi starlette opentelemetry-api; do
  echo "--- $p ---"
  "$VENV/pip" show "$p" 2>/dev/null | grep -E '^(Name|Version|Requires|Required-by):'
done

echo "=====SECTION_C /etc/caddy/Caddyfile REDACTED====="
if [ -r /etc/caddy/Caddyfile ]; then
  sed -E \
    -e 's/\$2[aby]\$[0-9]{2}\$[^" ]+/<REDACTED-BCRYPT>/g' \
    -e 's/\$argon2[A-Za-z0-9$,=+\/._-]+/<REDACTED-ARGON2>/g' \
    -e 's/([Bb]earer )[A-Za-z0-9._~+\/-]+=*/\1<REDACTED-TOKEN>/g' \
    -e 's/(([Tt]oken|[Ss]ecret|[Pp]assword|[Aa]pi[_-]?[Kk]ey)[=: ]+)[^ "]{12,}/\1<REDACTED-VALUE>/g' \
    -e 's/[A-Za-z0-9+=_-]{40,}/<REDACTED-LONGSTR>/g' \
    /etc/caddy/Caddyfile
else
  echo "NOT-READABLE /etc/caddy/Caddyfile"
fi
echo "--- imported Caddy fragments (if any) ---"
grep -nE '^\s*import ' /etc/caddy/Caddyfile 2>/dev/null || echo "(no import directives)"

echo "=====SECTION_D auth-line journal windows (unit=trading-corp)====="
START_RAW=$(systemctl show trading-corp -p ExecMainStartTimestamp --value 2>/dev/null)
echo "restart_raw=$START_RAW"
NOW=$(date +%s)
START=$(date -d "$(echo "$START_RAW" | sed -E 's/^[A-Za-z]+ //')" +%s 2>/dev/null)
if [ -z "${START:-}" ]; then echo "START parse failed"; exit 0; fi
LEN=$((NOW-START)); PRE_START=$((START-LEN))
echo "post_window=[$START,$NOW] len_s=$LEN   pre_window=[$PRE_START,$START]"
echo "post_window_human=[$(date -d @"$START" '+%F %T %Z') .. $(date -d @"$NOW" '+%F %T %Z')]"
echo "pre_window_human=[$(date -d @"$PRE_START" '+%F %T %Z') .. $(date -d @"$START" '+%F %T %Z')]"
PAT='auth.*fail|unauthoriz|\b401\b|\b403\b|invalid.*(key|signature|token)|session.*expir|attestation.*(fail|expir|403)|login.*required|signature.*(invalid|mismatch)'
BEN='polymarket|data-api|/positions|incomplete chunked|429'
# strip "Mon DD HH:MM:SS host proc[pid]: " prefix, then templatize digits -> # for set-compare
STRIP='s/^[A-Z][a-z]{2}[ ]+[0-9]{1,2} [0-9:]{8} [^ ]+ [^:]+: //'
for W in POST PRE; do
  if [ "$W" = POST ]; then S=$START; U=$NOW; else S=$PRE_START; U=$START; fi
  RAW=$(journalctl -u trading-corp --since "@$S" --until "@$U" --no-pager 2>/dev/null)
  ALL=$(printf '%s\n' "$RAW" | grep -iE "$PAT")
  REAL=$(printf '%s\n' "$ALL" | grep -ivE "$BEN" | grep -c . )
  BENC=$(printf '%s\n' "$ALL" | grep -iE "$BEN" | grep -c . )
  echo "[$W] total_auth_pattern=$(printf '%s\n' "$ALL" | grep -c .) real_nonbenign=$REAL benign_excluded=$BENC"
  echo "[$W] REAL (non-benign) distinct templates:"
  printf '%s\n' "$ALL" | grep -ivE "$BEN" | sed -E "$STRIP" | sed -E 's/[0-9]+/#/g' | sort | uniq -c | sort -rn | head -20 | sed 's/^/    /'
  echo "[$W] BENIGN-EXCLUDED distinct templates:"
  printf '%s\n' "$ALL" | grep -iE "$BEN" | sed -E "$STRIP" | sed -E 's/[0-9]+/#/g' | sort | uniq -c | sort -rn | head -30 | sed 's/^/    /'
done
echo "=====END====="
'@
$res=$sh | ssh -o BatchMode=yes -o ConnectTimeout=25 -o StrictHostKeyChecking=accept-new $hostt "tr -d '\r\357\273\277'|bash"
foreach($line in ($res -split "`n")){ L ($line.TrimEnd()) }
[IO.File]::WriteAllText((Join-Path $OutDir '28_b2c_box_probe.txt'),(($log -join "`n")+"`n"),$enc)
Write-Output "--- wrote 28_b2c_box_probe.txt ---"
