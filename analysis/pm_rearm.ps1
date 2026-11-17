# pm_rearm.ps1 -- AUTHORED, DO NOT RUN until R1 (attestation renewed) + R2 (Data API v2 live) are confirmed.
# Streams pm_rearm.sh to the box via ssh-STDIN. pm_rearm.sh WRITES legacy agent_state (clear-latch + arm) via
# the sanctioned pm_cli path ONLY -- a RESERVED action, so Jack runs this in his own terminal. Edit the two
# precondition flags + ARM_LIST inside pm_rearm.sh FIRST; it refuses to do anything until both flags are 1.
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$s = 'C:\Users\AA Incorporado\cc\pm_rearm.sh'
Get-Content -LiteralPath $s -Raw | ssh -o ConnectTimeout=20 -o BatchMode=yes $h "tr -d '\r\357\273\277' | bash"
