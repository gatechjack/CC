"""Rename-defense (Part 3) proofs.

(A) test_notfound_symbol_skips_exit_never_sells:
    a NOT-FOUND ticker (renamed/delisted -> broker.quote(strict=True) raises
    QuoteSymbolUnresolved) makes manage() SKIP exit eval and FLAG the row. It never
    derives a phantom stop from a $0 quote and never sells. (The ISSC bug was:
    get_latest_price(['ISSC']) -> None -> 0.0 -> stop pressure clamps to 1.0 -> a
    real phantom 'stop' sell. The _ISSC_EXTRA primitives below reproduce exactly
    that: a $0.00 quote WOULD fire the stop. The fix skips + flags instead.)

(B) test_identity_reresolution_auto_rewrites_issc_to_ia:
    identity re-resolution auto-rewrites a renamed symbol by its rename-stable
    instrument_id (ISSC -> IA), automated -- no manual ledger edit.

Mirrors tests/test_pead_offhours_single_outcome.py's harness (temp sqlite ledger,
paper mode, injected window state).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from trading_corp.persistence import db
from trading_corp.persistence.db import init_db
from trading_corp.agents.strategies.pead_strategy import PEADStrategy
from trading_corp.brokers.robinhood import QuoteSymbolUnresolved

SLUG = PEADStrategy.SLUG

# entry 23.20, ATR 1.27 -> stop_level 20.025; gap_top 23.20, pre_close 20.13.
# A $0.00 quote clamps stop pressure to 1.0 -> phantom 'stop' sell (the ISSC case).
_ISSC_EXTRA = {"entry_atr_14": 1.27, "post_earnings_swing_low": 19.83,
               "pre_earnings_close": 20.13, "earnings_gap_top": 23.20}


class _FakeLogger:
    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    def log_event(self, actor, kind, payload):
        self.events.append((actor, kind, payload))
        return len(self.events)

    def log_proposed_order(self, order):
        pass

    def kinds(self) -> list[str]:
        return [k for _, k, _ in self.events]


class _UnresolvedBroker:
    """quote(strict=True) raises for a renamed/delisted ticker -- the RobinhoodBroker
    contract for a symbol that resolves to no instrument. The non-strict path still
    returns the legacy 0.0 (which is exactly what USED to cause the phantom stop)."""
    async def snapshot(self):
        return SimpleNamespace(equity=5000.0)

    async def quote(self, symbol, *, strict=False):
        if strict:
            raise QuoteSymbolUnresolved(symbol)
        return 0.0


class _ReresolveBroker(_UnresolvedBroker):
    """Adds identity resolution: a stale instrument_id -> the CURRENT ticker."""
    def __init__(self, id_to_symbol):
        self._map = dict(id_to_symbol)

    async def symbol_for_instrument_id(self, instrument_id):
        return self._map.get(instrument_id)


def _mk_strat(url, logger):
    strat = PEADStrategy(
        db_url=url, risk_agent=None, data_exec=None, logger_agent=logger,
        earnings_provider=object(), strategies_yaml=Path("does-not-exist.yaml"),
        execution_mode="paper",
    )
    strat._risk_ok = lambda order, equity: True          # risk gate orthogonal + unchanged
    strat._exit_window_state = lambda now, cfg: ("session", True)   # force placement window
    return strat


def _insert_open_row(url, *, order_id, symbol, extra):
    db.insert_paper_trade_record({
        "order_id": order_id, "ts": datetime.now(timezone.utc).date().isoformat(),
        "strategy": SLUG, "division": SLUG, "symbol": symbol, "side": "buy", "qty": 3.11,
        "entry_reference_price": 23.20, "result": None,
        "extra_json": json.dumps(extra), "execution_mode": "paper",
    }, db_url=url)


def _row(url, order_id):
    with db.connect(url) as conn:
        return conn.execute(
            "SELECT symbol, result, "
            "json_extract(extra_json,'$.symbol_unresolved') AS unresolved, "
            "json_extract(extra_json,'$.name') AS name "
            "FROM paper_trade_record WHERE order_id=?", (order_id,)).fetchone()


# ── (A) not-found ticker -> skip exit eval + flag; NEVER a phantom stop sell ───
def test_notfound_symbol_skips_exit_never_sells(tmp_path):
    url = f"sqlite:///{tmp_path / 't.db'}"
    init_db(url)
    logger = _FakeLogger()
    strat = _mk_strat(url, logger)
    # ISSC-like row WITHOUT an instrument_id (pre-hook-3): cannot auto-heal, so the
    # only safe behavior is flag + skip (a $0.00 quote would otherwise fire 'stop').
    _insert_open_row(url, order_id="issc1", symbol="ISSC", extra=dict(_ISSC_EXTRA))

    exits, _ = asyncio.run(strat.manage(_UnresolvedBroker()))

    assert exits == []                                     # NO phantom stop sell
    r = _row(url, "issc1")
    assert r["result"] is None                             # position NOT closed
    assert r["unresolved"] == 1                            # durably flagged
    assert logger.kinds().count("pead_symbol_unresolved") == 1
    assert logger.kinds().count("pead_exit") == 0          # never fired an exit


# ── (B) identity re-resolution auto-rewrites ISSC -> IA by instrument_id ───────
def test_identity_reresolution_auto_rewrites_issc_to_ia(tmp_path):
    url = f"sqlite:///{tmp_path / 't.db'}"
    init_db(url)
    logger = _FakeLogger()
    strat = _mk_strat(url, logger)
    IID = "6a465f9b-af54-46e9-88c8-24d974eefd28"           # the real IA instrument_id
    extra = dict(_ISSC_EXTRA, name="ISSC", instrument_id=IID, symbol_unresolved=1)
    _insert_open_row(url, order_id="issc2", symbol="ISSC", extra=extra)

    broker = _ReresolveBroker({IID: "IA"})                 # instrument_id -> current ticker
    healed = asyncio.run(strat._reresolve_unresolved_symbols(broker))

    assert healed == 1
    r = _row(url, "issc2")
    assert r["symbol"] == "IA"                             # ledger symbol auto-rewritten
    assert r["name"] == "IA"                               # display name follows
    assert r["unresolved"] == 0                            # flag cleared
    assert logger.kinds().count("pead_symbol_reresolved") == 1
    ev = [p for a, k, p in logger.events if k == "pead_symbol_reresolved"][0]
    assert ev["old_symbol"] == "ISSC" and ev["new_symbol"] == "IA"
