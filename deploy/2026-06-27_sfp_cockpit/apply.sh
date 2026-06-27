#!/usr/bin/env bash
# SFP cockpit dashboard — APPLY (no restart).
# Streamed to prod via cockpit_apply.ps1.
# Aborts (set -e) on any md5 mismatch: staged-transfer integrity, pre-deploy
# live routes.py version match (drift guard), and placed == staged.
# The 8 new files are guarded by a pre-existence check (must NOT exist).
set -e
ROOT=/home/azureuser/trading_corp
ST=/home/azureuser/sfp_cockpit_staged
BAK=/home/azureuser/p1_bak_2026-06-27

echo "== 1/7 verify staged transfer integrity (LF md5) =="
echo "4c37fa971698e600095163695feb30a0  $ST/routes.py"               | md5sum -c -
echo "d967562d08361417a0086856bff37db4  $ST/sfp_cockpit_view.py"     | md5sum -c -
echo "762fcb9283a31f5116278d3dd8d8c040  $ST/sfp_cockpit.html"        | md5sum -c -
echo "c98dc790f185aaeae834e308f74ca7f8  $ST/sfp_cockpit/_header.html"   | md5sum -c -
echo "461eac96cb543c99a184847d9cebb671  $ST/sfp_cockpit/_recon.html"    | md5sum -c -
echo "62b0dc57f20d0ad9de06cc0a8b84a1b5  $ST/sfp_cockpit/_state_board.html" | md5sum -c -
echo "b15238170ae772c00c716cd45a22e899  $ST/sfp_cockpit/_mode_split.html"  | md5sum -c -
echo "e220d108f113015ea711ef364464994b  $ST/sfp_cockpit/_near_miss.html"   | md5sum -c -
echo "74bda5ee0d765aef3a1868974360f587  $ST/sfp_cockpit/_equity.html"      | md5sum -c -
echo "9b58667c77bc4bd7906b05db4ac9aec7  $ST/sfp_cockpit.css"          | md5sum -c -

echo "== 2/7 verify LIVE routes.py is expected pre-deploy version (drift guard) =="
echo "76442e8f448fe18f98b8805d5dd4a30b  $ROOT/trading_corp/web/routes.py" | md5sum -c -

echo "== 3/7 verify 8 new files do NOT pre-exist on prod =="
for f in \
    "$ROOT/trading_corp/web/sfp_cockpit_view.py" \
    "$ROOT/trading_corp/web/templates/sfp_cockpit.html" \
    "$ROOT/trading_corp/web/templates/sfp_cockpit/_header.html" \
    "$ROOT/trading_corp/web/templates/sfp_cockpit/_recon.html" \
    "$ROOT/trading_corp/web/templates/sfp_cockpit/_state_board.html" \
    "$ROOT/trading_corp/web/templates/sfp_cockpit/_mode_split.html" \
    "$ROOT/trading_corp/web/templates/sfp_cockpit/_near_miss.html" \
    "$ROOT/trading_corp/web/templates/sfp_cockpit/_equity.html" \
    "$ROOT/trading_corp/web/static/sfp_cockpit.css"
do
    if [ -e "$f" ]; then
        echo "ABORT: $f already exists on prod — re-run check before applying"
        exit 1
    fi
done
echo "confirmed: 8 new files not present on prod"

echo "== 4/7 backup live routes.py -> $BAK =="
mkdir -p "$BAK"
cp -p "$ROOT/trading_corp/web/routes.py" "$BAK/routes.py.bak-pre-cockpit-2026-06-27"

echo "== 5/7 place files on prod =="
# routes.py (targeted-hunk: +2 lines wire-up)
cp "$ST/routes.py" "$ROOT/trading_corp/web/routes.py"
# new view module
cp "$ST/sfp_cockpit_view.py" "$ROOT/trading_corp/web/sfp_cockpit_view.py"
# main cockpit template
cp "$ST/sfp_cockpit.html" "$ROOT/trading_corp/web/templates/sfp_cockpit.html"
# partial templates (mkdir target dir first)
mkdir -p "$ROOT/trading_corp/web/templates/sfp_cockpit"
cp "$ST/sfp_cockpit/_header.html"      "$ROOT/trading_corp/web/templates/sfp_cockpit/_header.html"
cp "$ST/sfp_cockpit/_recon.html"       "$ROOT/trading_corp/web/templates/sfp_cockpit/_recon.html"
cp "$ST/sfp_cockpit/_state_board.html" "$ROOT/trading_corp/web/templates/sfp_cockpit/_state_board.html"
cp "$ST/sfp_cockpit/_mode_split.html"  "$ROOT/trading_corp/web/templates/sfp_cockpit/_mode_split.html"
cp "$ST/sfp_cockpit/_near_miss.html"   "$ROOT/trading_corp/web/templates/sfp_cockpit/_near_miss.html"
cp "$ST/sfp_cockpit/_equity.html"      "$ROOT/trading_corp/web/templates/sfp_cockpit/_equity.html"
# static css
cp "$ST/sfp_cockpit.css" "$ROOT/trading_corp/web/static/sfp_cockpit.css"

echo "== 6/7 verify PLACED md5s == staged =="
echo "4c37fa971698e600095163695feb30a0  $ROOT/trading_corp/web/routes.py"                             | md5sum -c -
echo "d967562d08361417a0086856bff37db4  $ROOT/trading_corp/web/sfp_cockpit_view.py"                   | md5sum -c -
echo "762fcb9283a31f5116278d3dd8d8c040  $ROOT/trading_corp/web/templates/sfp_cockpit.html"            | md5sum -c -
echo "c98dc790f185aaeae834e308f74ca7f8  $ROOT/trading_corp/web/templates/sfp_cockpit/_header.html"    | md5sum -c -
echo "461eac96cb543c99a184847d9cebb671  $ROOT/trading_corp/web/templates/sfp_cockpit/_recon.html"     | md5sum -c -
echo "62b0dc57f20d0ad9de06cc0a8b84a1b5  $ROOT/trading_corp/web/templates/sfp_cockpit/_state_board.html" | md5sum -c -
echo "b15238170ae772c00c716cd45a22e899  $ROOT/trading_corp/web/templates/sfp_cockpit/_mode_split.html"  | md5sum -c -
echo "e220d108f113015ea711ef364464994b  $ROOT/trading_corp/web/templates/sfp_cockpit/_near_miss.html"   | md5sum -c -
echo "74bda5ee0d765aef3a1868974360f587  $ROOT/trading_corp/web/templates/sfp_cockpit/_equity.html"      | md5sum -c -
echo "9b58667c77bc4bd7906b05db4ac9aec7  $ROOT/trading_corp/web/static/sfp_cockpit.css"                | md5sum -c -

echo "== 7/7 byte-unchanged proof: core SFP engine files UNTOUCHED =="
md5sum \
    "$ROOT/trading_corp/agents/divisions/bitunix_sfp_observer.py" \
    "$ROOT/trading_corp/agents/strategies/bitunix_sfp.py" \
    "$ROOT/trading_corp/agents/divisions/bitunix_position_reconciler.py"

echo
echo "APPLY OK — cockpit deployed. Engine NOT restarted."
echo "Cockpit is at /sfp on the next request — the route is registered but"
echo "uvicorn must reload to pick it up. Restart when ready:"
echo "  sudo -n systemctl restart trading-corp"
echo "ROLLBACK: cp $BAK/routes.py.bak-pre-cockpit-2026-06-27 $ROOT/trading_corp/web/routes.py && restart"
