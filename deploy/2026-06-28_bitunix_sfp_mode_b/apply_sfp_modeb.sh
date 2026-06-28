#!/usr/bin/env bash
# bitunix_sfp Mode B (15m SFP -> 3m BOS) — CODE+CONFIG apply (2026-06-28).
# md5-gated full-file swap of 3 .py + strategies.yaml from ~/sfp_modeb_stage.
# Gate-A (prod-pre == pinned base, ABORT on drift), staged LF + md5==target check,
# backup, atomic swap, Gate-B (post == target), py_compile via prod venv. NO
# restart here — the new code is INERT until the engine restarts (the operator
# runs the ARM-GATE+restart runner next). SfpDetector class is byte-unchanged
# (git diff: 0 deletions; Mode-A parity green) — this only ADDS Mode B.
set -euo pipefail
TC="$HOME/trading_corp"; SRC="${1:-$HOME/sfp_modeb_stage}"; STAMP="bak-pre-modeb-2026-06-28"
cd "$TC"; md5of(){ md5sum "$1" | cut -d' ' -f1; }
PYFILES=(trading_corp/agents/strategies/bitunix_sfp.py \
         trading_corp/agents/divisions/bitunix_sfp_observer.py \
         trading_corp/main.py)
FILES=("${PYFILES[@]}" config/strategies.yaml)
declare -A BASE=(
  [trading_corp/agents/strategies/bitunix_sfp.py]=5c71a103575879df3fe36738fa8451fb
  [trading_corp/agents/divisions/bitunix_sfp_observer.py]=18da45f236b5b980e6312b6d435c8546
  [trading_corp/main.py]=2c1bb1dc80bddf4b90e3b68299b2ef1a
  [config/strategies.yaml]=0cd6e45d758c8e6d226302d4055bce44
)
declare -A TARGET=(
  [trading_corp/agents/strategies/bitunix_sfp.py]=91fd76726364331c8083aaaa68fce199
  [trading_corp/agents/divisions/bitunix_sfp_observer.py]=8a916526d67fccef406f0dabd63e0b12
  [trading_corp/main.py]=2ff188c73648c2f23d92f1168a5a803f
  [config/strategies.yaml]=84001f67a9bfbd93e3129e0f9e5cb2b8
)
echo "== Gate-A: prod-pre md5 == pinned base (ABORT on drift) =="
for f in "${FILES[@]}"; do c=$(md5of "$f"); [ "$c" = "${BASE[$f]}" ] || { echo "ABORT Gate-A $f: $c != ${BASE[$f]} (prod drifted — STOP)"; exit 1; }; echo "  OK base    $f  $c"; done
echo "== staged files present + LF (no CR) + md5 == target =="
for f in "${FILES[@]}"; do
  [ -f "$SRC/$f" ] || { echo "ABORT: missing staged $SRC/$f"; exit 1; }
  [ "$(tr -cd '\r' < "$SRC/$f" | wc -c)" = "0" ] || { echo "ABORT: staged $f has CR bytes"; exit 1; }
  c=$(md5of "$SRC/$f"); [ "$c" = "${TARGET[$f]}" ] || { echo "ABORT: staged $f md5 $c != target ${TARGET[$f]}"; exit 1; }
  echo "  OK staged  $f  $c"
done
echo "== backup + atomic swap =="
for f in "${FILES[@]}"; do cp -p "$f" "$f.$STAMP"; cp "$SRC/$f" "$f.new"; mv "$f.new" "$f"; echo "  placed $f (backup $f.$STAMP)"; done
echo "== Gate-B: post md5 == target =="
for f in "${FILES[@]}"; do c=$(md5of "$f"); [ "$c" = "${TARGET[$f]}" ] || { echo "ABORT Gate-B $f: $c != ${TARGET[$f]}"; exit 1; }; echo "  OK target  $f  $c"; done
echo "== py_compile (prod venv) =="
venv/bin/python -m py_compile "${PYFILES[@]}" && echo "  py_compile OK"
echo "== DONE — 4 files swapped on disk, engine NOT restarted. Run sfp_modeb_arm_restart next."
