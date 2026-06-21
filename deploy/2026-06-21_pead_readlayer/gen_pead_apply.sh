#!/usr/bin/env bash
# Generator: emits pead_apply.sh — a SELF-CONTAINED, CRLF-immune (base64 of LF
# git blobs), md5-verified, drift-gated, NO-RESTART installer for the PEAD
# read-layer (minimal 7 files). Run from the repo root on the merge commit.
#
#   bash deploy/2026-06-21_pead_readlayer/gen_pead_apply.sh
#
# Then scp deploy/2026-06-21_pead_readlayer/pead_apply.sh to prod and run it.
set -euo pipefail
REF=9fe4f53
DIR=deploy/2026-06-21_pead_readlayer
OUT=$DIR/pead_apply.sh
mkdir -p "$DIR"

cat > "$OUT" <<'HEADER'
#!/usr/bin/env bash
# PEAD read-layer apply — 2026-06-21. Generated; do not hand-edit.
# SELF-CONTAINED: base64-embedded LF file contents (CRLF-immune).
# NO restart. NO config-replace. NO DB writes. Backs up every target to
# <file>.bak-pre-pead-2026-06-21 and md5-verifies each install == target.
# Shared files are DRIFT-GATED: aborts if prod != expected base md5 (which
# would mean a non-PEAD change is present and a full-file write would revert
# it). Activate the new code with the separately-run engine restart.
set -euo pipefail
ROOT="${ROOT:-/home/azureuser/trading_corp}"
BK=".bak-pre-pead-2026-06-21"
fail(){ echo "ABORT: $*" >&2; exit 1; }

install_shared(){ # rel base_md5 target_md5 b64
  local rel="$1" base="$2" tgt="$3" b64="$4" f="$ROOT/$1"
  [ -f "$f" ] || fail "shared file missing on prod: $rel"
  local cur; cur=$(md5sum "$f" | awk '{print $1}')
  [ "$cur" = "$base" ] || fail "DRIFT $rel: prod=$cur != expected-base=$base (a non-PEAD change is present; refusing to overwrite)"
  [ -f "$f$BK" ] || cp "$f" "$f$BK"
  printf '%s' "$b64" | base64 -d > "$f.peadnew"
  local got; got=$(md5sum "$f.peadnew" | awk '{print $1}')
  [ "$got" = "$tgt" ] || { rm -f "$f.peadnew"; fail "target md5 $rel got=$got want=$tgt"; }
  case "$rel" in *.py) python3 -m py_compile "$f.peadnew" || { rm -f "$f.peadnew"; fail "py_compile $rel"; };; esac
  mv "$f.peadnew" "$f"
  echo "OK shared  $rel  $base -> $tgt"
}

install_new(){ # rel target_md5 b64
  local rel="$1" tgt="$2" b64="$3" f="$ROOT/$1"
  mkdir -p "$(dirname "$f")"
  [ -f "$f" ] && { [ -f "$f$BK" ] || cp "$f" "$f$BK"; }
  printf '%s' "$b64" | base64 -d > "$f.peadnew"
  local got; got=$(md5sum "$f.peadnew" | awk '{print $1}')
  [ "$got" = "$tgt" ] || { rm -f "$f.peadnew"; fail "target md5 $rel got=$got want=$tgt"; }
  case "$rel" in *.py) python3 -m py_compile "$f.peadnew" || { rm -f "$f.peadnew"; fail "py_compile $rel"; };; esac
  mv "$f.peadnew" "$f"
  echo "OK new     $rel  $tgt"
}

echo "== PEAD read-layer apply starting (7 files; NO restart) =="
# ---- generated per-file installs ----
HEADER

emit_shared(){ # rel base_md5
  local p="$1" base="$2" tgt b64
  tgt=$(git show "$REF:$p" | md5sum | awk '{print $1}')
  b64=$(git show "$REF:$p" | base64 -w0)
  printf "install_shared %s %s %s '%s'\n" "$p" "$base" "$tgt" "$b64" >> "$OUT"
}
emit_new(){ # rel
  local p="$1" tgt b64
  tgt=$(git show "$REF:$p" | md5sum | awk '{print $1}')
  b64=$(git show "$REF:$p" | base64 -w0)
  printf "install_new %s %s '%s'\n" "$p" "$tgt" "$b64" >> "$OUT"
}

# SHARED (prod==base, drift-gated additive superset)
emit_shared trading_corp/web/routes.py        e8113e6fef935d781ea80091e8ab744c
emit_shared trading_corp/persistence/db.py     a2c2ff46b89ec3d30640552db19b962c
# NEW (pure adds)
emit_new trading_corp/web/pead_view.py
emit_new trading_corp/agents/strategies/pead_pressures.py
emit_new trading_corp/persistence/pead_observability.py
emit_new trading_corp/web/templates/pead_live.html
emit_new trading_corp/web/templates/partials/pead_live_sections.html

cat >> "$OUT" <<'FOOTER'
echo "== PEAD read-layer APPLIED (7 files on disk, backups *.bak-pre-pead-2026-06-21). NO restart performed. =="
echo "== Rollback: restore each *.bak-pre-pead-2026-06-21 and remove the 5 new files. =="
FOOTER

chmod +x "$OUT" 2>/dev/null || true
echo "generated $OUT  ($(wc -l < "$OUT") lines)"
