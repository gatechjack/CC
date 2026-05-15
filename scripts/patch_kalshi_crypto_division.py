"""Surgical multi-file patcher: ship Kalshi Crypto Arbitrage division.

What this does:
  1. config/divisions.yaml         — add kalshi_crypto division (after kalshi_weather)
  2. config/strategies.yaml        — add kalshi_crypto_arb block;
                                     remove Crypto from kalshi_llm_arbitrage categories
                                     (tail/temporal kept — they do structural arb)
  3. trading_corp/main.py          — wire agent + scheduled task; add loop body
  4. trading_corp/agents/kalshi_resolver.py — add actor to allowlist + maps

Prerequisites (must already be on prod, scp'd before running this):
  - trading_corp/data/crypto_spot_provider.py
  - trading_corp/agents/strategies/kalshi_crypto_arb.py

Idempotent: re-running detects already-patched files and exits clean.
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

BASE = Path("/home/azureuser/trading_corp")
TAG = f"pre-crypto-division-{time.strftime('%Y%m%d-%H%M', time.gmtime())}"


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
    if "slug: kalshi_crypto\n" in src:
        print(f"  {p.name}: already patched (skipping)")
        return
    _backup(p)

    # Insert kalshi_crypto block after kalshi_weather (which we just shipped).
    old = (
        "  - slug: kalshi_weather\n"
        "    name: Kalshi Weather\n"
        "    broker: paper    # placeholder for equity tracking; lazy-resolves real KalshiBroker\n"
        "    account_filter: main\n"
        "    intent: aggressive\n"
        "    benchmark: SPY\n"
        "    target_annual_return: 0.50\n"
        "    standby: false\n"
        "    enabled: true\n"
    )
    new = old + (
        "\n"
        "  # Phase Crypto (2026-05-14): Kalshi Crypto Arbitrage. Live-spot-driven\n"
        "  # — pulls Coinbase spot for BTC/ETH/SOL/DOGE/XRP and computes deterministic\n"
        "  # P(YES) vs threshold using annualized realized vol. Replaces the generic\n"
        "  # LLM probability call on Crypto markets (kalshi_llm_arbitrage now skips\n"
        "  # that category for directional bets; structural tail/temporal arb still\n"
        "  # see crypto markets for their own patterns).\n"
        "  - slug: kalshi_crypto\n"
        "    name: Kalshi Crypto\n"
        "    broker: paper    # placeholder for equity; lazy-resolves real KalshiBroker + CoinbaseBroker\n"
        "    account_filter: main\n"
        "    intent: aggressive\n"
        "    benchmark: SPY\n"
        "    target_annual_return: 0.50\n"
        "    standby: false\n"
        "    enabled: true\n"
    )
    _assert_anchor(src, old, p.name, 1)
    src = src.replace(old, new, 1)
    p.write_text(src)
    print(f"  PATCHED: {p.name}")


def patch_strategies_yaml() -> None:
    p = BASE / "config/strategies.yaml"
    src = p.read_text()
    if "kalshi_crypto_arb:" in src:
        print(f"  {p.name}: kalshi_crypto_arb already present (skipping)")
        return
    _backup(p)

    # 1. Remove "Crypto" from kalshi_llm_arbitrage categories ONLY.
    #    (Tail/temporal still scan crypto for structural arb patterns.)
    # We need the exact context — find the kalshi_llm_arbitrage block's
    # categories list and remove just its "- Crypto" line.
    marker = "kalshi_llm_arbitrage:"
    block_start = src.find(marker)
    if block_start < 0:
        sys.exit("FAIL: kalshi_llm_arbitrage block not found")
    # Find the categories: line inside this block
    cats_start = src.find("    categories:", block_start)
    if cats_start < 0:
        sys.exit("FAIL: kalshi_llm_arbitrage.discovery.categories not found")
    cats_end = src.find("    max_series_per_category:", cats_start)
    if cats_end < 0:
        sys.exit("FAIL: kalshi_llm_arbitrage discovery block malformed")
    cats_section = src[cats_start:cats_end]
    crypto_line = "      - Crypto\n"
    if crypto_line in cats_section:
        new_cats_section = cats_section.replace(crypto_line, "", 1)
        src = src[:cats_start] + new_cats_section + src[cats_end:]
        print("    removed 'Crypto' from kalshi_llm_arbitrage categories")
    else:
        print("    NOTE: 'Crypto' line not in kalshi_llm_arbitrage categories")

    # 2. Add kalshi_crypto_arb block. Insert right after kalshi_weather_arb.
    block = (
        "\n# ── Kalshi Crypto Arbitrage (2026-05-14) ──\n"
        "# Live-spot-driven strategy on Crypto-category markets. Pulls Coinbase\n"
        "# spot for BTC/ETH/SOL/DOGE/XRP, computes P(YES) vs threshold via\n"
        "# Gaussian probability with annualized vol. No LLM in path.\n"
        "kalshi_crypto_arb:\n"
        "  enabled: true\n"
        "  auto_execute: false               # paper-mode until validation gate\n"
        "  division: kalshi_crypto\n"
        "  poll_interval_sec: 60             # crypto markets churn fast — short-window\n"
        "  discovery:\n"
        "    max_series_per_category: 30\n"
        "    max_markets_per_series: 50\n"
        "    cache_ttl_sec: 300              # 5 min cache on kalshi list_markets\n"
        "  k_markets_per_cycle: 30           # candidates evaluated per cycle\n"
        "  market_cooldown_hours: 1          # short cooldown — fast-churning markets\n"
        "  min_divergence_pct: 10.0          # |P(YES) - implied| × 100 ≥ this → fire\n"
        "  max_horizon_hours: 168            # 7 days — vol model gets noisy past this\n"
        "  sizing:\n"
        "    mode: fixed_usd\n"
        "    fixed_amount: 1.0               # $1 per shakedown trade\n"
        "\n"
    )
    # Insert before the kalshi_weather_arb -> next strategy block. Use a
    # stable anchor at the end of weather_arb block: 'kalshi_copy_trader:'.
    anchor = "kalshi_copy_trader:"
    if anchor in src:
        src = src.replace(anchor, block + anchor, 1)
    else:
        src += "\n" + block
        print("    NOTE: kalshi_copy_trader anchor not found; appended block to EOF")

    p.write_text(src)
    print(f"  PATCHED: {p.name}")


def patch_main_py() -> None:
    p = BASE / "trading_corp/main.py"
    src = p.read_text()
    if "_scheduled_kalshi_crypto_arb_loop" in src:
        print(f"  {p.name}: already patched (skipping)")
        return
    _backup(p)

    # 1. Inject agent setup right after kalshi_weather_agent setup.
    setup_anchor = (
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
    setup_new = setup_anchor + (
        "\n"
        "        # --- Kalshi Crypto Arbitrage (2026-05-14) ---\n"
        "        # Live-spot-driven Crypto strategy. Replaces the generic LLM call\n"
        "        # for these markets — uses Coinbase spot + Gaussian probability,\n"
        "        # no LLM in path.\n"
        "        from trading_corp.agents.strategies.kalshi_crypto_arb import (\n"
        "            KalshiCryptoArbAgent,\n"
        "        )\n"
        "        kalshi_crypto_agent = KalshiCryptoArbAgent(db_url=secrets.db_url)\n"
        "        kalshi_crypto_task = asyncio.create_task(\n"
        "            _scheduled_kalshi_crypto_arb_loop(\n"
        "                kalshi_crypto_agent,\n"
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

    # 2. Append the loop function before `if __name__ == "__main__":`
    eof_anchor = 'if __name__ == "__main__":'
    loop_body = '''

async def _scheduled_kalshi_crypto_arb_loop(
    agent,
    *,
    channel,
    logger_agent,
    data_exec,
    risk_agent,
    db_url: str,
) -> None:
    """Kalshi Crypto Arbitrage scanner loop.

    Pulls Crypto-category markets, fetches Coinbase spot for the asset,
    computes P(YES) vs threshold via Gaussian vol. No LLM in path.
    """
    from trading_corp.persistence.models import AccountState, StrategyState

    log.info(
        "Kalshi Crypto Arbitrage scanner online (enabled=%s, auto_execute=%s)",
        agent.enabled, agent.auto_execute,
    )
    while True:
        try:
            poll_sec = float(agent._strat_cfg.get("poll_interval_sec", 60))
            await asyncio.sleep(max(15.0, poll_sec))

            if not agent.enabled:
                continue

            # Lazy-resolve real KalshiBroker + CoinbaseBroker from data_exec.
            kalshi_broker = None
            coinbase_broker = None
            for div_name, br in data_exec.brokers.items():
                cls = br.__class__.__name__
                if cls == "KalshiBroker" and getattr(br, "_client", None):
                    kalshi_broker = br
                elif cls == "CoinbaseBroker" and not kalshi_broker == coinbase_broker:
                    coinbase_broker = br
            if kalshi_broker is None or coinbase_broker is None:
                log.debug("Kalshi Crypto: missing broker (kalshi=%s coinbase=%s)",
                          bool(kalshi_broker), bool(coinbase_broker))
                continue

            try:
                orders = await agent.run_scan_cycle(
                    kalshi_broker, coinbase_broker,
                    logger_agent=logger_agent,
                )
            except Exception as e:
                log.exception("Kalshi Crypto: run_scan_cycle failed: %s", e)
                continue

            if not orders:
                continue

            div_broker = data_exec.brokers.get(agent.division)
            account_equity = 0.0
            if div_broker is not None:
                try:
                    snap = await div_broker.snapshot()
                    account_equity = float(getattr(snap, "equity", 0.0) or 0.0)
                except Exception as e:
                    log.warning("Kalshi Crypto snapshot failed: %s; assuming $0", e)

            account = AccountState(
                account=agent.division, equity=account_equity,
                peak_equity=account_equity, halted=False,
            )
            strategy_state = StrategyState(strategy=agent.name, halted=False)

            log.info("Kalshi Crypto: %d ProposedOrder(s) emitted", len(orders))
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
                        agent.name, "kalshi_crypto_order_rejected_by_risk",
                        {**base_payload, "risk_reason": verdict.reason},
                    )
                    log.info("Kalshi Crypto: risk REJECT %s — %s",
                             order.symbol, verdict.reason)
                    continue
                if verdict.verdict == "resize" and verdict.new_qty is not None:
                    log.info("Kalshi Crypto: risk RESIZE qty %.4f -> %.4f (%s)",
                             order.qty, verdict.new_qty, verdict.reason)
                    order.qty = float(verdict.new_qty)

                logger_agent.log_event(
                    agent.name, "would_have_placed",
                    {
                        **base_payload,
                        "qty": order.qty,
                        "implied_prob_at_entry": ext.get("implied_prob_at_entry"),
                        "asset": ext.get("asset"),
                        "spot_price": ext.get("spot_price"),
                        "spot_sigma_usd": ext.get("spot_sigma_usd"),
                        "sigma_used_usd": ext.get("sigma_used_usd"),
                        "annual_vol": ext.get("annual_vol"),
                        "threshold_usd": ext.get("threshold_usd"),
                        "direction": ext.get("direction"),
                        "horizon_hours": ext.get("horizon_hours"),
                        "delta_usd": ext.get("delta_usd"),
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
                        f"🪙 Kalshi Crypto {order.side.upper()} {order.symbol} "
                        f"(spot ${ext.get('spot_price','?')} vs threshold "
                        f"${ext.get('threshold_usd','?')}, edge {div_pct:.1f}%) "
                        f"— logged to activity rail."
                    )
                except Exception as e:
                    log.warning("Kalshi Crypto channel push failed: %s", e)

        except asyncio.CancelledError:
            log.info("Kalshi Crypto Arbitrage scanner cancelled.")
            return
        except Exception as e:
            log.exception("Kalshi Crypto loop iteration failed: %s", e)
            await asyncio.sleep(5.0)


'''
    _assert_anchor(src, eof_anchor, p.name, 2)
    src = src.replace(eof_anchor, loop_body + eof_anchor, 1)
    p.write_text(src)
    print(f"  PATCHED: {p.name}")


def patch_kalshi_resolver() -> None:
    p = BASE / "trading_corp/agents/kalshi_resolver.py"
    src = p.read_text()
    if "kalshi_crypto_arb" in src:
        print(f"  {p.name}: already patched (skipping)")
        return
    _backup(p)

    # Add actor to _KALSHI_ACTORS tuple. (Weather is already in the tuple
    # from the prior deploy; both new actors land next to it.)
    old1 = (
        '_KALSHI_ACTORS = (\n'
        '    "kalshi_tail_price_arb",\n'
        '    "kalshi_temporal_bucket_arb",\n'
        '    "kalshi_llm_arbitrage",\n'
        '    "kalshi_copy_trader",\n'
        '    "kalshi_weather_arb",\n'
        ')'
    )
    new1 = (
        '_KALSHI_ACTORS = (\n'
        '    "kalshi_tail_price_arb",\n'
        '    "kalshi_temporal_bucket_arb",\n'
        '    "kalshi_llm_arbitrage",\n'
        '    "kalshi_copy_trader",\n'
        '    "kalshi_weather_arb",\n'
        '    "kalshi_crypto_arb",\n'
        ')'
    )
    _assert_anchor(src, old1, p.name, 1)
    src = src.replace(old1, new1, 1)

    old2 = '_KALSHI_DIVISIONS = ("kalshi_arbitrage", "kalshi_llm_arbitrage", "kalshi_copy_trading", "kalshi_weather")'
    new2 = '_KALSHI_DIVISIONS = ("kalshi_arbitrage", "kalshi_llm_arbitrage", "kalshi_copy_trading", "kalshi_weather", "kalshi_crypto")'
    _assert_anchor(src, old2, p.name, 2)
    src = src.replace(old2, new2, 1)

    old3 = (
        '_ACTOR_TO_DIVISION = {\n'
        '    "kalshi_tail_price_arb": "kalshi_arbitrage",\n'
        '    "kalshi_temporal_bucket_arb": "kalshi_arbitrage",\n'
        '    "kalshi_llm_arbitrage": "kalshi_llm_arbitrage",\n'
        '    "kalshi_copy_trader": "kalshi_copy_trading",\n'
        '    "kalshi_weather_arb": "kalshi_weather",\n'
        '}'
    )
    new3 = (
        '_ACTOR_TO_DIVISION = {\n'
        '    "kalshi_tail_price_arb": "kalshi_arbitrage",\n'
        '    "kalshi_temporal_bucket_arb": "kalshi_arbitrage",\n'
        '    "kalshi_llm_arbitrage": "kalshi_llm_arbitrage",\n'
        '    "kalshi_copy_trader": "kalshi_copy_trading",\n'
        '    "kalshi_weather_arb": "kalshi_weather",\n'
        '    "kalshi_crypto_arb": "kalshi_crypto",\n'
        '}'
    )
    _assert_anchor(src, old3, p.name, 3)
    src = src.replace(old3, new3, 1)

    old4 = (
        '_ACTOR_TO_ARB_TYPE_DEFAULT = {\n'
        '    "kalshi_tail_price_arb": "tail",\n'
        '    "kalshi_llm_arbitrage": "llm_divergence",\n'
        '    "kalshi_copy_trader": "copy_trade",\n'
        '    "kalshi_weather_arb": "weather_forecast",'
    )
    new4 = (
        '_ACTOR_TO_ARB_TYPE_DEFAULT = {\n'
        '    "kalshi_tail_price_arb": "tail",\n'
        '    "kalshi_llm_arbitrage": "llm_divergence",\n'
        '    "kalshi_copy_trader": "copy_trade",\n'
        '    "kalshi_weather_arb": "weather_forecast",\n'
        '    "kalshi_crypto_arb": "crypto_spot",'
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
