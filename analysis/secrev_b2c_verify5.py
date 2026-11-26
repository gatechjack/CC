"""BATCH 2c item 5 -- (1) re-parse dry-run reports as UTF-8 for full change lists;
(2) read INSTALLED metadata Requires-Dist to find the real version constraints each
consumer places on cryptography / starlette / aiohttp / yarl. Settles whether the
cryptography bump truly needs an msal/azure bump, and whether streamlit caps starlette."""
import json, os, re
from importlib import metadata as M

OUT = r"C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis"
REQS = r"C:\Users\AA Incorporado\cc\b2c_prod_reqs.txt"

cur = {}
for line in open(REQS):
    m = re.match(r"^([A-Za-z0-9._-]+)==(.+)$", line.strip())
    if m:
        cur[m.group(1).lower().replace("_", "-")] = m.group(2)

print("=" * 72, "\nFULL dry-run change lists (UTF-8 re-parse)\n" + "=" * 72)
for name in ["a_cryptography", "b_aiohttp", "c_fastapi"]:
    rep = os.path.join(OUT, f"29_dryrun_{name}.json")
    if not os.path.exists(rep):
        print(f"{name}: (no report)"); continue
    data = json.load(open(rep, encoding="utf-8"))
    print(f"\n### {name}:")
    for item in data.get("install", []):
        md = item.get("metadata", {})
        pn = md.get("name", "?").lower().replace("_", "-"); pv = md.get("version", "?")
        old = cur.get(pn)
        if old is None:
            print(f"    NEW    {pn:26} (absent) -> {pv}")
        elif old != pv:
            print(f"    CHANGE {pn:26} {old} -> {pv}")

def constraint(pkg, dep):
    """Return the version spec `pkg` declares on `dep` (installed metadata)."""
    try:
        reqs = M.requires(pkg) or []
    except M.PackageNotFoundError:
        return "(not installed)"
    dep = dep.lower()
    hits = []
    for r in reqs:
        base = re.split(r"[;\[]", r)[0].strip()
        mm = re.match(r"^([A-Za-z0-9._-]+)\s*(.*)$", base)
        if mm and mm.group(1).lower().replace("_", "-") == dep:
            hits.append(r.strip())
    return " | ".join(hits) if hits else "(no pin on " + dep + ")"

print("\n" + "=" * 72, "\nINSTALLED constraints on cryptography (who blocks >=49?)\n" + "=" * 72)
for p in ["msal", "azure-identity", "azure-core", "azure-keyvault-secrets",
          "pykalshi", "robin-stocks", "ccxt", "pyjwt"]:
    print(f"  {p:24} ({cur.get(p,'?'):10}) -> {constraint(p,'cryptography')}")

print("\n" + "=" * 72, "\nINSTALLED constraints on starlette (fastapi + streamlit)\n" + "=" * 72)
for p in ["fastapi", "streamlit"]:
    print(f"  {p:24} ({cur.get(p,'?'):10}) -> {constraint(p,'starlette')}")

print("\n" + "=" * 72, "\nINSTALLED constraints on aiohttp / yarl (ccxt + web3)\n" + "=" * 72)
for p in ["ccxt", "web3"]:
    print(f"  {p:24} ({cur.get(p,'?'):10}) -> aiohttp: {constraint(p,'aiohttp')}")
for p in ["aiohttp", "ccxt"]:
    print(f"  {p:24} ({cur.get(p,'?'):10}) -> yarl: {constraint(p,'yarl')}")

print("\n" + "=" * 72, "\nReverse-deps: who requires web3 / opentelemetry-api\n" + "=" * 72)
for target in ["web3", "aiohttp", "cryptography"]:
    users = []
    for dist in M.distributions():
        nm = dist.metadata["Name"]
        for r in (M.requires(nm) or []):
            base = re.split(r"[;\[]", r)[0].strip()
            mm = re.match(r"^([A-Za-z0-9._-]+)", base)
            if mm and mm.group(1).lower().replace("_", "-") == target:
                users.append(nm); break
    print(f"  {target:14} required-by: {sorted(set(users))}")
