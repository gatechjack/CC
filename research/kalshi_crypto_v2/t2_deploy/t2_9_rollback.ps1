# T2 rollback via az run-command (root, NO sudo). Tables are additive/harmless - left in place.
# bash passed with --scripts "@shortpath" (see step 1 note).
$ErrorActionPreference = 'Stop'
$rg = 'RG-SHARED-PROD'; $vm = 'tc-prod-vm'
$bash = @'
systemctl disable --now trading-corp-kcv2-observer 2>/dev/null || true
rm -f /etc/systemd/system/trading-corp-kcv2-observer.service
rm -rf /etc/systemd/system/trading-corp-kcv2-observer.service.d
systemctl daemon-reload
echo ROLLBACK_DONE
'@
$sh = Join-Path $env:TEMP 't2_kcv2_9.sh'
[IO.File]::WriteAllText($sh, ($bash -replace "`r", ""), (New-Object System.Text.UTF8Encoding($false)))
$short = (New-Object -ComObject Scripting.FileSystemObject).GetFile($sh).ShortPath
$raw = az vm run-command invoke -g $rg -n $vm --command-id RunShellScript --scripts "@$short"
try { ($raw | ConvertFrom-Json).value[0].message } catch { $raw }
Remove-Item $sh -ErrorAction SilentlyContinue
