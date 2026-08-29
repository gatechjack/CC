"""Prediction Markets -- BOOT-RECONCILE (Stage 3 R5.5): compare the durable journal against Kalshi's ACTUAL
portfolio at boot, and LATCH boot_reconcile_mismatch on ANY disagreement (fail-safe DISARMED until a human
clears it).

WHY IT EXISTS: the kill-switch defaults DISARMED on restart (R5) precisely so a human confirms reconciliation
before re-arming -- but the reconcile itself was never designed. R4's Journal seeds the O(1) budget counters
from the journal WITHOUT emitting (the seed half -- leg-corrected after the R5 review found the NO-leg
under-seed; NOT rebuilt here). R5 ships the boot_reconcile_mismatch LATCH as the seam (arm.py). This module
fills the MISSING half: the journal-vs-Kalshi POSITION comparison that decides `mismatch`.

SCOPE (Jack ruled): reconcile READS and COMPARES only. It NEVER places, cancels, arms, or adjusts a position.
The ONLY thing it ever writes is the disarm LATCH on mismatch (arm.latch_boot_reconcile_mismatch -> the
legacy agent_state control plane). If a mismatch needs fixing, a HUMAN fixes it (flatten on Kalshi / correct
the journal) then clears the latch with arm(require_latch_clear=True). A CLEAN reconcile writes NOTHING and
never arms (arming stays an explicit human act).

STRUCTURAL 'cannot place' (mirrors execution.py): this module imports NO broker. Kalshi positions come from a
CALLER-INJECTED zero-arg `fetch_positions` callable returning an iterable of position records; R7 injects the
real AUTHENTICATED pykalshi reader (that first real portfolio read = go-live-gate item 4, R7's -- NOT this
rung). A test asserts no broker symbol is in this module's namespace and that reconcile takes no broker object.

THE NO-LEG LENS -- a DOMAIN PROPERTY (the 5th instance; TRANSITION doc I). Kalshi exposes ONE SIGNED NET
position per ticker (`position_fp`): + = long YES, - = long NO (it auto-nets YES+NO on a ticker). The journal
denominates BY LEG (`outcome_leg` in {yes,no}, a POSITIVE contract count). So the comparison is over SIGNED
NET contracts per ticker, with each side's YES/NO denomination stated and proven in tests:
    journal_signed(T) = sum(+net if leg=='yes' else -net)   over the journal's net-open legs   (YES +, NO -)
    kalshi_signed(T)  = position_fp(T)                                                          (YES +, NO -)
A MAGNITUDE compare would PASS a side-flip (journal-NO-3 vs Kalshi-YES-3 both read magnitude 3); the SIGNED
compare FAILS it (-3 != +3). Tested by name.

RULINGS (Jack, 2026-08-29 -- STAGE3_PLAN_2026-08-28.md R5.5):
  R-a EXACT match, K=0        -- fixed sizing means one contract IS a whole position; a band masks the error.
  R-b journal-only (Kalshi flat) -> MISMATCH -> latch (likely settlement; a human confirms it booked).
  R-c kalshi-only  (journal flat) -> MISMATCH -> latch, FULL ACCOUNT (not ticker-scoped: ANY Kalshi position
      absent from the journal is a mismatch). CORRECT ONLY WHILE THE ACCOUNT IS PM-EXCLUSIVE -- a HARD R7
      precondition (co-tenant divisions OFF this account before the first arm; Jack's to do, not an agent's).
  R-d settlement drift        -> latch (first-live is few positions, human-watched). A journal settlement-close
      path is the real fix -- a FUTURE RUNG, filed not built.
  R-f COUNT-ONLY (dollars can't compare: Kalshi is mark-to-market, the journal is cost basis; fees are R7's
      balance-delta gate). Latch target = the reconciled (account_id, category); account-wide latching is
      revisited when a 2nd sub-division exists on one account.

Spec: reports/prediction_markets/STAGE3_PLAN_2026-08-28.md R5.5.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import arm, execution   # PM-package only; stdlib-at-import (execution pulls kalshi_live's PURE builders, no broker)

# ── mismatch classifications ──────────────────────────────────────────────────
MATCH = "match"                    # equal signed net -- NOT emitted as a diff
JOURNAL_ONLY = "journal_only"      # R-b: journal holds it, Kalshi flat
KALSHI_ONLY = "kalshi_only"        # R-c: Kalshi holds it, journal flat (full-account: even a PM-untouched ticker)
COUNT_MISMATCH = "count_mismatch"  # both hold, signed nets differ -- INCLUDES a YES/NO SIDE-FLIP

_DETAIL_MAX = 6                    # cap the tickers named in the latch detail string (keep it terse)


def _field(p, name):
    return p.get(name) if isinstance(p, dict) else getattr(p, name, None)


def _to_int_contracts(v) -> int:
    """Parse a Kalshi position count -> SIGNED int. `position_fp` is a signed contract count as a fixed-point
    STRING (or number); contracts are whole, so round absorbs a '3.00'. The SIGN carries the side (+ long YES,
    - long NO). Raises on un-parseable input (reconcile_account fail-safe-latches on it)."""
    return int(round(float(v)))


def journal_signed_positions(conn, account_id: str) -> dict:
    """The journal's net-open holdings for `account_id` as SIGNED NET contracts per ticker (YES leg -> +,
    NO leg -> -). REUSES execution.open_positions_needing_manual_exit -- the leg-aware net (filled entries -
    filled exits per (ticker, leg), net>0), the single source of truth for 'what the journal says we hold' --
    and folds it ACCOUNT-WIDE (across every category on the account) to signed-per-ticker. Zero-net tickers
    are dropped. ** NO-LEG LENS: a NO leg contributes a NEGATIVE signed count (matching Kalshi's position_fp<0
    == long NO); a magnitude fold would erase the side. **"""
    signed: dict = {}
    for p in execution.open_positions_needing_manual_exit(conn):
        if p["account_id"] != account_id:
            continue
        n = int(round(float(p["net_open_contracts"])))
        signed[p["ticker"]] = signed.get(p["ticker"], 0) + (n if p["outcome_leg"] == "yes" else -n)
    return {t: s for t, s in signed.items() if s != 0}


def kalshi_signed_positions(positions) -> dict:
    """Normalize an injected Kalshi positions iterable -> {ticker: signed_int}. Each item is a mapping or an
    object exposing `ticker` and `position_fp` (pykalshi PositionModel: a SIGNED contract count, + long YES /
    - long NO). Flats are dropped (Kalshi omits them anyway). NO broker is imported here -- the CALLER supplies
    the data (R7 injects the authenticated read). Raises on a record missing ticker/position_fp."""
    signed: dict = {}
    for p in positions:
        ticker, raw = _field(p, "ticker"), _field(p, "position_fp")
        if ticker is None or raw is None:
            raise ValueError("kalshi position record missing ticker/position_fp: %r" % (p,))
        s = _to_int_contracts(raw)
        if s != 0:
            signed[str(ticker)] = signed.get(str(ticker), 0) + s
    return signed


@dataclass(frozen=True)
class TickerDiff:
    ticker: str
    journal_signed: int
    kalshi_signed: int
    classification: str


@dataclass(frozen=True)
class ReconcileResult:
    account_id: str
    category: str
    reconciled: bool
    diffs: tuple = ()
    n_journal_tickers: int = 0
    n_kalshi_tickers: int = 0
    latched: bool = False
    read_error: str | None = None


def compare(journal_signed: dict, kalshi_signed: dict) -> list:
    """Pure SIGNED-NET comparison over the UNION of tickers, EXACT (K=0, ruling R-a). Returns the list of
    MISMATCH TickerDiffs (empty == reconciled). Classifies journal_only (R-b) / kalshi_only (R-c, full-account)
    / count_mismatch. A YES/NO SIDE-FLIP (journal -3 vs Kalshi +3) lands as count_mismatch BECAUSE the compare
    is SIGNED -- a magnitude compare would call it equal and pass a real reversal silently."""
    diffs = []
    for t in sorted(set(journal_signed) | set(kalshi_signed)):
        j, k = journal_signed.get(t, 0), kalshi_signed.get(t, 0)
        if j == k:
            continue
        cls = JOURNAL_ONLY if k == 0 else (KALSHI_ONLY if j == 0 else COUNT_MISMATCH)
        diffs.append(TickerDiff(t, j, k, cls))
    return diffs


def _detail(account_id: str, diffs) -> str:
    head = "; ".join("%s j=%d k=%d [%s]" % (d.ticker, d.journal_signed, d.kalshi_signed, d.classification)
                     for d in diffs[:_DETAIL_MAX])
    more = "" if len(diffs) <= _DETAIL_MAX else " (+%d more)" % (len(diffs) - _DETAIL_MAX)
    return "boot-reconcile mismatch on %s: %d ticker(s) disagree: %s%s" % (account_id, len(diffs), head, more)


def reconcile_account(conn, account_id: str, category: str, *, fetch_positions,
                      legacy_db_path=None, latch_on_mismatch: bool = True) -> ReconcileResult:
    """Boot-reconcile ONE account's Kalshi portfolio against the journal; LATCH boot_reconcile_mismatch on ANY
    disagreement (fail-safe DISARMED until a human clears it via arm(require_latch_clear=True)).

    `fetch_positions`: a CALLER-INJECTED zero-arg callable returning an iterable of Kalshi position records
    (mappings/objects with `ticker` + signed `position_fp`). This module imports NO broker; R7 injects the
    real authenticated reader. R5.5 tests inject a fake. `latch_on_mismatch=False` runs the pure comparison
    with NO side effect (for tests that assert classification without touching the arm control plane).

    FULL-ACCOUNT (R-c): the Kalshi side is the WHOLE account book -- ANY position absent from the journal is a
    mismatch (correct only while the account is PM-EXCLUSIVE, a hard R7 precondition). COUNT-ONLY (R-f), EXACT
    K=0 (R-a). On mismatch -> latch the reconciled (account_id, category) (R-f). CLEAN -> writes NOTHING.

    FAIL-SAFE: a portfolio READ/PARSE failure is NOT 'reconciled' -- it latches (cannot confirm -> stay
    disarmed). The JOURNAL read is our OWN DB and raises LOUDLY on failure (a system fault, not a mismatch)."""
    j = journal_signed_positions(conn, account_id)          # our own DB: loud on failure (never a silent 'reconciled')
    try:
        k = kalshi_signed_positions(fetch_positions())       # external venue read/parse: fail-safe-latched
    except Exception as e:                                    # noqa: BLE001 -- fail-safe is deliberate (money gate)
        if latch_on_mismatch:
            arm.latch_boot_reconcile_mismatch(
                account_id, category, detail="portfolio read FAILED (cannot reconcile): %r" % e,
                legacy_db_path=legacy_db_path)
        return ReconcileResult(account_id, category, reconciled=False, n_journal_tickers=len(j),
                               latched=latch_on_mismatch, read_error=repr(e))
    diffs = compare(j, k)
    if not diffs:
        return ReconcileResult(account_id, category, reconciled=True,
                               n_journal_tickers=len(j), n_kalshi_tickers=len(k))
    if latch_on_mismatch:
        arm.latch_boot_reconcile_mismatch(account_id, category, detail=_detail(account_id, diffs),
                                          legacy_db_path=legacy_db_path)
    return ReconcileResult(account_id, category, reconciled=False, diffs=tuple(diffs),
                           n_journal_tickers=len(j), n_kalshi_tickers=len(k), latched=latch_on_mismatch)
