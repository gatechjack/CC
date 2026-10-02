echo "== tracebacks SINCE restart (20:08:06Z) -- expect 0 =="
timeout 40 journalctl -u trading-corp --since "2026-10-02 20:08:06" --no-pager 2>/dev/null | grep -c -iE "traceback"
echo "== the 1 since-midnight traceback timestamp(s) (confirm pre-restart) =="
timeout 60 journalctl -u trading-corp --since "2026-10-02 00:00:00" --no-pager 2>/dev/null | grep -iE "traceback" | sed 's/ tc-prod-vm.*//' | head -5
echo "== any mace error/mark_unavailable since restart =="
timeout 40 journalctl -u trading-corp --since "2026-10-02 20:08:06" --no-pager 2>/dev/null | grep -ciE "mace_.*error|mark_unavailable|mace_manage_error"
echo "DONE_TBCHECK"
