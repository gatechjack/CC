#!/usr/bin/env bash
# SFP TP-placement fix — CODE apply (2026-06-26). RUNBOOK STEP 2.
# Gates each file (Gate-A prod-pre == pinned base / Gate-B post == target),
# backups, atomic swap, py_compile. CODE-ONLY: NO restart, NO migration, NO
# re-arm. The new observer code is INERT until the engine is restarted (a
# SEPARATE, operator-gated go-live step). SFP stays DISARMED (auto_execute:false)
# throughout — do NOT re-arm before the restart-on-new-code (else the OLD,
# TP-less observer would run armed = the blocker returns).
# Operator: scp staged/ to ~/sfp_tpfix_staged, then: bash ~/apply_sfp_tp_fix.sh
set -euo pipefail
TC="$HOME/trading_corp"; SRC="${1:-$HOME/sfp_tpfix_staged}"; STAMP="bak-pre-sfp-tpfix-2026-06-26"
cd "$TC"; md5of(){ md5sum "$1" | cut -d' ' -f1; }
FILES=(trading_corp/agents/divisions/bitunix_sfp_observer.py \
       trading_corp/main.py)
declare -A BASE=(
  [trading_corp/agents/divisions/bitunix_sfp_observer.py]=b2b856be78aadaab153e2100fbe8ed1b
  [trading_corp/main.py]=82a01f83ac4ed6043871362ff7c77a1b
)
declare -A TARGET=(
  [trading_corp/agents/divisions/bitunix_sfp_observer.py]=db831dafe28846d9da61104aaba7eff0
  [trading_corp/main.py]=1069a6db98da8cffbf34bb8f365bc4e6
)
echo "== Gate-A: prod-pre md5 == pinned base =="
for f in "${FILES[@]}"; do c=$(md5of "$f"); [ "$c" = "${BASE[$f]}" ] || { echo "ABORT Gate-A $f: $c != ${BASE[$f]} (prod drifted — STOP, re-stage)"; exit 1; }; echo "  OK base    $f  $c"; done
echo "== staged files present + LF =="
for f in "${FILES[@]}"; do [ -f "$SRC/$f" ] || { echo "ABORT: missing staged $SRC/$f"; exit 1; }; [ "$(tr -cd '\r' < "$SRC/$f" | wc -c)" = "0" ] || { echo "ABORT: $f has CR bytes"; exit 1; }; done
echo "== backup + atomic swap =="
for f in "${FILES[@]}"; do cp -p "$f" "$f.$STAMP"; cp "$SRC/$f" "$f.new"; mv "$f.new" "$f"; echo "  placed $f (backup $f.$STAMP)"; done
echo "== Gate-B: post md5 == target =="
for f in "${FILES[@]}"; do c=$(md5of "$f"); [ "$c" = "${TARGET[$f]}" ] || { echo "ABORT Gate-B $f: $c != ${TARGET[$f]}"; exit 1; }; echo "  OK target  $f  $c"; done
echo "== py_compile (prod venv) =="
venv/bin/python -m py_compile "${FILES[@]}" && echo "  py_compile OK"
echo "== DONE — code staged on disk, engine NOT restarted, SFP STILL DISARMED. =="
echo "== Go-live is SEPARATE (operator-gated): restart-on-new-code -> boot smoke -> re-arm auto_execute:true. =="
