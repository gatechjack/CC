J=/tmp/pmj2_$$.txt
journalctl -u trading-corp --since "2026-09-28 00:00:00" --no-pager -o short-iso 2>&1 | grep -iE 'boot-reconcile|MACE wired|HTTP 429|confirmed whale-ENTRY|new_cids=\[0x|OPPOSING-PAIR|force-latch' > "$J" 2>&1
echo "matched lines:"; wc -l "$J"; ls -la "$J"
echo "===== RESTARTS: boot-reconcile + MACE-wired markers (each boot) ====="
grep -iE 'boot-reconcile account=|MACE wired' "$J"
echo "===== boot-reconcile LATCH results (latched=True/False) ====="
grep -iE 'boot-reconcile account=' "$J" | sed 's/.*boot-reconcile //'
echo "===== HTTP 429 per hour ====="
grep 'HTTP 429' "$J" | awk '{print substr($1,1,13)}' | uniq -c
echo "===== HTTP 429 first + last ====="
grep 'HTTP 429' "$J" | head -1; echo "..."; grep 'HTTP 429' "$J" | tail -1
echo "429 total:"; grep -c 'HTTP 429' "$J"
echo "===== whale-ENTRY confirmations: count + last 6 ====="
grep -c 'confirmed whale-ENTRY' "$J"; grep 'confirmed whale-ENTRY' "$J" | tail -6
echo "===== new_cids with REAL content (new copyable opens detected): count + last 6 ====="
grep -c 'new_cids=\[0x' "$J"; grep 'new_cids=\[0x' "$J" | tail -6
echo "===== last ENTRY-side activity timestamp before restart ====="
grep -iE 'confirmed whale-ENTRY|new_cids=\[0x' "$J" | tail -1
rm -f "$J"
echo "===== DONE diag4 ====="
