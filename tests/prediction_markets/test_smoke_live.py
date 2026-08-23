"""Opt-in live-API smoke (G0 + ordering probe), READ-ONLY, no DB writes. Skipped unless
PM_LIVE_API=1 (so the normal offline suite never hits the network). Spec: §11.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("PM_LIVE_API") != "1",
    reason="opt-in live-API smoke; set PM_LIVE_API=1 to run",
)


async def test_g0_live_probe_and_ordering():
    from trading_corp.data.polymarket_data_api_client import PolymarketDataAPIClient
    from trading_corp.prediction_markets import ingest, rosters
    async with PolymarketDataAPIClient() as c:
        res = await ingest.g0_validate(c, rosters.G0_KNOWN_LOSERS)
    assert res["passed"] is True, res
    w = rosters.G0_KNOWN_LOSERS[0]["wallet"]
    async with PolymarketDataAPIClient() as c:
        a = await c.fetch_closed_positions(w, limit=50, offset=0)
        b = await c.fetch_closed_positions(w, limit=50, offset=0)
    assert [x.condition_id for x in a] == [x.condition_id for x in b]  # ordering stable
