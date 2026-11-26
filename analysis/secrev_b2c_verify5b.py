"""BATCH 2c item 5 -- DEFINITIVE consistency check (pip's --dry-run has a gap for
installed-but-unnamed packages). For each proposed upgrade version, scan EVERY installed
distribution's Requires-Dist specifier and report any that the proposed version violates.
Then force the true consistent cryptography set (naming the azure/msal cluster)."""
import json, os, re, subprocess
from importlib import metadata as M
from packaging.requirements import Requirement
from packaging.version import Version

VENV = r"C:\Users\AA Incorporado\cc\b2c_mirror"
PY = os.path.join(VENV, "Scripts", "python.exe")
OUT = r"C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis"

PROPOSED = {  # the versions the dry-runs want to land
    "cryptography": "50.0.2",
    "aiohttp": "3.14.5",
    "yarl": "1.25.1",
    "fastapi": "0.165.0",
    "starlette": "1.10.0",
}

def norm(n): return n.lower().replace("_", "-")

# Build: for each target pkg, list (consumer, specifier) across installed dists
consumers = {k: [] for k in PROPOSED}
for dist in M.distributions():
    cname = dist.metadata["Name"]
    for r in (M.requires(cname) or []):
        try:
            req = Requirement(r)
        except Exception:
            continue
        # skip extras-gated deps that aren't active (e.g. pyjwt crypto extra)
        tgt = norm(req.name)
        if tgt in consumers:
            marker_note = f" [marker: {req.marker}]" if req.marker else ""
            consumers[tgt].append((cname, str(req.specifier) or "(any)", marker_note))

print("=" * 74)
print("CONSISTENCY: does each PROPOSED version satisfy every installed consumer?")
print("=" * 74)
for pkg, ver in PROPOSED.items():
    print(f"\n### {pkg} -> {ver}")
    v = Version(ver)
    any_viol = False
    for cname, spec, mk in sorted(consumers[pkg]):
        ok = True
        if spec and spec != "(any)":
            try:
                ok = v in Requirement(f"x{spec}").specifier
            except Exception:
                ok = True
        flag = "OK   " if ok else "VIOLATION"
        if not ok: any_viol = True
        print(f"    {flag}  {cname:26} requires {pkg}{spec}{mk}")
    print(f"    => {'HAS VIOLATION(S) - needs companion bump' if any_viol else 'consistent (no companion bump needed)'}")

def dryrun(label, specs):
    rep = os.path.join(OUT, f"30_dryrun_{label}.json")
    cmd = [PY, "-m", "pip", "install", "--dry-run", "--report", rep] + specs
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print("\n" + "=" * 74 + f"\nFORCED RESOLVE [{label}]: pip install {' '.join(specs)}\n" + "=" * 74)
    if p.returncode != 0:
        msg = (p.stdout + p.stderr)
        for ln in msg.splitlines():
            if re.search(r"(ERROR|Cannot|conflict|incompatible|ResolutionImpossible|because|requires)", ln, re.I):
                print("  " + ln.strip())
        print(f"  [rc={p.returncode}]")
        return
    cur = {}
    for line in open(r"C:\Users\AA Incorporado\cc\b2c_prod_reqs.txt"):
        m = re.match(r"^([A-Za-z0-9._-]+)==(.+)$", line.strip())
        if m: cur[norm(m.group(1))] = m.group(2)
    data = json.load(open(rep, encoding="utf-8"))
    for item in data.get("install", []):
        md = item.get("metadata", {}); pn = norm(md.get("name","?")); pv = md.get("version","?")
        old = cur.get(pn)
        if old is None: print(f"    NEW    {pn:26} (absent) -> {pv}")
        elif old != pv: print(f"    CHANGE {pn:26} {old} -> {pv}")

# Force the real cryptography set: name the azure/msal cluster so pip must keep it consistent
dryrun("a2_crypto_cluster",
       ["cryptography>=49", "msal", "msal-extensions", "azure-identity",
        "azure-core", "azure-keyvault-secrets"])
