"""Unified per-asset PMCC decision record — freshness + precedence.

ONE current-status record per underlying, persisted in `agent_state` under
(agent="pmcc_robinhood", key="latest_decision:{SYMBOL}"):

    {symbol, status, source: 'scan'|'expert'|'executed', computed_at,
     urgency, confidence, summary, rationale, warnings}

Both the tile status badge (web/data.py) and the Expert Analysis panel
(web/routes.py) read this record so they can no longer disagree. The writers
are the POST-OPEN actionable scan (`source='scan'`, one per analyzed symbol),
a manual Expert "Re-analyze" (`source='expert'`), and a completed dashboard
execution (`source='executed'` — a filled roll/close consuming its own
decision). Pre-open triage writes NOTHING here.

Precedence — the crux:
  - An 'expert' write ALWAYS overwrites (deliberate, trusted source).
  - An 'executed' write ALSO always overwrites as an incoming write (a terminal
    fact: the acted-on recommendation is DONE, so it consumes whatever it acted
    on — even a fresh expert ROLL SHORT). BUT as a STORED record it is treated
    exactly like a scan verdict, NOT like a sticky expert: a later scan freely
    overwrites it. That asymmetry is the whole point — the tile flips to HOLD
    immediately, yet the very next scan can re-raise a signal if the position we
    just rolled moves, so there is no 8h blind spot on it.
  - A 'scan' write overwrites the current record if it is absent, itself
    scan-sourced, 'executed'-sourced, or a STALE (>= staleness_hours) expert
    verdict — so a scheduled scan (10:30 / 15:00 terminal / …) never clobbers a
    still-fresh manual Expert. Past the staleness window the expert verdict ages
    out and the next scan may repopulate it (the window doubles as the "session"
    boundary).

Everything here is read-only w.r.t. execution/DB-position state — it only
touches the generic `agent_state` key/value store. `classify_freshness` is a
pure function (no I/O) so the render logic is trivially unit-testable.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

_AGENT = "pmcc_robinhood"
_KEY_PREFIX = "latest_decision:"

# Default staleness / "session" window in hours. Overridable via
# strategies.yaml `robinhood_pmcc.tile_status.staleness_hours`.
DEFAULT_STALENESS_HOURS = 8.0


def decision_key(symbol: str) -> str:
    """agent_state key for a symbol's latest decision (symbol-normalized)."""
    return f"{_KEY_PREFIX}{(symbol or '').upper()}"


# ── time helpers (accept datetime OR ISO string; tz-naive treated as UTC) ────

def _parse_ts(ts: Any) -> datetime | None:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _age_hours(computed_at: Any, now: Any) -> float | None:
    a, b = _parse_ts(computed_at), _parse_ts(now)
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 3600.0


def _is_stale(computed_at: Any, now: Any, staleness_hours: float) -> bool:
    age = _age_hours(computed_at, now)
    # Unparseable timestamp => treat as stale (fail safe: a scan may repopulate).
    return age is None or age >= float(staleness_hours)


# ── pure freshness classifier (drives BOTH tile badge and Expert panel) ─────

def classify_freshness(
    record: Any, now: Any, staleness_hours: float = DEFAULT_STALENESS_HOURS
) -> str:
    """Classify a decision record as 'fresh' | 'stale' | 'none' (pure).

      - 'none'  : no decision this session (record is None/empty/statusless)
                  -> tile "awaiting scan / no signal".
      - 'stale' : has a decision but computed_at is >= staleness_hours old
                  (or unparseable) -> muted "stale —" badge / "stale as of Xh".
      - 'fresh' : computed_at is < staleness_hours old -> show the badge/text.

    `now` may be a datetime or ISO string; `staleness_hours` is configurable.
    """
    if not isinstance(record, dict) or not record.get("status"):
        return "none"
    return "stale" if _is_stale(record.get("computed_at"), now, staleness_hours) else "fresh"


def age_hours(record: Any, now: Any) -> float | None:
    """Age of a record in hours (None if unknown) — for the 'stale as of Xh' note."""
    if not isinstance(record, dict):
        return None
    return _age_hours(record.get("computed_at"), now)


# ── persistence (thin wrappers over the generic agent_state store) ──────────

def load_decision(symbol: str, *, db_url: str) -> dict | None:
    """Return the stored decision dict for `symbol`, or None. Never raises."""
    from trading_corp.persistence import db
    try:
        loaded = db.load_agent_state(_AGENT, decision_key(symbol), db_url=db_url)
    except Exception as e:  # noqa: BLE001 — a status read must never break a render
        log.warning("load_decision(%s): %s", symbol, e)
        return None
    if not loaded:
        return None
    value = loaded[0]
    return value if isinstance(value, dict) else None


def should_write(
    current: Any, source: str, computed_at: Any,
    staleness_hours: float = DEFAULT_STALENESS_HOURS,
) -> bool:
    """Pure precedence predicate (no I/O) — see module docstring.

    True iff a write with `source` at `computed_at` should overwrite `current`.
    """
    src = (source or "").lower()
    if src in ("expert", "executed"):
        # 'expert' = trusted manual source; 'executed' = a terminal fill that
        # must consume the decision it acted on. Both always win as an INCOMING
        # write. ('executed' is only scan-overwritable as a STORED record — see
        # the non-expert branch below, which treats current.source=='executed'
        # like 'scan', so a later scan re-raises with no blind spot.)
        return True
    # scan: overwrite absent / scan- or executed-sourced / stale-expert;
    # protect only a still-fresh manual expert.
    if not isinstance(current, dict) or current.get("source") != "expert":
        return True
    return _is_stale(current.get("computed_at"), computed_at, staleness_hours)


def record_pmcc_decision(
    symbol: str,
    *,
    status: str,
    source: str,
    computed_at: str,
    db_url: str,
    urgency: str | None = None,
    confidence: float | None = None,
    summary: str | None = None,
    rationale: str | None = None,
    warnings: Any = None,
    target_delta_low: float | None = None,
    target_delta_high: float | None = None,
    target_dte: int | None = None,
    staleness_hours: float = DEFAULT_STALENESS_HOURS,
) -> bool:
    """Persist the latest decision for `symbol`, honouring precedence.

    Returns True if written, False if skipped (a fresh manual Expert was
    protected from a scan clobber). Never raises — a status-persist failure
    must not break the scan or the render.

    `target_delta_low`/`target_delta_high` (a δ BAND, not a point) + `target_dte`
    (2026-07-31, P1): the JUDGMENT's consent envelope. Persisted so the free,
    deterministic pricing refresh can select the concrete strike WITHIN the band
    without re-running the LLM. Additive + backward-compatible — a record written
    before this change simply lacks the keys, and `load_decision` readers fall
    back to config defaults (`None` → default δ/DTE).
    """
    from trading_corp.persistence import db
    current = load_decision(symbol, db_url=db_url)
    if not should_write(current, source, computed_at, staleness_hours):
        return False
    value = {
        "symbol": (symbol or "").upper(),
        "status": status,
        "source": (source or "").lower(),
        "computed_at": computed_at,
        "urgency": urgency,
        "confidence": confidence,
        "summary": summary,
        "rationale": rationale,
        "warnings": list(warnings) if warnings else [],
        "target_delta_low": target_delta_low,
        "target_delta_high": target_delta_high,
        "target_dte": target_dte,
    }
    try:
        db.set_agent_state(_AGENT, decision_key(symbol), value, db_url=db_url)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("record_pmcc_decision(%s): %s", symbol, e)
        return False


# ── Effective (post-gate) status — shared by the tile badge AND the Expert panel ────
# The stored `status` is the RAW judgment (written pre-order-build, pmcc_robinhood.py).
# Downstream gates can suppress the action AFTER that write — earnings buffer (B9),
# sparse-chain / illiquid / can't-price — WITHOUT a write-back, so the raw action and
# the effective actionable state diverge. The tile and the panel must show the EFFECTIVE
# state so they can never disagree. `effective_status` is a PURE function of already-
# fetched inputs (the raw action + the cheap earnings gate + the LIVE pricing
# buildability from the pmcc_pricing cache); it is computed at RENDER time (never a
# stale scan snapshot) so it always tracks the live pricing badge.

# Actions the agent can build + place from a stored verdict. roll_leap is advisory
# (a manual LEAP reallocation) — never an agent-placeable Approve.
PLACEABLE_ACTIONS = ("roll_short", "roll_short_early", "close_short", "open_short")
_NON_ACTIONS = ("", "hold", "watch", "none", "—")
# The operator-facing guidance text for an earnings-buffer suppression (kept verbatim
# from `PMCCAgent.last_roll_abort_reason` so the tile, the panel, and the log agree).
_EARNINGS_SUPPRESS_REASON = "earnings within the buffer — roll suppressed (let the short expire)"


def _status_display(action: str) -> str:
    return (action or "—").upper().replace("_", " ")


def effective_status(
    raw_action,
    *,
    earnings_state=None,      # 'blocked' | 'clear' | 'data_unavailable' | None
    earnings_reason=None,     # technical detail (for a tooltip); the label reason stays friendly
    buildable=None,           # True/False from the live pricing cache; None = not priced / unknown
    price_reason=None,        # estimate_reason from the cache when NOT buildable
    market_closed=False,      # options session closed → can't price/act now (NOT a suppression)
) -> dict:
    """Compute the EFFECTIVE post-gate status for a PMCC verdict (pure — no I/O).

    Returns a dict:
      raw_action : the stored judgment action (lower-cased).
      label      : the display label — the raw action ONLY when actually actionable;
                   else EARNINGS WINDOW / CAN'T PRICE / the raw label (market closed).
      reason     : one-line why-suppressed / why-not-actionable (None when actionable).
      detail     : optional technical detail (e.g. the earnings date) for a tooltip.
      actionable : buildable AND not hard-suppressed AND agent-placeable — drives the
                   panel Approve (the caller ANDs this with freshness for rolls).
      suppressed : a HARD gate blocked the action (earnings-in-buffer / can't-price).
      advisory   : roll_leap (a manual decision; never an agent Approve).
      kind       : none|hold|advisory|earnings|actionable|market_closed|cant_price|pending|other.

    A net-DEBIT roll is buildable → ACTIONABLE (presented per the best-price fix); it is
    NOT a suppression. Precedence: earnings > actionable(buildable) > market-closed >
    can't-price > pending. `buildable is True` is checked BEFORE market_closed so a
    concretely-priced build stays approvable regardless of the wall clock.
    """
    raw = (raw_action or "").strip().lower()
    out = {
        "raw_action": raw, "label": _status_display(raw), "reason": None, "detail": None,
        "actionable": False, "suppressed": False, "advisory": False, "kind": "other",
    }
    if raw in _NON_ACTIONS:
        out["kind"] = "hold" if raw in ("hold", "watch") else "none"
        return out
    if raw == "roll_leap":
        out["advisory"] = True
        out["kind"] = "advisory"
        out["reason"] = "LEAP roll is a manual decision — the agent will not place it"
        return out
    if raw in PLACEABLE_ACTIONS:
        # 1) HARD-SUPPRESS: earnings within the buffer (stable; independent of hours).
        if earnings_state == "blocked":
            out.update(label="EARNINGS WINDOW", suppressed=True, kind="earnings",
                       reason=_EARNINGS_SUPPRESS_REASON, detail=earnings_reason)
            return out
        # 2) ACTIONABLE: a concrete live build exists. Checked BEFORE market_closed so a
        #    priced build (incl. a net-debit roll) stays approvable regardless of hours.
        if buildable is True:
            out.update(actionable=True, kind="actionable")
            return out
        # 3) Options session closed — the judgment stands; you just can't price/act now.
        if market_closed:
            out.update(kind="market_closed",
                       reason=price_reason or "market closed — the roll prices at the 9:30 ET open")
            return out
        # 4) HARD-SUPPRESS: a live pricing attempt could not build (illiquid / sparse chain).
        if buildable is False:
            out.update(label="CAN'T PRICE", suppressed=True, kind="cant_price",
                       reason=price_reason or "the roll can't be priced right now (illiquid or a sparse chain)")
            return out
        # 5) Not yet priced — show the judgment, but not approvable without a live build.
        out["kind"] = "pending"
        return out
    return out
