"""Surgical patcher: apply PCT resolution-check fix onto prod's current files.

Avoids the scp-stomp pattern. Patches:
  - trading_corp/agents/strategies/polymarket_copy_trader.py
  - trading_corp/main.py

Idempotent: re-running detects already-patched files and exits clean.
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

BASE = Path("/home/azureuser/trading_corp")
TAG = f"pre-pct-resolfix-{time.strftime('%Y%m%d-%H%M', time.gmtime())}"


def backup(p: Path) -> None:
    bak = p.with_suffix(p.suffix + f".{TAG}")
    shutil.copy(p, bak)
    print(f"  backup: {bak.name}")


def patch_pct() -> None:
    p = BASE / "trading_corp/agents/strategies/polymarket_copy_trader.py"
    src = p.read_text()
    if "polymarket_copy_entry_skipped_resolved" in src:
        print(f"  {p.name}: already patched (skipping)")
        return
    backup(p)

    # ── Patch 1: run_scan_cycle signature + docstring ──
    old = (
        "    async def run_scan_cycle(\n"
        "        self,\n"
        "        *,\n"
        "        data_api_client: PolymarketDataAPIClient,\n"
        "        logger_agent: Any = None,\n"
        "    ) -> list[ProposedOrder]:\n"
        '        """One copy-trader cycle. Returns ProposedOrders for the risk gate.\n'
        "\n"
        "        `data_api_client` must be an open async-context PolymarketDataAPIClient.\n"
        '        """'
    )
    new = (
        "    async def run_scan_cycle(\n"
        "        self,\n"
        "        *,\n"
        "        data_api_client: PolymarketDataAPIClient,\n"
        "        logger_agent: Any = None,\n"
        "        market_state_fetcher: Any = None,\n"
        "    ) -> list[ProposedOrder]:\n"
        '        """One copy-trader cycle. Returns ProposedOrders for the risk gate.\n'
        "\n"
        "        `data_api_client` must be an open async-context PolymarketDataAPIClient.\n"
        "\n"
        "        `market_state_fetcher` is an optional PolymarketBroker-like object\n"
        "        exposing async `get_market_resolution(condition_id=...)`. When\n"
        "        provided, `_emit_entry` checks resolution status before placing —\n"
        "        avoids the K3-class adverse-selection trap where a whale's stale\n"
        "        activity-feed entry lands on a market that has already settled\n"
        "        (observed on `btc-updown-5m-*` markets, 0/3 wins).\n"
        '        """'
    )
    if old not in src:
        sys.exit(f"FAIL: anchor 1 not found in {p}")
    src = src.replace(old, new, 1)

    # ── Patch 2: _process_whale_activity call site (sync → async) ──
    old = (
        "            whale_proposals = self._process_whale_activity(\n"
        "                wallet=wallet, user_name=user_name, rows=rows,\n"
        "                logger_agent=logger_agent,\n"
        "            )"
    )
    new = (
        "            whale_proposals = await self._process_whale_activity(\n"
        "                wallet=wallet, user_name=user_name, rows=rows,\n"
        "                logger_agent=logger_agent,\n"
        "                market_state_fetcher=market_state_fetcher,\n"
        "            )"
    )
    if old not in src:
        sys.exit(f"FAIL: anchor 2 not found in {p}")
    src = src.replace(old, new, 1)

    # ── Patch 3: _process_whale_activity signature ──
    old = (
        "    def _process_whale_activity(\n"
        "        self, *, wallet: str, user_name: str,\n"
        "        rows: list[ActivityRow], logger_agent: Any,\n"
        "    ) -> list[ProposedOrder]:"
    )
    new = (
        "    async def _process_whale_activity(\n"
        "        self, *, wallet: str, user_name: str,\n"
        "        rows: list[ActivityRow], logger_agent: Any,\n"
        "        market_state_fetcher: Any = None,\n"
        "    ) -> list[ProposedOrder]:"
    )
    if old not in src:
        sys.exit(f"FAIL: anchor 3 not found in {p}")
    src = src.replace(old, new, 1)

    # ── Patch 4: _emit_entry call site (sync → async + new arg) ──
    old = (
        "            if r.side == \"BUY\":\n"
        "                proposal = self._emit_entry(\n"
        "                    wallet=wallet, user_name=user_name, activity=r,\n"
        "                )"
    )
    new = (
        "            if r.side == \"BUY\":\n"
        "                proposal = await self._emit_entry(\n"
        "                    wallet=wallet, user_name=user_name, activity=r,\n"
        "                    market_state_fetcher=market_state_fetcher,\n"
        "                    logger_agent=logger_agent,\n"
        "                )"
    )
    if old not in src:
        sys.exit(f"FAIL: anchor 4 not found in {p}")
    src = src.replace(old, new, 1)

    # ── Patch 5: _emit_entry signature + resolution check ──
    old = (
        "    def _emit_entry(\n"
        "        self, *, wallet: str, user_name: str, activity: ActivityRow,\n"
        "    ) -> ProposedOrder | None:\n"
        "        copy_usdc = self._size_tier_usdc(activity.usdc_size)"
    )
    new = (
        "    async def _emit_entry(\n"
        "        self, *, wallet: str, user_name: str, activity: ActivityRow,\n"
        "        market_state_fetcher: Any = None,\n"
        "        logger_agent: Any = None,\n"
        "    ) -> ProposedOrder | None:\n"
        "        # ── Fix 2026-05-14: skip already-resolved markets ──\n"
        "        # The whale's activity feed surfaces trades with a 10-60s lag.\n"
        "        # On short-duration markets (e.g. `btc-updown-5m-*`, 5-min bars)\n"
        "        # the market may have already settled by the time we poll. Our\n"
        "        # paper-trade then 'enters' at the whale's stale price on a\n"
        "        # dead market and is guaranteed-loss when paired to a SELL.\n"
        "        # Observed: 3/3 losses on `btc-updown-5m-*` markets where the\n"
        "        # market's 5-min window had passed >hours before our poll.\n"
        "        if market_state_fetcher is not None and hasattr(\n"
        "            market_state_fetcher, 'get_market_resolution'\n"
        "        ):\n"
        "            try:\n"
        "                res = await market_state_fetcher.get_market_resolution(\n"
        "                    condition_id=activity.condition_id,\n"
        "                )\n"
        "                status = (res or {}).get('status')\n"
        "                if status in ('resolved', 'void'):\n"
        "                    if logger_agent is not None:\n"
        "                        logger_agent.log_event(\n"
        "                            self.name, 'polymarket_copy_entry_skipped_resolved',\n"
        "                            {'strategy': self.name, 'division': self.division,\n"
        "                             'wallet': wallet, 'whale_user_name': user_name,\n"
        "                             'condition_id': activity.condition_id,\n"
        "                             'slug': activity.slug,\n"
        "                             'outcome': activity.outcome,\n"
        "                             'whale_entry_price': activity.price,\n"
        "                             'market_status': status,\n"
        "                             'yes_won': (res or {}).get('yes_won')},\n"
        "                        )\n"
        "                    return None\n"
        "            except Exception as e:\n"
        "                log.warning(\n"
        "                    'polymarket_copy_trader: market_state_fetcher failed for %s: %s',\n"
        "                    activity.condition_id, e,\n"
        "                )\n"
        "        copy_usdc = self._size_tier_usdc(activity.usdc_size)"
    )
    if old not in src:
        sys.exit(f"FAIL: anchor 5 not found in {p}")
    src = src.replace(old, new, 1)

    p.write_text(src)
    print(f"  PATCHED: {p}")


def patch_main() -> None:
    p = BASE / "trading_corp/main.py"
    src = p.read_text()
    if "market_state_fetcher = None" in src and "get_market_resolution" in src:
        # Already patched? Check for our specific marker.
        if "Lazy-resolve a real PolymarketBroker for the resolution" in src:
            print(f"  {p.name}: already patched (skipping)")
            return

    # Find the polymarket_copy_trader's run_scan_cycle call site.
    old = (
        "                try:\n"
        "                    orders = await agent.run_scan_cycle(\n"
        "                        data_api_client=data_api_client,\n"
        "                        logger_agent=logger_agent,\n"
        "                    )"
    )
    new = (
        "                # Lazy-resolve a real PolymarketBroker for the resolution\n"
        "                # check inside _emit_entry. agent.division is broker:paper;\n"
        "                # polymarket_arbitrage owns the real PolymarketBroker.\n"
        "                # Same lazy-resolve pattern as K3 uses for trade-tape.\n"
        "                market_state_fetcher = None\n"
        "                for div_name, br in data_exec.brokers.items():\n"
        "                    if hasattr(br, 'get_market_resolution'):\n"
        "                        market_state_fetcher = br\n"
        "                        break\n"
        "\n"
        "                try:\n"
        "                    orders = await agent.run_scan_cycle(\n"
        "                        data_api_client=data_api_client,\n"
        "                        logger_agent=logger_agent,\n"
        "                        market_state_fetcher=market_state_fetcher,\n"
        "                    )"
    )
    if old not in src:
        sys.exit(f"FAIL: main.py anchor not found")
    backup(p)
    src = src.replace(old, new, 1)
    p.write_text(src)
    print(f"  PATCHED: {p}")


def main() -> None:
    print(f"TAG={TAG}")
    patch_pct()
    patch_main()
    print("DONE")


if __name__ == "__main__":
    main()
