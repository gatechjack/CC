"""Tests for the DataExecAgent.safety_notifier slot.

Commit 7a of Stage-1 Session N+1. The slot's actual consumers (the
mode-mismatch handler in place() + flatten_division()) live on the
Session-N safety branch (`bitunix-orderpath-safety-2026-05-29`); this
commit re-adds the constructor kwarg + attribute storage on this
branch so commit 7b's main.py wiring (safety_notifier=channel) is
type-safe right now, before the safety branch merges. After the
safety branch lands, git auto-merges the identical add to a no-op.

Hard contract
- Default constructor (logger only) works → safety_notifier is None.
- Explicit safety_notifier kwarg stores the passed object verbatim.
- Pre-existing dry_run kwarg still works (no signature regression).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trading_corp.agents.data_exec import DataExecAgent


def test_default_constructor_has_none_safety_notifier():
    """Pre-N+1 callers (logger only) must continue to construct cleanly
    with `safety_notifier=None`."""
    agent = DataExecAgent(logger=MagicMock())
    assert agent.safety_notifier is None


def test_explicit_safety_notifier_stored():
    """The kwarg is stored verbatim on the attribute. The slot is
    duck-typed (anything with async push() with the documented
    keyword-arg shape works); the storage doesn't validate."""
    notifier = MagicMock()
    agent = DataExecAgent(logger=MagicMock(), safety_notifier=notifier)
    assert agent.safety_notifier is notifier


def test_safety_notifier_and_dry_run_compose():
    """Both kwargs work together; signature didn't regress."""
    notifier = MagicMock()
    agent = DataExecAgent(
        logger=MagicMock(),
        dry_run=True,
        safety_notifier=notifier,
    )
    assert agent.dry_run is True
    assert agent.safety_notifier is notifier


def test_safety_notifier_is_keyword_only():
    """Slot is keyword-only — positional construction with safety_notifier
    must fail. This locks in the safety branch's signature shape so
    nobody silently passes a positional arg expecting it to land on the
    notifier slot."""
    with pytest.raises(TypeError):
        DataExecAgent(MagicMock(), False, MagicMock())  # type: ignore
