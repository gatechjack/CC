"""Prediction Markets -- the ARM / KILL-SWITCH control plane (Stage 3 R5).

The money gate that makes R7 survivable. It REUSES the platform's persistent agent_state store -- the
SAME mechanism StrategyState.persist_halt and the MACE `entry_halt` latch use (NOT a second mechanism;
Jack ruling #3 / the migration-010 comment) -- with ONE deliberate INVERSION:

  ** DEFAULT IS DISARMED. ** StrategyState + the MACE latch treat an ABSENT row as "running / NOT halted"
  (halt is the exception). A MONEY-ARMING gate must be the opposite: an absent OR unreadable arm row means
  DISARMED (fail-safe OFF). Arming is an explicit, opt-in, HUMAN act. A restart -- exactly when the
  in-memory counters + high-water marks are gone -- comes up DISARMED and STAYS disarmed until a human
  arms (which forces boot-reconciliation to be a conscious step, not an accident of process lifecycle).

WHERE the state lives (and why this does NOT collapse PM isolation):
  Arm state is a row in the LEGACY agent_state table (data/trading_corp.db) under the PM-namespaced actor
  'pm_live'. The PM-DB isolation guarantee -- prediction_markets.db NEVER opens trading_corp.db -- is
  UNTOUCHED: prediction_markets.db still refuses the legacy file (`_assert_not_legacy`). arm.py is a
  SEPARATE, narrow BRIDGE for the arming control plane ONLY:
    * READ  -> a mode=ro sqlite open of trading_corp.db (the rosters.read_agent_state precedent:
               read-only, WAL, no write, no lock contention). Used by the chokepoint (engine side) and
               safe for pm_web to DISPLAY. Absent file / absent table / bad json / ANY error -> DISARMED.
    * WRITE -> LAZILY imports trading_corp.persistence.db.set_agent_state (the engine's own writer -- the
               migration-010-sanctioned reuse). The lazy import means importing arm.py for READ pulls NO
               engine code, so execution.py stays stdlib-only-at-import and pm_web stays "imports only
               fastapi + the PM package". The WRITE path is only ever reached from ENGINE-SIDE callers:
               the `pm_cli live-*` operator commands, a future engine-web /pm/arm route, and the R7 engine
               driver's auto-disarm. pm_web itself must never call a write helper (it cannot -- isolation
               -- and has no reason to; it may only display via read_status).

SCOPES: a GLOBAL master ('arm:global') AND a per-sub-division ('arm:{account_id}:{category}'). A
sub-division is ARMED only if BOTH the global master AND its own row are armed. Either off -> disarmed.
(One master kill disarms everything; a single sub can be armed independently for the first-live period.)

LATCHING: an AUTO-disarm (auth failure / consecutive order errors / count ceiling / boot-reconcile
mismatch) writes armed=False, latched=True. A latched row STAYS disarmed until a HUMAN clears it: arm()
clears the latch (an explicit human arm IS the acknowledgement), and the CLI refuses to arm a latched row
without --clear-latch so the operator must SEE the trigger before re-arming.

Spec: reports/prediction_markets/STAGE3_PLAN_2026-08-28.md R5.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
from dataclasses import dataclass

# The agent_state ACTOR namespace for every PM-live arm row. Distinct from 'strategy_state' (engine
# strategy halts) and 'robinhood_mace' (the MACE latch) so PM arming can NEVER collide with them.
PM_LIVE_ACTOR = "pm_live"
GLOBAL_KEY = "arm:global"

# Auto-disarm trigger tags (the four latching triggers -- Jack's R5 list).
AUTO_AUTH_FAILURE = "auth_failure"
AUTO_CONSECUTIVE_ERRORS = "consecutive_order_errors"
AUTO_COUNT_CEILING = "count_ceiling"
AUTO_BOOT_RECONCILE = "boot_reconcile_mismatch"

# READ default resolves the same legacy DB the rest of the PM package reads read-only. Overridable via
# env (tests / a non-default box layout) WITHOUT threading a path through every call site.
_LEGACY_DB_DEFAULT = "data/trading_corp.db"


def sub_key(account_id: str, category: str) -> str:
    return "arm:%s:%s" % (account_id, category)


def resolve_legacy_db_path(legacy_db_path: str | None = None) -> str:
    return legacy_db_path or os.environ.get("PM_LEGACY_DB_PATH") or _LEGACY_DB_DEFAULT


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


# ── the verdict ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ArmVerdict:
    armed: bool
    scope: str                       # 'both' (armed) | 'global' | 'sub' (which scope is OFF)
    reason: str | None = None
    latched: bool = False
    auto_trigger: str | None = None
    manual_exit_required: bool = False


# ── READ (mode=ro; FAIL-SAFE DISARMED on any error) ──────────────────────────
def _read_row_status(legacy_db_path: str | None, key: str):
    """mode=ro read of agent_state(pm_live, key) that DISTINGUISHES a definitive ABSENCE from an
    INDETERMINATE read. Returns (status, row): 'ok' -> row is a dict; 'absent' -> there is definitively no
    persisted state (no file, or the table read cleanly and this scope has no row); 'error' -> the state
    could NOT be determined (locked DB / missing table / io error / corrupt-or-non-dict json).

    Why the distinction matters (the latch-clear guard): the READ path (read_arm_verdict) treats BOTH
    'absent' and 'error' as DISARMED (fail-safe OFF -- correct, an unreadable arm state must never place).
    But the LATCH-CLEAR guard needs more: it must let a cold-start ('absent') scope arm, yet must NEVER
    clear a latch it cannot read ('error'). Collapsing the two (the old `_load_row` -> None) let a transient
    read failure skip the latch guard and re-arm a killed account. `_scope_latched_failsafe` uses this."""
    path = resolve_legacy_db_path(legacy_db_path)
    if not os.path.exists(path):
        return ("absent", None)                         # no legacy DB yet -> genuinely no persisted latch
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(path), uri=True)
        try:
            r = conn.execute("SELECT value_json FROM agent_state WHERE agent=? AND key=?",
                             (PM_LIVE_ACTOR, key)).fetchone()
        finally:
            conn.close()
    except Exception:
        return ("error", None)                          # locked / no table / io error -> INDETERMINATE
    if not r or r[0] is None:
        return ("absent", None)                         # table read cleanly; this scope simply has no row
    try:
        v = json.loads(r[0])
    except (TypeError, ValueError):
        return ("error", None)                          # corrupt json -> INDETERMINATE (never 'no latch')
    if not isinstance(v, dict):
        return ("error", None)                          # not our shape -> INDETERMINATE
    return ("ok", v)


def _load_row(legacy_db_path: str | None, key: str) -> dict | None:
    """mode=ro read of agent_state(pm_live, key) -> dict, or None. FAIL-SAFE: a missing file, a missing
    agent_state table, malformed json, or ANY error returns None (the caller treats None as DISARMED).
    Both ABSENT and INDETERMINATE collapse to None here -- correct for the READ/verdict path, which is
    DISARMED either way. The latch-clear guard uses `_scope_latched_failsafe` instead (it must NOT)."""
    status, row = _read_row_status(legacy_db_path, key)
    return row if status == "ok" else None


def _scope_latched_failsafe(legacy_db_path: str | None, key: str):
    """(latched, auto_trigger, manual_exit_required) for the latch-clear guard. FAIL-SAFE: an INDETERMINATE
    read ('error') is treated as LATCHED (never clear a latch we cannot confirm is absent); a definitively
    ABSENT scope ('absent', e.g. cold start) is NOT latched (so a first arm still works); an 'ok' row
    reports its real latch. This closes the hole where a transient read failure skipped the guard and let a
    killed account be re-armed without the human --clear-latch."""
    status, row = _read_row_status(legacy_db_path, key)
    if status == "error":
        return (True, "unreadable_state", True)         # cannot confirm unlatched -> refuse to clear (worst-case flags)
    if status == "absent":
        return (False, None, False)                     # no persisted latch to clear (cold start / never latched)
    return (bool(row.get("latched")), row.get("auto_trigger"), bool(row.get("manual_exit_required")))


def _row_armed(row) -> bool:
    # STRICT: only a canonical JSON `true` (our own write) reads as ARMED. A truthy-but-non-True value
    # (1, "true", a hand-edited/partial row) falls to DISARMED -- the fail-safe inversion, hardened.
    return isinstance(row, dict) and row.get("armed") is True


def current_row(account_id: str | None = None, category: str | None = None, *,
                global_: bool = False, legacy_db_path: str | None = None) -> dict | None:
    """The raw stored row for one scope (or None). For the CLI's latch check + status display."""
    return _load_row(legacy_db_path, GLOBAL_KEY if global_ else sub_key(account_id, category))


def read_arm_verdict(account_id: str, category: str, *, legacy_db_path: str | None = None) -> ArmVerdict:
    """ARMED only if BOTH the global master AND the (account, category) row are armed. Global is checked
    FIRST -- a global disarm short-circuits (one master kill stops everything). Any read failure anywhere
    degrades to DISARMED (fail-safe). This is the authority the chokepoint's gate-1 disarm check reads."""
    g = _load_row(legacy_db_path, GLOBAL_KEY)
    if not _row_armed(g):
        gg = g or {}
        return ArmVerdict(False, "global", gg.get("reason") if g else "absent_global",
                          latched=bool(gg.get("latched")), auto_trigger=gg.get("auto_trigger"),
                          manual_exit_required=bool(gg.get("manual_exit_required")))
    s = _load_row(legacy_db_path, sub_key(account_id, category))
    if not _row_armed(s):
        ss = s or {}
        return ArmVerdict(False, "sub", ss.get("reason") if s else "absent_sub",
                          latched=bool(ss.get("latched")), auto_trigger=ss.get("auto_trigger"),
                          manual_exit_required=bool(ss.get("manual_exit_required")))
    return ArmVerdict(True, "both", None)


def is_armed(account_id: str, category: str, *, legacy_db_path: str | None = None) -> bool:
    try:
        return read_arm_verdict(account_id, category, legacy_db_path=legacy_db_path).armed
    except Exception:
        return False


def read_status(account_id: str | None = None, category: str | None = None, *,
                legacy_db_path: str | None = None) -> dict:
    """A read-only snapshot for the CLI + a pm_web DISPLAY (never a write). Shows the global master, the
    sub row (if account/category given), and the EFFECTIVE verdict (global AND sub)."""
    g = _load_row(legacy_db_path, GLOBAL_KEY)
    out: dict = {"actor": PM_LIVE_ACTOR, "global": g, "global_armed": _row_armed(g)}
    if account_id and category:
        v = read_arm_verdict(account_id, category, legacy_db_path=legacy_db_path)
        out.update({"account_id": account_id, "category": category, "sub": current_row(account_id, category, legacy_db_path=legacy_db_path),
                    "effective_armed": v.armed, "blocking_scope": None if v.armed else v.scope,
                    "reason": v.reason, "latched": v.latched, "auto_trigger": v.auto_trigger,
                    "manual_exit_required": v.manual_exit_required})
    return out


# ── WRITE (ENGINE / CLI side only; lazy engine import) ───────────────────────
def _write(key: str, value: dict, *, legacy_db_path: str | None = None) -> None:
    """Reuse the engine's set_agent_state (the migration-010-sanctioned mechanism). Lazily imported so
    importing arm.py for READ pulls NO engine code. Reached ONLY from engine/CLI callers."""
    from trading_corp.persistence.db import set_agent_state   # lazy: engine-side only
    set_agent_state(PM_LIVE_ACTOR, key, value, db_url=resolve_legacy_db_path(legacy_db_path))


def _armed_value(*, reason: str, source: str, by: str | None) -> dict:
    return {"armed": True, "latched": False, "auto_trigger": None, "manual_exit_required": False,
            "reason": reason, "source": source, "by": by, "ts": _now_iso()}


class LatchedError(RuntimeError):
    """Raised when arm() is asked to arm a scope whose auto-disarm LATCH is set, WITHOUT explicit
    acknowledgement (require_latch_clear=True / CLI --clear-latch). Keeps the human-ack STRUCTURAL, not
    CLI-only -- an engine-side caller that forgets the flag FAILS LOUD instead of silently re-arming."""


def arm(account_id: str | None = None, category: str | None = None, *, by: str | None = None,
        source: str = "cli", global_: bool = False, require_latch_clear: bool = False,
        legacy_db_path: str | None = None) -> None:
    """ARM a scope. A LATCHED auto-disarm (auth failure / error storm / count ceiling / boot mismatch)
    can be cleared ONLY here and ONLY with require_latch_clear=True -- the ONE structural place a latch is
    ever cleared, so an operator MUST have acknowledged the trigger. Any other caller (a bug, an
    over-eager R7 auto-recover) that arms a latched row without the flag raises LatchedError, never
    silently re-arms. Arming a NON-latched scope needs no flag."""
    key = GLOBAL_KEY if global_ else sub_key(account_id, category)
    if not require_latch_clear:
        # FAIL-SAFE latch guard: refuse if the scope is latched OR if the latch state cannot be read
        # (locked/corrupt). The old guard read `_load_row` and skipped on None -- but None also means an
        # INDETERMINATE read, so a transient read failure let a killed account re-arm without the ack. Now
        # only a definitively-ABSENT (cold-start) scope arms without the flag.
        latched, trigger, _mx = _scope_latched_failsafe(legacy_db_path, key)
        if latched:
            raise LatchedError(
                "refusing to arm %s: a LATCHED auto-disarm (%s) is set or the latch state is UNREADABLE; a "
                "human must acknowledge it (require_latch_clear=True / CLI --clear-latch) before arming"
                % (key, trigger))
    _write(key, _armed_value(reason="operator_arm", source=source, by=by), legacy_db_path=legacy_db_path)


def disarm(account_id: str | None = None, category: str | None = None, *, reason: str = "operator_disarm",
           by: str | None = None, source: str = "cli", global_: bool = False,
           legacy_db_path: str | None = None) -> None:
    """DISARM a scope (a MANUAL kill: a human can re-arm freely). Sets the PERSISTED state, never an
    in-memory flag -- it survives a restart and blocks the next order and every order after. It PRESERVES
    an existing auto-disarm LATCH (a manual disarm on top of an auth-failure latch keeps latched=True +
    its trigger + manual-exit flag), so the invariant holds: ONLY arm(require_latch_clear=True) clears a
    latch. A manual disarm of a non-latched scope stays non-latched."""
    key = GLOBAL_KEY if global_ else sub_key(account_id, category)
    # FAIL-SAFE latch preservation: an INDETERMINATE read must NOT drop a latch (the old `_load_row or {}`
    # coalesced a failed read to {} -> latched=False -> the latch silently vanished, letting a later arm skip
    # the ack). `_scope_latched_failsafe` returns latched=True on an unreadable row, so a manual disarm can
    # never clear an auto-disarm latch it could not read. armed is ALWAYS False here (disarm never arms).
    latched, trigger, manual_exit = _scope_latched_failsafe(legacy_db_path, key)
    _write(key, {"armed": False, "latched": latched,
                 "auto_trigger": trigger if latched else None,
                 "manual_exit_required": manual_exit if latched else False,
                 "reason": reason, "source": source, "by": by, "ts": _now_iso()},
           legacy_db_path=legacy_db_path)


def auto_disarm(account_id: str, category: str, *, trigger: str, detail: str | None = None,
                manual_exit_required: bool = False, legacy_db_path: str | None = None) -> None:
    """A LATCHING auto-disarm (armed=False, latched=True). Stays disarmed until a HUMAN arm() clears it.
    The four convenience wrappers below classify the trigger; the R7 engine driver calls them."""
    _write(sub_key(account_id, category),
           {"armed": False, "latched": True, "auto_trigger": trigger,
            "manual_exit_required": bool(manual_exit_required), "reason": detail or ("auto:%s" % trigger),
            "source": "auto_disarm", "by": None, "ts": _now_iso()},
           legacy_db_path=legacy_db_path)


# ── the four latching auto-disarm triggers (the legacy executor has NONE of the first three) ─────────
def latch_auth_failure(account_id: str, categories, *, detail: str | None = None,
                       legacy_db_path: str | None = None) -> None:
    """Order-path 401/403: DISARM every live sub-division on this ACCOUNT and FLAG open positions for
    MANUAL exit. Auth is dead -> the engine can no longer place the reduce_only exit either, so a human
    must flatten on Kalshi directly (Jack's ruling: auth failure disarms the account + flags positions)."""
    for cat in categories:
        auto_disarm(account_id, cat, trigger=AUTO_AUTH_FAILURE, detail=detail or "order-path 401/403",
                    manual_exit_required=True, legacy_db_path=legacy_db_path)


def latch_consecutive_errors(account_id: str, category: str, *, n: int, detail: str | None = None,
                             legacy_db_path: str | None = None) -> None:
    auto_disarm(account_id, category, trigger=AUTO_CONSECUTIVE_ERRORS,
                detail=detail or ("%d consecutive OrderPlacementError" % n), legacy_db_path=legacy_db_path)


def latch_count_ceiling(account_id: str, category: str, *, count: int, cap: int,
                        legacy_db_path: str | None = None) -> None:
    auto_disarm(account_id, category, trigger=AUTO_COUNT_CEILING,
                detail="orders/day ceiling %d>=%d" % (count, cap), legacy_db_path=legacy_db_path)


def latch_boot_reconcile_mismatch(account_id: str, category: str, *, detail: str | None = None,
                                  legacy_db_path: str | None = None) -> None:
    """The boot-reconcile MISMATCH latch. R5 ships the LATCH; the actual journal-vs-Kalshi-portfolio
    COMPARISON that decides `mismatch` needs the authenticated broker + a position-matching rule and is
    a RUNG OF ITS OWN (recommended slotted before R7). See the plan's R5 boot-reconcile scoping."""
    auto_disarm(account_id, category, trigger=AUTO_BOOT_RECONCILE,
                detail=detail or "journal vs Kalshi portfolio mismatch at boot", legacy_db_path=legacy_db_path)
