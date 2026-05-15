"""Surgical multi-file patcher: ship Kalshi Sports Scout (read-only).

What this does:
  1. config/strategies.yaml          — add kalshi_sports_scout block
  2. trading_corp/main.py            — wire agent + scheduled task; add loop body
  3. trading_corp/utils/secrets.py   — add ODDS_API_KEY to env_vars + Secrets

Prerequisites (must already be on prod, scp'd before running this):
  - trading_corp/data/odds_api_client.py
  - trading_corp/data/sports_team_mapping.py
  - trading_corp/agents/strategies/kalshi_sports_scout.py

NOT touching:
  - config/divisions.yaml          — scout doesn't trade; no division
  - kalshi_resolver.py             — no orders to resolve

Idempotent.
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

BASE = Path("/home/azureuser/trading_corp")
TAG = f"pre-sports-scout-{time.strftime('%Y%m%d-%H%M', time.gmtime())}"


def _backup(p: Path) -> None:
    bak = p.with_suffix(p.suffix + f".{TAG}")
    shutil.copy(p, bak)
    print(f"  backup: {bak.name}")


def _assert_anchor(src: str, anchor: str, fname: str, n: int) -> None:
    if anchor not in src:
        sys.exit(f"FAIL: anchor #{n} not found in {fname}")


def patch_strategies_yaml() -> None:
    p = BASE / "config/strategies.yaml"
    src = p.read_text()
    if "kalshi_sports_scout:" in src:
        print(f"  {p.name}: already patched (skipping)")
        return
    _backup(p)
    block = (
        "\n# ── Kalshi Sports Scout (2026-05-14) ──\n"
        "# Read-only observer. Pulls Kalshi Sports markets + bookmaker\n"
        "# implied probability from the-odds-api, logs divergence. NO\n"
        "# orders. 7-day observation pass to validate edge before deciding\n"
        "# whether to build a full trading division.\n"
        "kalshi_sports_scout:\n"
        "  enabled: true\n"
        "  poll_interval_sec: 900            # 15 min — quota-friendly\n"
        "  discovery:\n"
        "    max_series_per_category: 50\n"
        "    max_markets_per_series: 50\n"
        "    cache_ttl_sec: 900\n"
        "  leagues: [MLB, NBA, NHL, MLS, NFL]\n"
        "  divergence_log_threshold_pct: 1.0  # only log markets w/ |div| >= 1pp\n"
        "\n"
    )
    anchor = "kalshi_copy_trader:"
    if anchor in src:
        src = src.replace(anchor, block + anchor, 1)
    else:
        src += "\n" + block
    p.write_text(src)
    print(f"  PATCHED: {p.name}")


def patch_secrets_py() -> None:
    p = BASE / "trading_corp/utils/secrets.py"
    src = p.read_text()
    if "ODDS_API_KEY" in src:
        print(f"  {p.name}: already patched (skipping)")
        return
    _backup(p)

    # 1. Add ODDS_API_KEY to the redaction-keys tuple (around line 46).
    old1 = '    "KALSHI_API_KEY_ID",\n'
    if old1 in src and src.count(old1) >= 2:
        # Two occurrences expected (redaction-keys + expected-env-vars).
        # Replace BOTH so KV fetch and env-load both know about ODDS_API_KEY.
        src = src.replace(
            old1,
            '    "KALSHI_API_KEY_ID",\n    "ODDS_API_KEY",\n',
            1,
        )
        # Second occurrence (in expected_env_vars):
        src = src.replace(
            old1,
            '    "KALSHI_API_KEY_ID",\n    "ODDS_API_KEY",\n',
            1,
        )
    else:
        sys.exit("FAIL: ODDS_API_KEY anchor in secrets.py — KALSHI_API_KEY_ID not found twice")

    # 2. Add field to Secrets dataclass.
    old2 = "    kalshi_api_key_id: str | None"
    new2 = (
        "    kalshi_api_key_id: str | None\n"
        "    odds_api_key: str | None"
    )
    _assert_anchor(src, old2, p.name, 2)
    src = src.replace(old2, new2, 1)

    # 3. Populate field in factory call.
    old3 = "        kalshi_api_key_id=_env(\"KALSHI_API_KEY_ID\"),"
    new3 = (
        "        kalshi_api_key_id=_env(\"KALSHI_API_KEY_ID\"),\n"
        "        odds_api_key=_env(\"ODDS_API_KEY\"),"
    )
    _assert_anchor(src, old3, p.name, 3)
    src = src.replace(old3, new3, 1)

    p.write_text(src)
    print(f"  PATCHED: {p.name}")


def patch_main_py() -> None:
    p = BASE / "trading_corp/main.py"
    src = p.read_text()
    if "_scheduled_kalshi_sports_scout_loop" in src:
        print(f"  {p.name}: already patched (skipping)")
        return
    _backup(p)

    # 1. Inject agent setup right after kalshi_crypto_agent setup.
    setup_anchor = (
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
    setup_new = setup_anchor + (
        "\n"
        "        # --- Kalshi Sports Scout (2026-05-14, read-only observer) ---\n"
        "        # No order emission. Logs bookmaker vs Kalshi divergence to\n"
        "        # `kalshi_sports_observed` audit. 7-day pass to validate edge.\n"
        "        from trading_corp.agents.strategies.kalshi_sports_scout import (\n"
        "            KalshiSportsScoutAgent,\n"
        "        )\n"
        "        kalshi_sports_scout_agent = KalshiSportsScoutAgent(\n"
        "            odds_api_key=secrets.odds_api_key,\n"
        "            db_url=secrets.db_url,\n"
        "        )\n"
        "        kalshi_sports_scout_task = asyncio.create_task(\n"
        "            _scheduled_kalshi_sports_scout_loop(\n"
        "                kalshi_sports_scout_agent,\n"
        "                logger_agent=logger_agent,\n"
        "                data_exec=data_exec,\n"
        "            )\n"
        "        )\n"
    )
    _assert_anchor(src, setup_anchor, p.name, 1)
    src = src.replace(setup_anchor, setup_new, 1)

    # 2. Append the loop function before `if __name__ == "__main__":`
    eof_anchor = 'if __name__ == "__main__":'
    loop_body = '''

async def _scheduled_kalshi_sports_scout_loop(
    agent,
    *,
    logger_agent,
    data_exec,
) -> None:
    """Kalshi Sports Scout loop. NO order emission.

    Each cycle: discover Kalshi Sports markets → map to bookmaker games
    via team-code lookup → fetch the-odds-api lines → log divergence.
    The agent owns its OddsAPIClient (closed on cancellation).
    """
    log.info(
        "Kalshi Sports Scout online (enabled=%s, has_credentials=%s)",
        agent.enabled, agent.has_credentials,
    )
    try:
        while True:
            try:
                poll_sec = float(agent._strat_cfg.get("poll_interval_sec", 900))
                await asyncio.sleep(max(30.0, poll_sec))

                if not agent.enabled:
                    continue

                kalshi_broker = None
                for div_name, br in data_exec.brokers.items():
                    if br.__class__.__name__ == "KalshiBroker" and getattr(br, "_client", None):
                        kalshi_broker = br
                        break
                if kalshi_broker is None:
                    log.debug("Sports Scout: no live KalshiBroker available; skipping")
                    continue

                try:
                    await agent.run_scan_cycle(
                        kalshi_broker, logger_agent=logger_agent,
                    )
                except Exception as e:
                    log.exception("Sports Scout: run_scan_cycle failed: %s", e)
                    continue

            except asyncio.CancelledError:
                log.info("Kalshi Sports Scout cancelled.")
                return
            except Exception as e:
                log.exception("Sports Scout loop iteration failed: %s", e)
                await asyncio.sleep(5.0)
    finally:
        try:
            await agent.close()
        except Exception:
            pass


'''
    _assert_anchor(src, eof_anchor, p.name, 2)
    src = src.replace(eof_anchor, loop_body + eof_anchor, 1)
    p.write_text(src)
    print(f"  PATCHED: {p.name}")


def main() -> None:
    print(f"TAG={TAG}")
    patch_strategies_yaml()
    patch_secrets_py()
    patch_main_py()
    print("DONE")


if __name__ == "__main__":
    main()
