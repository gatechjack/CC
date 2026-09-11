"""Regression test for the OPEN-market ctx TRUNCATION fix (2026-09-11).

THE DEFECT (measured, moneyline-controlled): fetch_structural_market_context fetched OPEN markets at limit=1000
with fetch_all=False (single page) while paginating SETTLED. A high-cardinality series (CFB total 2008 / spread
2541 open) was truncated -> markets past the first 1000 were ABSENT from the index -> the matcher returned
no_kalshi_strike upstream of every gate. A SECOND truncation point: _merge_raw_market_fields fetched the raw
shard/size payload with the same single limit=1000 GET -> a page-2 market that DID reach the index got NO
exchange_index / yes_ask_size_fp merged -> gate 6b (shard) + gate 3 (depth) fail-closed on it (skip:illiquid/shard)
-- so fixing only the index would trade one failure for another.

THE FIX paginates BOTH OPEN fetches. This test proves, with a fake client that pages a >1000 catalog:
  (1) a page-2 total ticker lands in ctx.total_index (index pagination);
  (2) that page-2 market carries exchange_index + yes_ask_size_fp (raw-merge pagination);
  (3) a page-1 game ticker (the CONTROL, a <=1000 series -- the moneyline that already worked) is unchanged;
  (4) the OPEN get_markets call is now fetch_all=True (the fix), and pagination is load-bearing (page-2 target
      is INVISIBLE to a single non-paginated page).
Offline; no network, no DB.
"""
import asyncio

from trading_corp.prediction_markets import live_driver as LD
from trading_corp.data import sports_structural_match as SS

CFG = SS.LEAGUES["cfb"]
STEM = "26SEP11TESTGM"                       # a synthetic game stem shared by game + total tickers
GAME_TK = "KXNCAAFGAME-%s-AAA" % STEM        # control: game (moneyline) series, small (page 1)
TOTAL_P2 = "KXNCAAFTOTAL-%s-52" % STEM       # TARGET: total strike 51.5, placed on PAGE 2 (index 1000)


class _Mkt:
    """Minimal stand-in for a pykalshi get_markets object (only the attrs _market_quote_dict reads)."""
    def __init__(self, ticker):
        self.ticker = ticker
        self.yes_ask_dollars = 0.51; self.no_ask_dollars = 0.50
        self.yes_bid_dollars = 0.50; self.no_bid_dollars = 0.49
        self.liquidity_dollars = 0.0


def _open_catalog(series):
    if series == CFG.game_series:
        return [_Mkt(GAME_TK)]                                             # control: 1 market, page 1
    if series == CFG.total_series:
        pad = [_Mkt("KXNCAAFTOTAL-26SEP11PAD%04d-52" % i) for i in range(1000)]   # 1000 fillers -> page 1
        return pad + [_Mkt(TOTAL_P2)]                                      # target at index 1000 -> PAGE 2
    return []                                                             # spread: empty (fine)


class FakeClient:
    """get_markets: single page unless fetch_all (mirrors pykalshi). get: raw paginated payload w/ exchange_index+size."""
    def __init__(self):
        self.open_fetch_all = []

    async def get_markets(self, series_ticker, status, limit=1000, fetch_all=False, **extra):
        cat = _open_catalog(series_ticker) if str(status).endswith("OPEN") else []
        if str(status).endswith("OPEN"):
            self.open_fetch_all.append(fetch_all)
        return cat if fetch_all else cat[:limit]                          # NON-paginated = page 1 only

    def get(self, path):
        # raw /markets?series_ticker=..&status=open&limit=1000[&cursor=..] -> {markets, cursor}. Paginate 1000/page.
        import urllib.parse as up
        q = up.parse_qs(up.urlparse(path).query)
        series = q.get("series_ticker", [""])[0]
        cursor = q.get("cursor", [""])[0]
        cat = _open_catalog(series)
        raws = [{"ticker": m.ticker, "exchange_index": 1, "yes_ask_size_fp": "5000", "yes_bid_size_fp": "4000"}
                for m in cat]
        start = int(cursor) if cursor else 0
        page = raws[start:start + 1000]
        nxt = start + 1000
        return {"markets": page, "cursor": (str(nxt) if nxt < len(raws) else "")}


def test_open_pagination_indexes_and_merges_page2_market():
    fake = FakeClient()
    ctx = asyncio.run(LD.fetch_structural_market_context(fake, 1789200000, CFG))
    # (4) the fix: OPEN was fetched with fetch_all=True (pagination on)
    assert fake.open_fetch_all and all(fake.open_fetch_all), fake.open_fetch_all
    # (1) index pagination: the page-2 total strike is indexed
    assert STEM in ctx.total_index, "page-2 total stem missing from total_index (index truncated)"
    assert 51.5 in ctx.total_index[STEM], ctx.total_index.get(STEM)
    assert ctx.total_index[STEM][51.5] == TOTAL_P2
    # (2) raw-merge pagination: the page-2 market carries shard + size (else gate 6b/3 fail-closed)
    m = ctx.markets.get(TOTAL_P2)
    assert m is not None, "page-2 total absent from ctx.markets"
    assert m.get("exchange_index") == 1, "exchange_index NOT merged for page-2 market (gate 6b would fail-closed)"
    assert m.get("yes_ask_size_fp") == "5000", "yes_ask_size_fp NOT merged for page-2 market (gate 3 would fail-closed)"
    # (3) control: the small (page-1) game series is still fetched + raw-merged, UNCHANGED by the fix. (Membership in
    # structural_index is keyed by (date, team-set) and would need real CFB team codes; the LIVE box-scratch acceptance
    # proves the game INDEX + matcher resolve on the real catalog. Here the control proves the small series -- the one
    # that already worked -- is still fully fetched and raw-merged, i.e. the fix is a no-op for a <=1000 series.)
    assert GAME_TK in ctx.markets, "control game absent from ctx.markets (small page-1 series not fetched)"
    assert ctx.markets.get(GAME_TK, {}).get("exchange_index") == 1, "control game exchange_index not merged"


def test_pagination_is_load_bearing_page2_invisible_without_it():
    """Proof the fix matters: a single non-paginated page (the OLD behaviour) cannot see the page-2 target."""
    fake = FakeClient()
    page1_only = asyncio.run(fake.get_markets(CFG.total_series, "MarketStatus.OPEN", limit=1000, fetch_all=False))
    tickers = [m.ticker for m in page1_only]
    assert TOTAL_P2 not in tickers, "target was in page 1 -- test no longer exercises truncation"
    assert len(tickers) == 1000
