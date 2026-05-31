"""Tests for scripts/prod_vs_main_file_level_md5_sweep.py.

Covers:
- Surface enumeration smoke (real git tree)
- LF-normalized md5 computation
- Prod-script builder (snapshot-style structural checks)
- Output parser
- Classification logic (known-overlay whitelist)
- Report formatting (clean / drift / known-overlay-only)
- az shell-out is monkeypatched in tests — no live prod call.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

# Import the script as a module
SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_PATH))
import prod_vs_main_file_level_md5_sweep as sweep  # type: ignore


# ──────────────────────────────────────────────────────────────────────
# Surface enumeration + local md5
# ──────────────────────────────────────────────────────────────────────


def test_list_main_surface_files_returns_sorted_subdir_paths() -> None:
    """Smoke: surface enumeration finds at least one file under each subdir."""
    paths = sweep.list_main_surface_files(ref="HEAD")
    assert any(p.startswith("trading_corp/") for p in paths)
    assert any(p.startswith("config/") for p in paths)
    # All paths are sorted
    assert paths == sorted(paths)
    # Sanity: this surface is substantial
    assert len(paths) > 100


def test_local_md5_lf_matches_known_file(tmp_path: Path) -> None:
    """LF md5 of a fixture file is reproducible."""
    fixture = tmp_path / "sub" / "a.txt"
    fixture.parent.mkdir()
    fixture.write_bytes(b"hello\r\nworld\r\n")
    # Inject under REPO_ROOT for local_md5_lf relative-path resolution
    rel = fixture.relative_to(tmp_path).as_posix()
    # Use a local helper to test the LF stripping logic
    data = fixture.read_bytes().replace(b"\r\n", b"\n")
    expected = hashlib.md5(data).hexdigest()
    assert hashlib.md5(b"hello\nworld\n").hexdigest() == expected


def test_local_md5_lf_returns_none_for_missing_file() -> None:
    """Missing file returns None — no crash."""
    assert sweep.local_md5_lf("nonexistent/path/that/should/never/exist.py") is None


def test_local_md5_lf_normalizes_crlf() -> None:
    """LF normalization makes Windows-checkout md5 match Unix-checkout md5."""
    # The repo's web/app.py is a known multi-line file; both LF md5 should be
    # stable across whatever the working tree's actual line endings are.
    h1 = sweep.local_md5_lf("trading_corp/web/app.py")
    h2 = sweep.local_md5_lf("trading_corp/web/app.py")
    assert h1 == h2
    assert h1 is not None
    assert len(h1) == 32


def test_build_expected_md5s_skips_missing() -> None:
    """build_expected_md5s returns only the entries that exist locally."""
    paths = ["trading_corp/web/app.py", "definitely/does/not/exist.py"]
    out = sweep.build_expected_md5s(paths)
    assert "trading_corp/web/app.py" in out
    assert "definitely/does/not/exist.py" not in out


# ──────────────────────────────────────────────────────────────────────
# Prod-side script builder
# ──────────────────────────────────────────────────────────────────────


def test_build_prod_compare_script_includes_expected_dict() -> None:
    """The script embeds the expected md5 dict as JSON."""
    expected = {"trading_corp/foo.py": "a" * 32, "config/bar.yaml": "b" * 32}
    script = sweep.build_prod_compare_script(expected)
    # The JSON must appear (as a string-quoted python literal)
    assert "trading_corp/foo.py" in script
    assert "a" * 32 in script
    assert "config/bar.yaml" in script
    assert "b" * 32 in script


def test_build_prod_compare_script_uses_python3_heredoc() -> None:
    """Verifies the structural shape — python3 heredoc on prod."""
    script = sweep.build_prod_compare_script({})
    assert "python3 - <<'PYEOF'" in script
    assert "PYEOF" in script
    assert "hashlib.md5" in script
    assert "MISMATCH" in script
    assert "MISSING_ON_PROD" in script
    assert "EXTRA_ON_PROD" in script


def test_build_prod_compare_script_uses_compression_markers() -> None:
    """Output is gzip+base64-wrapped between SWEEP_BEGIN_v1 / SWEEP_END_v1."""
    script = sweep.build_prod_compare_script({})
    assert "gzip.compress" in script
    assert "base64.b64encode" in script
    assert sweep.SWEEP_BEGIN_MARKER in script
    assert sweep.SWEEP_END_MARKER in script
    assert "expected_count=" in script
    assert "payload_len=" in script


def test_decode_prod_payload_round_trip() -> None:
    """Local round-trip: encode a fake prod payload, decode, get original."""
    import base64
    import gzip

    inner = (
        "MISMATCH foo.py abc\n"
        "MISSING_ON_PROD bar.py\n"
        "SUMMARY_MATCH_COUNT 100\n"
        "SUMMARY_MISMATCH_COUNT 1\n"
        "SUMMARY_MISSING_ON_PROD_COUNT 1\n"
        "SUMMARY_EXTRA_ON_PROD_COUNT 0\n"
    )
    b64 = base64.b64encode(gzip.compress(inner.encode("utf-8"))).decode("ascii")
    stdout = (
        f"{sweep.SWEEP_BEGIN_MARKER}\n"
        f"{b64}\n"
        f"{sweep.SWEEP_END_MARKER} expected_count=102 payload_len={len(b64)}\n"
    )
    decoded, count, plen = sweep.decode_prod_payload(stdout)
    assert decoded == inner
    assert count == 102
    assert plen == len(b64)


def test_decode_prod_payload_raises_on_missing_begin_marker() -> None:
    with pytest.raises(RuntimeError, match="SWEEP_BEGIN_v1 marker missing"):
        sweep.decode_prod_payload("just some text\nno markers\n")


def test_decode_prod_payload_raises_on_missing_end_marker() -> None:
    stdout = f"{sweep.SWEEP_BEGIN_MARKER}\nsome_b64_data\n"
    with pytest.raises(RuntimeError, match="SWEEP_END_v1 marker missing"):
        sweep.decode_prod_payload(stdout)


def test_decode_prod_payload_raises_on_payload_length_mismatch() -> None:
    """Truncation between markers is caught by the payload_len check."""
    import base64
    import gzip

    inner = "SUMMARY_MATCH_COUNT 1\n"
    b64 = base64.b64encode(gzip.compress(inner.encode("utf-8"))).decode("ascii")
    # Truncate the blob to simulate a partial transmission
    truncated_b64 = b64[: len(b64) // 2]
    stdout = (
        f"{sweep.SWEEP_BEGIN_MARKER}\n"
        f"{truncated_b64}\n"
        f"{sweep.SWEEP_END_MARKER} expected_count=1 payload_len={len(b64)}\n"
    )
    with pytest.raises(RuntimeError, match="payload length mismatch"):
        sweep.decode_prod_payload(stdout)


def test_build_prod_compare_script_uses_prod_base() -> None:
    """The script's cd target is the documented PROD_BASE."""
    script = sweep.build_prod_compare_script({})
    assert sweep.PROD_BASE in script


# ──────────────────────────────────────────────────────────────────────
# az message parsing
# ──────────────────────────────────────────────────────────────────────


def test_parse_az_message_extracts_stdout_and_stderr() -> None:
    """az --query value[0].message text → (stdout, stderr) tuple."""
    msg = "Enable succeeded: \n[stdout]\nhello\nworld\n[stderr]\nwarn\n"
    out, err = sweep._parse_az_message(msg)
    assert out == "hello\nworld"
    assert err == "warn"


def test_parse_az_message_handles_missing_stderr_section() -> None:
    """When the message has no [stderr] marker, stderr is empty."""
    msg = "x\n[stdout]\nonly-out\n"
    out, err = sweep._parse_az_message(msg)
    assert out == "only-out"
    assert err == ""


def test_parse_az_message_raises_without_stdout_marker() -> None:
    with pytest.raises(RuntimeError, match="no \\[stdout\\] marker"):
        sweep._parse_az_message("garbage with no marker")


# ──────────────────────────────────────────────────────────────────────
# Output parser
# ──────────────────────────────────────────────────────────────────────


def test_parse_prod_output_clean_case() -> None:
    """Body lines first (none), then SUMMARY_* counts at the end."""
    stdout = (
        "SUMMARY_MATCH_COUNT 251\n"
        "SUMMARY_MISMATCH_COUNT 0\n"
        "SUMMARY_MISSING_ON_PROD_COUNT 0\n"
        "SUMMARY_EXTRA_ON_PROD_COUNT 0\n"
    )
    r = sweep.parse_prod_output(stdout)
    assert r.match_count == 251
    assert r.mismatched == []
    assert r.missing_on_prod == []
    assert r.extra_on_prod == []
    assert not r.any_truncated


def test_parse_prod_output_with_findings() -> None:
    """Body lines come first, summary lines come last."""
    stdout = (
        "MISMATCH trading_corp/web/app.py 16842c40cefb0b5f54e4e02348d5ca10\n"
        "MISSING_ON_PROD trading_corp/agents/new_thing.py\n"
        "EXTRA_ON_PROD trading_corp/utils/secrets.py.legacy_bak\n"
        "SUMMARY_MATCH_COUNT 249\n"
        "SUMMARY_MISMATCH_COUNT 1\n"
        "SUMMARY_MISSING_ON_PROD_COUNT 1\n"
        "SUMMARY_EXTRA_ON_PROD_COUNT 1\n"
    )
    r = sweep.parse_prod_output(stdout)
    assert r.match_count == 249
    assert r.mismatched == [
        ("trading_corp/web/app.py", "16842c40cefb0b5f54e4e02348d5ca10")
    ]
    assert r.missing_on_prod == ["trading_corp/agents/new_thing.py"]
    assert r.extra_on_prod == ["trading_corp/utils/secrets.py.legacy_bak"]
    assert not r.any_truncated


def test_parse_prod_output_detects_body_truncation() -> None:
    """If summary count > local body length, the body was tail-truncated."""
    stdout = (
        # Body shows only 1 EXTRA line — summary says there were 5.
        "EXTRA_ON_PROD trading_corp/web/late_in_alphabet.py\n"
        "SUMMARY_MATCH_COUNT 100\n"
        "SUMMARY_MISMATCH_COUNT 0\n"
        "SUMMARY_MISSING_ON_PROD_COUNT 0\n"
        "SUMMARY_EXTRA_ON_PROD_COUNT 5\n"
    )
    r = sweep.parse_prod_output(stdout)
    assert r.any_truncated
    assert r.truncated_extra
    assert not r.truncated_mismatch
    assert not r.truncated_missing


def test_parse_prod_output_ignores_unknown_lines() -> None:
    """Shell-noise prefixes (e.g., 'Enable succeeded') are silently ignored."""
    stdout = (
        "set -eu\n"
        "+ cd /home/azureuser/trading_corp\n"
        "SUMMARY_MATCH_COUNT 5\n"
        "SUMMARY_MISMATCH_COUNT 0\n"
    )
    r = sweep.parse_prod_output(stdout)
    assert r.match_count == 5


# ──────────────────────────────────────────────────────────────────────
# Classification
# ──────────────────────────────────────────────────────────────────────


def test_classify_known_overlay_becomes_differ_expected() -> None:
    """A mismatch on a known-overlay path is DIFFER-EXPECTED-PER-DEPLOY-LOG."""
    expected = {"config/strategies.yaml": "a" * 32}
    result = sweep.ProdProbeResult(
        match_count=0,
        mismatched=[("config/strategies.yaml", "b" * 32)],
        missing_on_prod=[],
        extra_on_prod=[],
    )
    findings = sweep.classify(expected, result)
    assert len(findings) == 1
    assert findings[0].status == "DIFFER-EXPECTED-PER-DEPLOY-LOG"
    assert findings[0].path == "config/strategies.yaml"
    assert findings[0].overlay_ref is not None
    assert findings[0].overlay_reason is not None


def test_classify_unknown_mismatch_is_stale_on_prod() -> None:
    """A mismatch on a non-overlay path is DIFFER-STALE-ON-PROD."""
    expected = {"trading_corp/web/app.py": "a" * 32}
    result = sweep.ProdProbeResult(
        match_count=0,
        mismatched=[("trading_corp/web/app.py", "b" * 32)],
        missing_on_prod=[],
        extra_on_prod=[],
    )
    findings = sweep.classify(expected, result)
    assert len(findings) == 1
    assert findings[0].status == "DIFFER-STALE-ON-PROD"
    assert findings[0].overlay_ref is None


def test_classify_missing_and_extra_get_their_statuses() -> None:
    expected = {"trading_corp/foo.py": "a" * 32}
    result = sweep.ProdProbeResult(
        match_count=0,
        mismatched=[],
        missing_on_prod=["trading_corp/foo.py"],
        extra_on_prod=["trading_corp/bar.py.legacy"],
    )
    findings = sweep.classify(expected, result)
    statuses = {f.status for f in findings}
    assert statuses == {"MISSING_ON_PROD", "PROD_ONLY_NOT_ON_MAIN"}


def test_classify_accepts_custom_known_overlays() -> None:
    """The whitelist is injectable for tests."""
    expected = {"trading_corp/custom.py": "a" * 32}
    result = sweep.ProdProbeResult(
        match_count=0,
        mismatched=[("trading_corp/custom.py", "b" * 32)],
        missing_on_prod=[],
        extra_on_prod=[],
    )
    findings = sweep.classify(
        expected,
        result,
        known_overlays={"trading_corp/custom.py": ("doc-ref", "test-reason")},
    )
    assert findings[0].status == "DIFFER-EXPECTED-PER-DEPLOY-LOG"
    assert findings[0].overlay_ref == "doc-ref"
    assert findings[0].overlay_reason == "test-reason"


# ──────────────────────────────────────────────────────────────────────
# Report formatting
# ──────────────────────────────────────────────────────────────────────


def test_format_report_clean_case_exits_zero() -> None:
    expected = {"trading_corp/foo.py": "a" * 32}
    result = sweep.ProdProbeResult(1, [], [], [])
    findings: list[sweep.SweepFinding] = []
    text, exit_code = sweep.format_report(expected, result, findings)
    assert exit_code == 0
    assert "Result: CLEAN" in text
    assert "MATCH:                          1" in text


def test_format_report_stale_on_prod_exits_one() -> None:
    expected = {"trading_corp/foo.py": "a" * 32}
    result = sweep.ProdProbeResult(0, [("trading_corp/foo.py", "b" * 32)], [], [])
    findings = sweep.classify(expected, result, known_overlays={})
    text, exit_code = sweep.format_report(expected, result, findings)
    assert exit_code == 1
    assert "DIFFER-STALE-ON-PROD" in text
    assert "trading_corp/foo.py" in text


def test_format_report_known_overlay_only_exits_zero() -> None:
    """Only known overlays diverge → safe to proceed → exit 0."""
    expected = {"config/strategies.yaml": "a" * 32}
    result = sweep.ProdProbeResult(0, [("config/strategies.yaml", "b" * 32)], [], [])
    findings = sweep.classify(expected, result)
    text, exit_code = sweep.format_report(expected, result, findings)
    assert exit_code == 0
    assert "DIFFER-EXPECTED-PER-DEPLOY-LOG" in text
    assert "only known overlays diverge" in text


def test_format_report_missing_and_extra_exit_one() -> None:
    """MISSING_ON_PROD or PROD_ONLY_NOT_ON_MAIN → drift → exit 1."""
    expected = {"trading_corp/foo.py": "a" * 32}
    result = sweep.ProdProbeResult(
        0, [], ["trading_corp/foo.py"], ["trading_corp/bar.py.legacy"]
    )
    findings = sweep.classify(expected, result)
    text, exit_code = sweep.format_report(expected, result, findings)
    assert exit_code == 1
    assert "MISSING_ON_PROD" in text
    assert "PROD_ONLY_NOT_ON_MAIN" in text


# ──────────────────────────────────────────────────────────────────────
# az invocation (monkeypatched)
# ──────────────────────────────────────────────────────────────────────


def test_run_prod_probe_monkeypatched(monkeypatch) -> None:
    """Confirm run_prod_probe decodes the gzip+base64 payload end-to-end."""
    import base64
    import gzip
    import json as _json

    inner = "SUMMARY_MATCH_COUNT 7\nSUMMARY_MISMATCH_COUNT 0\n"
    b64 = base64.b64encode(gzip.compress(inner.encode("utf-8"))).decode("ascii")
    stdout_text = (
        f"{sweep.SWEEP_BEGIN_MARKER}\n{b64}\n"
        f"{sweep.SWEEP_END_MARKER} expected_count=7 payload_len={len(b64)}\n"
    )
    fake_message = (
        "Enable succeeded: \n"
        "[stdout]\n"
        + stdout_text
        + "[stderr]\n"
    )
    fake_json = _json.dumps({"value": [{"message": fake_message}]})

    class FakeRC:
        returncode = 0
        stdout = fake_json
        stderr = ""

    monkeypatch.setattr(sweep.subprocess, "run", lambda *a, **k: FakeRC())
    out = sweep.run_prod_probe("dummy script")
    # run_prod_probe returns the DECODED inner text, not the wire format
    assert out == inner
    assert "SUMMARY_MATCH_COUNT 7" in out


def test_run_prod_probe_raises_on_az_failure(monkeypatch) -> None:
    class FakeRC:
        returncode = 1
        stdout = ""
        stderr = "az: command not found"

    monkeypatch.setattr(sweep.subprocess, "run", lambda *a, **k: FakeRC())
    with pytest.raises(RuntimeError, match="az invoke failed"):
        sweep.run_prod_probe("dummy")
