"""Engine-side M3 WRITER: a slow (5-min) timer that persists per-account PER-SHARD balance snapshots for pm_web to
read credential-free (shard_snapshot.read_latest). This is INFORMATION (a display + a balance HISTORY); it is
DELIBERATELY separate from the driver's per-cycle balance read, which is a funding GATE -- one gates orders, the
other shows a number. Fail-soft per account AND per cycle: a bad read skips THAT account this cycle and is logged,
never crashing the loop or the engine.

Imports the broker client duck-typed (`broker._client()` -> has `.get`) -- engine-side. NOT pm_web-safe (pm_web
only ever calls shard_snapshot.read_latest, which touches no venue). The per-account credential resolution mirrors
main.py's division resolution (secret_ref 'kalshi_karen' -> the isolated KALSHI-KAREN keypair; else the shared one),
so the ENGINE reads each account with ITS OWN keys -- which is why Karen's balance is readable here even though her
keys are separate.
"""
from __future__ import annotations

import asyncio
import logging
import time

from . import db, shard_balance, shard_snapshot

_LOG = logging.getLogger(__name__)

DEFAULT_INTERVAL_SEC = 300   # 5 min -> ~576 /portfolio/balance calls/day for 2 accounts; balance changes slowly + the age is shown.


def resolve_kalshi_keys(secret_ref, secrets):
    """(api_key_id, private_key_pem) for a pm_account.secret_ref, mirroring main.py:3047's division resolution:
    'kalshi_karen' -> the isolated KALSHI-KAREN-* keypair; anything else (e.g. 'KALSHI') -> the shared KALSHI-*
    keypair. getattr keeps it tolerant of a secrets object that lacks the karen fields (-> None -> caller skips)."""
    if secret_ref == "kalshi_karen":
        return getattr(secrets, "kalshi_karen_api_key_id", None), getattr(secrets, "kalshi_karen_private_key_pem", None)
    return getattr(secrets, "kalshi_api_key_id", None), getattr(secrets, "kalshi_private_key_pem", None)


async def snapshot_once(pm_db_path, account_id, client, *, now_ts=None):
    """Read ONE account's per-shard balance via an authenticated client + persist a snapshot. Returns the
    ShardBalances (for logging). Raises on a read/write failure -- the loop fail-softs per account around this."""
    sb = await shard_balance.fetch_shard_balances(client)
    with db.connect(pm_db_path) as conn:
        shard_snapshot.write_snapshot(conn, account_id, sb, int(now_ts if now_ts is not None else time.time()))
    return sb


async def scheduled_shard_snapshot_loop(pm_db_path, brokers_by_account, *, interval_sec=DEFAULT_INTERVAL_SEC):
    """Every `interval_sec`, snapshot each account's balance. Fail-soft per account (a bad read skips that account
    this cycle) AND per cycle (an unexpected error waits out the interval and retries) -- the display goes stale
    (its AGE shows it) rather than the engine crashing."""
    while True:
        for account_id, broker in brokers_by_account.items():
            try:
                sb = await snapshot_once(pm_db_path, account_id, broker._client())
                _LOG.info("shard-snapshot %s: total=$%.2f by_shard=%s has_breakdown=%s",
                          account_id, sb.total_dollars, sb.by_shard, sb.has_breakdown)
            except Exception as e:  # noqa: BLE001 -- fail-soft: skip THIS account this cycle, never crash the loop
                _LOG.warning("shard-snapshot for %s failed (skip this cycle; the display's AGE will show it): %s",
                             account_id, e)
        await asyncio.sleep(interval_sec)
