#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
#  Win-rate Paper/Live TOGGLE — dashboard-only template swap (2026-06-23).
#  Replaces division.html's two win-rate panels (Paper-trade + Live-trade) with
#  ONE panel + a client-side Paper/Live toggle (default LIVE). Pure front-end:
#  both slices are already computed in data.py:paper_trade_summary. No data.py
#  change, NO trading-path code, NO restart (Jinja FileSystemLoader auto_reload
#  picks the template up on the next request).
#
#  Surgical + reversible: drift-gated on the prod blob md5, ships the FULL new
#  template (built against that exact prod blob), backs up, swaps, verifies the
#  new md5, jinja-parses, self-rolls-back on any failure.
#  Rollback: cp <bak> <tpl>  (also hot, no restart).
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail
ROOT="${TC_ROOT:-/home/azureuser/trading_corp}"
TPL="$ROOT/trading_corp/web/templates/division.html"
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/stage/trading_corp/web/templates/division.html"
BAK="$TPL.bak-pre-wrtoggle-2026-06-23"
OLD_MD5="b6e23456a1cfcec484f41c5b3ce6e61e"
NEW_MD5="367ae47693ff8ff49026d92fc8bd6688"

echo "[wr] template = $TPL"
[ -f "$TPL" ] || { echo "[wr] ABORT: missing $TPL"; exit 2; }
[ -f "$SRC" ] || { echo "[wr] ABORT: missing staged file $SRC"; exit 2; }

cur=$(md5sum "$TPL" | awk '{print $1}')
echo "[wr] current prod md5 : $cur"
[ "$cur" = "$OLD_MD5" ] || { echo "[wr] ABORT: prod division.html drifted (want $OLD_MD5)"; exit 3; }

sm=$(md5sum "$SRC" | awk '{print $1}')
echo "[wr] staged file md5  : $sm"
[ "$sm" = "$NEW_MD5" ] || { echo "[wr] ABORT: staged file md5 != $NEW_MD5 (corrupt transfer?)"; exit 3; }

[ -e "$BAK" ] && { echo "[wr] ABORT: backup already exists ($BAK)"; exit 4; }
cp -p "$TPL" "$BAK"; echo "[wr] backup: $BAK"

cp "$SRC" "$TPL"
nm=$(md5sum "$TPL" | awk '{print $1}')
echo "[wr] new prod md5     : $nm"
[ "$nm" = "$NEW_MD5" ] || { echo "[wr] FAIL: post-copy md5 mismatch — ROLLING BACK"; cp -p "$BAK" "$TPL"; exit 5; }

python3 -c "import jinja2; jinja2.Environment().parse(open('$TPL',encoding='utf-8').read()); print('[wr] jinja parse OK')" \
  || { echo "[wr] FAIL: jinja parse — ROLLING BACK"; cp -p "$BAK" "$TPL"; exit 6; }

echo "[wr] ─────────────────────────────────────────────────────────────────"
echo "[wr] DONE — toggle template applied. NO restart needed (hot-reload)."
echo "[wr] Refresh /division/bitunix_futures — default LIVE view, Paper one click."
echo "[wr] ROLLBACK: cp \"$BAK\" \"$TPL\"   (hot, no restart)"
