"""Standalone bootstrap of the web app.

Runs the dashboard against either a stub data layer or (with --real) the
actual broker stack. Used for manual testing without firing up the full
LangGraph / Telegram process.

  python -m trading_corp.web._smoketest             # stub deps
  python -m trading_corp.web._smoketest --real      # real brokers (login!)
"""
from __future__ import annotations

import argparse
import asyncio
import logging

import uvicorn

from trading_corp.web.app import WebDeps, create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


class _StubDataExec:
    brokers: dict = {}


class _StubBroker:
    name = "stub"
    paper = True
    _connected = True

    async def snapshot(self):
        from types import SimpleNamespace
        return SimpleNamespace(account="stub", equity=12345.0, buying_power=0.0, cash=0.0, positions=[])


def _build_stub_deps() -> WebDeps:
    return WebDeps(
        db_url="sqlite:///data/trading_corp.db",
        db_path="data/trading_corp.db",
        mode="PAPER",
        logger_agent=None,
        data_exec=_StubDataExec(),
        trend_agent=None,
        portfolio=None,
        pmcc_agent=None,
        fidelity_agent=None,
        paper_broker=_StubBroker(),
        secrets=None,
    )


async def _build_real_deps() -> WebDeps:
    """Build real WebDeps by spinning up DataExecAgent and registering all
    8 division brokers (in paper mode). Used to preview the dashboard with
    actual account snapshots without running the full graph + Telegram bot.
    """
    from trading_corp.agents.data_exec import DataExecAgent
    from trading_corp.agents.logger import LoggerAgent
    from trading_corp.agents.portfolio import PortfolioAgent
    from trading_corp.agents.trend_regime import TrendAgent
    from trading_corp.agents.divisions.pmcc_robinhood import PMCCAgent
    from trading_corp.agents.divisions.fidelity_options import FidelityOptionsAgent
    from trading_corp.brokers.paper import PaperBroker
    from trading_corp.persistence import db
    from trading_corp.utils.divisions import load_divisions
    from trading_corp.utils.secrets import load_secrets
    from trading_corp.main import _build_broker_for_division

    secrets = load_secrets()
    db_path = db.init_db(secrets.db_url)
    logger_agent = LoggerAgent(secrets.db_url)
    trend_agent = TrendAgent()
    data_exec = DataExecAgent(logger_agent)
    portfolio = PortfolioAgent(data_exec)
    pmcc_agent = PMCCAgent()
    fidelity_agent = FidelityOptionsAgent()

    paper_broker = PaperBroker(account="paper-default", starting_equity=100_000.0)
    data_exec.register_broker("default", paper_broker)

    for d in load_divisions():
        if not d.enabled:
            continue
        broker = _build_broker_for_division(d, secrets, "PAPER", [])
        if broker is not None:
            data_exec.register_broker(d.slug, broker)

    await data_exec.connect_all()

    return WebDeps(
        db_url=secrets.db_url,
        db_path=str(db_path),
        mode="PAPER",
        logger_agent=logger_agent,
        data_exec=data_exec,
        trend_agent=trend_agent,
        portfolio=portfolio,
        pmcc_agent=pmcc_agent,
        fidelity_agent=fidelity_agent,
        paper_broker=paper_broker,
        secrets=secrets,
    )


async def _async_main(real: bool) -> None:
    """Build deps and run uvicorn in the same event loop.

    Important: real-broker mode opens Playwright (Fidelity) and creates
    asyncio Locks. Both are bound to the loop they're created in. If we
    use a separate asyncio.run() to build deps, that loop closes before
    uvicorn opens its own — and every later broker call fails with
    "loop is closed" or "different event loop". So everything happens
    inside one Server.serve() lifecycle.
    """
    if real:
        deps = await _build_real_deps()
    else:
        deps = _build_stub_deps()

    app = create_app(deps)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=8000,
        log_level="info", access_log=False,
        loop="asyncio", lifespan="on",
    )
    server = uvicorn.Server(config)
    await server.serve()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--real", action="store_true",
                   help="Use real broker stack (logs into Robinhood/Fidelity)")
    args = p.parse_args()
    asyncio.run(_async_main(args.real))


if __name__ == "__main__":
    main()
