"""K5·2 — factory live-vs-paper resolution for the kalshi family (anti-half-flip).

Mirrors the polymarket factory tests: LIVE + family-selected + slug in
--live-divisions => KalshiLiveBroker (placement-legal); any missing leg of the
AND-gate => the read-only KalshiBroker. Fundless (broker construction only; no
connect / no network).
"""
from __future__ import annotations

from types import SimpleNamespace

from trading_corp.brokers.base import Broker, ReadOnlyBroker
from trading_corp.brokers.kalshi import KalshiBroker
from trading_corp.brokers.kalshi_live import KalshiLiveBroker


def _div():
    return SimpleNamespace(broker="kalshi", slug="kalshi_copy_trading", account_filter=None)


def _secrets():
    return SimpleNamespace(kalshi_api_key_id="k", kalshi_private_key_pem="pem")


def _readonly(b):
    return isinstance(b, KalshiBroker) and not isinstance(b, KalshiLiveBroker)


def test_factory_live_and_selected_returns_live_broker():
    from trading_corp.main import _build_broker_for_division
    b = _build_broker_for_division(
        _div(), _secrets(), "LIVE", ["kalshi"], {"kalshi_copy_trading"},
    )
    assert isinstance(b, KalshiLiveBroker)   # anti-half-flip: live, not read-only
    assert isinstance(b, Broker)
    assert b.paper is False


def test_factory_paper_returns_readonly():
    from trading_corp.main import _build_broker_for_division
    b = _build_broker_for_division(_div(), _secrets(), "PAPER", ["kalshi"])
    assert _readonly(b)
    assert isinstance(b, ReadOnlyBroker)


def test_factory_live_but_not_selected_returns_readonly():
    from trading_corp.main import _build_broker_for_division
    b = _build_broker_for_division(_div(), _secrets(), "LIVE", [])
    assert _readonly(b)


def test_live_division_requires_family_selected():
    # slug listed but family NOT in --brokers => still paper (AND needs both halves)
    from trading_corp.main import _build_broker_for_division
    b = _build_broker_for_division(
        _div(), _secrets(), "LIVE", [], {"kalshi_copy_trading"},
    )
    assert _readonly(b)


def test_live_division_requires_live_mode():
    # slug + family selected but mode PAPER => paper (family_live_capable False)
    from trading_corp.main import _build_broker_for_division
    b = _build_broker_for_division(
        _div(), _secrets(), "PAPER", ["kalshi"], {"kalshi_copy_trading"},
    )
    assert _readonly(b)


def test_ghost_slug_does_not_arm_pct():
    from trading_corp.main import _build_broker_for_division
    b = _build_broker_for_division(
        _div(), _secrets(), "LIVE", ["kalshi"], {"some_other_division"},
    )
    assert _readonly(b)
