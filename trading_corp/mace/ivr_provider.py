"""MACE IVR provider — Tasty market-metrics IV rank + ATM-IV snapshots.

Version-independent by construction (trap 5: prod tastytrade is 12.4.1, local
13.2.2 — the API surface load-bears, so the provider NEVER imports the SDK).
It takes an injected `fetch_metrics(symbols) -> list[metric]` callable — the
Phase-3/4 wiring wraps the async `get_market_metrics` under a live Session; unit
tests inject a mock (version-independent, the stage-A p0a3 probe is the live
12.4.1 integration proof). Metric objects are read DEFENSIVELY (attribute OR
dict key) so a real MarketMetricInfo, a SimpleNamespace, or a plain dict all work.

Load-bearing traps encoded here:
  - x100 (trap 1): Tasty rank fields are 0-1 scale. The canonical rank is
    normalized x100 BEFORE anyone compares it to ivr_floor (25). SPY 0.272 -> 27.2.
    A post-normalization value outside [0, 100] is a scale regression, surfaced
    as UNAVAILABLE (anomaly) rather than a silent always-pass/always-fail.
  - field pin (trap 2): canonical = implied_volatility_index_rank (a STRING in
    the SDK, == the tos Decimal variant). NEVER tw_ (USO tw=1.0 vs real 0.295).
    tos is the only accepted fallback when the canonical field is absent/None.
  - staleness (ruling 2026-08-09): updated_at older than `stale_after_sessions`
    (default 2) business days => IVR_STALE for THAT symbol (detail carries symbol
    + age), DISTINCT from IVR_UNAVAILABLE (fetch failed / symbol missing / rank
    None). The Phase-2 entry pipeline skips the IVR filter on both but keeps
    credit floor + blackouts; the distinct status keeps per-symbol staleness
    visible in eval history.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from trading_corp.mace.domain import IVR_OK, IVR_STALE, IVR_UNAVAILABLE
from trading_corp.utils.time import now_utc, to_et

# Field pins (trap 2). Canonical rank first; tos == canonical is the ONLY
# accepted fallback. tw_ is deliberately absent — never read it.
FIELD_RANK = "implied_volatility_index_rank"
FIELD_RANK_TOS = "tos_implied_volatility_index_rank"
FIELD_ATM_IV = "implied_volatility_index"
FIELD_UPDATED_AT = "updated_at"

DEFAULT_SOURCE = "tastytrade_market_metrics"
IVR_RANK_SCALE = 100.0            # trap 1: 0-1 rank -> 0-100 IVR
_IVR_RANGE_EPS = 1e-6
_DEFAULT_STALE_AFTER_SESSIONS = 2
_SESSION_AGE_GUARD = 400          # loop backstop for absurd/None timestamps


@dataclass(frozen=True)
class IvrReading:
    """One symbol's IVR read. `ivr` is normalized 0-100 (None when unavailable);
    `atm_iv` is the raw implied_volatility_index. `status` is a domain IVR_*
    constant; `age_sessions` is populated for STALE (and informational otherwise)."""

    symbol: str
    status: str                       # IVR_OK | IVR_STALE | IVR_UNAVAILABLE
    ivr: float | None
    atm_iv: float | None
    updated_at: datetime | None
    age_sessions: int | None
    detail: str
    source: str = DEFAULT_SOURCE

    @property
    def usable(self) -> bool:
        """True only when the IVR value can be trusted for the floor compare."""
        return self.status == IVR_OK and self.ivr is not None


# ── field access + coercion ─────────────────────────────────────────────

def _attr(obj, name: str):
    """Read `name` from a metric object (attribute) or dict (key). None if absent."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _to_float(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):        # guard: bool is an int subclass
        return None
    if isinstance(v, (int, float, Decimal)):
        try:
            return float(v)
        except (ValueError, InvalidOperation):
            return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _session_age(updated_at, now: datetime) -> int:
    """Business-day (Mon-Fri) age of `updated_at` relative to `now`, both taken
    as ET calendar dates. 0 if same session or newer. Holidays are NOT modeled;
    ignoring them can only OVER-count age (skew toward 'stale' = the safe
    direction). Guarded against absurd/None inputs."""
    et_up = to_et(updated_at)
    et_now = to_et(now)
    if et_up is None or et_now is None:
        return 0
    d0, d1 = et_up.date(), et_now.date()
    if d1 <= d0:
        return 0
    age = 0
    cur = d0
    for _ in range(_SESSION_AGE_GUARD):
        cur = cur + timedelta(days=1)
        if cur > d1:
            break
        if cur.weekday() < 5:      # Mon-Fri
            age += 1
    return age


def classify(
    rank_raw,
    updated_at,
    now: datetime,
    *,
    stale_after_sessions: int = _DEFAULT_STALE_AFTER_SESSIONS,
) -> tuple[str, float | None, int | None]:
    """Pure classifier: (status, ivr_normalized_0_100, age_sessions).

    Testable without any fetch. `rank_raw` is the canonical (or tos) field value
    (str/Decimal/float/None); staleness is judged off `updated_at`.
    """
    rank = _to_float(rank_raw)
    if rank is None:
        return IVR_UNAVAILABLE, None, None
    ivr = rank * IVR_RANK_SCALE
    if ivr < -_IVR_RANGE_EPS or ivr > 100.0 + _IVR_RANGE_EPS:
        # Scale regression (e.g. API started returning 0-100 already) — do NOT
        # silently compare a 2720 against floor 25. Surface as unavailable.
        return IVR_UNAVAILABLE, None, None
    ivr = min(100.0, max(0.0, ivr))
    age = _session_age(updated_at, now)
    if age > stale_after_sessions:
        return IVR_STALE, ivr, age
    return IVR_OK, ivr, age


# ── fetch + assemble ────────────────────────────────────────────────────

def read_metrics(
    fetch_metrics,
    symbols,
    *,
    now: datetime | None = None,
    stale_after_sessions: int = _DEFAULT_STALE_AFTER_SESSIONS,
    source: str = DEFAULT_SOURCE,
) -> dict[str, IvrReading]:
    """Fetch metrics for `symbols` via the injected callable and return a
    {SYMBOL: IvrReading} map. A fetch exception marks EVERY requested symbol
    UNAVAILABLE; a symbol missing from the response is UNAVAILABLE for that
    symbol only. Never raises."""
    now = now or now_utc()
    wanted: list[str] = []
    for s in symbols:
        u = str(s).strip().upper()
        if u and u not in wanted:
            wanted.append(u)

    try:
        metrics = list(fetch_metrics(wanted))
    except Exception as e:  # Tasty down / network / auth — whole batch unavailable
        return {
            sym: IvrReading(sym, IVR_UNAVAILABLE, None, None, None, None,
                            f"fetch failed: {type(e).__name__}: {e}", source)
            for sym in wanted
        }

    by_sym: dict[str, object] = {}
    for m in metrics:
        msym = _attr(m, "symbol")
        if msym is not None:
            by_sym[str(msym).strip().upper()] = m

    out: dict[str, IvrReading] = {}
    for sym in wanted:
        m = by_sym.get(sym)
        if m is None:
            out[sym] = IvrReading(sym, IVR_UNAVAILABLE, None, None, None, None,
                                  "symbol missing from metrics response", source)
            continue
        rank_raw = _attr(m, FIELD_RANK)
        if rank_raw is None:
            rank_raw = _attr(m, FIELD_RANK_TOS)   # tos == canonical (trap 2)
        updated_at = _attr(m, FIELD_UPDATED_AT)
        atm_iv = _to_float(_attr(m, FIELD_ATM_IV))
        status, ivr, age = classify(rank_raw, updated_at, now,
                                    stale_after_sessions=stale_after_sessions)
        out[sym] = IvrReading(sym, status, ivr, atm_iv, updated_at, age,
                              _detail(sym, status, ivr, age, rank_raw, updated_at),
                              source)
    return out


def _detail(sym, status, ivr, age, rank_raw, updated_at) -> str:
    if status == IVR_STALE:
        return f"{sym} IVR stale: {age} sessions old (updated_at={updated_at})"
    if status == IVR_UNAVAILABLE:
        if rank_raw is None:
            return f"{sym} IVR unavailable: rank field None"
        return f"{sym} IVR unavailable: rank {rank_raw!r} out of range after x100"
    return f"{sym} IVR {ivr:.1f}"


# ── mace_iv_history snapshot writer ─────────────────────────────────────

def write_iv_snapshot(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    snap_date: date | str,
    atm_iv: float | None,
    ivr_tasty: float | None,
    source: str = DEFAULT_SOURCE,
    ts: str | None = None,
) -> None:
    """Upsert one (symbol, snap_date) row into mace_iv_history. ivr_tasty is the
    NORMALIZED 0-100 value (matches the column comment). INSERT OR REPLACE so a
    re-eval on the same session refreshes the row. Does not commit."""
    sd = snap_date.isoformat() if isinstance(snap_date, date) else str(snap_date)
    ts = ts or now_utc().isoformat(timespec="seconds")
    conn.execute(
        "INSERT OR REPLACE INTO mace_iv_history "
        "(symbol, snap_date, atm_iv, ivr_tasty, source, ts) VALUES (?, ?, ?, ?, ?, ?)",
        (str(symbol).strip().upper(), sd, atm_iv, ivr_tasty, source, ts),
    )


def snapshot_readings(
    conn: sqlite3.Connection,
    readings: dict[str, IvrReading],
    snap_date: date | str,
    *,
    ts: str | None = None,
) -> int:
    """Write mace_iv_history rows for every reading that carries data (atm_iv or
    ivr present — records STALE values too; skips fully-UNAVAILABLE symbols).
    Returns the number of rows written."""
    n = 0
    for r in readings.values():
        if r.atm_iv is None and r.ivr is None:
            continue
        write_iv_snapshot(conn, symbol=r.symbol, snap_date=snap_date,
                          atm_iv=r.atm_iv, ivr_tasty=r.ivr, source=r.source, ts=ts)
        n += 1
    return n
