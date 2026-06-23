#!/usr/bin/env bash
# Win-rate toggle post-apply VERIFY — READ-ONLY.
ROOT="${TC_ROOT:-/home/azureuser/trading_corp}"
TPL="$ROOT/trading_corp/web/templates/division.html"
echo "=== wr-toggle VERIFY (read-only) ==="
echo "--- new md5 (want 367ae47693ff8ff49026d92fc8bd6688) ---"; md5sum "$TPL" | awk '{print $1}'
echo "--- toggle markers present? ---"
grep -c 'data-wr-tab' "$TPL" | sed 's/^/  data-wr-tab count: /'
grep -c 'data-wr-slice' "$TPL" | sed 's/^/  data-wr-slice count: /'
grep -q 'No live trades resolved' "$TPL" && echo "  empty-live state: present"
grep -q 'all-time · signal replay' "$TPL" && echo "  paper scope label: present"
echo "--- OLD standalone panels gone? (want 0) ---"
echo -n "  'Live-trade win rate' header: "; grep -c 'Live-trade win rate' "$TPL"
echo "--- jinja parses? ---"
python3 -c "import jinja2; jinja2.Environment().parse(open('$TPL',encoding='utf-8').read()); print('  jinja OK')" 2>&1 | tail -1
echo "=== reminder ==="
echo "No restart needed (Jinja auto_reload). Refresh /division/bitunix_futures:"
echo "  default = LIVE ('No live trades resolved since 2026-06-23 yet.' until the"
echo "  02:00 trade resolves), Paper one click away (105W/49L). Paper-only"
echo "  divisions (kalshi/polymarket) unchanged (no toggle)."
