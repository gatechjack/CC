# Emit base (b11af9b) and target (HEAD) LF-md5 manifests for the 8 deploy files.
# LF-md5 = md5 of content with CRLF normalized to LF (prod files are LF).
import hashlib
import subprocess

WT = r"C:\Users\AA Incorporado\cc-2026-08-13b-wt"
BASE = "b11af9b"
TARGET = "HEAD"
FILES = [
    "trading_corp/mace/manager.py",
    "trading_corp/mace/execution.py",
    "trading_corp/mace/loops.py",
    "trading_corp/web/mace_view.py",
    "trading_corp/web/templates/mace_live.html",
    "trading_corp/web/templates/partials/mace_halt.html",  # NEW at target
    "config/mace.yaml",
    "config/ex_dividend_calendar.yaml",
]

def blob_md5(rev, path):
    r = subprocess.run(["git", "-C", WT, "show", f"{rev}:{path}"],
                       capture_output=True)
    if r.returncode != 0:
        return None
    return hashlib.md5(r.stdout.replace(b"\r\n", b"\n")).hexdigest()

print(f"{'file':55s} {'base(b11af9b)':34s} target(HEAD)")
for f in FILES:
    b = blob_md5(BASE, f) or "ABSENT"
    t = blob_md5(TARGET, f) or "ABSENT"
    print(f"{f:55s} {b:34s} {t}")
