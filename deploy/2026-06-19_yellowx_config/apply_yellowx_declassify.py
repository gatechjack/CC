#!/usr/bin/env python3
"""Targeted, drift-gated declassification of mc_a_yellow_x from the LIVE prod
config/strategies.yaml.

mc_a_yellow_x is a NON-DIRECTIONAL whale/manipulation flag that was miscategorized
as a `side: buy` directional factor (added spurious bull points). This removes the
single factor block and replaces it with a doc comment, BYTE-PRESERVING the rest of
the file (prod config 569c38f8 carries live operator settings — execution_mode,
DD-cap, kalshi divisions — which must NOT change).

Fail-closed: writes ONLY if the EXACT expected block is found exactly once (else
ABORT, no write). Backs up first; re-parses the YAML and asserts mc_a_yellow_x is no
longer a factor key. Requires a RESTART to take effect (config-and-restart, no
hot-reload). Rollback = restore the .bak + restart.

Run on prod (read the result; it does ONE config write + a backup):
  /home/azureuser/trading_corp/venv/bin/python apply_yellowx_declassify.py
"""
import hashlib
import pathlib
import sys

import yaml

CFG = pathlib.Path("/home/azureuser/trading_corp/config/strategies.yaml")
BAK = CFG.parent / (CFG.name + ".bak-pre-yellowx-2026-06-19")

OLD = (
    "      mc_a_yellow_x:\n"
    "        weight: 2\n"
    "        side: buy\n"
    "        ttl_minutes: 30\n"
    '        ttl_per_tf: {"3m": 30, "15m": 90, "30m": 180}\n'
)
NEW = (
    "      # mc_a_yellow_x - INTENTIONALLY NOT a directional factor. It is a\n"
    "      # NON-DIRECTIONAL whale/manipulation / tape-anomaly flag (neither bull\n"
    "      # nor bear). Was miscategorized as side: buy (added spurious bull\n"
    "      # points); declassified 2026-06-19. Handled like the other\n"
    "      # non-directional signals: absent from factors -> the scorer ignores it\n"
    "      # (btc_accumulator.evaluate_confluence: unknown signal -> 0 score) while\n"
    "      # it still flows through the alert ledger / audit. Do NOT re-add it as a\n"
    "      # factor, and do NOT flip it to side: sell (the same error inverted).\n"
)


def _factor_keys(node):
    out = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "factors" and isinstance(v, dict):
                out |= set(v.keys())
            out |= _factor_keys(v)
    elif isinstance(node, list):
        for it in node:
            out |= _factor_keys(it)
    return out


def main() -> int:
    raw = CFG.read_text()
    before = hashlib.md5(raw.encode()).hexdigest()
    n = raw.count(OLD)
    if n != 1:
        print(f"ABORT: expected exactly 1 mc_a_yellow_x block, found {n} — prod "
              f"config differs from the expected text. STOP + re-review (NO write).")
        return 1
    new = raw.replace(OLD, NEW)
    parsed = yaml.safe_load(new)            # must still be valid YAML
    if "mc_a_yellow_x" in _factor_keys(parsed):
        print("ABORT: mc_a_yellow_x still a factor after edit — NOT writing.")
        return 1
    BAK.write_bytes(raw.encode())           # backup BEFORE the write
    CFG.write_text(new)
    after = hashlib.md5(CFG.read_text().encode()).hexdigest()
    print("OK: mc_a_yellow_x declassified (removed from factors).")
    print(f"  backup: {BAK}")
    print(f"  md5 {before} -> {after}")
    print("  NOTE: requires a RESTART to take effect (config-and-restart, no hot-reload).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
