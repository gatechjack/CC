"""Phase-2a roster-split invariant helpers (wallet identity + disjointness).

The Poly->Kalshi copy division splits each whale into exactly ONE roster:

  * LIVE   — ``agent_state[poly_kalshi_mlb/live_whales]``   (real Kalshi orders)
  * PAPER  — ``agent_state[polymarket_copy_trader/selected_whales]`` (paper sim)

The load-bearing invariant is ``live ∩ paper == ∅``: a whale must never be
copied by BOTH sides at once (the "one whale in two states" failure). This
module is the single source of truth for

  1. wallet identity — how a roster's stored value maps to a set of wallets, and
  2. the disjointness assertion CP3 (boot) and CP4 (every promote/demote move)
     will call.

Wallet is the identity (immune to display-name edits), compared case-insensitively.

CP2 SCOPE: this helper is built + tested here but wired into NOTHING yet — CP3
(paper-sim read-time subtract + boot assert) and CP4 (endpoints) consume it.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

_log = logging.getLogger(__name__)

# agent_state actors/keys for the two rosters (defaults; overridable per call).
LIVE_ACTOR = "poly_kalshi_mlb"
LIVE_KEY = "live_whales"
PAPER_ACTOR = "polymarket_copy_trader"
PAPER_KEY = "selected_whales"

# Keys under which a whale's wallet may be stored across the roster shapes.
# selected/pinned entries carry ``wallet``; watch_only entries carry
# ``proxy_wallet``. Bare-string entries are the wallet itself.
_WALLET_FIELDS = ("wallet", "proxy_wallet")


class RosterInvariantError(AssertionError):
    """Raised when the live and paper rosters share one or more wallets.

    Subclasses AssertionError so a boot-time guard reads naturally while still
    being catchable distinctly from unrelated assertions.
    """


def extract_wallets(value: Any) -> set[str]:
    """Normalize a roster agent_state value to a set of lowercased wallets.

    Tolerates every shape the rosters use in the wild:
      * ``list[dict]`` with a ``wallet`` (selected/pinned) or ``proxy_wallet``
        (watch_only) field,
      * ``list[str]`` of bare wallet strings,
      * mixed lists,
      * ``None`` / non-list / empty -> ``set()``.

    Wallets are lowercased + stripped so identity is case-insensitive (the
    routes store them lowercased; the live roster keeps them raw). Blank/missing
    wallets are skipped.
    """
    out: set[str] = set()
    if not isinstance(value, list):
        return out
    for v in value:
        wallet = ""
        if isinstance(v, dict):
            for field in _WALLET_FIELDS:
                w = v.get(field)
                if w:
                    wallet = str(w)
                    break
        elif isinstance(v, str):
            wallet = v
        wallet = wallet.strip().lower()
        if wallet:
            out.add(wallet)
    return out


def assert_disjoint(live: Iterable[str], paper: Iterable[str]) -> set[str]:
    """Assert ``live ∩ paper == ∅``; return the (empty) overlap on success.

    Inputs may be raw wallet iterables OR already-extracted sets. Raises
    ``RosterInvariantError`` naming the offending wallet(s) if any whale is on
    both rosters.
    """
    live_set = {str(w).strip().lower() for w in live if str(w).strip()}
    paper_set = {str(w).strip().lower() for w in paper if str(w).strip()}
    overlap = live_set & paper_set
    if overlap:
        raise RosterInvariantError(
            "roster invariant violated: wallet(s) in BOTH live and paper "
            f"rosters: {sorted(overlap)}"
        )
    return overlap


def check_rosters_disjoint(
    db_url: str = "sqlite:///data/trading_corp.db",
    *,
    live_actor: str = LIVE_ACTOR,
    live_key: str = LIVE_KEY,
    paper_actor: str = PAPER_ACTOR,
    paper_key: str = PAPER_KEY,
) -> tuple[set[str], set[str]]:
    """Read both rosters from agent_state and assert they are disjoint.

    Returns ``(live_wallets, paper_wallets)`` on success (handy for logging the
    boot-time posture). Raises ``RosterInvariantError`` on overlap. A missing
    key reads as an empty roster.
    """
    from trading_corp.persistence.db import load_agent_state

    live_rec = load_agent_state(live_actor, live_key, db_url=db_url)
    paper_rec = load_agent_state(paper_actor, paper_key, db_url=db_url)
    live_wallets = extract_wallets(live_rec[0]) if live_rec else set()
    paper_wallets = extract_wallets(paper_rec[0]) if paper_rec else set()
    assert_disjoint(live_wallets, paper_wallets)
    return live_wallets, paper_wallets


def assert_roster_invariant_boot(
    db_url: str = "sqlite:///data/trading_corp.db",
    *,
    logger: "logging.Logger | None" = None,
) -> bool:
    """Boot-time roster-disjointness guard. LOG-LOUD-AND-CONTINUE: never raises.

    Returns True if the rosters are disjoint (or the check could not run),
    False if a ``live ∩ paper`` overlap was detected and logged as an error.

    ── Failure mode (deliberate): log-loud-and-CONTINUE, not hard-fail. ──
    The engine is ONE process hosting many divisions (MACE, PEAD, PMCC,
    bitunix, kalshi, poly_kalshi). Hard-failing boot over a roster-bookkeeping
    overlap would take down every unrelated division — a disproportionate blast
    radius. Crucially, the overlap cannot itself cause a double-COPY: the live
    loop reads only ``live_whales`` and the paper sim read-time-subtracts
    ``live_whales`` (`polymarket_copy_trader._load_live_whale_wallets`), so a
    live whale is never papered even when stored state is dirty. This boot check
    is therefore DETECTION + alerting, not the primary guard — so it logs LOUD
    (error) for the operator to reconcile and lets the engine come up healthy.
    An unexpected read error is treated as non-blocking (True) so we don't cry
    wolf and don't brick boot on an unrelated DB hiccup.
    """
    lg = logger or _log
    try:
        live, paper = check_rosters_disjoint(db_url=db_url)
        lg.info(
            "poly_kalshi roster invariant OK: %d live / %d paper wallet(s), disjoint",
            len(live), len(paper),
        )
        return True
    except RosterInvariantError as e:
        lg.error(
            "POLY_KALSHI ROSTER INVARIANT VIOLATED at boot: %s. A whale is on BOTH "
            "the live and paper rosters. The paper sim read-time subtract still "
            "prevents double-copy; operator must reconcile the rosters. Engine "
            "continues.", e,
        )
        return False
    except Exception as e:  # noqa: BLE001 — a read hiccup must not brick boot
        lg.warning("poly_kalshi roster invariant boot-check skipped (%s)", e)
        return True
