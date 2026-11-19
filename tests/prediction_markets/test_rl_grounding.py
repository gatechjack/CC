"""RL-supplement loss grounding (2026-11-19): /v2/positions?status=REDEEMABLE_LOST unioned into the loss side,
compared against the FULL live CLOSED walk (Jack ruling 2026-11-19). Pure-logic tests over ground_losses /
rl_decisions / honest_windowed_roi / assign_tier, plus fake-client orchestrator tests proving (a) the live
closed walk feeds recent redeemed WINS into the basis (the stale-vs-live test), (b) a legacy fake client
(no RL surface) gets byte-identical pre-RL behaviour."""
from types import SimpleNamespace

import pytest

from trading_corp.prediction_markets import analyze, loss_grounding, scoring


# ── row fixtures (duck-typed like the client dataclasses) ────────────────────────────────────────────
def act(cid, oi, side, size, usd=None, typ="TRADE", slug="s", event_slug="e"):
    return SimpleNamespace(type=typ, condition_id=cid, outcome_index=oi, side=side, size=size,
                           usdc_size=(usd if usd is not None else size * 0.5), slug=slug, event_slug=event_slug)


def closed(cid, oi, cur, tb=100.0, avg=0.5, pnl=0.0, slug="s", event_slug="e"):
    return SimpleNamespace(condition_id=cid, outcome_index=oi, cur_price=cur, total_bought=tb,
                           avg_price=avg, realized_pnl=pnl, slug=slug, event_slug=event_slug)


def rl(cid, oi, cost=50.0, cur_value=0.0, slug="s", event_slug="e"):
    return SimpleNamespace(condition_id=cid, extra={"outcomeIndex": oi, "curPrice": 0.0, "redeemable": True},
                           initial_value=cost, current_value=cur_value, size=cost * 2,
                           slug=slug, event_slug=event_slug)


def resolved(win_oi):
    return {"status": "resolved", "winning_outcome_index": win_oi}


# ── ground_losses with rl_rows ───────────────────────────────────────────────────────────────────────
def test_rl_loss_added_to_a_only():
    """An RL loser the /activity window never saw and closed never had -> +1 a_only loss, attributed to RL."""
    acts = [act("c1", 0, "BUY", 100)]                      # held loss found by activity
    res = {"c1": resolved(1), "c9": resolved(1)}
    g = loss_grounding.ground_losses(acts, [], res, activity_truncated=True,
                                     rl_rows=[rl("c9", 0)], loss_side_complete=True)
    assert g.a_only_losses == 2 and g.rl_added_losses == 1
    assert g.honest_losses == 2 and g.loss_basis == "activity+rl"
    assert g.loss_side_complete is True and "+rl" in g.completeness
    assert g.rl_false_losses == 0


def test_rl_closed_overlap_no_double_count():
    """An RL key already in the closed feed never enters A_only (the union excludes closed keys)."""
    res = {"c1": resolved(1)}
    g = loss_grounding.ground_losses([], [closed("c1", 0, 0.02)], res, activity_truncated=False,
                                     rl_rows=[rl("c1", 0)], loss_side_complete=True)
    assert g.closed_losses == 1 and g.a_only_losses == 0 and g.rl_added_losses == 0
    assert g.honest_losses == 1


def test_rl_activity_overlap_counted_once():
    """A loss found by BOTH the activity window and RL counts once; rl_added stays 0 (RL added nothing new)."""
    acts = [act("c1", 0, "BUY", 100)]
    res = {"c1": resolved(1)}
    g = loss_grounding.ground_losses(acts, [], res, activity_truncated=False,
                                     rl_rows=[rl("c1", 0)], loss_side_complete=True)
    assert g.a_only_losses == 1 and g.rl_added_losses == 0 and g.honest_losses == 1


def test_gamma_won_rl_row_excluded_and_counted():
    """A FALSE LOSS (RL row gamma says WON) is dropped -- never a loss -- and lands in the telemetry field."""
    res = {"c1": resolved(0)}                              # oi 0 WON; the RL row claims oi 0 lost
    g = loss_grounding.ground_losses([], [], res, activity_truncated=False,
                                     rl_rows=[rl("c1", 0)], loss_side_complete=True)
    assert g.a_only_losses == 0 and g.rl_added_losses == 0
    assert g.rl_false_losses == 1


def test_rl_unresolved_row_dropped():
    """Gamma unresolved/void/not_found -> the RL row is NOT counted (grounding only adds provable losses)."""
    g = loss_grounding.ground_losses([], [], {"c1": {"status": "void"}}, activity_truncated=False,
                                     rl_rows=[rl("c1", 0)], loss_side_complete=True)
    assert g.a_only_losses == 0 and g.rl_added_losses == 0 and g.rl_false_losses == 0


def test_partial_sell_remainder_stays_closed_side():
    """A partly-sold position with a material held remainder whose key IS in closed stays closed-side
    (no a_only double count), with RL present."""
    acts = [act("c1", 0, "BUY", 100), act("c1", 0, "SELL", 40)]
    res = {"c1": resolved(1)}
    g = loss_grounding.ground_losses(acts, [closed("c1", 0, 0.02, pnl=-30.0)], res, activity_truncated=False,
                                     rl_rows=[rl("c1", 0)], loss_side_complete=True)
    assert g.closed_losses == 1 and g.a_only_losses == 0 and g.honest_losses == 1


def test_truncated_omission_rises_with_rl_and_is_not_a_floor():
    """The headline fix: a truncated window understates omission; RL restores it, and the result is a
    measurement (loss side complete), not a lower bound."""
    acts = [act("c1", 0, "BUY", 100)]                      # the one loss the window reached
    res = {"c1": resolved(1), "c2": resolved(1), "c3": resolved(1), "c4": resolved(1), "cw": resolved(0)}
    cl = [closed("cw", 0, 0.95, pnl=50.0)]                 # one closed win
    base = loss_grounding.ground_losses(acts, cl, res, activity_truncated=True)
    with_rl = loss_grounding.ground_losses(acts, cl, res, activity_truncated=True,
                                           rl_rows=[rl("c2", 0), rl("c3", 0), rl("c4", 0)],
                                           loss_side_complete=True)
    assert base.loss_omission_pct == 1.0 and base.a_only_losses == 1
    assert with_rl.a_only_losses == 4 and with_rl.rl_added_losses == 3
    assert with_rl.honest_losses == 4 and with_rl.honest_wins == 1
    assert "lower bound" in base.completeness and "lower bound" not in with_rl.completeness
    assert with_rl.activity_truncated is True              # the WIN-side caveat is kept


def test_evanng_regression_union_equals_activity():
    """evanng shape (measured 2026-11-19): every RL key is already in the activity-held set -> the union
    changes nothing (same a_only, rl_added 0)."""
    acts = [act("c1", 0, "BUY", 100), act("c2", 1, "BUY", 60)]
    res = {"c1": resolved(1), "c2": resolved(0)}
    only_act = loss_grounding.ground_losses(acts, [], res, activity_truncated=False)
    union = loss_grounding.ground_losses(acts, [], res, activity_truncated=False,
                                         rl_rows=[rl("c1", 0), rl("c2", 1)], loss_side_complete=True)
    assert union.a_only_losses == only_act.a_only_losses == 2
    assert union.rl_added_losses == 0
    assert union.loss_omission_pct == only_act.loss_omission_pct == 1.0


# ── honest_windowed_roi with rl_rows ─────────────────────────────────────────────────────────────────
def test_honest_roi_includes_rl_only_loss():
    cl = [closed("cw", 0, 0.95, tb=200.0, avg=0.5, pnl=100.0)]     # cost 100, pnl +100
    h0 = scoring.honest_windowed_roi([], cl, {"cw": resolved(0)})
    h1 = scoring.honest_windowed_roi([], cl, {"cw": resolved(0)}, rl_rows=[rl("c9", 0, cost=50.0)])
    assert h0.honest_cost == 100.0 and h0.honest_pnl == 100.0
    assert h1.honest_cost == 150.0 and h1.honest_pnl == 50.0 and h1.n_positions == 2


def test_honest_roi_rl_no_double_count_vs_a_only_and_closed():
    """An RL row whose key the A_only loop already priced, or that sits in closed, adds nothing."""
    acts = [act("c1", 0, "BUY", 100, usd=50.0)]
    res = {"c1": resolved(1)}
    base = scoring.honest_windowed_roi(acts, [], res)
    same = scoring.honest_windowed_roi(acts, [], res, rl_rows=[rl("c1", 0, cost=50.0)])
    assert (same.honest_cost, same.honest_pnl, same.n_positions) == (base.honest_cost, base.honest_pnl, base.n_positions)
    cl = [closed("c2", 0, 0.02, tb=100.0, avg=0.5, pnl=-50.0)]
    with_closed = scoring.honest_windowed_roi([], cl, {"c2": resolved(1)}, rl_rows=[rl("c2", 0, cost=50.0)])
    assert with_closed.honest_cost == 50.0 and with_closed.n_positions == 1


# ── tier + floor semantics ───────────────────────────────────────────────────────────────────────────
def _tier(loss_side_complete, coverage=0.05):
    return scoring.assign_tier(n_honest=100, dominance_net=0.1, grounded=True, coverage_pct=coverage,
                               sort_roi=0.2, honest_roi=0.15, chalk=False, two_sided_pct=0.1,
                               one_sided_roi=0.2, loss_side_complete=loss_side_complete)


def test_coverage_gate_lifted_only_when_loss_side_complete():
    t_legacy, reason_legacy = _tier(False)
    t_rl, _ = _tier(True)
    assert t_legacy == scoring.TIER_INSUFFICIENT and "coverage" in reason_legacy
    assert t_rl == scoring.TIER_PROMOTE


def test_build_score_omission_floor_respects_loss_side_complete():
    g_rl = loss_grounding.ground_losses([act("c1", 0, "BUY", 100)], [], {"c1": resolved(1)},
                                        activity_truncated=True, rl_rows=[], loss_side_complete=True)
    g_legacy = loss_grounding.ground_losses([act("c1", 0, "BUY", 100)], [], {"c1": resolved(1)},
                                            activity_truncated=True)
    kw = dict(wallet="0xw", category="nba", pnls=[1.0], wons=[1], titles=["t"], sort_roi=0.1,
              n_resolved=50, two_sided_pct=0.1, avg_win_price=0.6, one_sided_roi=0.1)
    assert scoring.build_score(grounding=g_rl, honest=None, **kw).omission_floor is False
    assert scoring.build_score(grounding=g_legacy, honest=None, **kw).omission_floor is True
    assert scoring.build_score(grounding=g_rl, honest=None, **kw).skill_version == "score-2"


def test_report_loss_is_floor_respects_rl_basis():
    base = dict(wallet="0xw", category="nba", user_name=None, backfill_complete=True, n_total_rows=1,
                n_resolved=1, n_excluded=0, n_anomaly=0, wins=1, losses=0, win_rate=1.0,
                net_realized_pnl=1.0, total_bought=1.0, cost_basis=1.0, roi=1.0, roi_notional=1.0,
                avg_win_price=0.6, chalk=False, contested=False, n_condition_ids=1, two_sided_pct=0.0,
                onesided_roi=None, onesided_n=None, data_quality=None, dq_count_pct=0.0, dq_dollar_pct=0.0,
                data_state="ok", all_quarantined=False, min_resolved=1, rollup_n_resolved=None,
                reconciled=True, recon_note=None, generated_ts=1, skill_version="6",
                loss_grounded=True, loss_coverage_pct=0.05)
    rl_rep = analyze.PMAnalysisReport(**base, loss_basis="activity+rl",
                                      loss_completeness="complete(loss-side: +rl & closed walked to exhaustion; activity windowed for wins)")
    legacy_rep = analyze.PMAnalysisReport(**base, loss_basis="activity",
                                          loss_completeness="complete(activity exhausted within window)")
    assert rl_rep.loss_is_floor is False                   # low coverage no longer floors an RL-grounded report
    assert legacy_rep.loss_is_floor is True                # pre-RL semantics preserved


# ── orchestrator: fake clients ───────────────────────────────────────────────────────────────────────
class _Book(SimpleNamespace):
    pass


def _catof(row):
    return "nba"


class RLClient:
    """Fake client WITH the 2026-11-19 surface. Records which closed fetch was used."""
    def __init__(self, acts, closed_rows, rl_rows):
        self._acts, self._closed, self._rl = acts, closed_rows, rl_rows
        self.legacy_closed_called = False

    async def fetch_activity(self, wallet, *, limit, offset):
        return self._acts if offset == 0 else []

    async def fetch_closed_positions(self, wallet, *, limit, offset):
        self.legacy_closed_called = True
        return []

    async def fetch_closed_positions_book(self, wallet, **kw):
        return _Book(rows=self._closed, complete=True, pages=1, n=len(self._closed))

    async def fetch_redeemable_lost(self, wallet, **kw):
        return _Book(rows=self._rl, complete=True, pages=1, n=len(self._rl))

    async def fetch_market_resolutions(self, cids, **kw):
        return {"cw_recent": resolved(0), "c_rl": resolved(1)}


class LegacyClient:
    """Fake client WITHOUT the new surface (the pre-RL duck-type every old test used)."""
    def __init__(self, acts, closed_rows):
        self._acts, self._closed = acts, closed_rows

    async def fetch_activity(self, wallet, *, limit, offset):
        return self._acts if offset == 0 else []

    async def fetch_closed_positions(self, wallet, *, limit, offset):
        return self._closed if offset == 0 else []

    async def fetch_market_resolutions(self, cids, **kw):
        return {"c1": resolved(1)}


@pytest.mark.asyncio
async def test_stale_vs_live_closed_recent_redeemed_win_counted():
    """THE stale-basis test (Jack ruling): a recent redeemed WIN exists only in the LIVE closed walk (the
    stored pm_closed_position is weeks stale and grounding must not read it). The orchestrator walks CLOSED
    live -> the win lands in closed_wins AND in the honest $ basis, beside the RL-recovered loss."""
    recent_win = closed("cw_recent", 0, 0.95, tb=200.0, avg=0.5, pnl=100.0)
    cli = RLClient(acts=[], closed_rows=[recent_win], rl_rows=[rl("c_rl", 0, cost=50.0)])
    g, honest = await loss_grounding.fetch_and_ground_losses(cli, "0xw", "nba", category_of=_catof)
    assert cli.legacy_closed_called is False               # the live walk, never the offset-paged legacy path
    assert g.closed_wins == 1 and g.rl_added_losses == 1 and g.honest_losses == 1
    assert g.loss_side_complete is True and g.loss_basis == "activity+rl"
    assert honest.honest_cost == 150.0 and honest.honest_pnl == 50.0


@pytest.mark.asyncio
async def test_legacy_fake_client_backcompat_byte_identical():
    """A client without the new methods grounds EXACTLY as before the RL supplement."""
    cli = LegacyClient(acts=[act("c1", 0, "BUY", 100)], closed_rows=[])
    g, honest = await loss_grounding.fetch_and_ground_losses(cli, "0xw", "nba", category_of=_catof)
    assert g.loss_basis == "activity" and g.rl_added_losses == 0 and g.loss_side_complete is False
    assert g.a_only_losses == 1 and g.completeness == "complete(activity exhausted within window)"


@pytest.mark.asyncio
async def test_books_split_grounds_many_categories_from_one_fetch():
    """The batch-runner seam: fetch once, ground per category (no per-category re-fetch)."""
    calls = {"activity": 0}

    class CountingClient(RLClient):
        async def fetch_activity(self, wallet, *, limit, offset):
            calls["activity"] += 1
            return []

    cli = CountingClient(acts=[], closed_rows=[], rl_rows=[rl("c_rl", 0)])
    books = await loss_grounding.fetch_wallet_grounding_books(cli, "0xw")
    g1, _ = await loss_grounding.ground_category_from_books(cli, books, "nba", category_of=_catof)
    g2, _ = await loss_grounding.ground_category_from_books(cli, books, "nba", category_of=_catof)
    assert calls["activity"] == 1                          # one wallet-level fetch, N groundings
    assert g1.rl_added_losses == g2.rl_added_losses == 1
