# T2 step 3: install the systemd unit to /etc + verify its md5. NO start yet.
# ssh -t gives a TTY so sudo can prompt for your password if it is not NOPASSWD.
# The remote command has no embedded double-quotes, so it is safe as an ssh argument.
$ErrorActionPreference = 'Stop'
$h = 'azureuser@trading.jacksumner.com'
ssh -t $h "sudo cp /home/azureuser/trading-corp-kcv2-observer.service /etc/systemd/system/trading-corp-kcv2-observer.service && md5sum /etc/systemd/system/trading-corp-kcv2-observer.service && ( md5sum /etc/systemd/system/trading-corp-kcv2-observer.service | grep -q bf0014618895921790c6423f4fbd2255 && echo UNIT_MD5_MATCH || echo UNIT_MD5_MISMATCH_STOP )"
