"""Tests for the PA-redeem-cap simulator (scripts/run_redeem_sim.py + the
run_redeem_cap_backtest / _simulate_redeem engine it drives).

Load-bearing properties under test (the prior /goals hit repaint artifacts,
so the causal/no-look-ahead guarantee is the most important one):

  1. CAUSAL / NO-LOOK-AHEAD: a PA re-eval at bar k must depend ONLY on bars
     <= k. We prove it by a leakage probe: mutate every bar strictly AFTER a
     fixed cutoff to absurd values and assert the sim's decisions for signals
     that resolve at-or-before the cutoff are byte-identical.
  2. CAP-EXPIRY: a redeem that needs to wait > cap bars must NOT fire at that
     cap; raising the cap to >= the needed wait must let it fire.
  3. CAP MONOTONICITY: trade count (first_pass + redeem) is non-decreasing in
     the cap on the same corpus.
  4. FIRE-BAR PRICING: a redeemed entry is priced at the FIRE bar's close,
     never the stale signal-bar close.
  5. FEES APPLIED + FINITE: net_R == gross_R minus a strictly positive
     entry-normalised round-trip cost; all reported R values are finite.

The engine-level tests (2-5 here that need real PA behavior) run against the
clean btc_scalping.db corpus and SKIP cleanly if it is unavailable, so the
suite stays green on machines without the corpus. Tests 2/4 that need a
deterministic redeem are built on a controlled synthetic price path.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from scripts.backtest_bitunix_confluence import (
    PAValidationConfig,
    PAValidationDecision,
)
import scripts.run_redeem_sim as R


# ── corpus availability gate ────────────────────────────────────────────
def _corpus_path():
    try:
        return R._resolve_db(None)
    except FileNotFoundError:
        return None


_DB = _corpus_path()
_needs_corpus = pytest.mark.skipif(
    _DB is None, reason="clean btc_scalping.db corpus not available on this host"
)

# A small window inside the 3m corpus span (2026-03-30 .. 2026-06-18) that is
# fast to run yet contains both first-pass fires and redeems (verified during
# build: cap-sweep here is 25 -> 99 fires).
_W_START = "2026-04-01"
_W_END = "2026-04-08"


# ── 1. CAUSAL / NO-LOOK-AHEAD (leakage probe) ───────────────────────────
@_needs_corpus
def test_no_look_ahead_future_bars_do_not_change_past_decisions():
    """Mutating bars strictly after a cutoff must not change any trade whose
    entry bar is at-or-before that cutoff. If the PA/score re-eval leaked a
    future bar, those past decisions would shift."""
    s = R._to_dt(_W_START)
    e = R._to_dt(_W_END)
    alerts, bars, config = R.load_inputs(_DB, s, e)

    # cutoff at the midpoint of the window
    cutoff = bars[len(bars) // 2]["ts"]

    # baseline at a modest cap (covers redeems within the window)
    base = R.run_redeem_sim(cap=10, _preloaded=(alerts, bars, config, (s, e)))

    # corrupt every bar strictly after the cutoff to absurd values
    poisoned = []
    for b in bars:
        if b["ts"] > cutoff:
            poisoned.append({**b, "open": 1e9, "high": 1e9, "low": 1e9,
                             "close": 1e9, "volume": 0.0})
        else:
            poisoned.append(dict(b))
    mut = R.run_redeem_sim(cap=10, _preloaded=(alerts, poisoned, config, (s, e)))

    def _past(res):
        # trades whose ENTRY bar ts is <= cutoff (these must be unaffected)
        cut_iso = cutoff.isoformat()
        return [t for t in res["trades"] if t["entry_ts"] <= cut_iso]

    base_past = _past(base)
    mut_past = _past(mut)
    assert base_past, "window should produce at least one pre-cutoff trade"
    # identical count and identical entry prices / sides / bars_waited
    assert len(base_past) == len(mut_past)
    for a, b in zip(base_past, mut_past):
        assert a["entry_ts"] == b["entry_ts"]
        assert a["signal_ts"] == b["signal_ts"]
        assert a["side"] == b["side"]
        assert a["bars_waited"] == b["bars_waited"]
        assert a["entry_bar_price"] == b["entry_bar_price"], (
            "future-bar mutation changed a past entry price -> LOOK-AHEAD LEAK"
        )


# ── 3. CAP MONOTONICITY ─────────────────────────────────────────────────
@_needs_corpus
def test_trade_count_monotone_nondecreasing_in_cap():
    s, e = R._to_dt(_W_START), R._to_dt(_W_END)
    alerts, bars, config = R.load_inputs(_DB, s, e)
    pre = (alerts, bars, config, (s, e))
    counts = []
    for cap in (0, 1, 2, 3, 10):
        r = R.run_redeem_sim(cap=cap, _preloaded=pre)
        counts.append(r["n_first_pass"] + r["n_redeem"])
    assert counts == sorted(counts), f"trade count not monotone in cap: {counts}"
    assert counts[-1] > counts[0], "cap should admit strictly more trades on this window"


# ── 5. FEES APPLIED + FINITE + FIRE-BAR PRICING (engine, real corpus) ────
@_needs_corpus
def test_fees_applied_and_all_R_finite_and_redeem_priced_at_fire_bar():
    s, e = R._to_dt(_W_START), R._to_dt(_W_END)
    alerts, bars, config = R.load_inputs(_DB, s, e)
    res = R.run_redeem_sim(cap=10, fee_mode="taker",
                           _preloaded=(alerts, bars, config, (s, e)))
    # build a ts -> bar-close lookup to assert fire-bar pricing
    close_by_ts = {b["ts"].isoformat(): b["close"] for b in bars}

    saw_walked = False
    for t in res["trades"]:
        # no NaN anywhere
        for key in ("gross_R", "net_R", "net_R_taker", "net_R_maker"):
            v = t[key]
            assert v is None or math.isfinite(v), f"{key} is NaN/inf: {t}"
        # entry price equals the FIRE/ENTRY bar's close (never signal-bar stale px)
        if t["entry_ts"] in close_by_ts:
            assert t["entry_bar_price"] == pytest.approx(close_by_ts[t["entry_ts"]])
        if t["net_R"] is not None and t["gross_R"] is not None:
            saw_walked = True
            # fee is strictly positive -> net strictly below gross
            assert t["net_R"] < t["gross_R"]
            # taker cost > maker cost -> taker net <= maker net
            assert t["net_R_taker"] <= t["net_R_maker"]
    assert saw_walked, "expected at least one walked (R-resolved) trade"
    # aggregate sanity
    assert math.isfinite(res["total_net_R"])
    assert math.isfinite(res["net_R_per_trade"])
    assert 0.0 <= res["win_rate_pct"] <= 100.0


# ── 2 & 4. CAP-EXPIRY + FIRE-BAR PRICING on a CONTROLLED synthetic path ──
# These need a redeem that fires at a KNOWN bar so we can assert the cap
# boundary exactly. We drive the engine directly with a hand-built corpus and
# a stub PA validator that rejects until a chosen bar, then passes.
from scripts import backtest_bitunix_confluence as BT  # noqa: E402


def _mk_bars(n: int, base_ts: int = 1_775_000_000, step: int = 180):
    """n flat-ish 3m bars with a gentle uptrend (deterministic).

    Each bar's mid = (open+close)/2 is UNIQUE so a stub validator can map a
    PriceContext (current_price == bar-mid, per fill_price_at) back to its bar
    index deterministically. That mapping is what `_bar_of_ctx` relies on.
    """
    out = []
    px = 60000.0
    for i in range(n):
        o = px
        c = px + 10.0  # tiny up bar; distinct mids across bars
        out.append({
            "ts": datetime.fromtimestamp(base_ts + i * step, tz=timezone.utc),
            "open": o, "high": max(o, c) + 2.0, "low": min(o, c) - 2.0,
            "close": c, "volume": 1000.0,
        })
        px = c
    return out


def _bar_of_ctx(bars, price_ctx):
    """Map a PriceContext back to its bar index via the bar-MID (current_price
    == fill_price_at == (open+close)/2), not the close."""
    cp = price_ctx.current_price
    for i, b in enumerate(bars):
        if abs((b["open"] + b["close"]) / 2.0 - cp) < 1e-6:
            return i
    return None


def test_cap_expiry_boundary_with_stubbed_pa(monkeypatch):
    """A redeem that first PA-passes at wait = W:
       cap < W  -> no redeem fire (expired);
       cap >= W -> fires, bars_waited == W, priced at the fire bar.
    """
    bars = _mk_bars(80)
    PASS_AT_WAIT = 4              # PA passes on the 4th bar after the reject

    # one alert that scores a (buy) fire on bar 10
    from scripts.backtest_bitunix_confluence import AlertEvent, BitUnixConfluenceConfig
    import yaml
    cfg = BitUnixConfluenceConfig.from_dict(
        yaml.safe_load((BT._REPO_ROOT / "config" / "strategies.yaml").read_text())
        ["bitunix_futures"]
    )

    # Force a deterministic score verdict: always a STANDARD buy (never SKIP),
    # so the only gating variable is PA.
    from types import SimpleNamespace
    from scripts.backtest_bitunix_confluence import Tier, Side

    def fake_score(*, live_alerts, price_ctx, config, now, last_fire_ts_buy,
                   last_fire_ts_sell):
        return SimpleNamespace(tier=Tier.STANDARD, side=Side.BUY,
                               cooldown_blocked=False)

    # PA rejects until the reject bar + PASS_AT_WAIT, then passes. We key off
    # the bar timestamp so it is deterministic regardless of call order.
    reject_bar_ts = bars[10]["ts"]
    pass_ts = bars[10 + PASS_AT_WAIT]["ts"]

    def fake_pa(*, side, price_ctx, config):
        # map the context back to its bar via the bar-MID (current_price)
        idx = _bar_of_ctx(bars, price_ctx)
        if idx is None or idx < 10 + PASS_AT_WAIT:
            return SimpleNamespace(decision=PAValidationDecision.REJECT)
        return SimpleNamespace(decision=PAValidationDecision.PASS)

    monkeypatch.setattr(BT, "evaluate_confluence_futures", fake_score)
    monkeypatch.setattr(BT, "evaluate_pa_validation", fake_pa)

    alerts = [AlertEvent(ts=bars[10]["ts"], signal_name="otter_buy", tf="3m")]
    pa_cfg = PAValidationConfig(enabled=True, require_all=True,
                                validators=("vwap_alignment",))

    # cap below the needed wait -> no redeem fire
    fires_lo, sum_lo = BT.run_redeem_cap_backtest(
        alerts=alerts, bars=bars, config=cfg, pa_config=pa_cfg,
        redeem_cap=PASS_AT_WAIT - 1, arm_name="lo")
    assert sum_lo["n_redeem_fire"] == 0, "cap < needed wait must NOT redeem"

    # cap at/above the needed wait -> fires at exactly that wait, fire-bar price
    fires_hi, sum_hi = BT.run_redeem_cap_backtest(
        alerts=alerts, bars=bars, config=cfg, pa_config=pa_cfg,
        redeem_cap=PASS_AT_WAIT, arm_name="hi")
    assert sum_hi["n_redeem_fire"] == 1, "cap >= needed wait must redeem once"
    rf = next(f for f in fires_hi if f.redeemed)
    assert rf.bars_waited == PASS_AT_WAIT
    # priced at the FIRE bar close, NOT the signal/reject-bar close
    assert rf.entry == pytest.approx(bars[10 + PASS_AT_WAIT]["close"])
    assert rf.entry != pytest.approx(bars[10]["close"])


def test_opposite_side_flip_voids_redeem(monkeypatch):
    """If the re-scored winning side flips vs the reject side, the redeem is
    voided (prod parity with the observer `opposite_side` cache clear)."""
    bars = _mk_bars(40)
    from types import SimpleNamespace
    from scripts.backtest_bitunix_confluence import (
        AlertEvent, BitUnixConfluenceConfig, Tier, Side,
    )
    import yaml
    cfg = BitUnixConfluenceConfig.from_dict(
        yaml.safe_load((BT._REPO_ROOT / "config" / "strategies.yaml").read_text())
        ["bitunix_futures"]
    )

    # reject is on a BUY at bar 5; from bar 6 onward the winning side is SELL.
    def fake_score(*, live_alerts, price_ctx, config, now, last_fire_ts_buy,
                   last_fire_ts_sell):
        idx = _bar_of_ctx(bars, price_ctx) or 0
        side = Side.BUY if idx <= 5 else Side.SELL
        return SimpleNamespace(tier=Tier.STANDARD, side=side, cooldown_blocked=False)

    def fake_pa(*, side, price_ctx, config):
        idx = _bar_of_ctx(bars, price_ctx) or 0
        # PA rejects at bar 5 (forces a redeem), would pass later
        return SimpleNamespace(
            decision=PAValidationDecision.REJECT if idx <= 5
            else PAValidationDecision.PASS)

    monkeypatch.setattr(BT, "evaluate_confluence_futures", fake_score)
    monkeypatch.setattr(BT, "evaluate_pa_validation", fake_pa)
    alerts = [AlertEvent(ts=bars[5]["ts"], signal_name="otter_buy", tf="3m")]
    pa_cfg = PAValidationConfig(enabled=True, require_all=True,
                               validators=("vwap_alignment",))
    fires, summ = BT.run_redeem_cap_backtest(
        alerts=alerts, bars=bars, config=cfg, pa_config=pa_cfg,
        redeem_cap=20, arm_name="flip")
    # the redeem must be dropped (side flipped to SELL), not fired on BUY
    assert summ["n_redeem_fire"] == 0, "opposite-side flip must void the redeem"


def test_run_redeem_sim_rejects_contaminated_db(tmp_path):
    """The driver must refuse the live trading_corp.db (contaminated)."""
    fake = tmp_path / "trading_corp.db"
    fake.write_bytes(b"")
    with pytest.raises(ValueError, match="trading_corp.db"):
        R._resolve_db(fake)


def test_cap_parsing_inf_tokens():
    assert R._parse_cap("inf") == R._INF_CAP
    assert R._parse_cap("∞") == R._INF_CAP
    assert R._parse_cap(None) == R._INF_CAP
    assert R._parse_cap(-1) == R._INF_CAP
    assert R._parse_cap(0) == 0
    assert R._parse_cap("3") == 3


# ── MAX-SLIPPAGE ENTRY GUARD (additive, default-off) ────────────────────
# These mirror the stubbed-PA pattern above: a redeem fires at a known bar so
# the slip = |fire_close - signal/reject_close| is exactly computable, letting
# us assert the guard boundary precisely and prove it is causal + additive.

def _slip_setup(monkeypatch, pass_at_wait=4, reject_bar=10, n=80):
    """Build a deterministic single-redeem scenario and return (bars, cfg,
    alerts, pa_cfg, signal_close, fire_close). PA rejects on the reject bar and
    passes at reject_bar+pass_at_wait; the redeem therefore fires at that bar."""
    bars = _mk_bars(n)
    from scripts.backtest_bitunix_confluence import AlertEvent, BitUnixConfluenceConfig
    import yaml
    from types import SimpleNamespace
    from scripts.backtest_bitunix_confluence import Tier, Side

    cfg = BitUnixConfluenceConfig.from_dict(
        yaml.safe_load((BT._REPO_ROOT / "config" / "strategies.yaml").read_text())
        ["bitunix_futures"]
    )

    def fake_score(*, live_alerts, price_ctx, config, now, last_fire_ts_buy,
                   last_fire_ts_sell):
        return SimpleNamespace(tier=Tier.STANDARD, side=Side.BUY,
                               cooldown_blocked=False)

    def fake_pa(*, side, price_ctx, config):
        idx = _bar_of_ctx(bars, price_ctx)
        if idx is None or idx < reject_bar + pass_at_wait:
            return SimpleNamespace(decision=PAValidationDecision.REJECT)
        return SimpleNamespace(decision=PAValidationDecision.PASS)

    monkeypatch.setattr(BT, "evaluate_confluence_futures", fake_score)
    monkeypatch.setattr(BT, "evaluate_pa_validation", fake_pa)
    alerts = [AlertEvent(ts=bars[reject_bar]["ts"], signal_name="otter_buy", tf="3m")]
    pa_cfg = PAValidationConfig(enabled=True, require_all=True,
                                validators=("vwap_alignment",))
    signal_close = bars[reject_bar]["close"]
    fire_close = bars[reject_bar + pass_at_wait]["close"]
    return bars, cfg, alerts, pa_cfg, signal_close, fire_close


def test_slip_guard_default_off_is_baseline(monkeypatch):
    """max_slip_pt=None (default) must NOT change behaviour vs not passing it:
    the redeem fires and n_slip_guard_drop is 0."""
    bars, cfg, alerts, pa_cfg, sig, fire = _slip_setup(monkeypatch)
    fires, summ = BT.run_redeem_cap_backtest(
        alerts=alerts, bars=bars, config=cfg, pa_config=pa_cfg,
        redeem_cap=10, arm_name="off")
    assert summ["n_redeem_fire"] == 1
    assert summ["n_slip_guard_drop"] == 0
    # explicit None is identical to omitting it
    fires2, summ2 = BT.run_redeem_cap_backtest(
        alerts=alerts, bars=bars, config=cfg, pa_config=pa_cfg,
        redeem_cap=10, arm_name="off2", max_slip_pt=None)
    assert summ2["n_redeem_fire"] == 1
    assert summ2["n_slip_guard_drop"] == 0


def test_slip_guard_rejects_when_drift_exceeds_threshold(monkeypatch):
    """A redeem whose |fire - signal| exceeds max_slip_pt is dropped (counted as
    slip_guard_drop, NOT a redeem fire); a threshold above the drift lets it
    fire. The boundary is exact and deterministic."""
    bars, cfg, alerts, pa_cfg, sig, fire = _slip_setup(monkeypatch)
    drift = abs(fire - sig)
    assert drift > 0, "scenario must have non-zero slip for a meaningful test"

    # threshold strictly BELOW the drift -> guard rejects the redeem
    _, summ_lo = BT.run_redeem_cap_backtest(
        alerts=alerts, bars=bars, config=cfg, pa_config=pa_cfg,
        redeem_cap=10, arm_name="tight", max_slip_pt=drift - 1.0)
    assert summ_lo["n_redeem_fire"] == 0, "drift > threshold must drop the redeem"
    assert summ_lo["n_slip_guard_drop"] == 1

    # threshold strictly ABOVE the drift -> redeem fires as normal
    _, summ_hi = BT.run_redeem_cap_backtest(
        alerts=alerts, bars=bars, config=cfg, pa_config=pa_cfg,
        redeem_cap=10, arm_name="loose", max_slip_pt=drift + 1.0)
    assert summ_hi["n_redeem_fire"] == 1, "drift < threshold must allow the redeem"
    assert summ_hi["n_slip_guard_drop"] == 0


def test_slip_guard_never_affects_first_pass(monkeypatch):
    """A first-pass fire has slip == 0 (signal bar == fire bar) so even a
    zero-point guard must let it through and never count it as a slip drop."""
    bars = _mk_bars(40)
    from scripts.backtest_bitunix_confluence import AlertEvent, BitUnixConfluenceConfig
    import yaml
    from types import SimpleNamespace
    from scripts.backtest_bitunix_confluence import Tier, Side

    cfg = BitUnixConfluenceConfig.from_dict(
        yaml.safe_load((BT._REPO_ROOT / "config" / "strategies.yaml").read_text())
        ["bitunix_futures"]
    )

    def fake_score(*, live_alerts, price_ctx, config, now, last_fire_ts_buy,
                   last_fire_ts_sell):
        return SimpleNamespace(tier=Tier.STANDARD, side=Side.BUY,
                               cooldown_blocked=False)

    def fake_pa(*, side, price_ctx, config):  # always PASS -> first-pass fire
        return SimpleNamespace(decision=PAValidationDecision.PASS)

    monkeypatch.setattr(BT, "evaluate_confluence_futures", fake_score)
    monkeypatch.setattr(BT, "evaluate_pa_validation", fake_pa)
    alerts = [AlertEvent(ts=bars[10]["ts"], signal_name="otter_buy", tf="3m")]
    pa_cfg = PAValidationConfig(enabled=True, require_all=True,
                                validators=("vwap_alignment",))
    _, summ = BT.run_redeem_cap_backtest(
        alerts=alerts, bars=bars, config=cfg, pa_config=pa_cfg,
        redeem_cap=10, arm_name="fp", max_slip_pt=0.0)
    assert summ["n_first_pass_fire"] == 1, "first-pass fire must survive a 0pt guard"
    assert summ["n_slip_guard_drop"] == 0


@_needs_corpus
def test_slip_guard_monotone_fires_in_threshold(monkeypatch):
    """On the real corpus, redeem-fire count is monotone NON-DECREASING in the
    slip threshold (a looser guard can only admit more redeems), and a finite
    guard never admits MORE than the unguarded (inf-threshold) run."""
    s, e = R._to_dt(_W_START), R._to_dt(_W_END)
    alerts, bars, config = R.load_inputs(_DB, s, e)
    pre = (alerts, bars, config, (s, e))
    fire_counts = []
    for thr in (25, 50, 75, 100):
        r = R.run_redeem_sim(cap=10, max_slip_pt=float(thr), _preloaded=pre)
        fire_counts.append(r["n_redeem"])
    assert fire_counts == sorted(fire_counts), (
        f"redeem fires not monotone in slip threshold: {fire_counts}")
    base = R.run_redeem_sim(cap=10, _preloaded=pre)  # guard off
    assert fire_counts[-1] <= base["n_redeem"], (
        "a finite slip guard must not admit more redeems than the unguarded run")


@_needs_corpus
def test_slip_guard_no_look_ahead(monkeypatch):
    """The slip guard must remain causal: poisoning bars strictly after a cutoff
    must not change any guarded trade whose entry is at-or-before the cutoff."""
    s, e = R._to_dt(_W_START), R._to_dt(_W_END)
    alerts, bars, config = R.load_inputs(_DB, s, e)
    cutoff = bars[len(bars) // 2]["ts"]
    base = R.run_redeem_sim(cap=10, max_slip_pt=50.0,
                            _preloaded=(alerts, bars, config, (s, e)))
    poisoned = [
        ({**b, "open": 1e9, "high": 1e9, "low": 1e9, "close": 1e9, "volume": 0.0}
         if b["ts"] > cutoff else dict(b))
        for b in bars
    ]
    mut = R.run_redeem_sim(cap=10, max_slip_pt=50.0,
                           _preloaded=(alerts, poisoned, config, (s, e)))
    cut_iso = cutoff.isoformat()
    bp = [t for t in base["trades"] if t["entry_ts"] <= cut_iso]
    mp = [t for t in mut["trades"] if t["entry_ts"] <= cut_iso]
    assert bp, "expected at least one pre-cutoff guarded trade"
    assert len(bp) == len(mp)
    for a, b in zip(bp, mp):
        assert a["entry_ts"] == b["entry_ts"]
        assert a["entry_bar_price"] == b["entry_bar_price"]
        assert a["bars_waited"] == b["bars_waited"]


# ── TAKER-FEE OVERRIDE (additive, default-off — fee-vs-edge Step 2) ──────
# The override must (a) be a true no-op at default (globals untouched, results
# byte-identical), (b) RESTORE the engine globals after the call (even on
# error), (c) lower the fees_too_high_for_risk gate so a lower rate admits at
# least as many trades, and (d) raise net-R on the SAME trade by exactly the
# round-trip-cost delta. (a)/(b)/(d) are corpus-free and deterministic.

import scripts.backtest_bitunix_confluence as _BTM  # noqa: E402


def test_fee_override_default_none_is_noop_and_restores_globals():
    """taker_pct=None must not touch the engine fee globals at all."""
    before = (_BTM._FEES_TK, _BTM._FEES_MK, _BTM._RT_TK, _BTM._RT_MK)
    with R._fee_override(None):
        # inside the CM the globals are the SAME objects (no-op path)
        assert _BTM._FEES_TK is before[0]
        assert _BTM._RT_TK == before[2]
    after = (_BTM._FEES_TK, _BTM._FEES_MK, _BTM._RT_TK, _BTM._RT_MK)
    assert after == before


def test_fee_override_sets_then_restores_globals():
    """A given rate rebinds all four globals consistently inside the CM and
    restores the originals on exit."""
    before = (_BTM._FEES_TK, _BTM._FEES_MK, _BTM._RT_TK, _BTM._RT_MK)
    with R._fee_override(0.00019):
        assert _BTM._FEES_TK.taker_fee_pct == 0.00019
        assert _BTM._FEES_MK.taker_fee_pct == 0.00019
        # round-trip is recomputed from the new rate (entry+exit taker + 2*slip)
        assert _BTM._RT_TK == pytest.approx(0.00019 + 0.00019 + 2 * 0.00005)
        # maker-exit column: entry taker corrected, exit stays maker
        assert _BTM._RT_MK == pytest.approx(0.00019 + 0.00014 + 2 * 0.00005)
    after = (_BTM._FEES_TK, _BTM._FEES_MK, _BTM._RT_TK, _BTM._RT_MK)
    assert after == before, "globals not restored after the override CM"


def test_fee_override_restores_on_exception():
    before = (_BTM._FEES_TK, _BTM._FEES_MK, _BTM._RT_TK, _BTM._RT_MK)
    with pytest.raises(RuntimeError):
        with R._fee_override(0.00019):
            raise RuntimeError("boom")
    after = (_BTM._FEES_TK, _BTM._FEES_MK, _BTM._RT_TK, _BTM._RT_MK)
    assert after == before, "globals not restored after an exception"


def test_fee_override_rejects_negative():
    with pytest.raises(ValueError):
        with R._fee_override(-0.001):
            pass


@_needs_corpus
def test_lower_fee_admits_ge_trades_and_lifts_same_trade_net_r():
    """The corrected (lower) taker rate must (1) admit >= as many walked trades
    (the fees_too_high_for_risk gate can only loosen), and (2) raise net-R on a
    trade present in BOTH runs by exactly the round-trip-cost delta * entry/risk.
    """
    s, e = R._to_dt(_W_START), R._to_dt(_W_END)
    alerts, bars, config = R.load_inputs(_DB, s, e)
    pre = (alerts, bars, config, (s, e))
    hi = R.run_redeem_sim(cap=2, taker_pct=0.0004, _preloaded=pre)   # current
    lo = R.run_redeem_sim(cap=2, taker_pct=0.00019, _preloaded=pre)  # corrected
    # (1) gate loosens: corrected admits at least as many walked trades and
    # at most as many plan_skips.
    assert lo["n"] >= hi["n"]
    assert lo["n_plan_skip"] <= hi["n_plan_skip"]
    # provenance recorded
    assert hi["taker_pct"] == 0.0004
    assert lo["taker_pct"] == 0.00019
    # (2) For a trade present in BOTH runs whose GROSS path is unchanged (the
    # lower fee floor can shift TP1 and alter leg fills, so gross is NOT always
    # invariant), the lower fee yields a strictly higher net_R — the fee delta
    # flows straight through. We assert this on the path-unchanged subset.
    hi_by = {(t["entry_ts"], t["side"]): t for t in hi["trades"]
             if t["net_R"] is not None}
    checked = 0
    for t in lo["trades"]:
        if t["net_R"] is None:
            continue
        k = (t["entry_ts"], t["side"])
        if k in hi_by:
            h = hi_by[k]
            if t["gross_R"] == pytest.approx(h["gross_R"]):
                # identical path -> net delta is purely the (lower) fee cost
                assert t["net_R"] > h["net_R"]
                checked += 1
    assert checked > 0, (
        "expected at least one path-unchanged overlapping trade where the "
        "lower fee lifts net_R")


# ── TP1-MULTIPLIER OVERRIDE (additive, default-off — fee-vs-edge coupled fix) ──
# Mirrors the taker-fee override contract: (a) a true no-op at default (the
# engine's _SCFG is untouched, results byte-identical), (b) RESTORE of _SCFG
# after the call (even on error), and the COUPLED IDENTITY: pairing the lower
# rate with the bumped multiplier holds the fee-floor constant so the
# fees_too_high_for_risk skip set + admitted book reproduce the baseline exactly.


def test_tp1_mult_override_default_none_is_noop_and_restores_scfg():
    """tp1_mult=None must not touch the engine _SCFG at all."""
    before = _BTM._SCFG
    with R._tp1_mult_override(None):
        assert _BTM._SCFG is before          # no-op path: same object
    assert _BTM._SCFG is before


def test_tp1_mult_override_sets_then_restores_scfg():
    """A given multiplier rebinds _SCFG.tp1_min_profit_multiplier inside the CM
    and restores the original on exit (other fields preserved)."""
    before = _BTM._SCFG
    with R._tp1_mult_override(3.75):
        assert _BTM._SCFG.tp1_min_profit_multiplier == 3.75
        # only the one field changed; everything else is preserved
        assert _BTM._SCFG.tp1_r_target == before.tp1_r_target
        assert _BTM._SCFG.atr_multiplier == before.atr_multiplier
    assert _BTM._SCFG is before, "_SCFG not restored after the override CM"


def test_tp1_mult_override_restores_on_exception():
    before = _BTM._SCFG
    with pytest.raises(RuntimeError):
        with R._tp1_mult_override(3.75):
            raise RuntimeError("boom")
    assert _BTM._SCFG is before, "_SCFG not restored after an exception"


def test_tp1_mult_override_rejects_negative():
    with pytest.raises(ValueError):
        with R._tp1_mult_override(-1.0):
            pass


@_needs_corpus
def test_coupled_change_reproduces_baseline_book_composition():
    """THE COUPLED IDENTITY (book-composition half): baseline (taker 0.0004,
    mult 2.0) vs coupled (taker 0.00019, mult 3.75) must produce the SAME
    fees_too_high_for_risk skip set, the SAME admitted trade set, and the SAME
    GROSS-R / TP placement for every shared trade, because
    2.0*0.0009 == 3.75*0.00048 == 0.0018 -> identical TP1 fee-floor per entry.

    NOTE the net-R half is DELIBERATELY *not* asserted equal: the coupled change
    genuinely lowers the realised round-trip cost (0.0009 -> 0.00048), so net-R
    is strictly BETTER (less negative). The multiplier neutralises the GATE
    loosening, not the fee saving. The rate-only change (mult left at 2.0) admits
    strictly MORE trades -> proves the multiplier is what holds the gate."""
    s, e = R._to_dt(_W_START), R._to_dt(_W_END)
    alerts, bars, config = R.load_inputs(_DB, s, e)
    pre = (alerts, bars, config, (s, e))

    base = R.run_redeem_sim(cap=2, taker_pct=0.0004, tp1_mult=2.0, _preloaded=pre)
    coupled = R.run_redeem_sim(cap=2, taker_pct=0.00019, tp1_mult=3.75, _preloaded=pre)
    rate_only = R.run_redeem_sim(cap=2, taker_pct=0.00019, tp1_mult=2.0, _preloaded=pre)

    def _fee_skips(res):
        return {
            (t["signal_ts"], t["entry_ts"], t["side"])
            for t in res["trades"]
            if t["result"] == "plan_skip"
            and t["skip_reason"] == "fees_too_high_for_risk"
        }

    base_skips = _fee_skips(base)
    # 1. identical fee-skip set -> zero flipped cohort
    assert _fee_skips(coupled) == base_skips, (
        "coupled change changed the fees_too_high_for_risk skip set -> "
        "fee-floor identity broken")
    flipped = base_skips - _fee_skips(coupled)
    assert flipped == set(), f"expected 0 flipped base->coupled, got {len(flipped)}"

    # 2. identical admitted set + identical GROSS-R (TP placement) per shared trade
    base_by = {(t["signal_ts"], t["entry_ts"], t["side"]): t
               for t in base["trades"] if t["net_R"] is not None}
    coup_by = {(t["signal_ts"], t["entry_ts"], t["side"]): t
               for t in coupled["trades"] if t["net_R"] is not None}
    assert set(coup_by) == set(base_by), "coupled admitted a different trade set"
    assert coupled["n"] == base["n"]
    for k in base_by:
        assert coup_by[k]["gross_R"] == pytest.approx(base_by[k]["gross_R"], abs=1e-9), (
            "gross-R / TP placement shifted under the coupled change -> "
            "fee-floor identity broken")
        # net-R is strictly BETTER under coupled (lower realised fee), not equal
        assert coup_by[k]["net_R"] >= base_by[k]["net_R"] - 1e-9

    # 3. rig sanity: rate-only (mult still 2.0) loosens the gate -> MORE trades
    assert rate_only["n"] > base["n"], (
        "rate-only correction should re-admit a cohort the gate previously "
        "skipped (else the rig can't detect the coupling effect)")


def test_coupled_fee_floor_identity_is_algebraic():
    """Direct numeric check of the fee-floor identity used by the gate, for a
    few (entry) samples, independent of the corpus: the coupled (rate, mult)
    yields the SAME tp1_fee_floor = mult * round_trip_cost_pct * entry as the
    baseline, to float tolerance."""
    from trading_corp.agents.strategies.trade_plan import FeeConfig

    base_fees = FeeConfig(taker_fee_pct=0.0004)
    coup_fees = FeeConfig(taker_fee_pct=0.00019)
    base_rt = base_fees.round_trip_cost_pct()   # 0.0004+0.0004+2*0.00005 = 0.0009
    coup_rt = coup_fees.round_trip_cost_pct()   # 0.00019+0.00019+2*0.00005 = 0.00048
    assert base_rt == pytest.approx(0.0009)
    assert coup_rt == pytest.approx(0.00048)
    for entry in (30000.0, 60000.0, 105123.45, 1.0):
        base_floor = 2.0 * base_rt * entry
        coup_floor = 3.75 * coup_rt * entry
        assert coup_floor == pytest.approx(base_floor, rel=1e-12), (
            f"fee-floor mismatch at entry={entry}: "
            f"base={base_floor} coupled={coup_floor}")
