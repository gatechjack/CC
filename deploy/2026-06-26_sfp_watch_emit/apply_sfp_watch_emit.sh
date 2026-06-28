#!/usr/bin/env bash
# SFP watch-state emit + heartbeat — CODE apply (2026-06-26). RUNBOOK STEP 2.
# md5-gated full-file swap of the detector + observer. Gate-A (prod-pre == base),
# backup, atomic swap, Gate-B (post == target), py_compile. NO restart here —
# the new code is INERT until the engine restarts (RUNBOOK STEP 3, operator-run).
# OBSERVE-ONLY change: decision-path functions are byte-identical (proven in the
# changeset); the emit cannot alter trade behaviour. SFP stays ARMED throughout.
# Operator: scp staged/ to ~/sfp_emit_staged, then: bash ~/apply_sfp_watch_emit.sh
set -euo pipefail
TC="$HOME/trading_corp"; SRC="${1:-$HOME/sfp_emit_staged}"; STAMP="bak-pre-sfp-emit-2026-06-26"
cd "$TC"; md5of(){ md5sum "$1" | cut -d' ' -f1; }
FILES=(trading_corp/agents/strategies/bitunix_sfp.py \
       trading_corp/agents/divisions/bitunix_sfp_observer.py)
declare -A BASE=(
  [trading_corp/agents/strategies/bitunix_sfp.py]=ad8e36f53b85df47acff21f3b6a5bf61
  [trading_corp/agents/divisions/bitunix_sfp_observer.py]=db831dafe28846d9da61104aaba7eff0
)
declare -A TARGET=(
  [trading_corp/agents/strategies/bitunix_sfp.py]=5c71a103575879df3fe36738fa8451fb
  [trading_corp/agents/divisions/bitunix_sfp_observer.py]=18da45f236b5b980e6312b6d435c8546
)
echo "== Gate-A: prod-pre md5 == pinned base =="
for f in "${FILES[@]}"; do c=$(md5of "$f"); [ "$c" = "${BASE[$f]}" ] || { echo "ABORT Gate-A $f: $c != ${BASE[$f]} (prod drifted — STOP)"; exit 1; }; echo "  OK base    $f  $c"; done
echo "== staged files present + LF =="
for f in "${FILES[@]}"; do [ -f "$SRC/$f" ] || { echo "ABORT: missing staged $SRC/$f"; exit 1; }; [ "$(tr -cd '\r' < "$SRC/$f" | wc -c)" = "0" ] || { echo "ABORT: $f has CR bytes"; exit 1; }; done
echo "== backup + atomic swap =="
for f in "${FILES[@]}"; do cp -p "$f" "$f.$STAMP"; cp "$SRC/$f" "$f.new"; mv "$f.new" "$f"; echo "  placed $f (backup $f.$STAMP)"; done
echo "== Gate-B: post md5 == target =="
for f in "${FILES[@]}"; do c=$(md5of "$f"); [ "$c" = "${TARGET[$f]}" ] || { echo "ABORT Gate-B $f: $c != ${TARGET[$f]}"; exit 1; }; echo "  OK target  $f  $c"; done
echo "== py_compile (prod venv) =="
venv/bin/python -m py_compile "${FILES[@]}" && echo "  py_compile OK"
echo "== DONE — code staged on disk, NOT restarted. New emit is INERT until STEP 3 (restart)."
echo "== bitunix.py NOT touched. SFP still ARMED + unchanged trade behaviour."
