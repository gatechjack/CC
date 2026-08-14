"""Surgical prod patcher: advance DASHBOARD_RT_CUTOFFS to logic-change dates.

Backports the local-committed Phase 3 change (commit e99582d) to prod.
Anchor-based; single hunk; idempotent. Pure-stdlib; Python 3.10+ safe
(uses Path.read_bytes/write_bytes, not the 3.13+ newline kwarg —
see memory `prod-python-version-3.10`).

Mirrors the surgical pattern from scripts/patch_kalshi_weather_entry_price_floor.py.

Refuses to run if:
  - anchor not found
  - anchor count != 1 (ambiguous; another hand-edit at this seam)
  - post-patch AST parse fails
"""
from __future__ import annotations

import ast
import shutil
import sys
import time
from pathlib import Path

BASE = Path("/home/azureuser/trading_corp")
TARGET = BASE / "trading_corp/web/data.py"
TAG = f"pre-cutoff-bump-{time.strftime('%Y%m%d-%H%M', time.gmtime())}"

OLD_ANCHOR = (
    'DASHBOARD_RT_CUTOFFS: dict[str, str] = {\n'
    '    "kalshi_weather": "2026-05-16T19:18:00+00:00",  # bucket-guard + date-parse fix\n'
    '    "kalshi_crypto":  "2026-05-16T19:37:00+00:00",  # bucket-guard fix\n'
    '}\n'
)

NEW_CONTENT = (
    'DASHBOARD_RT_CUTOFFS: dict[str, str] = {\n'
    '    # Advanced 2026-05-20 from the 2026-05-16 bucket-guard fix to each\n'
    '    # strategy\'s own logic-change date. Pre-cutoff rows remain queryable\n'
    '    # in `kalshi_round_trips` (forensic + σ-scaling work); they\'re only\n'
    '    # filtered out of dashboard aggregates.\n'
    '    "kalshi_weather": "2026-05-20T11:34:59+00:00",  # entry-price floor live — see deploy_log.md 2026-05-20 11:35 UTC\n'
    '    "kalshi_crypto":  "2026-05-20T05:52:09+00:00",  # vol-v2 + max_divergence_pct live — see deploy_log.md 2026-05-20 05:52 UTC (matches KALSHI_CRYPTO_VOL_V2_CUTOFF in web/kalshi_crypto_vol_v2.py)\n'
    '}\n'
)

IDEMPOTENT_MARKER = '"kalshi_weather": "2026-05-20T11:34:59+00:00"'


def _read(p: Path) -> str:
    return p.read_bytes().decode("utf-8")


def _write(p: Path, src: str) -> None:
    p.write_bytes(src.encode("utf-8"))


def main() -> None:
    print(f"TAG={TAG}")
    src = _read(TARGET)

    if IDEMPOTENT_MARKER in src:
        print(f"  {TARGET.name}: already patched (skipping)")
        return

    n = src.count(OLD_ANCHOR)
    if n == 0:
        sys.exit(f"FAIL: anchor not found in {TARGET.name}")
    if n > 1:
        sys.exit(f"FAIL: anchor appears {n} times in {TARGET.name}; refusing to patch without unique anchor")

    bak = TARGET.with_suffix(TARGET.suffix + f".{TAG}")
    shutil.copy(TARGET, bak)
    print(f"  backup: {bak.name}")

    src = src.replace(OLD_ANCHOR, NEW_CONTENT, 1)
    _write(TARGET, src)

    try:
        ast.parse(_read(TARGET))
    except SyntaxError as e:
        sys.exit(f"FAIL: post-patch syntax error in {TARGET.name}: {e}")

    print(f"  PATCHED: {TARGET.name}")
    print("DONE")


if __name__ == "__main__":
    main()
