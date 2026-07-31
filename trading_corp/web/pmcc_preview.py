"""In-process consent stash for the PMCC roll card (2026-07-30).

Carries the EXACT `ProposedOrder`s a Re-analyze render built forward to the Approve
dispatch, so the strike + legs the operator saw on the card are the strike + legs
that get fired. Price may still drift — the dispatch re-prices from live quotes at
approval (`reprice_combo_from_quotes`) and the card says "actual fill will differ";
the combo SHAPE (contracts) does not.

Design:
  * Single slot per (slug, symbol): the latest render wins. An operator can only
    approve what they LAST saw rendered.
  * Single-use: a hit consumes the slot (a re-approve must re-render). This also
    guards a double-submit.
  * Not persisted: a process restart drops the stash. Approve then falls back to a
    live rebuild in `execute_pair_orders`, which fingerprint-guards against the
    combo it rebuilds differing from the one the operator approved (bail + re-surface).
  * `fingerprint` hashes the combo SHAPE only (contracts, not price), so it travels
    in the Approve form as the consent token and a price move never invalidates it.

No order/broker/DB side effects — pure in-memory carry.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

_TTL_SEC = 900  # 15 min — a rendered card older than this is re-rendered, not fired.

# (slug, SYMBOL) -> {"id", "fingerprint", "orders", "action", "ts"}
_STASH: dict[tuple[str, str], dict] = {}


def _key(slug: str, symbol: str) -> tuple[str, str]:
    return (slug, (symbol or "").upper())


def fingerprint(orders: list[Any]) -> str:
    """Stable short hash of the combo SHAPE — the sorted legs of
    (underlying, option_type, strike, expiration, side, position_effect,
    ratio_quantity). Independent of price, so a live re-quote at dispatch does NOT
    change it; two builds that pick the same contracts hash identically."""
    parts: list[str] = []
    for o in orders or []:
        ex = getattr(o, "extra", None) or {}
        parts.append("|".join(str(x) for x in (
            ex.get("underlying") or getattr(o, "symbol", ""),
            ex.get("option_type", "call"),
            ex.get("strike"),
            ex.get("expiration"),
            getattr(o, "side", ""),
            ex.get("position_effect", ""),
            ex.get("ratio_quantity", 1),
        )))
    joined = "\n".join(sorted(parts))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def stash_preview(
    slug: str, symbol: str, orders: list[Any], *, action: str | None = None,
    now: float | None = None,
) -> tuple[str, str] | None:
    """Store the previewed combo; return (preview_id, fingerprint). Returns None for
    an empty build (nothing to carry — the operator sees no Approve, and any dispatch
    would rebuild live anyway)."""
    if not orders:
        _STASH.pop(_key(slug, symbol), None)
        return None
    fp = fingerprint(orders)
    pid = uuid.uuid4().hex[:12]
    _STASH[_key(slug, symbol)] = {
        "id": pid, "fingerprint": fp, "orders": list(orders),
        "action": action, "ts": now if now is not None else time.time(),
    }
    return pid, fp


def load_preview(
    slug: str, symbol: str, preview_id: str | None, fingerprint_: str | None,
    *, now: float | None = None,
) -> list[Any] | None:
    """Return the stashed orders IFF (id, fingerprint) match and the slot is not
    expired. Consumes the slot on a hit (single-use). Any mismatch/expiry → None,
    and the caller rebuilds live (fingerprint-guarded)."""
    if not preview_id or not fingerprint_:
        return None
    k = _key(slug, symbol)
    ent = _STASH.get(k)
    if not ent:
        return None
    if ent["id"] != preview_id or ent["fingerprint"] != fingerprint_:
        return None
    _now = now if now is not None else time.time()
    if _now - ent["ts"] > _TTL_SEC:
        _STASH.pop(k, None)
        return None
    _STASH.pop(k, None)   # single-use
    return ent["orders"]
