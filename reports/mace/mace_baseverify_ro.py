#!/usr/bin/env python3
# READ-ONLY base-verify (2026-09-18): confirm the BOX running files == the trigmid build (2362db46)
# BEFORE building the mark-trust guard. NO writes / NO broker: md5sum/sha256sum/systemctl only.
import subprocess

ROOT = "/home/azureuser/trading_corp"
MACE = ROOT + "/trading_corp/mace"
# expected LF-normalized md5[:8] from local branch base cc-wt-trigmid-pl @ 2362db46
EXPECT = {
    "manager.py": "a83b62e1",
    "execution.py": "a65955bb",
    "strategy.py": "5937a4e3",
    "config.py": "30d9d99a",
    "notify.py": "95abf231",
}
EXPECT_CFG_SHA = "bfde856f1c46"


def sh(cmd, timeout=60):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "").strip()
        if p.stderr and p.stderr.strip():
            out += " [stderr] " + p.stderr.strip()[:200]
        return out
    except Exception as e:
        return "[cmd-error] %r" % (e,)


print("===== BASE VERIFY (box running files == trigmid build 2362db46) =====")
print(sh("systemctl show trading-corp -p MainPID,ActiveState,SubState,ExecMainStartTimestamp 2>&1"))
print("--- box mace/*.py md5[:8] (files are LF on box) vs expected trigmid ---")
all_ok = True
for f, exp in EXPECT.items():
    got = sh("md5sum '%s/%s' 2>/dev/null | cut -c1-8" % (MACE, f))
    ok = (got == exp)
    all_ok = all_ok and ok
    print("  %-14s box=%s expect=%s %s" % (f, got, exp, "OK" if ok else "*** DRIFT ***"))
cfg = sh("sha256sum '%s/config/mace.yaml' 2>/dev/null | cut -c1-12" % ROOT)
cfg_ok = (cfg == EXPECT_CFG_SHA)
all_ok = all_ok and cfg_ok
print("  %-14s box=%s expect=%s %s" % ("config/mace.yaml", cfg, EXPECT_CFG_SHA, "OK" if cfg_ok else "*** DRIFT ***"))
print("\nVERDICT:", "BASE VERIFIED - box == trigmid build, safe to branch off 2362db46"
      if all_ok else "*** DRIFT - box != trigmid build; STOP + reconcile before building ***")
print("===== END BASE VERIFY =====")
