"""File-level md5 sweep of trading_corp/ + config/ between prod and origin/main.

Generalizes ``scripts/bitunix_prod_surface_md5diff.py`` (10-file fixed manifest)
to the full transferable surface. Catches the prod-vs-main filesystem drift
class that rolled back the 2026-05-30 22:43 UTC redeploy — see
``[[deploy-transfer-set-diff-derived-misses-stale-prod-files]]``.

How it works
------------
1. Enumerate every git-tracked file under ``trading_corp/`` + ``config/`` on
   ``origin/main``. Compute the LF-normalized md5 of each via working-tree
   read (worktree is assumed clean against origin/main; pass ``--head HEAD``
   to override).
2. Embed that expected-md5 dict into a python script that runs on prod.
3. Single bundled ``az vm run-command`` call. The prod-side script:
   - For each expected file: computes actual md5, classifies MATCH /
     MISMATCH / MISSING_ON_PROD.
   - Walks prod's filesystem under the same subdirs to find PROD-ONLY files
     not in the expected list (uncommitted additions). Filters out
     ``__pycache__``, ``*.pyc``, and known backup-suffix files
     (``*.pre-stage1-*``).
   - Emits summary counts + per-finding lines. Output is small (typically
     << 1KB) so it fits comfortably under the 4KB az stdout tail-truncation
     cap from ``[[reference-az-run-command-stdout-cap]]``.
4. Locally: parse the output, classify mismatches against the known-overlay
   whitelist, render a report.

Classifications per file
------------------------
- MATCH                          prod md5 == origin/main LF md5
- DIFFER-EXPECTED-PER-DEPLOY-LOG known sed-overlay (whitelist below)
- DIFFER-STALE-ON-PROD           prod differs, not in whitelist (stale or
                                 forgotten overlay — flag for next transfer)
- MISSING_ON_PROD                file in main's tree, not on prod's disk
- PROD_ONLY_NOT_ON_MAIN          file on prod, not in main's tree

Exit code
---------
- 0 = clean (all MATCH or only DIFFER-EXPECTED entries)
- 1 = drift detected (any DIFFER-STALE-ON-PROD / MISSING_ON_PROD /
      PROD_ONLY_NOT_ON_MAIN)
- 2 = az probe failed

Usage
-----
::

    python scripts/prod_vs_main_file_level_md5_sweep.py
    python scripts/prod_vs_main_file_level_md5_sweep.py --report path/to/out.md
    python scripts/prod_vs_main_file_level_md5_sweep.py --manifest-only
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
PROD_BASE = "/home/azureuser/trading_corp"
SUBDIRS = ("trading_corp", "config")


# Known sed-overlays where prod intentionally diverges from origin/main.
# Each entry: rel_path -> (deploy_log_reference, short_reason).
# Anything not in this dict that DIFFERs is treated as DIFFER-STALE-ON-PROD.
KNOWN_OVERLAYS: dict[str, tuple[str, str]] = {
    "config/strategies.yaml": (
        "deploy_log.md 2026-05-30 03:57 UTC (branch bitunix-risk-tier-pre-live, NOT merged)",
        "BitUnix paper-mode tier sizing aligned with intended live values "
        "(PREMIUM 0.04/8x → 0.015/25x, STANDARD 0.02/5x → 0.0075/25x). "
        "Sed-overlay on prod; main carries the old values. Future deploy of "
        "main without re-applying the sed would silently revert sizing.",
    ),
}


@dataclasses.dataclass(frozen=True)
class SweepFinding:
    """A single per-file classification result."""

    status: str  # MATCH / DIFFER-EXPECTED-PER-DEPLOY-LOG / DIFFER-STALE-ON-PROD
    #            / MISSING_ON_PROD / PROD_ONLY_NOT_ON_MAIN
    path: str
    expected_md5: str | None
    actual_md5: str | None
    overlay_ref: str | None = None
    overlay_reason: str | None = None


# ──────────────────────────────────────────────────────────────────────
# Local enumeration: git-tracked files under SUBDIRS, LF-normalized md5
# ──────────────────────────────────────────────────────────────────────


def list_main_surface_files(ref: str = "HEAD") -> list[str]:
    """Return git-tracked rel-paths under SUBDIRS at `ref`, sorted."""
    rc = subprocess.run(
        ["git", "ls-tree", "-r", ref, "--name-only"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    paths = [
        line.strip()
        for line in rc.stdout.splitlines()
        if line.strip()
        and any(line.startswith(f"{d}/") or line == f"{d}" for d in SUBDIRS)
    ]
    return sorted(paths)


def local_md5_lf(rel_path: str) -> str | None:
    """LF-normalized md5 of working-tree file at rel_path. None if missing."""
    abs_path = REPO_ROOT / rel_path
    if not abs_path.is_file():
        return None
    data = abs_path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.md5(data).hexdigest()


def build_expected_md5s(paths: Iterable[str]) -> dict[str, str]:
    """Map every rel-path to its LF-normalized md5. Skips missing locally."""
    out: dict[str, str] = {}
    for p in paths:
        h = local_md5_lf(p)
        if h is not None:
            out[p] = h
    return out


# ──────────────────────────────────────────────────────────────────────
# Prod-side script: compare embedded expected vs actual, emit only diffs
# ──────────────────────────────────────────────────────────────────────


SWEEP_BEGIN_MARKER = "SWEEP_BEGIN_v1"
SWEEP_END_MARKER = "SWEEP_END_v1"


def build_prod_compare_script(expected: dict[str, str]) -> str:
    """Build a bash+python3 script string that runs on prod via az.

    The prod script collects the comparison output, gzip-compresses it, and
    base64-encodes it on a single line bracketed by SWEEP_BEGIN_v1 /
    SWEEP_END_v1 sentinels. Both compression and the explicit end-marker
    make the output deterministic under the 4KB stdout cap from
    ``[[reference-az-run-command-stdout-cap]]``:

    - Compression: ~5KB body + summary text → ~2KB base64'd → fits in 4KB cap.
    - End marker: locally we verify the marker is present; missing marker
      means truncation occurred and the result is INCOMPLETE.
    - Trailer carries ``expected_count`` so we can also verify all expected
      files were processed.

    PROD_ONLY filtering: skips ``__pycache__`` dirs, ``.pyc/.pyo`` files, and
    ``.pre-*`` deploy-backup suffixes.
    """
    expected_json = json.dumps(expected, separators=(",", ":"))
    return f"""set -eu
cd {PROD_BASE}
python3 - <<'PYEOF'
import base64
import gzip
import hashlib
import io
import json
import os
import re
import sys
EXPECTED = json.loads({expected_json!r})
ROOT = {PROD_BASE!r}
SUBDIRS = {list(SUBDIRS)!r}

matched = 0
mismatched = []
missing_on_prod = []
extra_on_prod = []

# Pass 1: every expected file
for rel_path, exp_md5 in EXPECTED.items():
    abs_path = os.path.join(ROOT, rel_path)
    if not os.path.exists(abs_path):
        missing_on_prod.append(rel_path)
        continue
    try:
        with open(abs_path, 'rb') as f:
            actual = hashlib.md5(f.read()).hexdigest()
    except OSError as e:
        mismatched.append((rel_path, "ERROR:" + type(e).__name__))
        continue
    if actual == exp_md5:
        matched += 1
    else:
        mismatched.append((rel_path, actual))

# Pass 2: filesystem walk for PROD_ONLY files
PRE_BACKUP_RE = re.compile(r'\\.pre-[A-Za-z0-9_]')
SKIP_SUFFIXES = ('.pyc', '.pyo')
SKIP_DIR_PARTS = ('__pycache__', '.git', 'data')
for subdir in SUBDIRS:
    for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, subdir)):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_PARTS]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, ROOT)
            if any(rel.endswith(s) for s in SKIP_SUFFIXES):
                continue
            if any(part in SKIP_DIR_PARTS for part in rel.split(os.sep)):
                continue
            if PRE_BACKUP_RE.search(rel):
                continue
            if rel not in EXPECTED:
                extra_on_prod.append(rel)

# Build the output as a string in memory
buf = io.StringIO()
for rel, actual in sorted(mismatched):
    buf.write("MISMATCH " + rel + " " + actual + "\\n")
for rel in sorted(missing_on_prod):
    buf.write("MISSING_ON_PROD " + rel + "\\n")
for rel in sorted(extra_on_prod):
    buf.write("EXTRA_ON_PROD " + rel + "\\n")
buf.write("SUMMARY_MATCH_COUNT " + str(matched) + "\\n")
buf.write("SUMMARY_MISMATCH_COUNT " + str(len(mismatched)) + "\\n")
buf.write("SUMMARY_MISSING_ON_PROD_COUNT " + str(len(missing_on_prod)) + "\\n")
buf.write("SUMMARY_EXTRA_ON_PROD_COUNT " + str(len(extra_on_prod)) + "\\n")
raw_text = buf.getvalue()

# Compress + base64
compressed = gzip.compress(raw_text.encode("utf-8"))
b64 = base64.b64encode(compressed).decode("ascii")
expected_count = len(EXPECTED)
print({SWEEP_BEGIN_MARKER!r})
print(b64)
print(f"{SWEEP_END_MARKER} expected_count={{expected_count}} payload_len={{len(b64)}}")
PYEOF
"""


# ──────────────────────────────────────────────────────────────────────
# az invocation + output parsing
# ──────────────────────────────────────────────────────────────────────


def _az_bin() -> str:
    return shutil.which("az") or shutil.which("az.cmd") or "az.cmd"


def _parse_az_message(message: str) -> tuple[str, str]:
    """Extract (stdout, stderr) from az run-command's combined message."""
    if "[stdout]" not in message:
        raise RuntimeError(
            f"unexpected az output (no [stdout] marker): {message[:300]}"
        )
    after_stdout = message.split("[stdout]", 1)[1]
    if "[stderr]" in after_stdout:
        stdout_part, stderr_part = after_stdout.split("[stderr]", 1)
    else:
        stdout_part, stderr_part = after_stdout, ""
    return stdout_part.strip("\n"), stderr_part.strip("\n")


def decode_prod_payload(stdout: str) -> tuple[str, int, int]:
    """Extract & decode the gzip+base64 payload from prod stdout.

    Returns (decoded_text, expected_count, payload_len).

    Raises RuntimeError if SWEEP_BEGIN_v1 or SWEEP_END_v1 markers are missing —
    that indicates az stdout was truncated and the result is INCOMPLETE.
    """
    import base64
    import gzip

    lines = stdout.splitlines()
    begin_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        if line.strip() == SWEEP_BEGIN_MARKER:
            begin_idx = i
        elif line.strip().startswith(SWEEP_END_MARKER):
            end_idx = i
    if begin_idx is None:
        raise RuntimeError(
            f"SWEEP_BEGIN_v1 marker missing — output truncated or prod script "
            f"failed. First 400 chars of stdout: {stdout[:400]}"
        )
    if end_idx is None:
        raise RuntimeError(
            "SWEEP_END_v1 marker missing — output was tail-truncated before "
            "the trailer (prod stdout exceeded the az cap). Reduce payload "
            "or chunk the retrieval. INCOMPLETE result; do not trust counts."
        )
    if end_idx <= begin_idx:
        raise RuntimeError(
            f"SWEEP markers out of order (begin={begin_idx}, end={end_idx})"
        )
    # Parse the trailer for expected_count and payload_len
    trailer = lines[end_idx]
    expected_count = 0
    payload_len = 0
    for token in trailer.split():
        if "=" in token:
            k, v = token.split("=", 1)
            if k == "expected_count":
                expected_count = int(v)
            elif k == "payload_len":
                payload_len = int(v)
    b64_lines = lines[begin_idx + 1 : end_idx]
    b64_blob = "".join(line.strip() for line in b64_lines)
    if payload_len and len(b64_blob) != payload_len:
        raise RuntimeError(
            f"payload length mismatch — trailer says {payload_len}, got "
            f"{len(b64_blob)} bytes. Truncation between markers."
        )
    compressed = base64.b64decode(b64_blob)
    decoded = gzip.decompress(compressed).decode("utf-8")
    return decoded, expected_count, payload_len


def run_prod_probe(script: str) -> str:
    """Single bundled az run-command call; return the decoded prod payload.

    Writes the script to a temp file and passes ``--scripts @path`` to az
    because Windows CMD has an 8KB command-line limit and our script with
    embedded md5s exceeds that.

    The returned text is the DECODED prod output (after gzip+base64 → text),
    ready for ``parse_prod_output``.
    """
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".sh", encoding="utf-8", delete=False, newline="\n"
    ) as tf:
        tf.write(script)
        script_path = tf.name
    try:
        cmd = [
            _az_bin(),
            "vm",
            "run-command",
            "invoke",
            "-g",
            "rg-shared-prod",
            "-n",
            "tc-prod-vm",
            "--command-id",
            "RunShellScript",
            "--scripts",
            f"@{script_path}",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, shell=False)
        if r.returncode != 0:
            raise RuntimeError(
                f"az invoke failed (rc={r.returncode}): "
                f"{(r.stderr or r.stdout or '')[:400]}"
            )
        data = json.loads(r.stdout)
        message = data["value"][0]["message"]
        raw_stdout, stderr = _parse_az_message(message)
        if stderr and not raw_stdout.strip():
            raise RuntimeError(f"prod script failed (stderr): {stderr[:400]}")
        decoded, expected_count, payload_len = decode_prod_payload(raw_stdout)
        return decoded
    finally:
        try:
            Path(script_path).unlink()
        except OSError:
            pass


# ──────────────────────────────────────────────────────────────────────
# Output parsing + classification
# ──────────────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class ProdProbeResult:
    """Parsed result of a single prod probe run.

    The ``summary_*_count`` fields come from the prod script's tail-emitted
    SUMMARY lines. When the body is tail-truncated (output > ~4KB), the
    summary count exceeds ``len(mismatched/missing/extra)`` and the
    ``truncated_*`` properties report True.
    """

    match_count: int
    mismatched: list[tuple[str, str]]
    missing_on_prod: list[str]
    extra_on_prod: list[str]
    summary_mismatch_count: int = -1
    summary_missing_count: int = -1
    summary_extra_count: int = -1

    @property
    def truncated_mismatch(self) -> bool:
        return (
            self.summary_mismatch_count >= 0
            and self.summary_mismatch_count > len(self.mismatched)
        )

    @property
    def truncated_missing(self) -> bool:
        return (
            self.summary_missing_count >= 0
            and self.summary_missing_count > len(self.missing_on_prod)
        )

    @property
    def truncated_extra(self) -> bool:
        return (
            self.summary_extra_count >= 0
            and self.summary_extra_count > len(self.extra_on_prod)
        )

    @property
    def any_truncated(self) -> bool:
        return (
            self.truncated_mismatch
            or self.truncated_missing
            or self.truncated_extra
        )


def parse_prod_output(stdout: str) -> ProdProbeResult:
    """Parse the prod-side script's stdout into a structured result.

    The prod script emits body lines (MISMATCH/MISSING_ON_PROD/EXTRA_ON_PROD)
    FIRST and SUMMARY_* count lines LAST. If stdout is tail-truncated at ~4KB,
    the summary survives even when the body overflows — the count is then a
    ground-truth that may exceed the body lines we see locally.
    """
    match_count = 0
    summary_mismatch = -1
    summary_missing = -1
    summary_extra = -1
    mismatched: list[tuple[str, str]] = []
    missing_on_prod: list[str] = []
    extra_on_prod: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=2)
        kind = parts[0]
        if kind == "SUMMARY_MATCH_COUNT" and len(parts) == 2:
            match_count = int(parts[1])
        elif kind == "SUMMARY_MISMATCH_COUNT" and len(parts) == 2:
            summary_mismatch = int(parts[1])
        elif kind == "SUMMARY_MISSING_ON_PROD_COUNT" and len(parts) == 2:
            summary_missing = int(parts[1])
        elif kind == "SUMMARY_EXTRA_ON_PROD_COUNT" and len(parts) == 2:
            summary_extra = int(parts[1])
        elif kind == "MISMATCH" and len(parts) == 3:
            mismatched.append((parts[1], parts[2]))
        elif kind == "MISSING_ON_PROD" and len(parts) >= 2:
            missing_on_prod.append(parts[1])
        elif kind == "EXTRA_ON_PROD" and len(parts) >= 2:
            extra_on_prod.append(parts[1])
        # Unknown lines silently ignored — could be shell-noise prefix
    # If the body counts don't match the summary, the body was tail-truncated.
    # Surface that to the caller via the truncated_* attributes (see below).
    return ProdProbeResult(
        match_count=match_count,
        mismatched=mismatched,
        missing_on_prod=missing_on_prod,
        extra_on_prod=extra_on_prod,
        summary_mismatch_count=summary_mismatch,
        summary_missing_count=summary_missing,
        summary_extra_count=summary_extra,
    )


def classify(
    expected: dict[str, str],
    result: ProdProbeResult,
    known_overlays: dict[str, tuple[str, str]] = KNOWN_OVERLAYS,
) -> list[SweepFinding]:
    """Convert a ProdProbeResult into per-file SweepFindings."""
    findings: list[SweepFinding] = []
    # Mismatches: split into DIFFER-EXPECTED-PER-DEPLOY-LOG vs DIFFER-STALE-ON-PROD
    for rel_path, actual in result.mismatched:
        if rel_path in known_overlays:
            overlay_ref, overlay_reason = known_overlays[rel_path]
            findings.append(
                SweepFinding(
                    status="DIFFER-EXPECTED-PER-DEPLOY-LOG",
                    path=rel_path,
                    expected_md5=expected.get(rel_path),
                    actual_md5=actual,
                    overlay_ref=overlay_ref,
                    overlay_reason=overlay_reason,
                )
            )
        else:
            findings.append(
                SweepFinding(
                    status="DIFFER-STALE-ON-PROD",
                    path=rel_path,
                    expected_md5=expected.get(rel_path),
                    actual_md5=actual,
                )
            )
    for rel_path in result.missing_on_prod:
        findings.append(
            SweepFinding(
                status="MISSING_ON_PROD",
                path=rel_path,
                expected_md5=expected.get(rel_path),
                actual_md5=None,
            )
        )
    for rel_path in result.extra_on_prod:
        findings.append(
            SweepFinding(
                status="PROD_ONLY_NOT_ON_MAIN",
                path=rel_path,
                expected_md5=None,
                actual_md5=None,
            )
        )
    return findings


def format_report(
    expected: dict[str, str],
    result: ProdProbeResult,
    findings: list[SweepFinding],
) -> tuple[str, int]:
    """Render a markdown-ish report. Returns (text, exit_code)."""
    n_total = len(expected)
    n_match = result.match_count
    # Prefer the prod-side SUMMARY counts (tail-emitted, survive truncation)
    # over the local body length (may be truncated). When parsing succeeded
    # without truncation, both numbers agree.
    n_mismatch = (
        result.summary_mismatch_count
        if result.summary_mismatch_count >= 0
        else len(result.mismatched)
    )
    n_missing = (
        result.summary_missing_count
        if result.summary_missing_count >= 0
        else len(result.missing_on_prod)
    )
    n_extra = (
        result.summary_extra_count
        if result.summary_extra_count >= 0
        else len(result.extra_on_prod)
    )
    n_differ_expected = sum(
        1 for f in findings if f.status == "DIFFER-EXPECTED-PER-DEPLOY-LOG"
    )
    n_differ_stale = sum(1 for f in findings if f.status == "DIFFER-STALE-ON-PROD")

    lines: list[str] = []
    lines.append("# Prod vs origin/main file-level md5 sweep")
    lines.append("")
    lines.append(f"Surface: trading_corp/ + config/  ({n_total} expected files)")
    lines.append("")
    if result.any_truncated:
        lines.append(
            "WARNING: prod output body was tail-truncated at the az 4KB cap. "
            "Summary counts below are authoritative (emitted last by the prod "
            "script). Per-finding lists may be incomplete."
        )
        lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- MATCH:                          {n_match}")
    lines.append(f"- DIFFER-EXPECTED-PER-DEPLOY-LOG: {n_differ_expected}")
    lines.append(f"- DIFFER-STALE-ON-PROD:           {n_differ_stale}")
    lines.append(f"- MISSING_ON_PROD:                {n_missing}")
    lines.append(f"- PROD_ONLY_NOT_ON_MAIN:          {n_extra}")
    lines.append("")

    if n_differ_expected:
        lines.append("## DIFFER-EXPECTED-PER-DEPLOY-LOG (known sed-overlays)")
        lines.append("")
        for f in findings:
            if f.status != "DIFFER-EXPECTED-PER-DEPLOY-LOG":
                continue
            lines.append(f"### `{f.path}`")
            lines.append("")
            lines.append(f"- expected (origin/main LF): `{f.expected_md5}`")
            lines.append(f"- actual (prod):             `{f.actual_md5}`")
            lines.append(f"- ref:                       {f.overlay_ref}")
            lines.append(f"- reason:                    {f.overlay_reason}")
            lines.append("")

    if n_differ_stale:
        lines.append("## DIFFER-STALE-ON-PROD (must be in next deploy's transfer set)")
        lines.append("")
        for f in findings:
            if f.status != "DIFFER-STALE-ON-PROD":
                continue
            lines.append(f"- `{f.path}`")
            lines.append(f"    expected (origin/main LF): `{f.expected_md5}`")
            lines.append(f"    actual (prod):             `{f.actual_md5}`")
        lines.append("")

    if n_missing:
        lines.append("## MISSING_ON_PROD (in main's tree, not on prod's disk)")
        lines.append("")
        lines.append("These files were either never deployed, or were deleted on")
        lines.append("prod. Investigate before next deploy.")
        lines.append("")
        for f in findings:
            if f.status != "MISSING_ON_PROD":
                continue
            lines.append(f"- `{f.path}`  (expected md5: `{f.expected_md5}`)")
        lines.append("")

    if n_extra:
        lines.append("## PROD_ONLY_NOT_ON_MAIN (uncommitted prod additions)")
        lines.append("")
        lines.append("These files exist on prod but are not git-tracked on")
        lines.append("origin/main. May be uncommitted surgical edits — round-trip")
        lines.append("to git or document as overlay.")
        lines.append("")
        for f in findings:
            if f.status != "PROD_ONLY_NOT_ON_MAIN":
                continue
            lines.append(f"- `{f.path}`")
        lines.append("")

    if not (n_differ_expected or n_differ_stale or n_missing or n_extra):
        lines.append("## Result: CLEAN")
        lines.append("")
        lines.append("Every file on prod matches origin/main LF md5. No drift.")
        exit_code = 0
    elif n_differ_stale or n_missing or n_extra:
        lines.append(
            "## Result: DRIFT DETECTED -- include stale-on-prod files in next "
            "deploy's transfer set; investigate missing/extra before proceeding."
        )
        exit_code = 1
    else:
        lines.append(
            "## Result: only known overlays diverge — no stale-on-prod files. "
            "Safe to proceed with next deploy."
        )
        exit_code = 0
    lines.append("")
    return "\n".join(lines), exit_code


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--manifest-only",
        action="store_true",
        help="print the surface manifest and exit (no az call)",
    )
    p.add_argument(
        "--head",
        default="HEAD",
        help="git ref to enumerate files from (default: HEAD)",
    )
    p.add_argument(
        "--report",
        type=Path,
        default=None,
        help="write the report to this file (default: stdout only)",
    )
    args = p.parse_args(argv)

    paths = list_main_surface_files(ref=args.head)
    if args.manifest_only:
        for path in paths:
            print(path)
        return 0

    expected = build_expected_md5s(paths)
    script = build_prod_compare_script(expected)
    try:
        stdout = run_prod_probe(script)
    except Exception as e:
        sys.stderr.write(f"ERROR running prod probe: {e}\n")
        return 2
    result = parse_prod_output(stdout)
    findings = classify(expected, result)
    report, exit_code = format_report(expected, result, findings)
    # Write file FIRST (UTF-8, lossless) before stdout (which may be cp1252 on Windows)
    if args.report is not None:
        args.report.write_text(report, encoding="utf-8")
    # Reconfigure stdout to UTF-8 if possible; otherwise fall back to ASCII-safe
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stdout.write(report)
    except (AttributeError, OSError):
        sys.stdout.write(report.encode("ascii", errors="replace").decode("ascii"))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
