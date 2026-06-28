#!/usr/bin/env bash
# bitunix_sfp cockpit display fixes (2026-06-28) — md5-gated swap of 5 DISPLAY-ONLY
# files (view + 3 templates + 1 css). NOT order-path. Gate-A (prod==base, ABORT on
# drift), staged LF + md5==target, backup, atomic swap, Gate-B, py_compile the view.
# NO restart here — the view + templates load on the next restart (operator runs
# the arm-gate+restart runner next).
set -euo pipefail
TC="$HOME/trading_corp"; SRC="${1:-$HOME/sfp_cockpit_stage}"; STAMP="bak-pre-cockpit-2026-06-28"
cd "$TC"; md5of(){ md5sum "$1" | cut -d' ' -f1; }
VIEW=trading_corp/web/sfp_cockpit_view.py
FILES=("$VIEW" \
  trading_corp/web/templates/sfp_cockpit.html \
  trading_corp/web/templates/sfp_cockpit/_header.html \
  trading_corp/web/templates/sfp_cockpit/_state_board.html \
  trading_corp/web/static/sfp_cockpit.css)
declare -A BASE=(
  [trading_corp/web/sfp_cockpit_view.py]=de8d90e380232755abc4f74b639c4c66
  [trading_corp/web/templates/sfp_cockpit.html]=fb84f5aa1bda4418a14766b72c1515b3
  [trading_corp/web/templates/sfp_cockpit/_header.html]=29be852f967cbbd2ff7cc259a0fbc9d1
  [trading_corp/web/templates/sfp_cockpit/_state_board.html]=57de1f1756052ff5bff54551b45c6e30
  [trading_corp/web/static/sfp_cockpit.css]=40665c54999f1a5a4471cb36135aa7ff
)
declare -A TARGET=(
  [trading_corp/web/sfp_cockpit_view.py]=2a6d0e5644489a9f1cd34c11784bf805
  [trading_corp/web/templates/sfp_cockpit.html]=7ad02ab0b256caab308653dfb839c029
  [trading_corp/web/templates/sfp_cockpit/_header.html]=bf0a2380744be9214d839a91aa6744ae
  [trading_corp/web/templates/sfp_cockpit/_state_board.html]=39dcc5fb12f8720eb3830b0e34853d2b
  [trading_corp/web/static/sfp_cockpit.css]=dd9d59d93984e8c9c43ff6be1a4934df
)
echo "== Gate-A: prod-pre md5 == base (ABORT on drift) =="
for f in "${FILES[@]}"; do c=$(md5of "$f"); [ "$c" = "${BASE[$f]}" ] || { echo "ABORT Gate-A $f: $c != ${BASE[$f]}"; exit 1; }; echo "  OK base   $f"; done
echo "== staged LF (no CR) + md5 == target =="
for f in "${FILES[@]}"; do
  [ -f "$SRC/$f" ] || { echo "ABORT: missing staged $SRC/$f"; exit 1; }
  [ "$(tr -cd '\r' < "$SRC/$f" | wc -c)" = "0" ] || { echo "ABORT: staged $f has CR bytes"; exit 1; }
  c=$(md5of "$SRC/$f"); [ "$c" = "${TARGET[$f]}" ] || { echo "ABORT: staged $f md5 $c != ${TARGET[$f]}"; exit 1; }
done
echo "== backup + atomic swap =="
for f in "${FILES[@]}"; do cp -p "$f" "$f.$STAMP"; cp "$SRC/$f" "$f.new"; mv "$f.new" "$f"; echo "  placed $f (backup $f.$STAMP)"; done
echo "== Gate-B: post md5 == target =="
for f in "${FILES[@]}"; do c=$(md5of "$f"); [ "$c" = "${TARGET[$f]}" ] || { echo "ABORT Gate-B $f: $c != ${TARGET[$f]}"; exit 1; }; echo "  OK target $f"; done
echo "== py_compile the view (prod venv) =="
venv/bin/python -m py_compile "$VIEW" && echo "  py_compile OK"
echo "== DONE — 5 cockpit files swapped (display-only). Restart to load the view + templates."
