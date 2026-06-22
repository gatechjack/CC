"""Generate apply_refvfill.sh — two-file, drift-gated, base64-embedded apply for
the ref-vs-fill fix. Run from the worktree root.

  observer:   base e88a7abc (prod, carries D4) -> target = prod + the 8-line
              capture hunk (targeted-hunk; preserves D4). Source =
              deploy/_target_observer.py (verified == prod + only my hunk).
  reconciler: base 5c4c8dba (prod = D1) -> target = HEAD (D1 + ref-vs-fill).
              Full-file (base matches prod).

Gates BOTH up front (abort before touching anything if either drifts); backs up
BOTH; atomic-mv BOTH; re-verifies BOTH; rolls back BOTH on any mismatch.
NO restart. NO prod contact (pure local codegen)."""
import base64
import hashlib
import subprocess
from pathlib import Path

OBS_REL = "trading_corp/agents/divisions/bitunix_futures_observer.py"
REC_REL = "trading_corp/agents/divisions/bitunix_position_reconciler.py"
OBS_BASE = "e88a7abca643f2048facfcb19a6c559b"
REC_BASE = "5c4c8dba04a267c660c5fe826dabb16c"
OUT = Path(__file__).with_name("apply_refvfill.sh")

# observer target = the spliced prod+hunk blob (LF)
obs_bytes = Path("deploy/_target_observer.py").read_bytes().replace(b"\r\n", b"\n")
# reconciler target = committed HEAD (LF)
rec_bytes = subprocess.check_output(["git", "show", "HEAD:" + REC_REL]).replace(b"\r\n", b"\n")

obs_md5 = hashlib.md5(obs_bytes).hexdigest()
rec_md5 = hashlib.md5(rec_bytes).hexdigest()


def wrap(b: bytes) -> str:
    s = base64.b64encode(b).decode("ascii")
    return "\n".join(s[i:i + 76] for i in range(0, len(s), 76))


script = f"""#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
#  ref-vs-fill — book close-side PnL from the ACTUAL entry fill, not the alert.
#  Branch bitunix-d1-netted-close-2026-06-21 @ HEAD (commit d234046).
#
#  TWO files. Drift-gated (BOTH up front) · backup · atomic mv · re-verify ·
#  rolls back BOTH on any mismatch. *** NO RESTART *** (operator restarts).
#
#  observer  : targeted-hunk (prod e88a7abc + the 8-line capture; D4 PRESERVED).
#  reconciler: full-file (base 5c4c8dba = D1; target = D1 + ref-vs-fill).
#  Rollback:  cp <f>.bak-pre-refvfill-2026-06-22 <f>   (both, then restart)
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail

ROOT="${{TC_ROOT:-/home/azureuser/trading_corp}}"
OBS="$ROOT/{OBS_REL}"
REC="$ROOT/{REC_REL}"
OBS_BASE="{OBS_BASE}"; OBS_NEW="{obs_md5}"
REC_BASE="{REC_BASE}"; REC_NEW="{rec_md5}"
SFX="bak-pre-refvfill-2026-06-22"

echo "[rvf] observer   = $OBS"
echo "[rvf] reconciler = $REC"

md5of() {{ md5sum "$1" | awk '{{print $1}}'; }}

# ── 1. drift-gate BOTH (abort before touching anything) ──────────────────────
for pair in "OBS:$OBS:$OBS_BASE" "REC:$REC:$REC_BASE"; do
  name="${{pair%%:*}}"; rest="${{pair#*:}}"; path="${{rest%%:*}}"; want="${{rest##*:}}"
  [ -f "$path" ] || {{ echo "[rvf] ABORT: $name missing: $path"; exit 2; }}
  got=$(md5of "$path")
  if [ "$got" != "$want" ]; then
    echo "[rvf] ABORT: $name DRIFTED. expected $want got $got"
    echo "[rvf]   refusing to apply over an unexpected base — re-stage vs current prod."
    exit 3
  fi
  echo "[rvf] drift gate OK: $name == $want"
done

# ── 2. backup BOTH ───────────────────────────────────────────────────────────
for path in "$OBS" "$REC"; do
  bak="$path.$SFX"
  [ -e "$bak" ] && {{ echo "[rvf] ABORT: backup exists ($bak) — prior apply?"; exit 4; }}
  cp -p "$path" "$bak"; echo "[rvf] backup: $bak"
done

# ── 3. write BOTH (embedded base64) → temp → atomic mv ───────────────────────
write_file() {{  # $1=path  $2=tmp-tag
  local path="$1" tmp
  tmp="$path.$2.tmp.$$"
  base64 -d > "$tmp"
  chmod --reference="$path.$SFX" "$tmp" 2>/dev/null || true
  chown --reference="$path.$SFX" "$tmp" 2>/dev/null || true
  mv -f "$tmp" "$path"
}}

write_file "$OBS" obs <<'OBS_B64'
{wrap(obs_bytes)}
OBS_B64
echo "[rvf] observer written (atomic mv)"

write_file "$REC" rec <<'REC_B64'
{wrap(rec_bytes)}
REC_B64
echo "[rvf] reconciler written (atomic mv)"

# ── 4. re-verify BOTH (roll back BOTH on any mismatch) ───────────────────────
og=$(md5of "$OBS"); rg=$(md5of "$REC")
if [ "$og" != "$OBS_NEW" ] || [ "$rg" != "$REC_NEW" ]; then
  echo "[rvf] FAIL: post-write md5 mismatch — ROLLING BACK BOTH"
  echo "[rvf]   observer   got $og want $OBS_NEW"
  echo "[rvf]   reconciler got $rg want $REC_NEW"
  cp -p "$OBS.$SFX" "$OBS"; cp -p "$REC.$SFX" "$REC"
  echo "[rvf] rolled back both from .$SFX"
  exit 5
fi
echo "[rvf] re-verify OK: observer==$OBS_NEW  reconciler==$REC_NEW"

# ── 5. syntax sanity ─────────────────────────────────────────────────────────
if python3 -m py_compile "$OBS" "$REC"; then
  echo "[rvf] py_compile OK (both)"
else
  echo "[rvf] WARN: py_compile failed — review before restart"
fi

echo "[rvf] ─────────────────────────────────────────────────────────────────"
echo "[rvf] DONE — both files applied. NO restart performed."
echo "[rvf] NEXT (operator): restart the engine to LOAD ref-vs-fill, then VERIFY.sh."
echo "[rvf] ROLLBACK: cp \\"$OBS.$SFX\\" \\"$OBS\\" ; cp \\"$REC.$SFX\\" \\"$REC\\"  (then restart)"
"""

OUT.write_text(script, encoding="utf-8", newline="\n")
print("wrote", OUT)
print("OBS  base", OBS_BASE, "-> target", obs_md5)
print("REC  base", REC_BASE, "-> target", rec_md5)
print("obs bytes", len(obs_bytes), " rec bytes", len(rec_bytes))
