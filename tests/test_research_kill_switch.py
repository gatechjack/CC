"""Kill switch: HALT_RESEARCH file presence aborts engagements at the FIRST node.

Pins (unchanged from v2):
  - is_kill_switch_present(repo_root) reads from <repo_root>/HALT_RESEARCH
  - returns (True, info) with mtime + age_seconds when present
  - returns (False, info) when absent (path-only info dict)
"""
from __future__ import annotations

from pathlib import Path

from trading_corp.agents.research.kill_switch import (
    KILL_SWITCH_FILENAME, is_kill_switch_present, kill_switch_path,
)


def test_kill_switch_path_uses_repo_root(tmp_path: Path):
    p = kill_switch_path(repo_root=tmp_path)
    assert p == tmp_path / KILL_SWITCH_FILENAME


def test_kill_switch_absent(tmp_path: Path):
    present, info = is_kill_switch_present(repo_root=tmp_path)
    assert present is False
    assert info["path"].endswith(KILL_SWITCH_FILENAME)
    assert "mtime_iso" not in info


def test_kill_switch_present(tmp_path: Path):
    (tmp_path / KILL_SWITCH_FILENAME).write_text("halt", encoding="utf-8")
    present, info = is_kill_switch_present(repo_root=tmp_path)
    assert present is True
    assert "mtime_iso" in info
    assert info["age_seconds"] >= 0
