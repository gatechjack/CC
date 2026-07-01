"""Tests for the regime-aware bidirectional SFP build (2026-07-01): engine-native
regime component, short detection geometry (M2=0), the regime side-gate, the config.side
HOT kill-switch, the isolated research-log, and the flip-watch. Fast + self-contained
(no 230d data — the full 230d offline parity lives in deploy_bidirectional_sfp/*)."""
from __future__ import annotations

import asyncio
import hashlib
import sqlite3

from trading_corp.persistence import db
from tests.test_bitunix_sfp_observer import _mk, _sig, _bar
from trading_corp.agents.divisions.bitunix_sfp_observer import (
    BitunixSfpObserver, DIVISION, compute_regime_label, _regime_ema200,
    geometry_short, reflect_neg, REGIME_MIN_BARS, REGIME_SEED_MIN,
)
import trading_corp.agents.divisions.bitunix_sfp_research_log as rl
from trading_corp.agents.strategies.bitunix_sfp import SfpBar

RISING = [100.0 + i * 0.1 for i in range(801)]     # -> regime 'up'
FALLING = [180.0 - i * 0.1 for i in range(801)]    # -> regime 'down'


# ── regime component ──────────────────────────────────────────────────────────
def _ref_label(closes):
    """Inline reference = research regime_filter 'ema200_pos_slope' (first-close-seed)."""
    if len(closes) < REGIME_MIN_BARS:
        return None
    a = 2.0 / 201.0; e = None; em = []
    for c in closes:
        e = c if e is None else a * c + (1 - a) * e
        em.append(e)
    rising = em[-1] > em[-33]
    if closes[-1] > em[-1] and rising:
        return "up"
    if closes[-1] < em[-1] and not rising:
        return "down"
    return "range"


def test_regime_label_parity_locked_to_research():
    for c in (RISING, FALLING, [100.0] * 801,
              [100.0 + (i % 40) * 0.3 for i in range(801)],
              [100.0 + i ** 0.5 for i in range(801)]):
        assert compute_regime_label(c) == _ref_label(c)


def test_regime_up_and_down():
    assert compute_regime_label(RISING) == "up"
    assert compute_regime_label(FALLING) == "down"


def test_regime_formula_warmup_none_below_min_bars():
    assert compute_regime_label([100.0] * (REGIME_MIN_BARS - 1)) is None
    assert compute_regime_label(RISING[:REGIME_MIN_BARS]) is not None


def test_compute_regime_seed_min_gate(tmp_path):
    obs, *_ = _mk(tmp_path)
    obs._regime_closes["BTCUSDT"] = RISING[:REGIME_SEED_MIN - 1]      # < 800
    assert obs._compute_regime("BTCUSDT") is None
    obs._regime_closes["BTCUSDT"] = RISING[:REGIME_SEED_MIN]          # == 800
    assert obs._compute_regime("BTCUSDT") == "up"


def test_seed_from_history_path(tmp_path):
    obs, de, risk, logger, db_url = _mk(tmp_path)
    with db.connect(db_url) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS bitunix_bar_history "
                     "(symbol TEXT, ts_ms INTEGER, timeframe TEXT, close REAL)")
        for i in range(810):
            conn.execute("INSERT INTO bitunix_bar_history (symbol, ts_ms, timeframe, close) "
                         "VALUES (?,?,?,?)", ("BTCUSDT", i * 900000, "15m", 100.0 + i * 0.1))
    obs._regime_closes.clear(); obs._regime_last_ts.clear()
    obs._seed_regime_from_history()
    assert len(obs._regime_closes.get("BTCUSDT", [])) >= REGIME_SEED_MIN
    assert obs._compute_regime("BTCUSDT") == "up"


# ── short detection / geometry ─────────────────────────────────────────────────
def test_reflect_neg_m2_zero():
    r = reflect_neg([SfpBar(1000, 10.0, 12.0, 9.0, 11.0)])[0]
    assert (r.ts_ms, r.open, r.high, r.low, r.close) == (1000, -10.0, -9.0, -12.0, -11.0)
    assert -r.low == 12.0        # reflected low = -real high -> un-reflect: real = -reflected


def test_geometry_short_sign_correct():
    stop, tp, r = geometry_short(100.0, 102.0, stop_buffer_pct=0.001, tp_r=2.0)
    assert stop == 102.0 + 0.1 and r == stop - 100.0 and tp == 100.0 - 2.0 * r
    assert stop > 100.0 > tp and r > 0


def test_geometry_short_degenerate_none():
    assert geometry_short(100.0, 99.0, stop_buffer_pct=0.001, tp_r=2.0) is None  # swept_high<entry


# ── side-gate ──────────────────────────────────────────────────────────────────
def test_regime_warmup_skips(tmp_path):
    obs, de, risk, logger, _ = _mk(tmp_path)
    obs._regime_closes["BTCUSDT"] = RISING[:100]                      # < 800 -> None
    asyncio.run(obs._handle_signal("BTC/USDT.P", "BTCUSDT", _sig(), _bar(100.0), side="long"))
    assert de.placed == [] and "sfp_skip_regime_warmup" in logger.kinds()


def test_side_gate_long_in_down_skipped(tmp_path):
    obs, de, risk, logger, _ = _mk(tmp_path)
    obs._regime_closes["BTCUSDT"] = FALLING                          # down
    asyncio.run(obs._handle_signal("BTC/USDT.P", "BTCUSDT", _sig(), _bar(100.0), side="long"))
    assert de.placed == [] and "sfp_skip_counter_trend" in logger.kinds()


def test_side_gate_short_in_up_skipped(tmp_path):
    obs, de, risk, logger, _ = _mk(tmp_path)                          # _mk seeds RISING -> up
    asyncio.run(obs._handle_signal("BTC/USDT.P", "BTCUSDT",
                                   _sig(swept_low=-200.0), _bar(100.0), side="short"))
    assert de.placed == [] and "sfp_skip_counter_trend" in logger.kinds()


def test_side_gate_short_in_down_allowed(tmp_path):
    obs, de, risk, logger, _ = _mk(tmp_path)
    obs._regime_closes["BTCUSDT"] = FALLING                          # down -> short allowed
    asyncio.run(obs._handle_signal("BTC/USDT.P", "BTCUSDT",
                                   _sig(swept_low=-200.0), _bar(100.0), side="short"))
    assert len(de.placed) == 1 and de.placed[0][0].side == "sell"


# ── config.side HOT kill-switch ─────────────────────────────────────────────────
def test_killswitch_long_suppresses_short(tmp_path):
    obs, de, risk, logger, _ = _mk(tmp_path)
    obs._regime_closes["BTCUSDT"] = FALLING                          # regime would allow short
    obs._yaml_side = lambda: "long"                                  # kill-switch: long-only
    asyncio.run(obs._handle_signal("BTC/USDT.P", "BTCUSDT",
                                   _sig(swept_low=-200.0), _bar(100.0), side="short"))
    assert de.placed == [] and "sfp_skip_side_disabled" in logger.kinds()


def test_killswitch_regime_allows_short(tmp_path):
    obs, de, risk, logger, _ = _mk(tmp_path)
    obs._regime_closes["BTCUSDT"] = FALLING
    obs._yaml_side = lambda: "regime"
    asyncio.run(obs._handle_signal("BTC/USDT.P", "BTCUSDT",
                                   _sig(swept_low=-200.0), _bar(100.0), side="short"))
    assert "sfp_skip_side_disabled" not in logger.kinds() and len(de.placed) == 1


def test_yaml_side_reads_and_failsafe(tmp_path):
    obs, *_ = _mk(tmp_path)
    y = tmp_path / "s.yaml"
    y.write_text(f"{DIVISION}:\n  side: long\n"); obs._strategies_yaml_path = str(y)
    assert BitunixSfpObserver._yaml_side(obs) == "long"
    y.write_text(f"{DIVISION}:\n  side: regime\n")
    assert BitunixSfpObserver._yaml_side(obs) == "regime"
    y.write_text(f"{DIVISION}:\n  side: bogus\n")               # unknown -> fail-safe long
    assert BitunixSfpObserver._yaml_side(obs) == "long"
    obs._strategies_yaml_path = str(tmp_path / "nope.yaml")     # missing -> fail-safe long
    assert BitunixSfpObserver._yaml_side(obs) == "long"


# ── research-log (round-trip + isolation) ───────────────────────────────────────
def _pt_hash(path):
    con = sqlite3.connect(path)
    rows = con.execute("SELECT * FROM paper_trade_record ORDER BY rowid").fetchall()
    con.close()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def test_research_log_roundtrip_and_isolation(tmp_path):
    p = tmp_path / "r.db"; url = "sqlite:///" + p.as_posix()
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE paper_trade_record (order_id TEXT, result TEXT)")
    con.execute("INSERT INTO paper_trade_record VALUES ('LIVE-1', NULL)")
    con.commit(); con.close()
    before = _pt_hash(str(p))

    rl.ensure_schema(url)
    rl.log_entry(url, {"order_id": "o1", "division": DIVISION, "coin": "BTC/USDT.P",
                       "side": "short", "regime_label": "down", "rr_target": 2.0,
                       "entry_ts": "2026-07-01 14:00:00", "entry_px": 60000.0,
                       "stop_px": 60600.0, "target_px": 58800.0})
    rl.log_exit(url, "o1", exit_ts="2026-07-01 15:30:00", exit_px=58800.0,
                realized_r=2.0, closing_leg="tp")

    con = sqlite3.connect(str(p)); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM bitunix_sfp_research_log WHERE order_id='o1'").fetchone()
    con.close()
    assert row["side"] == "short" and row["regime_label"] == "down"
    assert row["realized_r"] == 2.0 and row["closing_leg"] == "tp" and row["duration_sec"] == 5400
    assert _pt_hash(str(p)) == before          # paper_trade_record byte-identical (isolation)


# ── flip-watch ──────────────────────────────────────────────────────────────────
def test_flip_watch_label_to_label_only(tmp_path):
    p = tmp_path / "f.db"; url = "sqlite:///" + p.as_posix()
    rl.ensure_flip_schema(url)
    seq = [None, None, "down", "down", "range", "up", "up", "down", None, "up"]
    last = None
    for i, new in enumerate(seq):
        old, last = last, new
        if rl.is_regime_flip(old, new):
            rl.log_flip(url, ts=f"t{i}", coin="BTCUSDT", old_regime=old,
                        new_regime=new, ema200=100.0, slope=0.0)
    con = sqlite3.connect(str(p))
    rows = con.execute("SELECT old_regime, new_regime FROM bitunix_sfp_regime_flip "
                       "ORDER BY id").fetchall()
    nulls = con.execute("SELECT COUNT(*) FROM bitunix_sfp_regime_flip "
                        "WHERE old_regime IS NULL OR new_regime IS NULL").fetchone()[0]
    con.close()
    assert [tuple(r) for r in rows] == [("down", "range"), ("range", "up"), ("up", "down")]
    assert nulls == 0


def test_regime_state_mirror_always_current(tmp_path):
    p = tmp_path / "s.db"; url = "sqlite:///" + p.as_posix()
    rl.ensure_flip_schema(url)
    rl.upsert_regime_state(url, coin="BTCUSDT", regime="down", ema200=100.0, slope=-0.001, ts="t0")
    rl.upsert_regime_state(url, coin="BTCUSDT", regime="up", ema200=101.0, slope=0.002, ts="t1")
    con = sqlite3.connect(str(p))
    r = con.execute("SELECT regime, updated_ts FROM bitunix_sfp_regime_state "
                    "WHERE coin='BTCUSDT'").fetchone()
    n = con.execute("SELECT COUNT(*) FROM bitunix_sfp_regime_state").fetchone()[0]
    con.close()
    assert r == ("up", "t1") and n == 1        # always-current, single row/coin
