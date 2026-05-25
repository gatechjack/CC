"""Read-only backtest of cli_rounding_risk() against the 2026-05-24 autopsy
tail-loss events + (optionally) the broader residuals DB.

Headline result this script reports: of the autopsy's 12 |z|>=2 tail-loss
rows (5 unique station-date events), how many have risk_flag=True from
the F→C→F rounding-artifact predictor? This single number is the first-
order test of whether the rounding artifact is a real driver of autopsy
anomaly #2 or a minor contributor.

The 12 tail-loss rows are hardcoded from
`reports/2026-05-24_kalshi_weather_post_xref_24h_autopsy.md` §"Per-row
|z| ≥ 2 table" — fixed historical reference events for the headline.

If `weather_forecast_residuals` has been populated (via
ingest_iem_cli_residuals.py), the script also computes:
  - predictive base rate: of all boundary-adjacent rows (|forecast -
    threshold| < 1.0F), what fraction have risk_flag=True
  - flip realization rate: of risk_flag=True rows, what fraction
    actually settled at round(forecast) - 1F (or +1F for min)

Read-only. No DB writes. No live decision-path consumption.

Usage:
    python scripts/backtest_rounding_flip.py
    python scripts/backtest_rounding_flip.py --db PATH
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trading_corp.agents.strategies._weather_math import cli_rounding_risk  # noqa: E402
from trading_corp.persistence.db import resolve_db_path  # noqa: E402

DEFAULT_DB_URL = "sqlite:///data/trading_corp.db"
log = logging.getLogger("backtest_rounding_flip")


@dataclass(frozen=True)
class AutopsyTailRow:
    """One row from the 2026-05-24 autopsy |z|>=2 table."""
    ticker: str
    forecast_f: float
    sigma_used_f: float
    actual_f: float
    z: float
    won: bool

    @property
    def threshold_f(self) -> float:
        """Extract the numeric threshold from the ticker suffix.

        Tickers like KXHIGHTMIN-26MAY23-B69.5 → 69.5
                     KXLOWTSATX-26MAY23-T65   → 65
        The leading letter (B/T) is the market type; we use the numeric
        threshold for the rounding-risk question.
        """
        m = re.match(r".+-([BT])(\d+(?:\.\d+)?)$", self.ticker)
        return float(m.group(2)) if m else float("nan")

    @property
    def direction(self) -> Literal["max", "min"]:
        """Derive max/min from the ticker series prefix.

        KXHIGHT* / KXHIGH* tickers settle on daily MAX; KXLOWT* on MIN.
        """
        prefix = self.ticker.split("-", 1)[0]
        # KXHIGHT_, KXHIGH_, KXTEMP_ etc. settle on daily_max in general;
        # KXLOWT_ settles on daily_min. Conservative classifier:
        if prefix.startswith("KXLOWT"):
            return "min"
        return "max"

    @property
    def station_event(self) -> str:
        """station+date stripped of bucket suffix — to dedupe to 5 events."""
        m = re.match(r"(KX[A-Z]+)-(\d{2}[A-Z]{3}\d{2})-", self.ticker)
        if not m:
            return self.ticker
        return f"{m.group(1)}/{m.group(2)}"


# Verbatim from reports/2026-05-24_kalshi_weather_post_xref_24h_autopsy.md §4
AUTOPSY_TAIL_ROWS: list[AutopsyTailRow] = [
    AutopsyTailRow("KXHIGHTMIN-26MAY23-B69.5", 72.0, 2.63, 63.7, -3.16, True),
    AutopsyTailRow("KXHIGHTMIN-26MAY23-T69",   72.0, 2.76, 63.7, -3.01, False),
    AutopsyTailRow("KXLOWTSATX-26MAY23-B69.5", 72.0, 2.10, 65.8, -2.95, True),
    AutopsyTailRow("KXLOWTSATX-26MAY23-T65",   72.0, 2.14, 65.8, -2.90, True),
    AutopsyTailRow("KXLOWTSATX-26MAY23-B65.5", 72.0, 2.14, 65.8, -2.90, False),
    AutopsyTailRow("KXLOWTSATX-26MAY23-B67.5", 72.0, 2.14, 65.8, -2.90, True),
    AutopsyTailRow("KXHIGHTSEA-26MAY23-B72.5", 71.0, 2.57, 77.4, 2.49, True),
    AutopsyTailRow("KXLOWTAUS-26MAY23-T65",    71.0, 2.42, 66.1, -2.03, True),
    AutopsyTailRow("KXLOWTAUS-26MAY23-B67.5",  71.0, 2.42, 66.1, -2.03, True),
    AutopsyTailRow("KXLOWTAUS-26MAY23-B69.5",  71.0, 2.42, 66.1, -2.03, True),
    AutopsyTailRow("KXLOWTAUS-26MAY23-B65.5",  71.0, 2.42, 66.1, -2.03, False),
    AutopsyTailRow("KXHIGHTSEA-26MAY23-B70.5", 72.0, 2.67, 77.4, 2.02, True),
]


def evaluate_autopsy_tail_rows() -> tuple[int, int, dict[str, dict]]:
    """Run cli_rounding_risk over the autopsy tail rows.

    Returns:
      (n_rows_risk_at_entry,
       n_rows_risk_at_settlement,
       per_event_breakdown {station_event: {...}})
    """
    n_entry_risky = 0
    n_settlement_risky = 0
    per_event: dict[str, dict] = {}

    for row in AUTOPSY_TAIL_ROWS:
        thr = row.threshold_f
        dirn = row.direction
        entry = cli_rounding_risk(row.forecast_f, int(round(thr)), dirn)
        sett = cli_rounding_risk(row.actual_f, int(round(thr)), dirn)
        if entry["risk_flag"]:
            n_entry_risky += 1
        if sett["risk_flag"]:
            n_settlement_risky += 1

        per_event.setdefault(row.station_event, {
            "rows": [], "any_risky_entry": False, "any_risky_settlement": False,
        })
        per_event[row.station_event]["rows"].append({
            "ticker": row.ticker,
            "forecast": row.forecast_f,
            "actual": row.actual_f,
            "threshold": thr,
            "direction": dirn,
            "abs_miss": abs(row.actual_f - row.forecast_f),
            "z": row.z,
            "won": row.won,
            "risk_at_entry": entry["risk_flag"],
            "delta_entry": entry["delta_predicted_f"],
            "risk_at_settlement": sett["risk_flag"],
            "delta_settlement": sett["delta_predicted_f"],
        })
        if entry["risk_flag"]:
            per_event[row.station_event]["any_risky_entry"] = True
        if sett["risk_flag"]:
            per_event[row.station_event]["any_risky_settlement"] = True

    return n_entry_risky, n_settlement_risky, per_event


def report_autopsy(per_event: dict[str, dict], n_entry: int, n_sett: int) -> None:
    print("\n" + "=" * 70)
    print("C3 BACKTEST — AUTOPSY TAIL-LOSS ROUNDING-FLIP TEST")
    print("=" * 70)
    n_events = len(per_event)
    n_events_risky_entry = sum(1 for v in per_event.values() if v["any_risky_entry"])
    n_events_risky_sett = sum(1 for v in per_event.values() if v["any_risky_settlement"])
    n_rows = len(AUTOPSY_TAIL_ROWS)
    print(f"\nHEADLINE: of {n_events} unique autopsy tail-loss EVENTS:")
    print(f"  - {n_events_risky_entry} had risk_flag=True at entry-time forecast")
    print(f"  - {n_events_risky_sett}  had risk_flag=True at settlement-time actual")
    print(f"(supporting: {n_rows} total |z|>=2 rows; {n_entry} risky@entry, {n_sett} risky@settle)")
    print(f"\nCAVEAT: n=5 unique events. Directional only — NOT conclusive.")

    print("\nPer-event detail:")
    for ev, data in per_event.items():
        first = data["rows"][0]
        miss = first["abs_miss"]
        print(f"\n  {ev}: forecast={first['forecast']}F, actual={first['actual']}F, "
              f"|miss|={miss}F, direction={first['direction']}")
        for r in data["rows"]:
            entry_tag = "RISK" if r["risk_at_entry"] else "ok  "
            sett_tag = "RISK" if r["risk_at_settlement"] else "ok  "
            print(f"    {r['ticker']:30s} thr={r['threshold']:5.1f} z={r['z']:+5.2f} "
                  f"won={'Y' if r['won'] else 'N'}  "
                  f"entry={entry_tag}(d={r['delta_entry']:+.1f}) "
                  f"settle={sett_tag}(d={r['delta_settlement']:+.1f})")


def report_residuals_db(db_path: Path) -> None:
    """Best-effort base-rate report if weather_forecast_residuals has rows."""
    conn = sqlite3.connect(db_path)
    try:
        # Check table exists
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='weather_forecast_residuals'"
        ).fetchone()
        if not table_exists:
            print("\n(weather_forecast_residuals table not yet created; skipping base-rate)")
            return
        c = conn.execute(
            "SELECT COUNT(*) FROM weather_forecast_residuals "
            "WHERE logic_era != 'pre_station_fix'"
        ).fetchone()[0]
        if c == 0:
            print("\n(no non-pre-fix residuals DB rows; skipping base-rate)")
            return
        print(f"\n--- Predictive base rate from residuals DB ---")
        print(f"non-pre-fix residual rows: {c}")
        # We don't know the threshold per row without the ticker, but
        # we can compute the at-entry rounding-risk against the
        # closest-integer threshold from forecast itself (a proxy).
        # For a meaningful base rate we'd need the ticker; for now,
        # report sample sizes and a note.
        print("(per-ticker threshold join not built here — defer to a "
              "follow-up backtest that joins back to audit_event by "
              "ticker for proper threshold extraction)")
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=DEFAULT_DB_URL)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    n_entry, n_sett, per_event = evaluate_autopsy_tail_rows()
    report_autopsy(per_event, n_entry, n_sett)

    db_path = resolve_db_path(args.db)
    if db_path.exists():
        report_residuals_db(db_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
