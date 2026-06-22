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
