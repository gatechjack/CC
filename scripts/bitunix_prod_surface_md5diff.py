"""Pre-flip md5-diff of the full bitunix surface (prod vs git).

P1 pre-deploy gate per architectural-review Finding #7 §6 + readiness-audit
§12. Closes the "deployed but not on git / on git but not deployed" drift
class for the bitunix order-path before the first prod-deploy of `main` that
takes broker-write to prod.

Surface (12 files today; Phase-4 place_order code adds when it exists):

- 10 code files under trading_corp/ (incl. the bitunix_sfp division order-path)
- 2 config files: config/strategies.yaml, config/risk.yaml

Local md5s are LF-normalized before comparison (Windows checkouts are CRLF;
prod is LF for source files and for configs deployed via git-pull; sed-deploys
preserve whatever line-ending was already on disk). Whole-file diff on the
two configs is intentional — non-bitunix-section drift still matters because
the next deploy of main will carry it.

Exit:
    0  all MATCH
    1  any DIFFER (drift detected — investigate before deploy)
    2  any MISSING_LOCAL / MISSING_PROD (manifest error — fix script or
       investigate prod state)

Usage:
    python scripts/bitunix_prod_surface_md5diff.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
PROD_BASE = "/home/azureuser/trading_corp"

MANIFEST: tuple[str, ...] = (
    "trading_corp/brokers/bitunix.py",
    "trading_corp/agents/divisions/bitunix_futures_observer.py",
    "trading_corp/agents/divisions/bitunix_position_reconciler.py",
    "trading_corp/agents/paper_trade_replay.py",
    "trading_corp/agents/strategies/bitunix_confluence.py",
    "trading_corp/agents/strategies/bitunix_pa_validation.py",
    "trading_corp/agents/strategies/bitunix_htf_regime.py",
    "trading_corp/comms/bitunix_lifecycle_notifier.py",
    # bitunix_sfp division (2026-06-28): the live SFP order-path surface, added to
    # the drift gate so this real-money code is no longer ungated.
    "trading_corp/agents/strategies/bitunix_sfp.py",
    "trading_corp/agents/divisions/bitunix_sfp_observer.py",
    "config/strategies.yaml",
    "config/risk.yaml",
)


def local_md5_lf(path: Path) -> str | None:
    """LF-normalized md5 hex digest. Returns None if file is missing."""
    if not path.exists():
        return None
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.md5(data).hexdigest()


def _az_bin() -> str:
    return shutil.which("az") or shutil.which("az.cmd") or "az.cmd"


def _parse_az_message(message: str) -> tuple[str, str]:
    """Extract (stdout, stderr) from az run-command's combined message."""
    if "[stdout]" not in message:
        raise RuntimeError(f"unexpected az output (no [stdout] marker): {message[:300]}")
    after_stdout = message.split("[stdout]", 1)[1]
    if "[stderr]" in after_stdout:
        stdout_part, stderr_part = after_stdout.split("[stderr]", 1)
    else:
        stdout_part, stderr_part = after_stdout, ""
    return stdout_part.strip("\n"), stderr_part.strip("\n")


def parse_md5sum_stdout(stdout: str) -> dict[str, str]:
    """Parse `md5sum` output lines `<hash>  <path>` into {path: hash}."""
    out: dict[str, str] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, path = parts[0], parts[1].lstrip("*")
        if len(digest) == 32 and all(c in "0123456789abcdef" for c in digest):
            out[path] = digest
    return out


def _prod_md5_via_az(rel_paths: Iterable[str]) -> dict[str, str | None]:
    """Single az-vm-run-command call; returns {rel_path: md5 or None}."""
    paths = list(rel_paths)
    quoted = " ".join(f'"{p}"' for p in paths)
    script = f"cd {PROD_BASE} && md5sum {quoted}"
    cmd = [
        _az_bin(), "vm", "run-command", "invoke",
        "-g", "rg-shared-prod", "-n", "tc-prod-vm",
        "--command-id", "RunShellScript",
        "--scripts", script,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, shell=False)
    if r.returncode != 0:
        raise RuntimeError(
            f"az invoke failed (rc={r.returncode}): "
            f"{(r.stderr or r.stdout or '')[:400]}"
        )
    data = json.loads(r.stdout)
    message = data["value"][0]["message"]
    stdout, _stderr = _parse_az_message(message)
    by_path = parse_md5sum_stdout(stdout)
    return {p: by_path.get(p) for p in paths}


def compare(
    manifest: Iterable[str],
    local: dict[str, str | None],
    prod: dict[str, str | None],
) -> list[tuple[str, str, str | None, str | None]]:
    """Per-path status. Returns [(status, path, local_md5, prod_md5), ...]."""
    rows: list[tuple[str, str, str | None, str | None]] = []
    for p in manifest:
        lh = local.get(p)
        ph = prod.get(p)
        if lh is None and ph is None:
            status = "MISSING_BOTH"
        elif lh is None:
            status = "MISSING_LOCAL"
        elif ph is None:
            status = "MISSING_PROD"
        elif lh == ph:
            status = "MATCH"
        else:
            status = "DIFFER"
        rows.append((status, p, lh, ph))
    return rows


def format_report(
    rows: list[tuple[str, str, str | None, str | None]],
) -> tuple[str, int]:
    """Returns (report_text, exit_code)."""
    lines = ["md5-diff bitunix prod surface vs local (LF-normalized)", ""]
    width = max(len(r[1]) for r in rows) if rows else 0
    lines.append(f"{'STATUS':<14}{'PATH':<{width + 2}}")
    lines.append("-" * (14 + width + 2))
    counts: dict[str, int] = {}
    for status, path, lh, ph in rows:
        counts[status] = counts.get(status, 0) + 1
        lines.append(f"{status:<14}{path}")
        if status == "DIFFER":
            lines.append(f"{'':<14}  local {lh}")
            lines.append(f"{'':<14}  prod  {ph}")
        elif status == "MISSING_LOCAL":
            lines.append(f"{'':<14}  prod  {ph}")
        elif status == "MISSING_PROD":
            lines.append(f"{'':<14}  local {lh}")
    lines.append("")
    summary = ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
    lines.append(f"Summary: {summary}")
    has_drift = counts.get("DIFFER", 0) > 0
    has_missing = (
        counts.get("MISSING_LOCAL", 0)
        + counts.get("MISSING_PROD", 0)
        + counts.get("MISSING_BOTH", 0)
    ) > 0
    if has_drift and not has_missing:
        exit_code = 1
        lines.append("Result: DRIFT DETECTED — investigate before deploy.")
    elif has_missing:
        exit_code = 2
        lines.append("Result: MANIFEST ERROR — fix script or investigate prod.")
    else:
        exit_code = 0
        lines.append("Result: clean — prod surface matches git.")
    return "\n".join(lines) + "\n", exit_code


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--manifest-only",
        action="store_true",
        help="print the surface manifest and exit (no az call)",
    )
    args = p.parse_args(argv)
    if args.manifest_only:
        for path in MANIFEST:
            print(path)
        return 0
    local: dict[str, str | None] = {p: local_md5_lf(REPO_ROOT / p) for p in MANIFEST}
    prod = _prod_md5_via_az(MANIFEST)
    rows = compare(MANIFEST, local, prod)
    report, exit_code = format_report(rows)
    sys.stdout.write(report)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
