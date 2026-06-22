"""Generate apply_d1.sh — a self-contained, drift-gated, base64-embedded apply
script for the D1 reconciler fix. Run from the worktree root. Emits the .sh next
to this file. NO prod contact; pure local codegen."""
import base64
import hashlib
import subprocess
from pathlib import Path

REL = "trading_corp/agents/divisions/bitunix_position_reconciler.py"
BASE_MD5 = "bd06ea281a853687fad8d0a6831e9c0a"   # expected prod-current (drift gate)
OUT = Path(__file__).with_name("apply_d1.sh")

# the committed (HEAD) reconciler, LF-normalized to match prod line endings
blob = subprocess.check_output(["git", "show", "HEAD:" + REL]).replace(b"\r\n", b"\n")
new_md5 = hashlib.md5(blob).hexdigest()
b64 = base64.b64encode(blob).decode("ascii")
# wrap base64 at 76 cols for a tidy heredoc
b64_wrapped = "\n".join(b64[i:i + 76] for i in range(0, len(b64), 76))

script = f"""#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
#  D1 — netted-close PnL double-booking fix  (apply to prod reconciler)
#  Branch bitunix-d1-netted-close-2026-06-21 @ HEAD
#
#  Drift-gated · backup · atomic mv · re-verify · self-rollback on mismatch.
#  *** NO RESTART *** — the operator restarts the engine separately to LOAD D1.
#
#  Touches ONE file: bitunix_position_reconciler.py. Nothing else.
#  Rollback after run:  cp <target>.bak-pre-d1-2026-06-21 <target>  (+ restart)
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail

ROOT="${{TC_ROOT:-/home/azureuser/trading_corp}}"
TARGET="$ROOT/{REL}"
BASE_MD5="{BASE_MD5}"     # expected prod-current  (drift gate — abort if differs)
NEW_MD5="{new_md5}"      # post-D1 target
BAK="$TARGET.bak-pre-d1-2026-06-21"

echo "[d1] target = $TARGET"

# ── 1. drift gate ───────────────────────────────────────────────────────────
[ -f "$TARGET" ] || {{ echo "[d1] ABORT: target missing (check TC_ROOT)"; exit 2; }}
CUR=$(md5sum "$TARGET" | awk '{{print $1}}')
if [ "$CUR" != "$BASE_MD5" ]; then
  echo "[d1] ABORT: prod reconciler DRIFTED."
  echo "[d1]   expected base $BASE_MD5"
  echo "[d1]   got           $CUR"
  echo "[d1]   refusing to apply over an unexpected base — re-stage vs current prod."
  exit 3
fi
echo "[d1] drift gate OK  (prod == expected base $BASE_MD5)"

# ── 2. backup ────────────────────────────────────────────────────────────────
if [ -e "$BAK" ]; then echo "[d1] ABORT: backup already exists ($BAK) — prior apply?"; exit 4; fi
cp -p "$TARGET" "$BAK"
echo "[d1] backup written: $BAK"

# ── 3. write new content (embedded base64) → temp → atomic mv ─────────────────
TMP="$TARGET.d1.tmp.$$"
base64 -d > "$TMP" <<'B64_EOF'
{b64_wrapped}
B64_EOF
chmod --reference="$BAK" "$TMP" 2>/dev/null || true
chown --reference="$BAK" "$TMP" 2>/dev/null || true
mv -f "$TMP" "$TARGET"
echo "[d1] new reconciler written (atomic mv)"

# ── 4. re-verify (self-rollback on mismatch) ─────────────────────────────────
GOT=$(md5sum "$TARGET" | awk '{{print $1}}')
if [ "$GOT" != "$NEW_MD5" ]; then
  echo "[d1] FAIL: post-write md5 mismatch (got $GOT want $NEW_MD5) — ROLLING BACK"
  cp -p "$BAK" "$TARGET"
  echo "[d1] rolled back from $BAK"
  exit 5
fi
echo "[d1] re-verify OK  (prod reconciler == target $NEW_MD5)"

# ── 5. syntax sanity (no import side-effects) ────────────────────────────────
if python3 -m py_compile "$TARGET"; then
  echo "[d1] py_compile OK"
else
  echo "[d1] WARN: py_compile failed — review before restarting the engine"
fi

echo "[d1] ─────────────────────────────────────────────────────────────────"
echo "[d1] DONE — file applied. NO restart performed."
echo "[d1] NEXT (operator): restart the engine to LOAD D1, then run VERIFY.sh."
echo "[d1] ROLLBACK: cp \\"$BAK\\" \\"$TARGET\\"  (then restart)"
"""

OUT.write_text(script, encoding="utf-8", newline="\n")
print("wrote", OUT)
print("NEW_MD5 =", new_md5)
print("bytes embedded =", len(blob), " base64 chars =", len(b64))
