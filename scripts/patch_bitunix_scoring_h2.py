"""Surgical YAML patcher for the BitUnix H2 scoring re-tune.

Edits `config/strategies.yaml` `bitunix_futures.scoring.factors` block —
11 weight changes per `reports/scoring_recommendation.md` (H2).

  Cap heavy weights at 3:
    mc_a_blood_diamond   5 → 3
    mc_a_red_diamond     4 → 3
    mc_b_gold_buy        5 → 3
    mc_b_buy_circle_div  4 → 3
    mc_b_sell_circle_div 4 → 3
  Up-weight Otter precision family:
    water_buy_large      2 → 3
    water_sell_large     2 → 3
    spoon_bull           2 → 3
    spoon_bear           2 → 3
    money_bag_bottom     2 → 3
    money_bag_top        2 → 3

Each edited weight gets an inline comment `# H2: was N` so the revert
mode can restore deterministically and so a human reading the YAML can
see the original value in place.

Atomic write: writes to .tmp then renames. On apply, also creates a
timestamped backup at `config/strategies.yaml.bak-h2-<UTC>`.

USAGE:
  python scripts/patch_bitunix_scoring_h2.py --dry-run    # print diff, no write
  python scripts/patch_bitunix_scoring_h2.py --apply      # write + backup
  python scripts/patch_bitunix_scoring_h2.py --revert     # restore using # H2: was N markers
"""
from __future__ import annotations

import argparse
import difflib
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
YAML_PATH = REPO_ROOT / "config" / "strategies.yaml"

# Marker substring used to find edits during revert. Keep stable.
MARKER = "# H2: was"

# (factor_name, old_weight, new_weight)
EDITS: list[tuple[str, int, int]] = [
    # Cap heavy weights at 3
    ("mc_a_blood_diamond",   5, 3),
    ("mc_a_red_diamond",     4, 3),
    ("mc_b_gold_buy",        5, 3),
    ("mc_b_buy_circle_div",  4, 3),
    ("mc_b_sell_circle_div", 4, 3),
    # Up-weight Otter precision
    ("water_buy_large",      2, 3),
    ("water_sell_large",     2, 3),
    ("spoon_bull",           2, 3),
    ("spoon_bear",           2, 3),
    ("money_bag_bottom",     2, 3),
    ("money_bag_top",        2, 3),
]


def _patch_multiline(text: str, factor: str, old_w: int, new_w: int) -> tuple[str, bool]:
    """Multi-line block format:
        mc_a_blood_diamond:
          weight: 5
          ...
    Become:
        mc_a_blood_diamond:
          weight: 3    # H2: was 5
          ...
    """
    pat = re.compile(
        rf"(^(?P<indent>[ \t]+){re.escape(factor)}:\s*\n"
        rf"(?P=indent)[ \t]+weight:\s*){old_w}(?P<tail>\s*\n)",
        flags=re.MULTILINE,
    )
    m = pat.search(text)
    if not m:
        return text, False
    repl = f"{m.group(1)}{new_w}    {MARKER} {old_w}\n"
    return text[:m.start()] + repl + text[m.end():], True


def _patch_inline(text: str, factor: str, old_w: int, new_w: int) -> tuple[str, bool]:
    """Inline flow-style format:
        water_buy_large:       {weight: 2, side: buy,  ttl_minutes: 30}
    Becomes:
        water_buy_large:       {weight: 3, side: buy,  ttl_minutes: 30}  # H2: was 2
    """
    pat = re.compile(
        rf"(^[ \t]+{re.escape(factor)}:\s*\{{weight:\s*){old_w}"
        rf"(,\s*side:[^}}]*\}})(?P<tail>[^\n]*)$",
        flags=re.MULTILINE,
    )
    m = pat.search(text)
    if not m:
        return text, False
    existing_tail = m.group("tail")
    # Avoid double-tagging if a revert-then-reapply happens.
    if MARKER in existing_tail:
        existing_tail = re.sub(rf"\s*{re.escape(MARKER)}\s*\d+\s*", "", existing_tail)
    repl = f"{m.group(1)}{new_w}{m.group(2)}{existing_tail}  {MARKER} {old_w}"
    return text[:m.start()] + repl + text[m.end():], True


def apply_h2_edits(text: str) -> tuple[str, list[str]]:
    """Apply all 11 edits. Returns (new_text, list_of_failures).
    A failure means the regex didn't match — either already-patched or
    the YAML format changed; either way operator should investigate.
    """
    failures: list[str] = []
    new_text = text
    for factor, old_w, new_w in EDITS:
        # Try multi-line first, then inline.
        patched_text, ok = _patch_multiline(new_text, factor, old_w, new_w)
        if not ok:
            patched_text, ok = _patch_inline(new_text, factor, old_w, new_w)
        if not ok:
            failures.append(factor)
            continue
        new_text = patched_text
    return new_text, failures


def revert_h2_edits(text: str) -> tuple[str, int]:
    """Undo any H2: was N markers found in the file. Returns (new_text, n_reverted)."""
    n = 0

    # Multi-line: `weight: NEW    # H2: was OLD\n`  →  `weight: OLD\n`
    def _ml_repl(m: re.Match) -> str:
        nonlocal n
        n += 1
        return f"{m.group('lead')}{m.group('old')}\n"

    text = re.sub(
        rf"(?P<lead>^[ \t]+weight:\s*)\d+\s+{re.escape(MARKER)}\s+(?P<old>\d+)\s*\n",
        _ml_repl, text, flags=re.MULTILINE,
    )

    # Inline: `{weight: NEW, ... } ... # H2: was OLD` → `{weight: OLD, ... }`
    def _il_repl(m: re.Match) -> str:
        nonlocal n
        n += 1
        old = m.group("old")
        return f"{m.group('head')}{old}{m.group('rest')}"

    text = re.sub(
        rf"(?P<head>^[ \t]+\w+:\s*\{{weight:\s*)\d+(?P<rest>,\s*side:[^}}]*\}})"
        rf"[^\n]*?{re.escape(MARKER)}\s+(?P<old>\d+)\s*$",
        _il_repl, text, flags=re.MULTILINE,
    )
    return text, n


def _validate_parses(text: str) -> tuple[bool, str | None]:
    try:
        cfg = yaml.safe_load(text)
        # Spot-check: required structure still exists
        cfg["bitunix_futures"]["scoring"]["factors"]
        return True, None
    except Exception as e:
        return False, str(e)


def _validate_post_apply_weights(text: str) -> list[str]:
    """Parse the patched YAML; confirm every edit landed at the new weight.
    Returns a list of mismatch messages (empty = all good)."""
    cfg = yaml.safe_load(text)
    factors = cfg["bitunix_futures"]["scoring"]["factors"]
    mismatches: list[str] = []
    for factor, _old, new_w in EDITS:
        actual = factors.get(factor, {}).get("weight")
        if actual != new_w:
            mismatches.append(f"  {factor}: expected weight={new_w}, got {actual!r}")
    return mismatches


def _show_diff(original: str, patched: str) -> None:
    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        patched.splitlines(keepends=True),
        fromfile="config/strategies.yaml (current)",
        tofile="config/strategies.yaml (after H2)",
        n=1,
    )
    sys.stdout.write("".join(diff))


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def cmd_dry_run() -> int:
    original = YAML_PATH.read_text(encoding="utf-8")
    patched, failures = apply_h2_edits(original)
    if failures:
        print(f"⚠ {len(failures)} factor(s) NOT patched (regex didn't match — already patched, or YAML format changed):", file=sys.stderr)
        for f in failures:
            print(f"    {f}", file=sys.stderr)
    ok, err = _validate_parses(patched)
    if not ok:
        print(f"FATAL: patched YAML does not parse: {err}", file=sys.stderr)
        return 2
    mismatches = _validate_post_apply_weights(patched)
    if mismatches:
        print(f"⚠ post-apply weight check found {len(mismatches)} mismatch(es):", file=sys.stderr)
        for m in mismatches:
            print(m, file=sys.stderr)
        return 2
    print("=== DRY RUN — proposed diff ===\n")
    _show_diff(original, patched)
    print(f"\n=== Summary: {len(EDITS) - len(failures)}/{len(EDITS)} edits would apply ===")
    print("Run with --apply to commit. Run with --revert to undo after apply.")
    return 0 if not failures else 1


def cmd_apply() -> int:
    original = YAML_PATH.read_text(encoding="utf-8")
    patched, failures = apply_h2_edits(original)
    if failures:
        print(f"FATAL: {len(failures)} factor(s) failed to patch:", file=sys.stderr)
        for f in failures:
            print(f"    {f}", file=sys.stderr)
        print("Refusing to write a partial patch. Investigate and re-run.", file=sys.stderr)
        return 2
    ok, err = _validate_parses(patched)
    if not ok:
        print(f"FATAL: patched YAML does not parse: {err}", file=sys.stderr)
        return 2
    mismatches = _validate_post_apply_weights(patched)
    if mismatches:
        print("FATAL: post-apply weight check failed:", file=sys.stderr)
        for m in mismatches:
            print(m, file=sys.stderr)
        return 2

    # Backup
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    bak = YAML_PATH.with_suffix(f".yaml.bak-h2-{ts}")
    shutil.copy2(YAML_PATH, bak)
    print(f"backed up current YAML → {bak}")

    _atomic_write(YAML_PATH, patched)
    print(f"applied {len(EDITS)} H2 weight edits to {YAML_PATH}")
    print("see `reports/scoring_recommendation.md` for rationale.")
    print("revert with: python scripts/patch_bitunix_scoring_h2.py --revert")
    return 0


def cmd_revert() -> int:
    original = YAML_PATH.read_text(encoding="utf-8")
    if MARKER not in original:
        print(f"no '{MARKER}' markers found — nothing to revert.", file=sys.stderr)
        return 1
    reverted, n = revert_h2_edits(original)
    ok, err = _validate_parses(reverted)
    if not ok:
        print(f"FATAL: reverted YAML does not parse: {err}", file=sys.stderr)
        return 2

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    bak = YAML_PATH.with_suffix(f".yaml.bak-revert-{ts}")
    shutil.copy2(YAML_PATH, bak)
    print(f"backed up current YAML → {bak}")

    _atomic_write(YAML_PATH, reverted)
    print(f"reverted {n} H2 edit(s) from {YAML_PATH}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="print diff, no write")
    g.add_argument("--apply", action="store_true", help="write changes (with backup)")
    g.add_argument("--revert", action="store_true",
                    help=f"undo H2 edits using '{MARKER}' inline markers")
    args = ap.parse_args()
    if args.dry_run:
        return cmd_dry_run()
    if args.apply:
        return cmd_apply()
    if args.revert:
        return cmd_revert()
    return 1


if __name__ == "__main__":
    sys.exit(main())
