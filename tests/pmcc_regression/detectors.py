"""Structural pathology detectors for the PMCC Bucket-B build (Phase 0).

Each detector is a PURE function over a normalized recommendation record
(`RecRecord`). The same detectors run against:
  - the 157-row audit history (structural REGRESSION BASELINE), and
  - synthetic recommendation records constructed in later phases (acceptance
    that the targeted pathology is ABSENT post-fix). Phase 1 builds records from
    the code's ACTUAL proposed legs via `RecRecord.from_legs(...)`.

Nothing in `pmcc_robinhood.py` is imported or touched here.

Forward-compatible fields (`override_kind`, `old_leap_px`, `target_strike`,
`spot`, `has_new_leap`, `sold_leap`, `closed_short`) degrade safely to historical
semantics when absent (None).
"""
from __future__ import annotations

from dataclasses import dataclass

ROLL_TYPES = ("roll_short", "roll_leap")

# NYSE holidays inside the audit window 2026-05-01 .. 2026-07-21 (all FULL
# closures, verified against SPY: Memorial Day, Juneteenth, Independence-observed).
WINDOW_HOLIDAYS = frozenset({"2026-05-25", "2026-06-19", "2026-07-03"})

# Leg-action families (mirror pmcc_robinhood order-assembly actions).
FAMILY = {
    "roll_short_call_close": "roll_short", "roll_short_call_open": "roll_short",
    "roll_leap_close_short": "roll_leap", "roll_leap_close": "roll_leap",
    "roll_leap_open": "roll_leap", "roll_leap_open_short": "roll_leap",
    "open_leap": "open_pmcc", "open_short_call": "open_short",
    "close_short_urgent": "close_short", "close_leap_urgent": "close_all",
}
NEW_SHORT = {"roll_short_call_open", "roll_leap_open_short", "open_short_call"}
NEW_LEAP = {"roll_leap_open", "open_leap"}
CLOSE_SHORT = {"roll_leap_close_short", "roll_short_call_close", "close_short_urgent"}


def _f(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _b(v):
    if v in (True, "1", "true", "True"):
        return True
    if v in (False, "0", "false", "False"):
        return False
    return None


@dataclass
class RecRecord:
    """Normalized recommendation record. The enriched CSV columns are the schema;
    later phases build the same shape synthetically or via `from_legs`."""
    rec_type: str
    llm_action: str = ""
    old_strike: float | None = None
    new_strike: float | None = None
    old_exp: str = ""
    new_exp: str = ""
    new_dte: float | None = None
    new_delta: float | None = None
    net_cash_sh: float | None = None
    status: str = ""
    date: str = ""                     # ISO YYYY-MM-DD of the recommendation
    # forward-compatible (None/absent for older rows; set by synthetic records)
    override_kind: str | None = None   # "hold_override" | "net_debit_justified" | None
    old_leap_px: float | None = None
    target_strike: float | None = None
    spot: float | None = None
    has_new_leap: bool | None = None   # a new LEAP (roll_leap_open/open_leap) proposed?
    sold_leap: bool | None = None      # old LEAP sold (roll_leap_close) proposed?
    closed_short: bool | None = None   # existing short bought back?
    b4_subtype: str = ""               # "uncovered" | "fully_naked" | "" (precomputed hint)

    @classmethod
    def from_row(cls, row: dict) -> "RecRecord":
        return cls(
            rec_type=row.get("rec_type", ""),
            llm_action=(row.get("llm_action") or "").upper(),
            old_strike=_f(row.get("old_strike")),
            new_strike=_f(row.get("new_strike")),
            old_exp=row.get("old_exp") or "",
            new_exp=row.get("new_exp") or "",
            new_dte=_f(row.get("new_dte")),
            new_delta=_f(row.get("new_delta")),
            net_cash_sh=_f(row.get("net_cash_sh")),
            status=row.get("status") or "",
            date=(row.get("ts") or "")[:10],
            override_kind=(row.get("override_kind") or None),
            old_leap_px=_f(row.get("old_leap_px")),
            target_strike=_f(row.get("target_strike")),
            spot=_f(row.get("spot_parsed")),
            has_new_leap=_b(row.get("has_new_leap")),
            sold_leap=_b(row.get("sold_leap")),
            closed_short=_b(row.get("closed_short")),
            b4_subtype=row.get("b4_subtype") or "",
        )

    @classmethod
    def from_legs(cls, legs, *, llm_action="", override_kind=None, date="",
                  spot=None, target_strike=None) -> "RecRecord":
        """Normalize the CODE's actual proposed legs (Phase-1 acceptance).
        `legs` is an iterable of mappings shaped like `ProposedOrder.extra`:
        {'action': str, 'strike': float|None, 'expiration': str, 'price': float|None}."""
        legs = list(legs)
        aset = {(l.get("action") or "") for l in legs} - {""}
        fam = {FAMILY.get(a, "other") for a in aset}
        rec_type = ("roll_leap" if "roll_leap" in fam else
                    "roll_short" if "roll_short" in fam else
                    "open_pmcc" if "open_pmcc" in fam else
                    "cover_leap" if "open_short" in fam else
                    "close_short" if "close_short" in fam else
                    "close_all" if "close_all" in fam else "other")

        def field(action, key):
            for l in legs:
                if l.get("action") == action:
                    return l.get(key)
            return None

        new_short = next((a for a in NEW_SHORT if a in aset), None)
        old_short = next((a for a in ("roll_short_call_close", "roll_leap_close_short")
                          if a in aset), None)
        return cls(
            rec_type=rec_type,
            llm_action=(llm_action or "").upper(),
            old_strike=_f(field(old_short, "strike")) if old_short else None,
            new_strike=_f(field(new_short, "strike")) if new_short else None,
            old_exp=(field(old_short, "expiration") or "") if old_short else "",
            new_exp=(field(new_short, "expiration") or "") if new_short else "",
            old_leap_px=_f(field("roll_leap_close", "price")),
            has_new_leap=any(a in NEW_LEAP for a in aset),
            sold_leap=("roll_leap_close" in aset),
            closed_short=any(a in CLOSE_SHORT for a in aset),
            override_kind=override_kind, date=date, spot=spot,
            target_strike=target_strike,
        )


def is_roll(r: RecRecord) -> bool:
    return r.rec_type in ROLL_TYPES


# --- B4 (highest severity) -------------------------------------------------
# A roll that closes a leg without proposing its re-open short leg -> the
# position is left without its covering short until the next scan.
def close_without_recover(r: RecRecord) -> bool:
    return is_roll(r) and r.new_strike is None


# B4 sub-severities (see Phase-0 drill): fully_naked = old LEAP sold and NOT
# replaced; uncovered = the long remains but no covering short; naked_short =
# LEAP sold with a short left open (historical count 0 — the deterministic
# close-short leg fires whenever a short exists).
def b4_fully_naked(r: RecRecord) -> bool:
    return (close_without_recover(r) and r.rec_type == "roll_leap"
            and r.sold_leap is True and r.has_new_leap is False)


def b4_uncovered(r: RecRecord) -> bool:
    return close_without_recover(r) and not b4_fully_naked(r)


def b4_naked_short(r: RecRecord) -> bool:
    return b4_fully_naked(r) and r.closed_short is False


# --- B1 / failure-mode #5 --------------------------------------------------
def hold_overridden(r: RecRecord) -> bool:
    if r.llm_action not in ("HOLD", "WATCH"):
        return False
    if r.rec_type in ("", "other"):
        return False
    return r.override_kind != "hold_override"


# --- B7 --------------------------------------------------------------------
def same_expiry_roll(r: RecRecord) -> bool:
    return (is_roll(r) and bool(r.old_exp) and bool(r.new_exp)
            and r.old_exp == r.new_exp)


# --- B2 --------------------------------------------------------------------
def net_debit_roll(r: RecRecord) -> bool:
    if not is_roll(r) or r.net_cash_sh is None:
        return False
    if r.net_cash_sh >= 0:
        return False
    return r.override_kind != "net_debit_justified"


# --- B3 --------------------------------------------------------------------
# A LEAP roll whose cost is not computable: old-LEAP sell priced 0.0 OR no
# old-LEAP sell leg at all (-> old_leap_px None). Historical: 33 zero-priced + 5
# leg-absent = 38 not computable.
def cost_ignorant_leap_roll(r: RecRecord) -> bool:
    if r.rec_type != "roll_leap":
        return False
    return r.old_leap_px is None or r.old_leap_px == 0.0


# --- B11 -------------------------------------------------------------------
def holiday_scan(r: RecRecord) -> bool:
    return r.date in WINDOW_HOLIDAYS


# --- B5 / concern #4 (Phase-0 proxy) ---------------------------------------
def short_delta_ge_040(r: RecRecord) -> bool:
    return r.new_delta is not None and r.new_delta >= 0.40


# --- B6 --------------------------------------------------------------------
# target_strike + spot were never persisted historically -> no historical row
# trips this; exercised synthetically in Phase 2.
def itm_target_strike_bypass(r: RecRecord) -> bool:
    if r.target_strike is None or r.spot is None:
        return False
    return r.target_strike <= r.spot


def count_over(records, detector) -> int:
    """Count records tripping a detector — the phase-gate assertion primitive."""
    return sum(1 for r in records if detector(r))


ALL_DETECTORS = {
    "close_without_recover": close_without_recover,
    "b4_uncovered": b4_uncovered,
    "b4_fully_naked": b4_fully_naked,
    "b4_naked_short": b4_naked_short,
    "hold_overridden": hold_overridden,
    "same_expiry_roll": same_expiry_roll,
    "net_debit_roll": net_debit_roll,
    "cost_ignorant_leap_roll": cost_ignorant_leap_roll,
    "holiday_scan": holiday_scan,
    "short_delta_ge_040": short_delta_ge_040,
    "itm_target_strike_bypass": itm_target_strike_bypass,
}
