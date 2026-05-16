"""One-shot prod patcher: add equity-snapshot writers for kalshi_weather +
kalshi_crypto, update stale Phase-K2.4 comment block in main.py.

Idempotent: re-running detects already-patched state via marker substrings.
"""
from __future__ import annotations

import pathlib
import sys


MAIN_PY = pathlib.Path("/home/azureuser/trading_corp/trading_corp/main.py")

OLD_COMMENT = """        # --- Kalshi round-trip resolver + equity snapshot writers (Phase K2.4) ---
        # Closes the same data gaps for the two Kalshi divisions
        # (kalshi_arbitrage and kalshi_llm_arbitrage). One resolver loop scans
        # would_have_placed rows from ALL THREE Kalshi strategies (tail-price,
        # temporal-bucket, llm) and writes to the shared kalshi_round_trips
        # table. Two equity-snapshot loops — one per division — both backed by
        # the same KalshiBroker (the two divisions share a funded account)."""

NEW_COMMENT = """        # --- Kalshi round-trip resolver + equity snapshot writers (Phase K2.4) ---
        # Closes the same data gaps across the Kalshi divisions
        # (kalshi_arbitrage, kalshi_llm_arbitrage, kalshi_weather, kalshi_crypto;
        # kalshi_copy_trading uses the same resolver via paired-exits). One
        # resolver loop scans would_have_placed rows from ALL strategies in
        # `kalshi_resolver._KALSHI_ACTORS` and writes to the shared
        # kalshi_round_trips table. Per-division equity-snapshot loops record
        # paper equity over time. kalshi_arbitrage + kalshi_llm_arbitrage
        # share one funded KalshiBroker; kalshi_weather + kalshi_crypto have
        # their own per-division PaperBrokers (paper_capital=$500 each)."""

OLD_INSERT = """            kalshi_equity_task_llm = None

        # --- Paper-trade replay (Phase C of would_have_placed enrichment) ---"""

NEW_INSERT = """            kalshi_equity_task_llm = None

        kalshi_broker_for_weather = data_exec.brokers.get(
            kalshi_weather_agent.division
        )
        if kalshi_broker_for_weather is not None:
            kalshi_equity_task_weather = start_kalshi_equity_snapshot_loop(
                secrets.db_url,
                kalshi_weather_agent.division,
                kalshi_broker_for_weather,
                interval_sec=300,
            )
        else:
            log.warning(
                "Kalshi equity-snapshot (kalshi_weather) not started: "
                "no broker registered for division=%s",
                kalshi_weather_agent.division,
            )
            kalshi_equity_task_weather = None

        kalshi_broker_for_crypto = data_exec.brokers.get(
            kalshi_crypto_agent.division
        )
        if kalshi_broker_for_crypto is not None:
            kalshi_equity_task_crypto = start_kalshi_equity_snapshot_loop(
                secrets.db_url,
                kalshi_crypto_agent.division,
                kalshi_broker_for_crypto,
                interval_sec=300,
            )
        else:
            log.warning(
                "Kalshi equity-snapshot (kalshi_crypto) not started: "
                "no broker registered for division=%s",
                kalshi_crypto_agent.division,
            )
            kalshi_equity_task_crypto = None

        # --- Paper-trade replay (Phase C of would_have_placed enrichment) ---"""


def main() -> int:
    src = MAIN_PY.read_text()
    if "kalshi_equity_task_weather" in src and "kalshi_equity_task_crypto" in src:
        print("ALREADY PATCHED — weather + crypto equity writers present, skipping.")
        return 0
    if OLD_COMMENT not in src:
        print("ERROR: comment anchor not found in main.py", file=sys.stderr)
        return 2
    if OLD_INSERT not in src:
        print("ERROR: insert anchor not found in main.py", file=sys.stderr)
        return 2
    src = src.replace(OLD_COMMENT, NEW_COMMENT, 1)
    src = src.replace(OLD_INSERT, NEW_INSERT, 1)
    MAIN_PY.write_text(src)
    print("PATCHED OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
