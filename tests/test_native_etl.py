"""Tests for the native-BitUnix ETL (bar ingest + alert export).

Covers: ON CONFLICT(ts) upsert, file-level idempotence, venue tagging, the
corpus safety rail, the "native ingest never touches other tables" invariant,
and the alert-JSON shape the redeem-cap engine consumes.
All tests are stdlib-only and use temp DBs -- the real 28 MB corpus is never touched.
"""
import csv
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import ingest_bitunix_bars as ib  # noqa: E402
import export_bitunix_alerts as ea  # noqa: E402


def _write_csv(path, header, rows):
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _bars_csv(path, rows):
    _write_csv(path, ["ts", "open", "high", "low", "close", "volume"], rows)


def test_bar_ingest_creates_venue_tagged_table(tmp_path):
    db = tmp_path / "smoke.db"
    csvp = tmp_path / "bars.csv"
    _bars_csv(csvp, [
        (1778823000, 100.0, 101.0, 99.0, 100.5, 1000.0),
        (1778823180, 100.5, 102.0, 100.0, 101.5, 1200.0),
    ])
    assert ib.main([str(csvp), "--db", str(db), "--table", "bars_3m_bitunix"]) == 0
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT ts, datetime_utc, close, venue FROM bars_3m_bitunix ORDER BY ts"
    ).fetchall()
    con.close()
    assert len(rows) == 2
    assert rows[0][3] == "bitunix"                  # venue tag present
    assert rows[0][1].startswith("2026-05-15")      # datetime_utc derived from epoch-s ts


def test_bar_ingest_onconflict_upsert(tmp_path):
    db = tmp_path / "smoke.db"
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    _bars_csv(a, [(1778823000, 100, 101, 99, 100.5, 1000),
                  (1778823180, 100.5, 102, 100, 101.5, 1200)])
    _bars_csv(b, [(1778823180, 100.5, 102, 100, 999.0, 1200),   # same ts, changed close
                  (1778823360, 101.5, 103, 101, 102.5, 1300)])  # new ts
    assert ib.main([str(a), "--db", str(db), "--table", "bars_3m_bitunix"]) == 0
    assert ib.main([str(b), "--db", str(db), "--table", "bars_3m_bitunix"]) == 0
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM bars_3m_bitunix").fetchone()[0]
    close = con.execute("SELECT close FROM bars_3m_bitunix WHERE ts=1778823180").fetchone()[0]
    con.close()
    assert n == 3            # union of ts (2 existing + 1 new)
    assert close == 999.0    # ON CONFLICT(ts) updated the repainted row


def test_bar_ingest_file_idempotent(tmp_path):
    db = tmp_path / "smoke.db"
    csvp = tmp_path / "bars.csv"
    _bars_csv(csvp, [(1778823000, 100, 101, 99, 100.5, 1000)])
    assert ib.main([str(csvp), "--db", str(db), "--table", "bars_3m_bitunix"]) == 0
    assert ib.main([str(csvp), "--db", str(db), "--table", "bars_3m_bitunix"]) == 0  # dup -> skip
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM bars_3m_bitunix").fetchone()[0]
    con.close()
    assert n == 1            # second (identical) run was a no-op


def test_corpus_safety_rail_blocks_reserved_table(tmp_path):
    db = tmp_path / "btc_scalping.db"   # the canonical corpus filename
    csvp = tmp_path / "bars.csv"
    _bars_csv(csvp, [(1778823000, 100, 101, 99, 100.5, 1000)])
    assert ib.main([str(csvp), "--db", str(db), "--table", "bars_3m"]) == 2   # refused
    assert not db.exists()              # refused before the DB was even created


def test_native_ingest_leaves_other_tables_untouched(tmp_path):
    # Synthetic 'corpus' with a frozen bars_3m; native ingest into bars_3m_bitunix
    # must leave bars_3m's count AND content identical.
    db = tmp_path / "corpus.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE bars_3m (ts INTEGER PRIMARY KEY, close REAL)")
    con.executemany("INSERT INTO bars_3m VALUES (?,?)", [(1, 10.0), (2, 20.0), (3, 30.0)])
    con.commit()
    before_n = con.execute("SELECT COUNT(*) FROM bars_3m").fetchone()[0]
    before_h = hashlib.sha256(
        repr(con.execute("SELECT * FROM bars_3m ORDER BY ts").fetchall()).encode()).hexdigest()
    con.close()

    csvp = tmp_path / "bars.csv"
    _bars_csv(csvp, [(1778823000, 100, 101, 99, 100.5, 1000)])
    assert ib.main([str(csvp), "--db", str(db), "--table", "bars_3m_bitunix"]) == 0

    con = sqlite3.connect(db)
    after_n = con.execute("SELECT COUNT(*) FROM bars_3m").fetchone()[0]
    after_h = hashlib.sha256(
        repr(con.execute("SELECT * FROM bars_3m ORDER BY ts").fetchall()).encode()).hexdigest()
    has_native = con.execute("SELECT COUNT(*) FROM bars_3m_bitunix").fetchone()[0]
    con.close()
    assert after_n == before_n and after_h == before_h   # Bybit corpus table frozen
    assert has_native == 1


def test_alert_export_matches_engine_shape(tmp_path):
    csvp = tmp_path / "ledger.csv"
    _write_csv(csvp, ["ts", "signal", "source", "tf"], [
        ("2026-06-18T23:12:00Z", "mc_a_redx", "market_cypher", "3m"),
        ("2026-05-15T05:30:00+00:00", "otter_buy", "lord_otter", "3m"),
    ])
    out = tmp_path / "alerts.json"
    assert ea.main([str(csvp), "--out", str(out)]) == 0
    data = json.loads(out.read_text())
    assert isinstance(data, list) and len(data) == 2
    assert data[0]["ts"] <= data[1]["ts"]                 # sorted ascending
    for a in data:
        assert {"ts", "signal", "tf"}.issubset(a)
        dt = datetime.fromisoformat(a["ts"])              # the exact call the engine makes
        assert dt.tzinfo is not None                      # tz-aware or the engine TypeErrors
        assert a["ts"].endswith("+00:00")                 # 'Z' normalized
    assert {a["signal"] for a in data} == {"mc_a_redx", "otter_buy"}  # names verbatim
