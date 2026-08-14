# MACE OQ-2 deploy-runner generator (2026-08-13, Board tonight-deploy ruling).
# Emits 4 prod-side bash scripts into C:\Users\AA Incorporado\cc, all LF, pure ASCII:
#   _mace_oq2_deploy_payload.sh  self-gated deploy (PRE-GATE -> decode -> STAGE-GATE ->
#                                backup+rollback.sh -> swap -> POST-GATE -> py_compile; NO restart)
#   _mace_oq2_restart.sh         restart + boot wait (ET-window guard 15:35-16:00)
#   _mace_oq2_verify.sh          boot-verify checklist incl. halt-latch ARM->HALT->ARM cycle
#   _mace_oq2_shadow_am.sh       morning READ-ONLY shadow-eval (confidence check, not a gate)
# Gates: HEAD must be bb3cb7a, tree clean, every blob md5 must equal the pinned manifest.
import base64
import hashlib
import io
import subprocess
import sys
import tarfile

WT = r"C:\Users\AA Incorporado\cc-2026-08-13b-wt"
OUT = r"C:\Users\AA Incorporado\cc"
BASE_REV = "b11af9b"
TARGET_REV = "bb3cb7a"

# (repo-relative path, base b11af9b LF-md5, target bb3cb7a LF-md5)
EXISTING = [
    ("trading_corp/mace/manager.py",
     "ef84efc96790cb6afcd1b25e8b3dd6c2", "2f9ca06c37cdee27d55a5d48f9614c82"),
    ("trading_corp/mace/execution.py",
     "1cb214fee9a4774abab6fdb9df24ab65", "01c0b2594b11355b5c1c98ceb3e6987f"),
    ("trading_corp/mace/loops.py",
     "015f35d8fa0bb699426d950006e894bd", "5a9b3d9f38230407eab14c4a8f56cc9d"),
    ("trading_corp/web/mace_view.py",
     "8251be6f6cf6b952b04f2fd8b23a1b62", "c4a8004805d8943b604d5f149f446d91"),
    ("trading_corp/web/templates/mace_live.html",
     "b4dfcafdc2678774e1f4e64dbe5c89b5", "f5bc01cd000a83ddb3f921e7a6d9e08e"),
    ("config/mace.yaml",
     "454fff5bc7249b9d104bef9aadf073ff", "1dc7c276cbab2ac40e4ff62da3346574"),
    ("config/ex_dividend_calendar.yaml",
     "d320ff69e964e10f9cec4b8dba29a98c", "3feb4183ea8de4c3ddcce74dba1ed71d"),
]
NEW_FILE = ("trading_corp/web/templates/partials/mace_halt.html",
            None, "0058a239b4f3b541071786aa14c2c919")
ALL8 = EXISTING + [NEW_FILE]


def git(*args):
    r = subprocess.run(["git", "-C", WT] + list(args), capture_output=True)
    if r.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed: {r.stderr.decode()[:300]}")
    return r.stdout


def blob_lf(rev, path):
    return git("show", f"{rev}:{path}").replace(b"\r\n", b"\n")


def md5(b):
    return hashlib.md5(b).hexdigest()


def write_lf_ascii(name, text):
    data = text.encode("ascii")  # raises on any non-ASCII char
    assert b"\r" not in data, f"{name}: CR found"
    with open(f"{OUT}\\{name}", "wb") as f:
        f.write(data)
    print(f"  wrote {name}  {len(data)} bytes")


# ── gates: HEAD pin + clean tree + manifest verification ─────────────────────
head = git("rev-parse", "HEAD").decode().strip()
if not head.startswith(TARGET_REV):
    sys.exit(f"HEAD is {head[:12]}, expected {TARGET_REV}* — refusing")
if git("status", "--porcelain").strip():
    sys.exit("worktree not clean — refusing")
print(f"HEAD pinned: {head[:12]}  (target {TARGET_REV})")

payload_files = {}
for path, base_m, tgt_m in ALL8:
    t = blob_lf(TARGET_REV, path)
    got = md5(t)
    if got != tgt_m:
        sys.exit(f"TARGET manifest mismatch {path}: got {got} want {tgt_m}")
    payload_files[path] = t
    if base_m is not None:
        gb = md5(blob_lf(BASE_REV, path))
        if gb != base_m:
            sys.exit(f"BASE manifest mismatch {path}: got {gb} want {base_m}")
print(f"manifest verified: 8 target blobs + 7 base blobs match pinned md5s")

# ── tar.gz + base64 ─────────────────────────────────────────────────────────
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as tf:
    for path in payload_files:
        data = payload_files[path]
        ti = tarfile.TarInfo(name=path)
        ti.size = len(data)
        ti.mode = 0o644
        ti.mtime = 0
        tf.addfile(ti, io.BytesIO(data))
tgz = buf.getvalue()
b64 = base64.encodebytes(tgz).decode("ascii")  # 76-char lines, trailing \n
print(f"payload: {sum(len(v) for v in payload_files.values())} bytes raw"
      f" -> {len(tgz)} tgz -> {len(b64)} b64")

# ── script fragments ────────────────────────────────────────────────────────
def cks(root_var, which, label):
    lines = []
    for path, base_m, tgt_m in ALL8:
        m = base_m if which == "base" else tgt_m
        if m is None:
            continue
        lines.append(f'ck "${root_var}/{path}" {m} {label}')
    return "\n".join(lines)


HALT = "trading_corp/web/templates/partials/mace_halt.html"
LIVEHTML = "trading_corp/web/templates/mace_live.html"

bak_lines, restore_lines, swap_lines = [], [], []
for path, _, _ in EXISTING:
    bn = path.rsplit("/", 1)[1]
    bak_lines.append(f'cp -p "$R/{path}" "$K/{bn}.bak"')
    restore_lines.append(f'cat "$K/{bn}.bak" > "$R/{path}"')
    swap_lines.append(f'cat "$S/{path}" > "$R/{path}"')

rollback_body = f"""#!/bin/bash
# MACE OQ-2 ROLLBACK - restores prod to b11af9b (7 files back, halt partial
# removed) then RESTARTS the engine. Guarded: refuses inside 15:35-16:00 ET
# (a restart there runs daily-slots catch-up, which can PLACE).
set -u
R=/home/azureuser/trading_corp
K=/home/azureuser/mace_oq2_bak_20260813
fail(){{ echo ""; echo "ROLLBACK RESULT: FAILED - $1"; exit 1; }}
ck(){{ m=$(md5sum "$1" | cut -d" " -f1); [ "$m" = "$2" ] || fail "verify $1 got=$m want=$2"; }}
et=$(TZ=America/New_York date +%H%M)
if [ "$et" -ge 1535 ] && [ "$et" -le 1600 ]; then fail "now $et ET - inside 15:35-16:00 restart guard"; fi
{chr(10).join(restore_lines)}
rm -f "$R/{HALT}"
{cks("R", "base", "restored")}
[ -e "$R/{HALT}" ] && fail "halt partial still present"
echo "files restored to b11af9b - restarting engine"
systemctl restart trading-corp
sleep 155
systemctl show trading-corp -p MainPID -p ActiveState -p NRestarts
curl -s -o /dev/null -w "web home HTTP %{{http_code}}\\n" http://127.0.0.1:8000/
echo ""
echo "ROLLBACK RESULT: OK - prod at b11af9b, engine restarted"
"""

deploy_sh = f"""#!/bin/bash
# MACE OQ-2 + 3-active + halt-button DEPLOY payload (2026-08-13, Board
# tonight-deploy ruling). Target = claude-2026-08-13b @ {TARGET_REV}; base =
# prod-live {BASE_REV}. 8 files. NO RESTART IN THIS SCRIPT.
# Self-gated: PRE-GATE (prod==base) -> decode -> STAGE-GATE (staged==target)
# -> backup + rollback.sh -> swap -> POST-GATE -> py_compile.
set -u
R=/home/azureuser/trading_corp
K=/home/azureuser/mace_oq2_bak_20260813
S=/tmp/mace_oq2_stage
PY=$R/venv/bin/python
fail(){{ echo ""; echo "DEPLOY RESULT: FAILED - $1"; exit 1; }}
ck(){{ m=$(md5sum "$1" | cut -d" " -f1); [ "$m" = "$2" ] || fail "$3 $1 got=$m want=$2"; }}

[ -x "$PY" ] || fail "prod venv python missing: $PY"
[ -d "$R/trading_corp/web/templates/partials" ] || fail "partials dir missing"
[ -e "$K" ] && fail "backup dir already exists: $K - inspect before re-running"

echo "== PRE-GATE: prod == base {BASE_REV} =="
{cks("R", "base", "PRE-GATE")}
[ -e "$R/{HALT}" ] && fail "PRE-GATE mace_halt.html already present"
echo "PRE-GATE OK (7 files at base, halt partial absent)"

echo "== DECODE payload =="
rm -rf "$S"
mkdir -p "$S"
base64 -d > /tmp/mace_oq2_payload.tgz <<'B64PAYLOAD'
{b64}B64PAYLOAD
tar xzf /tmp/mace_oq2_payload.tgz -C "$S" || fail "tar extract"

echo "== STAGE-GATE: staged == target {TARGET_REV} =="
{cks("S", "target", "STAGE-GATE")}
echo "STAGE-GATE OK (8 files)"

echo "== BACKUP + rollback.sh =="
mkdir -p "$K"
{chr(10).join(bak_lines)}
cat > "$K/rollback.sh" <<'RBEOF'
{rollback_body}RBEOF
chmod +x "$K/rollback.sh"
echo "BACKUP OK: $K (rollback.sh written)"

echo "== SWAP =="
{chr(10).join(swap_lines)}
cp "$S/{HALT}" "$R/{HALT}"
chown --reference="$R/{LIVEHTML}" "$R/{HALT}"
chmod --reference="$R/{LIVEHTML}" "$R/{HALT}"
echo "SWAP done (cat-> for 7 existing preserves owner; new partial ref-cloned)"

echo "== POST-GATE: in-place == target =="
{cks("R", "target", "POST-GATE")}
echo "POST-GATE OK (8 files)"

echo "== PY-COMPILE (as azureuser, prod venv) =="
runuser -u azureuser -- "$PY" -m py_compile "$R/trading_corp/mace/manager.py" || fail "py_compile manager.py"
runuser -u azureuser -- "$PY" -m py_compile "$R/trading_corp/mace/execution.py" || fail "py_compile execution.py"
runuser -u azureuser -- "$PY" -m py_compile "$R/trading_corp/mace/loops.py" || fail "py_compile loops.py"
runuser -u azureuser -- "$PY" -m py_compile "$R/trading_corp/web/mace_view.py" || fail "py_compile mace_view.py"
echo "PY-COMPILE OK"

rm -rf "$S" /tmp/mace_oq2_payload.tgz
ls -la "$K"
echo ""
echo "DEPLOY RESULT: OK - 8 files live at target {TARGET_REV}. ENGINE NOT RESTARTED YET."
echo "next: mace_oq2_restart.ps1 (never 15:40-15:58 ET) then mace_oq2_verify.ps1"
echo "rollback: mace_oq2_rollback.ps1 (runs bash $K/rollback.sh)"
"""

restart_sh = """#!/bin/bash
# MACE OQ-2 RESTART runner. Guarded: refuses inside 15:35-16:00 ET (restart
# there runs daily-slots catch-up, which can PLACE). Boot takes ~2.5 min.
set -u
et=$(TZ=America/New_York date +%H%M)
if [ "$et" -ge 1535 ] && [ "$et" -le 1600 ]; then
  echo "RESTART RESULT: REFUSED - now $et ET, inside 15:35-16:00 guard"
  exit 1
fi
echo "restarting trading-corp (boot ~2.5 min: bitunix seeds 1500 closes before web :8000)"
systemctl restart trading-corp
sleep 155
systemctl show trading-corp -p MainPID -p ActiveState -p NRestarts
code=000
for i in 1 2 3 4 5 6; do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/ || echo 000)
  [ "$code" = "200" ] && break
  sleep 20
done
echo "web home HTTP $code"
T=$(systemctl show trading-corp -p ActiveEnterTimestamp --value | cut -d" " -f2-3)
echo "tracebacks since boot: $(journalctl -u trading-corp --since "$T" --no-pager | grep -c Traceback)"
echo ""
echo "RESTART RESULT: DONE - now run mace_oq2_verify.ps1"
"""

verify_sh = """#!/bin/bash
# MACE OQ-2 post-restart BOOT VERIFY - read-only EXCEPT the Board-sanctioned
# halt-latch cycle (ARM->HALT->ARM: agent_state latch writes only, no orders --
# market closed, entries eval only at the 15:45 slot).
set -u
R=/home/azureuser/trading_corp
PY=$R/venv/bin/python
echo "== unit state (want new MainPID, active, NRestarts=0) =="
systemctl show trading-corp -p MainPID -p ActiveState -p NRestarts
T=$(systemctl show trading-corp -p ActiveEnterTimestamp --value | cut -d" " -f2-3)
echo "boot: $T UTC"
J=/tmp/mace_oq2_boot.log
journalctl -u trading-corp --since "$T" --no-pager > "$J"
echo "== tracebacks since boot (want 0) =="
grep -c Traceback "$J"
echo "== config_hash lines (want NEW hash; pre-deploy was fe177fcd3882) =="
grep -i config_hash "$J" | tail -3
echo "== MACE scheduler-online lines (want all 4) =="
grep -o "MACE [a-z-]* scheduler online" "$J" | sort | uniq -c
echo "== GET /mace =="
curl -s -o /tmp/mace_page.html -w "GET /mace HTTP %{http_code}\\n" http://127.0.0.1:8000/mace
for s in IBIT XLE GDX SPY; do echo "$s on page: $(grep -c $s /tmp/mace_page.html)"; done
grep -o "ENTRIES: [A-Z()a-z ]*" /tmp/mace_page.html | head -2
grep -io "config_hash[^<]*" /tmp/mace_page.html | head -2
echo "== halt latch cycle ARM->HALT->ARM (latch only, no orders) =="
curl -s -X POST http://127.0.0.1:8000/mace/halt > /tmp/mace_halt_resp.html
grep -q "HALTED (button)" /tmp/mace_halt_resp.html && echo "HALT: OK - HALTED (button) rendered" || echo "HALT: FAIL - inspect /tmp/mace_halt_resp.html"
curl -s -X POST http://127.0.0.1:8000/mace/arm > /tmp/mace_arm_resp.html
grep -q "ENTRIES: ARMED" /tmp/mace_arm_resp.html && echo "ARM: OK - ENTRIES: ARMED rendered" || echo "ARM: FAIL - inspect /tmp/mace_arm_resp.html"
echo "== DB: latch + ui audits + rung counts (want latch cleared, halt+arm audits, SPY open=2, GLD no open) =="
runuser -u azureuser -- "$PY" - <<'PYEOF'
import sqlite3
c = sqlite3.connect("/home/azureuser/trading_corp/data/trading_corp.db")
print("entry_halt latch:", list(c.execute(
    "SELECT value_json, updated_ts FROM agent_state"
    " WHERE agent='robinhood_mace' AND key='entry_halt'")))
print("ui audits (last 4):", list(c.execute(
    "SELECT kind, ts, actor FROM audit_event WHERE kind LIKE 'mace_ui_%'"
    " ORDER BY id DESC LIMIT 4")))
print("rung counts:", list(c.execute(
    "SELECT symbol, status, COUNT(*) FROM mace_rung GROUP BY symbol, status")))
PYEOF
echo "== division health =="
for d in bitunix pead pmcc kalshi; do echo "$d boot lines: $(grep -ic $d $J)"; done
grep -Ei "resume|matched=" "$J" | head -6
curl -s -o /dev/null -w "home HTTP %{http_code}\\n" http://127.0.0.1:8000/
echo ""
echo "VERIFY DONE - checklist: new PID / 0 tracebacks / NEW config_hash /"
echo "IBIT+XLE+GDX on page + ENTRIES: ARMED / halt cycle OK-OK / 4 MACE loops /"
echo "SPY open=2, GLD no open rungs / divisions healthy / home+mace HTTP 200"
"""

shadow_sh = """#!/bin/bash
# MACE morning shadow-eval - READ-ONLY confidence check (Board ruling
# 2026-08-13: NOT a deploy gate; eval-time credit-floor filter is the
# operative safety). Places NOTHING, writes NOTHING. Run >= 09:35 ET.
# Read: each active (IBIT/XLE/GDX) should clear credit floor 0.30 x width
# on live quotes. A symbol failing floor -> Board rules: accept the engine's
# eval-time SKIP, or config-only backfill restart completed before 15:40 ET.
set -u
cd /home/azureuser/trading_corp || exit 1
runuser -u azureuser -- venv/bin/python scripts/mace_shadow_eval.py --json
"""

print("emitting scripts:")
write_lf_ascii("_mace_oq2_deploy_payload.sh", deploy_sh)
write_lf_ascii("_mace_oq2_restart.sh", restart_sh)
write_lf_ascii("_mace_oq2_verify.sh", verify_sh)
write_lf_ascii("_mace_oq2_shadow_am.sh", shadow_sh)
print("ALL SCRIPTS EMITTED OK (LF, pure ASCII)")
