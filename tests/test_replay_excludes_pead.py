"""paper_trade_replay must NOT touch robinhood_pead rows.

PEAD self-manages exits via its manage() pressure engine and has no tp_price
(no take-profit). The replay's mark_pre_phase_a_rows stamps result='pre_phase_a'
on any result-NULL row with tp_price NULL — which would silently drop every live
PEAD position from the open book before manage() can exit it (observed live
2026-06-23). Both the pre_phase_a marker AND the pending-scan must exclude
robinhood_pead.
"""
from __future__ import annotations

from trading_corp.agents.paper_trade_replay import _load_pending, mark_pre_phase_a_rows
from trading_corp.persistence.db import connect, init_db


def _seed(url, order_id, division, tp_price):
    with connect(url) as c:
        c.execute(
            "INSERT INTO paper_trade_record (order_id, ts, strategy, division, "
            "symbol, side, qty, stop_price, tp_price) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (order_id, "2026-06-23T00:00:00+00:00", division, division, "F",
             "buy", 1.0, 13.0, tp_price),
        )


def test_mark_pre_phase_a_skips_robinhood_pead(tmp_db):
    init_db(tmp_db)
    _seed(tmp_db, "pead1", "robinhood_pead", None)      # no tp_price, result NULL
    _seed(tmp_db, "bx1", "bitunix_futures", None)       # no tp_price, result NULL
    mark_pre_phase_a_rows(tmp_db)
    with connect(tmp_db) as c:
        pead = c.execute("SELECT result FROM paper_trade_record WHERE order_id='pead1'").fetchone()["result"]
        bx = c.execute("SELECT result FROM paper_trade_record WHERE order_id='bx1'").fetchone()["result"]
    assert pead is None, "PEAD row must NOT be stamped pre_phase_a (it self-manages)"
    assert bx == "pre_phase_a", "non-PEAD no-tp row should still be marked"


def test_load_pending_excludes_robinhood_pead(tmp_db):
    init_db(tmp_db)
    _seed(tmp_db, "pead1", "robinhood_pead", None)
    _seed(tmp_db, "bx1", "bitunix_futures", 14.0)
    divs = {p.division for p in _load_pending(tmp_db)}
    assert "robinhood_pead" not in divs, "replay must not scan PEAD rows"
    assert "bitunix_futures" in divs
