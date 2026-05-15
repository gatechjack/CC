"""Surgical patcher: add PCT entry drift-check on top of resolution-check.

Patches `polymarket_copy_trader.py` only. Adds a price-drift skip gate
right after the resolution-check block (which must already be present).

Idempotent: detects already-patched files and exits clean.
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

BASE = Path("/home/azureuser/trading_corp")
TAG = f"pre-pct-driftcheck-{time.strftime('%Y%m%d-%H%M', time.gmtime())}"


def patch_pct() -> None:
    p = BASE / "trading_corp/agents/strategies/polymarket_copy_trader.py"
    src = p.read_text()
    if "polymarket_copy_entry_skipped_drift" in src:
        print(f"  {p.name}: already patched (skipping)")
        return
    if "polymarket_copy_entry_skipped_resolved" not in src:
        sys.exit(f"FAIL: prerequisite resolution-check patch not in {p}")

    bak = p.with_suffix(p.suffix + f".{TAG}")
    shutil.copy(p, bak)
    print(f"  backup: {bak.name}")

    old = (
        "            except Exception as e:\n"
        "                log.warning(\n"
        "                    'polymarket_copy_trader: market_state_fetcher failed for %s: %s',\n"
        "                    activity.condition_id, e,\n"
        "                )\n"
        "        copy_usdc = self._size_tier_usdc(activity.usdc_size)"
    )
    new = (
        "            except Exception as e:\n"
        "                log.warning(\n"
        "                    'polymarket_copy_trader: market_state_fetcher failed for %s: %s',\n"
        "                    activity.condition_id, e,\n"
        "                )\n"
        "        # ── Fix 2026-05-14: drift check ──\n"
        "        # Activity-feed lag (10-60s+) means by our poll time the market may\n"
        "        # have already moved against the whale's bet. Observed pattern in\n"
        "        # Pedrobeliever47's political losses (Trump/Xi/Musk insider markets):\n"
        "        # whale fills, insiders move price, market resolves opposite within\n"
        "        # minutes. We were paper-trading at whale's stale fill price, which\n"
        "        # over-states our actual entry. Skip when our outcome's current\n"
        "        # price has dropped >threshold%% below whale's fill — alpha is gone.\n"
        "        if market_state_fetcher is not None and hasattr(\n"
        "            market_state_fetcher, 'quote'\n"
        "        ):\n"
        "            try:\n"
        "                current_price = await market_state_fetcher.quote(\n"
        "                    f'{activity.slug}:{activity.outcome}'\n"
        "                )\n"
        "                if 0.0 < current_price < 1.0:\n"
        "                    drift = (current_price - activity.price) / max(\n"
        "                        activity.price, 0.01\n"
        "                    )\n"
        "                    threshold = float(self._strat_cfg.get(\n"
        "                        'entry_drift_skip_threshold', -0.30,\n"
        "                    ))\n"
        "                    if drift < threshold:\n"
        "                        if logger_agent is not None:\n"
        "                            logger_agent.log_event(\n"
        "                                self.name,\n"
        "                                'polymarket_copy_entry_skipped_drift',\n"
        "                                {'strategy': self.name,\n"
        "                                 'division': self.division,\n"
        "                                 'wallet': wallet,\n"
        "                                 'whale_user_name': user_name,\n"
        "                                 'condition_id': activity.condition_id,\n"
        "                                 'slug': activity.slug,\n"
        "                                 'outcome': activity.outcome,\n"
        "                                 'whale_entry_price': activity.price,\n"
        "                                 'current_price': current_price,\n"
        "                                 'drift_pct': drift * 100,\n"
        "                                 'threshold_pct': threshold * 100},\n"
        "                            )\n"
        "                        return None\n"
        "            except Exception as e:\n"
        "                log.warning(\n"
        "                    'polymarket_copy_trader: quote drift check failed for %s: %s',\n"
        "                    activity.condition_id, e,\n"
        "                )\n"
        "        copy_usdc = self._size_tier_usdc(activity.usdc_size)"
    )
    if old not in src:
        sys.exit(f"FAIL: anchor not found in {p}")
    src = src.replace(old, new, 1)
    p.write_text(src)
    print(f"  PATCHED: {p}")


def main() -> None:
    print(f"TAG={TAG}")
    patch_pct()
    print("DONE")


if __name__ == "__main__":
    main()
