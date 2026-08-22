# pk_pm_pytest_ro.ps1 -- READ-ONLY: run tests/prediction_markets/ in an isolated ~/ scratch on the box.
# Tars the worktree PM package + tests + real conftest/pyproject/__init__, ships (base64), extracts into
# ~/pm_p1_scratch, runs pytest (box venv, PM_DB_PATH=tmp FILE, -p no:pytest_ethereum), proves isolation
# (legacy DB md5+mtime before/after = context; no *.db under scratch; chain-of-custody sha256 vs local),
# cleans up. Board-authorized read-only + temp cleanup. NOTHING lands in /home/azureuser/trading_corp.
# Run: powershell -ep bypass -f .\pk_pm_pytest_ro.ps1
$ErrorActionPreference = 'Stop'
$W = "C:\Users\AA Incorporado\cc-prediction-markets-wt"
$tar = Join-Path $env:TEMP 'pm_tree.tgz'
if (Test-Path $tar) { Remove-Item $tar -Force }
tar.exe -czf $tar -C $W trading_corp/prediction_markets trading_corp/data tests/prediction_markets tests/conftest.py pyproject.toml trading_corp/__init__.py
$localDb  = (Get-FileHash (Join-Path $W 'trading_corp\prediction_markets\db.py') -Algorithm SHA256).Hash.ToLower()
$localCat = (Get-FileHash (Join-Path $W 'trading_corp\prediction_markets\category.py') -Algorithm SHA256).Hash.ToLower()
Write-Host "LOCAL sha256 db.py       = $localDb"
Write-Host "LOCAL sha256 category.py = $localCat"
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_pt_chunk.sh'
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($tar))
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pm_tree.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
$bash = @'
S="${HOME:-/home/azureuser}/pm_p1_scratch"; OUT=/tmp/pm_pt_out.txt; LEG=/home/azureuser/trading_corp/data/trading_corp.db
{
echo "===== LEGACY md5+mtime BEFORE (context; live WAL DB expected-to-differ) ====="
md5sum "$LEG"; stat -c "%n size=%s mtime=%y" "$LEG"
echo "===== SCRATCH extract ($S) ====="
rm -rf "$S"; mkdir -p "$S"
base64 -d /tmp/pm_tree.b64 | tar xzf - -C "$S"
echo "scratch files:"; find "$S" -type f | sort
echo "===== chain-of-custody sha256 (compare to LOCAL printed by runner) ====="
sha256sum "$S/trading_corp/prediction_markets/db.py" "$S/trading_corp/prediction_markets/category.py"
echo "===== PYTEST tests/prediction_markets/ ====="
cd "$S" && PM_DB_PATH="/tmp/pm_test_$$.db" PYTHONPATH=. /home/azureuser/trading_corp/venv/bin/python -m pytest tests/prediction_markets/ -q -p no:pytest_ethereum -p no:cacheprovider
echo "pytest_exit=$?"
echo "resolved PM_DB_PATH=/tmp/pm_test_$$.db"; ls -la /tmp/pm_test_*.db 2>/dev/null; rm -f /tmp/pm_test_*.db
echo "===== isolation: *.db anywhere under scratch? (expect none) ====="
find "$S" -name '*.db' | sort; echo "(end db scan)"
echo "===== LEGACY md5+mtime AFTER ====="
md5sum "$LEG"; stat -c "%n size=%s mtime=%y" "$LEG"
echo "===== cleanup ====="
rm -rf "$S"; if [ -d "$S" ]; then echo SCRATCH_FAIL; else echo SCRATCH_REMOVED_OK; fi
} > "$OUT" 2>&1
echo "RUN_DONE lines=$(wc -l < "$OUT")"
'@
$bash = $bash -replace "`r", ""
$b2 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$first2 = $true
for ($i = 0; $i -lt $b2.Length; $i += $size) {
    $chunk = $b2.Substring($i, [Math]::Min($size, $b2.Length - $i))
    $op = if ($first2) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pm_pt.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first2 = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pm_pt.b64 > /tmp/pm_pt.sh && bash /tmp/pm_pt.sh`n", $enc)
Write-Host "== PM P1 SCRATCH PYTEST (READ-ONLY, isolated) =="
$runMsg = az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Write-Host $runMsg
$lines = 400; $runStr = ($runMsg | Out-String); if ($runStr -match 'lines=(\d+)') { $lines = [int]$Matches[1] }
$per = 30; $nchunks = [math]::Ceiling(($lines + 2) / $per)
for ($n = 0; $n -lt $nchunks; $n++) {
    $a = $n * $per + 1; $b = $n * $per + $per
    [IO.File]::WriteAllText($tf, "sed -n '$a,${b}p' /tmp/pm_pt_out.txt`n", $enc)
    Write-Host ("---- OUTPUT lines " + $a + "-" + $b + " ----")
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
}
[IO.File]::WriteAllText($tf, "rm -f /tmp/pm_tree.b64 /tmp/pm_pt.b64 /tmp/pm_pt.sh /tmp/pm_pt_out.txt`n", $enc)
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
Remove-Item $tf, $tar -Force -ErrorAction SilentlyContinue
