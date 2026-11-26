# secrev_b6_item3_probe.ps1 -- Batch 6 item 3: desktop-leftovers removal SCOPING. READ-ONLY, no root, stage nothing.
param([string]$OutDir='C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis')
$ErrorActionPreference='Continue'
$enc=New-Object System.Text.UTF8Encoding $false; [Console]::OutputEncoding=$enc; $OutputEncoding=$enc
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$log=New-Object System.Collections.Generic.List[string]
function L([string]$s){ $log.Add($s); Write-Output $s }
$sh=@'
set -u
PAT='^(xrdp|xorgxrdp|xserver-xorg|xserver-common|xorg|x11-|xauth|xfonts|lightdm|xfce4|xfconf|xfdesktop|xfwm|xfce|exo-|libexo|thunar|tumbler|garcon|ristretto|parole|mousepad|xfburn|libxfce|ffmpeg|libavcodec|libavdevice|libavfilter|libavformat|libavutil|libswscale|libswresample|libpostproc|gstreamer|libgstreamer|gir1.2-gst|libsoup|libzbar|libzvbi|libopenexr|libde265|libaom|libsrt|fluidsynth|libfluidsynth|dbus-x11|at-spi|gvfs|libwebkit|libgtk|desktop-base|plymouth)'
echo "=====A. installed desktop/RDP/X/media packages (count + list)====="
INV=$(dpkg-query -W -f='${Package}\n' 2>/dev/null | grep -iE "$PAT" | sort)
echo "  count=$(printf '%s\n' "$INV" | grep -c .)"
printf '%s\n' "$INV" | sed 's/^/    /'
echo "=====B. which of these are MANUALLY installed (top-level roots to purge)====="
comm -12 <(apt-mark showmanual 2>/dev/null | sort) <(printf '%s\n' "$INV") | sed 's/^/    /' || echo "    (none manual)"
echo "=====C. reverse-deps (INSTALLED) of key candidates -- who still needs them?====="
for p in xrdp ffmpeg libsoup-3.0-0 libavcodec58 gstreamer1.0-plugins-bad libexo-2-0 libopenexr25 libzbar0; do
  rd=$(apt-cache rdepends --installed "$p" 2>/dev/null | sed -n '2,$p' | sed 's/^ *//' | grep -vE "^$" | sort -u)
  echo "  $p <- [$(printf '%s' "$rd" | tr '\n' ' ')]"
done
echo "=====D. does the TRADING venv / python link any of these? (ldd of compiled .so)====="
VENV=/home/azureuser/trading_corp/venv/lib/python3.12/site-packages
echo "  pillow/PIL compiled .so -> system libav/soup/gst/openexr/zbar?"
hit=0; for so in $(find "$VENV/PIL" -name '*.so*' 2>/dev/null); do m=$(ldd "$so" 2>/dev/null | grep -iE 'libav|libsoup|gstreamer|libswscale|libopenexr|libzbar|libde265|libaom'); [ -n "$m" ] && { echo "    $(basename $so): $m"; hit=1; }; done; [ "$hit" = 0 ] && echo "    (PIL links NONE of them -- Pillow bundles its own libs)"
echo "  other venv .so linking these libs:"
grep_hit=0; for so in $(find "$VENV" -name '*.so' 2>/dev/null); do m=$(ldd "$so" 2>/dev/null | grep -iE 'libsoup|libavcodec|libgstreamer|libswscale|libopenexr|libzbar'); [ -n "$m" ] && { echo "    $(echo $so | sed "s#$VENV/##"): $(printf '%s' "$m" | tr '\n' ' ')"; grep_hit=1; }; done; [ "$grep_hit" = 0 ] && echo "    (NO venv .so links any of them)"
echo "  playwright browsers present + link system libsoup/gst?"
if [ -d /home/azureuser/.cache/ms-playwright ]; then echo "    ms-playwright present: $(ls /home/azureuser/.cache/ms-playwright 2>/dev/null | tr '\n' ' ')"; else echo "    (no ms-playwright cache -- browsers not installed)"; fi
echo "=====E. does ANY running process load these libs right now?====="
for L in libsoup-3.0 libavcodec libavformat libgstreamer libswscale libopenexr libzbar libexo; do
  n=$(for pid in $(ls /proc | grep -E '^[0-9]+$'); do cat /proc/$pid/maps 2>/dev/null; done | grep -c "/$L" 2>/dev/null); echo "  $L mapped by any proc: ${n:-0}"
done
echo "=====F. apt purge --autoremove DRY-RUN (what would be removed) -- STAGE NOTHING====="
CANDS=$(printf '%s\n' "$INV" | tr '\n' ' ')
echo "  purging candidate set + autoremove (simulation only):"
DRY=$(DEBIAN_FRONTEND=noninteractive apt-get -s purge --autoremove $CANDS 2>&1)
echo "  --- summary line ---"; printf '%s\n' "$DRY" | grep -E 'to remove|newly installed|not upgraded' | sed 's/^/    /'
echo "  --- REMOVED set (Remv/Purg) ---"; printf '%s\n' "$DRY" | grep -E '^(Remv|Purg)' | awk '{print $2}' | sort | sed 's/^/    /'
echo "  --- FLAG: anything trading-relevant in the removal cascade? ---"
printf '%s\n' "$DRY" | grep -E '^(Remv|Purg)' | awk '{print $2}' | grep -iE 'python3\.1|python3-|trading|caddy|authelia|openssl|libssl|libc6|curl|ca-certificates|systemd|azure|waagent|chrony|openssh|cron' | sed 's/^/    FLAG: /' || echo "    (none trading-relevant flagged)"
echo "  --- would it REMOVE or HOLD-back anything unexpected? (Err/held) ---"
printf '%s\n' "$DRY" | grep -iE 'E:|held back|but it is|not going to be installed' | sed 's/^/    /' | head
'@
$res=$sh | ssh -o BatchMode=yes -o ConnectTimeout=35 -o StrictHostKeyChecking=accept-new azureuser@trading.jacksumner.com "tr -d '\r\357\273\277'|bash"
foreach($line in ($res -split "`n")){ L ($line.TrimEnd()) }
[IO.File]::WriteAllText((Join-Path $OutDir '39_b6_item3_desktop_purge_scope.txt'),(($log -join "`n")+"`n"),$enc)
Write-Output "--- wrote 39_b6_item3_desktop_purge_scope.txt ---"
