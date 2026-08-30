"""Shard money-mgmt RUNG 1 -- the shard-aware balance READ (Option B). The old reader returned the MASKED TOTAL
(bal.balance/100) and ignored `balance_breakdown`; a healthy total with an empty market-shard is the exact state
that silently killed legacy poly_kalshi_mlb for two days. These tests pin, per R4 (requirements-as-tests): the
REQUIRED value (the per-SHARD balance) != the WRONG value (the masked total), and the read surfaces the SHARD. Plus
the fail-safe when the split is UNKNOWN, dollar-string vs integer-cents parsing, STRICT malformed-entry raising, and
the async fetch wrapper against both a sync-.get and an async-.get client. Offline; pure; no order-path import."""
import asyncio

import pytest

from trading_corp.prediction_markets import shard_balance as sb

# The ACTUAL kalshi_jack response shape (R7 probe 2026-08-30T17:18Z): total ~$509.81, all on shard 3.
JACK = {"balance": 50981, "balance_dollars": "509.8121",
        "balance_breakdown": [{"balance": "0.0081", "exchange_index": 0}, {"balance": "0.0000", "exchange_index": 1},
                              {"balance": "0.0000", "exchange_index": 2}, {"balance": "509.8040", "exchange_index": 3}],
        "portfolio_value": 0, "updated_ts": 1788110325}

# ★ KAREN'S SILENT-DEATH SHAPE: a healthy $515 TOTAL masking an empty market-shard (only $2.45 on shard 3).
KAREN_DEATH = {"balance": 51542, "balance_dollars": "515.4200",
               "balance_breakdown": [{"balance": "512.9700", "exchange_index": 0}, {"balance": "0.0000", "exchange_index": 1},
                                     {"balance": "0.0000", "exchange_index": 2}, {"balance": "2.4500", "exchange_index": 3}],
               "updated_ts": 1788000000}


def test_parses_the_real_jack_shape():
    b = sb.parse_balance(JACK)
    assert b.has_breakdown is True
    assert b.shard(3) == pytest.approx(509.8040)
    assert b.shard(0) == pytest.approx(0.0081)
    assert b.shard(1) == 0.0 and b.shard(2) == 0.0
    assert b.total_dollars == pytest.approx(509.8121)          # from balance_dollars, NOT balance/100
    assert b.updated_ts == 1788110325
    assert b.shard_sum() == pytest.approx(509.8121, abs=1e-3)  # shards ~ total


# ★★ THE LOAD-BEARING TEST: the required value (shard-3) != the wrong value (the masked total).
def test_karen_death_the_masked_total_is_not_the_shard():
    b = sb.parse_balance(KAREN_DEATH)
    assert b.total_dollars == pytest.approx(515.42)            # the healthy-LOOKING total
    assert b.shard(3) == pytest.approx(2.45)                   # the REAL fundable balance for an MLB order
    assert b.total_dollars != b.shard(3)                       # they DIFFER -- the whole point of this rung
    # a ~$5 MLB order routes to shard 3 and CANNOT be funded there, though the total is 100x it:
    assert b.can_fund(3, 5.0) is False
    assert b.total_dollars > 5.0                               # ... the masked total would have said "fine"


def test_can_fund_boundary():
    b = sb.parse_balance(JACK)
    assert b.can_fund(3, 509.8040) is True                     # exactly equal -> fundable (epsilon)
    assert b.can_fund(3, 509.81) is False                      # just above shard-3 -> not
    assert b.can_fund(0, 2.0) is False                         # shard 0 holds $0.008
    assert b.can_fund(3, 0.55) is True                         # a 1-contract order easily funds


# ★ FAIL-SAFE: no breakdown (subaccount-restricted key) -> split UNKNOWN -> shard()/can_fund() return None.
def test_no_breakdown_is_unknown_not_zero_and_not_total():
    b = sb.parse_balance({"balance": 51542, "balance_dollars": "515.42"})   # no balance_breakdown key
    assert b.has_breakdown is False
    assert b.total_dollars == pytest.approx(515.42)
    assert b.shard(3) is None                                  # UNKNOWN -- NOT 0 and NOT the total
    assert b.can_fund(3, 5.0) is None                          # caller MUST fail-safe on None (do not place)
    assert b.shard_sum() is None


def test_empty_breakdown_list_is_unknown():
    b = sb.parse_balance({"balance": 100, "balance_breakdown": []})
    assert b.has_breakdown is False and b.shard(3) is None


def test_total_falls_back_to_cents_when_no_balance_dollars():
    b = sb.parse_balance({"balance": 50981, "balance_breakdown": [{"balance": "509.8100", "exchange_index": 3}]})
    assert b.total_dollars == pytest.approx(509.81)            # 50981 cents / 100
    assert b.shard(3) == pytest.approx(509.81)


def test_malformed_breakdown_entry_raises():
    for bad in ([{"balance": "1.0"}],                          # missing exchange_index
                [{"exchange_index": 3}],                        # missing balance
                [{"exchange_index": 3, "balance": "abc"}],      # unparseable balance
                ["notadict"]):                                  # entry not a dict
        with pytest.raises(ValueError):
            sb.parse_balance({"balance": 100, "balance_breakdown": bad})


def test_empty_or_none_response():
    for r in (None, {}):
        b = sb.parse_balance(r)
        assert b.has_breakdown is False and b.total_dollars == 0.0 and b.shard(3) is None


# ── the async fetch wrapper: a sync-.get and an async-.get client both parse (asyncio.run; no pytest-asyncio dep) ──
class _SyncClient:
    def __init__(self, resp): self._r = resp
    def get(self, path):
        assert path == "/portfolio/balance"
        return self._r


class _AsyncClient:
    def __init__(self, resp): self._r = resp
    async def get(self, path):
        assert path == "/portfolio/balance"
        return self._r


def test_fetch_async_client():
    b = asyncio.run(sb.fetch_shard_balances(_AsyncClient(JACK)))
    assert b.shard(3) == pytest.approx(509.8040)


def test_fetch_sync_client():
    b = asyncio.run(sb.fetch_shard_balances(_SyncClient(JACK)))
    assert b.shard(3) == pytest.approx(509.8040)
