$ErrorActionPreference = "Stop"
# PHASE 6 -- RESTART trading-corp + BOOT-VERIFY (root, via az vm run-command). Jack runs; agent does not.
# The box NOPASSWD sudo is INERT (shadowed by trailing (ALL) ALL) so a runner that reports success while
# changing nothing is a proven failure mode here -> this VERIFIES ACTUAL UNIT STATE after the fact.
# Precondition-gated: prod-live == 8f35f254 (laptop-side; box is not a git repo) AND box main.py == grafted md5.
# Restarts ONLY trading-corp; asserts engine PID CHANGED and pm_web/sfp/kcv2 UNCHANGED; surfaces boot
# tracebacks/ERRORs; prints boot-reconcile for both Kalshi accounts. Does NOT arm/halt/disarm.
$rg = "rg-shared-prod"; $vm = "tc-prod-vm"
$expectProdLive = "8f35f254aed9194d8b1e6483644242a8ab4c5718"
$wt = "C:\Users\AA Incorporado\cc-legpm-removal-wt"

Write-Host "== Precondition: origin/prod-live must == $expectProdLive =="
$tip = (git -C $wt ls-remote origin prod-live | ForEach-Object { ($_ -split "`t")[0] })
Write-Host "  origin/prod-live = $tip"
if ($tip -ne $expectProdLive) { throw "ABORT: prod-live != 8f35f254 -- deploy not on prod-live; refusing to restart." }

$script = @'
set -u
GRAFTED=c15b4de64ad19b814580cb6ed311262f
M=/home/azureuser/trading_corp/trading_corp/main.py
echo "### PHASE-6 RESTART + BOOT-VERIFY (root) $(date -u +%FT%TZ) ###"
md5=$(tr -d '\r' < "$M" | md5sum | cut -c1-32)
echo "box main.py CR-md5=$md5 (expect grafted $GRAFTED)"
if [ "$md5" != "$GRAFTED" ]; then echo "ABORT: box main.py is NOT the grafted file -- run graft-deploy first. NOT restarting."; exit 3; fi

getpid(){ systemctl show "$1" -p MainPID --value 2>/dev/null; }
getnr(){ systemctl show "$1" -p NRestarts --value 2>/dev/null; }
KCV2_B=$(pgrep -f 'kalshi_crypto_v2_observer' | head -1)
TC_B=$(getpid trading-corp); PM_B=$(getpid prediction-markets-web); SF_B=$(getpid sfp-card-watcher)
echo "BEFORE: trading-corp=$TC_B (NR=$(getnr trading-corp)) pm_web=$PM_B sfp=$SF_B kcv2=$KCV2_B"

T0=$(date +'%Y-%m-%d %H:%M:%S')
echo "--- systemctl restart trading-corp (T0=$T0) ---"
systemctl restart trading-corp
sleep 20

TC_A=$(getpid trading-corp); PM_A=$(getpid prediction-markets-web); SF_A=$(getpid sfp-card-watcher)
KCV2_A=$(pgrep -f 'kalshi_crypto_v2_observer' | head -1)
echo "AFTER:  trading-corp=$TC_A (NR=$(getnr trading-corp)) pm_web=$PM_A sfp=$SF_A kcv2=$KCV2_A"
echo "trading-corp state: $(systemctl show trading-corp -p ActiveState,SubState --value | tr '\n' ' ')"

echo "=== ASSERTIONS (state-based) ==="
if [ -n "$TC_A" ] && [ "$TC_A" != 0 ] && [ "$TC_A" != "$TC_B" ]; then echo "  PASS trading-corp PID changed ($TC_B -> $TC_A)"; else echo "  FAIL trading-corp PID unchanged/not-running ($TC_B -> $TC_A)"; fi
if [ "$PM_A" = "$PM_B" ] && [ -n "$PM_A" ]; then echo "  PASS pm_web PID unchanged ($PM_A)"; else echo "  FAIL pm_web PID CHANGED ($PM_B -> $PM_A)"; fi
if [ "$SF_A" = "$SF_B" ] && [ -n "$SF_A" ]; then echo "  PASS sfp-card-watcher PID unchanged ($SF_A)"; else echo "  FAIL sfp PID CHANGED ($SF_B -> $SF_A)"; fi
if [ "$KCV2_A" = "$KCV2_B" ] && [ -n "$KCV2_A" ]; then echo "  PASS kcv2 observer PID unchanged ($KCV2_A)"; else echo "  FAIL kcv2 PID changed/absent ($KCV2_B -> $KCV2_A)"; fi
AS=$(systemctl show trading-corp -p ActiveState --value)
if [ "$AS" = active ]; then echo "  PASS trading-corp ActiveState=active"; else echo "  FAIL trading-corp ActiveState=$AS"; fi

echo "=== BOOT TRACEBACKS / ERRORS since restart (surface, do not swallow) ==="
journalctl -u trading-corp --since "$T0" --no-pager 2>/dev/null | grep -iE 'traceback|exception|critical|: error|ERROR ' | head -40 || echo "  (none matched)"
echo "=== BOOT-RECONCILE / arm lines, BOTH Kalshi accounts ==="
journalctl -u trading-corp --since "$T0" --no-pager 2>/dev/null | grep -iE 'reconcil|kalshi_jack|kalshi_karen|\barm|latch|live_driver|pm_live|poly_kalshi' | head -60 || echo "  (none matched -- inspect full boot log below)"
echo "=== last 30 boot log lines ==="
journalctl -u trading-corp --since "$T0" --no-pager 2>/dev/null | tail -30
echo "### DONE -- PLACING gate is watched separately by the agent (fresh dry_run=0 order within 20 min). ###"
'@
Write-Host "Restarting trading-corp on $vm via az-root RunShellScript (boot-verify inside)..."
az vm run-command invoke -g $rg -n $vm --command-id RunShellScript --scripts $script
