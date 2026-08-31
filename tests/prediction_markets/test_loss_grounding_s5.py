"""Stage 5 loss-grounding (Analyze input integrity). Proves the ported loss-visibility method: TRADE BUY/SELL ->
held-to-resolution, GAMMA as the resolution authority, A_only = the losses /closed-positions DROPPED, the HONEST
win/loss = /closed-positions UNION A_only, and a MEASURED completeness bound when /activity truncates. Fixtures use
the REAL ActivityRow/ClosedPositionRow (from_api) so the adapters run against the true field shapes."""
import pytest

from trading_corp.prediction_markets import loss_grounding as LG
from trading_corp.data.polymarket_data_api_client import ActivityRow, ClosedPositionRow


def _act(cid, oidx, side, size, typ="TRADE"):
    return ActivityRow.from_api({"conditionId": cid, "outcomeIndex": oidx, "side": side, "size": size,
                                 "type": typ, "proxyWallet": "0xW"})


def _closed(cid, oidx, cur):
    return ClosedPositionRow.from_api({"conditionId": cid, "outcomeIndex": oidx, "curPrice": cur, "proxyWallet": "0xW"})


def _res(win_idx, status="resolved"):
    return {"status": status, "winning_outcome_index": win_idx}


# ── activity_decisions: held test + TRADE filter ──────────────────────────────
def test_activity_decisions_held_roundtrip_unresolved():
    rows = [_act("c_held", 0, "BUY", 10),                              # held long -> held
            _act("c_rt", 0, "BUY", 5), _act("c_rt", 0, "SELL", 5),     # round-trip -> NOT held
            _act("c_unres", 0, "BUY", 5),                              # held but unresolved
            _act("c_redeem", 0, "BUY", 5, typ="REDEEM")]               # non-TRADE -> ignored
    res = {"c_held": _res(1), "c_rt": _res(1), "c_unres": _res(0, status="active")}
    d = LG.activity_decisions(rows, res)
    assert d[("c_held", 0)] == {"resolved": True, "won": False, "held": True}    # oidx0 lost (winner=1)
    assert d[("c_rt", 0)]["held"] is False                                       # net 0 -> not held
    assert d[("c_unres", 0)]["resolved"] is False
    assert ("c_redeem", 0) not in d                                              # REDEEM row not counted


def test_held_floor_is_material_net_long():
    # net long must exceed max(0.5, 1% of buy): 100 bought, 99.5 sold -> net 0.5 is NOT > max(0.5, 1.0) -> not held
    d = LG.activity_decisions([_act("c", 0, "BUY", 100), _act("c", 0, "SELL", 99.5)], {"c": _res(1)})
    assert d[("c", 0)]["held"] is False
    d2 = LG.activity_decisions([_act("c", 0, "BUY", 100), _act("c", 0, "SELL", 98)], {"c": _res(1)})
    assert d2[("c", 0)]["held"] is True                                          # net 2 > 1% of 100


# ── ground_losses: the A_only omitted set + honest counts ─────────────────────
def test_ground_losses_recovers_the_omitted_losses():
    # /closed-positions: 1 loss (in_both) + 1 win. /activity held+resolved: the same loss + a DROPPED loss + a
    # DROPPED win. A_only = {dropped loss, dropped win}; honest = closed + A_only.
    activity = [_act("in_both", 0, "BUY", 10),                        # also in closed (loss) -> not A_only
                _act("drop_loss", 0, "BUY", 5),                       # held loss ABSENT from closed -> A_only loss
                _act("drop_win", 0, "BUY", 5),                        # held win  ABSENT from closed -> A_only win
                _act("rt", 0, "BUY", 5), _act("rt", 0, "SELL", 5)]    # round-trip -> excluded
    closed = [_closed("in_both", 0, 0.0),                             # a LOSS /closed-positions DID report
              _closed("cw", 1, 1.0)]                                  # a WIN /closed-positions reported
    res = {"in_both": _res(1), "drop_loss": _res(1), "drop_win": _res(0), "rt": _res(1)}
    g = LG.ground_losses(activity, closed, res, activity_truncated=False)
    assert g.closed_wins == 1 and g.closed_losses == 1                # what Analyze reports today
    assert g.a_only_losses == 1 and g.a_only_wins == 1                # the DROPPED decisions recovered
    assert g.honest_wins == 2 and g.honest_losses == 2               # closed UNION A_only
    assert g.loss_omission_pct == 0.5                                # 1 of 2 honest losses was omitted
    assert g.n_activity_held_resolved == 3
    assert g.activity_truncated is False and g.completeness.startswith("complete")


def test_truncation_stamps_a_lower_bound_completeness():
    g = LG.ground_losses([_act("d", 0, "BUY", 5)], [], {"d": _res(1)}, activity_truncated=True)
    assert g.a_only_losses == 1 and g.activity_truncated is True
    assert "windowed" in g.completeness and "lower bound" in g.completeness   # honest about the bound


def test_no_losses_gives_none_omission_pct():
    g = LG.ground_losses([_act("w", 0, "BUY", 5)], [], {"w": _res(0)}, activity_truncated=False)
    assert g.honest_losses == 0 and g.loss_omission_pct is None and g.honest_wins == 1


def test_closed_decisions_winner_convention():
    d = LG.closed_decisions([_closed("a", 0, 0.95), _closed("b", 1, 0.10)])
    assert d[("a", 0)]["won"] is True and d[("b", 1)]["won"] is False           # cur_price >= 0.9 = win


# ── R2a: the async orchestrator (page + category-filter + ground) ─────────────
def _act_slug(cid, oidx, side, size, slug, typ="TRADE"):
    return ActivityRow.from_api({"conditionId": cid, "outcomeIndex": oidx, "side": side, "size": size,
                                 "type": typ, "slug": slug, "proxyWallet": "0xW"})


class _FakeClient:
    """Duck-types PolymarketDataAPIClient's three readers. activity/closed are LISTS OF PAGES (paged by offset)."""
    def __init__(self, activity_pages, closed_pages, resolutions):
        self._a = activity_pages; self._c = closed_pages; self._r = resolutions
    async def fetch_activity(self, wallet, *, limit, offset):
        i = offset // limit; return list(self._a[i]) if i < len(self._a) else []
    async def fetch_closed_positions(self, wallet, *, limit, offset):
        i = offset // limit; return list(self._c[i]) if i < len(self._c) else []
    async def fetch_market_resolutions(self, cids, **kw):
        return {cid: self._r[cid] for cid in cids if cid in self._r}


def _cat_of(r):                                                                 # derive category from the slug prefix
    s = str(getattr(r, "slug", "") or ""); return s.split("-")[0] if s else ""


@pytest.mark.asyncio
async def test_orchestrator_category_filter_and_grounding():
    # activity: an mlb held LOSS absent from closed (a_only) + an nba row that MUST be filtered out
    a = [[_act_slug("c_mlb", 0, "BUY", 5, "mlb-x"), _act_slug("c_nba", 0, "BUY", 5, "nba-y")]]
    fake = _FakeClient(a, [[]], {"c_mlb": _res(1), "c_nba": _res(1)})            # both would be losses if counted
    g = await LG.fetch_and_ground_losses(fake, "0xW", "mlb", category_of=_cat_of)
    assert g.a_only_losses == 1 and g.honest_losses == 1                        # ONLY the mlb row; nba excluded
    assert g.activity_truncated is False


@pytest.mark.asyncio
async def test_orchestrator_flags_truncation_at_the_page_ceiling():
    # 2 FULL pages of `limit` rows with max_pages=2 -> the page ceiling is hit -> truncated=True
    full = [_act_slug("c%d" % i, 0, "BUY", 5, "mlb-x") for i in range(3)]       # limit=3 -> a full page
    fake = _FakeClient([full, full], [[]], {("c%d" % i): _res(1) for i in range(3)})
    g = await LG.fetch_and_ground_losses(fake, "0xW", "mlb", category_of=_cat_of,
                                         activity_max_pages=2, activity_limit=3)
    assert g.activity_truncated is True and "windowed" in g.completeness
