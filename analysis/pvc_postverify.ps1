# PVC post-restart verify - streams pvc_postverify.sh via ssh STDIN. READ-ONLY: systemctl show,
# journalctl, sha, sqlite3 mode=ro. No writes, no creds. Run AFTER both services restarted.
#   -Mode v2       : verifies the v2-alone deploy (chase sections print as NOT-DEPLOYED)
#   -Mode v2chase  : also verifies gate C -- TARGET shas on the 4 chase files, migration 025, the
#                    chase_skips heartbeat counter PRESENT and MOVING (two samples 20s apart)
param(
    [Parameter(Mandatory = $true)][ValidateSet('v2', 'v2chase')][string]$Mode
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$wt = 'C:\Users\AA Incorporado\cc-pm-polyv2-wt'
Get-Content -LiteralPath (Join-Path $wt 'analysis\pvc_postverify.sh') -Raw |
    ssh -o ConnectTimeout=25 -o BatchMode=yes $h ("tr -d '\r\357\273\277' | MODE={0} bash" -f $Mode)
