"""PEAD Phase 2 STEP 1 — Robinhood account hard-bind.

The agentic cash account 680725082 is OMITTED from robin_stocks' discovery
list, and the old `_resolve_account_filter` silently fell back to the main
margin account 461391328. These tests pin: a numeric account_filter must bind
to EXACTLY that account (via the direct /accounts/{n}/ fetch) or fail loud —
NEVER the main account.
"""
from __future__ import annotations

import asyncio

import pytest

from trading_corp.brokers import robinhood as rh
from trading_corp.brokers.robinhood import RobinhoodAccountBindError, RobinhoodBroker

_MAIN = {"account_number": "461391328", "brokerage_account_type": "individual"}
_AGENTIC = {"account_number": "680725082", "brokerage_account_type": "individual",
            "type": "cash"}


def _broker(filt: str) -> RobinhoodBroker:
    return RobinhoodBroker(username="u", password="p", account_filter=filt)


def test_numeric_filter_absent_from_discovery_raises_no_fallback(monkeypatch):
    # discovery omits 680725082 — only the main account is present
    monkeypatch.setattr(rh, "_ACCOUNT_LIST", [dict(_MAIN)])
    b = _broker("680725082")
    with pytest.raises(RobinhoodAccountBindError):
        b._resolve_account_filter()           # the defense-in-depth guard
    assert b._account_number != "461391328"   # must NOT have bound to main


def test_ensure_resolvable_binds_via_direct_fetch(monkeypatch):
    monkeypatch.setattr(rh, "_ACCOUNT_LIST", [dict(_MAIN)])

    async def fake_fetch(num):
        return dict(_AGENTIC) if num == "680725082" else None
    monkeypatch.setattr(RobinhoodBroker, "_fetch_account_by_number",
                        staticmethod(fake_fetch))
    b = _broker("680725082")
    asyncio.run(b._ensure_numeric_filter_resolvable())
    b._resolve_account_filter()
    assert b._account_number == "680725082"    # bound to the agentic account


def test_ensure_resolvable_hard_fails_on_404(monkeypatch):
    monkeypatch.setattr(rh, "_ACCOUNT_LIST", [dict(_MAIN)])

    async def fake_fetch(num):
        return None                            # simulate 404
    monkeypatch.setattr(RobinhoodBroker, "_fetch_account_by_number",
                        staticmethod(fake_fetch))
    b = _broker("680725082")
    with pytest.raises(RobinhoodAccountBindError):
        asyncio.run(b._ensure_numeric_filter_resolvable())


def test_numeric_filter_present_in_discovery_binds_directly(monkeypatch):
    monkeypatch.setattr(rh, "_ACCOUNT_LIST", [dict(_MAIN), dict(_AGENTIC)])
    b = _broker("680725082")
    asyncio.run(b._ensure_numeric_filter_resolvable())   # no-op, already present
    b._resolve_account_filter()
    assert b._account_number == "680725082"


def test_nonnumeric_filter_path_unchanged(monkeypatch):
    # type-keyword filters still match/fall-back as before — no raise, no direct fetch
    monkeypatch.setattr(rh, "_ACCOUNT_LIST", [dict(_MAIN)])
    b = _broker("individual")
    asyncio.run(b._ensure_numeric_filter_resolvable())   # no-op for non-numeric
    b._resolve_account_filter()
    assert b._account_number == "461391328"              # matched 'individual'
