#!/usr/bin/env bash
# RESTART ① — bitunix_sfp PAPER dry-run apply (2026-06-25).
# Stages 7 files onto prod with per-file Gate-A (prod-pre == pinned base) /
# Gate-B (post == target) md5 checks, backups, atomic swap, py_compile.
# Does NOT restart and does NOT flip bitunix_futures (still LIVE at this stage).
# strategies.yaml ships bitunix_sfp.execution_mode=paper.
#
# Operator: scp the staged/ tree to ~/sfp_staged on prod, then:
#   bash ~/apply_paper.sh
# Rollback: restore *.bak-pre-sfp-2026-06-25 and (for the 2 new modules) delete them.
set -euo pipefail
TC="$HOME/trading_corp"
SRC="${1:-$HOME/sfp_staged}"
STAMP="bak-pre-sfp-2026-06-25"
cd "$TC"
md5of(){ md5sum "$1" | cut -d' ' -f1; }

# Existing files: Gate-A base (prod-pre) → Gate-B target (post).
EXISTING=(trading_corp/main.py config/strategies.yaml config/divisions.yaml \
          trading_corp/utils/divisions.py trading_corp/brokers/bitunix_symbols.py)
declare -A BASE=(
  [trading_corp/main.py]=ec7bd6962bba02d1ba5b601af131f4e2
  [config/strategies.yaml]=36f5b32309e4342a4521a69a8cb53a42
  [config/divisions.yaml]=090174da86bddc9d2a4fdcc74b631d2c
  [trading_corp/utils/divisions.py]=2ef1e3e8aa5f9a1522cef3799613bbd6
  [trading_corp/brokers/bitunix_symbols.py]=aa7700822344c417fdbc46d80509988f
)
# All staged files (existing + new): Gate-B target.
declare -A TARGET=(
  [trading_corp/main.py]=2b504cbce7410334fed3908d153734cf
  [config/strategies.yaml]=930a146f27d503ca22a02eaa200ea05b
  [config/divisions.yaml]=6dcbe16f2b63399664012b076cc3843c
  [trading_corp/utils/divisions.py]=91b09f5077bb305c7506dee7122fecbf
  [trading_corp/brokers/bitunix_symbols.py]=4d5f87ee49e31b52ec89301569614d8e
  [trading_corp/agents/strategies/bitunix_sfp.py]=ad8e36f53b85df47acff21f3b6a5bf61
  [trading_corp/agents/divisions/bitunix_sfp_observer.py]=b2b856be78aadaab153e2100fbe8ed1b
)
NEWFILES=(trading_corp/agents/strategies/bitunix_sfp.py \
          trading_corp/agents/divisions/bitunix_sfp_observer.py)

echo "== Gate-A: prod-pre md5 must match pinned base =="
for f in "${EXISTING[@]}"; do
  cur=$(md5of "$f")
  [ "$cur" = "${BASE[$f]}" ] || { echo "ABORT Gate-A $f: $cur != ${BASE[$f]} (prod drifted — STOP)"; exit 1; }
  echo "  OK base  $f  $cur"
done
echo "== new modules must NOT already exist =="
for f in "${NEWFILES[@]}"; do [ -e "$f" ] && { echo "ABORT: $f already exists"; exit 1; }; echo "  absent OK  $f"; done
echo "== staged source files present + LF =="
for f in "${!TARGET[@]}"; do
  [ -f "$SRC/$f" ] || { echo "ABORT: missing staged $SRC/$f"; exit 1; }
  [ "$(tr -cd '\r' < "$SRC/$f" | wc -c)" = "0" ] || { echo "ABORT: $f has CR bytes (not LF)"; exit 1; }
done

echo "== backup existing =="
for f in "${EXISTING[@]}"; do cp -p "$f" "$f.$STAMP"; echo "  backed up $f -> $f.$STAMP"; done

echo "== stage in (atomic mv per file) =="
for f in "${!TARGET[@]}"; do cp "$SRC/$f" "$f.new"; mv "$f.new" "$f"; echo "  placed $f"; done

echo "== Gate-B: post md5 must match target =="
for f in "${!TARGET[@]}"; do
  cur=$(md5of "$f")
  [ "$cur" = "${TARGET[$f]}" ] || { echo "ABORT Gate-B $f: $cur != ${TARGET[$f]}"; exit 1; }
  echo "  OK target  $f  $cur"
done

echo "== py_compile (prod venv) =="
venv/bin/python -m py_compile trading_corp/main.py "${NEWFILES[@]}" && echo "  py_compile OK"

echo "== DONE — files staged, NO restart performed. =="
echo "   strategies.yaml: bitunix_sfp.execution_mode=paper; bitunix_futures UNCHANGED (still live)."
echo "   Next: operator restarts (SSH-NOPASSWD systemctl restart trading-corp), then boot smoke."
