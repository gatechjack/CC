#!/usr/bin/env bash
# Bitunix P1 reconciler-fix — prod-side APPLY (backup -> md5 gate -> atomic mv).
# OPERATOR-RUN, §4-gated. DRAFT prepared 2026-06-14 by the prep session — NOT run
# by the agent. Aborts on ANY md5 mismatch — never deploys blindly. Does NOT
# restart (the restart is a SEPARATE explicit step AFTER you verify the apply).
#
# Operator usage:
#   1) Stage the new files on prod from a checkout of 8b78da8 (CRLF ok — this
#      script tr-d's \r before gating):
#        scp <local>/trading_corp/brokers/bitunix.py \
#            azureuser@trading.jacksumner.com:/tmp/p1/bitunix.py
#        scp <local>/trading_corp/agents/divisions/bitunix_position_reconciler.py \
#            azureuser@trading.jacksumner.com:/tmp/p1/bitunix_position_reconciler.py
#   2) Stream this script (one line to paste):
#        Get-Content runbooks/deploy_apply_p1.sh -Raw | ssh azureuser@trading.jacksumner.com "tr -d '\r'|bash"
#   3) Every file must print "OK applied". THEN, separately: systemctl restart
#      trading-corp ; then run the post-restart verification (plan §2e).
#
# To co-batch the kalshi-sports-arb disable: append its reviewed row to
# FILE_SPECS (staged_basename|prod_relpath|expected_base_md5|target_md5), stage
# its file under /tmp/p1, and re-run — it deploys in the same pass + one restart.
set -euo pipefail

ROOT=/home/azureuser/trading_corp
STAGE=/tmp/p1
PYBIN="$ROOT/venv/bin/python"
BAK_SUFFIX=.bak-pre-p1reconciler-2026-06-14

# staged_basename | prod_relpath | expected_base_md5 (prod NOW) | target_md5 (new, LF)
FILE_SPECS=(
  "bitunix.py|trading_corp/brokers/bitunix.py|8a81b30e74a5a38e60752e0c88de8d9e|64d857246a0879c4378e5b3a4185874e"
  "bitunix_position_reconciler.py|trading_corp/agents/divisions/bitunix_position_reconciler.py|bcefc1c0b95a784c35d8e236f86748ed|64f33e76934e754c76437e6ce7d7d290"
  # "<kalshi staged basename>|<kalshi prod relpath>|<base md5>|<target md5>"   # <- add when reviewed
)

md5of(){ md5sum "$1" | cut -d' ' -f1; }

for spec in "${FILE_SPECS[@]}"; do
  IFS='|' read -r base prod_rel base_md5 target_md5 <<< "$spec"
  staged="$STAGE/$base"; prod="$ROOT/$prod_rel"
  echo "=== $prod_rel ==="
  [ -f "$staged" ] || { echo "  ABORT: staged file missing: $staged"; exit 1; }
  [ -f "$prod" ]   || { echo "  ABORT: prod file missing: $prod"; exit 1; }

  lf="$staged.lf"; tr -d '\r' < "$staged" > "$lf"          # normalize to LF

  cur=$(md5of "$prod")                                      # Gate A: prod = clean base (no drift)
  [ "$cur" = "$base_md5" ] || { echo "  ABORT: prod drift — prod=$cur expected_base=$base_md5"; exit 1; }
  got=$(md5of "$lf")                                        # Gate B: staged = reviewed target
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

echo "ALL FILES APPLIED. Next (separate, explicit): systemctl restart trading-corp ; then verify (plan 2e)."
