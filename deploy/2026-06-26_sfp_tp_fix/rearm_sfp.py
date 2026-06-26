#!/usr/bin/env python3
"""Phase 2 / Step 4 — HOT RE-ARM the bitunix_sfp division (auto_execute false->true).

BLOCK-SCOPED + FAIL-CLOSED. Only the `bitunix_sfp:` block is touched; the script
refuses (exits non-zero, writes nothing) unless that block has exactly ONE
auto_execute, it is currently `false`, and execution_mode is `live`. It then
backs up, flips, and POST-ASSERTS that futures + pead auto_execute are unchanged.
NO restart needed: the engine's _yaml_auto_execute() fresh-reads per signal.

There are ~20 other `auto_execute: false` lines in this file — that is exactly
why this is block-scoped and never a global sed.
"""
import re
import shutil
import sys

P = "/home/azureuser/trading_corp/config/strategies.yaml"
BAK = "/home/azureuser/strategies.yaml.bak-pre-sfp-rearm-2026-06-26"


def load():
    with open(P, encoding="utf-8", newline="") as f:   # newline="" => preserve exact line endings
        return f.readlines()


def block_bounds(lines, key):
    """[start, end) line indices of a top-level YAML block, end = next top-level key or EOF."""
    start = next((i for i, l in enumerate(lines) if re.match(rf"^{re.escape(key)}:", l)), None)
    if start is None:
        return None, None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^[A-Za-z_]", lines[j]):   # next column-0 key (comments/blank lines don't count)
            end = j
            break
    return start, end


def field_idx(lines, start, end, field):
    return [k for k in range(start, end) if re.match(rf"^\s+{field}\s*:", lines[k])]


def show(lines, label):
    print(f"--- {label} ---")
    for key in ("bitunix_futures", "robinhood_pead", "bitunix_sfp"):
        s, e = block_bounds(lines, key)
        if s is None:
            print(f"  {key}: NOT FOUND")
            continue
        ae = field_idx(lines, s, e, "auto_execute")
        em = field_idx(lines, s, e, "execution_mode")
        ae_s = f"L{ae[0] + 1} {lines[ae[0]].strip()}" if ae else "auto_execute MISSING"
        em_s = f"L{em[0] + 1} {lines[em[0]].strip()}" if em else "execution_mode (none)"
        print(f"  {key}: {ae_s} | {em_s}")


def main():
    lines = load()
    s, e = block_bounds(lines, "bitunix_sfp")
    if s is None:
        sys.exit("ABORT: bitunix_sfp block not found")
    ae = field_idx(lines, s, e, "auto_execute")
    if len(ae) != 1:
        sys.exit(f"ABORT: expected exactly 1 auto_execute in bitunix_sfp block, found {len(ae)} at {[k + 1 for k in ae]}")
    k = ae[0]
    if not re.search(r"auto_execute\s*:\s*false", lines[k]):
        sys.exit(f"ABORT: bitunix_sfp auto_execute is not 'false' (L{k + 1}): {lines[k].strip()!r}")
    em = field_idx(lines, s, e, "execution_mode")
    if not em or not re.search(r"execution_mode\s*:\s*live", lines[em[0]]):
        sys.exit("ABORT: bitunix_sfp execution_mode is not 'live' — refusing to arm")

    show(lines, "BEFORE")
    shutil.copy2(P, BAK)
    lines[k] = re.sub(r"(auto_execute\s*:\s*)false", r"\1true", lines[k], count=1)
    with open(P, "w", encoding="utf-8", newline="") as f:
        f.writelines(lines)

    after = load()
    show(after, "AFTER")

    # POST-ASSERTIONS (fail loud if anything is off)
    s2, e2 = block_bounds(after, "bitunix_sfp")
    a2 = field_idx(after, s2, e2, "auto_execute")
    if not re.search(r"auto_execute\s*:\s*true", after[a2[0]]):
        sys.exit("POST-CHECK FAILED: bitunix_sfp auto_execute is not 'true' after write")
    e2m = field_idx(after, s2, e2, "execution_mode")
    if not re.search(r"execution_mode\s*:\s*live", after[e2m[0]]):
        sys.exit("POST-CHECK FAILED: bitunix_sfp execution_mode changed off 'live'")
    for key, want in (("bitunix_futures", "true"), ("robinhood_pead", "true")):
        bs, be = block_bounds(after, key)
        a = field_idx(after, bs, be, "auto_execute")
        if not re.search(rf"auto_execute\s*:\s*{want}", after[a[0]]):
            sys.exit(f"POST-CHECK FAILED: {key} auto_execute changed (expected {want}) — RESTORE {BAK}")

    print(f"\nOK: bitunix_sfp ARMED (auto_execute -> true at L{k + 1}); execution_mode live unchanged; "
          f"bitunix_futures + robinhood_pead untouched.")
    print(f"backup: {BAK}")


if __name__ == "__main__":
    main()
