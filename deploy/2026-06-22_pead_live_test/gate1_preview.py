"""PEAD STEP 3 — GATE 1 preview harness (READ-ONLY; PLACES NOTHING).

Runs the REAL branch entry engine (`PEADStrategy.scan`) against the live
Robinhood pickle session on the prod VM, to report the INTENDED book BEFORE any
order is placed. Faithful — it uses the exact branch selection + sizing +
`pead_pressures` primitives the armed live engine would. It is safe because:

  * `_place_or_paper` is monkeypatched to a no-op → `data_exec.place` is NEVER
    called (no real order, ever).
  * `_write_record` is monkeypatched to a no-op → the prod DB is never written.
  * the EarningsProvider cache + StrategyState read/write go to a throwaway temp
    DB, not the prod DB.
  * `auto_execute:false` in the committed strategies.yaml ALSO forces the paper
    path independently (belt-and-suspenders) — but we never reach placement.

It exercises the REAL branch RobinhoodBroker(account_filter="680725082") so the
hard-bind (3ad16a2) is proven read-only here too: the harness ABORTS if the
broker binds anything other than 680725082.

RUN ON PROD (operator-driven):
    cd <branch_checkout_root>           # a checkout of robinhood-pead-2026-06-20
    KEY_VAULT_URI=https://kv-tc-vtwbowt3wtkpy.vault.azure.net/ \
    PYTHONPATH=$PWD PYTHONIOENCODING=utf-8 \
    /home/azureuser/trading_corp/venv/bin/python \
        deploy/2026-06-22_pead_live_test/gate1_preview.py

The prod venv supplies the third-party deps; PYTHONPATH=$PWD makes `trading_corp`
resolve to the BRANCH code (hard-bind broker + PEAD modules). Nothing is
installed, no deployed file is touched, no service is restarted.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from types import SimpleNamespace

ACCOUNT = "680725082"


class _StubLogger:
    def __init__(self):
        self.events = []
        self.proposed = []

    def log_event(self, slug, kind, payload):
        self.events.append((slug, kind, payload))

    def log_proposed_order(self, order):
        self.proposed.append(order)


class _NoExec:
    """data_exec stand-in — MUST never be called in preview (place is patched out)."""

    async def place(self, order, division=None):  # pragma: no cover - safety tripwire
        raise AssertionError(
            "PREVIEW SAFETY VIOLATION: data_exec.place was called — abort. "
            "_place_or_paper should have been patched to a no-op."
        )


async def _amain() -> int:
    from trading_corp.agents.risk import RiskAgent
    from trading_corp.agents.strategies.pead_strategy import PEADStrategy
    from trading_corp.brokers.robinhood import RobinhoodBroker
    from trading_corp.data.earnings_provider import EarningsProvider
    from trading_corp.persistence.db import init_db
    from trading_corp.utils.secrets import load_secrets

    secrets = load_secrets()
    if not secrets.eodhd_api_key:
        print("ABORT: EODHD key not resolved (need KEY_VAULT_URI + Azure auth).")
        return 2

    # ── Real branch broker, READ-ONLY — proves the hard-bind to 680725082 ──
    broker = RobinhoodBroker(
        username=secrets.robinhood_username,
        password=secrets.robinhood_password,
        mfa_secret=secrets.robinhood_mfa_secret,
        account_filter=ACCOUNT,
    )
    await broker.connect()
    bound = getattr(broker, "_account_number", "")
    print(f"broker bound account = {bound!r}")
    if bound != ACCOUNT:
        print(f"ABORT: broker bound {bound!r}, expected {ACCOUNT!r} — hard-bind FAILED.")
        return 3
    snap = await broker.snapshot()
    equity = float(getattr(snap, "equity", 0.0) or 0.0)
    cash = float(getattr(snap, "cash", 0.0) or 0.0)
    print(f"live equity = ${equity:,.2f}  (cash ${cash:,.2f})  account {ACCOUNT}")

    # ── Throwaway temp DB (empty → no held positions; EODHD cache lives here) ──
    tmpdb = os.path.join(tempfile.gettempdir(), "pead_gate1_preview.db")
    if os.path.exists(tmpdb):
        os.remove(tmpdb)
    db_url = f"sqlite:///{tmpdb}"
    init_db(db_url)
    provider = EarningsProvider(api_key=secrets.eodhd_api_key, db_url=db_url)

    strat = PEADStrategy(
        db_url=db_url,
        risk_agent=RiskAgent(narrator_enabled=False),
        data_exec=_NoExec(),
        logger_agent=_StubLogger(),
        earnings_provider=provider,
        execution_mode="live",          # reflect the ARMED state
    )

    # ── NEUTRALIZE placement + record write (preview only — no order, no write) ──
    async def _no_place(order):
        order.execution_mode = "live"   # show the live path it WOULD take
        return True

    strat._place_or_paper = _no_place           # type: ignore[assignment]
    strat._write_record = lambda order, **k: None  # type: ignore[assignment]

    cfg = strat._cfg()
    print("\n--- config (as the engine reads it) ---")
    print(f"  position_pct            = {cfg.get('position_pct')}")
    print(f"  max_concurrent_positions= {cfg.get('max_concurrent_positions')}")
    print(f"  entry window (td)       = {cfg.get('entry_delay_days')}..{cfg.get('entry_max_delay_days')}")
    print(f"  auto_execute            = {cfg.get('auto_execute')}  (false ⇒ paper path even if armed)")
    print(f"  universe size           = {len(strat._universe())}")
    print(f"  execution_mode resolved = live  (_is_live()={strat._is_live()} — needs auto_execute too)")

    print("\nScanning for the post-earnings wave (this walks the universe — may take a few min)...")
    orders = await strat.scan(broker)

    print(f"\n=== INTENDED BOOK ({len(orders)} order(s)) — target account {ACCOUNT} — NOTHING PLACED ===")
    if not orders:
        print("  (empty — no names announced in the 1-2 trading-day entry window today, "
              "or none cleared screen+SUE)")
    six = ("entry_atr_14", "post_earnings_swing_low", "pre_earnings_close",
           "earnings_gap_top", "next_earnings_date", "entry_sue")
    for o in orders:
        ex = o.extra or {}
        notional = float(o.qty) * float(ex.get("entry_reference_price") or 0.0)
        print(f"\n  {o.symbol}: BUY {int(o.qty)} sh  ~${notional:,.2f}  "
              f"(ref ${ex.get('entry_reference_price')})  SUE={ex.get('entry_sue')}")
        print(f"    target_account = {ACCOUNT}   stop_price = {ex.get('stop_price')}")
        missing = [k for k in six if k not in ex]
        print(f"    6 extra_json keys present = {not missing}"
              + (f"  MISSING={missing}" if missing else ""))
        print("    " + json.dumps({k: ex.get(k) for k in six}, default=str))

    print("\n=== END PREVIEW — no orders placed, prod DB untouched ===")
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
