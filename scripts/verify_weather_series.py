#!/usr/bin/env python3
"""Flip a single Kalshi weather series from ``verified: false`` to
``verified: true`` in ``config/weather_stations.yaml``.

ONE series at a time, on purpose: ``verified: true`` is the marker that
a human personally checked the ``rules_excerpt`` against the cited
settlement station. Bulk operations defeat the audit value of the flag.

The helper:
- refuses to flip a series that is already verified;
- refuses to flip a disabled series (KXTEMPNYCH);
- prints the YAML diff before writing;
- requires explicit confirmation (or ``--yes``) before writing;
- never deploys, never commits, never pushes — you do that step.

Usage::

    python scripts/verify_weather_series.py KXHIGHTSEA \\
        --via KXHIGHTSEA-26MAY22-T72 \\
        --user jack

    # then review and commit yourself:
    git diff config/weather_stations.yaml
    git add config/weather_stations.yaml
    git commit -m "weather_stations: verify KXHIGHTSEA"

The ``--via`` argument is the specific Kalshi ticker whose
``rules_primary`` you actually read. It is stored in
``verified_via_market`` for traceability — a reviewer can pull that
ticker's rules and re-verify the mapping.
"""
from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

YAML_PATH = Path("config/weather_stations.yaml")
SERIES_HEADER_INDENT = "  "  # 2 spaces — under ``series:``
FIELD_INDENT = "    "  # 4 spaces — fields inside a series block
VERIFIED_FALSE_LINE = f"{FIELD_INDENT}verified: false\n"


def find_series_block(lines: list[str], series: str) -> tuple[int, int]:
    """Return (start_idx, end_idx_exclusive) of the series block.

    Block is the lines from ``  SERIES:`` (inclusive) up to but not
    including the next series header at the same indent or EOF.
    """
    header = f"{SERIES_HEADER_INDENT}{series}:\n"
    try:
        start = lines.index(header)
    except ValueError:
        sys.exit(f"ERROR: series {series!r} not found in {YAML_PATH}")
    # Find next line that starts with 2 spaces + non-space and ends with ":"
    end = len(lines)
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if (
            line.startswith(SERIES_HEADER_INDENT)
            and not line.startswith(FIELD_INDENT)
            and line.rstrip().endswith(":")
            and line[len(SERIES_HEADER_INDENT)] != " "
        ):
            end = i
            break
    return start, end


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("series", help="Series prefix, e.g. KXHIGHTSEA")
    p.add_argument(
        "--via",
        required=True,
        help="Specific Kalshi ticker whose rules_primary you read "
        "(e.g. KXHIGHTSEA-26MAY22-T72). Recorded as verified_via_market.",
    )
    p.add_argument(
        "--user",
        default="jack",
        help="Username recorded as verified_by (default: jack).",
    )
    p.add_argument(
        "--date",
        default=None,
        help="ISO date for verified_at (default: today UTC).",
    )
    p.add_argument(
        "--yaml-path",
        default=str(YAML_PATH),
        help=f"Override YAML path (default: {YAML_PATH}).",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt.",
    )
    args = p.parse_args()

    yaml_path = Path(args.yaml_path)
    if not yaml_path.exists():
        sys.exit(f"ERROR: {yaml_path} does not exist")

    date_str = args.date or datetime.date.today().isoformat()

    text = yaml_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    start, end = find_series_block(lines, args.series)
    block = lines[start:end]
    block_str = "".join(block)

    # Refuse if already verified
    if any(line.strip() == "verified: true" for line in block):
        sys.exit(
            f"ERROR: series {args.series!r} is already verified. "
            "Refusing to re-flip. If you need to update verified_by/at, "
            "edit the YAML by hand."
        )
    # Refuse disabled series
    if any(line.strip() == "disabled: true" for line in block):
        sys.exit(
            f"ERROR: series {args.series!r} is disabled "
            "(refuse to model — see disabled_reason). Cannot mark verified."
        )
    # Locate the `verified: false` line
    vline_idx_in_block = None
    for i, line in enumerate(block):
        if line == VERIFIED_FALSE_LINE:
            vline_idx_in_block = i
            break
    if vline_idx_in_block is None:
        sys.exit(
            f"ERROR: could not find {VERIFIED_FALSE_LINE!r} in the {args.series!r} "
            "block. The YAML structure may have changed; investigate before retrying."
        )

    # Build replacement: keep the verified line; add 3 metadata fields immediately after
    replacement = (
        f"{FIELD_INDENT}verified: true\n"
        f"{FIELD_INDENT}verified_by: {args.user}\n"
        f"{FIELD_INDENT}verified_at: '{date_str}'\n"
        f"{FIELD_INDENT}verified_via_market: {args.via}\n"
    )
    new_block = list(block)
    new_block[vline_idx_in_block] = replacement
    new_block_str = "".join(new_block)

    # Diff print
    print(f"=== Pending verification: {args.series} ===\n")
    print("--- block before ---")
    print(block_str.rstrip("\n"))
    print("\n--- block after ---")
    print(new_block_str.rstrip("\n"))
    print()

    if not args.yes:
        resp = input("Apply this change? [y/N]: ").strip().lower()
        if resp not in ("y", "yes"):
            print("Aborted. No changes written.")
            return 1

    # Write back
    new_lines = lines[:start] + new_block + lines[end:]
    yaml_path.write_text("".join(new_lines), encoding="utf-8")

    print(f"\nOK — {args.series} flipped to verified=true in {yaml_path}.")
    print("\nNext steps:")
    print(f"  git diff {yaml_path}")
    print(f"  git add {yaml_path}")
    print(f"  git commit -m 'weather_stations: verify {args.series}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
