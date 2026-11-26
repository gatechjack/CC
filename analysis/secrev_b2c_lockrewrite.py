"""BATCH 2c item 0d -- rewrite requirements.lock: swap the 6 changed blocks to the new
version + Linux hashes (preserving each '# via' trailer) and insert a new opentelemetry-api
block in alpha order with '# via fastapi'. Hashes come from the generated pins files."""
import re, os

LOCK = r"C:\Users\AA Incorporado\cc-b2c-2026-11-25-wt\requirements.lock"
PINS = [r"C:\Users\AA Incorporado\cc\b2c_pins\batch2c1_pins.txt",
        r"C:\Users\AA Incorporado\cc\b2c_pins\batch2c2_pins.txt"]

def norm(n): return n.lower().replace("_","-")

# parse pins -> name -> (version, [hashes])
new = {}
for pf in PINS:
    cur = None
    for line in open(pf, encoding="utf-8"):
        m = re.match(r"^([A-Za-z0-9._-]+)==([^\s\\]+)", line)
        if m:
            cur = norm(m.group(1)); new[cur] = (m.group(2), [])
        else:
            h = re.search(r"--hash=sha256:([0-9a-f]+)", line)
            if h and cur: new[cur][1].append(h.group(1))

TARGETS = {"aiohttp","yarl","fastapi","starlette","cryptography","msal"}

lines = open(LOCK, encoding="utf-8").read().splitlines()

# split into blocks: each starts at a top-level 'name==' line, runs to the next one
blocks = []  # (name or None, [lines])
header_re = re.compile(r"^([A-Za-z0-9._-]+)==")
i = 0; preamble = []
while i < len(lines) and not header_re.match(lines[i]):
    preamble.append(lines[i]); i += 1
while i < len(lines):
    nm = norm(header_re.match(lines[i]).group(1))
    blk = [lines[i]]; i += 1
    while i < len(lines) and not header_re.match(lines[i]):
        blk.append(lines[i]); i += 1
    blocks.append([nm, blk])

def via_of(blk):
    return [l for l in blk if l.lstrip().startswith("#")]

def render(name, ver, hashes, via):
    out = [f"{name}=={ver} \\"]
    for j,h in enumerate(hashes):
        out.append(f"    --hash=sha256:{h}" + (" \\" if j < len(hashes)-1 else ""))
    out.extend(via)
    return out

# canonical display names (match existing casing where it matters: PyJWT etc. not relevant here)
DISP = {"aiohttp":"aiohttp","yarl":"yarl","fastapi":"fastapi","starlette":"starlette",
        "cryptography":"cryptography","msal":"msal","opentelemetry-api":"opentelemetry-api"}

changed = []
for b in blocks:
    nm = b[0]
    if nm in TARGETS:
        ver, hashes = new[nm]
        b[1] = render(DISP[nm], ver, hashes, via_of(b[1]))
        changed.append(f"{nm}=={ver} ({len(hashes)} hashes)")

# insert opentelemetry-api in alpha order with '# via fastapi'
otel_ver, otel_hashes = new["opentelemetry-api"]
otel_blk = ["opentelemetry-api", render("opentelemetry-api", otel_ver, otel_hashes, ["    # via fastapi"])]
pos = len(blocks)
for idx,b in enumerate(blocks):
    if b[0] > "opentelemetry-api":
        pos = idx; break
blocks.insert(pos, otel_blk)
changed.append(f"opentelemetry-api=={otel_ver} ({len(otel_hashes)} hashes) INSERTED before '{blocks[pos+1][0]}'")

# reassemble
out = list(preamble)
for b in blocks: out.extend(b[1])
open(LOCK, "w", encoding="utf-8", newline="\n").write("\n".join(out) + "\n")

print("requirements.lock rewritten. Changes:")
for c in changed: print("  "+c)
print(f"total blocks now: {len(blocks)}")
