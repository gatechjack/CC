#!/usr/bin/env bash
# Generator: emits home_tile_apply.sh — drift-gated, base64/LF (CRLF-immune),
# md5-verified, NO-RESTART installer for the single home.html change that adds
# the Operations/PEAD tile. Jinja auto_reload=True on prod picks it up on the
# next homepage render, so no service restart is needed.
#   bash deploy/2026-06-21_home_pead_tile/gen_home_tile_apply.sh
set -euo pipefail
REF=HEAD                       # the commit carrying the PEAD-tile home.html
DIR=deploy/2026-06-21_home_pead_tile
OUT=$DIR/home_tile_apply.sh
REL=trading_corp/web/templates/home.html
BASE=cec108f517752dde994d7550b4084afe   # prod==base==pre-tile home.html

mkdir -p "$DIR"
cat > "$OUT" <<'HEADER'
#!/usr/bin/env bash
# PEAD homepage tile — installs home.html carrying the Operations/PEAD tile.
# Generated; do not hand-edit. Drift-gated (prod==pre-tile base), base64/LF,
# md5-verified, backs up to home.html.bak-pre-pead-tile-2026-06-21.
# NO restart: Jinja auto_reload=True re-reads the template on the next request.
set -euo pipefail
ROOT="${ROOT:-/home/azureuser/trading_corp}"
BK=".bak-pre-pead-tile-2026-06-21"
fail(){ echo "ABORT: $*" >&2; exit 1; }
install_shared(){ # rel base_md5 target_md5 b64
  local rel="$1" base="$2" tgt="$3" b64="$4" f="$ROOT/$1"
  [ -f "$f" ] || fail "missing $rel on prod"
  local cur; cur=$(md5sum "$f"|awk '{print $1}')
  [ "$cur" = "$base" ] || fail "DRIFT $rel: prod=$cur != expected-base=$base (refusing)"
  [ -f "$f$BK" ] || cp "$f" "$f$BK"
  printf '%s' "$b64" | base64 -d > "$f.tilenew"
  local got; got=$(md5sum "$f.tilenew"|awk '{print $1}')
  [ "$got" = "$tgt" ] || { rm -f "$f.tilenew"; fail "target md5 $rel got=$got want=$tgt"; }
  mv "$f.tilenew" "$f"
  echo "OK $rel  $base -> $tgt"
}
echo "== PEAD homepage tile apply (1 file; NO restart) =="
# ---- generated install ----
HEADER

TGT=$(git show "$REF:$REL" | md5sum | awk '{print $1}')
B64=$(git show "$REF:$REL" | base64 -w0)
printf "install_shared %s %s %s '%s'\n" "$REL" "$BASE" "$TGT" "$B64" >> "$OUT"

cat >> "$OUT" <<'FOOTER'
echo "== home.html updated. Jinja auto_reload picks it up on the next homepage load. NO restart. =="
echo "== Rollback: restore trading_corp/web/templates/home.html.bak-pre-pead-tile-2026-06-21 =="
FOOTER

echo "generated $OUT  (target md5 $TGT)"
