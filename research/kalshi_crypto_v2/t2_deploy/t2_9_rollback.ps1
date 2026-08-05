# T2 rollback: disable + remove the unit (ssh -t sudo TTY). Tables are additive/harmless (left in place).
$ErrorActionPreference = 'Stop'
$h = 'azureuser@trading.jacksumner.com'
ssh -t $h "sudo systemctl disable --now trading-corp-kcv2-observer; sudo rm -f /etc/systemd/system/trading-corp-kcv2-observer.service; sudo rm -rf /etc/systemd/system/trading-corp-kcv2-observer.service.d; sudo systemctl daemon-reload; echo ROLLBACK_DONE"
