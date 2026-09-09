"""Phase-1 Activity-Pulse redesign — data-layer + template guards.

Covers the UI-only redesign of the MACE Activity Pulse (mace_view._pulse_view +
mace_live.html): scheduler-actor inclusion, day/eval grouping, date-range default
vs show-all, the mace_rung close backbone (per-trade P&L), housekeeping collapse,
noise filtering, and signal-priority. No engine path is exercised — pure reads of
audit_event + mace_rung. Self-contained (uses tmp_path directly; no shared DB).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jinja2

from trading_corp.persistence import db
from trading_corp.utils.time import now_et
from trading_corp.web import mace_view


# ── seeding helpers ──────────────────────────────────────────────────────
def _et_noon_iso(days_back: int, minute: int = 0) -> str:
    """UTC ISO for ET-noon (+`minute`) `days_back` days ago — a stable in-day
    anchor that never straddles the ET-date boundary."""
    et = now_et()
    d = (et - timedelta(days=days_back)).date()
    dt = datetime(d.year, d.month, d.day, 12, minute, tzinfo=et.tzinfo)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _audit(conn, ts, actor, kind, payload):
    conn.execute(
        "INSERT INTO audit_event (ts, actor, kind, payload_json) VALUES (?,?,?,?)",
        (ts, actor, kind, json.dumps(payload)))


_LEGS = json.dumps([
    {"type": "put", "strike": 734.0, "side": "sell"},
    {"type": "put", "strike": 731.0, "side": "buy"},
    {"type": "call", "strike": 796.0, "side": "sell"},
    {"type": "call", "strike": 799.0, "side": "buy"},
])


def _rung(conn, rung_id, *, status="closed", exit_ts=None, realized=None,
          exit_reason=None, symbol="SPY", expiry="2026-10-23"):
    conn.execute(
        "INSERT INTO mace_rung (rung_id, symbol, status, expiry, legs_json, "
        "width_dollars, contracts, exit_ts, exit_reason, realized_pnl) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (rung_id, symbol, status, expiry, _LEGS, 3.0, 1, exit_ts, exit_reason, realized))


def _seed(db_url: str) -> None:
    db.init_db(db_url)
    with db.connect(db_url) as conn:
        # ── TODAY (ET) ──
        _audit(conn, _et_noon_iso(0, 0), "scheduler", "mace_entry_round",
               {"date": "today", "entered": 1, "placed": 1, "auto_execute": True})
        _audit(conn, _et_noon_iso(0, 1), "robinhood_mace", "mace_entry_eval",
               {"symbol": "FXI", "entered": False, "skip_reason": "ivr",
                "reason": "SKIP ivr 22.35 < 25", "ivr_value": 22.35})
        _audit(conn, _et_noon_iso(0, 2), "robinhood_mace", "mace_entry_eval",
               {"symbol": "SPY", "entered": True, "skip_reason": None,
                "reason": "TAKEN 734-731-796-799 x1 credit 0.95", "ivr_value": 30.6})
        _audit(conn, _et_noon_iso(0, 2), "robinhood_mace", "mace_entry_start",
               {"rung_id": "R1", "symbol": "SPY", "strikes": "734-731-796-799"})
        _audit(conn, _et_noon_iso(0, 3), "robinhood_mace", "mace_entry_fill",
               {"rung_id": "R1", "credit": 0.92, "order_id": "oid1", "attempts": 1})
        _audit(conn, _et_noon_iso(0, 4), "robinhood_mace", "mace_exit_fill",
               {"rung_id": "R2", "reason": "pt", "exit_debit": 0.54, "realized": 40.0,
                "symbol": "SPY",
                "line": "734/731P 796/799C exp 2026-10-02 profit-target debit 0.54 pnl +40.00 (43% of credit)"})
        _audit(conn, _et_noon_iso(0, 5), "robinhood_mace", "mace_entry_error",
               {"rung_id": "R3", "attempt": 2, "error": "combo reject on SPY"})
        # duplicate daily_summary (engine + scheduler) -> collapses to one, count 2
        _audit(conn, _et_noon_iso(0, 6), "robinhood_mace", "mace_daily_summary",
               {"session_date": "today", "equity": 1463.0, "open": 13, "day_pnl": 0})
        _audit(conn, _et_noon_iso(0, 6), "scheduler", "mace_daily_summary",
               {"date": "today"})
        # 3 reconcile ticks -> collapse to one, count 3
        for m in (7, 8, 9):
            _audit(conn, _et_noon_iso(0, m), "robinhood_mace",
                   "mace_reconcile_open_orders_error", {"error": "'NoneType' not subscriptable"})
        # pure noise -> filtered out entirely
        _audit(conn, _et_noon_iso(0, 10), "robinhood_mace", "mace_mark_unavailable",
               {"symbol": "SPY", "error": "not logged in"})
        # NON-mace scheduler events (shared platform scheduler) -> MUST be excluded
        _audit(conn, _et_noon_iso(0, 11), "scheduler", "pmcc_judgment_slot_done",
               {"division": "pmcc"})
        _audit(conn, _et_noon_iso(0, 12), "scheduler", "pead_scan_done", {"division": "pead"})

        # ── 3 DAYS AGO (inside the 7d window) ──
        _audit(conn, _et_noon_iso(3), "robinhood_mace", "mace_entry_eval",
               {"symbol": "SPY", "entered": False, "skip_reason": "ivr",
                "reason": "SKIP ivr 24.60 < 25", "ivr_value": 24.6})

        # ── 10 DAYS AGO (outside 7d; only under show_all) ──
        _audit(conn, _et_noon_iso(10), "robinhood_mace", "mace_entry_eval",
               {"symbol": "SPY", "entered": False, "skip_reason": "ivr",
                "reason": "SKIP ivr 20.00 < 25", "ivr_value": 20.0})

        # ── closed rungs: R2 has an exit_fill (managed) -> no backbone dup;
        #    R4 has NO exit_fill (expiry/manual) -> backbone close row w/ P&L ──
        _rung(conn, "R2", exit_ts=_et_noon_iso(0, 4), realized=40.0, exit_reason="pt")
        _rung(conn, "R4", exit_ts=_et_noon_iso(0, 5), realized=-12.5, exit_reason="time")


# ── data-layer tests ─────────────────────────────────────────────────────
def _view(tmp_path, show_all=False, name="pulse"):
    db_url = f"sqlite:///{(tmp_path / (name + '.db')).as_posix()}"
    _seed(db_url)
    return mace_view._pulse_view(db_url, 7, show_all)


def test_scheduler_round_becomes_eval_header(tmp_path):
    v = _view(tmp_path)
    today = v["days"][0]
    assert today["is_recent"] is True
    assert today["round"] is not None
    assert today["round"]["entered"] == 1 and today["round"]["placed"] == 1
    assert today["round"]["auto_execute"] is True
    # the round is a HEADER, never a row
    assert all(r["kind"] != "mace_entry_round" for d in v["days"] for r in d["rows"])


def test_days_grouped_newest_first_and_dated(tmp_path):
    v = _view(tmp_path)
    dates = [d["date"] for d in v["days"]]
    assert dates == sorted(dates, reverse=True)           # newest first
    for d in v["days"]:
        assert d["date"] and d["weekday"]                 # every day carries a date
        ts = [r["ts"] for r in d["rows"]]
        assert ts == sorted(ts)                           # chronological within day


def test_date_range_default_excludes_old_showall_includes(tmp_path):
    recent = _view(tmp_path, show_all=False, name="recent")
    old_date = _et_noon_iso(10)[:10]
    assert all(d["date"] != old_date for d in recent["days"])   # 10d ago hidden by default
    full = _view(tmp_path, show_all=True, name="full")
    assert any(_pulse_day_has(full, old_date))                  # present under show-all
    assert full["show_all"] is True and recent["show_all"] is False


def _pulse_day_has(view, date_iso):
    return [d for d in view["days"] if d["date"] == date_iso]


def test_noise_filtered_and_housekeeping_collapsed(tmp_path):
    v = _view(tmp_path)
    all_rows = [r for d in v["days"] for r in d["rows"]]
    assert all(r["kind"] != "mace_mark_unavailable" for r in all_rows)   # noise gone
    recon = [r for r in all_rows if r["kind"] == "mace_reconcile_open_orders_error"]
    assert len(recon) == 1 and recon[0]["count"] == 3 and recon[0]["priority"] == "muted"
    summ = [r for r in all_rows if r["kind"] == "mace_daily_summary"]
    assert len(summ) == 1 and summ[0]["count"] == 2 and summ[0]["priority"] == "muted"


def test_close_backbone_from_mace_rung_no_double(tmp_path):
    v = _view(tmp_path)
    all_rows = [r for d in v["days"] for r in d["rows"]]
    # R4 (no exit_fill) surfaces as a synthesized close row with its per-trade P&L
    closes = [r for r in all_rows if r["kind"] == "mace_close"]
    assert len(closes) == 1 and closes[0]["pnl"] == -12.5 and closes[0]["priority"] == "high"
    assert "-12.50" in closes[0]["text"]
    # R2 (has exit_fill) shows via the exit_fill row only — NOT duplicated as mace_close
    assert all("R2" not in (r.get("text") or "") for r in closes)
    fills = [r for r in all_rows if r["kind"] == "mace_exit_fill"]
    assert len(fills) == 1 and fills[0]["pnl"] == 40.0


def test_priority_signal_tiers(tmp_path):
    v = _view(tmp_path)
    by = {}
    for d in v["days"]:
        for r in d["rows"]:
            by.setdefault(r["kind"], r["priority"])
    assert by["mace_entry_fill"] == "high"
    assert by["mace_exit_fill"] == "high"
    assert by["mace_close"] == "high"
    assert by["mace_entry_error"] == "alert"
    assert by["mace_reconcile_open_orders_error"] == "muted"
    # entered eval is high; skip eval is normal
    takes = [r for d in v["days"] for r in d["rows"]
             if r["kind"] == "mace_entry_eval" and r["symbol"] == "SPY" and "TAKEN" in r["text"]]
    skips = [r for d in v["days"] for r in d["rows"]
             if r["kind"] == "mace_entry_eval" and "SKIP" in r["text"]]
    assert takes and takes[0]["priority"] == "high"
    assert skips and all(s["priority"] == "normal" for s in skips)


def test_day_counts_and_attempt_surfaced(tmp_path):
    v = _view(tmp_path)
    today = v["days"][0]
    assert today["counts"]["fills"] == 1
    assert today["counts"]["closes"] == 2          # exit_fill (R2) + backbone close (R4)
    assert today["counts"]["skips"] == 1           # FXI ivr skip
    assert today["counts"]["alerts"] == 1          # entry_error
    err = [r for r in today["rows"] if r["kind"] == "mace_entry_error"][0]
    assert err["attempt"] == 2                     # ladder attempt surfaced


def test_non_mace_scheduler_events_excluded(tmp_path):
    """The 'scheduler' actor is SHARED (pmcc/pead/scheduled_ slots); only its
    mace_* kinds belong in the MACE pulse."""
    v = _view(tmp_path)
    all_rows = [r for d in v["days"] for r in d["rows"]]
    assert all_rows                                        # sanity: not empty
    assert all(r["kind"].startswith("mace_") for r in all_rows)
    assert all(("pmcc" not in r["kind"]) and ("pead" not in r["kind"]) for r in all_rows)


def test_empty_db_is_honest(tmp_path):
    db_url = f"sqlite:///{(tmp_path / 'empty.db').as_posix()}"
    db.init_db(db_url)
    v = mace_view._pulse_view(db_url, 7, False)
    assert v["days"] == [] and v["total_rows"] == 0


# ── template guards ──────────────────────────────────────────────────────
def _template_src() -> str:
    tpl = Path(mace_view.__file__).parent / "templates" / "mace_live.html"
    return tpl.read_text(encoding="utf-8")


def test_template_parses_and_uses_pulse_dated(tmp_path):
    src = _template_src()
    # Jinja syntax must be valid (extends/filters resolved at runtime, not here)
    jinja2.Environment(autoescape=True).parse(src)
    assert "pulse.days" in src                     # renders the new grouped view
    assert "et_hms" not in src                     # time-only render is gone
    assert "et_short" in src                       # every row is dated
    assert "{% if audits %}" not in src            # old flat feed removed
    assert "<details" in src                       # collapsible day sections
