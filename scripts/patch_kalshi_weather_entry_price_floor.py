"""Surgical prod patcher: side-asymmetric entry-price floor for kalshi_weather_arb.

What this does (three independent surgeries, each idempotent):
  1. trading_corp/agents/strategies/_weather_math.py
     Append `apply_entry_price_floor` (NEW pure helper) to the end of the
     module. Anchor: the trailing `return BucketGuardResult(...)` of the
     existing `apply_bucket_guard` function.
  2. trading_corp/agents/strategies/kalshi_weather_arb.py
     (a) Add `apply_entry_price_floor,` to the _weather_math import block,
         alphabetically between `apply_bucket_guard,` and
         `evaluate_weather_market,`.
     (b) Insert the floor-call block between the `share_price` out-of-range
         skip and the `# ── Sizing: fractional Kelly` comment.
  3. config/strategies.yaml
     Insert 8 lines (6 comments + min_yes_entry + min_no_entry) into the
     `kalshi_weather_arb:` block, between the `max_horizon_hours: 72` line
     and the `# ── Tier-1 upgrades (2026-05-15)` comment.

Anchor verification (2026-05-20):
  - Prod md5 of _weather_math.py = `007790327b43c74f1048276fe7108947` (byte-
    identical to local HEAD `504c992`). Append anchor verified.
  - Prod md5 of kalshi_weather_arb.py = `4bf3005a0f638dae4c0c73d5dd296a09`
    (byte-identical to local HEAD). Both insert anchors verified.
  - Prod yaml has drift on `sizing.max_per_day_pct: 120.0` (vs local 25.0;
    hot-patched 2026-05-15, backport pending). This patcher deliberately
    does NOT touch that line so the hot-patch is preserved.

Idempotent: re-running detects per-file markers (`apply_entry_price_floor`
in source, `min_yes_entry: 0.10` in yaml) and skips already-patched files.

Refuses to run if:
  - any anchor block is missing from a file
  - any anchor appears more than once (surgical patch needs unique anchor)
  - the post-patch .py source fails `ast.parse`

Companion to scripts/patch_kalshi_weather_kelly_sizing.py — same pattern.
Same operator usage:
    ssh azureuser@trading.jacksumner.com
    cd ~/trading_corp
    scp this script over, then: python3 scripts/patch_kalshi_weather_entry_price_floor.py

After this patcher runs and exits clean: `sudo systemctl restart trading-corp`.
"""
from __future__ import annotations

import ast
import shutil
import sys
import time
from pathlib import Path

BASE = Path("/home/azureuser/trading_corp")
TAG = f"pre-floor-{time.strftime('%Y%m%d-%H%M', time.gmtime())}"


# ─── _weather_math.py: append apply_entry_price_floor ─────────────────────────

MATH_MARKER = "def apply_entry_price_floor("

MATH_ANCHOR = (
    "\n    return BucketGuardResult(outcome=proposed_outcome, action=None, skip_reason=None)\n"
)

MATH_APPEND = (
    "\n    return BucketGuardResult(outcome=proposed_outcome, action=None, skip_reason=None)\n"
    "\n"
    "\n"
    "def apply_entry_price_floor(\n"
    "    *,\n"
    "    outcome: str,\n"
    "    share_price: float,\n"
    "    min_yes_entry: float = 0.10,\n"
    "    min_no_entry: float = 0.50,\n"
    ") -> str | None:\n"
    '    """Side-specific cheap-tail skip.\n'
    "\n"
    "    Comparator asymmetry by design:\n"
    "      - YES: skip when share_price <= min_yes_entry  (inclusive)\n"
    "      - NO:  skip when share_price <  min_no_entry   (strict)\n"
    "\n"
    "    The NO comparator stays strict so $0.50 itself aligns with the live\n"
    "    [0.50, 0.60) entry-price band used in the post-cutoff RT analysis,\n"
    "    rather than being suppressed at the boundary. YES stays inclusive\n"
    "    because the cheap-YES floor sits in a region where no trades have\n"
    "    been observed at all in the post-cutoff window.\n"
    "\n"
    "    Backed by post-cutoff round-trip data (2026-05-16T19:18Z onward):\n"
    "    YES entries <= $0.10 went 0/5 (-$37.50); NO entries < $0.50 went 0/5\n"
    "    (-$37.50). Cheap-tail bets sized to fixed notional lose the full\n"
    "    stake on every miss; with zero wins observed in either bucket, EV is\n"
    "    negative regardless of model edge.\n"
    "\n"
    "    Returns a skip_reason string when the price triggers the floor; None\n"
    "    means proceed to sizing.\n"
    '    """\n'
    '    if outcome == "yes" and share_price <= min_yes_entry:\n'
    '        return f"entry_below_floor: yes {share_price:.3f} <= {min_yes_entry:.2f}"\n'
    '    if outcome == "no" and share_price < min_no_entry:\n'
    '        return f"entry_below_floor: no {share_price:.3f} < {min_no_entry:.2f}"\n'
    "    return None\n"
)


# ─── kalshi_weather_arb.py: import + call-site insertion ─────────────────────

ARB_IMPORT_ANCHOR = "    apply_bucket_guard,\n    evaluate_weather_market,\n"
ARB_IMPORT_NEW = (
    "    apply_bucket_guard,\n"
    "    apply_entry_price_floor,\n"
    "    evaluate_weather_market,\n"
)

ARB_CALL_ANCHOR = (
    '            return verdict, None, {"code": "no_edge", **eval_payload}, eval_payload\n'
    "\n"
    "        # ── Sizing: fractional Kelly with per-market / day / city caps ────\n"
)
ARB_CALL_NEW = (
    '            return verdict, None, {"code": "no_edge", **eval_payload}, eval_payload\n'
    "\n"
    "        # ── Entry-price floor (config-driven; cheap-tail skip) ─────────\n"
    "        # See _weather_math.apply_entry_price_floor for the data motivating\n"
    "        # the side-specific defaults and the YES-inclusive / NO-strict\n"
    "        # comparator asymmetry. Skips here become `entry_below_floor`\n"
    "        # audit rows so suppression rate stays observable.\n"
    "        floor_skip = apply_entry_price_floor(\n"
    "            outcome=outcome,\n"
    "            share_price=share_price,\n"
    '            min_yes_entry=float(self._strat_cfg.get("min_yes_entry", 0.10)),\n'
    '            min_no_entry=float(self._strat_cfg.get("min_no_entry", 0.50)),\n'
    "        )\n"
    "        if floor_skip is not None:\n"
    '            eval_payload["skip_reason"] = floor_skip\n'
    '            eval_payload["fired"] = False\n'
    '            return verdict, None, {"code": "entry_below_floor", **eval_payload}, eval_payload\n'
    "\n"
    "        # ── Sizing: fractional Kelly with per-market / day / city caps ────\n"
)


# ─── config/strategies.yaml: floor lines insertion ───────────────────────────

YAML_ANCHOR = (
    "  max_horizon_hours: 72             # NWS forecast precision degrades past 72h\n"
    "  # ── Tier-1 upgrades (2026-05-15) ──────────────────────────────\n"
)
YAML_NEW = (
    "  max_horizon_hours: 72             # NWS forecast precision degrades past 72h\n"
    "  # ── Entry-price floor (2026-05-20) ─────────────────────────────────\n"
    "  # Skip proposals where the chosen-outcome ask is at or below the side\n"
    "  # floor. Post-cutoff data: YES <= $0.10 went 0/5 (-$37.50); NO < $0.50\n"
    "  # went 0/5 (-$37.50). YES comparator is inclusive (no observed trades\n"
    "  # at the boundary); NO comparator is strict so $0.50 itself stays in\n"
    "  # the live [0.50, 0.60) band rather than being suppressed.\n"
    "  min_yes_entry: 0.10\n"
    "  min_no_entry: 0.50\n"
    "  # ── Tier-1 upgrades (2026-05-15) ──────────────────────────────\n"
)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _read(p: Path) -> str:
    # read_bytes + decode bypasses Python's universal-newlines translation so
    # the CRLF/LF byte sequences round-trip unchanged. Portable to Python
    # 3.10 (prod) — Path.read_text only gained `newline=` in 3.13.
    return p.read_bytes().decode("utf-8")


def _write(p: Path, src: str) -> None:
    p.write_bytes(src.encode("utf-8"))


def _backup(p: Path) -> None:
    bak = p.with_suffix(p.suffix + f".{TAG}")
    shutil.copy(p, bak)
    print(f"  backup: {bak.name}")


def _check_unique_anchor(src: str, anchor: str, fname: str, label: str) -> None:
    n = src.count(anchor)
    if n == 0:
        sys.exit(f"FAIL: anchor '{label}' not found in {fname}")
    if n > 1:
        sys.exit(f"FAIL: anchor '{label}' appears {n} times in {fname}; refusing to patch without unique anchor")


def _ast_check(p: Path) -> None:
    try:
        ast.parse(_read(p))
    except SyntaxError as e:
        sys.exit(f"FAIL: post-patch syntax error in {p.name}: {e}")


# ─── per-file patchers ───────────────────────────────────────────────────────

def patch_weather_math() -> None:
    p = BASE / "trading_corp/agents/strategies/_weather_math.py"
    src = _read(p)
    if MATH_MARKER in src:
        print(f"  {p.name}: already patched (skipping)")
        return
    _check_unique_anchor(src, MATH_ANCHOR, p.name, "trailing return of apply_bucket_guard")
    _backup(p)
    src = src.replace(MATH_ANCHOR, MATH_APPEND, 1)
    _write(p, src)
    _ast_check(p)
    print(f"  PATCHED: {p.name}")


def patch_weather_arb() -> None:
    p = BASE / "trading_corp/agents/strategies/kalshi_weather_arb.py"
    src = _read(p)
    already_imported = "apply_entry_price_floor" in src
    already_called = "code\": \"entry_below_floor" in src
    if already_imported and already_called:
        print(f"  {p.name}: already patched (skipping)")
        return
    if already_imported ^ already_called:
        sys.exit(f"FAIL: {p.name} is half-patched (import={already_imported}, call={already_called}); manual inspection required")
    _check_unique_anchor(src, ARB_IMPORT_ANCHOR, p.name, "_weather_math import block")
    _check_unique_anchor(src, ARB_CALL_ANCHOR, p.name, "share_price no_edge → Kelly sizing seam")
    _backup(p)
    src = src.replace(ARB_IMPORT_ANCHOR, ARB_IMPORT_NEW, 1)
    src = src.replace(ARB_CALL_ANCHOR, ARB_CALL_NEW, 1)
    _write(p, src)
    _ast_check(p)
    print(f"  PATCHED: {p.name}")


def patch_strategies_yaml() -> None:
    p = BASE / "config/strategies.yaml"
    src = _read(p)
    if "min_yes_entry: 0.10" in src and "min_no_entry: 0.50" in src:
        print(f"  {p.name}: already patched (skipping)")
        return
    _check_unique_anchor(src, YAML_ANCHOR, p.name, "kalshi_weather_arb: max_horizon_hours → Tier-1 seam")
    _backup(p)
    src = src.replace(YAML_ANCHOR, YAML_NEW, 1)
    _write(p, src)
    print(f"  PATCHED: {p.name}")


# ─── entry point ─────────────────────────────────────────────────────────────

def main() -> None:
    print(f"TAG={TAG}")
    patch_weather_math()
    patch_weather_arb()
    patch_strategies_yaml()
    print("DONE")


if __name__ == "__main__":
    main()
