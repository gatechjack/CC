"""Regression guard (2026-09-14): a ctx builder that references a matcher alias NOT imported at
live_driver module scope compiles fine and imports fine (the NameError is inside a function only
reached at engine boot with pykalshi), so py_compile + `import live_driver` + the offline dry-run
all MISS it. This test STUBS pykalshi + a fake client and actually CALLS the boxing and F1 ctx
builders, asserting they resolve their matcher imports and build a MarketContext without NameError.
Found live for F1 by an adversarial skeptic (fetch_f1_market_context used F1X, never imported)."""
import sys
import types
import asyncio


class _FakeMarket:
    def __init__(self, ticker, yes_sub_title, title=None, close_time=None):
        self.ticker = ticker
        self.yes_sub_title = yes_sub_title
        self.title = title
        self.close_time = close_time
        # quote fields _market_quote_dict reads (all optional / None-tolerant)
        self.yes_ask_dollars = self.no_ask_dollars = self.yes_bid_dollars = self.no_bid_dollars = None
        self.liquidity_dollars = None
        self.exchange_index = 0


class _FakeClient:
    def __init__(self, by_series):
        self._by_series = by_series

    async def get_markets(self, series_ticker=None, status=None, limit=None, fetch_all=None, **kw):
        # return the OPEN set once; SETTLED empty (status object identity is irrelevant to the builder)
        st = getattr(status, "name", str(status))
        return self._by_series.get(series_ticker, []) if "OPEN" in st.upper() else []

    def get(self, path):   # _merge_raw_market_fields raw GET -> empty (fields absent -> gates fail-close, fine here)
        return {}


def _with_stub_pykalshi(fn):
    saved = sys.modules.get("pykalshi")
    stub = types.ModuleType("pykalshi")

    class MarketStatus:
        OPEN = types.SimpleNamespace(name="OPEN")
        SETTLED = types.SimpleNamespace(name="SETTLED")
    stub.MarketStatus = MarketStatus
    sys.modules["pykalshi"] = stub
    try:
        return fn()
    finally:
        if saved is not None:
            sys.modules["pykalshi"] = saved
        else:
            sys.modules.pop("pykalshi", None)


def test_f1_ctx_builder_resolves_imports_and_builds():
    from trading_corp.prediction_markets import live_driver as LD
    client = _FakeClient({"KXF1RACE": [
        _FakeMarket("KXF1RACE-ITAGP26-VER", "Max Verstappen", close_time="2026-09-06T19:08:43Z"),
        _FakeMarket("KXF1RACE-ITAGP26-GAS", "Pierre Gasly", close_time="2026-09-06T19:08:43Z"),
    ]})
    ctx = _with_stub_pykalshi(lambda: asyncio.run(LD.fetch_f1_market_context(client, 1_800_000_000)))
    assert ctx.f1_race_index and "2026-09-06" in ctx.f1_race_index          # F1X import resolved + index built
    assert len(ctx.f1_race_index["2026-09-06"].drivers) == 2
    # every OTHER index slot stays at its default (non-F1 ctx byte-identical)
    assert ctx.boxing_index is None and ctx.match_index is None and ctx.fight_index is None


def test_boxing_ctx_builder_resolves_imports_and_builds():
    from trading_corp.prediction_markets import live_driver as LD
    client = _FakeClient({"KXBOXING": [
        _FakeMarket("KXBOXING-26SEP12GARCIAMORALE-GARCIA", "Sean Garcia", title="Sean Garcia wins"),
        _FakeMarket("KXBOXING-26SEP12GARCIAMORALE-MORALE", "Abraham Morales", title="Abraham Morales wins"),
    ]})
    ctx = _with_stub_pykalshi(lambda: asyncio.run(LD.fetch_boxing_market_context(client, 1_800_000_000)))
    assert ctx.boxing_index and "2026-09-12" in {k[0] for k in ctx.boxing_index}   # BX import resolved + index built
    assert ctx.f1_race_index is None
