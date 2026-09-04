"""Bet-slot pass (2026-09-04): copied-from whale on each HELD slot + the non-MLB positions row.

Jack reversed the drawer-only attribution ruling: each held slot now carries the whale it was copied from --
the FIRST label plus a '+N' count of additional whales, journal-sourced (never inferred). The pixel-level
right-truncation is CSS (verified in the render harness cc/pm_betslot_render.py); here we lock the DATA the
template renders: single whale, two whales '+1', wallet-only, unheld (no tag), settled (no tag)."""
from trading_corp.prediction_markets.web import live_view as lv
from trading_corp.prediction_markets.web import feed_mlb, marks as marks_mod

STEM = "26SEP021240SDCIN"
ML = "KXMLBGAME-%s-SD" % STEM
TOT = "KXMLBTOTAL-%s-O8.5" % STEM
WALLET = "0x64e93f87d8a0c1b2cde6f20d71f211372a95eb4c"


# ── the pure tag helper ──────────────────────────────────────────────────────────────────────────────────────
def test_whale_tag_single():
    assert lv._whale_tag(["Kingfish"]) == {"first": "Kingfish", "extra": 0, "all": ["Kingfish"]}


def test_whale_tag_two_is_plus_one():
    t = lv._whale_tag(["Kingfish", "domer"])
    assert t["first"] == "Kingfish" and t["extra"] == 1 and t["all"] == ["Kingfish", "domer"]


def test_whale_tag_three_is_plus_two():
    assert lv._whale_tag(["a", "b", "c"])["extra"] == 2


def test_whale_tag_wallet_only():
    t = lv._whale_tag([WALLET])
    assert t["first"] == WALLET and t["extra"] == 0        # the full wallet is the data; CSS right-truncates it


def test_whale_tag_empty_is_none():
    assert lv._whale_tag([]) is None and lv._whale_tag(None) is None


# ── the tag on a built slot ──────────────────────────────────────────────────────────────────────────────────
def _open(contracts=5, cost=2.60):
    return {"ticker": ML, "market_type": "moneyline", "held_leg": "yes", "contracts": contracts,
            "cost_basis_usd": cost, "avg_price": 0.52, "fees_usd": 0.04}


def _mark():
    return marks_mod.Mark(ML, 0.58, 0.41, 0.60, 0.43, 0.59, "active", 1)


def test_open_slot_carries_single_whale():
    slot = lv._build_slot(ML, "moneyline", _open(), None, _mark(), whales=["Kingfish"])
    assert slot["whale_tag"] == {"first": "Kingfish", "extra": 0, "all": ["Kingfish"]}
    assert slot["whales"] == ["Kingfish"] and slot["settled"] is False


def test_open_slot_two_whales_plus_one():
    slot = lv._build_slot(ML, "moneyline", _open(), None, _mark(), whales=["Kingfish", "domer"])
    assert slot["whale_tag"]["first"] == "Kingfish" and slot["whale_tag"]["extra"] == 1


def test_open_slot_wallet_only():
    slot = lv._build_slot(ML, "moneyline", _open(), None, _mark(), whales=[WALLET])
    assert slot["whale_tag"]["first"] == WALLET and slot["whale_tag"]["extra"] == 0


def test_unheld_slot_has_no_tag():
    # no open position and no settlement -> the slot is 'not held' (None); the template renders it dimmed, no whale
    assert lv._build_slot(TOT, "total", None, None, None) is None


def test_settled_slot_shows_whale():
    # Jack 2026-09-04: a SETTLED slot shows its copied-from whale too (from the entry-fill copiers).
    settle = {"won": True, "contracts": 5, "realized": 4.8, "settled_ts": 1000}
    slot = lv._build_slot(ML, "moneyline", None, settle, None, settled_leg="yes", whales=["Kingfish"])
    assert slot["settled"] is True and slot["whale_tag"] == {"first": "Kingfish", "extra": 0, "all": ["Kingfish"]}


def test_settled_slot_multi_whale_plus_one():
    settle = {"won": False, "contracts": 5, "realized": -5.0, "settled_ts": 1000}
    slot = lv._build_slot(ML, "moneyline", None, settle, None, settled_leg="yes", whales=["domer", "Kingfish"])
    assert slot["whale_tag"]["first"] == "domer" and slot["whale_tag"]["extra"] == 1


def test_settled_slot_empty_whales_is_none():
    slot = lv._build_slot(ML, "moneyline", None, {"won": True, "contracts": 5}, None, settled_leg="yes", whales=[])
    assert slot["settled"] is True and slot["whale_tag"] is None


# ── _entry_whales: the settled slot's whale set, from entry fills (never the exit rows) ──────────────────────
def test_entry_whales_from_entry_fills_only_sorted_by_wallet():
    orders = [{"ticker": ML, "is_exit": 0, "outcome_status": "filled", "wallet": "0xb", "user_name": "domer"},
              {"ticker": ML, "is_exit": 0, "outcome_status": "filled", "wallet": "0xa", "user_name": "Kingfish"},
              {"ticker": ML, "is_exit": 1, "outcome_status": "filled", "wallet": "0xa", "close_source": "settlement"}]
    assert lv._entry_whales(orders) == ["Kingfish", "domer"]     # entries only (exit ignored), wallet-sorted


def test_entry_whales_wallet_only_and_distinct():
    orders = [{"is_exit": 0, "outcome_status": "filled", "wallet": WALLET, "user_name": None},
              {"is_exit": 0, "outcome_status": "filled", "wallet": WALLET, "user_name": None}]   # duplicate wallet
    assert lv._entry_whales(orders) == [WALLET]                  # one label, wallet used when no name


# ── end-to-end through build_live_context: the whale reaches the card slot from open_positions_by_whale ───────
def _slate():
    return feed_mlb.SlateResult("2026-09-02", {}, False, None, 6000, error="down")   # feed down is fine; slot is journal-built


def _ctx(by_whale):
    return lv.build_live_context(
        orders=[{"id": 1, "ticker": ML, "outcome_leg": "yes", "is_exit": 0, "outcome_status": "filled",
                 "fill_count": 5, "fill_price": 0.52, "wallet": by_whale[0]["wallet"], "response_ts": 100}],
        open_positions=[_open()], open_positions_by_whale=by_whale, slate=_slate(),
        marks_result=marks_mod.MarksResult({ML: _mark()}, True, 6000), now_ts=6000, category="mlb")


def test_context_slot_gets_whale_from_by_whale_rows():
    ctx = _ctx([{"ticker": ML, "wallet": "0xa", "user_name": "Kingfish"},
                {"ticker": ML, "wallet": "0xb", "user_name": "domer"}])
    slot = ctx["cards"][0]["slots_by_kind"]["ML"]
    assert slot["whale_tag"]["first"] in ("Kingfish", "domer") and slot["whale_tag"]["extra"] == 1


def test_context_wallet_only_whale_reaches_slot():
    ctx = _ctx([{"ticker": ML, "wallet": WALLET, "user_name": None}])   # no display name -> wallet is the label
    slot = ctx["cards"][0]["slots_by_kind"]["ML"]
    assert slot["whale_tag"]["first"] == WALLET


# ── the non-MLB positions row carries the same tag ───────────────────────────────────────────────────────────
def test_positions_row_carries_whale_tag():
    HAL = "KXATPMATCH-26SEP03ZVEHAL-HAL"
    ctx = lv.build_live_context(
        orders=[{"id": 1, "ticker": HAL, "outcome_leg": "yes", "is_exit": 0, "outcome_status": "filled",
                 "fill_count": 5, "fill_price": 0.10, "wallet": "0xstc", "response_ts": 100}],
        open_positions=[{"ticker": HAL, "held_leg": "yes", "contracts": 5, "cost_basis_usd": 0.5,
                         "avg_price": 0.1, "fees_usd": 0.0, "market_type": "moneyline"}],
        open_positions_by_whale=[{"ticker": HAL, "wallet": "0xstc", "user_name": "STC14"},
                                 {"ticker": HAL, "wallet": "0xk", "user_name": "Kingfish"}],
        slate=_slate(), marks_result=marks_mod.MarksResult({}, True, 6000), now_ts=6000, category="atp")
    row = ctx["positions_view"]["active"][0]
    assert row["whale_tag"]["first"] in ("STC14", "Kingfish") and row["whale_tag"]["extra"] == 1


# ── settled slot / row gets its whale end-to-end from the entry fills (Jack 2026-09-04) ──────────────────────
def _settled_orders(ticker, wallet="0xa", name="Kingfish", won=1):
    return [{"id": 1, "ticker": ticker, "outcome_leg": "yes", "is_exit": 0, "outcome_status": "filled",
             "fill_count": 5, "fill_price": 0.52, "wallet": wallet, "user_name": name, "response_ts": 100},
            {"id": 2, "ticker": ticker, "outcome_leg": "yes", "is_exit": 1, "outcome_status": "filled",
             "fill_count": 5, "close_source": "settlement", "won": won, "realized_pnl": (4.8 if won else -5.0),
             "settled_ts": 5000, "response_ts": 5000, "wallet": wallet}]


def test_context_settled_card_slot_gets_whale_from_entries():
    ctx = lv.build_live_context(orders=_settled_orders(ML), open_positions=[], open_positions_by_whale=[],
                                slate=_slate(), marks_result=marks_mod.MarksResult({}, True, 6000),
                                now_ts=6000, category="mlb")
    slot = ctx["cards"][0]["slots_by_kind"]["ML"]
    assert slot["settled"] is True and slot["whale_tag"]["first"] == "Kingfish"   # net-open set is empty; entries name it


def test_positions_complete_row_carries_whale_tag():
    HAL = "KXATPMATCH-26SEP03ZVEHAL-HAL"
    ctx = lv.build_live_context(orders=_settled_orders(HAL, wallet="0xstc", name="STC14", won=0),
                                open_positions=[], open_positions_by_whale=[], slate=_slate(),
                                marks_result=marks_mod.MarksResult({}, True, 6000), now_ts=6000, category="atp")
    row = ctx["positions_view"]["complete"][0]
    assert row["status"] == "settled" and row["whale_tag"]["first"] == "STC14"
