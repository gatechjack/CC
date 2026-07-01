"""Isolated research-log for the ``bitunix_sfp`` division.

A SEPARATE table (``bitunix_sfp_research_log``) that catalogs every entry by
(coin x regime x side x rr_target) for the months-long learn run. It NEVER touches
``paper_trade_record`` (a logging bug must not corrupt live P&L records), and EVERY
write is FAIL-SOFT — swallow + log on error so a logging failure can never interrupt
or unwind a trade. Entry is stamped at fill time (observer); exit is UPDATE'd by
order_id at auto-book time (reconciler). order_id == ProposedOrder.id == the reconciler
book key, so entry and exit join without any schema change to the live tables.
"""
from __future__ import annotations

import logging
from datetime import datetime

from trading_corp.persistence import db

log = logging.getLogger(__name__)

TABLE = "bitunix_sfp_research_log"

_DDL = (
    f"CREATE TABLE IF NOT EXISTS {TABLE} ("
    " id INTEGER PRIMARY KEY,"
    " order_id TEXT UNIQUE,"              # join key (== ProposedOrder.id / reconciler key)
    " division TEXT NOT NULL,"            # always 'bitunix_sfp'
    " coin TEXT NOT NULL,"
    " side TEXT NOT NULL,"                # long | short (semantic)
    " regime_label TEXT,"                 # up | range | down (at entry)
    " regime_engine TEXT,"                # '15m_ema200_slope'
    " rr_target REAL,"                    # 2.0
    " sfp_mode TEXT,"
    " bos_tf TEXT,"
    " entry_ts TEXT,"
    " entry_px REAL, stop_px REAL, target_px REAL,"
    " sfp_sweep_px REAL, bos_confirm_px REAL,"
    " htf_1h_ema200 REAL, htf_1h_slope REAL, htf_1h_strength REAL,"
    " htf_4h_ema200 REAL, htf_4h_slope REAL, htf_4h_strength REAL,"
    " htf_1d_ema200 REAL, htf_1d_slope REAL, htf_1d_strength REAL,"
    " exit_ts TEXT, exit_px REAL, realized_r REAL, closing_leg TEXT, duration_sec INTEGER,"
    " broker_order_id TEXT, extra_json TEXT"
    ")"
)
_IX = (f"CREATE INDEX IF NOT EXISTS {TABLE}_coin_regime_side_rr "
       f"ON {TABLE}(coin, regime_label, side, rr_target)")

_ENTRY_COLS = [
    "order_id", "division", "coin", "side", "regime_label", "regime_engine",
    "rr_target", "sfp_mode", "bos_tf", "entry_ts", "entry_px", "stop_px", "target_px",
    "sfp_sweep_px", "bos_confirm_px",
    "htf_1h_ema200", "htf_1h_slope", "htf_1h_strength",
    "htf_4h_ema200", "htf_4h_slope", "htf_4h_strength",
    "htf_1d_ema200", "htf_1d_slope", "htf_1d_strength",
    "broker_order_id", "extra_json",
]


def ensure_schema(db_url: str) -> None:
    """Idempotent CREATE TABLE/INDEX. Fail-soft — never raises."""
    try:
        with db.connect(db_url) as conn:
            conn.execute(_DDL)
            conn.execute(_IX)
    except Exception as e:                                   # fail-soft
        log.warning("%s ensure_schema failed: %s", TABLE, e)


def log_entry(db_url: str, row: dict) -> bool:
    """Fail-soft INSERT of one entry row (only the known columns; missing -> NULL).
    INSERT OR IGNORE on order_id so a ret/re-fire can never duplicate or raise."""
    try:
        vals = [row.get(c) for c in _ENTRY_COLS]
        ph = ",".join("?" * len(_ENTRY_COLS))
        with db.connect(db_url) as conn:
            conn.execute(
                f"INSERT OR IGNORE INTO {TABLE} ({','.join(_ENTRY_COLS)}) VALUES ({ph})",
                vals,
            )
        return True
    except Exception as e:                                   # fail-soft
        log.warning("%s log_entry failed (order_id=%s): %s", TABLE, row.get("order_id"), e)
        return False


def _parse(ts):
    for fn in (lambda s: datetime.fromisoformat(str(s)),
               lambda s: datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")):
        try:
            return fn(ts)
        except Exception:
            continue
    return None


def log_exit(db_url: str, order_id: str, *, exit_ts, exit_px, realized_r,
             closing_leg) -> bool:
    """Fail-soft UPDATE-by-order_id of the exit fields + duration_sec (from the row's
    own entry_ts). No-op if the entry row is absent (logging-only; never blocks book)."""
    try:
        with db.connect(db_url) as conn:
            r = conn.execute(f"SELECT entry_ts FROM {TABLE} WHERE order_id=?",
                             (order_id,)).fetchone()
            dur = None
            if r is not None and r[0] is not None:
                a, b = _parse(r[0]), _parse(exit_ts)
                if a and b:
                    dur = int((b - a).total_seconds())
            conn.execute(
                f"UPDATE {TABLE} SET exit_ts=?, exit_px=?, realized_r=?, closing_leg=?, "
                f"duration_sec=? WHERE order_id=?",
                (str(exit_ts), exit_px, realized_r, closing_leg, dur, order_id),
            )
        return True
    except Exception as e:                                   # fail-soft
        log.warning("%s log_exit failed (order_id=%s): %s", TABLE, order_id, e)
        return False
