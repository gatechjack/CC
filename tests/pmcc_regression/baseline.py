"""Phase-0 structural regression BASELINE snapshot (the 'before' numbers).

Computed from planning/pmcc_rec_history.csv (157 recommendations / 279 order
legs / 126 rolls, 2026-05-01 .. 07-21), enriched with LEAP-leg facts.

Denominator note (reconciled 2026-07-21): close_without_recover = 51.
  51/157 = 32.5% of ALL recs   (this is the plan's "~30% book" figure)
  51/126 = 40.5% of ROLLS
The earlier "~30%" and "40.5%" are the SAME finding under different denominators.

B4 sub-severities (Phase-0 drill): 31 uncovered + 20 fully_naked + 0 naked_short.
  uncovered   = the long (LEAP) remains but no covering short.
  fully_naked = old LEAP sold and NOT replaced; all 20 were FLAT (short also
                bought back) -> a roll that collapsed to a full close.
  naked_short = LEAP sold with a short left open -> 0 (the deterministic
                close-short leg fires whenever a short exists).

cost_ignorant_leap_roll = 38 not computable = 33 old-LEAP sells priced 0.0
  + 5 roll_leaps with no old-LEAP sell leg at all.

itm_target_strike_bypass has NO historical baseline (target_strike + spot were
  never persisted); validated synthetically in Phase 2. Baseline = None.
"""
import csv
from pathlib import Path

from . import detectors as D

CSV_PATH = Path(__file__).resolve().parents[2] / "planning" / "pmcc_rec_history.csv"

# Frozen 'before' counts the Bucket-B fixes must drive down.
BASELINE = {
    "total_recs": 157,
    "total_rolls": 126,
    "close_without_recover": 51,        # 32.5% of recs / 40.5% of rolls (Phase 1: 0)
    "b4_uncovered": 31,                 # (Phase 1: 0)
    "b4_fully_naked": 20,               # all flat (Phase 1: 0)
    "b4_naked_short": 0,                # already 0 — must STAY 0
    "hold_overridden": 50,              # 32% of recs (Phase 2: 0 unjustified)
    "same_expiry_roll": 18,             # (Phase 2: 0)
    "net_debit_roll": 37,               # (Phase 2: 0 unjustified)
    "cost_ignorant_leap_roll": 38,      # 33 zero-priced + 5 leg-absent (Phase 3: 0)
    "holiday_scan": 6,                  # 3 dates (Phase 1: 0)
    "short_delta_ge_040": 24,           # (Phase 4: 0 outside band)
    "itm_target_strike_bypass": None,   # not persisted; synthetic-only (Phase 2)
}


def load_records():
    """Return the frozen 157-row history as normalized RecRecords."""
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        return [D.RecRecord.from_row(row) for row in csv.DictReader(f)]
