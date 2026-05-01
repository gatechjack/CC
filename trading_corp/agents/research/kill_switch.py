"""Operational kill switch for the research firm.

`<repo_root>/HALT_RESEARCH` file present → every engagement aborts at the
FIRST node of the subgraph (`kill_switch_check_node`). No analyst is
invoked. No LLM cost. Audit row records file mtime for "how long has
this been in place?" diagnostics.

Repo-root location (not `data/`) is deliberate: a load-bearing
operational halt belongs where it's visible in `ls`. If a deploy ever
automates and the file isn't copied, the live system is "back on" —
easier failure mode to notice than the inverse.

See planning/research_firm_design.md §6.5.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

KILL_SWITCH_FILENAME = "HALT_RESEARCH"


def kill_switch_path(repo_root: Path | None = None) -> Path:
    """Resolve the kill-switch path. `repo_root` overridable for tests."""
    base = Path(repo_root) if repo_root else Path.cwd()
    return base / KILL_SWITCH_FILENAME


def is_kill_switch_present(repo_root: Path | None = None) -> tuple[bool, dict]:
    """Return (present, info_dict).

    info_dict carries `path` always; `mtime_iso` and `age_seconds` only
    when present. Caller is responsible for stamping into audit payload.
    """
    p = kill_switch_path(repo_root)
    if not p.exists():
        return False, {"path": str(p)}

    try:
        st = p.stat()
        mtime_dt = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        age_s = (datetime.now(timezone.utc) - mtime_dt).total_seconds()
        return True, {
            "path": str(p),
            "mtime_iso": mtime_dt.isoformat(),
            "age_seconds": int(age_s),
        }
    except OSError:
        # File raced out from under us between exists() and stat() — treat
        # as not-present rather than fail-safe-aborting an engagement.
        # The race window is tiny and the bias toward "let work continue"
        # matches the "default OFF" semantics of an operational toggle.
        return False, {"path": str(p)}
