"""FORK 2 (2026-07-30): on a stash HIT, execute_pair_orders dispatches the previewed
combo WITHOUT re-running analyze_symbol (LLM). The dispatch-time earnings re-check +
fingerprint consent match still run; the display is synthesized from the stash. A
stash MISS still rebuilds (LLM) and fingerprint-bails on a drifted contract."""
from __future__ import annotations

import types
from collections import namedtuple

from fastapi.testclient import TestClient

from trading_corp.persistence import db
from trading_corp.persistence.models import ProposedOrder
from trading_corp.web.app import WebDeps, create_app
from trading_corp.web import pmcc_preview


_Fill = namedtuple("_Fill", ["order_id", "price", "venue"])


def _leg(side, effect, strike, expiration, *, action, limit):
    return ProposedOrder(
        strategy="robinhood_pmcc", symbol="AAPL", side=side, qty=1.0,
        order_type="limit", limit_price=limit, rationale="roll",
        extra={
            "is_option": True, "underlying": "AAPL", "option_type": "call",
            "expiration": expiration, "strike": strike, "position_effect": effect,
            "action": action, "is_multi_leg": True, "combo_id": "c1",
            "combo_direction": "credit", "net_limit_price": 0.60, "ratio_quantity": 1,
        },
    )


def _roll_legs():
    return [
        _leg("buy", "close", 170.0, "2026-07-31", action="roll_short_call_close", limit=1.20),
        _leg("sell", "open", 175.0, "2026-08-07", action="roll_short_call_open", limit=1.80),
    ]


class _Broker:
    paper = True

    async def snapshot(self):
        return types.SimpleNamespace(equity=100_000.0, account="acct")


class _DataExec:
    def __init__(self):
        self.brokers = {"robinhood_pmcc": _Broker()}
        self.place_combo_calls = []

    async def place_combo(self, legs, division=None):
        self.place_combo_calls.append(list(legs))
        return [_Fill(order_id=o.id, price=1.55, venue="paper") for o in legs]

    async def place(self, order, division=None):
        raise AssertionError("combo must not leg in via single-leg place")


class _Risk:
    def evaluate(self, order, account, strat_state, regime, _):
        return types.SimpleNamespace(verdict="approve", reason="ok", new_qty=None)


class _Logger:
    def __init__(self):
        self.events = []

    def log_proposed_order(self, o):
        pass

    def log_event(self, actor=None, kind=None, payload=None):
        self.events.append({"actor": actor, "kind": kind, "payload": payload})


class _PMCC:
    def __init__(self, earnings_kind="clear"):
        self.analyze_calls = 0
        self.propose_calls = 0
        self.reprice_calls = 0
        self._earnings_kind = earnings_kind
        self._cfg = {"tile_status": {}}

    async def analyze_symbol(self, broker, sym, regime="unknown"):
        self.analyze_calls += 1
        return types.SimpleNamespace(
            action="roll_short", urgency="routine", confidence=0.8, summary="",
            rationale="", warnings=[], target_delta=None, target_dte=None)

    def earnings_card_state(self, sym, short_strike=None, spot=None):
        return {"kind": self._earnings_kind,
                "offer_roll": self._earnings_kind != "blocked", "date": "2026-08-05"}

    async def propose_orders_for_pair(self, broker, sym, analysis, *, preview=False):
        self.propose_calls += 1
        return _roll_legs()

    async def reprice_combo(self, legs, broker):
        self.reprice_calls += 1


def _client(pmcc, tmp_db):
    db.init_db(tmp_db)
    deps = WebDeps(
        db_url=tmp_db, db_path=tmp_db.replace("sqlite:///", ""), mode="PAPER",
        logger_agent=_Logger(), data_exec=_DataExec(),
        trend_agent=types.SimpleNamespace(
            read=lambda: types.SimpleNamespace(regime="neutral")),
        portfolio=None, pmcc_agent=pmcc, fidelity_agent=None, paper_broker=None,
        secrets=None, risk_agent=_Risk(),
    )
    return TestClient(create_app(deps)), deps


def test_stash_hit_dispatches_without_llm(tmp_db):
    pmcc = _PMCC(earnings_kind="clear")
    client, deps = _client(pmcc, tmp_db)
    orders = _roll_legs()
    pid, fp = pmcc_preview.stash_preview(
        "robinhood_pmcc", "AAPL", orders, action="roll_short")
    r = client.post("/division/robinhood_pmcc/pair/AAPL/execute",
                    data={"preview_id": pid, "fingerprint": fp})
    assert r.status_code == 200
    # FORK 2: NO analyze_symbol (LLM) call, and no rebuild — the combo came from the stash
    assert pmcc.analyze_calls == 0
    assert pmcc.propose_calls == 0
    # earnings re-check ran (clear → proceed) and the EXACT stashed legs fired
    assert len(deps.data_exec.place_combo_calls) == 1
    fired = deps.data_exec.place_combo_calls[0]
    assert [o.id for o in fired] == [o.id for o in orders]
    assert "AAPL" in r.text                      # display populated from stash


def test_stash_hit_earnings_blocked_bails_no_llm_no_place(tmp_db):
    pmcc = _PMCC(earnings_kind="blocked")
    client, deps = _client(pmcc, tmp_db)
    orders = _roll_legs()
    pid, fp = pmcc_preview.stash_preview(
        "robinhood_pmcc", "AAPL", orders, action="roll_short")
    r = client.post("/division/robinhood_pmcc/pair/AAPL/execute",
                    data={"preview_id": pid, "fingerprint": fp})
    assert r.status_code == 200
    assert pmcc.analyze_calls == 0                        # still no LLM on the hit path
    assert deps.data_exec.place_combo_calls == []         # blocked → nothing placed
    assert any(e["kind"] == "pmcc_consent_earnings_block" for e in deps.logger_agent.events)


def test_stash_miss_wrong_fp_rebuilds_and_bails(tmp_db):
    """A wrong fingerprint misses the stash → the miss path runs the LLM rebuild,
    then the fingerprint guard bails rather than firing a drifted contract."""
    pmcc = _PMCC(earnings_kind="clear")
    client, deps = _client(pmcc, tmp_db)
    orders = _roll_legs()
    pid, _fp = pmcc_preview.stash_preview(
        "robinhood_pmcc", "AAPL", orders, action="roll_short")
    r = client.post("/division/robinhood_pmcc/pair/AAPL/execute",
                    data={"preview_id": pid, "fingerprint": "deadbeef00000000"})
    assert r.status_code == 200
    assert pmcc.analyze_calls == 1                        # miss → LLM rebuild path ran
    assert pmcc.propose_calls == 1
    assert deps.data_exec.place_combo_calls == []         # fingerprint mismatch → bail
    assert any(e["kind"] == "pmcc_consent_fingerprint_mismatch"
               for e in deps.logger_agent.events)
