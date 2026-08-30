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
    assert b.can_fund(3, 5.0) is False                         # REQUIRED source (shard): NO
    old_reader_would_place = b.total_dollars >= 5.0            # WRONG source (masked total): would have said YES
    assert old_reader_would_place is True                      # ... the exact silent-failure the old reader shipped
    assert b.total_dollars > 5.0


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


# ── review-hardening: fail-loud on corruption; the tri-state safe pattern; subaccount-scoped detection ──

def test_duplicate_exchange_index_raises():
    # two entries for shard 3 -> silent last-wins would report a WRONG (empty) shard -> must RAISE
    with pytest.raises(ValueError, match="duplicate"):
        sb.parse_balance({"balance": 100, "balance_breakdown": [
            {"exchange_index": 3, "balance": "509.80"}, {"exchange_index": 3, "balance": "0.00"}]})


def test_non_integer_exchange_index_raises():
    # int(3.7)->3 (wrong shard) and "3" (string) are corruption for a load-bearing read -> RAISE, not coerce
    for bad_idx in (3.7, 3.0, "3", True):
        with pytest.raises(ValueError):
            sb.parse_balance({"balance": 100, "balance_breakdown": [{"exchange_index": bad_idx, "balance": "1.0"}]})


def test_non_finite_balance_raises():
    # 'Infinity' would make can_fund True for ANY need; 'NaN' poisons comparisons -> both must RAISE (via None)
    for bad in ("Infinity", "-Infinity", "NaN", float("inf"), float("nan")):
        with pytest.raises(ValueError):
            sb.parse_balance({"balance": 100, "balance_breakdown": [{"exchange_index": 3, "balance": bad}]})


def test_non_list_breakdown_raises():
    # a dict-wrapped breakdown (API schema drift) must be LOUD, not silently has_breakdown=False
    with pytest.raises(ValueError, match="not a list"):
        sb.parse_balance({"balance": 100, "balance_breakdown": {"shards": []}})


def test_non_dict_response_raises():
    class _Resp:  # e.g. a raw requests/httpx Response reaching parse by mistake
        def get(self, *a):
            return "wrong"
    with pytest.raises(TypeError):
        sb.parse_balance(_Resp())


def test_can_fund_negative_need_raises_zero_need_is_true():
    b = sb.parse_balance(JACK)
    with pytest.raises(ValueError):
        b.can_fund(3, -0.01)                       # a negative order size is an upstream sign bug -> LOUD
    assert b.can_fund(3, 0.0) is True              # zero need is degenerate-but-harmless


def test_none_from_can_fund_is_falsy_and_is_not_true():
    # pin the rung-2 safe gate: unknown split -> None; None is falsy AND `is not True` -> never place blind
    b = sb.parse_balance({"balance": 100})         # no breakdown -> unknown
    r = b.can_fund(3, 5.0)
    assert r is None and (not r) and (r is not True)


def test_shard_absent_from_known_breakdown_is_zero_not_none():
    # a shard not listed in a KNOWN breakdown is $0 (Kalshi lists every shard), NOT unknown
    b = sb.parse_balance({"balance_dollars": "515.42", "balance_breakdown": [{"exchange_index": 0, "balance": "515.42"}]})
    assert b.has_breakdown is True
    assert b.shard(3) == 0.0 and b.can_fund(3, 1.0) is False


def test_shard_sum_can_diverge_from_total_subaccount_scoped():
    # a subaccount-scoped read: breakdown shows $10 but the total field says $515 -> the gap is detectable
    b = sb.parse_balance({"balance_dollars": "515.42", "balance_breakdown": [{"exchange_index": 3, "balance": "10.00"}]})
    assert b.total_dollars == pytest.approx(515.42) and b.shard_sum() == pytest.approx(10.00)
    assert abs(b.shard_sum() - b.total_dollars) > 100.0
