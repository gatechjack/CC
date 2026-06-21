"""Render verification for the PEAD dashboard templates (plan's Phase-C
contract + render checks).

Seeds a synthetic open position (full extra_json primitives) + a scan session +
a DOWN feed, builds the REAL view via build_pead_view (stub broker supplies the
quote + equity), and renders both templates with the app's ACTUAL Jinja filters
under StrictUndefined for the partial — so any undefined `v.*` key, filter typo,
or unrendered branch fails the test. Also exercises the graceful-empty path.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import jinja2

from trading_corp.persistence.db import connect, init_db
from trading_corp.utils.time import format_et_full, format_et_hms, format_et_short
from trading_corp.web.app import (
    _fmt_compact,
    _fmt_money,
    _fmt_money_signed,
    _fmt_pct,
    _fmt_pct_signed,
    _fmt_strike,
)
from trading_corp.web.pead_view import DIVISION, build_pead_view

_TEMPLATES = Path(__file__).resolve().parents[1] / "trading_corp" / "web" / "templates"
_TODAY = date(2026, 6, 22)


def _env(strict: bool) -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATES)),
        autoescape=True,
        undefined=jinja2.StrictUndefined if strict else jinja2.Undefined,
    )
    env.filters.update({
        "money": _fmt_money, "money_signed": _fmt_money_signed,
        "pct": _fmt_pct, "pct_signed": _fmt_pct_signed,
        "compact_num": _fmt_compact, "strike": _fmt_strike,
        "et_hms": format_et_hms, "et_short": format_et_short, "et_full": format_et_full,
    })
    env.globals["stage1_badge"] = lambda request: {
        "division": "bitunix_futures", "execution_mode": "paper",
        "git_sha": "abc1234", "live_since_iso": "", "live_since_label": "—",
    }
    return env


class _StubBroker:
    paper = True

    async def quote(self, symbol):       # noqa: D401
        return 105.0

    async def snapshot(self):
        return SimpleNamespace(equity=75.0)


def _seed(url: str) -> None:
    extra = {
        "entry_atr_14": 4.0, "post_earnings_swing_low": 90.0,
        "pre_earnings_close": 100.0, "earnings_gap_top": 110.0,
        "entry_sue": 3.1, "next_earnings_date": "2026-09-15", "name": "Acme Corp",
    }
    sess = "2026-06-22T13:00:00+00:00"
    with connect(url) as conn:
        conn.execute(
            "INSERT INTO paper_trade_record "
            "(order_id, ts, strategy, division, symbol, side, qty, "
            " entry_reference_price, extra_json, execution_mode) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("pead-AAA-1", _TODAY.isoformat(), "robinhood_pead", DIVISION,
             "AAA", "buy", 2.0, 100.0, json.dumps(extra), "paper"),
        )
        for tk, verdict, reason in [
            ("AAA", "passed", None), ("BBB", "passed", None),
            ("CCC", "rejected", "below-min-cap"), ("DDD", "rejected", "below-min-cap"),
            ("EEE", "rejected", "financial/utility"),
        ]:
            conn.execute(
                "INSERT INTO scan_evaluation "
                "(session_ts, ticker, verdict, reason_code, created_ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (sess, tk, verdict, reason, sess),
            )
        conn.execute(
            "INSERT INTO data_feed_status "
            "(feed_name, status, last_ok_ts, last_check_ts, detail) VALUES (?, ?, ?, ?, ?)",
            ("eodhd", "down", None, sess, "HTTP 500"),
        )
        conn.execute(
            "INSERT INTO data_feed_status "
            "(feed_name, status, last_ok_ts, last_check_ts, detail) VALUES (?, ?, ?, ?, ?)",
            ("tastytrade", "live", sess, sess, "ok"),
        )


def _view(url: str) -> dict:
    deps = SimpleNamespace(
        db_url=url, data_exec=SimpleNamespace(brokers={DIVISION: _StubBroker()}),
    )
    return asyncio.run(build_pead_view(deps, today=_TODAY))


def test_seeded_view_has_complete_book_row(tmp_path):
    url = f"sqlite:///{tmp_path / 't.db'}"
    init_db(url)
    _seed(url)
    view = _view(url)
    assert view["mode"]["paper"] is True
    assert view["account"]["equity"] == 75.0
    assert len(view["book"]) == 1
    row = view["book"][0]
    assert row["symbol"] == "AAA" and row["complete"] is True
    assert row["governing"] in ("stop", "drift", "guard", "time")
    assert view["health"]["eodhd"]["status"] == "down"
    assert (view["funnel"]["scanned"], view["funnel"]["qualified"]) == (5, 2)
    assert view["rejections"]["reconciles"] is True


def test_partial_renders_strict(tmp_path):
    url = f"sqlite:///{tmp_path / 't.db'}"
    init_db(url)
    _seed(url)
    html = _env(strict=True).get_template(
        "partials/pead_live_sections.html").render(v=_view(url))
    assert "AAA" in html
    assert "Acme Corp" in html
    assert "awaiting position metadata" not in html   # seeded row is complete
    assert "below-min-cap" in html                    # rejection tally rendered
    assert "No closed trades yet" in html             # equity empty state


def test_partial_renders_empty_strict(tmp_path):
    url = f"sqlite:///{tmp_path / 't.db'}"
    init_db(url)
    html = _env(strict=True).get_template(
        "partials/pead_live_sections.html").render(v=_view(url))   # no seed
    assert "No open positions" in html
    assert "No scan yet today" in html


def test_full_page_renders(tmp_path):
    url = f"sqlite:///{tmp_path / 't.db'}"
    init_db(url)
    _seed(url)
    req = SimpleNamespace(url=SimpleNamespace(path="/telemetry/pead"))
    html = _env(strict=False).get_template(
        "pead_live.html").render(v=_view(url), request=req)
    assert "PAPER" in html        # unmissable paper pill
    assert "HALT PEAD" in html    # kill switch (only write surface)
    assert "DOWN" in html         # eodhd down chip distinct from live
    assert "v1" in html
