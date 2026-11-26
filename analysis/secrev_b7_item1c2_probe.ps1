# secrev_b7_item1c2_probe.ps1 -- Batch 7 item 1c (part 2): Xvfb-path ldd + firefox RUNTIME/dlopen deps vs purge set.
param([string]$OutDir='C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis')
$ErrorActionPreference='Continue'
$enc=New-Object System.Text.UTF8Encoding $false; [Console]::OutputEncoding=$enc; $OutputEncoding=$enc
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$log=New-Object System.Collections.Generic.List[string]
function L([string]$s){ $log.Add($s); Write-Output $s }
$sh=@'
set -u
ROOTS="xfce4 xfce4-goodies xrdp xorgxrdp thunar mousepad ristretto xfburn xarchiver gnome-terminal yelp pavucontrol pulseaudio ffmpeg gstreamer1.0-libav gstreamer1.0-plugins-bad gstreamer1.0-plugins-good gstreamer1.0-plugins-base libsoup-3.0-0 libwebkit2gtk-4.0-37 libgtk-3-0 libgtk-4-1"
DEBIAN_FRONTEND=noninteractive apt-get -s purge --autoremove $ROOTS 2>/dev/null | grep -E '^(Remv|Purg)' | awk '{print $2}' | sort -u > /tmp/b7_purge392.txt
echo "purge set = $(wc -l < /tmp/b7_purge392.txt)"
echo "=====1c-Xvfb. ldd /usr/bin/Xvfb -> any lib in the purge set?====="
Xv=$(command -v Xvfb || echo /usr/bin/Xvfb)
ldd "$Xv" 2>/dev/null | grep '=> /' | sed -E 's/.*=> (\/[^ ]+).*/\1/' | while read -r l; do p=$(dpkg -S "$l" 2>/dev/null|head -1|cut -d: -f1); echo "$p"; done | sort -u > /tmp/b7_xvfb_pkgs.txt
echo "  Xvfb links $(wc -l < /tmp/b7_xvfb_pkgs.txt) packages; IN purge set: [$(comm -12 /tmp/b7_xvfb_pkgs.txt /tmp/b7_purge392.txt | tr '\n' ' ')]"
echo "=====1c-dlopen. firefox RUNTIME deps (playwright firefox host list) -> which are in the purge set?====="
PWFF="libgtk-3-0 libx11-xcb1 libxcb1 libxcomposite1 libxcursor1 libxdamage1 libxfixes3 libxi6 libxtst6 libpangocairo-1.0-0 libcairo-gobject2 libgdk-pixbuf-2.0-0 libdbus-glib-1-2 libxt6 libpci3 libasound2 libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 libxrandr2 libgbm1 libdrm2 fonts-liberation"
echo "  playwright-firefox host deps that ARE in the purge set (-> must KEEP if browser matters):"
for p in $PWFF; do grep -qx "$p" /tmp/b7_purge392.txt && echo "    IN-PURGE: $p (installed=$(dpkg -l $p 2>/dev/null|awk '/^ii/{print "yes"}'))"; done
KEEPADD=$(for p in $PWFF; do grep -qx "$p" /tmp/b7_purge392.txt && echo "$p"; done)
echo "  KEEP-additions (dlopen): [$(printf '%s' "$KEEPADD" | tr '\n' ' ')]"
echo "=====recompute final purge = 392 - (xvfb-pkgs + dlopen KEEP + their Depends closure)====="
KEEP=$(cat /tmp/b7_xvfb_pkgs.txt; printf '%s\n' "$KEEPADD"; echo software-properties-common; echo python3-software-properties)
CLOSURE=$(for p in $KEEP; do [ -n "$p" ] || continue; echo "$p"; apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts --no-breaks --no-replaces --no-enhances "$p" 2>/dev/null | grep -E '^[a-z0-9]'; done | sort -u)
comm -23 /tmp/b7_purge392.txt <(printf '%s\n' "$CLOSURE" | sort -u) > /tmp/b7_final.txt
echo "  FINAL purge count: $(wc -l < /tmp/b7_final.txt)  (from 392)"
echo "  heavy hitters still removed: [$(grep -iE '^(xrdp|xorgxrdp|xfce4|xfce4-goodies|thunar|ffmpeg|libwebkit2gtk-4.0-37|gstreamer1.0-plugins-bad|libgtk-4-1|pulseaudio|yelp|ristretto|mousepad)$' /tmp/b7_final.txt | tr '\n' ' ')]"
echo "  firefox dlopen KEEP survived (not in final)? $(comm -12 <(printf '%s\n' "$KEEPADD"|sort) /tmp/b7_final.txt | grep -c .) in-final (expect 0)"
echo "  libgtk-3-0 in final purge? $(grep -qx libgtk-3-0 /tmp/b7_final.txt && echo 'YES(!)' || echo 'no -- KEPT')"
echo "=====FINAL explicit purge dry-run: removes exactly the list? touches any KEEP?====="
FIN=$(tr '\n' ' ' < /tmp/b7_final.txt)
DRY=$(DEBIAN_FRONTEND=noninteractive apt-get -s purge $FIN 2>&1)
echo "  summary: $(printf '%s\n' "$DRY" | grep -E '[0-9]+ to remove' | head -1)"
echo "  extra beyond the list: $(comm -13 <(sort /tmp/b7_final.txt) <(printf '%s\n' "$DRY"|grep -E '^(Remv|Purg)'|awk '{print $2}'|sort -u) | grep -c .) (expect 0)"
echo "  KEEP touched (xvfb/xserver-common/libgtk-3-0/software-properties/firefox-deps)? [$(printf '%s\n' "$DRY"|grep -E '^(Remv|Purg)'|awk '{print $2}'|grep -iE 'xvfb|xserver-common|libxfont2|libgtk-3-0|software-properties|libasound2|libdbus-glib' | tr '\n' ' ')]"
echo "  disk freed (installed-size of final set): $(dpkg-query -Wf '${Installed-Size}\n' $FIN 2>/dev/null | awk '{s+=$1} END{printf "~%.0f MB", s/1024}')"
echo "  (final list saved on box: /tmp/b7_final.txt -- will be the staged purge list)"
'@
$res=$sh | ssh -o BatchMode=yes -o ConnectTimeout=40 -o StrictHostKeyChecking=accept-new azureuser@trading.jacksumner.com "tr -d '\r\357\273\277'|bash"
foreach($line in ($res -split "`n")){ L ($line.TrimEnd()) }
[IO.File]::WriteAllText((Join-Path $OutDir '42_b7_item1c2_keep_final.txt'),(($log -join "`n")+"`n"),$enc)
Write-Output "--- wrote 42_b7_item1c2_keep_final.txt ---"
