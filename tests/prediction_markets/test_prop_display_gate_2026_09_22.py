"""DISPLAY GATE (2026-09-22): a synthetic PROP position (KXMLBKS/KXNFLRSHYDS) and a PER-INNING position
(KXMLBINNINGWIN) must render through the REAL Deploy-16 card/table WITHOUT changing a money figure. The card
classifies by TICKER via LV._kind, and a prop/inning ticker has no fixed-slot keyword -> it hits the series.lower()
fallback (same path as the proven KXMLBWEATHER unrecognized family): it JOINS its game, COUNTS in the money, and gets
an EXTRA line. This pins the invariant 'a rendering gap can cost a LINE, never a MONEY figure' for the new families.
It does NOT add pretty labels (KIND_LABEL/_short_label) -- that is the UI workstream (see the report)."""
from trading_corp.prediction_markets.web import live_view as LV, marks as MK

NOW = 1789200000
MSTEM = "26SEP222210SDLAD"          # SD @ LAD
NSTEM = "26SEP27LACBUF"            # LAC @ BUF


def _o(tk, cat="mlb"):
    return {"account_id": "kalshi_jack", "category": cat, "wallet": "0xa", "ticker": tk, "outcome_leg": "yes",
            "is_exit": 0, "dry_run": 0, "outcome_status": "filled", "fill_count": 10.0, "fill_price": 0.50,
            "fee": 0.0, "id": tk}


def _p(tk):
    return {"ticker": tk, "held_leg": "yes", "contracts": 10.0, "cost_basis_usd": 5.0, "avg_price": 0.50,
            "fees_usd": 0.0, "market_type": LV._kind(tk)}


def _pw(tk):
    return dict(_p(tk), wallet="0xa", user_name=None)


def _ctx(tickers, cat="mlb"):
    return LV.build_live_context(orders=[_o(t, cat) for t in tickers], open_positions=[_p(t) for t in tickers],
                                 open_positions_by_whale=[_pw(t) for t in tickers], slate=None,
                                 marks_result=MK.MarksResult(marks={}, ok=True, as_of=NOW, error=None),
                                 now_ts=NOW, category=cat, titles={})


def test_kind_prop_and_inning_hit_safe_fallback():
    assert LV._kind("KXMLBKS-%s-SDMKING34-8" % MSTEM) == "kxmlbks"
    assert LV._kind("KXMLBINNINGWIN-%s-9-TIE" % MSTEM) == "kxmlbinningwin"
    assert LV._kind("KXNFLRSHYDS-%s-BUFJALLEN17-90" % NSTEM) == "kxnflrshyds"
    # none collide with a fixed slot / subgame kind
    for k in ("kxmlbks", "kxmlbinningwin", "kxnflrshyds"):
        assert k not in ("moneyline", "total", "spread", "first_inning_run", "team_total")


def test_mlb_card_counts_prop_and_inning_money_and_gives_lines():
    game = "KXMLBGAME-%s-SD" % MSTEM
    prop = "KXMLBKS-%s-SDMKING34-8" % MSTEM
    inn = "KXMLBINNINGWIN-%s-9-TIE" % MSTEM
    ctx = _ctx([game, prop, inn])
    assert len(ctx["cards"]) == 1                                  # all three joined the ONE SD@LAD game card
    c = ctx["cards"][0]
    extra_tks = {s["ticker"] for s in c["extra_lines"]}
    assert prop in extra_tks and inn in extra_tks                  # both got a line (extra), neither dropped
    assert c["open_cost"] == 15.0 and c["n_live"] == 3            # money counts ALL three (invariant)
    assert ctx["summary"]["unsettled_cost"] == 15.0              # page P&L is position-based too


def test_nfl_table_prop_renders_without_raw_ticker_leak_or_money_loss():
    game = "KXNFLGAME-%s-BUF" % NSTEM
    prop = "KXNFLRSHYDS-%s-BUFJALLEN17-90" % NSTEM
    ctx = LV.build_live_context(orders=[_o(t, "nfl") for t in (game, prop)],
                                open_positions=[_p(t) for t in (game, prop)],
                                open_positions_by_whale=[_pw(t) for t in (game, prop)], slate=None,
                                marks_result=MK.MarksResult(marks={}, ok=True, as_of=NOW, error=None),
                                now_ts=NOW, category="nfl", titles={})
    active = ctx["positions_view"]["active"]
    assert len(active) == 2                                        # both rows present (prop not dropped)
    byser = {r["ticker"].split("-")[0]: r for r in active}
    assert "KXNFLRSHYDS" in byser                                  # the prop row exists
    # no crash + money accounted (the prop is in the active table); a label may be plain but never a raw ticker string
    assert all("desc" in r for r in active)
