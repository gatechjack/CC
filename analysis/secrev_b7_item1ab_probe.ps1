# secrev_b7_item1ab_probe.ps1 -- Batch 7 item 1a/1b/1d. READ-ONLY, no root. NEVER reads /proc/*/environ (secrets).
param([string]$OutDir='C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis')
$ErrorActionPreference='Continue'
$enc=New-Object System.Text.UTF8Encoding $false; [Console]::OutputEncoding=$enc; $OutputEncoding=$enc
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$log=New-Object System.Collections.Generic.List[string]
function L([string]$s){ $log.Add($s); Write-Output $s }
$sh=@'
set -u
echo "=====1a. display :99 -- Xvfb + who is attached====="
ps -o pid,ppid,user,comm,args -p 17958 2>/dev/null | sed 's/^/  Xvfb: /'
echo "  xvfb-run/trading-corp process tree (ppid chain):"
ps -eo pid,ppid,user,comm,args 2>/dev/null | grep -E 'xvfb-run|Xvfb| 17947 |trading_corp' | grep -v grep | sed -E 's/(-c |--scripts ).*/\1<...>/' | sed 's/^/    /' | head -8
echo "  processes with the :99 X socket open (fuser -- PIDs only, no env):"
fuser /tmp/.X11-unix/X99 2>/dev/null | sed 's/^/    pids: /' || echo "    (fuser: none/again)"
echo "  (trading-corp ExecStart uses xvfb-run -> the WHOLE engine runs on :99; Fidelity fidelity.py:829 spawns firefox there)"
echo "=====1b. browser binaries on the box====="
echo "  -- ms-playwright caches (all standard locations) --"
for d in /home/azureuser/.cache/ms-playwright /root/.cache/ms-playwright /home/*/.cache/ms-playwright; do [ -d "$d" ] 2>/dev/null && echo "    DIR $d: $(ls "$d" 2>/dev/null | tr '\n' ' ')"; done
echo "  -- playwright firefox binary actually present + runnable? --"
FFX=$(ls /home/azureuser/.cache/ms-playwright/firefox-*/firefox/firefox 2>/dev/null | head -1)
if [ -n "$FFX" ]; then echo "    firefox bin: $FFX ($(stat -c '%s bytes mode=%a' "$FFX" 2>/dev/null))"; echo "    version: $("$FFX" --version 2>&1 | head -1)"; else echo "    NO firefox binary under firefox-*/firefox/ -- playwright firefox NOT fully installed"; fi
echo "    firefox-1511 dir tree (top):"; ls -la /home/azureuser/.cache/ms-playwright/firefox-1511/ 2>/dev/null | sed 's/^/      /' | head
echo "    chromium/webkit under ms-playwright? $(ls -d /home/azureuser/.cache/ms-playwright/chromium-* /home/azureuser/.cache/ms-playwright/webkit-* 2>/dev/null | tr '\n' ' ' || echo none)"
echo "  -- playwright bundled ffmpeg --"
ls /home/azureuser/.cache/ms-playwright/ffmpeg-*/ffmpeg-linux 2>/dev/null | sed 's/^/    /' || echo "    (none)"
echo "  -- apt/snap browsers --"
for b in google-chrome google-chrome-stable chromium chromium-browser chromedriver firefox; do p=$(command -v $b 2>/dev/null); [ -n "$p" ] && echo "    $b -> $p ($($p --version 2>&1 | head -1))"; done
echo "    snap browsers: $(command -v snap >/dev/null 2>&1 && snap list 2>/dev/null | grep -iE 'chrom|firefox' | awk '{print $1"="$2}' | tr '\n' ' ' || echo 'snap n/a')"
echo "    dpkg browsers: $(dpkg -l 2>/dev/null | awk '/^ii/{print $2}' | grep -iE '^(google-chrome|chromium|firefox)' | tr '\n' ' ' || echo none)"
echo "  -- playwright python pkg version (venv) --"
/home/azureuser/trading_corp/venv/bin/pip show playwright 2>/dev/null | awk '/^Version:/{print "    playwright-py "$2}'
echo "=====1d. browser-launch history (journal, last 7 days)====="
echo "  firefox/playwright/Fidelity launch + outcome lines:"
journalctl --since '7 days ago' --no-pager 2>/dev/null | grep -iE 'playwright|firefox|BrowserType|fidelity.*(broker|launch|browser|session)|xvfb' | grep -ivE 'display:|flex' | sed -E 's/0x[0-9a-fA-F]+/0x<R>/g' | tail -15 | sed 's/^/    /'
echo "  counts (7d): playwright-launch-attempts=$(journalctl --since '7 days ago' --no-pager 2>/dev/null | grep -icE 'playwright.*(launch|start)|firefox.launch'), fidelity-broker-fail=$(journalctl --since '7 days ago' --no-pager 2>/dev/null | grep -icE 'Broker connect failed for division=fidelity'), browser-success=$(journalctl --since '7 days ago' --no-pager 2>/dev/null | grep -icE 'browser.*(connected|launched|ready)|playwright.*connected')"
'@
$res=$sh | ssh -o BatchMode=yes -o ConnectTimeout=30 -o StrictHostKeyChecking=accept-new azureuser@trading.jacksumner.com "tr -d '\r\357\273\277'|bash"
foreach($line in ($res -split "`n")){ L ($line.TrimEnd()) }
[IO.File]::WriteAllText((Join-Path $OutDir '40_b7_item1ab_browser.txt'),(($log -join "`n")+"`n"),$enc)
Write-Output "--- wrote 40_b7_item1ab_browser.txt ---"
