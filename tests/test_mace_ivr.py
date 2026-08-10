"""Phase-1 tests: MACE IVR provider — MOCK-based per trap 4 (never the SDK).

Encodes the load-bearing traps: x100 normalization, tw never used / tos
fallback, ivr_stale vs ivr_unavailable, scale-anomaly guard, ATM-IV recorded
even when the rank is unusable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from trading_corp.mace import ivr_provider as ivr
from trading_corp.mace.domain import IVR_OK, IVR_STALE, IVR_UNAVAILABLE
from trading_corp.persistence import db

NOW = datetime(2026, 8, 10, 20, 0, tzinfo=timezone.utc)   # Mon 2026-08-10


def _m(sym, rank=None, tos=None, atm=None, updated=None, tw=None):
    return SimpleNamespace(
        symbol=sym,
        implied_volatility_index_rank=rank,
        tos_implied_volatility_index_rank=tos,
        tw_implied_volatility_index_rank=tw,
        implied_volatility_index=atm,
        updated_at=updated,
    )


@pytest.fixture
def conn(tmp_path):
    url = f"sqlite:///{(tmp_path / 'mace.db').as_posix()}"
    with db.connect(url) as c:
        c.executescript(db.SCHEMA)
        yield c


# ── classify (pure) ──────────────────────────────────────────────────────

def test_normalize_x100():
    status, ivrv, age = ivr.classify("0.272132797", NOW, NOW)
    assert status == IVR_OK and abs(ivrv - 27.21) < 0.01


def test_stale_gt_2_sessions():
    updated = datetime(2026, 8, 5, 17, 0, tzinfo=timezone.utc)   # Wed -> Thu,Fri,Mon = 3
    status, ivrv, age = ivr.classify("0.258", updated, NOW)
    assert status == IVR_STALE and age == 3 and ivrv is not None


def test_fresh_within_2_sessions():
    updated = datetime(2026, 8, 6, 17, 0, tzinfo=timezone.utc)   # Thu -> Fri,Mon = 2
    status, ivrv, age = ivr.classify("0.258", updated, NOW)
    assert status == IVR_OK and age == 2


def test_scale_anomaly_unavailable():
    status, ivrv, age = ivr.classify("30.3", NOW, NOW)           # ->3030 out of range
    assert status == IVR_UNAVAILABLE and ivrv is None


def test_rank_none_unavailable():
    assert ivr.classify(None, NOW, NOW)[0] == IVR_UNAVAILABLE


def test_rank_max_1_is_ivr_100():
    status, ivrv, _ = ivr.classify("1.0", NOW, NOW)
    assert status == IVR_OK and ivrv == 100.0


# ── read_metrics (fetch orchestration) ───────────────────────────────────

def test_never_uses_tw():
    r = ivr.read_metrics(lambda s: [_m("USO", rank="0.295", tw="1.0", updated=NOW)],
                         ["USO"], now=NOW)
    assert abs(r["USO"].ivr - 29.5) < 0.01          # 0.295*100, NOT tw 1.0 -> 100


def test_tos_fallback():
    r = ivr.read_metrics(lambda s: [_m("GLD", rank=None, tos=Decimal("0.330"), updated=NOW)],
                         ["GLD"], now=NOW)
    assert r["GLD"].status == IVR_OK and abs(r["GLD"].ivr - 33.0) < 0.01


def test_missing_symbol_unavailable():
    r = ivr.read_metrics(lambda s: [], ["SPY"], now=NOW)
    assert r["SPY"].status == IVR_UNAVAILABLE and "missing" in r["SPY"].detail


def test_fetch_exception_marks_whole_batch():
    def boom(_s):
        raise RuntimeError("tasty down")
    r = ivr.read_metrics(boom, ["SPY", "USO"], now=NOW)
    assert set(r) == {"SPY", "USO"}
    assert all(v.status == IVR_UNAVAILABLE for v in r.values())


def test_dict_metrics_supported():
    r = ivr.read_metrics(
        lambda s: [{"symbol": "SPY", "implied_volatility_index_rank": "0.272",
                    "updated_at": NOW}],
        ["SPY"], now=NOW)
    assert r["SPY"].status == IVR_OK


def test_stale_detail_carries_symbol_and_age():
    updated = datetime(2026, 8, 5, 17, 0, tzinfo=timezone.utc)
    r = ivr.read_metrics(lambda s: [_m("FXI", rank="0.258", updated=updated)],
                         ["FXI"], now=NOW)
    assert r["FXI"].status == IVR_STALE
    assert "FXI" in r["FXI"].detail and "3 sessions" in r["FXI"].detail


# ── coercion + business-day helpers ──────────────────────────────────────

def test_to_float_edges():
    assert ivr._to_float(None) is None
    assert ivr._to_float(True) is None          # bool guard
    assert ivr._to_float(Decimal("0.5")) == 0.5
    assert ivr._to_float("  0.3 ") == 0.3
    assert ivr._to_float("") is None
    assert ivr._to_float("abc") is None


def test_session_age_business_days():
    # Intraday timestamps (as Tasty returns) -> anchored on the ET trading date.
    fri = datetime(2026, 8, 7, 17, 0, tzinfo=timezone.utc)      # Fri 13:00 ET
    assert ivr._session_age(fri, NOW) == 1                      # Fri -> Mon = 1
    assert ivr._session_age(NOW, NOW) == 0                      # same day
    future = datetime(2026, 8, 11, 17, 0, tzinfo=timezone.utc)  # ahead of NOW
    assert ivr._session_age(future, NOW) == 0


# ── snapshot writer ──────────────────────────────────────────────────────

def test_snapshot_writes_including_atm_only(conn):
    def fetch(_s):
        return [
            _m("SPY", rank="0.272", atm="0.15", updated=NOW),
            _m("EWZ", rank="30.3", atm="0.40", updated=NOW),   # rank anomaly, atm ok
            # TLT missing entirely -> no data -> not written
        ]
    r = ivr.read_metrics(fetch, ["SPY", "EWZ", "TLT"], now=NOW)
    n = ivr.snapshot_readings(conn, r, "2026-08-10", ts="2026-08-10T20:00:00+00:00")
    rows = {row["symbol"]: dict(row)
            for row in conn.execute("SELECT * FROM mace_iv_history ORDER BY symbol")}
    assert n == 2 and set(rows) == {"SPY", "EWZ"}
    assert abs(rows["SPY"]["ivr_tasty"] - 27.2) < 0.1
    assert rows["EWZ"]["ivr_tasty"] is None            # anomalous rank -> NULL
    assert abs(rows["EWZ"]["atm_iv"] - 0.40) < 1e-9    # but ATM-IV preserved


def test_snapshot_upsert_same_session(conn):
    r1 = ivr.read_metrics(lambda s: [_m("SPY", rank="0.20", atm="0.1", updated=NOW)],
                          ["SPY"], now=NOW)
    ivr.snapshot_readings(conn, r1, "2026-08-10")
    r2 = ivr.read_metrics(lambda s: [_m("SPY", rank="0.30", atm="0.2", updated=NOW)],
                          ["SPY"], now=NOW)
    ivr.snapshot_readings(conn, r2, "2026-08-10")       # same (symbol, date) -> replace
    rows = list(conn.execute("SELECT ivr_tasty FROM mace_iv_history WHERE symbol='SPY'"))
    assert len(rows) == 1 and abs(rows[0][0] - 30.0) < 0.1
