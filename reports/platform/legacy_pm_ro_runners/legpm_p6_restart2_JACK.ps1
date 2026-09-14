$ErrorActionPreference = "Stop"
# PHASE 6 -- RESTART trading-corp (root via az), gated. Rebuild after the multi-line --scripts
# variant returned empty output and did NOT restart (engine stayed PID 370246 / 09-12 boot).
# EVERY az --scripts here is SINGLE-LINE (the proven pattern: disable_JACK / canonical restart_tc).
# Full boot-verify is the separate RO runner legpm_p6_gate_watch_ro.ps1 (driver-cycle + placement).
# Gates: prod-live == 8f35f254 (local git) AND box main.py == grafted md5 (single-line az RO).
$rg = "rg-shared-prod"; $vm = "tc-prod-vm"
$expect = "8f35f254aed9194d8b1e6483644242a8ab4c5718"
$grafted = "c15b4de64ad19b814580cb6ed311262f"
$wt = "C:\Users\AA Incorporado\cc-legpm-removal-wt"

Write-Host "== Gate 1: origin/prod-live must == $expect =="
$tip = (git -C $wt ls-remote origin prod-live | ForEach-Object { ($_ -split "`t")[0] })
Write-Host "  origin/prod-live = $tip"
if ($tip -ne $expect) { throw "ABORT: prod-live != 8f35f254 -- not restarting." }

Write-Host "== Gate 2: box main.py must == grafted $grafted (single-line az RO) =="
$md5s = 'tr -d "\r" < /home/azureuser/trading_corp/trading_corp/main.py | md5sum | cut -c1-32'
$m = (az vm run-command invoke -g $rg -n $vm --command-id RunShellScript --scripts $md5s --query "value[0].message" -o tsv)
Write-Host $m
if ($m -notmatch $grafted) { throw "ABORT: box main.py md5 != grafted -- box not deployed; not restarting." }

Write-Host "== GATES PASSED -- restarting trading-corp (single-line az) =="
$r = 'systemctl restart trading-corp; sleep 6; echo POST-RESTART-SHOW:; systemctl show trading-corp -p MainPID,ActiveState,SubState,ExecMainStartTimestamp,NRestarts'
az vm run-command invoke -g $rg -n $vm --command-id RunShellScript --scripts $r --query "value[0].message" -o tsv
Write-Host "---- restart issued. New MainPID/ExecMainStartTimestamp shown above should DIFFER from 370246 / 09-12 21:00:28. ----"
Write-Host "---- NEXT: run legpm_p6_gate_watch_ro.ps1 at ~2-3 min for the full boot-verify + driver-cycle + placement gate. ----"
