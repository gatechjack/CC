"""Earnings data adapter for the robinhood_pead division.

PRIMARY source: EODHD REST API (no SDK — stdlib urllib.request only).
  - GET /api/fundamentals/{TICKER}.US  → one large JSON per symbol
  - Earnings::History   → QuarterlyEPS history (actuals + estimates)
  - Highlights::MarketCapitalization + General::Sector → company facts
  Read key from env EODHD_API_KEY (loaded by utils/secrets.py).

  Key advantage over Finnhub: the `reportDate` field in each
  Earnings::History entry is the ANNOUNCEMENT date (when the company
  actually published results), not the fiscal-period-end date.  PEAD
  returns are measured from the announcement day, so using the wrong date
  biases returns.

FALLBACK: yfinance (already a dep).
  - quarterly_financials → QuarterlyEPS actuals (no estimate / surprise)
  - earnings_dates       → announcement calendar fallback

No auto-failover: like data_providers.yaml convention, the source that
serves each response is LOGGED explicitly.  Missing EODHD key → yfinance
path only (labeled; never a silent switch mid-call).

24-hour per-symbol in-memory cache with TTL — mirrors utils/market_data.py.
A SINGLE fetch of the EODHD fundamentals JSON serves BOTH
get_quarterly_eps AND get_company_facts (one HTTP call per symbol per day).

Boundary contract: return None / empty list on any data gap or failure.
None = "no data, don't block" (matches get_next_earnings contract).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, replace
from datetime import date, timedelta
from threading import Lock
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

_CACHE_TTL_SEC = 86_400  # 24 hours — earnings dates change rarely

# EODHD base URL + default exchange suffix
_EODHD_BASE = "https://eodhd.com/api"
_EODHD_EXCHANGE_SUFFIX = "US"  # appended as "{symbol}.{suffix}"
_HTTP_TIMEOUT_SEC = 10


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QuarterlyEPS:
    """Normalised quarterly EPS row.

    fiscal_period  — e.g. "2024Q1" (derived from the fiscal period-end date)
    report_date    — date the quarter was ANNOUNCED / released to the public
                     (EODHD: Earnings.History[key].reportDate)
    actual_eps     — reported EPS (float)
    estimate_eps   — consensus estimate at time of report; None if unavailable
    surprise_pct   — (actual - estimate) / |estimate| * 100; None if no estimate
    report_time    — 'BeforeMarket' | 'AfterMarket' | None (BMO/AMC reporting slot,
                     sourced from /api/calendar/earnings — NOT fundamentals — and
                     joined on report_date). None = unknown; NEVER defaulted/guessed.
    """
    fiscal_period: str
    report_date: date
    actual_eps: float
    estimate_eps: float | None
    surprise_pct: float | None
    report_time: str | None = None


# ---------------------------------------------------------------------------
# Internal cache helpers  (per-symbol, per-method)
# ---------------------------------------------------------------------------

# Shared raw fundamentals cache: symbol.upper() -> (raw_dict, timestamp)
_FUND_CACHE: dict[str, tuple[dict, float]] = {}
# EPS result cache: symbol.upper() -> (result, timestamp)
_EPS_CACHE: dict[str, tuple[list[QuarterlyEPS] | None, float]] = {}
# Announcement calendar cache: (on_date, lookback_days) -> (result, timestamp)
_ANN_CACHE: dict[tuple[date, int], tuple[list[str], float]] = {}
# Calendar BMO/AMC slot cache: symbol.upper() -> ({report_date_str: 'BeforeMarket'|'AfterMarket'|None}, ts)
_SLOT_CACHE: dict[str, tuple[dict, float]] = {}
_CACHE_LOCK = Lock()


def _fund_cache_get(symbol: str) -> tuple[bool, dict | None]:
    with _CACHE_LOCK:
        entry = _FUND_CACHE.get(symbol.upper())
    if entry is None:
        return False, None
    val, ts = entry
    if time.time() - ts < _CACHE_TTL_SEC:
        return True, val
    return False, None


def _fund_cache_set(symbol: str, val: dict | None) -> None:
    if val is None:
        return
    with _CACHE_LOCK:
        _FUND_CACHE[symbol.upper()] = (val, time.time())


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


def _slot_cache_get(symbol: str) -> tuple[bool, dict]:
    with _CACHE_LOCK:
        entry = _SLOT_CACHE.get(symbol.upper())
    if entry is None:
        return False, {}
    val, ts = entry
    if time.time() - ts < _CACHE_TTL_SEC:
        return True, val
    return False, {}


def _slot_cache_set(symbol: str, val: dict) -> None:
    with _CACHE_LOCK:
        _SLOT_CACHE[symbol.upper()] = (val, time.time())


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
        _FUND_CACHE.clear()
        _EPS_CACHE.clear()
        _ANN_CACHE.clear()
        _SLOT_CACHE.clear()


# ---------------------------------------------------------------------------
# Low-level HTTP helpers
# ---------------------------------------------------------------------------

def _eodhd_get_fundamentals(symbol: str, api_key: str) -> dict | None:
    """GET EODHD /api/fundamentals/{symbol}.{exchange}  Returns parsed JSON or None."""
    ticker = f"{symbol.upper()}.{_EODHD_EXCHANGE_SUFFIX}"
    url = f"{_EODHD_BASE}/fundamentals/{ticker}?api_token={api_key}&fmt=json"
    req = Request(url, headers={"User-Agent": "trading-corp/1.0"})
    try:
        with urlopen(req, timeout=_HTTP_TIMEOUT_SEC) as resp:
            body = resp.read()
        data = json.loads(body)
        if not isinstance(data, dict):
            log.warning("EODHD fundamentals for %s: unexpected response type %s", ticker, type(data))
            return None
        return data
    except HTTPError as e:
        log.warning("EODHD HTTP %s for %s: %s", e.code, ticker, e)
        return None
    except (URLError, OSError) as e:
        log.warning("EODHD network error for %s: %s", ticker, e)
        return None
    except Exception as e:
        log.warning("EODHD unexpected error for %s: %s", ticker, e)
        return None


def _eodhd_get_calendar_slots(symbol: str, api_key: str, dfrom: str, dto: str) -> dict:
    """GET EODHD /api/calendar/earnings for ONE symbol over [dfrom, dto].

    Returns {report_date_str: before_after_market}, value 'BeforeMarket' |
    'AfterMarket' | None. ADDITIONAL lookup for the BMO/AMC slot ONLY — the
    EPS/fundamentals path is untouched. {} on any failure. NULL
    before_after_market (~19% of rows, skewed foreign/OTC) is carried through as
    None (unknown) — never filled or guessed.
    """
    ticker = f"{symbol.upper()}.{_EODHD_EXCHANGE_SUFFIX}"
    url = (f"{_EODHD_BASE}/calendar/earnings?api_token={api_key}&fmt=json"
           f"&symbols={ticker}&from={dfrom}&to={dto}")
    req = Request(url, headers={"User-Agent": "trading-corp/1.0"})
    try:
        with urlopen(req, timeout=_HTTP_TIMEOUT_SEC) as resp:
            data = json.loads(resp.read())
    except HTTPError as e:
        log.warning("EODHD calendar HTTP %s for %s", e.code, ticker)
        return {}
    except (URLError, OSError) as e:
        log.warning("EODHD calendar network error for %s: %s", ticker, e)
        return {}
    except Exception as e:
        log.warning("EODHD calendar unexpected error for %s: %s", ticker, e)
        return {}
    rows = data.get("earnings", []) if isinstance(data, dict) else []
    out: dict = {}
    tgt = symbol.upper()
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").upper()
        if code.split(".")[0] != tgt:      # calendar 'code' carries the .US suffix
            continue
        rd = str(row.get("report_date") or "")[:10]
        if rd:
            out[rd] = row.get("before_after_market")  # 'BeforeMarket'|'AfterMarket'|None
    return out


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _compute_surprise(actual: float, estimate: float | None) -> float | None:
    if estimate is None:
        return None
    if estimate == 0.0:
        return None  # avoid div-by-zero; not meaningful
    return round((actual - estimate) / abs(estimate) * 100.0, 4)


def _normalise_fiscal_period(raw: str) -> str:
    """Convert 'YYYY-MM-DD' fiscal end date to 'YYYYQn' if possible; else return as-is."""
    try:
        d = date.fromisoformat(raw[:10])
        # Infer quarter from fiscal end month
        q = (d.month - 1) // 3 + 1
        return f"{d.year}Q{q}"
    except Exception:
        return raw


def _parse_eodhd_earnings(history: dict) -> list[QuarterlyEPS]:
    """Parse EODHD Earnings.History dict into QuarterlyEPS list.

    Each key is a date string; each value contains:
      - reportDate: YYYY-MM-DD  (the ANNOUNCEMENT date — what we want)
      - date:       YYYY-MM-DD  (fiscal period end — used for fiscal_period)
      - epsActual:  float | null
      - epsEstimate: float | null
    """
    rows: list[QuarterlyEPS] = []
    for key, item in history.items():
        try:
            actual_raw = item.get("epsActual")
            if actual_raw is None:
                continue  # skip unreported / future quarters

            actual_eps = float(actual_raw)

            # Announcement date (the PEAD-critical date)
            report_date_str = item.get("reportDate") or key
            report_date = date.fromisoformat(str(report_date_str)[:10])

            # Fiscal period derived from period-end date
            period_end_str = item.get("date") or key
            fiscal_str = _normalise_fiscal_period(str(period_end_str))

            estimate_raw = item.get("epsEstimate")
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
            log.debug("_parse_eodhd_earnings: skipping malformed row key=%s item=%s: %s", key, item, e)
    return rows


def _eodhd_next_earnings_date(history: dict, asof: date) -> date | None:
    """Next FUTURE earnings announcement date from an EODHD Earnings.History
    dict: the minimum ``reportDate`` strictly after ``asof``; None if none found.

    EODHD includes the upcoming quarter as an unreported row (epsActual=null)
    with its ``reportDate`` populated — verified live (AAPL.US → 2026-07-30,
    epsActual=null, epsEstimate=1.9). We do NOT filter on epsActual: a future
    reportDate is authoritative regardless of whether the actual has printed.
    """
    best: date | None = None
    for item in (history or {}).values():
        rd = item.get("reportDate") if isinstance(item, dict) else None
        if not rd:
            continue
        try:
            d = date.fromisoformat(str(rd)[:10])
        except (ValueError, TypeError):
            continue
        if d > asof and (best is None or d < best):
            best = d
    return best


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
    """Earnings data adapter: EPS history + announcement calendar + company facts.

    PRIMARY: EODHD REST API (key from env EODHD_API_KEY).
      - get_quarterly_eps: parses Earnings.History; report_date = reportDate
        (the ANNOUNCEMENT date, not the fiscal period end — critical for PEAD).
      - get_company_facts: parses Highlights.MarketCapitalization + General.Sector.
      - Both methods share a single 24h in-memory cache of the raw fundamentals
        JSON — one HTTP fetch per symbol per day serves both.

    FALLBACK: yfinance — activated when key absent or EODHD returns nothing.
      - get_quarterly_eps: actuals only (no estimate / surprise).
      - get_company_facts: not supported via yfinance fallback (returns None).

    LABELED: every response logs which source served it.
    NO auto-failover within a single call that already succeeded.

    get_recent_announcements: still backed by the EODHD fundamentals path
    per-symbol (secondary; labeled) — not a live calendar endpoint.
    """

    def __init__(self, api_key: str | None = None, db_url: str | None = None) -> None:
        """Create an EarningsProvider.

        api_key — override EODHD_API_KEY env var (for tests / injection).
        If None, reads from os.environ["EODHD_API_KEY"].
        Missing key → EODHD skipped, yfinance path only.
        db_url — if set, the EODHD feed's tri-state health is written to
        data_feed_status on each fetch (for the ops dashboard's Stage-0 strip).
        """
        self._api_key: str | None = api_key or os.environ.get("EODHD_API_KEY") or None
        self._db_url = db_url
        if not self._api_key:
            log.info(
                "EarningsProvider: EODHD_API_KEY not set — yfinance fallback only"
            )

    # ------------------------------------------------------------------
    # Internal: shared fundamentals fetch (one HTTP call serves both methods)
    # ------------------------------------------------------------------

    def _get_fundamentals(self, sym: str) -> dict | None:
        """Fetch (or return cached) raw EODHD fundamentals JSON for `sym`.

        Shared 24h cache: calling get_quarterly_eps then get_company_facts
        (or vice versa) on the same symbol within one day makes exactly one
        HTTP request.  Returns None on failure.
        """
        hit, cached = _fund_cache_get(sym)
        if hit:
            log.debug("EarningsProvider._get_fundamentals(%s): cache hit", sym)
            return cached
        if not self._api_key:
            return None
        data = _eodhd_get_fundamentals(sym, self._api_key)
        self._write_feed_status(sym, data)
        if data:
            _fund_cache_set(sym, data)
        return data

    def _write_feed_status(self, sym: str, data: dict | None) -> None:
        """Update the EODHD feed's tri-state health for the ops dashboard's
        Stage-0 strip. No-op unless `db_url` was provided; never raises."""
        if not self._db_url:
            return
        try:
            from trading_corp.persistence.pead_observability import upsert_feed_status
            if data:
                upsert_feed_status("eodhd", "live", ok=True, detail=f"{sym} ok",
                                   db_url=self._db_url)
            else:
                upsert_feed_status("eodhd", "down", detail=f"{sym}: empty/error",
                                   db_url=self._db_url)
        except Exception as e:  # never let observability break a data fetch
            log.debug("EarningsProvider feed-status write failed: %s", e)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_quarterly_eps(self, symbol: str) -> list[QuarterlyEPS] | None:
        """Return >=8 most-recent quarterly EPS rows (oldest→newest), or None.

        None means no data available — callers should treat as "don't block"
        (same contract as get_next_earnings in utils/market_data.py).

        PRIMARY: EODHD fundamentals (if key present); report_date is the
        ANNOUNCEMENT date (Earnings.History[key].reportDate) — the critical
        field for PEAD drift measurement.
        FALLBACK: yfinance (actuals only, no estimates, no announcement dates).
        The serving source is logged.
        """
        if not symbol:
            return None
        sym = symbol.upper()

        hit, cached = _eps_cache_get(sym)
        if hit:
            return cached

        result = self._fetch_quarterly_eps(sym)
        result = self._enrich_report_time(sym, result)  # ADDITIONAL calendar lookup for BMO/AMC slot (EPS path unchanged)
        _eps_cache_set(sym, result)
        return result

    def _enrich_report_time(
        self, sym: str, rows: "list[QuarterlyEPS] | None",
    ) -> "list[QuarterlyEPS] | None":
        """ADDITIONAL calendar lookup: set QuarterlyEPS.report_time (BMO/AMC) by
        joining /api/calendar/earnings on report_date (both sides bare ticker +
        YYYY-MM-DD). Cached per symbol (24h). Does NOT touch EPS values or the
        fundamentals path; on any gap the slot stays None (unknown). Called from
        get_quarterly_eps, which the strategy runs via asyncio.to_thread — so
        this HTTP never blocks the event loop.
        """
        if not rows or not self._api_key:
            return rows
        hit, slots = _slot_cache_get(sym)
        if not hit:
            dfrom = min(r.report_date for r in rows).isoformat()
            dto = max(r.report_date for r in rows).isoformat()
            slots = _eodhd_get_calendar_slots(sym, self._api_key, dfrom, dto)
            _slot_cache_set(sym, slots)
        if not slots:
            return rows
        out: list[QuarterlyEPS] = []
        for r in rows:
            slot = slots.get(r.report_date.isoformat())  # None if absent OR NULL in calendar
            out.append(replace(r, report_time=slot) if slot is not None else r)
        return out

    def get_company_facts(self, symbol: str) -> dict | None:
        """Return {"market_cap": float|None, "sector": str|None} for `symbol`.

        Sourced from the same EODHD fundamentals JSON as get_quarterly_eps
        (shared 24h cache — one HTTP fetch serves both methods).
        Returns None on failure (key absent, network error, etc.).
        """
        if not symbol:
            return None
        sym = symbol.upper()
        data = self._get_fundamentals(sym)
        if data is None:
            log.info(
                "EarningsProvider.get_company_facts(%s): no EODHD data — returning None",
                sym,
            )
            return None
        highlights = data.get("Highlights") or {}
        general = data.get("General") or {}
        market_cap_raw = highlights.get("MarketCapitalization")
        sector_raw = general.get("Sector")
        result = {
            "market_cap": float(market_cap_raw) if market_cap_raw is not None else None,
            "sector": str(sector_raw) if sector_raw is not None else None,
        }
        log.info(
            "EarningsProvider.get_company_facts(%s): served by EODHD "
            "(market_cap=%s sector=%s)",
            sym, result["market_cap"], result["sector"],
        )
        return result

    def get_next_earnings_date(
        self, symbol: str, asof: date | None = None,
    ) -> date | None:
        """Next FUTURE earnings announcement date (EODHD ``reportDate``), or None.

        PRIMARY source for ``utils/market_data.get_next_earnings`` — the
        earnings-AVOIDANCE gate used by PMCC + the iron-condor strategies. Reuses
        the shared 24h fundamentals cache (same single HTTP/symbol/day fetch as
        ``get_quarterly_eps`` / ``get_company_facts``). EODHD-only here; the
        labeled yfinance fallback lives in the market_data wrapper (no
        auto-failover). None = no EODHD data → caller treats as "no earnings
        filter, don't block" (same contract as the legacy function).
        """
        if not symbol:
            return None
        data = self._get_fundamentals(symbol.upper())
        if not data:
            return None
        history = (data.get("Earnings") or {}).get("History") or {}
        return _eodhd_next_earnings_date(history, asof or date.today())

    def get_recent_announcements(
        self,
        on_date: date,
        lookback_days: int = 1,
    ) -> list[str]:
        """Return symbols that reported earnings on/within lookback_days of on_date.

        NOTE: EODHD does not expose a cross-symbol calendar endpoint.  This
        method is NOT the primary path used by the PEAD backtest (which derives
        announcement dates directly from QuarterlyEPS.report_date).  Returns []
        unless you call it with a per-symbol universe approach; keep for
        interface compatibility.

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
        # --- PRIMARY: EODHD ---
        if self._api_key:
            data = self._get_fundamentals(sym)
            if data is not None:
                history = (data.get("Earnings") or {}).get("History") or {}
                if isinstance(history, dict) and history:
                    rows = _parse_eodhd_earnings(history)
                    if rows:
                        rows_sorted = sorted(rows, key=lambda r: r.report_date)
                        # Most-recent 8+ quarters; return all if fewer
                        result = rows_sorted[-max(8, len(rows_sorted)):]
                        log.info(
                            "EarningsProvider.get_quarterly_eps(%s): served by EODHD "
                            "(%d quarters)",
                            sym, len(result),
                        )
                        return result
            log.info(
                "EarningsProvider.get_quarterly_eps(%s): EODHD returned nothing "
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
        # EODHD does not have a cross-symbol calendar endpoint; return [].
        # get_recent_announcements is not used by the backtest pipeline.
        log.info(
            "EarningsProvider.get_recent_announcements(%s +%dd): EODHD has no "
            "cross-symbol calendar endpoint — returning []",
            on_date, lookback_days,
        )
        return []
