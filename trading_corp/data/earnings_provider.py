"""Earnings data adapter for the robinhood_pead division.

PRIMARY source: Finnhub REST API (no SDK — stdlib urllib.request only).
  - /company/earnings  → QuarterlyEPS history (actuals + estimates)
  - /calendar/earnings → announcements within a date range
  Read key from env FINNHUB_API_KEY (loaded by utils/secrets.py).

FALLBACK: yfinance (already a dep).
  - quarterly_financials → QuarterlyEPS actuals (no estimate / surprise)
  - earnings_dates       → announcement calendar fallback

No auto-failover: like data_providers.yaml convention, the source that serves
each response is LOGGED explicitly.  Missing Finnhub key → yfinance path only
(labeled; never a silent switch mid-call).

24-hour per-symbol in-memory cache with TTL — mirrors utils/market_data.py.

Boundary contract: return None / empty list on any data gap or failure.
None = "no data, don't block" (matches get_next_earnings contract).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, timedelta
from threading import Lock
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

_CACHE_TTL_SEC = 86_400  # 24 hours — earnings dates change rarely

# Finnhub base URL
_FINNHUB_BASE = "https://finnhub.io/api/v1"
_HTTP_TIMEOUT_SEC = 10


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QuarterlyEPS:
    """Normalised quarterly EPS row.

    fiscal_period  — e.g. "2024Q1"
    report_date    — date the quarter was reported
    actual_eps     — reported EPS (float)
    estimate_eps   — consensus estimate at time of report; None if unavailable
    surprise_pct   — (actual - estimate) / |estimate| * 100; None if no estimate
    """
    fiscal_period: str
    report_date: date
    actual_eps: float
    estimate_eps: float | None
    surprise_pct: float | None


# ---------------------------------------------------------------------------
# Internal cache helpers  (per-symbol, per-method)
# ---------------------------------------------------------------------------

_EPS_CACHE: dict[str, tuple[list[QuarterlyEPS] | None, float]] = {}
_ANN_CACHE: dict[tuple[date, int], tuple[list[str], float]] = {}
_CACHE_LOCK = Lock()


def _eps_cache_get(symbol: str) -> tuple[bool, list[QuarterlyEPS] | None]:
    with _CACHE_LOCK:
        entry = _EPS_CACHE.get(symbol.upper())
    if entry is None:
        return False, None
    val, ts = entry
    if time.time() - ts < _CACHE_TTL_SEC:
        return True, val
    return False, None


def _eps_cache_set(symbol: str, val: list[QuarterlyEPS] | None) -> None:
    with _CACHE_LOCK:
        _EPS_CACHE[symbol.upper()] = (val, time.time())


def _ann_cache_get(key: tuple[date, int]) -> tuple[bool, list[str]]:
    with _CACHE_LOCK:
        entry = _ANN_CACHE.get(key)
    if entry is None:
        return False, []
    val, ts = entry
    if time.time() - ts < _CACHE_TTL_SEC:
        return True, val
    return False, []


def _ann_cache_set(key: tuple[date, int], val: list[str]) -> None:
    with _CACHE_LOCK:
        _ANN_CACHE[key] = (val, time.time())


def reset_earnings_provider_cache() -> None:
    """Clear all caches (useful for tests)."""
    with _CACHE_LOCK:
        _EPS_CACHE.clear()
        _ANN_CACHE.clear()


# ---------------------------------------------------------------------------
# Low-level HTTP helper
# ---------------------------------------------------------------------------

def _finnhub_get(path: str, params: dict[str, str], api_key: str) -> dict | list | None:
    """GET `path` from Finnhub with `params`.  Returns parsed JSON or None."""
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{_FINNHUB_BASE}{path}?{qs}&token={api_key}"
    req = Request(url, headers={"User-Agent": "trading-corp/1.0"})
    try:
        with urlopen(req, timeout=_HTTP_TIMEOUT_SEC) as resp:
            body = resp.read()
        return json.loads(body)
    except HTTPError as e:
        log.warning("Finnhub HTTP %s for %s: %s", e.code, path, e)
        return None
    except (URLError, OSError) as e:
        log.warning("Finnhub network error for %s: %s", path, e)
        return None
    except Exception as e:
        log.warning("Finnhub unexpected error for %s: %s", path, e)
        return None


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _compute_surprise(actual: float, estimate: float | None) -> float | None:
    if estimate is None:
        return None
    if estimate == 0.0:
        return None  # avoid div-by-zero; not meaningful
    return round((actual - estimate) / abs(estimate) * 100.0, 4)


def _parse_finnhub_earnings(data: list[dict]) -> list[QuarterlyEPS]:
    """Parse Finnhub /company/earnings response into QuarterlyEPS list."""
    rows: list[QuarterlyEPS] = []
    for item in data:
        try:
            # Finnhub fields: period (YYYY-MM-DD of fiscal end), date (report date),
            # actual, estimate
            report_date_str = item.get("date") or item.get("period")
            if not report_date_str:
                continue
            report_date = date.fromisoformat(str(report_date_str)[:10])
            fiscal_period = item.get("period", report_date_str)
            # fiscal_period may be "YYYY-MM-DD" — normalise to "YYYYQn" if possible
            fiscal_str = _normalise_fiscal_period(str(fiscal_period))

            actual_raw = item.get("actual")
            if actual_raw is None:
                continue  # skip quarters without actual EPS
            actual_eps = float(actual_raw)

            estimate_raw = item.get("estimate")
            estimate_eps = float(estimate_raw) if estimate_raw is not None else None
            surprise_pct = _compute_surprise(actual_eps, estimate_eps)

            rows.append(QuarterlyEPS(
                fiscal_period=fiscal_str,
                report_date=report_date,
                actual_eps=actual_eps,
                estimate_eps=estimate_eps,
                surprise_pct=surprise_pct,
            ))
        except Exception as e:
            log.debug("_parse_finnhub_earnings: skipping malformed row %s: %s", item, e)
    return rows


def _normalise_fiscal_period(raw: str) -> str:
    """Convert 'YYYY-MM-DD' fiscal end date to 'YYYYQn' if possible; else return as-is."""
    try:
        d = date.fromisoformat(raw[:10])
        # Infer quarter from fiscal end month
        q = (d.month - 1) // 3 + 1
        return f"{d.year}Q{q}"
    except Exception:
        return raw


def _parse_yfinance_quarterly(symbol: str) -> list[QuarterlyEPS] | None:
    """Extract QuarterlyEPS from yfinance quarterly_earnings DataFrame.

    yfinance does not provide consensus estimates in quarterly data — only
    actuals.  surprise_pct / estimate_eps will be None.
    """
    try:
        import yfinance as yf  # type: ignore
        ticker = yf.Ticker(symbol.upper())

        # Use .quarterly_income_stmt (or .quarterly_financials) for EPS
        # Note: yfinance >=0.2.50 uses quarterly_income_stmt; earlier used
        # quarterly_financials.  We try both paths for robustness.
        eps_series = None
        for attr in ("quarterly_earnings", "earnings"):
            df = getattr(ticker, attr, None)
            if df is not None and not df.empty and "Earnings" in df.columns:
                eps_series = df["Earnings"]
                break

        if eps_series is None or eps_series.empty:
            log.debug("_parse_yfinance_quarterly: %s — no earnings data", symbol)
            return None

        rows: list[QuarterlyEPS] = []
        for idx, val in eps_series.items():
            try:
                if hasattr(idx, "to_pydatetime"):
                    report_date = idx.to_pydatetime().date()
                elif hasattr(idx, "date"):
                    report_date = idx.date()
                else:
                    report_date = date.fromisoformat(str(idx)[:10])

                fiscal_str = _normalise_fiscal_period(report_date.isoformat())
                actual_eps = float(val)
                rows.append(QuarterlyEPS(
                    fiscal_period=fiscal_str,
                    report_date=report_date,
                    actual_eps=actual_eps,
                    estimate_eps=None,
                    surprise_pct=None,
                ))
            except Exception as e:
                log.debug("_parse_yfinance_quarterly: skip row idx=%s val=%s: %s", idx, val, e)

        return rows if rows else None

    except ImportError:
        log.warning("EarningsProvider yfinance fallback: yfinance not installed")
        return None
    except Exception as e:
        log.warning("EarningsProvider yfinance fallback (_parse_yfinance_quarterly): %s", e)
        return None


def _yfinance_announcement_dates(symbol: str) -> list[date]:
    """Return known earnings report dates via yfinance.earnings_dates."""
    try:
        import yfinance as yf  # type: ignore
        ticker = yf.Ticker(symbol.upper())
        df = getattr(ticker, "earnings_dates", None)
        if df is None or df.empty:
            return []
        dates: list[date] = []
        for idx in df.index:
            try:
                if hasattr(idx, "to_pydatetime"):
                    d = idx.to_pydatetime().date()
                else:
                    d = date.fromisoformat(str(idx)[:10])
                dates.append(d)
            except Exception:
                continue
        return dates
    except ImportError:
        log.warning("EarningsProvider yfinance fallback: yfinance not installed")
        return []
    except Exception as e:
        log.warning("EarningsProvider._yfinance_announcement_dates: %s", e)
        return []


# ---------------------------------------------------------------------------
# EarningsProvider
# ---------------------------------------------------------------------------

class EarningsProvider:
    """Earnings data adapter: EPS history + announcement calendar.

    PRIMARY: Finnhub REST API (key from env FINNHUB_API_KEY).
    FALLBACK: yfinance — activated when key absent or Finnhub returns nothing.
    LABELED: every response logs which source served it.
    NO auto-failover within a single call that already succeeded.
    """

    def __init__(self, api_key: str | None = None) -> None:
        """Create an EarningsProvider.

        api_key — override FINNHUB_API_KEY env var (for tests / injection).
        If None, reads from os.environ["FINNHUB_API_KEY"].
        Missing key → Finnhub skipped, yfinance path only.
        """
        self._api_key: str | None = api_key or os.environ.get("FINNHUB_API_KEY") or None
        if not self._api_key:
            log.info(
                "EarningsProvider: FINNHUB_API_KEY not set — yfinance fallback only"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_quarterly_eps(self, symbol: str) -> list[QuarterlyEPS] | None:
        """Return >=8 most-recent quarterly EPS rows (oldest→newest), or None.

        None means no data available — callers should treat as "don't block"
        (same contract as get_next_earnings in utils/market_data.py).

        Tries Finnhub first (if key present); falls back to yfinance if
        Finnhub returns empty or errors.  The serving source is logged.
        """
        if not symbol:
            return None
        sym = symbol.upper()

        hit, cached = _eps_cache_get(sym)
        if hit:
            return cached

        result = self._fetch_quarterly_eps(sym)
        _eps_cache_set(sym, result)
        return result

    def get_recent_announcements(
        self,
        on_date: date,
        lookback_days: int = 1,
    ) -> list[str]:
        """Return symbols that reported earnings on/within lookback_days of on_date.

        Uses Finnhub /calendar/earnings if key is available; falls back to
        yfinance .earnings_dates cross-symbol (impractical for large universes
        — returns [] in that case unless Finnhub served it first).

        Empty list on any failure (never raises).
        """
        key = (on_date, lookback_days)
        hit, cached = _ann_cache_get(key)
        if hit:
            return cached

        result = self._fetch_recent_announcements(on_date, lookback_days)
        _ann_cache_set(key, result)
        return result

    # ------------------------------------------------------------------
    # Internal fetch helpers
    # ------------------------------------------------------------------

    def _fetch_quarterly_eps(self, sym: str) -> list[QuarterlyEPS] | None:
        # --- PRIMARY: Finnhub ---
        if self._api_key:
            data = _finnhub_get(
                "/stock/earnings",
                {"symbol": sym},
                self._api_key,
            )
            if isinstance(data, list) and data:
                rows = _parse_finnhub_earnings(data)
                if rows:
                    rows_sorted = sorted(rows, key=lambda r: r.report_date)
                    # Most-recent 8+ quarters; return all if fewer
                    result = rows_sorted[-max(8, len(rows_sorted)):]
                    log.info(
                        "EarningsProvider.get_quarterly_eps(%s): served by Finnhub "
                        "(%d quarters)",
                        sym, len(result),
                    )
                    return result
            log.info(
                "EarningsProvider.get_quarterly_eps(%s): Finnhub returned nothing "
                "— trying yfinance fallback",
                sym,
            )

        # --- FALLBACK: yfinance ---
        rows = _parse_yfinance_quarterly(sym)
        if rows:
            rows_sorted = sorted(rows, key=lambda r: r.report_date)
            result = rows_sorted[-max(8, len(rows_sorted)):]
            log.info(
                "EarningsProvider.get_quarterly_eps(%s): served by yfinance fallback "
                "(%d quarters)",
                sym, len(result),
            )
            return result

        log.info(
            "EarningsProvider.get_quarterly_eps(%s): no data from any source",
            sym,
        )
        return None

    def _fetch_recent_announcements(
        self,
        on_date: date,
        lookback_days: int,
    ) -> list[str]:
        start = on_date - timedelta(days=lookback_days - 1)
        end = on_date

        # --- PRIMARY: Finnhub calendar ---
        if self._api_key:
            data = _finnhub_get(
                "/calendar/earnings",
                {
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                },
                self._api_key,
            )
            if isinstance(data, dict):
                items = data.get("earningsCalendar", []) or []
            elif isinstance(data, list):
                items = data
            else:
                items = []

            if items:
                symbols: list[str] = []
                for item in items:
                    sym = item.get("symbol") or item.get("ticker")
                    if sym:
                        symbols.append(str(sym).upper())
                symbols = sorted(set(symbols))
                log.info(
                    "EarningsProvider.get_recent_announcements(%s +%dd): served by "
                    "Finnhub (%d symbols)",
                    on_date, lookback_days, len(symbols),
                )
                return symbols
            log.info(
                "EarningsProvider.get_recent_announcements(%s +%dd): Finnhub returned "
                "nothing — yfinance cross-symbol not feasible; returning []",
                on_date, lookback_days,
            )
            return []

        # --- FALLBACK: yfinance (no cross-symbol scan; single-symbol only) ---
        # yfinance earnings_dates requires per-symbol calls; without a universe
        # we can't enumerate all reporters.  Return [] and log.
        log.info(
            "EarningsProvider.get_recent_announcements(%s +%dd): no Finnhub key; "
            "yfinance fallback not feasible without a symbol universe — returning []",
            on_date, lookback_days,
        )
        return []
