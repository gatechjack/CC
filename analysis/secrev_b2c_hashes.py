"""BATCH 2c item 0d -- generate REAL Linux (manylinux cp312 x86_64 wheels + sdist) hashes
from PyPI for the 2c-1 / 2c-2 pins, verify opentelemetry-api's full dependency closure
against the current requirements.lock, and emit (1) --require-hashes pins files for the
deploy and (2) requirements.lock blocks. No placeholder / PC-wheel hashes.
"""
import json, os, re, ssl, urllib.request
import certifi
from packaging.requirements import Requirement
from packaging.version import Version

LOCK = r"C:\Users\AA Incorporado\cc-b2c-2026-11-25-wt\requirements.lock"
OUTDIR = r"C:\Users\AA Incorporado\cc\b2c_pins"
os.makedirs(OUTDIR, exist_ok=True)
CTX = ssl.create_default_context(cafile=certifi.where())

SET1 = [("aiohttp","3.14.5"),("yarl","1.25.1"),("fastapi","0.165.0"),
        ("starlette","1.10.0"),("opentelemetry-api","1.45.2")]
SET2 = [("cryptography","50.0.2"),("msal","1.39.0")]

def pypi(pkg, ver):
    url = f"https://pypi.org/pypi/{pkg}/{ver}/json"
    with urllib.request.urlopen(url, context=CTX, timeout=30) as r:
        return json.load(r)

def lock_pins():
    pins = {}
    for line in open(LOCK, encoding="utf-8"):
        m = re.match(r"^([A-Za-z0-9._-]+)==([^\s\\]+)", line)
        if m:
            pins[m.group(1).lower().replace("_","-")] = m.group(2)
    return pins

BOXENV = {"python_version":"3.12","python_full_version":"3.12.16","sys_platform":"linux",
          "platform_machine":"x86_64","os_name":"posix","platform_system":"Linux",
          "platform_python_implementation":"CPython","implementation_name":"cpython"}

def select_linux(data):
    """sdist + linux x86_64 wheels a cp312 glibc box can install: cp312-cp312 (non-abi3),
    or cpXY-abi3 with XY<=312 (exclude abi3t free-threaded + cp>312), or pure py3-none-any."""
    out = []
    for f in data["urls"]:
        fn = f["filename"]; low = fn.lower(); sha = f["digests"]["sha256"]
        if f["packagetype"] == "sdist":
            out.append((fn, sha)); continue
        if not low.endswith(".whl"):
            continue
        if "py3-none-any" in low or "py2.py3-none-any" in low:   # pure python
            out.append((fn, sha)); continue
        if not ("x86_64" in low and "manylinux" in low):
            continue
        if "abi3t" in low:                                        # free-threaded, not the box
            continue
        m = re.search(r"cp(\d+)-abi3", low)
        if m:                                                     # abi3: usable if cpXY<=312
            if int(m.group(1)) <= 312:
                out.append((fn, sha))
            continue
        if "cp312-cp312" in low:                                  # version-specific cp312
            out.append((fn, sha))
    return out

def dep_active(req):
    """True if this runtime dep is active on the box (marker evaluates true / absent)."""
    if req.marker is None:
        return True
    if "extra ==" in str(req.marker):
        return False
    try:
        return req.marker.evaluate(BOXENV)
    except Exception:
        return True

def emit_block(pkg, ver, files):
    lines = [f"{pkg}=={ver} \\"]
    for i,(fn,sha) in enumerate(files):
        tail = " \\"
        lines.append(f"    --hash=sha256:{sha}{tail}")
    # strip trailing backslash on the very last line
    lines[-1] = lines[-1][:-2]
    return "\n".join(lines), [fn for fn,_ in files]

pins = lock_pins()
print("="*74, "\n0d HASH GENERATION (real PyPI, linux cp312 x86_64 + sdist)\n"+"="*74)

def process(setname, pkgs):
    blocks = []
    reqfile = os.path.join(OUTDIR, f"{setname}_pins.txt")
    lockfile = os.path.join(OUTDIR, f"{setname}_lockblocks.txt")
    for pkg, ver in pkgs:
        d = pypi(pkg, ver)
        files = select_linux(d)
        if not files:
            print(f"  !! {pkg}=={ver}: NO linux/pure files selected"); continue
        block, fns = emit_block(pkg, ver, files)
        blocks.append(block)
        print(f"\n  {pkg}=={ver}: {len(files)} file(s)")
        for fn in fns: print(f"      {fn}")
        # dep check vs lock (runtime deps only, ignore extras-gated)
        set_names = {p.lower().replace("_","-") for p,_ in pkgs}
        for r in (d["info"].get("requires_dist") or []):
            try: req = Requirement(r)
            except Exception: continue
            if not dep_active(req):
                mk = f" [marker false on box: {req.marker}]" if req.marker else ""
                print(f"      dep-inactive: {req.name}{req.specifier}{mk}")
                continue
            nm = req.name.lower().replace("_","-")
            have = pins.get(nm)
            in_set = nm in set_names
            if in_set:
                print(f"      dep-in-this-set: {req.name}{req.specifier} (covered)")
            elif have is None:
                print(f"      *** DEP-NEW: {pkg} needs {req.name}{req.specifier} -- NOT in lock -> MUST ADD ***")
            else:
                ok = (not req.specifier) or (Version(have) in req.specifier)
                if not ok:
                    print(f"      *** DEP-INCOMPATIBLE: {pkg} needs {req.name}{req.specifier}; lock has {have} ***")
    open(reqfile,"w",encoding="utf-8").write("\n".join(blocks)+"\n")
    open(lockfile,"w",encoding="utf-8").write("\n".join(blocks)+"\n")
    print(f"\n  -> wrote {reqfile}")
    return reqfile

print("\n### SET 2c-1"); process("batch2c1", SET1)
print("\n### SET 2c-2"); process("batch2c2", SET2)
print("\nDONE")
