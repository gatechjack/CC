"""E5a — polymarket execution config reaches the broker via the Division.

Proves `order_type`/`fak_poll_seconds` flow config → Division (`load_divisions`) →
`PolymarketLiveBroker` ctor; that an UNSET division gets the ctor defaults
(byte-identical to pre-E5a); per-division isolation of the values; that the
migration relocated the keys without stranding them; and that the shared Division
change is inert for bitunix / other families. Fundless, mocked — no funds, no live.
"""
from pathlib import Path
from types import SimpleNamespace

import yaml

from trading_corp.main import _build_broker_for_division
from trading_corp.utils.divisions import load_divisions
from trading_corp.brokers.polymarket_live import (
    PolymarketLiveBroker,
    _SYNTH_FAK,
    _DEFAULT_FAK_POLL_SECONDS,
)

_REPO = Path(__file__).resolve().parents[1]
PCT_PK = "0x" + "b" * 64
PCT_FUNDER = "0x" + "2" * 40
RPC = "http://rpc.invalid"


def _secrets():
    # Two per-division polymarket wallets + a shared RPC (mirrors the assembly tests).
    return SimpleNamespace(
        polymarket_wallets={
            "pct_a": SimpleNamespace(private_key=PCT_PK, funder_address=PCT_FUNDER),
            "pct_b": SimpleNamespace(private_key=PCT_PK, funder_address=PCT_FUNDER),
        },
        polygon_rpc_url=RPC,
    )


def _poly_div(slug="pct_a", *, order_type=None, fak_poll_seconds=None, exit_chase=None):
    return SimpleNamespace(
        broker="polymarket", slug=slug, account_filter="main",
        order_type=order_type, fak_poll_seconds=fak_poll_seconds, exit_chase=exit_chase,
    )


def _build_live(div):
    # mode LIVE + family in --brokers + slug in --live-divisions ⇒ PolymarketLiveBroker.
    return _build_broker_for_division(div, _secrets(), "LIVE", ["polymarket"], {div.slug})


def test_order_type_reaches_broker():
    b = _build_live(_poly_div(order_type="fok"))
    assert isinstance(b, PolymarketLiveBroker)
    assert b._order_type == "fok"


def test_fak_poll_seconds_reaches_broker():
    b = _build_live(_poly_div(fak_poll_seconds=12))
    assert b._fak_poll_seconds == 12.0


def test_unset_division_gets_ctor_defaults():
    # Neither key set ⇒ exec_kwargs empty ⇒ broker ctor defaults (pre-E5a behavior).
    b = _build_live(_poly_div())
    assert b._order_type == _SYNTH_FAK
    assert b._fak_poll_seconds == float(_DEFAULT_FAK_POLL_SECONDS)


def test_division_isolation_of_values():
    a = _build_live(_poly_div(slug="pct_a", order_type="gtc", fak_poll_seconds=3))
    b = _build_live(_poly_div(slug="pct_b", order_type="fok", fak_poll_seconds=9))
    assert (a._order_type, a._fak_poll_seconds) == ("gtc", 3.0)
    assert (b._order_type, b._fak_poll_seconds) == ("fok", 9.0)


def test_migration_not_stranded():
    # (a) divisions.yaml carries the keys AND load_divisions maps them onto the
    #     Division (the explicit-mapping checkpoint — a dataclass-only field would
    #     silently never flow).
    divs = {d.slug: d for d in load_divisions(_REPO / "config" / "divisions.yaml")}
    pct = divs["polymarket_copy_trading"]
    assert pct.order_type == "fak_synth"
    assert pct.fak_poll_seconds == 5.0
    assert pct.exit_chase is None  # E5b attachment point, unset now
    # (b) the keys were REMOVED from the strategies.yaml strategy block (no dual
    #     source to drift). The strategy never read them, so nothing is stranded.
    strat = yaml.safe_load((_REPO / "config" / "strategies.yaml").read_text(encoding="utf-8"))
    pct_strat = strat["polymarket_copy_trader"]
    assert "order_type" not in pct_strat
    assert "fak_poll_seconds" not in pct_strat


def test_bitunix_family_inert_to_new_fields(monkeypatch):
    # A bitunix division carrying the new fields ⇒ BitunixBroker built normally; the
    # polymarket execution fields never reach it (construction gates on family).
    captured = {}

    class _SpyBitunix:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("trading_corp.brokers.bitunix.BitunixBroker", _SpyBitunix)
    div = SimpleNamespace(
        broker="bitunix", slug="bitunix_futures", account_filter="futures",
        order_type="fok", fak_poll_seconds=99, exit_chase={"x": 1},
    )
    secrets = SimpleNamespace(bitunix_futures_api_key="k", bitunix_futures_api_secret="s")
    b = _build_broker_for_division(div, secrets, "LIVE", ["bitunix"], {"bitunix_futures"})
    assert isinstance(b, _SpyBitunix)
    assert set(captured) == {"api_key", "api_secret", "logger"}
    assert "order_type" not in captured and "fak_poll_seconds" not in captured
