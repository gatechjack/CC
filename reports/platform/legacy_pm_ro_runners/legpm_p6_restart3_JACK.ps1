$ErrorActionPreference = "Stop"
# PHASE 6 -- RESTART trading-corp (root via az), gated. restart3: fixes restart2's Gate-2 md5 bug
# (tr -d "\r" got its backslash eaten through az --scripts -> stripped every 'r' -> false md5 c68e4a94).
# The deployed main.py is LF-only (graft stripped CR), so RAW md5sum == CR-stripped md5 == grafted.
# No tr, no backslash to mangle. EVERY az --scripts is SINGLE-LINE (proven pattern).
# Gates: prod-live == 8f35f254 (local git) AND box main.py raw md5 == grafted c15b4de6.
# Full boot-verify is the separate RO runner legpm_p6_gate_watch_ro.ps1.
$rg = "rg-shared-prod"; $vm = "tc-prod-vm"
$expect = "8f35f254aed9194d8b1e6483644242a8ab4c5718"
$grafted = "c15b4de64ad19b814580cb6ed311262f"
$wt = "C:\Users\AA Incorporado\cc-legpm-removal-wt"

Write-Host "== Gate 1: origin/prod-live must == $expect =="
$tip = (git -C $wt ls-remote origin prod-live | ForEach-Object { ($_ -split "`t")[0] })
Write-Host "  origin/prod-live = $tip"
if ($tip -ne $expect) { throw "ABORT: prod-live != 8f35f254 -- not restarting." }

Write-Host "== Gate 2: box main.py RAW md5 (LF-only file => raw==CR-stripped) must == $grafted =="
$md5s = 'md5sum /home/azureuser/trading_corp/trading_corp/main.py | cut -c1-32'
$m = (az vm run-command invoke -g $rg -n $vm --command-id RunShellScript --scripts $md5s --query "value[0].message" -o tsv)
Write-Host $m
if ($m -notmatch $grafted) { throw "ABORT: box main.py raw md5 != grafted $grafted -- not restarting." }

Write-Host "== GATES PASSED -- restarting trading-corp (single-line az) =="
$r = 'systemctl restart trading-corp; sleep 6; echo POST-RESTART-SHOW:; systemctl show trading-corp -p MainPID,ActiveState,SubState,ExecMainStartTimestamp,NRestarts'
az vm run-command invoke -g $rg -n $vm --command-id RunShellScript --scripts $r --query "value[0].message" -o tsv
Write-Host "---- restart issued. New MainPID / ExecMainStartTimestamp above should DIFFER from 370246 / 2026-09-12 21:00:28. ----"
Write-Host "---- NEXT: agent runs legpm_p6_gate_watch_ro.ps1 (RO) at ~+2-3 min for WIRED line, then cycles/placement. ----"
