# pk_cp6_deploy.ps1 -- Phase 2 CP6 STAGE 2, step 1 of 3: INSTALL the 12-file batch with NO RESTART.
#
# The key deviation from a normal deploy: this installs the new code on disk but does NOT restart. The
# running engine (PID 760172) stays on the OLD code until step 3. Reason: the files must be present so
# db.set_agent_state_multi EXISTS for the cutover (step 2, pk_cutover_seed.ps1), and the restart (step 3,
# pk_cp6_restart_verify.ps1) must wait until AFTER the roster is seeded -- else the engine boots onto the
# retargeted-but-empty live_whales and watches nobody.
#
# Abort-safe: aborts on bad bundle md5 / any drift / roster_split.py already present / install md5
# mismatch (backups .bak_cp6_<ts> intact -> pk_cp6_rollback.ps1). Needs sidecar cp_cp6_bundle.b64. Run:
#   powershell -ep bypass -f .\pk_cp6_deploy.ps1
$ErrorActionPreference = 'Stop'
$RG = 'RG-SHARED-PROD'; $VM = 'tc-prod-vm'
$b64 = [IO.File]::ReadAllText('C:\Users\AA Incorporado\cc\cp_cp6_bundle.b64')
Write-Host "sidecar cp_cp6_bundle.b64 length $($b64.Length) -- uploading in chunks"
$size = 50000; $tf = Join-Path $env:TEMP 'cp6_chunk.sh'; $enc = New-Object Text.UTF8Encoding($false); $first = $true; $n = 0
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/cp_cp6_bundle.b64`n", $enc)
    az vm run-command invoke -g $RG -n $VM --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $n++; $first = $false; Write-Host "  chunk $n uploaded ($($chunk.Length) chars)"
}
Remove-Item $tf -ErrorAction SilentlyContinue
$apply = @'
cd /home/azureuser/trading_corp
M=$(base64 -d /tmp/cp_cp6_bundle.b64 | md5sum | cut -d" " -f1); echo "BUNDLE_MD5 $M"
[ "$M" = "e6e7feaf39daa911aaf5ffd448f75808" ] || { echo ABORT_BUNDLE_MD5; exit 3; }
base64 -d /tmp/cp_cp6_bundle.b64 | gunzip -c > /tmp/cp6.tar || { echo ABORT_DECODE; exit 3; }
g() { c=$(tr -d '\r' < "$1" 2>/dev/null | md5sum | cut -d" " -f1); if [ "$c" != "$2" ]; then echo "ABORT_DRIFT $1 got=$c want=$2"; exit 4; fi; }
g config/strategies.yaml ec8684da6911f0d79c08148bab07d518
g trading_corp/agents/poly_kalshi_marks.py 887f1bb09610a7c301dc3fc9060b37cc
g trading_corp/agents/strategies/poly_kalshi_executor.py 3ad0824666d5d89435c7b614a5ff1872
g trading_corp/agents/strategies/polymarket_copy_trader.py 49d3a5d01280e02d7761bd66957f7eec
g trading_corp/main.py 229693a8b5a2dd809f6e8825b667cb80
g trading_corp/persistence/db.py 9daf8bf6474f3fef712bbf217d7ab3a1
g trading_corp/web/data.py 36180479f3df051ff43ce5f496bfd7dd
g trading_corp/web/routes.py 589291482a32911e41229b60680fcd2e
g trading_corp/web/templates/home.html 31589243c9e8f92a0d8cfd7eb0c2d176
g trading_corp/web/templates/partials/poly_kalshi_live.html 176d102c3c867890d35fdaeeb5e7db03
g trading_corp/web/templates/partials/poly_kalshi_live_inner.html 69267e6dae67b345c53e00aa09545581
[ -f trading_corp/agents/strategies/roster_split.py ] && { echo "ABORT_NEW_PRESENT roster_split.py"; exit 4; }
echo DRIFT_GATE_OK
TS=$(date -u +%Y%m%d_%H%M%S)
MODS="config/strategies.yaml trading_corp/agents/poly_kalshi_marks.py trading_corp/agents/strategies/poly_kalshi_executor.py trading_corp/agents/strategies/polymarket_copy_trader.py trading_corp/main.py trading_corp/persistence/db.py trading_corp/web/data.py trading_corp/web/routes.py trading_corp/web/templates/home.html trading_corp/web/templates/partials/poly_kalshi_live.html trading_corp/web/templates/partials/poly_kalshi_live_inner.html"
for f in $MODS; do cp "$f" "${f}.bak_cp6_$TS"; done
echo "BACKUP_SUFFIX .bak_cp6_$TS"
chmod 644 /tmp/cp6.tar
runuser -u azureuser -- tar -xf /tmp/cp6.tar || { echo "ABORT_EXTRACT restore .bak_cp6_$TS"; exit 6; }
i() { c=$(tr -d '\r' < "$1" 2>/dev/null | md5sum | cut -d" " -f1); if [ "$c" != "$2" ]; then echo "ABORT_INSTALL $1 got=$c want=$2 -- restore .bak_cp6_$TS + rm roster_split.py"; exit 7; fi; }
i config/strategies.yaml df6ce50ab1155eaa295f772ce8de1a23
i trading_corp/agents/poly_kalshi_marks.py 6fd9cf5d9c2338004c5af260422d72e3
i trading_corp/agents/strategies/poly_kalshi_executor.py d1f871f9c3e83530dc6fba3bd58c2eae
i trading_corp/agents/strategies/polymarket_copy_trader.py b203db5fcc1548aa06349e4fd5dd0f77
i trading_corp/main.py 6b8b516990443f7a5c8f264f8314dfde
i trading_corp/persistence/db.py c0432d37d859f2376a6507f3ce06c00b
i trading_corp/web/data.py 28db1cc33a597c85ed8ef0449e51bf0d
i trading_corp/web/routes.py b4d1ee00d097737a967bdef4ca76ee93
i trading_corp/web/templates/home.html c55beec58137e56a4a2b9ce1b89e186e
i trading_corp/web/templates/partials/poly_kalshi_live.html 020e1bf831b94feaf78378addffee328
i trading_corp/web/templates/partials/poly_kalshi_live_inner.html 17cff25c381d433e80742118cce753c5
i trading_corp/agents/strategies/roster_split.py 21b1ccbef6db76975364c7a8885a8166
echo "INSTALL_VERIFIED all 12 LF-md5 == new-expected"
PID=$(systemctl show trading-corp -p MainPID --value)
echo "NO_RESTART engine PID $PID still on OLD code (deliberate)"
python3 -c "import importlib.util,sys; sys.path.insert(0,'.'); print('SET_AGENT_STATE_MULTI_ON_DISK', 'set_agent_state_multi' in open('trading_corp/persistence/db.py').read())"
echo "NEXT step 2: pk_cutover_seed.ps1 (DRY then -Apply); then step 3: pk_cp6_restart_verify.ps1"
'@
$apply = $apply -replace "`r", ""
$ab64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($apply))
$acmd = "printf %s '$ab64' | base64 -d | bash"
Write-Host "== CP6 STAGE 2 step 1 DEPLOY: bundle-md5 -> drift-gate -> backup -> extract -> install-verify -> NO RESTART =="
az vm run-command invoke -g $RG -n $VM --command-id RunShellScript --scripts $acmd --query "value[0].message" -o tsv
