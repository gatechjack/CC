"""Tests for the division LIVE vs PAPER badge introduced in B-ARM #5.

Pins the pure helper ``_division_is_live`` that ``build_division_view``
and the PMCC combo-approval route both call to determine whether a
division's broker is live (real-money) or paper/sim.

The helper is a tiny pure function — no async, no DB, no broker I/O —
so it can be unit-tested directly without standing up the full app.
"""
from __future__ import annotations

import pytest

from trading_corp.web.data import _division_is_live


# ── Stub broker classes ───────────────────────────────────────────────────

class _LiveBroker:
    """Mimics a live real-money broker: paper=False."""
    paper = False


class _PaperBroker:
    """Mimics a paper/sim broker: paper=True."""
    paper = True


class _NoPaperAttr:
    """Broker that has no `paper` attribute at all (e.g. an unexpected stub)."""


# ── Tests ─────────────────────────────────────────────────────────────────

def test_live_broker_returns_true():
    """A broker with paper=False is live — helper must return True."""
    assert _division_is_live(_LiveBroker()) is True


def test_paper_broker_returns_false():
    """A broker with paper=True is sim — helper must return False."""
    assert _division_is_live(_PaperBroker()) is False


def test_none_broker_returns_false():
    """When no broker is available (division not wired), helper is conservative: False."""
    assert _division_is_live(None) is False


def test_missing_paper_attr_returns_false():
    """A broker with no `paper` attribute → default True in getattr → returns False.

    This ensures an unknown broker type is treated conservatively as paper
    rather than accidentally flagged as live.
    """
    assert _division_is_live(_NoPaperAttr()) is False


def test_paper_true_via_getattr_default():
    """Explicit verification: getattr(broker, 'paper', True) default=True means
    not-live for an attribute-less broker."""
    broker = object()  # plain object, no `paper`
    assert _division_is_live(broker) is False


def test_paper_false_subclass():
    """Subclass with paper=False from class attribute → still live."""
    class Sub(_LiveBroker):
        pass
    assert _division_is_live(Sub()) is True


def test_paper_truthy_non_bool_treated_as_paper():
    """paper=1 (truthy but not strict False) → treated as paper → not live."""
    class TruthyPaper:
        paper = 1
    assert _division_is_live(TruthyPaper()) is False


def test_paper_falsy_non_bool_treated_as_live():
    """paper=0 (falsy) → treated as live."""
    class FalsyLive:
        paper = 0
    assert _division_is_live(FalsyLive()) is True


def test_is_live_field_default_on_dataclass():
    """DivisionViewSnapshot.is_live defaults to False (additive, non-breaking)."""
    from trading_corp.web.data import DivisionViewSnapshot
    import inspect
    fields = {f.name: f for f in DivisionViewSnapshot.__dataclass_fields__.values()}
    assert "is_live" in fields, "is_live field missing from DivisionViewSnapshot"
    assert fields["is_live"].default is False, (
        "is_live must default to False so existing callers without broker info "
        "stay conservatively paper"
    )
