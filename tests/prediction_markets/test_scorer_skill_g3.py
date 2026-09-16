"""Group 3 (2026-09-15) items 5 + 7 -- the Analyze scorer/skill changes. Pure ranking_metrics / _build_user_content
assertions over a constructed PMAnalysisReport (no LLM, no DB -> off the pre-existing TestClient failure surface).

★ ITEM 5 is proven by ASSERTING THE PROMPT STRING the narrator receives (the inverting metrics must be ABSENT),
NOT by reading the code -- the same discipline that caught the model-name check echoing its own config."""
from trading_corp.prediction_markets import analyze


def _rep(tier, **score_over):
    """A full PMAnalysisReport with a WhaleScore dict; override score fields via kwargs (samples default ())."""
    score = {"tier": tier, "reason": "r", "sort_roi": 0.30, "n_honest": 19, "grounded": True,
             "omission_pct": 0.0, "coverage_pct": 0.95, "omission_floor": False, "honest_roi": 0.20,
             "dominance_net": 0.05, "dominance_gross": 0.05, "largest_pnl": 10.0, "largest_title": "x",
             "two_sided_pct": 0.0, "avg_win_price": 0.60, "chalk": False, "dd_tell": 0.0, "dd_wins": 10,
             "dd_losses": 5, "copy_fills": 0, "copy_pnl": None}
    score.update(score_over)
    return analyze.PMAnalysisReport(
        wallet="0xabc", category="mlb", user_name="W", backfill_complete=True, n_total_rows=30, n_resolved=25,
        n_excluded=2, n_anomaly=0, wins=15, losses=10, win_rate=0.6, net_realized_pnl=100.0, total_bought=200.0,
        cost_basis=120.0, roi=0.20, roi_notional=0.11, avg_win_price=0.60, chalk=False, contested=True,
        n_condition_ids=20, two_sided_pct=0.0, onesided_roi=0.20, onesided_n=20, data_quality=None,
        dq_count_pct=0.0, dq_dollar_pct=0.0, data_state="ok", all_quarantined=False, min_resolved=30,
        rollup_n_resolved=None, reconciled=True, recon_note=None, generated_ts=1000, skill_version="5", score=score)


# ── ITEM 5: ranking_metrics clean-vs-inverting split ──
def test_ranking_metrics_split_clean_vs_inverting():
    rm = analyze.ranking_metrics(_rep("WATCH"))
    clean, inv = dict(rm["clean"]), dict(rm["inverting"])
    for lab in ("net ROI (cost-basis)", "edge factor (1 + clipped cost-ROI)", "n resolved (scoreable)",
                "n excluded (quarantined)", "min resolved (rank floor)"):
        assert lab in clean, lab
    for lab in ("composite score (wilson_lcb x edge)", "wilson_lcb (95% win-rate lower bound)",
                "ROI (notional, net / total_bought)"):
        assert lab in inv, lab
    # recency-weighted n_eff/score are NOT surfaced (rep.samples is the top-5 illustrative rows, not a valid input)
    assert "effective n (recency-weighted)" not in clean and "recency-weighted score" not in inv
    assert "HIGHER" in rm["invert_flag"]          # ★ the flag says WHICH WAY it inverts (the mirage tell)


# ── ITEM 5: the CLEAN set is fed to Sonnet; the INVERTING set is WITHHELD -- asserted on the PROMPT STRING ──
def test_prompt_feeds_clean_metrics_and_withholds_inverting():
    txt = analyze._build_user_content(_rep("WATCH"))
    assert "CLEAN set only" in txt
    assert "net ROI (cost-basis)" in txt and "edge factor" in txt and "n resolved (scoreable)" in txt
    low = txt.lower()
    assert "wilson_lcb" not in low                # ★ the inverting metrics are ABSENT from the narrator input
    assert "composite score" not in low
    assert "recency-weighted score" not in low
    assert "notional" not in low                  # roi_notional withheld


# ── ITEM 7: tier-conditional sentence count ──
def test_insufficient_data_asks_for_two_sentences():
    txt = analyze._build_user_content(_rep("INSUFFICIENT_DATA", n_honest=19))
    assert "EXACTLY TWO sentences" in txt and "what is MISSING" in txt and "the READ" in txt
    assert "EXACTLY ONE sentence" not in txt


def test_other_tiers_stay_one_sentence():
    for tier in ("PROMOTE", "WATCH", "PASS"):
        txt = analyze._build_user_content(_rep(tier))
        assert "EXACTLY ONE sentence" in txt and "EXACTLY TWO sentences" not in txt, tier


def test_system_prompt_is_tier_aware_with_anti_prose_guards():
    p = analyze._SYSTEM_PROMPT
    assert "INSUFFICIENT_DATA: EXACTLY TWO sentences" in p
    assert "NO third sentence" in p and "do NOT restate the caveat" in p
    assert "ONE sentence for PROMOTE/WATCH/PASS" in p


# ── ITEM 7: the LONGEST plausible case -- thin + high omission + concentration + chalk AT ONCE (the clip risk) ──
def test_longest_multi_caveat_insufficient_data_prompt_is_wellformed():
    txt = analyze._build_user_content(_rep(
        "INSUFFICIENT_DATA", n_honest=8, grounded=True, omission_pct=0.90, coverage_pct=0.40, omission_floor=True,
        dominance_net=0.85, two_sided_pct=0.50, avg_win_price=0.90, chalk=True))
    assert "MIRAGE" in txt                        # 90% omission flag
    assert "ONE-POSITION RECORD" in txt           # dominance beyond the extreme threshold
    assert "hedger/market-maker" in txt           # two-sided >= 0.40
    assert "chalk" in txt                         # avg_win_price >= 0.85
    assert "EXACTLY TWO sentences" in txt         # still the 2-sentence ask (not clipped to a caveat-only refusal)
    assert "wilson_lcb" not in txt.lower()        # inverting metrics stay withheld even in the worst case


def test_skill_version_and_token_cap_bumped():
    assert analyze.PM_ANALYZE_SKILL_VERSION == "5"
    assert analyze.PM_ANALYZE_MAX_OUTPUT_TOKENS == 220   # headroom so the 2nd sentence never truncates
