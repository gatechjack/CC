"""Surgical patch: surface per-market `title` to the LLM for kalshi_llm_arbitrage.

Root-cause fix for KXTEMPNYCH weather losses (-$6.11 across 15 trades):
LLM was given `event_title` ("New York City temperature on May 11, 2026
at 1pm EDT?") + delta-encoded `subtitle` ("-1° or below") and hallucinated
the threshold as "-1°C (30°F)". The MarketRecord already carries
`m.title` ("Will the temp in New York City be above 57.99° on May 11,
2026 at 1pm EDT?") — we just weren't passing it.

Idempotent.
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

BASE = Path("/home/azureuser/trading_corp")
TAG = f"pre-llm-mkttitle-{time.strftime('%Y%m%d-%H%M', time.gmtime())}"


def patch() -> None:
    p = BASE / "trading_corp/agents/strategies/kalshi_llm_arbitrage.py"
    src = p.read_text()
    if "market_title" in src and "Prefer per-market `title`" in src:
        print(f"  {p.name}: already patched (skipping)")
        return

    bak = p.with_suffix(p.suffix + f".{TAG}")
    shutil.copy(p, bak)
    print(f"  backup: {bak.name}")

    # ── Patch 1: survivor dict — add market_title ──
    old = (
        '                survivors.append({\n'
        '                    "ticker": m.ticker,\n'
        '                    "event_ticker": m.event_ticker,\n'
        '                    "event_title": event.title,\n'
        '                    "event_type": event.event_type.value,\n'
        '                    "category": event.category,\n'
        '                    "subtitle": m.subtitle,'
    )
    new = (
        '                survivors.append({\n'
        '                    "ticker": m.ticker,\n'
        '                    "event_ticker": m.event_ticker,\n'
        '                    "event_title": event.title,\n'
        '                    "event_type": event.event_type.value,\n'
        '                    "category": event.category,\n'
        '                    # Per-market `title` carries the EXPLICIT threshold for\n'
        '                    # binary-strike markets (e.g. "Will the temp in NYC be\n'
        '                    # above 57.99° on May 11, 2026 at 1pm EDT?"). Parent\n'
        '                    # event_title omits the threshold and `subtitle` is\n'
        '                    # delta-encoded ("-1° or below") which the LLM\n'
        '                    # systematically mis-interprets. Surfaces m.title\n'
        '                    # to fix the 15-trade KXTEMPNYCH -$6 loss pattern.\n'
        '                    "market_title": m.title,\n'
        '                    "subtitle": m.subtitle,'
    )
    if old not in src:
        sys.exit("FAIL: anchor 1 (survivor dict) not found")
    src = src.replace(old, new, 1)

    # ── Patch 2: _estimate_probability — prefer market_title ──
    old = (
        '        ticker = market.get("ticker") or "(no ticker)"\n'
        '        event_title = market.get("event_title") or "(no event title)"\n'
        '        subtitle = market.get("subtitle") or ""\n'
        '        category = market.get("category") or "other"\n'
        '        end_iso = market.get("expected_expiration_time") or "(no end date)"\n'
        '        implied = market.get("implied_prob_yes")\n'
        '\n'
        '        # Phrase as a YES/NO question. event_title is "What/Will/When/Who..."\n'
        '        # subtitle is the specific outcome (e.g. "Anthony Edwards: 2+",\n'
        '        # "Before July 2026", "Q1 2026"). Combine for the LLM context.\n'
        '        question = event_title\n'
        '        if subtitle:\n'
        '            question = f"{event_title} — outcome: {subtitle}"'
    )
    new = (
        '        ticker = market.get("ticker") or "(no ticker)"\n'
        '        event_title = market.get("event_title") or "(no event title)"\n'
        '        market_title = market.get("market_title") or ""\n'
        '        subtitle = market.get("subtitle") or ""\n'
        '        category = market.get("category") or "other"\n'
        '        end_iso = market.get("expected_expiration_time") or "(no end date)"\n'
        '        implied = market.get("implied_prob_yes")\n'
        '\n'
        '        # Prefer per-market `title` when present — carries the explicit\n'
        '        # threshold in plain English ("Will the temp in NYC be above\n'
        '        # 57.99° on May 11, 2026 at 1pm EDT?"). Fall back to\n'
        '        # event_title + subtitle for legacy / malformed markets.\n'
        '        if market_title:\n'
        '            question = market_title\n'
        '        else:\n'
        '            question = event_title\n'
        '            if subtitle:\n'
        '                question = f"{event_title} — outcome: {subtitle}"'
    )
    if old not in src:
        sys.exit("FAIL: anchor 2 (_estimate_probability) not found")
    src = src.replace(old, new, 1)
    p.write_text(src)
    print(f"  PATCHED: {p}")


def main() -> None:
    print(f"TAG={TAG}")
    patch()
    print("DONE")


if __name__ == "__main__":
    main()
