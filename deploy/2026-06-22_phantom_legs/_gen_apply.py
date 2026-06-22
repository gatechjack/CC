"""Generate apply_phantomlegs.sh — single-file, drift-gated, base64-embedded
apply for the phantom-legs skip. Target = prod blob (5619910d, Issue#1) + the
14-line skip hunk = 28817062 (verified by _pl_splice.py). Run from worktree root.
NO restart; NO prod contact."""
import base64
import hashlib
from pathlib import Path

REL = "trading_corp/agents/paper_trade_replay.py"
BASE = "5619910dab44b053124fbbc2e7671cec"
OUT = Path(__file__).with_name("apply_phantomlegs.sh")

blob = Path("deploy/_target_ptr.py").read_bytes().replace(b"\r\n", b"\n")
new_md5 = hashlib.md5(blob).hexdigest()
b64 = base64.b64encode(blob).decode("ascii")
wrapped = "\n".join(b64[i:i + 76] for i in range(0, len(b64), 76))

script = f"""#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
#  phantom-legs fix — skip the paper bar-walk for bracket-managed LIVE rows.
#  Branch bitunix-d1-netted-close-2026-06-21 @ HEAD (commit 24519c8).
#
#  ONE file: paper_trade_replay.py = TARGETED-HUNK (prod 5619910d + the 14-line
#  skip; Issue#1 PRESERVED). Drift-gated · backup · atomic mv · re-verify ·
#  self-rollback · py_compile. *** NO RESTART *** (operator restarts to load it).
#  Rollback: cp <f>.bak-pre-phantomlegs-2026-06-22 <f>  (then restart)
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail

ROOT="${{TC_ROOT:-/home/azureuser/trading_corp}}"
F="$ROOT/{REL}"
BASE="{BASE}"; NEW="{new_md5}"
BAK="$F.bak-pre-phantomlegs-2026-06-22"
echo "[pl] target = $F"

[ -f "$F" ] || {{ echo "[pl] ABORT: missing $F"; exit 2; }}
CUR=$(md5sum "$F" | awk '{{print $1}}')
if [ "$CUR" != "$BASE" ]; then
  echo "[pl] ABORT: DRIFTED. expected $BASE got $CUR — re-stage vs current prod."
  exit 3
fi
echo "[pl] drift gate OK (prod == $BASE)"

[ -e "$BAK" ] && {{ echo "[pl] ABORT: backup exists ($BAK)"; exit 4; }}
cp -p "$F" "$BAK"; echo "[pl] backup: $BAK"

TMP="$F.pl.tmp.$$"
base64 -d > "$TMP" <<'B64_EOF'
{wrapped}
B64_EOF
chmod --reference="$BAK" "$TMP" 2>/dev/null || true
chown --reference="$BAK" "$TMP" 2>/dev/null || true
mv -f "$TMP" "$F"
echo "[pl] written (atomic mv)"

GOT=$(md5sum "$F" | awk '{{print $1}}')
if [ "$GOT" != "$NEW" ]; then
  echo "[pl] FAIL: post-write md5 $GOT != $NEW — ROLLING BACK"
  cp -p "$BAK" "$F"; echo "[pl] rolled back"; exit 5
fi
echo "[pl] re-verify OK (== $NEW)"

if python3 -m py_compile "$F"; then echo "[pl] py_compile OK"; else echo "[pl] WARN: py_compile failed — review before restart"; fi

echo "[pl] DONE — applied. NO restart performed."
echo "[pl] NEXT (operator): restart to LOAD, then VERIFY.sh."
echo "[pl] ROLLBACK: cp \\"$BAK\\" \\"$F\\"  (then restart)"
"""

OUT.write_text(script, encoding="utf-8", newline="\n")
print("wrote", OUT)
print("BASE", BASE, "-> TARGET", new_md5)
