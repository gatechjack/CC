"""Tests for _query_kalshi_whale_intel — per-whale copy-quality intel.

Network-free. Uses a synthetic SQLite DB built from scratch with
_db.init_db() + direct SQL inserts into audit_event + kalshi_round_trips.

Cost model under test (mirrors kanalysis.py 2026-06-21):
  fee = ceil(0.07 * C * P * (1-P)) per traded side
  slip = $0.01 / contract per traded side
  entry side always counted; exit counted only when 0 < exit_price < 1
  (pre-resolution exit). Settled (exit_price NULL/0/1) → exit fee+slip = 0.

Skip kinds:
  kalshi_copy_entry_skipped_no_side  — payload.whale_handle (primary) or .whale
  kalshi_copy_entry_skipped_sports   — payload.whale (primary) or .whale_handle
  would_have_placed (side=buy)       — payload.whale_handle → copies + days_since
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone, timedelta

import pytest

from trading_corp.persistence import db as _db
from trading_corp.web.data import _query_kalshi_whale_intel, _query_pm_whales


# ── helpers ──────────────────────────────────────────────────────────────────

def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _insert_audit(conn, ts: str, actor: str, kind: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO audit_event(ts, actor, kind, payload_json) VALUES (?,?,?,?)",
        (ts, actor, kind, json.dumps(payload)),
    )


def _insert_rt(
    conn,
    order_id: str,
    ticker: str,
    qty: float,
    entry_price: float,
    realized_pnl: float,
    won: int,
    division: str,
    whale_handle: str | None = None,
    exit_price: float | None = None,
) -> None:
    extra = {}
    if whale_handle is not None:
        extra["whale_handle"] = whale_handle
    if exit_price is not None:
        extra["exit_price"] = exit_price
    conn.execute(
        """INSERT INTO kalshi_round_trips
           (order_id, ticker, event_ticker, event_title, category, strategy, division,
            arb_type, arb_set_id, outcome_bet, qty, entry_price, notional,
            entry_ts, resolved_ts, market_result, won, realized_pnl, roi_pct, extra_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            order_id, ticker, None, None, None, "kalshi_copy_trader", division,
            None, None, "yes", qty, entry_price, qty * entry_price,
            "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00",
            "yes" if won else "no", won, realized_pnl, 0.0,
            json.dumps(extra) if extra else None,
        ),
    )


def _fee(c: float, p: float) -> float:
    """Mirror the production fee formula."""
    p = max(0.0, min(1.0, p))
    return math.ceil(0.07 * c * p * (1.0 - p) * 100.0) / 100.0


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def db_url(tmp_path):
    path = tmp_path / "intel_test.db"
    url = f"sqlite:///{path}"
    _db.init_db(url)
    return url


# ── basic behaviour ───────────────────────────────────────────────────────────

def test_empty_handles_returns_empty(db_url):
    assert _query_kalshi_whale_intel(db_url, []) == {}


def test_unknown_handle_returns_defaults(db_url):
    result = _query_kalshi_whale_intel(db_url, ["nobody.here"])
    d = result["nobody.here"]
    assert d["copies"] == 0
    assert d["detections"] == 0
    assert d["no_side"] == 0
    assert d["sports"] == 0
    assert d["copyability_pct"] is None
    assert d["net_pnl"] == 0.0
    assert d["n_resolved"] == 0
    assert d["hit_rate_pct"] is None
    assert d["days_since_last_copy"] is None
    assert d["crypto_pct"] is None


# ── copies + days_since_last_copy ─────────────────────────────────────────────

def test_copies_counted_from_would_have_placed(db_url):
    now = datetime.now(timezone.utc)
    with _db.connect(db_url) as conn:
        _insert_audit(conn, _iso(now - timedelta(days=2)), "kalshi_copy_trader",
                      "would_have_placed", {"whale_handle": "alpha.whale", "side": "buy"})
        _insert_audit(conn, _iso(now - timedelta(days=1)), "kalshi_copy_trader",
                      "would_have_placed", {"whale_handle": "alpha.whale", "side": "buy"})
        conn.commit()

    result = _query_kalshi_whale_intel(db_url, ["alpha.whale"])
    d = result["alpha.whale"]
    assert d["copies"] == 2
    # days_since ≈ 1.0 (last copy was 1 day ago)
    assert d["days_since_last_copy"] == pytest.approx(1.0, abs=0.1)


def test_sell_side_not_counted_as_copy(db_url):
    """Only side='buy' audit entries count as copies."""
    now = datetime.now(timezone.utc)
    with _db.connect(db_url) as conn:
        _insert_audit(conn, _iso(now), "kalshi_copy_trader",
                      "would_have_placed", {"whale_handle": "sell.only", "side": "sell"})
        conn.commit()

    result = _query_kalshi_whale_intel(db_url, ["sell.only"])
    assert result["sell.only"]["copies"] == 0


def test_different_actor_not_counted(db_url):
    """Non kalshi_copy_trader actor ignored."""
    now = datetime.now(timezone.utc)
    with _db.connect(db_url) as conn:
        _insert_audit(conn, _iso(now), "polymarket_copy_trader",
                      "would_have_placed", {"whale_handle": "wrong.actor", "side": "buy"})
        conn.commit()

    result = _query_kalshi_whale_intel(db_url, ["wrong.actor"])
    assert result["wrong.actor"]["copies"] == 0


# ── skip breakdown ────────────────────────────────────────────────────────────

def test_no_side_skips_via_whale_handle(db_url):
    with _db.connect(db_url) as conn:
        for _ in range(5):
            _insert_audit(conn, "2026-01-01T00:00:00+00:00", "kalshi_copy_trader",
                          "kalshi_copy_entry_skipped_no_side",
                          {"whale_handle": "lengthy.starfish"})
        conn.commit()

    result = _query_kalshi_whale_intel(db_url, ["lengthy.starfish"])
    assert result["lengthy.starfish"]["no_side"] == 5


def test_no_side_skips_via_whale_fallback(db_url):
    """payload.whale (no whale_handle key) is the fallback for no_side."""
    with _db.connect(db_url) as conn:
        _insert_audit(conn, "2026-01-01T00:00:00+00:00", "kalshi_copy_trader",
                      "kalshi_copy_entry_skipped_no_side",
                      {"whale": "fallback.whale"})
        conn.commit()

    result = _query_kalshi_whale_intel(db_url, ["fallback.whale"])
    assert result["fallback.whale"]["no_side"] == 1


def test_sports_skips_via_whale_key(db_url):
    """payload.whale (primary) for sports skip."""
    with _db.connect(db_url) as conn:
        for _ in range(3):
            _insert_audit(conn, "2026-01-01T00:00:00+00:00", "kalshi_copy_trader",
                          "kalshi_copy_entry_skipped_sports",
                          {"whale": "sporty.mcwhale"})
        conn.commit()

    result = _query_kalshi_whale_intel(db_url, ["sporty.mcwhale"])
    assert result["sporty.mcwhale"]["sports"] == 3


def test_sports_skips_via_whale_handle_fallback(db_url):
    """payload.whale_handle fallback for sports skip."""
    with _db.connect(db_url) as conn:
        _insert_audit(conn, "2026-01-01T00:00:00+00:00", "kalshi_copy_trader",
                      "kalshi_copy_entry_skipped_sports",
                      {"whale_handle": "sporty2"})
        conn.commit()

    result = _query_kalshi_whale_intel(db_url, ["sporty2"])
    assert result["sporty2"]["sports"] == 1


# ── copyability ratio ─────────────────────────────────────────────────────────

def test_lengthy_starfish_copyability(db_url):
    """lengthy.starfish: 4 copies vs 1845 no_side → copyability ≈ 0.2%."""
    now = datetime.now(timezone.utc)
    with _db.connect(db_url) as conn:
        for _ in range(4):
            _insert_audit(conn, _iso(now), "kalshi_copy_trader",
                          "would_have_placed",
                          {"whale_handle": "lengthy.starfish", "side": "buy"})
        for _ in range(1845):
            _insert_audit(conn, "2026-01-01T00:00:00+00:00", "kalshi_copy_trader",
                          "kalshi_copy_entry_skipped_no_side",
                          {"whale_handle": "lengthy.starfish"})
        conn.commit()

    result = _query_kalshi_whale_intel(db_url, ["lengthy.starfish"])
    d = result["lengthy.starfish"]
    assert d["copies"] == 4
    assert d["no_side"] == 1845
    assert d["detections"] == 1849  # 4+1845+0
    assert d["copyability_pct"] == pytest.approx(100.0 * 4 / 1849, abs=0.2)
    # < 5% → structurally uncopyable
    assert d["copyability_pct"] < 5.0


def test_copyability_none_when_no_detections(db_url):
    result = _query_kalshi_whale_intel(db_url, ["ghost.whale"])
    assert result["ghost.whale"]["copyability_pct"] is None


def test_copyability_100_all_copies(db_url):
    """All detections are copies → 100%."""
    now = datetime.now(timezone.utc)
    with _db.connect(db_url) as conn:
        for _ in range(10):
            _insert_audit(conn, _iso(now), "kalshi_copy_trader",
                          "would_have_placed",
                          {"whale_handle": "perfect.whale", "side": "buy"})
        conn.commit()

    result = _query_kalshi_whale_intel(db_url, ["perfect.whale"])
    assert result["perfect.whale"]["copyability_pct"] == pytest.approx(100.0, abs=0.01)


# ── net PnL (fee + slippage model) ───────────────────────────────────────────

def test_net_pnl_settled_entry(db_url):
    """Settled exit (exit_price=None): only entry fee + slip charged once."""
    # C=10, ep=0.5, realized_pnl=+5.0 (won)
    c, ep = 10, 0.5
    gross = 5.0
    ef = _fee(c, ep)        # ceil(0.07*10*0.5*0.5*100)/100 = ceil(17.5)/100 = 0.18
    sl = 0.01 * c * 1       # settled → 1 side of slip = 0.10
    expected_net = round(gross - ef - sl, 2)

    with _db.connect(db_url) as conn:
        _insert_rt(conn, "rt-settled-1", "KXBTC-001", c, ep, gross, 1,
                   "kalshi_copy_trading", whale_handle="settled.whale")
        conn.commit()

    result = _query_kalshi_whale_intel(db_url, ["settled.whale"])
    d = result["settled.whale"]
    assert d["n_resolved"] == 1
    assert d["net_pnl"] == pytest.approx(expected_net, abs=0.01)
    assert d["hit_rate_pct"] == 100.0


def test_net_pnl_pre_resolution_exit(db_url):
    """Pre-resolution exit (0 < exit_price < 1): entry + exit fee + 2 sides slip."""
    c, ep, xp = 8, 0.6, 0.35
    gross = -2.0   # lost money
    ef = _fee(c, ep)
    xf = _fee(c, xp)
    sl = 0.01 * c * 2  # two sides (entry + pre-resolution exit)
    expected_net = round(gross - ef - xf - sl, 2)

    with _db.connect(db_url) as conn:
        _insert_rt(conn, "rt-preresol-1", "KXETH-001", c, ep, gross, 0,
                   "kalshi_copy_trading", whale_handle="preresol.whale",
                   exit_price=xp)
        conn.commit()

    result = _query_kalshi_whale_intel(db_url, ["preresol.whale"])
    d = result["preresol.whale"]
    assert d["net_pnl"] == pytest.approx(expected_net, abs=0.01)
    assert d["hit_rate_pct"] == 0.0


def test_net_pnl_exit_price_zero_treated_as_settled(db_url):
    """exit_price=0 → settled (no extra fee/slip beyond entry)."""
    c, ep = 5, 0.7
    gross = 3.0
    ef = _fee(c, ep)
    sl = 0.01 * c * 1
    expected_net = round(gross - ef - sl, 2)

    with _db.connect(db_url) as conn:
        _insert_rt(conn, "rt-xp0-1", "KXSOL-001", c, ep, gross, 1,
                   "kalshi_copy_trading", whale_handle="xp0.whale",
                   exit_price=0.0)
        conn.commit()

    result = _query_kalshi_whale_intel(db_url, ["xp0.whale"])
    assert result["xp0.whale"]["net_pnl"] == pytest.approx(expected_net, abs=0.01)


def test_net_pnl_accumulates_multiple_trades(db_url):
    """Net PnL sums over all resolved round-trips for a whale."""
    c, ep = 5, 0.5
    ef = _fee(c, ep)
    sl = 0.01 * c
    # 3 wins + 1 loss
    trades = [(1.5, 1), (2.0, 1), (-0.5, 0), (1.0, 1)]
    expected_net = round(sum(g - ef - sl for g, _ in trades), 2)

    with _db.connect(db_url) as conn:
        for i, (gross, won) in enumerate(trades):
            _insert_rt(conn, f"rt-acc-{i}", "KXBTC-001", c, ep, gross, won,
                       "kalshi_copy_trading", whale_handle="multi.whale")
        conn.commit()

    result = _query_kalshi_whale_intel(db_url, ["multi.whale"])
    d = result["multi.whale"]
    assert d["n_resolved"] == 4
    assert d["net_pnl"] == pytest.approx(expected_net, abs=0.01)
    assert d["hit_rate_pct"] == pytest.approx(75.0, abs=0.1)


# ── the.hoff.85 net-negative case ────────────────────────────────────────────

def test_hoff_net_negative(db_url):
    """the.hoff.85: 733 resolved, mixed WR, net PnL should be negative."""
    now = datetime.now(timezone.utc)
    # Simulate 733 copies (audit) and 733 round-trips with net-negative outcome
    with _db.connect(db_url) as conn:
        for _ in range(733):
            _insert_audit(conn, _iso(now), "kalshi_copy_trader",
                          "would_have_placed",
                          {"whale_handle": "the.hoff.85", "side": "buy"})
        # Insert a handful of round-trips that produce negative net PnL
        # (fees eat the gross profit on low-edge trades)
        c, ep = 1, 0.5
        ef = _fee(c, ep)
        sl = 0.01 * c
        for i in range(10):
            # Even "winning" gross pnl = 0.30 barely covers fee+slip
            gross = 0.30 if i % 2 == 0 else -0.70
            won = 1 if gross > 0 else 0
            _insert_rt(conn, f"hoff-{i}", "KXBTC-001", c, ep, gross, won,
                       "kalshi_copy_trading", whale_handle="the.hoff.85")
        conn.commit()

    result = _query_kalshi_whale_intel(db_url, ["the.hoff.85"])
    d = result["the.hoff.85"]
    assert d["copies"] == 733
    assert d["n_resolved"] == 10
    # Net PnL must be negative (fees exceed gross gains)
    expected_net = round(sum(
        (0.30 if i % 2 == 0 else -0.70) - ef - sl for i in range(10)
    ), 2)
    assert d["net_pnl"] == pytest.approx(expected_net, abs=0.01)
    assert d["net_pnl"] < 0.0


# ── crypto classification ─────────────────────────────────────────────────────

def test_crypto_pct_mixed_tickers(db_url):
    """KXBTC/KXETH tickers are crypto; others are not."""
    with _db.connect(db_url) as conn:
        for ticker in ["KXBTC-23DEC", "KXETH-001", "KXSOL-Q1", "NASDAQ-001", "PRES-2024"]:
            _insert_rt(conn, f"rt-{ticker}", ticker, 1, 0.5, 0.3, 1,
                       "kalshi_copy_trading", whale_handle="mix.whale")
        conn.commit()

    result = _query_kalshi_whale_intel(db_url, ["mix.whale"])
    d = result["mix.whale"]
    assert d["n_resolved"] == 5
    # 3 crypto (BTC/ETH/SOL), 2 non-crypto
    assert d["crypto_pct"] == pytest.approx(60.0, abs=0.1)


def test_crypto_pct_all_non_crypto(db_url):
    with _db.connect(db_url) as conn:
        _insert_rt(conn, "rt-noncrypto", "PRES-DEM-2024", 2, 0.4, 0.8, 1,
                   "kalshi_copy_trading", whale_handle="nocrypto.whale")
        conn.commit()

    result = _query_kalshi_whale_intel(db_url, ["nocrypto.whale"])
    assert result["nocrypto.whale"]["crypto_pct"] == pytest.approx(0.0, abs=0.01)


def test_crypto_pct_all_crypto(db_url):
    with _db.connect(db_url) as conn:
        for ticker in ["KXBTC-001", "KXDOGE-001", "KXXRP-001"]:
            _insert_rt(conn, f"rt-{ticker}", ticker, 3, 0.6, 0.5, 1,
                       "kalshi_copy_trading", whale_handle="allcrypto.whale")
        conn.commit()

    result = _query_kalshi_whale_intel(db_url, ["allcrypto.whale"])
    assert result["allcrypto.whale"]["crypto_pct"] == pytest.approx(100.0, abs=0.01)


# ── multiple whales in one call ───────────────────────────────────────────────

def test_multiple_whales_isolated(db_url):
    """Intel for whale A does not bleed into whale B."""
    now = datetime.now(timezone.utc)
    with _db.connect(db_url) as conn:
        for _ in range(3):
            _insert_audit(conn, _iso(now), "kalshi_copy_trader",
                          "would_have_placed",
                          {"whale_handle": "whale.a", "side": "buy"})
        for _ in range(7):
            _insert_audit(conn, "2026-01-01T00:00:00+00:00", "kalshi_copy_trader",
                          "kalshi_copy_entry_skipped_no_side",
                          {"whale_handle": "whale.b"})
        conn.commit()

    result = _query_kalshi_whale_intel(db_url, ["whale.a", "whale.b"])
    assert result["whale.a"]["copies"] == 3
    assert result["whale.a"]["no_side"] == 0
    assert result["whale.b"]["copies"] == 0
    assert result["whale.b"]["no_side"] == 7


def test_round_trips_wrong_division_excluded(db_url):
    """Round-trips in a different division are not counted."""
    with _db.connect(db_url) as conn:
        _insert_rt(conn, "rt-wrong-div", "KXBTC-001", 10, 0.5, 5.0, 1,
                   "kalshi_arbitrage", whale_handle="div.whale")
        conn.commit()

    result = _query_kalshi_whale_intel(db_url, ["div.whale"],
                                        division="kalshi_copy_trading")
    d = result["div.whale"]
    assert d["n_resolved"] == 0
    assert d["net_pnl"] == 0.0


def test_round_trips_no_whale_handle_excluded(db_url):
    """Round-trips without whale_handle in extra_json are excluded."""
    with _db.connect(db_url) as conn:
        # Insert without whale_handle (different strategy context)
        conn.execute(
            """INSERT INTO kalshi_round_trips
               (order_id, ticker, event_ticker, event_title, category, strategy, division,
                arb_type, arb_set_id, outcome_bet, qty, entry_price, notional,
                entry_ts, resolved_ts, market_result, won, realized_pnl, roi_pct, extra_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("rt-no-whale", "KXBTC-001", None, None, None,
             "kalshi_copy_trader", "kalshi_copy_trading",
             None, None, "yes", 5, 0.5, 2.5,
             "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00",
             "yes", 1, 2.5, 0.0, json.dumps({"some_other": "field"})),
        )
        conn.commit()

    # This whale name is not referenced anywhere
    result = _query_kalshi_whale_intel(db_url, ["unknown.whale.x"])
    assert result["unknown.whale.x"]["n_resolved"] == 0


# ── _query_pm_whales — Selected Whales sort + filter ─────────────────────────
#
# These tests verify the SORT + FILTER machinery added to _query_pm_whales for
# the Kalshi Selected Whales panel (independent of the Watch List).
#
# Fixtures seed:
#   - agent_state(kalshi_copy_trader, selected_whales) — 3 Kalshi handles
#   - kalshi_round_trips — gives each whale their n_resolved base count
#   - audit_event — gives each whale their intel fields (copies, no_side, etc.)
#   - For the polymarket isolation test: agent_state(polymarket_copy_trader, ...)
#
# All tests are read-only + network-free.

def _seed_selected_pm_whales(db_url: str) -> None:
    """Seed 3 Kalshi selected whales with distinct intel profiles.

    whale.alpha  — copyability 100%  (10 detections all copies), net_pnl > 0  (10 wins)
    whale.beta   — copyability 0%    (20 no_side, 0 copies),     net_pnl = 0.0 (0 resolved)
    whale.gamma  — copyability 50%   (4 copies / 8 detections),  net_pnl < 0   (31 losses, net-neg)
    """
    _db.set_agent_state(
        "kalshi_copy_trader", "selected_whales",
        ["whale.alpha", "whale.beta", "whale.gamma"],
        db_url=db_url,
    )

    now = datetime.now(timezone.utc)

    with _db.connect(db_url) as conn:
        # alpha: 10 copies (would_have_placed buy)
        for _ in range(10):
            _insert_audit(conn, _iso(now), "kalshi_copy_trader",
                          "would_have_placed",
                          {"whale_handle": "whale.alpha", "side": "buy"})
        # beta: 20 no_side skips, 0 copies
        for _ in range(20):
            _insert_audit(conn, _iso(now), "kalshi_copy_trader",
                          "kalshi_copy_entry_skipped_no_side",
                          {"whale_handle": "whale.beta"})
        # gamma: 4 copies + 4 no_side → copyability 50%
        for _ in range(4):
            _insert_audit(conn, _iso(now), "kalshi_copy_trader",
                          "would_have_placed",
                          {"whale_handle": "whale.gamma", "side": "buy"})
        for _ in range(4):
            _insert_audit(conn, _iso(now), "kalshi_copy_trader",
                          "kalshi_copy_entry_skipped_no_side",
                          {"whale_handle": "whale.gamma"})
        # alpha: 10 winning round-trips, positive net PnL
        c_a, ep_a = 5, 0.5
        for i in range(10):
            _insert_rt(conn, f"alpha-rt-{i}", "KXBTC-001", c_a, ep_a, 2.0, 1,
                       "kalshi_copy_trading", whale_handle="whale.alpha")
        # beta: 0 round-trips (intel_net_pnl stays None)
        # gamma: 31 losing round-trips → n_resolved=31 ≥ 30, net PnL < 0
        c_g, ep_g = 2, 0.5
        for i in range(31):
            _insert_rt(conn, f"gamma-rt-{i}", "KXBTC-001", c_g, ep_g, -1.0, 0,
                       "kalshi_copy_trading", whale_handle="whale.gamma")
        conn.commit()


def test_pm_whales_sort_by_intel_copies_desc(db_url):
    """sort by intel_copies descending: alpha(10) > gamma(4) > beta(0)."""
    _seed_selected_pm_whales(db_url)

    rows = _query_pm_whales(
        db_url, ["kalshi_copy_trading"],
        selected_sort="copies", selected_desc=True,
    )
    kalshi_rows = [w for w in rows if w.venue == "kalshi"]
    handles = [w.handle for w in kalshi_rows]
    # alpha has most copies; beta has 0 → appended after (None-trailing logic
    # doesn't apply since 0 is not None, but alpha > gamma > beta by count)
    assert handles.index("whale.alpha") < handles.index("whale.gamma"), (
        f"Expected alpha before gamma, got {handles}"
    )
    assert handles.index("whale.gamma") < handles.index("whale.beta"), (
        f"Expected gamma before beta, got {handles}"
    )


def test_pm_whales_sort_by_intel_copies_asc(db_url):
    """sort ascending: beta(0) or gamma(4) first, alpha(10) last."""
    _seed_selected_pm_whales(db_url)

    rows = _query_pm_whales(
        db_url, ["kalshi_copy_trading"],
        selected_sort="copies", selected_desc=False,
    )
    kalshi_rows = [w for w in rows if w.venue == "kalshi"]
    handles = [w.handle for w in kalshi_rows]
    assert handles.index("whale.alpha") > handles.index("whale.gamma"), (
        f"Expected alpha after gamma in ASC order, got {handles}"
    )
    assert handles.index("whale.gamma") > handles.index("whale.beta"), (
        f"Expected gamma after beta in ASC order, got {handles}"
    )


def test_pm_whales_sort_by_net_pnl_desc(db_url):
    """sort by net_pnl descending: alpha (positive) > beta (0.0) > gamma (negative).

    Note: _query_kalshi_whale_intel defaults net_pnl to 0.0 (not None) for
    whales with no resolved round-trips, so beta (no round-trips) sorts between
    alpha and gamma rather than trailing-None."""
    _seed_selected_pm_whales(db_url)

    rows = _query_pm_whales(
        db_url, ["kalshi_copy_trading"],
        selected_sort="net_pnl", selected_desc=True,
    )
    kalshi_rows = [w for w in rows if w.venue == "kalshi"]
    handles = [w.handle for w in kalshi_rows]
    # alpha net_pnl > 0; beta net_pnl = 0.0; gamma net_pnl < 0
    # DESC order: alpha → beta → gamma
    assert handles.index("whale.alpha") < handles.index("whale.beta"), (
        f"Expected alpha before beta in net_pnl DESC, got {handles}"
    )
    assert handles.index("whale.beta") < handles.index("whale.gamma"), (
        f"Expected beta before gamma in net_pnl DESC, got {handles}"
    )


def test_pm_whales_hide_uncopyable_drops_low_copyability_kalshi(db_url):
    """hide_uncopyable removes kalshi rows with copyability < 5% (beta: 0%)."""
    _seed_selected_pm_whales(db_url)

    rows_without = _query_pm_whales(db_url, ["kalshi_copy_trading"])
    rows_with = _query_pm_whales(
        db_url, ["kalshi_copy_trading"],
        hide_uncopyable=True,
    )
    without_handles = {w.handle for w in rows_without if w.venue == "kalshi"}
    with_handles = {w.handle for w in rows_with if w.venue == "kalshi"}

    assert "whale.beta" in without_handles, "beta should appear without filter"
    assert "whale.beta" not in with_handles, (
        "beta (0% copyability, detections>0) should be hidden by hide_uncopyable"
    )
    # alpha and gamma remain (alpha=100%, gamma=50% — both ≥ 5%)
    assert "whale.alpha" in with_handles
    assert "whale.gamma" in with_handles


def test_pm_whales_hide_uncopyable_keeps_zero_detection_rows(db_url):
    """Rows with intel_detections==0 are NOT dropped by hide_uncopyable.
    (no detections at all ≠ structurally uncopyable; it's just silent.)"""
    _db.set_agent_state(
        "kalshi_copy_trader", "selected_whales",
        ["silent.whale"],
        db_url=db_url,
    )
    # silent.whale has no audit_event entries → detections=0, copyability=None

    rows = _query_pm_whales(
        db_url, ["kalshi_copy_trading"],
        hide_uncopyable=True,
    )
    handles = {w.handle for w in rows if w.venue == "kalshi"}
    assert "silent.whale" in handles, (
        "silent whale (0 detections) must NOT be hidden by hide_uncopyable"
    )


def test_pm_whales_hide_net_neg_drops_confirmed_loser(db_url):
    """hide_net_neg removes kalshi rows with n_resolved>=30 and net_pnl<0."""
    _seed_selected_pm_whales(db_url)

    rows_without = _query_pm_whales(db_url, ["kalshi_copy_trading"])
    rows_with = _query_pm_whales(
        db_url, ["kalshi_copy_trading"],
        hide_net_neg=True,
    )
    without_handles = {w.handle for w in rows_without if w.venue == "kalshi"}
    with_handles = {w.handle for w in rows_with if w.venue == "kalshi"}

    assert "whale.gamma" in without_handles, "gamma should appear without filter"
    assert "whale.gamma" not in with_handles, (
        "gamma (n_resolved=31, net_pnl<0) should be hidden by hide_net_neg"
    )
    # alpha (net_pnl > 0) and beta (n_resolved=0 < 30) remain
    assert "whale.alpha" in with_handles
    assert "whale.beta" in with_handles


def test_pm_whales_hide_net_neg_keeps_insufficient_sample(db_url):
    """n_resolved < 30 → not dropped even if net_pnl < 0."""
    _db.set_agent_state(
        "kalshi_copy_trader", "selected_whales",
        ["small.sample"],
        db_url=db_url,
    )
    # 5 losing round-trips → n_resolved=5 < 30; net_pnl < 0
    with _db.connect(db_url) as conn:
        for i in range(5):
            _insert_rt(conn, f"ss-rt-{i}", "KXBTC-001", 1, 0.5, -1.0, 0,
                       "kalshi_copy_trading", whale_handle="small.sample")
        conn.commit()

    rows = _query_pm_whales(
        db_url, ["kalshi_copy_trading"],
        hide_net_neg=True,
    )
    handles = {w.handle for w in rows if w.venue == "kalshi"}
    assert "small.sample" in handles, (
        "small.sample (n_resolved=5 < 30) must NOT be hidden by hide_net_neg"
    )


def test_pm_whales_filters_kalshi_only_polymarket_untouched(db_url):
    """hide_uncopyable + hide_net_neg affect ONLY kalshi rows.
    Polymarket rows pass through unchanged regardless of their (absent) intel."""
    # Seed kalshi selected
    _db.set_agent_state(
        "kalshi_copy_trader", "selected_whales",
        ["uncopyable.ks"],
        db_url=db_url,
    )
    # Seed 20 no_side for the kalshi whale → copyability=0, should be hidden
    with _db.connect(db_url) as conn:
        for _ in range(20):
            _insert_audit(conn, "2026-01-01T00:00:00+00:00", "kalshi_copy_trader",
                          "kalshi_copy_entry_skipped_no_side",
                          {"whale_handle": "uncopyable.ks"})
        conn.commit()

    # Seed polymarket selected + a round-trip so the PM whale gets a real row
    _db.set_agent_state(
        "polymarket_copy_trader", "selected_whales",
        [{"wallet": "0xpm001", "user_name": "poly.whale",
          "category": "Politics", "promoted_iso": "2026-01-01T00:00:00+00:00",
          "source": "seed"}],
        db_url=db_url,
    )

    rows = _query_pm_whales(
        db_url, ["kalshi_copy_trading", "polymarket_copy_trading"],
        hide_uncopyable=True,
        hide_net_neg=True,
    )
    ks_handles = {w.handle for w in rows if w.venue == "kalshi"}
    pm_handles = {w.handle for w in rows if w.venue == "polymarket"}

    assert "uncopyable.ks" not in ks_handles, (
        "uncopyable.ks should be hidden by hide_uncopyable"
    )
    assert "poly.whale" in pm_handles, (
        "polymarket whale must survive kalshi filters"
    )


def test_pm_whales_no_sort_preserves_default_order(db_url):
    """With no selected_sort kwarg, kalshi rows use the existing default sort
    (n_resolved==0 last, then highest total_realized_pnl first)."""
    _seed_selected_pm_whales(db_url)

    rows_default = _query_pm_whales(db_url, ["kalshi_copy_trading"])
    rows_explicit_none = _query_pm_whales(
        db_url, ["kalshi_copy_trading"],
        selected_sort=None,
    )
    ks_default = [(w.handle, w.n_resolved) for w in rows_default if w.venue == "kalshi"]
    ks_none = [(w.handle, w.n_resolved) for w in rows_explicit_none if w.venue == "kalshi"]
    assert ks_default == ks_none, (
        "selected_sort=None must produce same order as omitting the kwarg"
    )
