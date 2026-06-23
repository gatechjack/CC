#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
#  D3 role-recording fix — record maker/taker from ORDER SEMANTICS, not the
#  unreliable venue roleType (which reports MAKER for economically-taker fills).
#  Two trading-path files. Role-attribution ONLY (no PnL/D1/ref-vs-fill/B1/risk).
#
#  All-or-nothing, targeted-hunk, drift-gated. Pre-flight compiles the STAGED
#  files, drift-gates BOTH prod md5s, backs up BOTH, atomic-installs BOTH,
#  re-verifies + py_compiles BOTH; ANY failure rolls back BOTH. *** NO RESTART ***
#  (restart loads it — operator runs it in a flat window). Rollback: restore the
#  two *.bak-pre-d3-2026-06-23 + restart.
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail
ROOT="${TC_ROOT:-/home/azureuser/trading_corp}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SFX="bak-pre-d3-2026-06-23"

B_REL="trading_corp/brokers/bitunix.py"
B_OLD="3f68473a4ddfe27ca035308414c1c280"; B_NEW="4b00dea2a913f20af68ca2754b5cc6b0"
R_REL="trading_corp/agents/divisions/bitunix_position_reconciler.py"
R_OLD="a3e9d50da2527664a2016e7205cac9f8"; R_NEW="8c3adcd173c3a9f65e596e64db7ef6e8"

md5of(){ md5sum "$1" | awk '{print $1}'; }
rollback_all(){
  echo "[d3] !! ROLLING BACK BOTH FILES"
  for r in "$B_REL" "$R_REL"; do
    [ -e "$ROOT/$r.$SFX" ] && cp -p "$ROOT/$r.$SFX" "$ROOT/$r" && echo "[d3]   restored $r"
  done
}

echo "[d3] === pre-flight: STAGED files compile? ==="
for r in "$B_REL" "$R_REL"; do
  s="$HERE/stage/$r"
  [ -f "$s" ] || { echo "[d3] ABORT: missing staged $s"; exit 2; }
  python3 -c "import py_compile; py_compile.compile('$s', doraise=True)" \
    || { echo "[d3] ABORT: staged $r does not compile"; exit 6; }
  echo "[d3]   staged $r compiles OK"
done

echo "[d3] === drift-gate (BOTH prod files unchanged) ==="
for pair in "$B_REL:$B_OLD" "$R_REL:$R_OLD"; do
  r="${pair%%:*}"; want="${pair##*:}"; f="$ROOT/$r"
  [ -f "$f" ] || { echo "[d3] ABORT: missing prod $f"; exit 2; }
  cur=$(md5of "$f")
  [ "$cur" = "$want" ] || { echo "[d3] ABORT: $r DRIFTED (cur $cur != $want)"; exit 3; }
  echo "[d3]   $r == $want OK"
done

echo "[d3] === staged md5 == targets ==="
for pair in "$B_REL:$B_NEW" "$R_REL:$R_NEW"; do
  r="${pair%%:*}"; want="${pair##*:}"; sm=$(md5of "$HERE/stage/$r")
  [ "$sm" = "$want" ] || { echo "[d3] ABORT: staged $r md5 $sm != $want"; exit 3; }
done

echo "[d3] === backup BOTH ==="
for r in "$B_REL" "$R_REL"; do
  [ -e "$ROOT/$r.$SFX" ] && { echo "[d3] ABORT: backup exists $ROOT/$r.$SFX"; exit 4; }
done
for r in "$B_REL" "$R_REL"; do cp -p "$ROOT/$r" "$ROOT/$r.$SFX"; echo "[d3]   backup $r.$SFX"; done

echo "[d3] === atomic install BOTH ==="
for pair in "$B_REL:$B_NEW" "$R_REL:$R_NEW"; do
  r="${pair%%:*}"; want="${pair##*:}"; f="$ROOT/$r"
  cp "$HERE/stage/$r" "$f.d3tmp" && mv -f "$f.d3tmp" "$f"
  nm=$(md5of "$f")
  if [ "$nm" != "$want" ]; then echo "[d3] FAIL: $r post-install md5 $nm != $want"; rollback_all; exit 5; fi
  if ! python3 -c "import py_compile; py_compile.compile('$f', doraise=True)"; then
    echo "[d3] FAIL: py_compile $r post-install"; rollback_all; exit 6; fi
  echo "[d3]   installed + verified + compiled: $r == $want"
done

echo "[d3] ────────────────────────────────────────────────────────────────"
echo "[d3] DONE — D3 role fix installed (both files). *** NO RESTART performed ***"
echo "[d3] NEXT (operator, FLAT window): restart to LOAD, then VERIFY.sh."
echo "[d3] ROLLBACK: cp $ROOT/$B_REL.$SFX $ROOT/$B_REL ; cp $ROOT/$R_REL.$SFX $ROOT/$R_REL ; restart"
