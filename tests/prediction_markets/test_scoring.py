"""ANALYZE scoring (2026-09-12): the deterministic tier + dimensions. Pure logic -> runs offline (no LLM, no DB).

Covers: single-trade dominance (both shares + the >1.0 one-position case), the labelled-fiction drawdown-tell,
the four tiers with their gates, and a COMPLICATED whale (multiple caveats) proving the output holds to ONE reason.
Thresholds asserted are the INVENTED starting values (Jack 2026-09-12) -- if they are tuned, these update with them.
"""
from trading_corp.prediction_markets import scoring as S


# ── single-trade dominance ──
def test_dominance_diversified_vs_one_position():
    d = S.single_trade_dominance([10.0, 10.0, 10.0, 10.0], ["a", "b", "c", "d"])
    assert abs(d.net_share - 0.25) < 1e-9 and abs(d.gross_share - 0.25) < 1e-9   # even record
    d2 = S.single_trade_dominance([100.0, 5.0, -3.0], ["big", "x", "y"])
    assert d2.largest_pnl == 100.0 and d2.largest_title == "big"
    assert d2.net_share > 0.9                                                     # one win dominates net


def test_dominance_net_share_exceeds_one_when_record_is_one_position():
    # net = 6.0; the single win of 20 EXCEEDS the whole net -> without it the whale is net-negative
    d = S.single_trade_dominance([20.0, -8.0, -6.0], ["win", "l1", "l2"])
    assert d.net_share is not None and d.net_share > 1.0


def test_dominance_net_share_none_on_a_net_loser():
    d = S.single_trade_dominance([-5.0, -3.0, 1.0])
    assert d.net_share is None            # dominance is meaningless when net<=0


# ── drawdown-tell (labelled fiction) ──
def test_drawdown_tell_zero_over_all_wins_is_the_mirage_smell():
    dt = S.drawdown_tell([100.0] * 20, [1] * 20)      # 20 wins, never a down-step
    assert dt.max_drawdown == 0.0 and dt.n_wins == 20 and dt.n_losses == 0


def test_drawdown_tell_real_downsteps_when_losses_present():
    dt = S.drawdown_tell([50.0, -80.0, 40.0], [1, 0, 1])
    assert dt.max_drawdown == 80.0 and dt.n_losses == 1


# ── the four tiers ──
def _tier(**kw):
    base = dict(n_honest=100, dominance_net=0.05, grounded=True, coverage_pct=0.96, sort_roi=0.5,
               honest_roi=0.4, chalk=False, two_sided_pct=0.05, one_sided_roi=0.5)
    base.update(kw)
    return S.assign_tier(**base)


def test_tier_promote_when_grounded_and_every_bar_clears():
    tier, reason = _tier()
    assert tier == S.TIER_PROMOTE and "survives grounding" in reason


def test_tier_ungrounded_caps_at_watch_never_promote():
    tier, reason = _tier(grounded=False, honest_roi=None, coverage_pct=None)
    assert tier == S.TIER_WATCH and "grounds clean" in reason      # the 0x684baa57c3 case: best numbers, still WATCH


def test_tier_insufficient_on_thin_sample():
    tier, reason = _tier(n_honest=6)
    assert tier == S.TIER_INSUFFICIENT and "too thin" in reason


def test_tier_insufficient_on_one_position_record():
    tier, reason = _tier(dominance_net=0.7)
    assert tier == S.TIER_INSUFFICIENT and "one position" in reason


def test_tier_insufficient_on_low_grounding_coverage():
    tier, reason = _tier(coverage_pct=0.5)
    assert tier == S.TIER_INSUFFICIENT and "floor" in reason


def test_tier_pass_on_no_roi_edge():
    tier, reason = _tier(sort_roi=-0.1)
    assert tier == S.TIER_PASS and "no cost-ROI edge" in reason


def test_tier_pass_when_honest_roi_vanishes_under_grounding():
    # the mirage: a big headline ROI but once the dropped losers are added the honest ROI is <=0
    tier, reason = _tier(sort_roi=0.73, honest_roi=-0.05)
    assert tier == S.TIER_PASS and "vanishes once dropped losses" in reason


def test_tier_pass_on_chalk_with_negligible_edge():
    tier, reason = _tier(chalk=True, sort_roi=0.03, honest_roi=0.03)
    assert tier == S.TIER_PASS and "chalk" in reason


def test_tier_watch_on_concentration_below_the_gate():
    tier, reason = _tier(dominance_net=0.42)
    assert tier == S.TIER_WATCH and "concentrated" in reason


def test_tier_watch_on_hedger_upper_bound():
    tier, reason = _tier(two_sided_pct=0.6, one_sided_roi=-0.1)   # hedger + one-sided not positive
    assert tier == S.TIER_WATCH and "two-sided" in reason


# ── the COMPLICATED whale: multiple real caveats, output STILL one tier + one reason (the brevity proof) ──
def test_complicated_whale_holds_to_one_reason():
    # grounded; a real +40% honest ROI; BUT concentrated (38% one position), a partial hedger (35% two-sided),
    # AND a chunky omission. Three things are true at once -- the template must still emit ONE decisive reason.
    tier, reason = _tier(dominance_net=0.38, two_sided_pct=0.35, coverage_pct=0.92,
                         sort_roi=0.55, honest_roi=0.40, chalk=False)
    assert tier == S.TIER_WATCH
    assert isinstance(reason, str) and reason.count(";") == 0 and len(reason) < 90   # ONE phrase, no caveat-list
    assert "concentrated" in reason                                                  # the FIRST material caveat wins


# ── thresholds are the INVENTED starting values (documented as tunable, not derived) ──
def test_invented_thresholds_are_the_ruled_starting_values():
    assert S.MIN_N_HONEST == 30 and S.DOM_EXTREME_NET == 0.60 and S.DOM_HIGH_NET == 0.30
    assert S.COVERAGE_FLOOR == 0.90 and S.TWO_SIDED_HIGH == 0.40


# ── the DISPLAY sort: the TIER caps the number (the Prospects-list ruling) ──
def test_tier_rank_order_promote_highest_unanalyzed_zero():
    assert S.tier_rank(S.TIER_PROMOTE) > S.tier_rank(S.TIER_WATCH) > S.tier_rank(S.TIER_INSUFFICIENT) > S.tier_rank(S.TIER_PASS)
    assert S.tier_rank(S.TIER_PASS) > 0
    assert S.tier_rank(None) == 0 and S.tier_rank("nonsense") == 0     # un-analyzed / unknown -> bottom


def test_score_sort_key_tier_caps_the_number():
    # ★ THE ACCEPTANCE for the sort: the 0x684baa57c3 case -- an INSUFFICIENT_DATA whale at a huge +88.5% ROI must
    # sort BELOW a PROMOTE at a modest +10%. The number can never lift a lesser tier above a higher one.
    insufficient_high = S.score_sort_key(S.TIER_INSUFFICIENT, 0.885)
    promote_low = S.score_sort_key(S.TIER_PROMOTE, 0.10)
    assert promote_low > insufficient_high, (promote_low, insufficient_high)
    # WATCH above INSUFFICIENT above PASS, whatever the ROI within
    assert S.score_sort_key(S.TIER_WATCH, -0.5) > S.score_sort_key(S.TIER_INSUFFICIENT, 5.0)
    assert S.score_sort_key(S.TIER_INSUFFICIENT, -0.5) > S.score_sort_key(S.TIER_PASS, 5.0)
    # within a tier the number still orders
    assert S.score_sort_key(S.TIER_PROMOTE, 0.5) > S.score_sort_key(S.TIER_PROMOTE, 0.2)


def test_score_sort_key_unanalyzed_is_none():
    assert S.score_sort_key(None, None) is None            # un-analyzed -> caller renders 'not analyzed' + bottom sentinel
    assert S.score_sort_key("PROMOTE", None) is not None   # analyzed with no ROI still ranks by tier


# ── Phase A: honest windowed ROI (the "did winning pay" number) ──
class _Act:
    def __init__(self, cid, oi, side, size, usd):
        self.type = "TRADE"; self.condition_id = cid; self.outcome_index = oi
        self.side = side; self.size = size; self.usdc_size = usd


class _Closed:
    def __init__(self, cid, oi, total_bought, avg_price, realized_pnl):
        self.condition_id = cid; self.outcome_index = oi
        self.total_bought = total_bought; self.avg_price = avg_price; self.realized_pnl = realized_pnl


def test_honest_roi_adds_recovered_held_to_worthless_losers():
    # closed side: one WIN (cost 60 = 100*0.6, pnl +40). activity recovers one A_only LOSER (held to worthless):
    # bought 100 @ $0.50 = $50 committed, never sold, resolved LOST -> honest pnl -50, cost +50.
    closed = [_Closed("cidW", 0, total_bought=100.0, avg_price=0.6, realized_pnl=40.0)]
    acts = [_Act("cidL", 1, "BUY", 100.0, 50.0)]           # A_only held loser, absent from closed
    res = {"cidL": {"status": "resolved", "winning_outcome_index": 0}}   # oi=1 lost (winner is 0)
    hw = S.honest_windowed_roi(acts, closed, res)
    # honest: cost 60+50=110, pnl 40-50=-10 -> roi ~ -9%. Without the recovered loser closed-only ROI was +67%.
    assert hw.n_positions == 2 and abs(hw.honest_cost - 110.0) < 1e-6 and abs(hw.honest_pnl - (-10.0)) < 1e-6
    assert hw.honest_roi is not None and hw.honest_roi < 0        # the edge vanishes once the dropped loss is added


def test_honest_roi_recovered_winner_pays_out():
    closed = []
    acts = [_Act("cidW", 0, "BUY", 100.0, 30.0)]           # held 100 @ $0.30 = $30, won -> payout 100, pnl +70
    res = {"cidW": {"status": "resolved", "winning_outcome_index": 0}}
    hw = S.honest_windowed_roi(acts, closed, res)
    assert abs(hw.honest_cost - 30.0) < 1e-6 and abs(hw.honest_pnl - 70.0) < 1e-6


def test_honest_roi_partial_sale_winner_uses_gross_basis_not_inflated():
    # Skeptic-1 regression: BUY 100 @ $0.50 ($50), SELL 40 @ $0.75 ($30), hold 60 to a WIN. GROSS basis (=buy $50)
    # -> pnl = 60 held-payout + 30 sales - 50 buy = +40 -> ROI +80%. A NET-of-sales basis ($50-$30=$20) would credit
    # the same +40 over a shrunken $20 denom -> a bogus +200%. The fix keeps the A_only side on the closed convention.
    closed = []
    acts = [_Act("cidP", 0, "BUY", 100.0, 50.0), _Act("cidP", 0, "SELL", 40.0, 30.0)]
    res = {"cidP": {"status": "resolved", "winning_outcome_index": 0}}
    hw = S.honest_windowed_roi(acts, closed, res)
    assert hw.n_positions == 1
    assert abs(hw.honest_cost - 50.0) < 1e-6 and abs(hw.honest_pnl - 40.0) < 1e-6
    assert hw.honest_roi is not None and abs(hw.honest_roi - 0.80) < 1e-6   # +80%, NOT +200%


def test_honest_roi_partial_sale_loser_held_to_worthless():
    # BUY 100 @ $0.50 ($50), SELL 40 @ $0.60 ($24), hold 60 to a LOSS (worthless). GROSS basis $50; the kept shares
    # pay $0 -> pnl = 24 sales - 50 buy = -26 -> ROI -52%. Symmetric with the winner: sales net into pnl, basis is gross.
    closed = []
    acts = [_Act("cidPL", 1, "BUY", 100.0, 50.0), _Act("cidPL", 1, "SELL", 40.0, 24.0)]
    res = {"cidPL": {"status": "resolved", "winning_outcome_index": 0}}   # oi=1 lost
    hw = S.honest_windowed_roi(acts, closed, res)
    assert abs(hw.honest_cost - 50.0) < 1e-6 and abs(hw.honest_pnl - (-26.0)) < 1e-6


# ── build_score assembler: the four real whales, end to end ──
def _rows(pnls, wons=None, titles=None):
    wons = wons if wons is not None else [1 if p > 0 else 0 for p in pnls]
    titles = titles if titles is not None else ["m%d" % i for i in range(len(pnls))]
    return pnls, wons, titles


def test_build_score_mirage_whale_pass_and_drawdown_tell_zero():
    # 0xbca08c1bc2/mlb-shaped: 40 wins, 0 losses (loss omission), positive ROI, ungrounded.
    p, w, t = _rows([100.0] * 40)
    sc = S.build_score(wallet="0xbca", category="mlb", pnls=p, wons=w, titles=t, sort_roi=0.73,
                       n_resolved=40, two_sided_pct=0.0, avg_win_price=0.92, one_sided_roi=0.73)
    assert sc.dd_tell == 0.0 and sc.dd_wins == 40 and sc.dd_losses == 0     # the labelled-fiction smell
    assert sc.tier in (S.TIER_WATCH, S.TIER_PASS)                            # ungrounded -> not PROMOTE; chalk caveat


def test_build_score_ungrounded_best_numbers_still_watch():
    # 0x684baa57c3-shaped: diversified, strong roi, but ungrounded -> WATCH not PROMOTE (the spec working)
    p, w, t = _rows([50.0] * 200 + [-30.0] * 13)
    sc = S.build_score(wallet="0x684", category="mlb", pnls=p, wons=w, titles=t, sort_roi=0.89,
                       n_resolved=213, two_sided_pct=0.02, avg_win_price=0.75, one_sided_roi=0.89,
                       copy_fills=214, copy_pnl=None)
    assert sc.grounded is False and sc.omission_pct is None                 # UNKNOWN, never zero
    assert sc.tier == S.TIER_WATCH and "grounds clean" in sc.reason
    assert sc.copy_fills == 214 and sc.dominance_net is not None and sc.dominance_net < 0.30
