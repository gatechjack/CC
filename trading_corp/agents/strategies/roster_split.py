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
from datetime import datetime, timezone
from typing import Any, Iterable

_log = logging.getLogger(__name__)

# agent_state actors/keys for the two rosters (defaults; overridable per call).
LIVE_ACTOR = "poly_kalshi_mlb"
LIVE_KEY = "live_whales"
PAPER_ACTOR = "polymarket_copy_trader"
PAPER_KEY = "selected_whales"
PIN_KEY = "pinned_whales"          # paper eviction-exempt pins; the §1.5 3rd move key

# Keys under which a whale's wallet may be stored across the roster shapes.
# selected/pinned entries carry ``wallet``; watch_only entries carry
# ``proxy_wallet``. Bare-string entries are the wallet itself.
_WALLET_FIELDS = ("wallet", "proxy_wallet")


def wallet_of(entry: Any) -> str:
    """Lowercased wallet for a SINGLE roster entry (dict or bare string); '' if none.

    The single-entry counterpart of `extract_wallets` — same identity rules, so
    filtering (`wallet_of(x) != w`) and the disjointness set use one source of truth.
    """
    if isinstance(entry, dict):
        for field in _WALLET_FIELDS:
            w = entry.get(field)
            if w:
                return str(w).strip().lower()
        return ""
    if isinstance(entry, str):
        return entry.strip().lower()
    return ""


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
        w = wallet_of(v)
        if w:
            out.add(w)
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


# ── Atomic cross-roster moves (CP4) ─────────────────────────────────────
# Promote (paper->live) and Demote (live->paper) are each ONE atomic 3-key
# transaction via db.set_agent_state_multi. MANUAL operator actions only — never
# auto-called. Endpoints in web/routes.py are thin wrappers over these.


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_roster_list(actor: str, key: str, db_url: str) -> list:
    from trading_corp.persistence.db import load_agent_state
    rec = load_agent_state(actor, key, db_url=db_url)
    return list(rec[0]) if rec and isinstance(rec[0], list) else []


def _find_entry(entries: list, wallet_lower: str) -> dict:
    """Return the dict entry matching `wallet_lower`, or {} if not found / bare-str."""
    for x in entries:
        if wallet_of(x) == wallet_lower:
            return x if isinstance(x, dict) else {"wallet": wallet_lower}
    return {}


def promote_whale_to_live(
    wallet: str,
    db_url: str = "sqlite:///data/trading_corp.db",
    *,
    logger_agent: Any = None,
    source: str = "dashboard_button",
) -> dict:
    """PAPER -> LIVE promote: flatten the paper book, then ONE atomic 3-key move.

    Steps:
      1. FLATTEN-ON-PROMOTE — close the whale's open PAPER positions by REUSING
         `polymarket_copy_trader.force_close_whale_positions` (synthetic sells at
         mark; also resets the whale_state slot so a future move won't replay
         history). No new flatten logic.
      2. ONE `set_agent_state_multi` transaction: +live_whales, -selected_whales,
         -pinned_whales. Removing from pins is the §1.5 fix — a still-pinned whale
         would be silently re-added to paper by the weekly refresh.
      3. Assert `live ∩ paper == ∅`.

    Manual operator action — never auto-called. Returns a summary dict.
    """
    from trading_corp.persistence.db import set_agent_state_multi
    from trading_corp.agents.strategies.polymarket_copy_trader import (
        force_close_whale_positions,
    )

    w = str(wallet).strip().lower()
    sel = _load_roster_list(PAPER_ACTOR, PAPER_KEY, db_url)
    pin = _load_roster_list(PAPER_ACTOR, PIN_KEY, db_url)
    live = _load_roster_list(LIVE_ACTOR, LIVE_KEY, db_url)
    meta = _find_entry(sel, w) or _find_entry(pin, w) or _find_entry(live, w)
    user_name = str(meta.get("user_name") or "")
    category = str(meta.get("category") or meta.get("best_category") or "")

    # 1) Flatten the whale's open paper book (reuse the existing path).
    close_summary = force_close_whale_positions(
        w, db_url=db_url, logger_agent=logger_agent, reason="promoted_to_live",
    )

    # 2) Build the 3-key after-states.
    sel_after = [x for x in sel if wallet_of(x) != w]
    pin_after = [x for x in pin if wallet_of(x) != w]
    live_after = live if any(wallet_of(x) == w for x in live) else live + [{
        "wallet": w, "user_name": user_name, "category": category,
        "promoted_iso": _now_iso(), "source": source,
    }]

    # 3) ONE atomic transaction across all three keys.
    set_agent_state_multi([
        (PAPER_ACTOR, PAPER_KEY, sel_after),
        (PAPER_ACTOR, PIN_KEY, pin_after),
        (LIVE_ACTOR, LIVE_KEY, live_after),
    ], db_url=db_url)

    # 4) Invariant holds after the move.
    assert_disjoint(extract_wallets(live_after), extract_wallets(sel_after))
    return {
        "wallet": w, "user_name": user_name,
        "n_paper_closed": int(close_summary.get("n_closed", 0) or 0),
    }


def demote_whale_to_paper(
    wallet: str,
    db_url: str = "sqlite:///data/trading_corp.db",
    *,
    logger_agent: Any = None,
    source: str = "dashboard_button",
) -> dict:
    """LIVE -> PAPER demote: ONE atomic 3-key move. RIDE-TO-SETTLEMENT.

    Move: -live_whales, +selected_whales, +pinned_whales (re-pin so the whale is
    eviction-safe in paper again). Then assert `live ∩ paper == ∅`.

    RIDE-TO-SETTLEMENT — deliberately does **NO** live-broker action and **NO**
    force-flatten. Any open live position rides to natural settlement: the mark
    poller (`poly_kalshi_marks._fetch_open_positions`) and the settlement sweep
    (`poly_kalshi_copy_trader.run_settlement_sweep`) are POSITION/SETTLEMENT-driven,
    not roster-driven, so an off-roster position is still marked, still settles,
    and still books to the live division. Demote only stops NEW-entry detection.

    Manual operator action — never auto-called. Returns a summary dict.
    """
    from trading_corp.persistence.db import set_agent_state_multi

    w = str(wallet).strip().lower()
    live = _load_roster_list(LIVE_ACTOR, LIVE_KEY, db_url)
    sel = _load_roster_list(PAPER_ACTOR, PAPER_KEY, db_url)
    pin = _load_roster_list(PAPER_ACTOR, PIN_KEY, db_url)
    meta = _find_entry(live, w) or _find_entry(sel, w)
    user_name = str(meta.get("user_name") or "")
    category = str(meta.get("category") or "")

    live_after = [x for x in live if wallet_of(x) != w]
    entry = {
        "wallet": w, "user_name": user_name, "category": category,
        "demoted_iso": _now_iso(), "source": source,
    }
    sel_after = sel if any(wallet_of(x) == w for x in sel) else sel + [entry]
    pin_after = pin if any(wallet_of(x) == w for x in pin) else pin + [entry]

    set_agent_state_multi([
        (LIVE_ACTOR, LIVE_KEY, live_after),
        (PAPER_ACTOR, PAPER_KEY, sel_after),
        (PAPER_ACTOR, PIN_KEY, pin_after),
    ], db_url=db_url)

    assert_disjoint(extract_wallets(live_after), extract_wallets(sel_after))
    return {"wallet": w, "user_name": user_name}
