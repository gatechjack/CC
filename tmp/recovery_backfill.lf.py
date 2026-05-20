"""One-off backfill: recover the 6 wallets deleted from watch_only_whales
by the v0 promote endpoint (which mutated the slot before v2 stopped
doing so).

Strategy: pull top-200 from the Polymarket leaderboard, match by wallet,
synthesize a watch_only_whales entry with leaderboard-derived stats.
Falls back to a zero-stat placeholder for any wallet not found in the
top slice. Appends to the existing watch_only_whales slot; idempotent
(skips wallets already present).
"""
from __future__ import annotations

import asyncio
import logging
import sys

from trading_corp.persistence import db
from trading_corp.data.polymarket_data_api_client import PolymarketDataAPIClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("recovery_backfill")

DB_URL = "sqlite:////home/azureuser/trading_corp/data/trading_corp.db"

# (wallet_lower, user_name_hint) — gathered from pinned_whales + audit_event
MISSING_WHALES: list[tuple[str, str]] = [
    ("0x7f9e2d1df78614564a70becc7fa14aa9a6623a0e", "nojnn"),
    ("0x91eee6b7cea1916214daebec3b92b7513079c5b8", "everydaymortgage"),
    ("0xbc43a2f0deb85ba4ad316300762972089c911540", "westminster"),
    ("0x86cd93526a4e7ad201ed3d1c6f2647b61837504c", "IlIIllIIIllIIl"),
    ("0xef27152015c5313daf457804e7319e869ed3381b", "superbeter007"),
    ("0x335592400e402c26583ce8b56d12605e9548a126", "ranger44"),
]


def _build_placeholder(wallet: str, user_name: str) -> dict:
    """Zero-stat placeholder when the leaderboard doesn't have this wallet."""
    return {
        "rank": None,
        "proxy_wallet": wallet,
        "user_name": user_name,
        "x_username": "",
        "verified_badge": False,
        "total_resolved_positions": 0,
        "wins": 0, "losses": 0,
        "win_rate": None,
        "realized_pnl_usdc": 0.0,
        "total_usdc_size_resolved": 0.0,
        "lifetime_pnl_from_leaderboard": 0.0,
        "lifetime_vol_from_leaderboard": 0.0,
        "best_category": "",
        "included_iso": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc,
        ).isoformat(),
        "notes": "recovered post-demote (v0-deleted entry; stats stale until next weekly cron)",
    }


async def main() -> int:
    rec = db.load_agent_state(
        "polymarket_copy_trader", "watch_only_whales", db_url=DB_URL,
    )
    current = list(rec[0]) if rec and isinstance(rec[0], list) else []
    log.info("current watch_only_whales size: %d", len(current))

    # Strip out prior placeholder entries from this recovery (so we can
    # replace them with enriched data on a re-run).
    missing_wallet_set = {w for w, _ in MISSING_WHALES}
    current = [
        w for w in current
        if not (isinstance(w, dict)
                and str(w.get("proxy_wallet") or "").lower() in missing_wallet_set)
    ]
    log.info("after stripping prior recovery placeholders: %d", len(current))

    missing_to_add = MISSING_WHALES[:]

    # Try to enrich from the Polymarket leaderboard (one async-context slice).
    enrich_by_wallet: dict[str, dict] = {}
    try:
        async with PolymarketDataAPIClient() as client:
            entries = await client.fetch_leaderboard(
                limit=100, offset=0, sort_by="profit_all",
            )
            entries += await client.fetch_leaderboard(
                limit=100, offset=100, sort_by="profit_all",
            )
            entries += await client.fetch_leaderboard(
                limit=100, offset=200, sort_by="profit_all",
            )
            for e in entries:
                enrich_by_wallet[e.proxy_wallet.lower()] = {
                    "user_name": e.user_name,
                    "x_username": e.x_username,
                    "verified_badge": e.verified_badge,
                    "lifetime_pnl": e.pnl,
                    "lifetime_vol": e.vol,
                }
        log.info("fetched leaderboard, %d entries indexed", len(enrich_by_wallet))
    except Exception as e:
        log.warning("leaderboard fetch failed; falling back to placeholders: %s", e)

    appended = []
    async with PolymarketDataAPIClient() as client:
        for wallet, name_hint in missing_to_add:
            enrich = enrich_by_wallet.get(wallet)
            # Always fetch closed-positions for accurate wins/losses/resolved.
            # Paginate: API caps at 50 per call.
            closed_rows = []
            try:
                for offset in (0, 50, 100, 150, 200):
                    page = await client.fetch_closed_positions(
                        wallet, limit=50, offset=offset,
                    )
                    if not page:
                        break
                    closed_rows.extend(page)
                    if len(page) < 50:
                        break
            except Exception as e:
                log.warning("closed-positions fetch failed for %s: %s", wallet[:10], e)
            resolved = len(closed_rows)
            wins = sum(1 for r in closed_rows if r.cur_price >= 0.9)
            losses = resolved - wins
            wr = (wins / resolved) if resolved > 0 else None
            realized_pnl = sum(r.realized_pnl for r in closed_rows)
            total_size = sum(r.total_bought for r in closed_rows)

            entry = {
                "rank": None,
                "proxy_wallet": wallet,
                "user_name": (enrich["user_name"] if enrich else "") or name_hint,
                "x_username": (enrich["x_username"] if enrich else "") or "",
                "verified_badge": bool(enrich["verified_badge"]) if enrich else False,
                "total_resolved_positions": resolved,
                "wins": wins, "losses": losses,
                "win_rate": round(wr, 4) if wr is not None else None,
                "realized_pnl_usdc": round(realized_pnl, 2),
                "total_usdc_size_resolved": round(total_size, 2),
                "lifetime_pnl_from_leaderboard": round(
                    float(enrich["lifetime_pnl"] or 0.0) if enrich else 0.0, 2,
                ),
                "lifetime_vol_from_leaderboard": round(
                    float(enrich["lifetime_vol"] or 0.0) if enrich else 0.0, 2,
                ),
                "best_category": "",
                "included_iso": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc,
                ).isoformat(),
                "notes": (
                    f"recovered post-demote (v0-deleted entry; "
                    f"{'leaderboard+' if enrich else ''}closed-positions API)"
                ),
            }
            log.info(
                "RECOVERED: %s (%s) resolved=%d wins=%d wr=%.0f%% pnl=$%.0fK %s",
                name_hint, wallet[:10], resolved, wins,
                ((wr or 0.0) * 100),
                realized_pnl / 1000.0,
                "[leaderboard]" if enrich else "",
            )
            current.append(entry)
            appended.append(name_hint)

    db.set_agent_state(
        "polymarket_copy_trader", "watch_only_whales", current, db_url=DB_URL,
    )
    log.info("DONE: appended %d entries: %s", len(appended), appended)
    log.info("watch_only_whales new size: %d", len(current))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
