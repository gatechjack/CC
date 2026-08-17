"""Phase 2b CP3 tests — broker-free poly_kalshi live view + partial templates.
No broker, no network: the builder joins audit_event + poly_kalshi_mark_live/_history +
kalshi_round_trips (SELECT-only). Also renders the HTMX partial templates on fixture data
(catches Jinja errors) and proves graceful rendering when a trigger/mark is absent."""
from __future__ import annotations

import json
import os

import pytest

from trading_corp.persistence import db as _db
from trading_corp.web import data as wd


@pytest.fixture
def hdb(tmp_path):
    url = f"sqlite:///{tmp_path / 'live.db'}"
    _db.init_db(url)
    return url


def _order(db_url, *, order_id, ticker="KXMLBGAME-A-MIA", fill_price=0.54, fill_count=9,
           whale="SDTrading", trigger=True, status="placed", ts="2026-08-16T18:00:00+00:00"):
    p = {"status": status, "division": "poly_kalshi_mlb", "action": "entry", "outcome": "yes",
         "ticker": ticker, "order_id": order_id, "whale": whale,
         "fill_price": fill_price, "fill_count": fill_count, "count": fill_count}
    if trigger:
        p.update(poly_slug="mlb-mia-cin-2026-08-16", poly_outcome="Miami Marlins",
                 poly_side="BUY", poly_market_type="moneyline")
    with _db.connect(db_url) as c:
        c.execute("INSERT INTO audit_event (ts, actor, kind, payload_json) VALUES (?,?,?,?)",
                  (ts, "poly_kalshi_mlb", "poly_kalshi_order", json.dumps(p)))


def _mark(db_url, *, order_id, ticker="KXMLBGAME-A-MIA", yes_mid=0.60, unrealized=0.54,
          unrealized_pct=11.1, mark_ts="2026-08-16T18:00:30+00:00"):
    with _db.connect(db_url) as c:
        c.execute("INSERT OR REPLACE INTO poly_kalshi_mark_live "
                  "(order_id, ticker, yes_mid, unrealized, unrealized_pct, mark_ts) VALUES (?,?,?,?,?,?)",
                  (order_id, ticker, yes_mid, unrealized, unrealized_pct, mark_ts))


def _hist(db_url, order_id, series, ticker="KXMLBGAME-A-MIA"):
    with _db.connect(db_url) as c:
        for v in series:
            c.execute("INSERT INTO poly_kalshi_mark_history (order_id, ticker, yes_mid, ts) VALUES (?,?,?,?)",
                      (order_id, ticker, v, "2026-08-16T18:00:00+00:00"))


def _resolve(db_url, order_id):
    with _db.connect(db_url) as c:
        c.execute("INSERT INTO kalshi_round_trips (order_id, ticker, strategy, division, outcome_bet, qty, "
                  "entry_price, notional, entry_ts, resolved_ts, market_result, won, realized_pnl, roi_pct) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (order_id, "KX", "poly_kalshi_mlb", "poly_kalshi_mlb", "yes", 9, 0.5, 4.5,
                   "t", "t", "yes", 1, 4.5, 100.0))


def test_live_view_full_position(hdb):
    _order(hdb, order_id="mia", fill_price=0.54, fill_count=9)
    _mark(hdb, order_id="mia", yes_mid=0.60, unrealized=(0.60 - 0.54) * 9, unrealized_pct=11.1,
          mark_ts="2026-08-16T18:00:30+00:00")
    _hist(hdb, "mia", [0.54, 0.56, 0.58, 0.60])
    v = wd.build_poly_kalshi_live_view(hdb)
    assert v.n_open == 1
    pos = v.open_positions[0]
    assert pos.order_id == "mia" and pos.contracts == 9.0 and pos.fill_price == 0.54
    assert pos.cost_basis == pytest.approx(0.54 * 9) and pos.whale == "SDTrading"
    assert pos.poly_outcome == "Miami Marlins" and pos.poly_side == "BUY"     # CP1 trigger
    assert pos.yes_mid == pytest.approx(0.60)                                 # CP2 mark
    assert pos.unrealized == pytest.approx((0.60 - 0.54) * 9)
    assert pos.sparkline == [0.54, 0.56, 0.58, 0.60] and pos.sparkline_text != ""
    assert v.total_unrealized == pytest.approx((0.60 - 0.54) * 9)
    assert v.latest_order_id == "mia"


def test_live_view_renders_readable_team_names(hdb):
    # a real KXMLBGAME ticker -> readable matchup + bet team, broker-free (Part 1).
    _order(hdb, order_id="bal", ticker="KXMLBGAME-26AUG171805BALTB-TB")
    v = wd.build_poly_kalshi_live_view(hdb)
    pos = v.open_positions[0]
    assert pos.market_title == "Tampa Bay Rays vs Baltimore Orioles"   # readable, not raw ticker
    assert pos.bet_team == "Tampa Bay Rays"                            # YES-side team (the copy leg)
    assert pos.ticker == "KXMLBGAME-26AUG171805BALTB-TB"              # raw ticker kept (tooltip/identity)
    assert v.copy_moments[0].market_title == "Tampa Bay Rays vs Baltimore Orioles"
    assert v.copy_moments[0].bet_team == "Tampa Bay Rays"


def test_live_view_unparseable_ticker_falls_back_to_raw(hdb):
    _order(hdb, order_id="odd", ticker="KXNBA-WEIRD")                  # non-MLB -> parser returns None
    v = wd.build_poly_kalshi_live_view(hdb)
    assert v.open_positions[0].market_title == "KXNBA-WEIRD"          # raw fallback, never blank
    assert v.open_positions[0].bet_team is None


def test_live_view_graceful_when_trigger_and_mark_absent(hdb):
    _order(hdb, order_id="bare", trigger=False)          # pre-CP1 (no trigger) + unmarked
    v = wd.build_poly_kalshi_live_view(hdb)
    pos = v.open_positions[0]
    assert pos.poly_outcome is None and pos.poly_slug is None      # no trigger -> None (graceful)
    assert pos.yes_mid is None and pos.unrealized is None and pos.stale is True   # unmarked
    assert pos.sparkline == [] and pos.sparkline_text == ""
    assert v.total_unrealized is None                             # no marks -> None (not fabricated 0)


def test_live_view_excludes_resolved_and_precp3(hdb):
    _order(hdb, order_id="open1")
    _order(hdb, order_id="res")
    _resolve(hdb, "res")                                 # resolved -> excluded
    _order(hdb, order_id="", trigger=False)              # pre-CP3: no order_id -> excluded
    v = wd.build_poly_kalshi_live_view(hdb)
    assert [p.order_id for p in v.open_positions] == ["open1"]


def test_live_view_copy_moment_feed_newest_first(hdb):
    _order(hdb, order_id="old", ts="2026-08-16T17:00:00+00:00")
    _order(hdb, order_id="new", ts="2026-08-16T19:00:00+00:00")
    v = wd.build_poly_kalshi_live_view(hdb)
    assert [m.order_id for m in v.copy_moments] == ["new", "old"]  # newest first
    assert v.copy_moments[0].poly_outcome == "Miami Marlins"        # CP1 trigger in the feed
    assert v.latest_order_id == "new" and v.latest_ts == "2026-08-16T19:00:00+00:00"


def test_live_view_stale_flag_on_old_mark(hdb):
    _order(hdb, order_id="mia")
    _mark(hdb, order_id="mia", yes_mid=0.6, mark_ts="2020-01-01T00:00:00+00:00")   # ancient -> stale
    v = wd.build_poly_kalshi_live_view(hdb)
    assert v.open_positions[0].stale is True and v.open_positions[0].yes_mid == pytest.approx(0.6)


def test_live_view_is_broker_free(hdb):
    import inspect
    assert list(inspect.signature(wd.build_poly_kalshi_live_view).parameters) == ["db_url"]  # only db_url
    _order(hdb, order_id="x")
    assert wd.build_poly_kalshi_live_view(hdb).n_open == 1        # runs with NO broker at all


# ── the HTMX partial templates render on fixture data (catch Jinja errors) ──
def _env():
    from jinja2 import Environment, FileSystemLoader
    import trading_corp.web as _webpkg
    tdir = os.path.join(os.path.dirname(_webpkg.__file__), "templates")
    return Environment(loader=FileSystemLoader(tdir), autoescape=True)


def test_inner_template_renders_positions_marks_moments(hdb):
    _order(hdb, order_id="mia")
    _mark(hdb, order_id="mia")
    _hist(hdb, "mia", [0.54, 0.60])
    _order(hdb, order_id="bare", trigger=False)          # graceful: no trigger, no mark
    view = wd.build_poly_kalshi_live_view(hdb)
    html = _env().get_template("partials/poly_kalshi_live_inner.html").render(live=view)
    assert "Live unrealized" in html
    assert "Miami Marlins" in html                       # trigger rendered
    assert "marking" in html                             # unmarked 'bare' -> "marking..."
    assert "Recent copies" in html                       # copy-moment feed
    assert "as of" in html                               # mark staleness label


def test_shell_template_wires_hx_trigger():
    html = _env().get_template("partials/poly_kalshi_live.html").render()
    assert 'hx-get="/partials/prediction-markets/poly_kalshi_mlb/live"' in html
    assert 'hx-trigger="load, every 60s"' in html
