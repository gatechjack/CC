"""Cross-branch class-identity guard for BitunixPositionModeMismatch.

Pre-merge cross-branch coordination flag (filed in
[[bitunix-order-path-safety-pattern]] § "Cross-branch coordination flag"):
the broker-write branch USED to define `BitunixPositionModeMismatch` inline
in `trading_corp/brokers/bitunix.py`; the safety branch defines it in
`trading_corp/brokers/bitunix_exceptions.py` and the safety consumer
(`data_exec.py`) imports from there. Python `except` matches on class
object IDENTITY (the `__mro__` lookup uses `is` semantics on each entry,
not name equality) — if `raise` and `except` reference two same-named
but distinct class objects, the catch silently fails.

This file moves `BitunixPositionModeMismatch` to the canonical home
(`bitunix_exceptions.py`) on the broker-write branch too, and re-exports
the symbol from `bitunix.py` for back-compat. Both import paths must
yield the SAME object so a future merge can't degrade silently.

Hard contract
- `from trading_corp.brokers.bitunix import BitunixPositionModeMismatch`
  and `from trading_corp.brokers.bitunix_exceptions import
  BitunixPositionModeMismatch` are IS-equal (same class object).
- `except BitunixPositionModeMismatch` catches a `raise
  BitunixPositionModeMismatch(...)` from either import path.
- `isinstance(exc, ...)` works against either import path.
- Inheritance chain is unchanged (still a RuntimeError).
- Constructor signature unchanged: `(current, expected="ONE_WAY")`.
"""
from __future__ import annotations

import pytest


def test_bitunix_and_bitunix_exceptions_export_same_class_object():
    """The IS-equality assertion — load-bearing for cross-branch merge
    safety. If this ever fires, the broker raises one class object and
    the consumer catches a different one with the same name; the catch
    silently fails."""
    from trading_corp.brokers.bitunix import (
        BitunixPositionModeMismatch as FromBroker,
    )
    from trading_corp.brokers.bitunix_exceptions import (
        BitunixPositionModeMismatch as FromCanonical,
    )
    assert FromBroker is FromCanonical, (
        "BitunixPositionModeMismatch must be the SAME class object via "
        "both import paths. Two same-named but distinct classes would "
        "break `except` matching (silent miss). "
        f"FromBroker={FromBroker!r} id={id(FromBroker)}; "
        f"FromCanonical={FromCanonical!r} id={id(FromCanonical)}."
    )


def test_except_clause_catches_raise_across_both_import_paths():
    """Actual try/except dance — raises via one import path, catches
    via the other. If class identity is broken, this would not catch."""
    from trading_corp.brokers.bitunix import (
        BitunixPositionModeMismatch as FromBroker,
    )
    from trading_corp.brokers.bitunix_exceptions import (
        BitunixPositionModeMismatch as FromCanonical,
    )

    # raise from broker import, catch from canonical
    caught = False
    try:
        raise FromBroker(current="HEDGE")
    except FromCanonical:
        caught = True
    assert caught, (
        "except (FromCanonical) failed to catch raise FromBroker — "
        "class identity is broken; consumer would silently miss the trap"
    )

    # raise from canonical import, catch from broker (mirror)
    caught = False
    try:
        raise FromCanonical(current="HEDGE")
    except FromBroker:
        caught = True
    assert caught, (
        "except (FromBroker) failed to catch raise FromCanonical — "
        "the other direction is also load-bearing"
    )


def test_isinstance_works_across_both_import_paths():
    from trading_corp.brokers.bitunix import (
        BitunixPositionModeMismatch as FromBroker,
    )
    from trading_corp.brokers.bitunix_exceptions import (
        BitunixPositionModeMismatch as FromCanonical,
    )

    exc = FromBroker(current="HEDGE")
    assert isinstance(exc, FromBroker)
    assert isinstance(exc, FromCanonical)


def test_inheritance_chain_unchanged_runtimeerror():
    """The canonical class must still inherit from RuntimeError. If
    someone refactored the base class, broader `except RuntimeError`
    clauses elsewhere (paper_trade_replay etc.) would diverge from
    the historical behavior."""
    from trading_corp.brokers.bitunix_exceptions import (
        BitunixPositionModeMismatch,
    )
    assert issubclass(BitunixPositionModeMismatch, RuntimeError)
    # `except Exception` also still catches it — generic safety net
    # in the observer's run loop.
    assert issubclass(BitunixPositionModeMismatch, Exception)


def test_constructor_signature_current_required_expected_default():
    """The constructor signature must match the safety-branch shape:
    `(current, expected="ONE_WAY")`. Changing the signature would
    break the safety branch's raise sites (data_exec.py) on merge
    even with class identity intact."""
    from trading_corp.brokers.bitunix_exceptions import (
        BitunixPositionModeMismatch,
    )
    # current required → no-arg construction raises TypeError
    with pytest.raises(TypeError):
        BitunixPositionModeMismatch()  # type: ignore

    # current provided → default expected="ONE_WAY"
    exc = BitunixPositionModeMismatch(current="HEDGE")
    assert exc.current == "HEDGE"
    assert exc.expected == "ONE_WAY"
    assert "HEDGE" in str(exc)
    assert "ONE_WAY" in str(exc)

    # explicit expected= still honored
    exc2 = BitunixPositionModeMismatch(current="UNKNOWN", expected="ONE_WAY")
    assert exc2.expected == "ONE_WAY"


def test_canonical_module_does_not_re_import_from_broker():
    """The canonical module (bitunix_exceptions.py) must NOT import the
    class from bitunix.py — that would re-introduce the circular-import
    risk + couple the canonical home to the broker module. Locked by
    inspecting source.

    Note: bitunix.py importing FROM bitunix_exceptions is fine and
    desired (that's the whole point of the move)."""
    from pathlib import Path
    canonical = Path(__file__).resolve().parent.parent / "trading_corp" / "brokers" / "bitunix_exceptions.py"
    src = canonical.read_text(encoding="utf-8")
    assert "from trading_corp.brokers.bitunix " not in src and \
           "import trading_corp.brokers.bitunix" not in src, (
        "bitunix_exceptions.py must NOT depend on bitunix.py — that "
        "would defeat the 'safe-to-import-without-the-broker' purpose "
        "of the canonical module"
    )
