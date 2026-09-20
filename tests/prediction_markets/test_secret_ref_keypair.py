"""Fail-closed whitelist coverage for shard_snapshot_task.resolve_kalshi_keys.

This whitelist (`_SECRET_REF_KEYPAIR`) is the load-bearing per-account credential
router: the PM live driver (main.py:1477) AND the M3 shard-snapshot writer
(main.py:1549) both resolve each `pm_account`'s Kalshi keys through it. It is
DELIBERATELY fail-CLOSED: an unmapped secret_ref -> (None, None) -> that account
is SKIPPED, never traded on the shared (jack) keypair. These tests lock that
property AND assert the third account (`kalshi_marc`, 2026-09-20) resolves to its
OWN isolated keypair -- never jack's. There was no prior test on this file.
"""
from __future__ import annotations

from types import SimpleNamespace

from trading_corp.prediction_markets.shard_snapshot_task import (
    _SECRET_REF_KEYPAIR,
    resolve_kalshi_keys,
)


def _fake_secrets():
    """Stand-in Secrets with a DISTINCT sentinel per Kalshi field, so a
    mis-route (marc -> jack's keys) is caught by VALUE, not just presence."""
    return SimpleNamespace(
        kalshi_api_key_id="JACK_KID",
        kalshi_private_key_pem="JACK_PEM",
        kalshi_karen_api_key_id="KAREN_KID",
        kalshi_karen_private_key_pem="KAREN_PEM",
        kalshi_marc_api_key_id="MARC_KID",
        kalshi_marc_private_key_pem="MARC_PEM",
    )


def test_marc_resolves_to_his_own_keypair_not_jacks():
    kid, pem = resolve_kalshi_keys("kalshi_marc", _fake_secrets())
    assert (kid, pem) == ("MARC_KID", "MARC_PEM")
    # The exact failure this whole sequence exists to prevent: marc on jack's keys.
    assert kid != "JACK_KID" and pem != "JACK_PEM"


def test_marc_is_in_the_whitelist_pointing_at_marc_fields():
    assert _SECRET_REF_KEYPAIR["kalshi_marc"] == (
        "kalshi_marc_api_key_id",
        "kalshi_marc_private_key_pem",
    )


def test_existing_accounts_unchanged():
    s = _fake_secrets()
    assert resolve_kalshi_keys("kalshi_karen", s) == ("KAREN_KID", "KAREN_PEM")
    assert resolve_kalshi_keys("KALSHI", s) == ("JACK_KID", "JACK_PEM")
    assert resolve_kalshi_keys("kalshi_jack", s) == ("JACK_KID", "JACK_PEM")


def test_unmapped_ref_fails_closed():
    """The single most important property: an unknown / typo'd / not-yet-wired
    ref returns (None, None) so the caller SKIPS -- never routes to jack.
    'kalshi_trey' is here on purpose: Trey is fail-closed until his own code
    change lands (he is NOT pure data under the karen-mirror shape)."""
    for bad in ("kalshi_marcc", "kalshi_trey", "marc", "", "KALSHI_MARC", None):
        assert resolve_kalshi_keys(bad, _fake_secrets()) == (None, None)


def test_recognised_ref_with_absent_field_still_fails_closed():
    """A whitelisted ref whose backing Secrets field is absent -> None
    (getattr default) -> caller skips (e.g. marc mapped but his KV secret
    never loaded). Fails CLOSED, never falls back to jack."""
    partial = SimpleNamespace(kalshi_api_key_id="JACK_KID", kalshi_private_key_pem="JACK_PEM")
    assert resolve_kalshi_keys("kalshi_marc", partial) == (None, None)
