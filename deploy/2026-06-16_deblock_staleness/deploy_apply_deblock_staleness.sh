#!/usr/bin/env bash
# Deploy: C (bar-interval staleness-reject gate) + A (RiskAgent polymarket
# audit_event-scan cap REMOVAL) + ix_audit_event_actor_kind index.
# Merged prep SHA 888dd31 (branch deploy-prep-2026-06-16, off current main a66e36d).
#
# PREPARE-ONLY ARTIFACT. This script does prod writes (backup + atomic mv +
# yaml edit). It does NOT restart. The engine reload is the operator's step:
#     ssh -t azureuser@trading.jacksumner.com sudo systemctl restart trading-corp
#
# Delivery (operator, before running this): place the staged tree on prod at
#     $BASE/_deblock_stage/   (mirrors trading_corp/... + staleness_gate.snippet.yaml)
# then stream-run this script. See PLAN.md.
#
# IN-SCOPE NOTE (read PLAN.md §divergence): prod main.py (659bbb80 = commit
# 710e181) and db.py (9f946491 = b5278c5) LAG repo main on the *un-deployed
# polymarket E-series batch* (E1.6..E5b in main.py; E2.5 column in db.py).
# Therefore main.py + db.py are NOT full-file overwrites of the merged blob
# (that would ship the polymarket batch). They are the PROD blob + ONLY the
# in-scope C / A hunk -> the TARGET md5s below. observer.py + risk.py prod ==
# repo base, so those two ARE full-file (merged blob = base + C / + A only).
set -euo pipefail

BASE=/home/azureuser/trading_corp
STAGE="$BASE/_deblock_stage"
BAK=".bak-pre-deblock-2026-06-16"
cd "$BASE"

FILES="trading_corp/agents/divisions/bitunix_futures_observer.py trading_corp/agents/risk.py trading_corp/main.py trading_corp/persistence/db.py"

# expected CURRENT prod md5 (drift guard -- abort if prod is not where we think)
cur_md5() { case "$1" in
  trading_corp/agents/divisions/bitunix_futures_observer.py) echo 3067a3e9d979624dca040657632dd1ba;;
  trading_corp/agents/risk.py)                               echo 4b87e1497da62051f109a8dcd28558f3;;
  trading_corp/main.py)                                      echo 659bbb801317fecec20865f47cbe81a9;;
  trading_corp/persistence/db.py)                            echo 9f94649158c299671717a1efa016fa43;;
esac; }
# TARGET md5 after deploy
tgt_md5() { case "$1" in
  trading_corp/agents/divisions/bitunix_futures_observer.py) echo eec6bda62e23038edd09f29ff65addcb;;
  trading_corp/agents/risk.py)                               echo 49a1c7968dc3e7e6e00352a7ca706f9f;;
  trading_corp/main.py)                                      echo f733e37407617d5f9d3330ad15a0ebc6;;
  trading_corp/persistence/db.py)                            echo d56e06393403147c3f8dfc49914c814e;;
esac; }

m5() { md5sum "$1" | cut -d' ' -f1; }

echo "===== deblock+staleness deploy (NO RESTART) ====="

echo "-- 0. staged tree present + matches TARGET (fail early, touch nothing) --"
[ -d "$STAGE" ] || { echo "ABORT: $STAGE missing (deliver staged tree first)"; exit 1; }
[ -f "$STAGE/staleness_gate.snippet.yaml" ] || { echo "ABORT: snippet missing in $STAGE"; exit 1; }
for f in $FILES; do
  [ -f "$STAGE/$f" ] || { echo "ABORT: staged $f missing"; exit 1; }
  sm=$(m5 "$STAGE/$f"); want=$(tgt_md5 "$f")
  [ "$sm" = "$want" ] || { echo "ABORT: staged $f md5 $sm != target $want"; exit 1; }
done
echo "   OK (4 staged files match TARGET)"

echo "-- 1. PRE-FLIGHT: prod current md5 drift guard --"
for f in $FILES; do
  pm=$(m5 "$f"); exp=$(cur_md5 "$f")
  [ "$pm" = "$exp" ] || { echo "ABORT drift: $f is $pm, expected current $exp (already applied? prod moved?)"; exit 1; }
done
grep -q '^  staleness_gate:' config/strategies.yaml && { echo "ABORT: staleness_gate already in strategies.yaml"; exit 1; }
echo "   OK (4 .py at expected current; yaml has no staleness_gate yet)"

echo "-- 2. INSTALL 4 .py (backup -> *$BAK, md5-gated atomic mv) --"
for f in $FILES; do
  want=$(tgt_md5 "$f")
  cp -p "$f" "$f$BAK"
  cp -p "$STAGE/$f" "$f.deblock-new"
  mv "$f.deblock-new" "$f"
  pm=$(m5 "$f")
  [ "$pm" = "$want" ] || { echo "ABORT post-mv: $f is $pm != $want"; exit 1; }
  echo "   installed $f ($pm)  [backup: $f$BAK]"
done

echo "-- 3. strategies.yaml targeted staleness_gate insert (backup + assert) --"
cp -p config/strategies.yaml "config/strategies.yaml$BAK"
"$BASE/venv/bin/python" - "$STAGE/staleness_gate.snippet.yaml" <<'PY'
import sys
p = "config/strategies.yaml"
snippet = open(sys.argv[1], encoding="utf-8").read()
lines = open(p, encoding="utf-8").read().splitlines(keepends=True)
anchor = "  snapshot_staleness_threshold_seconds: 60"
idx = [i for i, l in enumerate(lines) if l.startswith(anchor)]
assert len(idx) == 1, f"anchor count {len(idx)} (want 1)"
assert sum("staleness_gate:" in l for l in lines) == 0, "staleness_gate already present"
assert any(l.startswith("  execution_mode: live") for l in lines), "execution_mode: live not found (refusing)"
n0 = len(lines)
block = snippet if snippet.endswith("\n") else snippet + "\n"
lines[idx[0] + 1: idx[0] + 1] = [block]
open(p, "w", encoding="utf-8", newline="").write("".join(lines))
t = open(p, encoding="utf-8").read().splitlines()
assert sum("staleness_gate:" in l for l in t) == 1, "post: staleness_gate count != 1"
assert any(l.startswith("  enabled: true") or l.strip() == "enabled: true" for l in t), "post: enabled key missing"
assert any(l.startswith("  execution_mode: live") for l in t), "post: execution_mode: live lost!"
assert len(t) == n0 + 10, f"post: line delta {len(t)-n0} (want 10)"
print(f"   yaml OK: +{len(t)-n0} lines, staleness_gate:1, execution_mode: live preserved")
PY

echo "-- 4. py_compile all 4 with prod venv --"
"$BASE/venv/bin/python" -m py_compile $FILES && echo "   compile OK"

echo "-- 5. final md5 summary --"
for f in $FILES; do echo "   $(m5 "$f")  $f"; done
echo "   $(m5 config/strategies.yaml)  config/strategies.yaml (was 8e8e31173a26e53f610ab66c764848fe)"

cat <<'EOF'
===== APPLIED (NO RESTART). =====
NEXT (operator): ssh -t azureuser@trading.jacksumner.com sudo systemctl restart trading-corp
THEN run VERIFY.md layers (a) standard (b) deps-activation [only if deps install also done this window]
     (c) index migration (d) freeze-fix.
ROLLBACK (no restart needed if not yet restarted): for f in <4 .py> config/strategies.yaml; do mv "$f.bak-pre-deblock-2026-06-16" "$f"; done
EOF
