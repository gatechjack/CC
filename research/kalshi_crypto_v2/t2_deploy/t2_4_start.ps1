# T2 step 4: start the service (only after step 3 printed UNIT_MD5_MATCH).
# ssh -t for the sudo TTY. No embedded double-quotes in the remote command.
$ErrorActionPreference = 'Stop'
$h = 'azureuser@trading.jacksumner.com'
ssh -t $h "sudo systemctl daemon-reload && sudo systemctl enable --now trading-corp-kcv2-observer && systemctl status trading-corp-kcv2-observer --no-pager | head -6"
