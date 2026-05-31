"""Stage-1 redeploy attempt #3 chunked transfer helper.

Reads the 66-file manifest, splits into ~17-file chunks, and for each chunk:
  1. Tar+gzip the chunk's files locally
  2. Multi-call b64-push the tarball to prod /tmp/redeploy3-chunk-N.tgz
  3. On prod: extract to staging dir, then per-file:
     - If file exists on prod: backup to <file>.<TAG>
     - Install from staging
     - md5 verify
  4. Return per-file md5 manifest; compare vs expected.

STOP on any mismatch (caller surfaces to operator).
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import shlex
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

_AZ_EXE = shutil.which("az.cmd") or shutil.which("az")
if _AZ_EXE is None:
    sys.stderr.write("FATAL: 'az' not on PATH\n")
    sys.exit(2)

VM_RG = "rg-shared-prod"
VM_NAME = "tc-prod-vm"
PROD_BASE = "/home/azureuser/trading_corp"

# Per-az-call payload budget. The az --scripts cap is ~28KB by az's own
# rules, but on Windows the cmd.exe shim hits its 8191-char total-command-
# line limit MUCH earlier. Budget: 6500 chars of b64 per call leaves ~1500
# chars for wrapper + az's other args under the cmd 8K limit.
PUSH_CHUNK_BYTES_B64 = 6500

MANIFEST_STALE = [
    "config/divisions.yaml",
    "config/risk.yaml",
    "config/weather_stations.yaml",
    "trading_corp/agents/backtester.py",
    "trading_corp/agents/data_exec.py",
    "trading_corp/agents/divisions/bitunix_futures_observer.py",
    "trading_corp/agents/divisions/fidelity_options.py",
    "trading_corp/agents/logger.py",
    "trading_corp/agents/risk.py",
    "trading_corp/agents/strategies/_weather_math.py",
    "trading_corp/agents/strategies/bitunix_confluence.py",
    "trading_corp/agents/strategies/bitunix_pa_validation.py",
    "trading_corp/agents/strategies/btc_accumulator.py",
    "trading_corp/agents/strategies/ic_candidate_grader.py",
    "trading_corp/agents/strategies/kalshi_llm_arbitrage.py",
    "trading_corp/agents/strategies/kalshi_sports_scout.py",
    "trading_corp/agents/strategies/polymarket_arbitrage.py",
    "trading_corp/brokers/bitunix.py",
    "trading_corp/brokers/kalshi.py",
    "trading_corp/comms/bitunix_lifecycle_notifier.py",
    "trading_corp/comms/telegram_commands.py",
    "trading_corp/data/bitunix_bar_archiver.py",
    "trading_corp/data/bitunix_htf_context.py",
    "trading_corp/data/kalshi_market_map.py",
    "trading_corp/data/kalshi_whale_stats.py",
    "trading_corp/data/weather_stations.py",
    "trading_corp/graph/ceo_graph.py",
    "trading_corp/main.py",
    "trading_corp/persistence/db.py",
    "trading_corp/persistence/models.py",
    "trading_corp/utils/divisions.py",
    "trading_corp/utils/secrets.py",
    "trading_corp/web/app.py",
    "trading_corp/web/data.py",
    "trading_corp/web/routes.py",
    "trading_corp/web/static/icons/apple-touch-icon-152.png",
    "trading_corp/web/static/icons/apple-touch-icon-167.png",
    "trading_corp/web/static/icons/apple-touch-icon-180.png",
    "trading_corp/web/static/icons/favicon-16.png",
    "trading_corp/web/static/icons/favicon-32.png",
    "trading_corp/web/static/icons/icon-192.png",
    "trading_corp/web/static/icons/icon-512.png",
    "trading_corp/web/static/icons/icon-maskable-512.png",
    "trading_corp/web/templates/division.html",
    "trading_corp/web/templates/home.html",
    "trading_corp/web/templates/iron_condor_live.html",
    "trading_corp/web/templates/partials/bitunix_score_panel.html",
    "trading_corp/web/templates/partials/stat_cards.html",
    "trading_corp/web/templates/partials/trade_flow.html",
    "trading_corp/web/templates/research.html",
    "trading_corp/web/webhooks.py",
]
assert len(MANIFEST_STALE) == 51, len(MANIFEST_STALE)

MANIFEST_MISSING = [
    "trading_corp/agents/divisions/tasty_options.py",
    "trading_corp/agents/strategies/tasty_options_iron_condor.py",
    "trading_corp/brokers/bitunix_exceptions.py",
    "trading_corp/brokers/bitunix_symbols.py",
    "trading_corp/brokers/tastytrade.py",
    "trading_corp/data/iem_cli_client.py",
    "trading_corp/data/nbm_client.py",
    "trading_corp/data/residual_logic.py",
    "trading_corp/path_logger/__init__.py",
    "trading_corp/path_logger/__main__.py",
    "trading_corp/path_logger/logger.py",
    "trading_corp/path_logger/main.py",
    "trading_corp/path_logger/store.py",
    "trading_corp/scripts/analyze_polymarket_whale.py",
]
assert len(MANIFEST_MISSING) == 14, len(MANIFEST_MISSING)

MANIFEST_STRATEGIES_YAML = ["config/strategies.yaml"]
MANIFEST_ALL = MANIFEST_STALE + MANIFEST_MISSING + MANIFEST_STRATEGIES_YAML
assert len(MANIFEST_ALL) == 66, len(MANIFEST_ALL)

# Files that EXIST on prod (need .TAG backup before overwrite)
MANIFEST_BACKUP_REQUIRED = set(MANIFEST_STALE) | set(MANIFEST_STRATEGIES_YAML)
assert len(MANIFEST_BACKUP_REQUIRED) == 52


def lf_normalize(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def is_text_file(path: str) -> bool:
    """Files that should be LF-normalized before md5/transfer."""
    return not path.endswith((".png", ".jpg", ".jpeg", ".gif", ".ico"))


def read_file_bytes(repo_root: Path, path: str) -> bytes:
    """Read with LF normalization for text files (matches prod's md5sum)."""
    data = (repo_root / path).read_bytes()
    if is_text_file(path):
        data = lf_normalize(data)
    return data


def compute_expected_md5s(repo_root: Path, files: list[str]) -> dict[str, str]:
    out = {}
    for f in files:
        data = read_file_bytes(repo_root, f)
        out[f] = hashlib.md5(data).hexdigest()
    return out


def az_run(script: str, timeout: int = 180, b64_wrap: bool = True) -> tuple[int, str]:
    """Invoke az vm run-command, return (exit_code, stdout).

    When b64_wrap=True (default), wraps the script as base64 so cmd.exe never
    sees embedded quotes/specials in the --scripts arg. The outer arg becomes
    `echo <B64> | base64 -d | bash`. Use b64_wrap=False ONLY when the script
    is known cmd-safe (single-quoted alphanumeric content) and you need to
    stay under the cmd-line limit — b64-wrap adds ~33% to arg size.
    """
    if b64_wrap:
        b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
        arg = f"echo {b64} | base64 -d | bash"
    else:
        arg = script
    cmd = [
        _AZ_EXE, "vm", "run-command", "invoke",
        "-g", VM_RG, "-n", VM_NAME,
        "--command-id", "RunShellScript",
        "--scripts", arg,
        "--query", "value[0].message",
        "-o", "tsv",
    ]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            shell=False, encoding="utf-8", errors="replace",
        )
        return r.returncode, (r.stdout or "") + (("\n[STDERR]\n" + r.stderr) if r.stderr else "")
    except subprocess.TimeoutExpired:
        return 124, "[TIMEOUT]"


def chunkify(files: list[str], n_chunks: int) -> list[list[str]]:
    """Split files into n_chunks roughly-equal lists."""
    chunks = [[] for _ in range(n_chunks)]
    for i, f in enumerate(files):
        chunks[i % n_chunks].append(f)
    return chunks


def build_tarball(repo_root: Path, files: list[str]) -> bytes:
    """Tar+gzip a list of files; text files are LF-normalized."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=9) as tar:
        for f in files:
            data = read_file_bytes(repo_root, f)
            info = tarfile.TarInfo(name=f)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def push_tarball(tag: str, chunk_idx: int, tar_bytes: bytes) -> bool:
    """Multi-call b64-push a tarball to prod /tmp/redeploy3-chunk-N.tgz."""
    b64 = base64.b64encode(tar_bytes).decode("ascii")
    remote_path = f"/tmp/redeploy3-{tag}-chunk-{chunk_idx}.tgz"
    remote_b64 = remote_path + ".b64"
    local_md5 = hashlib.md5(tar_bytes).hexdigest()

    # Step 1: truncate the b64 file
    rc, out = az_run(f"rm -f {shlex.quote(remote_b64)} {shlex.quote(remote_path)}; touch {shlex.quote(remote_b64)}; echo TRUNC_OK")
    if "TRUNC_OK" not in out:
        print(f"[CHUNK {chunk_idx}] truncate failed: {out[:500]}", file=sys.stderr)
        return False

    # Step 2: append b64 in PUSH_CHUNK_BYTES_B64-sized pieces
    n_pieces = (len(b64) + PUSH_CHUNK_BYTES_B64 - 1) // PUSH_CHUNK_BYTES_B64
    print(f"[CHUNK {chunk_idx}] {len(tar_bytes)} bytes raw, {len(b64)} b64, {n_pieces} push-calls", file=sys.stderr)
    for i in range(n_pieces):
        piece = b64[i * PUSH_CHUNK_BYTES_B64:(i + 1) * PUSH_CHUNK_BYTES_B64]
        script = f"printf '%s' {shlex.quote(piece)} >> {shlex.quote(remote_b64)}; echo APPEND_OK {i}"
        rc, out = az_run(script, timeout=120, b64_wrap=False)
        if f"APPEND_OK {i}" not in out:
            print(f"[CHUNK {chunk_idx}] piece {i} append failed: {out[:500]}", file=sys.stderr)
            return False
        if (i + 1) % 10 == 0 or i == n_pieces - 1:
            print(f"[CHUNK {chunk_idx}]   pushed {i + 1}/{n_pieces}", file=sys.stderr)

    # Step 3: decode and md5-verify
    script = (
        f"base64 -d < {shlex.quote(remote_b64)} > {shlex.quote(remote_path)} && "
        f"M=$(md5sum {shlex.quote(remote_path)} | awk '{{print $1}}') && "
        f"echo \"DECODE_OK md5=$M\""
    )
    rc, out = az_run(script, timeout=60)
    if "DECODE_OK" not in out or local_md5 not in out:
        print(f"[CHUNK {chunk_idx}] decode/md5 mismatch (expected {local_md5}): {out[:500]}", file=sys.stderr)
        return False
    print(f"[CHUNK {chunk_idx}] tarball md5 verified: {local_md5}", file=sys.stderr)
    return True


INSTALL_BATCH_SIZE = 5  # ~6 files per az call to stay under cmd 8K limit


def _install_batch(tag: str, chunk_idx: int, batch_idx: int, batch_files: list[str], staging: str, remote_tgz: str, backup_required: set[str]) -> dict[str, str]:
    """Run one install sub-batch on prod. Returns {file: md5} for installed files. Empty on failure."""
    lines = [
        "set -e",
        f"BASE={PROD_BASE}",
        f"STAGE={staging}",
        f"TAG={tag}",
    ]
    # Only the first batch creates staging + extracts tarball.
    if batch_idx == 0:
        lines.append(f"rm -rf $STAGE && mkdir -p $STAGE")
        lines.append(f"tar -xzf {remote_tgz} -C $STAGE")
    lines.append("cd $BASE")
    lines.append(f"echo BATCH_BEGIN {batch_idx}")
    for f in batch_files:
        qf = shlex.quote(f)
        backup_suffix = f".{tag}"
        if f in backup_required:
            lines.append(f"[ -f {qf} ] && cp -p {qf} {qf}{backup_suffix}; true")
        lines.append(f"mkdir -p \"$(dirname {qf})\"")
        lines.append(f"cp $STAGE/{qf} {qf}")
        lines.append(f"M=$(md5sum {qf} | awk '{{print $1}}'); echo \"INSTALLED {f} $M\"")
    lines.append(f"echo BATCH_END {batch_idx}")
    script = "; ".join(lines)
    if len(script) > 6500:
        print(f"[CHUNK {chunk_idx} batch {batch_idx}] WARN script {len(script)} chars (near cmd 8K limit)", file=sys.stderr)
    rc, out = az_run(script, timeout=180)
    # Always emit raw out for diagnostics
    print(f"[CHUNK {chunk_idx} batch {batch_idx}] rc={rc} out_len={len(out)} script_len={len(script)}", file=sys.stderr)
    print(f"[CHUNK {chunk_idx} batch {batch_idx}] raw_out:\n{out}\n[end raw_out]", file=sys.stderr)
    if f"BATCH_BEGIN {batch_idx}" not in out or f"BATCH_END {batch_idx}" not in out:
        print(f"[CHUNK {chunk_idx} batch {batch_idx}] INCOMPLETE — markers missing", file=sys.stderr)
        return {}
    got: dict[str, str] = {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("INSTALLED "):
            parts = line.split()
            if len(parts) >= 3:
                got[parts[1]] = parts[2]
    return got


def install_chunk(tag: str, chunk_idx: int, files: list[str], backup_required: set[str], expected_md5s: dict[str, str]) -> bool:
    """On prod: extract tarball (in batch 0), then per-batch backup+install+md5-verify."""
    remote_tgz = f"/tmp/redeploy3-{tag}-chunk-{chunk_idx}.tgz"
    staging = f"/tmp/redeploy3-{tag}-chunk-{chunk_idx}-stage"

    # Sub-batch files
    batches = [files[i:i + INSTALL_BATCH_SIZE] for i in range(0, len(files), INSTALL_BATCH_SIZE)]
    all_got: dict[str, str] = {}
    for batch_idx, batch_files in enumerate(batches):
        print(f"[CHUNK {chunk_idx}] install batch {batch_idx + 1}/{len(batches)}: {len(batch_files)} files", file=sys.stderr)
        got = _install_batch(tag, chunk_idx, batch_idx, batch_files, staging, remote_tgz, backup_required)
        if not got:
            print(f"[CHUNK {chunk_idx} batch {batch_idx}] FAILED — STOP", file=sys.stderr)
            return False
        all_got.update(got)

    # Verify md5s across all batches
    all_ok = True
    for f in files:
        exp = expected_md5s[f]
        actual = all_got.get(f, "<missing>")
        if actual == exp:
            print(f"[CHUNK {chunk_idx}] OK  {f} ({exp[:12]})", file=sys.stderr)
        else:
            print(f"[CHUNK {chunk_idx}] MISMATCH {f} expected={exp} got={actual}", file=sys.stderr)
            all_ok = False
    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True, help="Backup tag e.g. pre-stage1-redeploy3-20260531-0307")
    parser.add_argument("--n-chunks", type=int, default=4)
    parser.add_argument("--start-chunk", type=int, default=0, help="Resume from this chunk (0-indexed)")
    parser.add_argument("--end-chunk", type=int, default=None, help="Stop after this chunk (inclusive, 0-indexed)")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-push", action="store_true", help="Skip tarball push (use existing /tmp/redeploy3-*.tgz on prod); install only.")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    print(f"[redeploy3] repo_root={repo_root}", file=sys.stderr)
    print(f"[redeploy3] tag={args.tag}", file=sys.stderr)
    print(f"[redeploy3] manifest=66 files; n_chunks={args.n_chunks}", file=sys.stderr)

    # Pre-flight: all files exist locally
    missing_locally = [f for f in MANIFEST_ALL if not (repo_root / f).is_file()]
    if missing_locally:
        print(f"[redeploy3] MISSING LOCALLY: {missing_locally}", file=sys.stderr)
        return 2

    expected_md5s = compute_expected_md5s(repo_root, MANIFEST_ALL)
    print("[redeploy3] expected md5s computed", file=sys.stderr)

    chunks = chunkify(MANIFEST_ALL, args.n_chunks)
    for i, c in enumerate(chunks):
        print(f"[redeploy3] chunk {i}: {len(c)} files", file=sys.stderr)

    if args.dry_run:
        print(json.dumps({"chunks": [{"idx": i, "files": c} for i, c in enumerate(chunks)]}, indent=2))
        return 0

    for i, chunk_files in enumerate(chunks):
        if i < args.start_chunk:
            print(f"[redeploy3] skip chunk {i} (resume)", file=sys.stderr)
            continue
        if args.end_chunk is not None and i > args.end_chunk:
            print(f"[redeploy3] stop after end-chunk={args.end_chunk}", file=sys.stderr)
            break
        print(f"\n=== CHUNK {i} ({len(chunk_files)} files) ===", file=sys.stderr)
        if args.skip_push:
            print(f"[CHUNK {i}] skip-push: relying on existing /tmp/redeploy3-{args.tag}-chunk-{i}.tgz", file=sys.stderr)
        else:
            tar = build_tarball(repo_root, chunk_files)
            ok = push_tarball(args.tag, i, tar)
            if not ok:
                print(f"[redeploy3] CHUNK {i} PUSH FAILED — STOP", file=sys.stderr)
                return 3
        ok = install_chunk(args.tag, i, chunk_files, MANIFEST_BACKUP_REQUIRED, expected_md5s)
        if not ok:
            print(f"[redeploy3] CHUNK {i} INSTALL FAILED — STOP (DO NOT RESTART)", file=sys.stderr)
            return 4
        print(f"[redeploy3] CHUNK {i} COMPLETE", file=sys.stderr)

    print("\n[redeploy3] ALL CHUNKS INSTALLED + MD5-VERIFIED", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
