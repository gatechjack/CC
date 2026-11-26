# secrev_b7_item1c_probe.ps1 -- Batch 7 item 1c. READ-ONLY. ldd the playwright firefox + its .so, cross-check every
# resolved library against the 392-pkg surgical purge set. Identify KEEP-additions + any missing (not-found) libs.
param([string]$OutDir='C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis')
$ErrorActionPreference='Continue'
$enc=New-Object System.Text.UTF8Encoding $false; [Console]::OutputEncoding=$enc; $OutputEncoding=$enc
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$log=New-Object System.Collections.Generic.List[string]
function L([string]$s){ $log.Add($s); Write-Output $s }
$sh=@'
set -u
FFDIR=/home/azureuser/.cache/ms-playwright/firefox-1511/firefox
FFMPEG=/home/azureuser/.cache/ms-playwright/ffmpeg-1011/ffmpeg-linux
echo "=====build the 392 surgical purge set (apt-get -s purge --autoremove ROOTS)====="
ROOTS="xfce4 xfce4-goodies xrdp xorgxrdp thunar mousepad ristretto xfburn xarchiver gnome-terminal yelp pavucontrol pulseaudio ffmpeg gstreamer1.0-libav gstreamer1.0-plugins-bad gstreamer1.0-plugins-good gstreamer1.0-plugins-base libsoup-3.0-0 libwebkit2gtk-4.0-37 libgtk-3-0 libgtk-4-1"
DEBIAN_FRONTEND=noninteractive apt-get -s purge --autoremove $ROOTS 2>/dev/null | grep -E '^(Remv|Purg)' | awk '{print $2}' | sort -u > /tmp/b7_purge392.txt
echo "  purge set size: $(wc -l < /tmp/b7_purge392.txt)"
echo "=====ldd firefox binary + all firefox/*.so + ffmpeg -> resolved system libs + NOT-FOUND====="
: > /tmp/b7_ff_libs.txt; : > /tmp/b7_ff_notfound.txt
for so in "$FFDIR/firefox" "$FFDIR"/*.so "$FFMPEG"; do
  [ -e "$so" ] || continue
  ldd "$so" 2>/dev/null | grep '=> /' | sed -E 's/.*=> (\/[^ ]+).*/\1/' >> /tmp/b7_ff_libs.txt
  ldd "$so" 2>/dev/null | grep -iE 'not found' | sed -E 's/^[[:space:]]*//' >> /tmp/b7_ff_notfound.txt
done
sort -u /tmp/b7_ff_libs.txt -o /tmp/b7_ff_libs.txt
echo "  distinct resolved system libs firefox links: $(wc -l < /tmp/b7_ff_libs.txt)"
echo "  NOT-FOUND libs (firefox needs but missing -> explains launch failure):"
sort -u /tmp/b7_ff_notfound.txt | sed 's/^/    /' ; [ -s /tmp/b7_ff_notfound.txt ] || echo "    (none -- all firefox libs resolve)"
echo "=====map resolved libs -> owning package -> in the 392 purge set?====="
: > /tmp/b7_ff_pkgs.txt
while IFS= read -r lib; do
  pkg=$(dpkg -S "$lib" 2>/dev/null | head -1 | cut -d: -f1)
  [ -n "$pkg" ] && echo "$pkg" >> /tmp/b7_ff_pkgs.txt
done < /tmp/b7_ff_libs.txt
sort -u /tmp/b7_ff_pkgs.txt -o /tmp/b7_ff_pkgs.txt
echo "  distinct packages providing firefox's libs: $(wc -l < /tmp/b7_ff_pkgs.txt)"
echo "  *** KEEP-ADDITIONS: firefox-needed packages that ARE in the 392 purge set (MUST move to KEEP) ***"
comm -12 /tmp/b7_ff_pkgs.txt /tmp/b7_purge392.txt | sed 's/^/    KEEP: /'
KN=$(comm -12 /tmp/b7_ff_pkgs.txt /tmp/b7_purge392.txt | grep -c .)
echo "  KEEP-additions count: $KN"
echo "=====recompute: final purge = 392 - firefox-needed (+ what those pull, via depends closure)====="
# expand KEEP-additions with their recursive Depends so we do not strand a firefox dep
cp /tmp/b7_purge392.txt /tmp/b7_final.txt
KEEP=$(comm -12 /tmp/b7_ff_pkgs.txt /tmp/b7_purge392.txt)
CLOSURE=$(for p in $KEEP; do echo "$p"; apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts --no-breaks --no-replaces --no-enhances "$p" 2>/dev/null | grep -E '^[a-z0-9]' ; done | sort -u)
comm -23 /tmp/b7_purge392.txt <(printf '%s\n' "$CLOSURE" | sort -u) > /tmp/b7_final2.txt
echo "  final purge count after removing firefox-needed closure: $(wc -l < /tmp/b7_final2.txt)  (was 392)"
echo "  still-removed heavy hitters (sanity): $(grep -iE 'xrdp|xfce4$|ffmpeg$|libwebkit2gtk|gstreamer1.0-plugins-bad|thunar$' /tmp/b7_final2.txt | tr '\n' ' ')"
echo "  did any firefox-needed pkg survive in final? $(comm -12 /tmp/b7_ff_pkgs.txt /tmp/b7_final2.txt | grep -c .) (expect 0)"
echo "=====does the FINAL explicit purge dry-run remove exactly the list (no extra, no firefox lib)?====="
FINAL=$(tr '\n' ' ' < /tmp/b7_final2.txt)
DRY=$(DEBIAN_FRONTEND=noninteractive apt-get -s purge $FINAL 2>&1)
echo "  dry-run summary: $(printf '%s\n' "$DRY" | grep -E 'to remove' | head -1)"
echo "  dry-run removes anything NOT in the final list (extra)? $(comm -13 <(sort /tmp/b7_final2.txt) <(printf '%s\n' "$DRY" | grep -E '^(Remv|Purg)' | awk '{print $2}' | sort -u) | grep -c .) (expect 0)"
echo "  dry-run touches a firefox lib / xvfb / xserver-common / software-properties? $(printf '%s\n' "$DRY" | grep -E '^(Remv|Purg)' | awk '{print $2}' | grep -iE 'xvfb|xserver-common|libxfont2|^xauth$|x11-xkb-utils|libgl1$|libpixman|software-properties|'"$(tr '\n' '|' < /tmp/b7_ff_pkgs.txt | sed 's/|$//')"'' | tr '\n' ' ')"
'@
$res=$sh | ssh -o BatchMode=yes -o ConnectTimeout=40 -o StrictHostKeyChecking=accept-new azureuser@trading.jacksumner.com "tr -d '\r\357\273\277'|bash"
foreach($line in ($res -split "`n")){ L ($line.TrimEnd()) }
[IO.File]::WriteAllText((Join-Path $OutDir '41_b7_item1c_ldd_crosscheck.txt'),(($log -join "`n")+"`n"),$enc)
Write-Output "--- wrote 41_b7_item1c_ldd_crosscheck.txt ---"
