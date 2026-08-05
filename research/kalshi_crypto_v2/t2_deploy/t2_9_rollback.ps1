# T2 rollback via az run-command (root, NO sudo). Tables are additive/harmless - left in place.
$ErrorActionPreference = 'Stop'
$rg = 'RG-SHARED-PROD'; $vm = 'tc-prod-vm'
$bash = @'
systemctl disable --now trading-corp-kcv2-observer 2>/dev/null || true
rm -f /etc/systemd/system/trading-corp-kcv2-observer.service
rm -rf /etc/systemd/system/trading-corp-kcv2-observer.service.d
systemctl daemon-reload
echo ROLLBACK_DONE
'@
$bash = $bash -replace "`r", ""
(az vm run-command invoke -g $rg -n $vm --command-id RunShellScript --scripts $bash | ConvertFrom-Json).value[0].message
