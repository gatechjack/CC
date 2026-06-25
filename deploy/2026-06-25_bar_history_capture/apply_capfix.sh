#!/usr/bin/env bash
# Capture-fix CODE apply (2026-06-25) — RUNBOOK STEP 3. Run AFTER the migration
# (step 2), while the engine is STILL STOPPED. Gates each file (Gate-A prod-pre
# == pinned base / Gate-B post == target), backups, atomic swap, py_compile.
# NO restart, NO migration, NO backfill (those are separate runbook steps).
# Operator: scp staged/ to ~/capfix_staged, then: bash ~/apply_capfix.sh
set -euo pipefail
TC="$HOME/trading_corp"; SRC="${1:-$HOME/capfix_staged}"; STAMP="bak-pre-capfix-2026-06-25"
cd "$TC"; md5of(){ md5sum "$1" | cut -d' ' -f1; }
FILES=(trading_corp/agents/divisions/bitunix_position_reconciler.py \
       trading_corp/main.py trading_corp/data/bitunix_bar_archiver.py)
declare -A BASE=(
  [trading_corp/agents/divisions/bitunix_position_reconciler.py]=8c3adcd173c3a9f65e596e64db7ef6e8
  [trading_corp/main.py]=2b504cbce7410334fed3908d153734cf
  [trading_corp/data/bitunix_bar_archiver.py]=f83a305f0d096503ea308cacbaa08ef0
)
declare -A TARGET=(
  [trading_corp/agents/divisions/bitunix_position_reconciler.py]=3a23610c9e2bbd3d863163f657eeca36
  [trading_corp/main.py]=82a01f83ac4ed6043871362ff7c77a1b
  [trading_corp/data/bitunix_bar_archiver.py]=53c2e64d8bb28d882254b4488cfca7d5
)
echo "== Gate-A: prod-pre md5 == pinned base =="
for f in "${FILES[@]}"; do c=$(md5of "$f"); [ "$c" = "${BASE[$f]}" ] || { echo "ABORT Gate-A $f: $c != ${BASE[$f]} (prod drifted — STOP)"; exit 1; }; echo "  OK base  $f  $c"; done
echo "== staged files present + LF =="
for f in "${FILES[@]}"; do [ -f "$SRC/$f" ] || { echo "ABORT: missing staged $SRC/$f"; exit 1; }; [ "$(tr -cd '\r' < "$SRC/$f" | wc -c)" = "0" ] || { echo "ABORT: $f has CR bytes"; exit 1; }; done
echo "== backup + atomic swap =="
for f in "${FILES[@]}"; do cp -p "$f" "$f.$STAMP"; cp "$SRC/$f" "$f.new"; mv "$f.new" "$f"; echo "  placed $f (backup $f.$STAMP)"; done
echo "== Gate-B: post md5 == target =="
for f in "${FILES[@]}"; do c=$(md5of "$f"); [ "$c" = "${TARGET[$f]}" ] || { echo "ABORT Gate-B $f: $c != ${TARGET[$f]}"; exit 1; }; echo "  OK target  $f  $c"; done
echo "== py_compile (prod venv) =="
venv/bin/python -m py_compile "${FILES[@]}" && echo "  py_compile OK"
echo "== DONE — code staged, NO restart performed. Next: start engine (step 4), then backfill (step 5). =="
