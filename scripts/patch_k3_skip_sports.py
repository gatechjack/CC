"""Surgical patch: K3 skips Sports-category Kalshi tickers.

Why: K3 mirrors whale positions regardless of category. ~80 historical
sports trades. With the kalshi_sports_scout shipping today as the
dedicated sports observer (and a real sports trading division to follow
once edge is validated), we route all Sports tickers AWAY from K3.

The K3 strategy has no category filter today — markets reach it as
`ticker` strings from Apify whale-position scrapes. We add a
prefix-based ticker filter to skip known sports market families.

Idempotent.
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

BASE = Path("/home/azureuser/trading_corp")
TAG = f"pre-k3-skip-sports-{time.strftime('%Y%m%d-%H%M', time.gmtime())}"


def patch() -> None:
    p = BASE / "trading_corp/agents/strategies/kalshi_copy_trader.py"
    src = p.read_text()
    if "_SPORTS_TICKER_PREFIXES" in src or "kalshi_copy_entry_skipped_sports" in src:
        print(f"  {p.name}: already patched (skipping)")
        return
    bak = p.with_suffix(p.suffix + f".{TAG}")
    shutil.copy(p, bak)
    print(f"  backup: {bak.name}")

    # 1. Inject module-level sports-ticker prefix tuple + helper. Place
    #    after the existing import block (look for the first `class`
    #    declaration as anchor).
    helper_block = '''
# Sports ticker families to skip — kalshi_sports_scout owns these (2026-05-14).
# Add new prefixes here as Kalshi launches new sport categories.
_SPORTS_TICKER_PREFIXES = (
    "KXMLB", "KXNBA", "KXNHL", "KXNFL", "KXMLS",
    "KXATP", "KXWTA", "KXITF",
    "KXCS2", "KXDOTA", "KXLCS",
    "KXLIGAMX", "KXARGPREM", "KXCOPADOBRASIL", "KXDIMAYOR",
    "KXDENSUPERLIGA", "KXSAUDIPL", "KXURYPD", "KXAPFDDH",
    "KXEPL", "KXUCL", "KXUEL", "KXBUNDESLIGA", "KXLALIGA", "KXSERIEA",
    "KXLIGUE1", "KXJLEAGUE", "KXNCAAF", "KXNCAAB", "KXUFC", "KXBOXING",
)


def _is_sports_ticker(ticker: str) -> bool:
    """True if `ticker` is in a known sports market family.

    Used by K3 to route Sports-category trades to `kalshi_sports_scout`
    (and eventually a dedicated trading division). Kalshi doesn't tag
    `category` on the activity-feed scraper output, so we prefix-match
    on the ticker. Maintenance: add new prefixes as Kalshi launches new
    sport categories.
    """
    if not ticker:
        return False
    return any(ticker.startswith(p) for p in _SPORTS_TICKER_PREFIXES)


'''
    anchor1 = "class KalshiCopyTraderAgent"
    if anchor1 not in src:
        sys.exit(f"FAIL: class anchor not found in {p}")
    src = src.replace(anchor1, helper_block + anchor1, 1)

    # 2. Inject skip inside the entries loop, right before _emit_entry call.
    anchor2 = (
        "            # Entries: emit ProposedOrder + persist our_side/size if accepted.\n"
        "            for ticker in new_tickers:\n"
        "                pos = current_by_ticker[ticker]\n"
    )
    new2 = (
        "            # Entries: emit ProposedOrder + persist our_side/size if accepted.\n"
        "            for ticker in new_tickers:\n"
        "                # Skip Sports — handled by kalshi_sports_scout (2026-05-14).\n"
        "                if _is_sports_ticker(ticker):\n"
        "                    if logger_agent is not None:\n"
        "                        logger_agent.log_event(\n"
        "                            self.name, 'kalshi_copy_entry_skipped_sports',\n"
        "                            {'strategy': self.name, 'division': self.division,\n"
        "                             'wallet': wallet, 'whale_handle': user_name,\n"
        "                             'ticker': ticker,\n"
        "                             'reason': 'sports_routed_to_scout'},\n"
        "                        )\n"
        "                    continue\n"
        "                pos = current_by_ticker[ticker]\n"
    )
    if anchor2 not in src:
        sys.exit(f"FAIL: entries-loop anchor not found in {p}")
    src = src.replace(anchor2, new2, 1)

    p.write_text(src)
    print(f"  PATCHED: {p.name}")


def main() -> None:
    print(f"TAG={TAG}")
    patch()
    print("DONE")


if __name__ == "__main__":
    main()
