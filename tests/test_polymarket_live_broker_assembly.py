"""E1·6 — PolymarketLiveBroker(Broker) assembly + factory live-vs-paper resolution.

Mocked/fundless: no real CLOB, no creds, no funds. Verifies the WIRING (the
delegated pieces — mapping/sign/place/poll/cancel/quote — are tested in E1·2-5):
  - PolymarketLiveBroker is a placement-legal Broker (NOT a ReadOnlyBroker);
  - connect() L2-authorizes the client (create_or_derive_api_creds -> set_api_creds);
  - place_order/cancel_order delegate to the E1·2-4 module fns with the L2 client;
  - snapshot/quote delegate to the read adapter (E1·5 quote);
  - the main.py factory returns the LIVE broker when LIVE+selected, else read-only
    (the anti-half-flip).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import trading_corp.brokers.polymarket_live as pl
from trading_corp.brokers.base import Broker, ReadOnlyBroker
from trading_corp.brokers.polymarket import PolymarketBroker
from trading_corp.brokers.polymarket_live import PolymarketLiveBroker


def _live():
    return PolymarketLiveBroker(
        private_key="0xkey", funder_address="0xfunder", polygon_rpc_url="http://rpc",
    )


# ── Broker-ABC conformance: placement-legal, NOT read-only ──────────────────

def test_live_broker_is_placement_legal_broker():
    b = _live()
    assert isinstance(b, Broker)          # placement-legal (has place_order)
    assert b.paper is False               # live, not paper
    assert hasattr(b, "place_order") and hasattr(b, "cancel_order")


def test_read_adapter_is_readonly_not_broker():
    rb = PolymarketBroker(funder_address="0xf", polygon_rpc_url="http://rpc")
    assert isinstance(rb, ReadOnlyBroker)
    assert not isinstance(rb, Broker)     # cannot place — the key distinction


# ── connect(): L2 authorization ─────────────────────────────────────────────

async def test_connect_l2_authorizes_client():
    b = _live()
    b._read = AsyncMock()                  # read-adapter connect mocked
    b._assert_funded_and_approved = AsyncMock()  # E1·7 preflight stubbed (tested below)
    clob = MagicMock()
    clob.create_or_derive_api_creds.return_value = "CREDS"
    b._build_clob_client = lambda: clob    # inject mock client (no real SDK)

    await b.connect()

    b._read.connect.assert_awaited_once()
    b._assert_funded_and_approved.assert_awaited_once()  # preflight runs on connect
    clob.create_or_derive_api_creds.assert_called_once()
    clob.set_api_creds.assert_called_once_with("CREDS")
    assert b._clob is clob
    assert b._connected is True


async def test_disconnect_clears_state():
    b = _live()
    b._read = AsyncMock()
    b._clob = MagicMock()
    b._connected = True
    await b.disconnect()
    b._read.disconnect.assert_awaited_once()
    assert b._clob is None and b._connected is False


# ── place/cancel delegate to the (E1·2-4) module fns with the L2 client ─────

# E2·2: place_order dispatches on the configured order_type. The DEFAULT is
# fak_synth (synthesized FAK), so the default broker delegates to the synth fn —
# NOT _place_order_fn (which is the native gtc/fok/gtd path). This supersedes the
# E1·6 single-delegation assertion (place_order had no dispatch before E2·2).


async def test_place_order_default_fak_synth_delegates_to_synth_fn(monkeypatch):
    b = _live()                                # default order_type == fak_synth
    assert b._order_type == "fak_synth"
    b._clob = MagicMock()
    b._connected = True
    fill = object()
    fn = AsyncMock(return_value=fill)
    monkeypatch.setattr(pl, "_place_order_fak_synth_fn", fn)
    result = await b.place_order("ORDER")
    assert result is fill
    fn.assert_awaited_once_with(b._clob, "ORDER", poll_seconds=b._fak_poll_seconds)


async def test_place_order_native_order_type_delegates_to_native_fn(monkeypatch):
    b = PolymarketLiveBroker(
        private_key="0xkey", funder_address="0xfunder", polygon_rpc_url="http://rpc",
        order_type="gtc",
    )
    b._clob = MagicMock()
    b._connected = True
    fill = object()
    fn = AsyncMock(return_value=fill)
    monkeypatch.setattr(pl, "_place_order_fn", fn)
    result = await b.place_order("ORDER")
    assert result is fill
    # The order_type STRING is passed through; the broker never imports the SDK to
    # resolve it (resolution happens inside the real place_order, mocked away here).
    fn.assert_awaited_once_with(b._clob, "ORDER", order_type="gtc")


def test_default_order_type_and_poll_window():
    b = _live()
    assert b._order_type == "fak_synth"
    assert b._fak_poll_seconds == 5.0


def test_invalid_order_type_rejected_at_construction():
    with pytest.raises(ValueError, match="order_type"):
        PolymarketLiveBroker(
            private_key="0xk", funder_address="0xf", polygon_rpc_url="http://rpc",
            order_type="market",
        )


def test_negative_fak_poll_seconds_rejected():
    with pytest.raises(ValueError, match="fak_poll_seconds"):
        PolymarketLiveBroker(
            private_key="0xk", funder_address="0xf", polygon_rpc_url="http://rpc",
            fak_poll_seconds=-1.0,
        )


async def test_cancel_order_delegates_to_module_fn(monkeypatch):
    b = _live()
    b._clob = MagicMock()
    b._connected = True
    fn = AsyncMock(return_value=True)
    monkeypatch.setattr(pl, "_cancel_order_fn", fn)
    result = await b.cancel_order("0xOID")
    assert result is True
    fn.assert_awaited_once_with(b._clob, "0xOID")


async def test_place_and_cancel_require_connected():
    b = _live()                            # not connected (_clob=None)
    with pytest.raises(RuntimeError):
        await b.place_order("O")
    with pytest.raises(RuntimeError):
        await b.cancel_order("O")


# ── snapshot/quote delegate to the read adapter (E1·5 SDK quote) ────────────

async def test_quote_and_snapshot_delegate_to_read_adapter():
    b = _live()
    b._read = AsyncMock()
    b._read.quote.return_value = 0.55
    b._read.snapshot.return_value = "SNAP"
    assert await b.quote("slug:Yes") == 0.55
    b._read.quote.assert_awaited_once_with("slug:Yes")
    assert await b.snapshot() == "SNAP"


# ── factory: live-vs-paper resolution (the anti-half-flip) ──────────────────

def _div():
    return SimpleNamespace(broker="polymarket", slug="polymarket_copy_trading", account_filter=None)


def _secrets():
    return SimpleNamespace(
        polymarket_wallets={
            "polymarket_copy_trading": SimpleNamespace(private_key="0xk", funder_address="0xf"),
        },
        polygon_rpc_url="http://rpc",
    )


def test_factory_live_and_selected_returns_live_broker():
    from trading_corp.main import _build_broker_for_division
    # E2·4: arming live now requires the SLUG in live_divisions too (family-level
    # selection alone is no longer sufficient — see the E2·4 tests below).
    b = _build_broker_for_division(
        _div(), _secrets(), "LIVE", ["polymarket"], {"polymarket_copy_trading"},
    )
    assert isinstance(b, PolymarketLiveBroker)   # the anti-half-flip: live, not read-only


def test_factory_paper_returns_readonly():
    from trading_corp.main import _build_broker_for_division
    b = _build_broker_for_division(_div(), _secrets(), "PAPER", ["polymarket"])
    assert isinstance(b, PolymarketBroker)
    assert not isinstance(b, PolymarketLiveBroker)


def test_factory_live_but_not_selected_returns_readonly():
    from trading_corp.main import _build_broker_for_division
    b = _build_broker_for_division(_div(), _secrets(), "LIVE", [])
    assert isinstance(b, PolymarketBroker)
    assert not isinstance(b, PolymarketLiveBroker)


# ── E2·4: per-division live-select (--live-divisions) — slug-level anti-half-flip ──
# A division arms LIVE iff family-live-capable (LIVE + --brokers <family>) AND its
# slug ∈ --live-divisions. The family check alone is NOT sufficient.


def _arb_div():
    # Same polymarket FAMILY as PCT (_div), different slug — must stay paper.
    return SimpleNamespace(broker="polymarket", slug="polymarket_arbitrage", account_filter=None)


def _readonly(b):
    return isinstance(b, PolymarketBroker) and not isinstance(b, PolymarketLiveBroker)


def test_live_divisions_isolates_pct_live_arb_paper():
    # CORE property: --live-divisions {polymarket_copy_trading} arms PCT live but
    # leaves the arb division PAPER — even though both are the SAME polymarket family.
    from trading_corp.main import _build_broker_for_division
    live = {"polymarket_copy_trading"}
    pct = _build_broker_for_division(_div(), _secrets(), "LIVE", ["polymarket"], live)
    arb = _build_broker_for_division(_arb_div(), _secrets(), "LIVE", ["polymarket"], live)
    assert isinstance(pct, PolymarketLiveBroker)   # PCT armed live
    assert _readonly(arb)                          # arb stays paper despite same family


def test_no_live_divisions_all_paper_even_with_brokers_polymarket():
    # LIVE + --brokers polymarket but NO --live-divisions ⇒ everything paper.
    from trading_corp.main import _build_broker_for_division
    for ld in (None, set(), frozenset()):
        b = _build_broker_for_division(_div(), _secrets(), "LIVE", ["polymarket"], ld)
        assert _readonly(b), f"live_divisions={ld!r} should leave the division paper"


def test_family_capable_alone_without_slug_stays_paper():
    # Family IS live-capable (LIVE + --brokers polymarket) but the slug is NOT listed
    # ⇒ the family-level path alone does NOT arm the division live (the AND-gate).
    from trading_corp.main import _build_broker_for_division
    b = _build_broker_for_division(
        _div(), _secrets(), "LIVE", ["polymarket"], {"some_other_division"},
    )
    assert _readonly(b)


def test_slug_not_among_running_divisions_no_effect():
    # A slug that matches no running division ⇒ no crash, nothing silently armed:
    # PCT (not listed) stays paper; the ghost slugs simply never match.
    from trading_corp.main import _build_broker_for_division
    b = _build_broker_for_division(
        _div(), _secrets(), "LIVE", ["polymarket"], {"ghost_division", "another_ghost"},
    )
    assert _readonly(b)


def test_live_division_also_requires_family_selected():
    # Slug listed but family NOT in --brokers ⇒ still paper (the AND needs both halves).
    from trading_corp.main import _build_broker_for_division
    b = _build_broker_for_division(
        _div(), _secrets(), "LIVE", [], {"polymarket_copy_trading"},
    )
    assert _readonly(b)


def test_live_division_requires_live_mode():
    # Slug listed + family listed but mode PAPER ⇒ paper (family_live_capable is False).
    from trading_corp.main import _build_broker_for_division
    b = _build_broker_for_division(
        _div(), _secrets(), "PAPER", ["polymarket"], {"polymarket_copy_trading"},
    )
    assert _readonly(b)


# ── E2·4: --live-divisions CLI parsing ──────────────────────────────────────


def test_parse_live_divisions_comma_space_and_empty():
    from trading_corp.main import _parse_live_divisions
    assert _parse_live_divisions([]) == set()
    assert _parse_live_divisions(None) == set()
    assert _parse_live_divisions(["a", "b"]) == {"a", "b"}            # space-separated
    assert _parse_live_divisions(["a,b"]) == {"a", "b"}               # comma-separated
    assert _parse_live_divisions(["a,b", "c"]) == {"a", "b", "c"}     # mixed
    assert _parse_live_divisions([" a , ,b "]) == {"a", "b"}          # trims + drops empties


def test_cli_live_divisions_flag_default_and_parse():
    from trading_corp.main import parse_args, _parse_live_divisions
    assert _parse_live_divisions(parse_args([]).live_divisions) == set()   # opt-in default
    a = parse_args(["--live-divisions", "polymarket_copy_trading"])
    assert _parse_live_divisions(a.live_divisions) == {"polymarket_copy_trading"}


# ── E1·7: connect() on-chain funded+approved preflight (read-only, mocked) ──
#
# OP·A's creds-completeness preflight already shipped in `assert_live_ready`
# (utils/secrets.py, item-7 `500cc1e`); E1·7 is ONLY the connect()-level on-chain
# check (funder holds USDC.e + the ERC-20/ERC-1155 exchange approvals are set) —
# NOT a duplicate creds-check. All on-chain reads are mocked; no funds, no signing.


def _preflight_broker(balance=100.0, eth_calls=(1, 1, 1, 1)):
    """A live broker with the read adapter's connect mocked and the on-chain
    reads stubbed for the E1·7 preflight: `_read` provisioned
    (funder/_client/_rpc_url), USDC.e balance = `balance`, and `_eth_call`
    returning `eth_calls` = (allowance_std, allowance_neg, approved_std,
    approved_neg) in call order. `_build_clob_client` returns a mock so a passing
    preflight proceeds to the (mocked) L2 auth."""
    b = _live()
    b._read.connect = AsyncMock()          # don't hit real endpoints
    b._read.disconnect = AsyncMock()
    b._read._funder = "0x" + "ab" * 20     # provisioned (valid 20-byte funder EOA)
    b._read._client = MagicMock()          # httpx client present (connect would set it)
    b._read._rpc_url = "http://rpc"
    b._read._fetch_usdc_balance = AsyncMock(return_value=balance)
    b._eth_call = AsyncMock(side_effect=list(eth_calls))
    clob = MagicMock()
    clob.create_or_derive_api_creds.return_value = "CREDS"
    b._build_clob_client = lambda: clob
    b._clob_for_test = clob
    return b


async def test_preflight_unprovisioned_wallet_aborts_connect():
    b = _live()
    b._read.connect = AsyncMock()
    b._read._funder = None                 # stub wallet (no key/funder provisioned)
    b._read._client = None
    with pytest.raises(RuntimeError, match="not provisioned"):
        await b.connect()
    assert b._connected is False and b._clob is None


async def test_preflight_unfunded_aborts_connect():
    b = _preflight_broker(balance=0.0)     # 0 USDC.e
    with pytest.raises(RuntimeError, match="USDC.e"):
        await b.connect()
    assert b._connected is False and b._clob is None
    b._read._fetch_usdc_balance.assert_awaited_once()


async def test_preflight_zero_std_allowance_aborts_connect():
    b = _preflight_broker(balance=100.0, eth_calls=(0, 1, 1, 1))  # std ERC-20 allowance 0
    with pytest.raises(RuntimeError, match="allowance"):
        await b.connect()
    assert b._connected is False


async def test_preflight_zero_negrisk_allowance_aborts_connect():
    b = _preflight_broker(balance=100.0, eth_calls=(1, 0, 1, 1))  # negRisk allowance 0
    with pytest.raises(RuntimeError, match="allowance"):
        await b.connect()
    assert b._connected is False


async def test_preflight_unset_std_approval_aborts_connect():
    b = _preflight_broker(balance=100.0, eth_calls=(1, 1, 0, 1))  # std CTF approval not set
    with pytest.raises(RuntimeError, match="approval-for-all"):
        await b.connect()
    assert b._connected is False


async def test_preflight_unset_negrisk_approval_aborts_connect():
    b = _preflight_broker(balance=100.0, eth_calls=(1, 1, 1, 0))  # negRisk CTF approval not set
    with pytest.raises(RuntimeError, match="approval-for-all"):
        await b.connect()
    assert b._connected is False


async def test_preflight_fully_ready_connects():
    b = _preflight_broker(balance=100.0, eth_calls=(1, 1, 1, 1))
    await b.connect()
    assert b._connected is True
    assert b._clob is b._clob_for_test     # proceeded to L2 auth after preflight passed
    # exactly 4 on-chain reads: 2 allowances (std, negRisk) + 2 approvals (std, negRisk)
    assert b._eth_call.await_count == 4
    b._read._fetch_usdc_balance.assert_awaited_once()


# ── E1·7: calldata builders (pure — selector + 32-byte ABI padding) ─────────

def test_pad_addr_left_pads_to_32_bytes():
    padded = pl._pad_addr("0x" + "ab" * 20)
    assert padded == ("0" * 24) + ("ab" * 20)     # 24 zero-nibbles + 20-byte addr
    assert len(padded) == 64                       # 32 bytes


def test_pad_addr_rejects_wrong_length():
    with pytest.raises(ValueError):
        pl._pad_addr("0x1234")                     # not a 20-byte address


def test_allowance_calldata_selector_and_args():
    owner = "0x" + "11" * 20
    spender = "0x" + "22" * 20
    data = pl._allowance_calldata(owner, spender)
    assert data.startswith("0xdd62ed3e")           # ERC-20 allowance(owner,spender)
    assert len(data) == 10 + 64 + 64               # 0x+selector(8) + 2*32-byte args
    assert data[10:74] == ("0" * 24) + ("11" * 20)  # owner padded
    assert data[74:] == ("0" * 24) + ("22" * 20)    # spender padded


def test_is_approved_for_all_calldata_selector_and_args():
    owner = "0x" + "33" * 20
    operator = "0x" + "44" * 20
    data = pl._is_approved_for_all_calldata(owner, operator)
    assert data.startswith("0xe985e9c5")           # ERC-1155 isApprovedForAll(owner,operator)
    assert len(data) == 10 + 64 + 64
    assert data[10:74] == ("0" * 24) + ("33" * 20)
    assert data[74:] == ("0" * 24) + ("44" * 20)
