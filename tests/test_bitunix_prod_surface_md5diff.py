"""Unit tests for scripts/bitunix_prod_surface_md5diff.py (gate c).

No live prod calls; the az-shell-out function is monkeypatched in the
end-to-end main() test.
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "bitunix_prod_surface_md5diff.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location(
        "bitunix_prod_surface_md5diff", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_script_module()


def test_local_md5_lf_normalizes_crlf(mod, tmp_path: Path) -> None:
    crlf = tmp_path / "crlf.txt"
    crlf.write_bytes(b"line1\r\nline2\r\n")
    lf = tmp_path / "lf.txt"
    lf.write_bytes(b"line1\nline2\n")
    assert mod.local_md5_lf(crlf) == mod.local_md5_lf(lf)
    expected = hashlib.md5(b"line1\nline2\n").hexdigest()
    assert mod.local_md5_lf(crlf) == expected


def test_local_md5_lf_missing_returns_none(mod, tmp_path: Path) -> None:
    assert mod.local_md5_lf(tmp_path / "does_not_exist.txt") is None


def test_parse_md5sum_stdout_happy(mod) -> None:
    stdout = (
        "d41d8cd98f00b204e9800998ecf8427e  trading_corp/brokers/bitunix.py\n"
        "60526f15ed4d0e1b9a8f7c6e5d4c3b2a  config/strategies.yaml\n"
    )
    parsed = mod.parse_md5sum_stdout(stdout)
    assert parsed == {
        "trading_corp/brokers/bitunix.py": "d41d8cd98f00b204e9800998ecf8427e",
        "config/strategies.yaml": "60526f15ed4d0e1b9a8f7c6e5d4c3b2a",
    }


def test_parse_md5sum_stdout_ignores_garbage(mod) -> None:
    stdout = (
        "d41d8cd98f00b204e9800998ecf8427e  ok/path.py\n"
        "\n"
        "md5sum: missing/file.py: No such file or directory\n"
        "not-a-hash  bad/line.py\n"
    )
    parsed = mod.parse_md5sum_stdout(stdout)
    assert parsed == {"ok/path.py": "d41d8cd98f00b204e9800998ecf8427e"}


def test_parse_az_message_with_stderr(mod) -> None:
    msg = (
        "Enable succeeded:\n[stdout]\nhash1  a.py\nhash2  b.py\n\n"
        "[stderr]\nsome warning\n"
    )
    stdout, stderr = mod._parse_az_message(msg)
    assert "hash1  a.py" in stdout
    assert "hash2  b.py" in stdout
    assert "some warning" in stderr


def test_parse_az_message_no_stderr_block(mod) -> None:
    msg = "Enable succeeded:\n[stdout]\nhash1  a.py\n"
    stdout, stderr = mod._parse_az_message(msg)
    assert "hash1  a.py" in stdout
    assert stderr == ""


def test_parse_az_message_no_stdout_marker_raises(mod) -> None:
    with pytest.raises(RuntimeError, match="no \\[stdout\\] marker"):
        mod._parse_az_message("Enable failed: something went wrong")


def test_compare_all_status_branches(mod) -> None:
    manifest = ["a", "b", "c", "d", "e"]
    local = {"a": "h1", "b": "h2", "c": "h3", "d": None, "e": "h5"}
    prod = {"a": "h1", "b": "h99", "c": None, "d": "h4", "e": None}
    rows = mod.compare(manifest, local, prod)
    statuses = {path: status for status, path, _, _ in rows}
    assert statuses == {
        "a": "MATCH",
        "b": "DIFFER",
        "c": "MISSING_PROD",
        "d": "MISSING_LOCAL",
        "e": "MISSING_PROD",
    }


def test_format_report_clean_exits_zero(mod) -> None:
    rows = [
        ("MATCH", "a", "h1", "h1"),
        ("MATCH", "b", "h2", "h2"),
    ]
    report, exit_code = mod.format_report(rows)
    assert exit_code == 0
    assert "Result: clean" in report
    assert "2 MATCH" in report


def test_format_report_drift_exits_one(mod) -> None:
    rows = [
        ("MATCH", "a", "h1", "h1"),
        ("DIFFER", "config/strategies.yaml", "local_h", "prod_h"),
    ]
    report, exit_code = mod.format_report(rows)
    assert exit_code == 1
    assert "DRIFT DETECTED" in report
    assert "local local_h" in report
    assert "prod  prod_h" in report


def test_format_report_missing_exits_two(mod) -> None:
    rows = [
        ("MATCH", "a", "h1", "h1"),
        ("MISSING_PROD", "b", "h2", None),
    ]
    report, exit_code = mod.format_report(rows)
    assert exit_code == 2
    assert "MANIFEST ERROR" in report


def test_format_report_missing_outranks_drift(mod) -> None:
    rows = [
        ("DIFFER", "a", "h1", "h2"),
        ("MISSING_LOCAL", "b", None, "h3"),
    ]
    report, exit_code = mod.format_report(rows)
    assert exit_code == 2
    assert "MANIFEST ERROR" in report


def test_main_manifest_only_flag(mod, capsys) -> None:
    rc = mod.main(["--manifest-only"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "trading_corp/brokers/bitunix.py" in out
    assert "config/risk.yaml" in out
    assert "config/strategies.yaml" in out
    line_count = len([line for line in out.splitlines() if line.strip()])
    assert line_count == len(mod.MANIFEST)


def test_main_clean_run_end_to_end(mod, monkeypatch, capsys, tmp_path: Path) -> None:
    """End-to-end main() with az mocked + REPO_ROOT redirected to a fixture tree."""
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    for rel in mod.MANIFEST:
        target = fake_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"content of {rel}\n".encode())
    monkeypatch.setattr(mod, "REPO_ROOT", fake_root)
    expected = {
        rel: hashlib.md5(f"content of {rel}\n".encode()).hexdigest()
        for rel in mod.MANIFEST
    }
    monkeypatch.setattr(mod, "_prod_md5_via_az", lambda paths: dict(expected))
    rc = mod.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Result: clean" in out
    assert f"{len(mod.MANIFEST)} MATCH" in out


def test_main_drift_run_end_to_end(mod, monkeypatch, capsys, tmp_path: Path) -> None:
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    for rel in mod.MANIFEST:
        target = fake_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"content of {rel}\n".encode())
    monkeypatch.setattr(mod, "REPO_ROOT", fake_root)
    drift_path = "config/strategies.yaml"
    prod_map = {
        rel: (
            hashlib.md5(b"DRIFTED").hexdigest()
            if rel == drift_path
            else hashlib.md5(f"content of {rel}\n".encode()).hexdigest()
        )
        for rel in mod.MANIFEST
    }
    monkeypatch.setattr(mod, "_prod_md5_via_az", lambda paths: dict(prod_map))
    rc = mod.main([])
    out = capsys.readouterr().out
    assert rc == 1
    assert "DRIFT DETECTED" in out
    assert drift_path in out
