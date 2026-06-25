#!/usr/bin/env bash
# RESTART ② (part 1 of 2) — flip strategies.yaml: bitunix_sfp -> live, bitunix_futures -> paper.
# Gate-A (prod-pre == 930a146f = the ①-deployed state) -> backup -> write ②-target -> Gate-B (281b373f)
# -> YAML semantic check. NO restart. (Part 2 = ExecStart --live-divisions swap, a separate ROOT step;
# the restart applies both.)
set -euo pipefail
TC="$HOME/trading_corp"
SRC="${1:-$HOME/sfp_live}"
cd "$TC"
f=config/strategies.yaml
BASE=930a146f27d503ca22a02eaa200ea05b
TARGET=281b373f033dbcf23fc0176372470e1e
md5of(){ md5sum "$1" | cut -d' ' -f1; }

cur=$(md5of "$f")
[ "$cur" = "$BASE" ] || { echo "ABORT Gate-A $f: $cur != $BASE (NOT the ①-deployed state — STOP)"; exit 1; }
[ -f "$SRC/$f" ] || { echo "ABORT: missing staged $SRC/$f"; exit 1; }
[ "$(tr -cd '\r' < "$SRC/$f" | wc -c)" = "0" ] || { echo "ABORT: staged $f has CR bytes"; exit 1; }
echo "  Gate-A OK: $f == $BASE"

cp -p "$f" "$f.bak-pre-sfp-live-2026-06-25"
cp "$SRC/$f" "$f.new"; mv "$f.new" "$f"

cur=$(md5of "$f")
[ "$cur" = "$TARGET" ] || { echo "ABORT Gate-B $f: $cur != $TARGET"; exit 1; }
echo "  Gate-B OK: $f == $TARGET"

venv/bin/python -c "import yaml; d=yaml.safe_load(open('$f')); assert d['bitunix_sfp']['execution_mode']=='live', d['bitunix_sfp']['execution_mode']; assert d['bitunix_futures']['execution_mode']=='paper', d['bitunix_futures']['execution_mode']; print('  YAML OK: bitunix_sfp=live, bitunix_futures=paper')"

echo "== strategies.yaml FLIPPED (gated). NO restart performed. =="
echo "   NEXT (root): ExecStart --live-divisions swap (execstart_swap.sh via az run-command),"
echo "   then re-verify FLAT, then restart."
