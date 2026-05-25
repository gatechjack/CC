"""Pull FULL prod kalshi_weather_evaluated forecast-bearing audit corpus
via server-side dump → gzip → chunked-binary pull with sha256 verification.

This is the "chunked-readback driver that solved the 4KB cap" pattern,
adapted from the existing scripts/fetch_kalshi_weather_corpus.py shape.
Single dump on prod; chunked pull back.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import time
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from trading_corp.persistence.db import init_db  # noqa: E402

DEFAULT_DB = REPO_ROOT / "data" / "trading_corp.db"
CHUNK = 2800  # bytes binary per dd; base64 ~3733 chars; fits under stdout cap


def _az_bin() -> str:
    return shutil.which("az") or shutil.which("az.cmd") or "az.cmd"


def _az(script: str, retries: int = 8, sleep_s: float = 8.0) -> str:
    b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
    wrapper = f"echo {b64} | base64 -d | bash"
    cmd = [
        _az_bin(), "vm", "run-command", "invoke",
        "-g", "rg-shared-prod", "-n", "tc-prod-vm",
        "--command-id", "RunShellScript",
        "--scripts", wrapper,
    ]
    last_err = ""
    for _ in range(retries):
        r = subprocess.run(cmd, capture_output=True, text=True, shell=False)
        combined = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0:
            data = json.loads(r.stdout)
            msg = data["value"][0]["message"]
            if "[stdout]" in msg:
                return msg.split("[stdout]", 1)[1].split("[stderr]", 1)[0]
            return msg
        last_err = combined
        if "Conflict" in combined or "in progress" in combined:
            time.sleep(sleep_s)
            continue
        break
    raise RuntimeError(f"az fail: {last_err[:300]}")


def server_dump_and_gzip(series_filter_sql: str) -> tuple[int, str, int]:
    """Server-side: dump → gzip. Return (size_bytes, sha256, row_count)."""
    sql = (
        "SELECT ts || '|' || hex(json_object("
        "'ticker', json_extract(payload_json,'$.ticker'),"
        "'lat', json_extract(payload_json,'$.lat'),"
        "'lon', json_extract(payload_json,'$.lon'),"
        "'coord_source', json_extract(payload_json,'$.coord_source'),"
        "'yaml_coords', json_extract(payload_json,'$.yaml_coords'),"
        "'legacy_coords', json_extract(payload_json,'$.legacy_coords'),"
        "'forecast_temp_f', json_extract(payload_json,'$.forecast_temp_f'),"
        "'target_iso', json_extract(payload_json,'$.target_iso'),"
        "'horizon_hours', json_extract(payload_json,'$.horizon_hours')"
        ")) "
        "FROM audit_event "
        "WHERE actor='kalshi_weather_arb' "
        "AND kind='kalshi_weather_evaluated' "
        "AND json_extract(payload_json,'$.forecast_temp_f') IS NOT NULL "
        f"AND ({series_filter_sql}) "
        "ORDER BY ts"
    )
    script = f"""
set -e
cd /home/azureuser/trading_corp
sqlite3 data/trading_corp.db <<'EOF_SQL' > /tmp/audit_dump.tsv
{sql}
EOF_SQL
gzip -f /tmp/audit_dump.tsv
echo "size_bytes=$(stat -c%s /tmp/audit_dump.tsv.gz)"
echo "sha256=$(sha256sum /tmp/audit_dump.tsv.gz | cut -d' ' -f1)"
echo "row_count=$(zcat /tmp/audit_dump.tsv.gz | wc -l)"
"""
    out = _az(script)
    print(f"  server-side dump complete:")
    print(f"  {out.strip()}")
    fields = {}
    for line in out.strip().splitlines():
        line = line.strip()
        if "=" in line:
            k, v = line.split("=", 1)
            fields[k.strip()] = v.strip()
    return int(fields["size_bytes"]), fields["sha256"], int(fields["row_count"])


def chunked_pull(remote_path: str, size: int, expected_sha256: str) -> bytes:
    n_chunks = (size + CHUNK - 1) // CHUNK
    print(f"  pulling {size:,} bytes in {n_chunks} chunks × {CHUNK} bytes...")
    pieces: list[bytes] = []
    offset = 0
    while offset < size:
        cmd = (
            f"dd if={remote_path} bs=1 skip={offset} count={CHUNK} 2>/dev/null "
            f"| base64 -w0"
        )
        out = _az(cmd).strip()
        try:
            piece = base64.b64decode(out)
        except Exception:
            cleaned = out.replace("\n", "").replace(" ", "").replace("\t", "")
            piece = base64.b64decode(cleaned)
        pieces.append(piece)
        offset += CHUNK
        if (offset // CHUNK) % 10 == 0 or offset >= size:
            pct = 100 * min(offset, size) / size
            print(f"    pulled {min(offset, size):,}/{size:,} ({pct:.0f}%)")
    blob = b"".join(pieces)[:size]
    got_sha = hashlib.sha256(blob).hexdigest()
    if got_sha != expected_sha256:
        raise RuntimeError(f"sha256 mismatch: expected {expected_sha256}, got {got_sha}")
    print(f"  sha256 OK: {got_sha}")
    return blob


def insert_local(db_path: Path, lines: list[str]) -> int:
    init_db(f"sqlite:///{db_path}")
    conn = sqlite3.connect(db_path)
    try:
        sql = (
            "INSERT INTO audit_event (ts, actor, kind, payload_json) "
            "VALUES (?, 'kalshi_weather_arb', 'kalshi_weather_evaluated', ?)"
        )
        n = 0
        for line in lines:
            if "|" not in line:
                continue
            ts, hex_payload = line.split("|", 1)
            try:
                payload_bytes = bytes.fromhex(hex_payload)
                payload_text = payload_bytes.decode("utf-8")
            except ValueError:
                continue
            try:
                conn.execute(sql, (ts, payload_text))
                n += 1
            except sqlite3.IntegrityError:
                pass
        conn.commit()
        return n
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrected-only", action="store_true",
                        help="filter to NYC/CHI/HOU corrected-series only (~9.2K rows)")
    parser.add_argument("--all", action="store_true",
                        help="all stations (~59K rows; will take longer)")
    args = parser.parse_args()

    corrected_filter = (
        "json_extract(payload_json,'$.ticker') LIKE 'KXHIGHNY%' OR "
        "json_extract(payload_json,'$.ticker') LIKE 'KXLOWTNYC%' OR "
        "json_extract(payload_json,'$.ticker') LIKE 'KXHIGHCHI%' OR "
        "json_extract(payload_json,'$.ticker') LIKE 'KXLOWTCHI%' OR "
        "json_extract(payload_json,'$.ticker') LIKE 'KXHIGHTHOU%' OR "
        "json_extract(payload_json,'$.ticker') LIKE 'KXLOWTHOU%'"
    )
    all_filter = "1=1"  # no filter
    series_filter = corrected_filter if args.corrected_only else all_filter

    print(f"=== Phase 1: server-side dump + gzip ===")
    size, expected_sha, row_count = server_dump_and_gzip(series_filter)

    print(f"\n=== Phase 2: chunked-binary pull ({size:,} bytes, {row_count:,} rows) ===")
    blob = chunked_pull("/tmp/audit_dump.tsv.gz", size, expected_sha)

    print(f"\n=== Phase 3: decompress + insert ===")
    raw = zlib.decompress(blob, 31)  # gzip header
    text = raw.decode("utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    print(f"  decoded {len(lines):,} lines")
    if len(lines) != row_count:
        print(f"  WARN: decoded {len(lines)} != server row_count {row_count}")
    n_inserted = insert_local(Path(args.db), lines)
    print(f"INSERTED {n_inserted:,} rows into local audit_event")
    return 0


if __name__ == "__main__":
    sys.exit(main())
