# PVC stage (chase-rule A/B): tar the worktree (trading_corp + tests + scripts + pyproject) PLUS the
# dbbc24bf BASE copies of the three chase-edited test files -> scp to box -> stream pvc_run1.sh.
# Box-scratch only: the live tree is READ (BASE engine copies + venv python), never written. No creds.
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$wt = 'C:\Users\AA Incorporado\cc-pm-polyv2-wt'
$tgz = Join-Path $env:TEMP 'pvc_stage.tgz'
$h = 'azureuser@trading.jacksumner.com'
Set-Location $wt
# BASE copies of the chase-EDITED test files (run A must use the pre-chase versions: the old e2e tests
# construct CopySignal without whale_avg_price, which the BASE dataclass does not accept -- and vice versa).
$ab = Join-Path $wt '_ab_base_tests'
if (Test-Path $ab) { Remove-Item -Recurse -Force $ab }
New-Item -ItemType Directory -Force (Join-Path $ab 'prediction_markets') | Out-Null
foreach ($f in @('tests/prediction_markets/test_itf_match.py', 'tests/prediction_markets/test_boxing_match.py',
                 'tests/prediction_markets/test_f1_match.py', 'tests/prediction_markets/test_attachment_span_history.py',
                 'tests/prediction_markets/test_rung3_observability.py')) {
    $out = Join-Path $ab ($f -replace '^tests/', '' -replace '/', '\')
    # cmd /c redirection keeps git's RAW bytes (no PS re-encode/BOM/CRLF mangling -- the autocrlf trap)
    cmd /c "git cat-file blob dbbc24bf:$f > `"$out`""
    if ($LASTEXITCODE -ne 0) { throw "git cat-file failed for $f" }
}
if (Test-Path $tgz) { Remove-Item -Force $tgz }
tar -czf $tgz -C $wt trading_corp tests scripts pyproject.toml _ab_base_tests
if ($LASTEXITCODE -ne 0) { throw "tar failed: $LASTEXITCODE" }
Remove-Item -Recurse -Force $ab   # scratch only -- never left in the worktree
$mb = [math]::Round((Get-Item $tgz).Length / 1MB, 1)
Write-Output ("tarball: {0} MB" -f $mb)
scp -o ConnectTimeout=20 -o BatchMode=yes $tgz ("{0}:/home/azureuser/pvc_stage.tgz" -f $h)
if ($LASTEXITCODE -ne 0) { throw "scp failed: $LASTEXITCODE" }
Get-Content -LiteralPath (Join-Path $wt 'analysis\pvc_run1.sh') -Raw | ssh -o ConnectTimeout=20 -o BatchMode=yes $h "tr -d '\r\357\273\277' | bash"
