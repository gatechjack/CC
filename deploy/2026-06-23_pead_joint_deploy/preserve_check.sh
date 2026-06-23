#!/usr/bin/env bash
# EXTENDED GUARD (joint-deploy step 3 gate). Proves PEAD's edits to the Bitunix-/prod-
# sensitive shared files preserve prod content.
#
# strategies.yaml carries ONE authorized non-additive change: Bitunix's cross-restart
# halt — bitunix_futures.auto_execute true -> false (rides in PEAD's single write so
# the drift baseline stays 544458b2). It is checked precisely:
#   bitunix_futures(installed) == bitunix_futures(backup) with ONLY auto_execute flipped
#   => fee-coupled + every other bitunix key byte-identical.
# All OTHER changes (this file and the rest) must be PURELY ADDITIVE: every non-blank
# backup line still present in installed. ABORT(9) on any unexpected drop.
#
#   ./preserve_check.sh [PROD_ROOT]
# No `set -e`/`pipefail`: this is a multi-check guard — it runs EVERY check and exits 9
# if any fails (rc managed explicitly), rather than bailing on the first.
set -u
ROOT="${1:-${PROD_ROOT:-/home/azureuser/trading_corp}}"
SUF=".bak-pre-pead-2026-06-23"
rc=0

bxblock(){ awk '/^bitunix_futures:/{f=1} f && /^[A-Za-z]/ && !/^bitunix_futures:/{exit} f' "$1"; }
# subset: every non-blank BACKUP line must still be present in INSTALLED, compared
# WHITESPACE-INSENSITIVELY (strip spaces/tabs from the key). Whitespace-insensitive so a
# line that PEAD legitimately EXTENDED in place (e.g. paper_trade_replay's WHERE clause
# gained a space + AND-division continuation) is not flagged, while any real content drop
# (non-space chars gone) still is. md5 integrity (apply.sh) is the byte-exact guard.
subset(){ # $1=backup $2=installed $3=extra-grep-exclude-pattern(optional) -> echo dropped (stripped) keys
  local bk
  if [ -n "${3:-}" ]; then bk=$(grep -vE '^[[:space:]]*$' "$1" | grep -vE "$3"); else bk=$(grep -vE '^[[:space:]]*$' "$1"); fi
  comm -23 <(printf '%s\n' "$bk" | tr -d ' \t' | sort -u) <(grep -vE '^[[:space:]]*$' "$2" | tr -d ' \t' | sort -u)
}

echo "== extended guard =="

# --- strategies.yaml: authorized halt flip + additive ---
sf="config/strategies.yaml"; bak="$ROOT/$sf$SUF"; cur="$ROOT/$sf"
if [ ! -f "$bak" ] || [ ! -f "$cur" ]; then echo "  MISSING $sf (backup or current)"; rc=9; else
  if diff <(bxblock "$bak" | sed '/^  auto_execute: true /s/: true /: false/') <(bxblock "$cur") >/dev/null; then
    echo "  ok    $sf bitunix_futures (== prod + halt flip; fee-coupled byte-preserved)"
  else
    echo "  DRIFT $sf : bitunix_futures changed beyond the authorized auto_execute halt flip:"
    diff <(bxblock "$bak" | sed '/^  auto_execute: true /s/: true /: false/') <(bxblock "$cur") | head -20 | sed 's/^/        /'
    rc=9
  fi
  # additive everywhere else: exclude exactly the pre-flip bitunix auto_execute:true line
  miss=$(subset "$bak" "$cur" '^  auto_execute: true ')
  n=$(printf '%s' "$miss" | grep -c . || true)
  if [ "$n" -gt 0 ]; then echo "  DROP  $sf : $n line(s) dropped (beyond halt flip):"; printf '%s\n' "$miss" | head -20 | sed 's/^/        /'; rc=9
  else echo "  ok    $sf (all other prod lines preserved)"; fi
fi

# --- purely-additive files ---
for f in config/risk.yaml config/divisions.yaml trading_corp/agents/paper_trade_replay.py trading_corp/main.py trading_corp/persistence/models.py; do
  bak="$ROOT/$f$SUF"; cur="$ROOT/$f"
  [ -f "$bak" ] && [ -f "$cur" ] || { echo "  MISSING $f (backup or current)"; rc=9; continue; }
  miss=$(subset "$bak" "$cur")
  n=$(printf '%s' "$miss" | grep -c . || true)
  if [ "$n" -gt 0 ]; then echo "  DROP  $f : $n pre-deploy line(s) missing:"; printf '%s\n' "$miss" | head -20 | sed 's/^/        /'; rc=9
  else echo "  ok    $f (100% of pre-deploy content preserved)"; fi
done

[ $rc = 9 ] && { echo "ABORT(9): prod content dropped/changed beyond authorized halt flip — run rollback.sh."; exit 9; }
echo "PRESERVE_CHECK OK."
