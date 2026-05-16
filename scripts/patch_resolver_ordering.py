"""One-shot prod patcher: switch kalshi_resolver + polymarket_resolver
`_fetch_unresolved_orders` ordering from `a.ts ASC` to
`(expires_at IS NULL), expires_at ASC, a.ts ASC` so past-expiration rows
get scanned before long-horizon-still-pending rows. Idempotent.
"""
from __future__ import annotations

import pathlib
import sys

KR = pathlib.Path("/home/azureuser/trading_corp/trading_corp/agents/kalshi_resolver.py")
PR = pathlib.Path("/home/azureuser/trading_corp/trading_corp/agents/polymarket_resolver.py")


# ── kalshi_resolver.py ─────────────────────────────────────────────────

KR_OLD = """    Per-actor budget: each actor in `_KALSHI_ACTORS` fetches up to
    `max_per_actor` of its OLDEST unresolved rows. A single
    `WHERE actor IN (...) ORDER BY ts ASC LIMIT N` query starved newer
    strategies — when kalshi_llm_arbitrage had 1700+ stuck-pending rows,
    the global ts-ASC cap meant kalshi_weather_arb + kalshi_crypto_arb
    rows never made the top-N cut.
    \"\"\"
    rows: list[dict] = []
    with _db.connect(db_url) as conn:
        for actor in _KALSHI_ACTORS:
            cur = conn.execute(
                "SELECT a.ts AS ts, a.actor AS actor, a.payload_json "
                "FROM audit_event a "
                "LEFT JOIN kalshi_round_trips r "
                "  ON r.order_id = json_extract(a.payload_json, '$.order_id') "
                "WHERE a.actor = ? "
                "  AND a.kind = 'would_have_placed' "
                "  AND COALESCE(json_extract(a.payload_json, '$.side'), 'buy') = 'buy' "
                "  AND r.order_id IS NULL "
                "  AND json_extract(a.payload_json, '$.order_id') NOT IN ("
                "        SELECT entry_order_id FROM kalshi_round_trips "
                "        WHERE entry_order_id IS NOT NULL"
                "      ) "
                "ORDER BY a.ts ASC LIMIT ?",
                (actor, max_per_actor),
            )"""

KR_NEW = """    Per-actor budget: each actor in `_KALSHI_ACTORS` fetches up to
    `max_per_actor` of its unresolved rows. A single
    `WHERE actor IN (...) ORDER BY ts ASC LIMIT N` query starved newer
    strategies — when kalshi_llm_arbitrage had 1700+ stuck-pending rows,
    the global ts-ASC cap meant kalshi_weather_arb + kalshi_crypto_arb
    rows never made the top-N cut.

    Ordering: `expires_at ASC NULLS LAST` (NULLs synthesized via
    `(expires_at IS NULL)` since SQLite NULLS LAST is version-conditional).
    Past-expiration rows scanned first — they're the ones most likely to
    have a final resolution on Kalshi. The original `ts ASC` ordering
    prioritized OLDEST audit rows, but oldest-audit ≠ most-likely-resolved
    — early LLM bets targeted multi-week-out Politics markets that are
    still pending while later, short-horizon bets already settled. The
    old ordering left 600+ past-expiration kalshi_llm rows permanently
    stuck behind the long-horizon backlog.
    \"\"\"
    rows: list[dict] = []
    with _db.connect(db_url) as conn:
        for actor in _KALSHI_ACTORS:
            cur = conn.execute(
                "SELECT a.ts AS ts, a.actor AS actor, a.payload_json "
                "FROM audit_event a "
                "LEFT JOIN kalshi_round_trips r "
                "  ON r.order_id = json_extract(a.payload_json, '$.order_id') "
                "WHERE a.actor = ? "
                "  AND a.kind = 'would_have_placed' "
                "  AND COALESCE(json_extract(a.payload_json, '$.side'), 'buy') = 'buy' "
                "  AND r.order_id IS NULL "
                "  AND json_extract(a.payload_json, '$.order_id') NOT IN ("
                "        SELECT entry_order_id FROM kalshi_round_trips "
                "        WHERE entry_order_id IS NOT NULL"
                "      ) "
                "ORDER BY (json_extract(a.payload_json, '$.expires_at') IS NULL), "
                "         json_extract(a.payload_json, '$.expires_at') ASC, "
                "         a.ts ASC "
                "LIMIT ?",
                (actor, max_per_actor),
            )"""


# ── polymarket_resolver.py ─────────────────────────────────────────────

PR_OLD = """        cur = conn.execute(
            "SELECT a.ts AS ts, a.actor AS actor, a.payload_json "
            "FROM audit_event a "
            "LEFT JOIN polymarket_round_trips r "
            "  ON r.order_id = json_extract(a.payload_json, '$.order_id') "
            "WHERE a.actor IN ('polymarket_arbitrage', 'polymarket_copy_trader') "
            "  AND a.kind  = 'would_have_placed' "
            "  AND COALESCE(json_extract(a.payload_json, '$.side'), 'buy') = 'buy' "
            "  AND r.order_id IS NULL "
            "  AND json_extract(a.payload_json, '$.order_id') NOT IN ("
            "        SELECT entry_order_id FROM polymarket_round_trips "
            "        WHERE entry_order_id IS NOT NULL"
            "      ) "
            "ORDER BY a.ts ASC"
        )"""

PR_NEW = """        # Ordering: `resolves_at ASC NULLS LAST` (NULLs synthesized via
        # `(resolves_at IS NULL)` since SQLite NULLS LAST is version-
        # conditional). Past-resolution rows scanned first — they're the
        # most likely to have a final settlement on Polymarket. The
        # original `ts ASC` ordering left past-expiration rows stuck
        # behind long-horizon backlog (same shape as the kalshi_resolver
        # bug fixed in the same session).
        cur = conn.execute(
            "SELECT a.ts AS ts, a.actor AS actor, a.payload_json "
            "FROM audit_event a "
            "LEFT JOIN polymarket_round_trips r "
            "  ON r.order_id = json_extract(a.payload_json, '$.order_id') "
            "WHERE a.actor IN ('polymarket_arbitrage', 'polymarket_copy_trader') "
            "  AND a.kind  = 'would_have_placed' "
            "  AND COALESCE(json_extract(a.payload_json, '$.side'), 'buy') = 'buy' "
            "  AND r.order_id IS NULL "
            "  AND json_extract(a.payload_json, '$.order_id') NOT IN ("
            "        SELECT entry_order_id FROM polymarket_round_trips "
            "        WHERE entry_order_id IS NOT NULL"
            "      ) "
            "ORDER BY (json_extract(a.payload_json, '$.resolves_at') IS NULL), "
            "         json_extract(a.payload_json, '$.resolves_at') ASC, "
            "         a.ts ASC"
        )"""


def _apply(path: pathlib.Path, old: str, new: str, label: str) -> bool:
    src = path.read_text(encoding="utf-8")
    if new in src and old not in src:
        print(f"  [{label}] already patched — skip")
        return True
    if old not in src:
        print(f"  [{label}] ANCHOR NOT FOUND", file=sys.stderr)
        return False
    path.write_text(src.replace(old, new, 1), encoding="utf-8")
    print(f"  [{label}] patched")
    return True


def main() -> int:
    ok = True
    print(f"== {KR} ==")
    ok &= _apply(KR, KR_OLD, KR_NEW, "kalshi_resolver ordering")
    print(f"== {PR} ==")
    ok &= _apply(PR, PR_OLD, PR_NEW, "polymarket_resolver ordering")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
