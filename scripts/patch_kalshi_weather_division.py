"""Surgical multi-file patcher: ship Kalshi Weather Arbitrage division.

What this does:
  1. config/divisions.yaml         — add kalshi_weather division (after kalshi_llm_arbitrage)
  2. config/strategies.yaml        — add kalshi_weather_arb block;
                                     remove Climate/Weather from kalshi_llm_arbitrage categories
  3. trading_corp/main.py          — wire agent + scheduled task; add loop body
  4. trading_corp/agents/kalshi_resolver.py — add actor to allowlist + maps

Prerequisites (must already be on prod, scp'd before running this):
  - trading_corp/data/weather_forecast.py
  - trading_corp/agents/strategies/_weather_math.py
  - trading_corp/agents/strategies/kalshi_weather_arb.py

Idempotent: re-running detects already-patched files and exits clean.
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

BASE = Path("/home/azureuser/trading_corp")
TAG = f"pre-weather-division-{time.strftime('%Y%m%d-%H%M', time.gmtime())}"


def _backup(p: Path) -> None:
    bak = p.with_suffix(p.suffix + f".{TAG}")
    shutil.copy(p, bak)
    print(f"  backup: {bak.name}")


def _assert_anchor(src: str, anchor: str, fname: str, n: int) -> None:
    if anchor not in src:
        sys.exit(f"FAIL: anchor #{n} not found in {fname}")


def patch_divisions_yaml() -> None:
    p = BASE / "config/divisions.yaml"
    src = p.read_text()
    if "slug: kalshi_weather\n" in src:
        print(f"  {p.name}: already patched (skipping)")
        return
    _backup(p)

    old = "  - slug: kalshi_copy_trading\n    name: Kalshi Copy Trading"
    new = (
        "  # Phase Weather (2026-05-14): Kalshi Weather Arbitrage. Forecast-driven\n"
        "  # — pulls NWS hourly forecasts and computes deterministic P(YES) vs\n"
        "  # threshold. Replaces the generic LLM probability call on Climate/Weather\n"
        "  # markets (kalshi_llm_arbitrage now skips that category).\n"
        "  - slug: kalshi_weather\n"
        "    name: Kalshi Weather\n"
        "    broker: paper    # placeholder for equity tracking; lazy-resolves real KalshiBroker\n"
        "    account_filter: main\n"
        "    intent: aggressive\n"
        "    benchmark: SPY\n"
        "    target_annual_return: 0.50\n"
        "    standby: false\n"
        "    enabled: true\n"
        "\n"
        "  - slug: kalshi_copy_trading\n"
        "    name: Kalshi Copy Trading"
    )
    _assert_anchor(src, old, p.name, 1)
    src = src.replace(old, new, 1)
    p.write_text(src)
    print(f"  PATCHED: {p.name}")


def patch_strategies_yaml() -> None:
    p = BASE / "config/strategies.yaml"
    src = p.read_text()
    if "kalshi_weather_arb:" in src:
        print(f"  {p.name}: kalshi_weather_arb already present (skipping yaml patch)")
        return
    _backup(p)

    # 1. Remove Climate/Weather from kalshi_llm_arbitrage discovery categories.
    #    Surgical: replace the exact line.
    old_cat = "      - Climate and Weather\n"
    if old_cat in src:
        src = src.replace(old_cat, "", 1)
        print("    removed 'Climate and Weather' from kalshi_llm_arbitrage categories")
    else:
        print("    NOTE: 'Climate and Weather' line not in kalshi_llm_arbitrage block "
              "(may have been removed manually or via prior patch)")

    # 2. Add kalshi_weather_arb block. Insert before the kalshi_copy_trader block.
    block = (
        "\n# ── Kalshi Weather Arbitrage (2026-05-14) ──\n"
        "# Forecast-driven strategy on Climate/Weather markets. Pulls NWS hourly\n"
        "# forecast for the market's lat/lon + target time; computes deterministic\n"
        "# P(YES) via Gaussian probability vs threshold; emits ProposedOrder when\n"
        "# divergence ≥ min_divergence_pct. No LLM in path (pure math).\n"
        "kalshi_weather_arb:\n"
        "  enabled: true\n"
        "  auto_execute: false               # paper-mode until validation gate\n"
        "  division: kalshi_weather\n"
        "  poll_interval_sec: 300            # 5 min — weather markets don't churn\n"
        "  discovery:\n"
        "    max_series_per_category: 30\n"
        "    max_markets_per_series: 50\n"
        "    cache_ttl_sec: 600              # 10 min cache on Kalshi list_markets\n"
        "  k_markets_per_cycle: 30           # candidates evaluated per cycle\n"
        "  market_cooldown_hours: 4          # don't re-emit same ticker within 4h\n"
        "  min_divergence_pct: 10.0          # |P(YES) - implied| × 100 ≥ this → fire\n"
        "  max_horizon_hours: 72             # NWS forecast precision degrades past 72h\n"
        "  sizing:\n"
        "    mode: fixed_usd\n"
        "    fixed_amount: 1.0               # $1 per shakedown trade\n"
        "\n"
    )
    # Insert before kalshi_copy_trader block.
    anchor = "kalshi_copy_trader:"
    if anchor in src:
        src = src.replace(anchor, block + anchor, 1)
    else:
        # Fallback: append to end of file.
        src += "\n" + block
        print("    NOTE: kalshi_copy_trader anchor not found; appended block to EOF")

    p.write_text(src)
    print(f"  PATCHED: {p.name}")


def patch_main_py() -> None:
    p = BASE / "trading_corp/main.py"
    src = p.read_text()
    if "_scheduled_kalshi_weather_arb_loop" in src:
        print(f"  {p.name}: already patched (skipping)")
        return
    _backup(p)

    # 1. Inject agent setup after the kalshi_llm_agent task creation.
    setup_anchor = (
        "        kalshi_llm_agent = KalshiLLMArbitrageAgent(db_url=secrets.db_url)\n"
        "        kalshi_llm_task = asyncio.create_task(\n"
        "            _scheduled_kalshi_llm_arb_loop(\n"
        "                kalshi_llm_agent,\n"
        "                channel=channel,\n"
        "                logger_agent=logger_agent,\n"
        "                data_exec=data_exec,\n"
        "                risk_agent=risk_agent,\n"
        "                db_url=secrets.db_url,\n"
        "            )\n"
        "        )\n"
    )
    setup_new = setup_anchor + (
        "\n"
        "        # --- Kalshi Weather Arbitrage (2026-05-14) ---\n"
        "        # Forecast-driven Climate/Weather strategy. Replaces the generic\n"
        "        # LLM probability call for these markets — uses NWS hourly\n"
        "        # forecast + Gaussian probability math, no LLM in path.\n"
        "        from trading_corp.agents.strategies.kalshi_weather_arb import (\n"
        "            KalshiWeatherArbAgent,\n"
        "        )\n"
        "        kalshi_weather_agent = KalshiWeatherArbAgent(db_url=secrets.db_url)\n"
        "        kalshi_weather_task = asyncio.create_task(\n"
        "            _scheduled_kalshi_weather_arb_loop(\n"
        "                kalshi_weather_agent,\n"
        "                channel=channel,\n"
        "                logger_agent=logger_agent,\n"
        "                data_exec=data_exec,\n"
        "                risk_agent=risk_agent,\n"
        "                db_url=secrets.db_url,\n"
        "            )\n"
        "        )\n"
    )
    _assert_anchor(src, setup_anchor, p.name, 1)
    src = src.replace(setup_anchor, setup_new, 1)

    # 2. Append the loop function. Find a stable insertion point — just
    # after `_scheduled_kalshi_llm_arb_loop`'s last line (cancelled-error
    # handler). Cleaner: append before the final `if __name__ == "__main__":`
    # anchor at EOF.
    eof_anchor = 'if __name__ == "__main__":'
    loop_body = '''

async def _scheduled_kalshi_weather_arb_loop(
    agent,
    *,
    channel,
    logger_agent,
    data_exec,
    risk_agent,
    db_url: str,
) -> None:
    """Kalshi Weather Arbitrage scanner loop.

    Pulls Climate/Weather markets, fetches NWS forecasts, emits orders
    when forecast diverges from implied. No LLM in path — pure math.

    Mirror of `_scheduled_kalshi_llm_arb_loop` but uses a forecast-based
    evaluator instead of the LLM. Risk gate identical (single chokepoint).
    """
    from trading_corp.persistence.models import AccountState, StrategyState

    log.info(
        "Kalshi Weather Arbitrage scanner online (enabled=%s, auto_execute=%s)",
        agent.enabled, agent.auto_execute,
    )
    # Lazy-resolve a real KalshiBroker for market discovery.
    while True:
        try:
            poll_sec = float(agent._strat_cfg.get("poll_interval_sec", 300))
            await asyncio.sleep(max(15.0, poll_sec))

            if not agent.enabled:
                continue

            kalshi_broker = None
            for div_name, br in data_exec.brokers.items():
                if br.__class__.__name__ == "KalshiBroker" and getattr(br, "_client", None):
                    kalshi_broker = br
                    break
            if kalshi_broker is None:
                log.debug("Kalshi Weather: no live KalshiBroker available; skipping")
                continue

            try:
                orders = await agent.run_scan_cycle(
                    kalshi_broker, logger_agent=logger_agent,
                )
            except Exception as e:
                log.exception("Kalshi Weather: run_scan_cycle failed: %s", e)
                continue

            if not orders:
                continue

            # Equity from the agent's own paper-broker division.
            div_broker = data_exec.brokers.get(agent.division)
            account_equity = 0.0
            if div_broker is not None:
                try:
                    snap = await div_broker.snapshot()
                    account_equity = float(getattr(snap, "equity", 0.0) or 0.0)
                except Exception as e:
                    log.warning("Kalshi Weather snapshot failed: %s; assuming $0", e)

            account = AccountState(
                account=agent.division, equity=account_equity,
                peak_equity=account_equity, halted=False,
            )
            strategy_state = StrategyState(strategy=agent.name, halted=False)

            log.info("Kalshi Weather: %d ProposedOrder(s) emitted", len(orders))
            for order in orders:
                logger_agent.log_proposed_order(order)
                ext = order.extra or {}
                base_payload = {
                    "strategy": agent.name,
                    "division": agent.division,
                    "order_id": order.id,
                    "side": order.side,
                    "qty": order.qty,
                    "limit_price": order.limit_price,
                    "rationale": order.rationale,
                    "ticker": ext.get("ticker"),
                    "event_ticker": ext.get("event_ticker"),
                    "event_title": ext.get("event_title"),
                    "outcome": ext.get("outcome"),
                    "category": ext.get("category"),
                    "divergence_pct": ext.get("divergence_pct"),
                }

                verdict = risk_agent.evaluate(
                    order, account, strategy_state, db_url=db_url,
                )
                if verdict.verdict == "reject":
                    logger_agent.log_event(
                        agent.name, "kalshi_weather_order_rejected_by_risk",
                        {**base_payload, "risk_reason": verdict.reason},
                    )
                    log.info("Kalshi Weather: risk REJECT %s — %s",
                             order.symbol, verdict.reason)
                    continue
                if verdict.verdict == "resize" and verdict.new_qty is not None:
                    log.info("Kalshi Weather: risk RESIZE qty %.4f -> %.4f (%s)",
                             order.qty, verdict.new_qty, verdict.reason)
                    order.qty = float(verdict.new_qty)

                logger_agent.log_event(
                    agent.name, "would_have_placed",
                    {
                        **base_payload,
                        "qty": order.qty,
                        "implied_prob_at_entry": ext.get("implied_prob_at_entry"),
                        "forecast_temp_f": ext.get("forecast_temp_f"),
                        "forecast_sigma_f": ext.get("forecast_sigma_f"),
                        "sigma_used_f": ext.get("sigma_used_f"),
                        "threshold_f": ext.get("threshold_f"),
                        "direction": ext.get("direction"),
                        "horizon_hours": ext.get("horizon_hours"),
                        "delta_f": ext.get("delta_f"),
                        "prob_yes": ext.get("prob_yes"),
                        "expires_at": ext.get("expires_at"),
                        "title": ext.get("title"),
                        "risk_verdict": verdict.verdict,
                        "risk_reason": verdict.reason,
                    },
                )
                try:
                    div_pct = float(ext.get("divergence_pct") or 0)
                    await channel.push(
                        f"☀️ Kalshi Weather {order.side.upper()} {order.symbol} "
                        f"(forecast {ext.get('forecast_temp_f','?')}°F vs "
                        f"threshold {ext.get('threshold_f','?')}°F, "
                        f"edge {div_pct:.1f}%) — logged to activity rail."
                    )
                except Exception as e:
                    log.warning("Kalshi Weather channel push failed: %s", e)

        except asyncio.CancelledError:
            log.info("Kalshi Weather Arbitrage scanner cancelled.")
            return
        except Exception as e:
            log.exception("Kalshi Weather loop iteration failed: %s", e)
            await asyncio.sleep(5.0)


'''
    _assert_anchor(src, eof_anchor, p.name, 2)
    src = src.replace(eof_anchor, loop_body + eof_anchor, 1)
    p.write_text(src)
    print(f"  PATCHED: {p.name}")


def patch_kalshi_resolver() -> None:
    p = BASE / "trading_corp/agents/kalshi_resolver.py"
    src = p.read_text()
    if "kalshi_weather_arb" in src:
        print(f"  {p.name}: already patched (skipping)")
        return
    _backup(p)

    # Add actor to _KALSHI_ACTORS tuple.
    old1 = (
        '_KALSHI_ACTORS = (\n'
        '    "kalshi_tail_price_arb",\n'
        '    "kalshi_temporal_bucket_arb",\n'
        '    "kalshi_llm_arbitrage",\n'
        '    "kalshi_copy_trader",\n'
        ')'
    )
    new1 = (
        '_KALSHI_ACTORS = (\n'
        '    "kalshi_tail_price_arb",\n'
        '    "kalshi_temporal_bucket_arb",\n'
        '    "kalshi_llm_arbitrage",\n'
        '    "kalshi_copy_trader",\n'
        '    "kalshi_weather_arb",\n'
        ')'
    )
    _assert_anchor(src, old1, p.name, 1)
    src = src.replace(old1, new1, 1)

    # Add to _KALSHI_DIVISIONS tuple.
    old2 = '_KALSHI_DIVISIONS = ("kalshi_arbitrage", "kalshi_llm_arbitrage", "kalshi_copy_trading")'
    new2 = '_KALSHI_DIVISIONS = ("kalshi_arbitrage", "kalshi_llm_arbitrage", "kalshi_copy_trading", "kalshi_weather")'
    _assert_anchor(src, old2, p.name, 2)
    src = src.replace(old2, new2, 1)

    # Add to _ACTOR_TO_DIVISION.
    old3 = (
        '_ACTOR_TO_DIVISION = {\n'
        '    "kalshi_tail_price_arb": "kalshi_arbitrage",\n'
        '    "kalshi_temporal_bucket_arb": "kalshi_arbitrage",\n'
        '    "kalshi_llm_arbitrage": "kalshi_llm_arbitrage",\n'
        '    "kalshi_copy_trader": "kalshi_copy_trading",\n'
        '}'
    )
    new3 = (
        '_ACTOR_TO_DIVISION = {\n'
        '    "kalshi_tail_price_arb": "kalshi_arbitrage",\n'
        '    "kalshi_temporal_bucket_arb": "kalshi_arbitrage",\n'
        '    "kalshi_llm_arbitrage": "kalshi_llm_arbitrage",\n'
        '    "kalshi_copy_trader": "kalshi_copy_trading",\n'
        '    "kalshi_weather_arb": "kalshi_weather",\n'
        '}'
    )
    _assert_anchor(src, old3, p.name, 3)
    src = src.replace(old3, new3, 1)

    # Add default arb_type for the new actor.
    old4 = (
        '_ACTOR_TO_ARB_TYPE_DEFAULT = {\n'
        '    "kalshi_tail_price_arb": "tail",\n'
        '    "kalshi_llm_arbitrage": "llm_divergence",\n'
        '    "kalshi_copy_trader": "copy_trade",'
    )
    new4 = (
        '_ACTOR_TO_ARB_TYPE_DEFAULT = {\n'
        '    "kalshi_tail_price_arb": "tail",\n'
        '    "kalshi_llm_arbitrage": "llm_divergence",\n'
        '    "kalshi_copy_trader": "copy_trade",\n'
        '    "kalshi_weather_arb": "weather_forecast",'
    )
    _assert_anchor(src, old4, p.name, 4)
    src = src.replace(old4, new4, 1)

    p.write_text(src)
    print(f"  PATCHED: {p.name}")


def main() -> None:
    print(f"TAG={TAG}")
    patch_divisions_yaml()
    patch_strategies_yaml()
    patch_main_py()
    patch_kalshi_resolver()
    print("DONE")


if __name__ == "__main__":
    main()
