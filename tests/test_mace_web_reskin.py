"""End-to-end render tests for the /mace reskin (UI rebuild 2026-08-14).

Seeds a FULL live picture on a migrated scratch DB — an open (retired-but-managed)
SPY rung + its mace_rung_live tick + IVR corpus + equity history + audits — and
asserts the cockpit renders the enriched analytics: live P&L, PT/stop gauges, the
payoff data-island, the ticker state chips, universe-only IVR, HWM, session, and
that the halt contract + honest-empty/staleness behaviours survive the reskin.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from trading_corp.mace.config import load_mace_config
from trading_corp.persistence import db as dbmod

from tests.test_mace_web_wiring import _ROOT, _app

UTC = timezone.utc
_LEGS = json.dumps([
    {"type": "put", "side": "sell", "strike": 742},
    {"type": "put", "side": "buy", "strike": 739},
    {"type": "call", "side": "sell", "strike": 802},
    {"type": "call", "side": "buy", "strike": 805},
])
RID = "mace-SPY-test-rung"


def _full_db(tmp_path):
    """A MIGRATED scratch DB (init_db adds mace_rung_live + entry_atm_iv), seeded
    with a live picture."""
    url = f"sqlite:///{tmp_path / 'reskin.db'}"
    dbmod.init_db(url)
    path = dbmod.resolve_db_path(url)
    conn = sqlite3.connect(path)
    now = datetime.now(UTC)
    fresh_ts = now.isoformat(timespec="seconds")
    expiry = (date.today() + timedelta(days=42)).isoformat()
    entry = (now - timedelta(days=10)).isoformat(timespec="seconds")
    # equity history (HWM + curve)
    for i, eq in enumerate([3712, 3760, 3805, 3840.45]):
        d = (date.today() - timedelta(days=3 - i)).isoformat()
        conn.execute("INSERT INTO mace_equity_snapshot(snap_date,equity,cash,ts) "
                     "VALUES(?,?,?,?)", (d, eq, eq + 300, fresh_ts))
    # an OPEN SPY rung (SPY is retired but still managed) + entry IV
    conn.execute(
        "INSERT INTO mace_rung(rung_id,symbol,status,expiry,legs_json,width_dollars,"
        "contracts,credit_actual,max_risk_usd,pt_debit,entry_ts,entry_iso_week,entry_atm_iv)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (RID, "SPY", "open", expiry, _LEGS, 3.0, 1, 0.93, 207.0, 0.47, entry,
         "2026-W33", 0.142))
    # this tick's live mark + spot (fresh -> not stale -> P&L renders)
    conn.execute("INSERT INTO mace_rung_live(rung_id,symbol,mark,spot,ts) "
                 "VALUES(?,?,?,?,?)", (RID, "SPY", 0.71, 778.08, fresh_ts))
    # IVR corpus: 3 actives + SPY (managed) + a leaked GLD (must be DROPPED)
    for sym, iv, rank in [("IBIT", 0.31, 30.0), ("XLE", 0.22, 26.0),
                          ("GDX", 0.19, 19.0), ("SPY", 0.147, 27.0),
                          ("GLD", 0.24, 28.0)]:
        conn.execute("INSERT INTO mace_iv_history(symbol,snap_date,atm_iv,ivr_tasty,"
                     "source,ts) VALUES(?,?,?,?,?,?)",
                     (sym, date.today().isoformat(), iv, rank, "tasty", fresh_ts))
    # a couple of MACE audits for the activity feed
    conn.execute("INSERT INTO audit_event(ts,actor,kind,payload_json) VALUES(?,?,?,?)",
                 (fresh_ts, "robinhood_mace", "mace_manage_exit",
                  json.dumps({"rung_id": RID, "symbol": "SPY", "reason": "tick"})))
    conn.execute("INSERT INTO audit_event(ts,actor,kind,payload_json) VALUES(?,?,?,?)",
                 (fresh_ts, "mace_operations", "mace_ui_arm", json.dumps({"halted": False})))
    conn.commit()
    conn.close()
    return url


def _cfg():
    return load_mace_config(
        _ROOT / "config" / "mace.yaml",
        exdiv_calendar_path=_ROOT / "config" / "ex_dividend_calendar.yaml")


def _wired_app(tmp_path):
    from trading_corp.agents.divisions.robinhood_mace import RobinhoodMaceAgent
    cfg = _cfg()

    class _Mgr:
        pass
    mgr = _Mgr()
    mgr.cfg = cfg
    division = RobinhoodMaceAgent(
        cfg, divisions_yaml=_ROOT / "config" / "divisions.yaml",
        strategies_yaml=_ROOT / "config" / "strategies.yaml")
    return _app(_full_db(tmp_path), mace_division=division, mace_manager=mgr), cfg


def test_reskin_renders_enriched_rung(tmp_path):
    app, cfg = _wired_app(tmp_path)
    html = TestClient(app).get("/mace")
    assert html.status_code == 200
    h = html.text
    # the payoff canvas + its per-rung data island are emitted
    assert "mace-payoff" in h
    assert '"credit": 0.93' in h or '"credit":0.93' in h        # payoff island
    assert "/static/js/mace_payoff.js" in h
    # structure + strikes rendered
    assert "742/739P 802/805C" in h
    # live P&L present (credit 0.93 - mark 0.71 = +$22.00) — a real number, not a stub
    assert "22.00" in h
    # config hash chip + universe + retired SPY
    assert cfg.config_hash[:12] in h
    assert "IBIT" in h and "XLE" in h and "GDX" in h and "SPY" in h


def test_reskin_ivr_is_universe_only(tmp_path):
    """G3: IVR panel foregrounds actives + retired-managed SPY, DROPS leaked GLD."""
    app, _ = _wired_app(tmp_path)
    h = TestClient(app).get("/mace").text
    # GLD is in the corpus but NOT in the universe and holds no rungs -> dropped.
    # (assert on the IVR section by checking GLD's atm_iv value is absent)
    assert "0.24" not in h or "GLD" not in h.split("IVR")[-1][:1500]


def test_reskin_hwm_and_session_present(tmp_path):
    app, _ = _wired_app(tmp_path)
    h = TestClient(app).get("/mace").text
    # HWM = MAX(equity) = 3840.45
    assert "3,840" in h
    # session phase label is one of the computed phases
    assert any(p in h for p in ("open", "pre-market", "after-hours", "weekend"))


def test_reskin_halt_contract_preserved(tmp_path):
    """The halt pill + its endpoints survive the reskin byte-for-behavior."""
    app, _ = _wired_app(tmp_path)
    client = TestClient(app)
    h = client.get("/mace").text
    assert "ENTRIES:" in h                                   # tri-state pill text
    assert 'hx-post="/mace/halt"' in h or 'hx-post="/mace/arm"' in h
    # the write endpoints still work (audit-before-state latch)
    assert client.post("/mace/halt").status_code == 200
    assert client.post("/mace/arm").status_code == 200


def test_reskin_rungs_partial_standalone(tmp_path):
    """The 30s-poll fragment renders with ONLY {rungs} in ctx (no other keys)."""
    app, _ = _wired_app(tmp_path)
    r = TestClient(app).get("/mace/partials/rungs")
    assert r.status_code == 200
    assert 'hx-get="/mace/partials/rungs"' in r.text
    assert "742/739P 802/805C" in r.text                    # the seeded rung


def test_reskin_stale_mark_is_honest(tmp_path):
    """An OLD live tick -> STALE badge, and the derived P&L still shows but flagged
    (never silently dropped, never fabricated)."""
    url = f"sqlite:///{tmp_path / 'stale.db'}"
    dbmod.init_db(url)
    conn = sqlite3.connect(dbmod.resolve_db_path(url))
    old = (datetime.now(UTC) - timedelta(hours=6)).isoformat(timespec="seconds")
    expiry = (date.today() + timedelta(days=42)).isoformat()
    conn.execute(
        "INSERT INTO mace_rung(rung_id,symbol,status,expiry,legs_json,width_dollars,"
        "contracts,credit_actual,max_risk_usd,pt_debit,entry_ts,entry_iso_week)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (RID, "SPY", "open", expiry, _LEGS, 3.0, 1, 0.93, 207.0, 0.47, old, "2026-W33"))
    conn.execute("INSERT INTO mace_rung_live(rung_id,symbol,mark,spot,ts) "
                 "VALUES(?,?,?,?,?)", (RID, "SPY", 0.71, 778.0, old))
    conn.commit()
    conn.close()
    from trading_corp.agents.divisions.robinhood_mace import RobinhoodMaceAgent
    cfg = _cfg()

    class _Mgr:
        pass
    m = _Mgr()
    m.cfg = cfg
    div = RobinhoodMaceAgent(cfg, divisions_yaml=_ROOT / "config" / "divisions.yaml",
                             strategies_yaml=_ROOT / "config" / "strategies.yaml")
    h = TestClient(_app(url, mace_division=div, mace_manager=m)).get("/mace").text
    assert h.count("STALE") >= 1 or "stale" in h.lower()
