#!/usr/bin/env bash
# Bitunix P2 auto-book + latch-release — prod-side APPLY (backup -> md5 gate ->
# atomic mv). OPERATOR-RUN, §4-gated. DRAFT prepared 2026-06-14 by the prep
# session — NOT run by the agent. Aborts on ANY md5 mismatch — never deploys
# blindly. Does NOT restart (the restart is a SEPARATE explicit step AFTER you
# verify the apply). SINGLE FILE: only bitunix_position_reconciler.py changed
# (bitunix.py is UNCHANGED vs the deployed P1 — confirmed by diff).
#
# Operator usage:
#   1) Stage the new file on prod from the reviewed P2 commit (LF, md5-exact):
#        git show dbd9dcf:trading_corp/agents/divisions/bitunix_position_reconciler.py \
#          > /tmp/bitunix_position_reconciler.py
#        scp /tmp/bitunix_position_reconciler.py \
#          azureuser@trading.jacksumner.com:/tmp/p2/bitunix_position_reconciler.py
#   2) Stream this script (one line to paste):
#        Get-Content runbooks/deploy_apply_p2.sh -Raw | ssh azureuser@trading.jacksumner.com "tr -d '\r'|bash"
#   3) Must print "OK applied". THEN, separately: systemctl restart trading-corp ;
#      then run the post-restart verification (plan section 4).
#
# NEVER add config/strategies.yaml here — it holds prod-only execution_mode: live
# (line 1022); a whole-file replace would revert Bitunix to PAPER. FILE_SPECS is
# live-engine .py CODE only.
set -euo pipefail

ROOT=/home/azureuser/trading_corp
STAGE=/tmp/p2
PYBIN="$ROOT/venv/bin/python"
BAK_SUFFIX=.bak-pre-p2autobook-2026-06-14

# staged_basename | prod_relpath | expected_base_md5 (prod NOW = P1) | target_md5 (P2, LF)
FILE_SPECS=(
  "bitunix_position_reconciler.py|trading_corp/agents/divisions/bitunix_position_reconciler.py|64f33e76934e754c76437e6ce7d7d290|ae2fbc74895d5b4341f0d2d0804579c1"
)

md5of(){ md5sum "$1" | cut -d' ' -f1; }

for spec in "${FILE_SPECS[@]}"; do
  IFS='|' read -r base prod_rel base_md5 target_md5 <<< "$spec"
  staged="$STAGE/$base"; prod="$ROOT/$prod_rel"
  echo "=== $prod_rel ==="
  [ -f "$staged" ] || { echo "  ABORT: staged file missing: $staged"; exit 1; }
  [ -f "$prod" ]   || { echo "  ABORT: prod file missing: $prod"; exit 1; }

  lf="$staged.lf"; tr -d '\r' < "$staged" > "$lf"          # normalize to LF

  cur=$(md5of "$prod")                                      # Gate A: prod = P1 base (no drift)
  [ "$cur" = "$base_md5" ] || { echo "  ABORT: prod drift — prod=$cur expected_base=$base_md5"; exit 1; }
  got=$(md5of "$lf")                                        # Gate B: staged = reviewed P2 target
  [ "$got" = "$target_md5" ] || { echo "  ABORT: staged md5 $got != target $target_md5"; exit 1; }

  "$PYBIN" -c "import py_compile,sys; py_compile.compile(sys.argv[1],doraise=True)" "$lf" \
    || { echo "  ABORT: py_compile failed"; exit 1; }

  cp -n "$prod" "$prod$BAK_SUFFIX"                          # backup (no-clobber)
  cp "$lf" "$prod.new.$$" && mv -f "$prod.new.$$" "$prod"   # atomic replace (same fs)
  chown azureuser:azureuser "$prod" 2>/dev/null || true

  fin=$(md5of "$prod")                                      # re-verify
  [ "$fin" = "$target_md5" ] || { echo "  ABORT: post-mv md5 $fin != target $target_md5"; exit 1; }
  echo "  OK applied (backup: $prod$BAK_SUFFIX)"
done

echo "ALL FILES APPLIED. Next (separate, explicit): systemctl restart trading-corp ; then verify (plan 4)."
