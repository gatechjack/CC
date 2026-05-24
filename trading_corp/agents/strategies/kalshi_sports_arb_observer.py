"""Kalshi Sports Arbitrage observer — Phase 0.

Read-only. NEVER emits orders. Writes `kalshi_sports_arb_observation`
audit rows for the new Kalshi Sports Arbitrage division's Phase 0
verdict, with raw Kalshi quotes + per-book sportsbook prices +
EV-at-fill for both Hypotheses A (cross-venue arb) and B (lead-lag
directional) computed at qty=10 contracts via `_sports_math`.

Distinct from `kalshi_sports_scout` (running on prod since 2026-05-14,
divergence-pct only, h2h-only, 100x units bug per
[[project-kalshi-sports-scout-phase0-blocked]]) — both can coexist.

Phase 0 scope (per Phase 0 plan):
  - NBA only.
  - Full-game markets only — ML (KXNBAGAME-*) is the only Kalshi
    market type currently in scope. Audit evidence shows Kalshi does
    NOT offer full-game spread/total binary contracts for NBA;
    KXNBA1HSPREAD/KXNBA1HTOTAL exist as 1H markets but are out of
    Phase 0 scope. KXNBASERIES*, KXNBADRAFT* etc. are exotic and
    excluded.
  - qty=10 contracts as the EV-at-fill sizing assumption.

Audit payload per observation (one row per matched market per cycle):
  strategy, division, observation_id (uuid4), matching_key (league,
  game_date_utc, team_home, team_away, market_type, line_value, side),
  kalshi_quote (raw yes_bid/yes_ask/no_bid/no_ask in dollars + sanity
  guard flag), book_prices (raw per-book per-side), a_arb_best (best
  EV across the per-book A-arb candidates), b_ev_dollars, sharp_book_used,
  flags (no_kalshi_quote, kalshi_quote_invalid, no_pinnacle_used_proxy,
  no_book_match, no_book_with_opposing_side).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from trading_corp.agents.strategies._sports_math import (
    LegFill,
    compute_ev_at_fill_a_arb,
    compute_ev_at_fill_b_directional,
    kalshi_fee,
)
from trading_corp.data.odds_api_client import (
    BookPrice,
    GameLine,
    OddsAPIClient,
)
from trading_corp.data.sports_team_mapping import (
    LEAGUE_TO_SPORT_KEY,
    parse_sports_ticker,
)

log = logging.getLogger(__name__)


# Ticker prefix → in-scope market_type. Anything else is flagged
# out_of_scope (not silently dropped) so the audit captures the
# universe Kalshi was actually offering.
_PHASE0_NBA_TICKER_PREFIXES: dict[str, str] = {
    "KXNBAGAME": "game_ml",
}

# Ticker prefixes for NBA Kalshi markets known to exist but out of
# Phase 0 scope (per audit evidence as of 2026-05-23). Tracked
# explicitly so a cycle summary can report market-type coverage.
_OUT_OF_SCOPE_NBA_PREFIXES: tuple[str, ...] = (
    "KXNBA1HSPREAD", "KXNBA1HTOTAL", "KXNBA1HWINNER",
    "KXNBAOVERTIME", "KXNBATEAMTOTAL",
    "KXNBASERIESSPREAD", "KXNBASERIESROADWIN", "KXNBASERIESGAMES",
    "KXNBADRAFTPICK", "KXNBADRAFTTEAM", "KXNBADRAFTCOMP",
    "KXNBAMVP", "KXNBAEFINMVP", "KXNBAWFINMVP",
    "KXNBA2NDTEAM", "KXNBA2NDTEAMDEF",
    "KXNBASTL", "KXNBAPTS",
    "KXNBAEAST", "KXNBAUNBEATEN", "KXNBATEAM",
)


def classify_nba_ticker(ticker: str) -> tuple[str, str | None]:
    """Returns (status, market_type_or_None).

    status ∈ {"in_scope", "out_of_scope", "unknown"}.
    """
    if not ticker:
        return "unknown", None
    head = ticker.split("-", 1)[0]
    if head in _PHASE0_NBA_TICKER_PREFIXES:
        return "in_scope", _PHASE0_NBA_TICKER_PREFIXES[head]
    if head in _OUT_OF_SCOPE_NBA_PREFIXES:
        return "out_of_scope", None
    return "unknown", None


# ── MLB classifier — sibling, NOT a generalization ────────────────────────
# Added 2026-05-23 after MLB pre-flip audit. Kalshi MLB ticker shape:
# KXMLBGAME-{YYMMMDD}{HHMM}{TEAM_BLOB}-{YES_SIDE}. Audit-evidence
# confirms Kalshi offers only KXMLBGAME (game ML) for live MLB games;
# all other KXMLB* prefixes are season-long futures / props / awards.
# NO KXMLBSPREAD or KXMLBTOTAL — same Phase 0 ML-only constraint as NBA.

_PHASE0_MLB_TICKER_PREFIXES: dict[str, str] = {
    "KXMLBGAME": "game_ml",
}

_OUT_OF_SCOPE_MLB_PREFIXES: tuple[str, ...] = (
    "KXMLBWINS",                            # season-win totals
    "KXMLBSTATCOUNT",                       # generic stat-counter futures
    "KXMLBRFI",                             # Run First Inning prop
    "KXMLBKS",                              # strikeout-pitcher props
    "KXMLBPLAYOFFS",
    "KXMLBPITCHEROTM",                      # Pitcher of the Month
    "KXMLBLSTREAK",                         # longest streak
    "KXMLBEOTY",                            # Executive of the Year
    "KXMLBNL", "KXMLBNLWEST", "KXMLBNLEAST", "KXMLBNLCENT",
    "KXMLBNLROTY", "KXMLBNLMVP", "KXMLBNLHAARON",
    "KXMLBAL", "KXMLBALMVP", "KXMLBALHAARON",
    "KXMLBALRELOTY", "KXMLBALCPOTY",
)


def classify_mlb_ticker(ticker: str) -> tuple[str, str | None]:
    """MLB sibling of classify_nba_ticker. Same return shape.

    NOT a generalization of the NBA function — kept separate per the
    'preserve NBA path intact' constraint. Future leagues add their
    own classify_<league>_ticker + _PHASE0_*_TICKER_PREFIXES siblings.
    """
    if not ticker:
        return "unknown", None
    head = ticker.split("-", 1)[0]
    if head in _PHASE0_MLB_TICKER_PREFIXES:
        return "in_scope", _PHASE0_MLB_TICKER_PREFIXES[head]
    if head in _OUT_OF_SCOPE_MLB_PREFIXES:
        return "out_of_scope", None
    return "unknown", None


# ── League dispatch table ─────────────────────────────────────────────────
# Read by run_scan_cycle to look up (classifier, ticker_prefix) per
# configured league. Adding a new league = adding one entry here +
# sibling classify_* function + ticker-prefix constants. Never mutate
# existing league entries to add new league semantics.

_PHASE0_LEAGUE_CLASSIFIERS: dict[str, tuple[Any, str]] = {
    "NBA": (classify_nba_ticker, "KXNBA"),
    "MLB": (classify_mlb_ticker, "KXMLB"),
}


# ── Series filter for kalshi_broker.list_markets() ────────────────────────
# Kalshi Sports category has ~2000 series; without a series_filter, the
# discovery's max_series_per_category cap returns a rotating ~50-series
# slice and in-scope leagues land only ~11% of scans (verified bug per
# kalshi_sports_scout commit b880b66). Pass the in-scope series tuple
# to list_markets() so the cap counts ONLY relevant series.
#
# Derived from the per-league ticker-prefix dicts so adding a new league
# automatically flows through. Sibling to _PHASE0_LEAGUE_CLASSIFIERS
# (not extending its tuple shape — preserves existing dispatch tests).

_PHASE0_LEAGUE_SERIES_FILTER: dict[str, tuple[str, ...]] = {
    "NBA": tuple(_PHASE0_NBA_TICKER_PREFIXES.keys()),
    "MLB": tuple(_PHASE0_MLB_TICKER_PREFIXES.keys()),
}


def _vig_remove_two_sides(p1: float, p2: float) -> tuple[float, float]:
    """For one book's two-sided market (h2h or spread or total).
    Returns (vig_removed_p1, vig_removed_p2).
    """
    total = p1 + p2
    if total <= 0:
        return 0.0, 0.0
    return p1 / total, p2 / total


def _pick_pinnacle_or_proxy(
    books: tuple[BookPrice, ...],
    home_side: str = "home",
    away_side: str = "away",
    proxy_preference: tuple[str, ...] = ("draftkings", "fanduel", "betmgm"),
) -> tuple[str, float, float, bool] | None:
    """Returns (book_key_used, vig_removed_home_prob, vig_removed_away_prob,
    is_pinnacle).

    Tries Pinnacle first (both sides must be present at the same book to
    vig-remove). Falls back to median of `proxy_preference` books.
    Returns None if neither path yields a valid two-sided quote.
    """
    by_book: dict[str, dict[str, BookPrice]] = {}
    for bp in books:
        by_book.setdefault(bp.book_key, {})[bp.side] = bp

    # Pinnacle path.
    if "pinnacle" in by_book and home_side in by_book["pinnacle"] and away_side in by_book["pinnacle"]:
        p_h = by_book["pinnacle"][home_side].implied_raw
        p_a = by_book["pinnacle"][away_side].implied_raw
        vh, va = _vig_remove_two_sides(p_h, p_a)
        if vh > 0 and va > 0:
            return ("pinnacle", vh, va, True)

    # Proxy fallback: vig-remove per book, then take the median across
    # books with both sides.
    h_probs: list[float] = []
    a_probs: list[float] = []
    used_books: list[str] = []
    for bk in proxy_preference:
        if bk in by_book and home_side in by_book[bk] and away_side in by_book[bk]:
            vh, va = _vig_remove_two_sides(
                by_book[bk][home_side].implied_raw,
                by_book[bk][away_side].implied_raw,
            )
            if vh > 0 and va > 0:
                h_probs.append(vh)
                a_probs.append(va)
                used_books.append(bk)
    if h_probs:
        h_probs.sort()
        a_probs.sort()
        mid = len(h_probs) // 2
        return ("median:" + "+".join(used_books), h_probs[mid], a_probs[mid], False)
    return None


def _kalshi_dollars(cents_or_dollars: Any) -> float | None:
    """Kalshi market_map returns prices in dollars per
    `data/kalshi_market_map.py` (verified via 2026-05-23 scout
    units-bug post-mortem). Float-coerce; reject None/0/negative."""
    if cents_or_dollars is None:
        return None
    try:
        v = float(cents_or_dollars)
    except (TypeError, ValueError):
        return None
    if not (0.0 < v < 1.0):
        return None
    return v


@dataclass(frozen=True)
class _ArbCandidate:
    book_key: str
    book_side: str
    book_american: int
    book_price_per_unit: float
    ev_dollars: float
    is_arb: bool


def _evaluate_a_arb_for_ml(
    *,
    kalshi_yes_ask: float,
    kalshi_no_ask: float | None,
    books_h2h: tuple[BookPrice, ...],
    yes_is_home: bool,
    qty: int,
) -> list[_ArbCandidate]:
    """For each book offering the OPPOSING-side h2h quote, compute the
    two-leg arb of (Kalshi YES) + (book opposing side).

    If Kalshi YES is on the home side (yes_is_home=True), opposing book
    side is "away" — book pays when Kalshi NO would have been correct.
    Returns one _ArbCandidate per book that offers the opposing side.
    """
    # Kalshi leg
    k_fee = kalshi_fee(qty, kalshi_yes_ask)
    kalshi_leg = LegFill(
        venue="kalshi", side="yes", qty=qty,
        price_per_unit=kalshi_yes_ask, fee=k_fee,
    )
    opposing_side = "away" if yes_is_home else "home"
    candidates: list[_ArbCandidate] = []
    by_book: dict[str, dict[str, BookPrice]] = {}
    for bp in books_h2h:
        by_book.setdefault(bp.book_key, {})[bp.side] = bp
    for book_key, sides in by_book.items():
        if opposing_side not in sides:
            continue
        bp = sides[opposing_side]
        # implied_raw is 1/decimal_odds. For our unit-normalized leg
        # this IS the price-per-unit you pay to get $1 if the opposing
        # side wins.
        book_leg = LegFill(
            venue=book_key, side=opposing_side, qty=qty,
            price_per_unit=bp.implied_raw, fee=0.0,
        )
        result = compute_ev_at_fill_a_arb(kalshi_leg, book_leg)
        candidates.append(_ArbCandidate(
            book_key=book_key,
            book_side=opposing_side,
            book_american=bp.american,
            book_price_per_unit=bp.implied_raw,
            ev_dollars=result.ev_dollars,
            is_arb=result.is_arb,
        ))
    # Sort by EV desc; caller picks the best.
    candidates.sort(key=lambda c: c.ev_dollars, reverse=True)
    return candidates


# ── Agent ────────────────────────────────────────────────────────────────

class KalshiSportsArbObserverAgent:
    """Read-only Phase 0 observer. Owns its OddsAPIClient lifecycle."""

    name = "kalshi_sports_arb_observer"
    division = "kalshi_arbitrage"

    def __init__(
        self,
        *,
        odds_api_key: str | None,
        db_url: str | None = None,
    ) -> None:
        self._db_url = db_url
        self._strategies_yaml = Path("config/strategies.yaml")
        self._strat_mtime: float = 0.0
        self._strat_cfg: dict[str, Any] = {}
        self._client = OddsAPIClient(odds_api_key)
        self._reload()
        self._discovery_cache: Any = None
        self._discovery_ts: datetime | None = None

    def _reload(self) -> None:
        try:
            sm = self._strategies_yaml.stat().st_mtime
            if sm != self._strat_mtime:
                with self._strategies_yaml.open("r") as f:
                    all_cfg = yaml.safe_load(f) or {}
                self._strat_cfg = all_cfg.get(self.name, {}) or {}
                self._strat_mtime = sm
        except FileNotFoundError:
            self._strat_cfg = {}
        except Exception as e:
            log.warning("kalshi_sports_arb_observer: yaml reload failed: %s", e)
            self._strat_cfg = {}

    @property
    def enabled(self) -> bool:
        self._reload()
        return bool(self._strat_cfg.get("enabled", False))

    @property
    def auto_execute(self) -> bool:
        return False  # observer NEVER emits orders

    @property
    def has_credentials(self) -> bool:
        return self._client.has_credentials

    async def close(self) -> None:
        await self._client.close()

    async def run_scan_cycle(
        self, kalshi_broker: Any, *, logger_agent: Any = None,
    ) -> None:
        """One observer cycle. Always returns None (no orders).

        Pipeline (per configured league):
          1. Discover Kalshi sports markets (once per cycle, shared).
          2. Filter to this league + in-scope ticker + mapped teams.
          3. Fetch per-book lines via OddsAPIClient.get_lines(sport_key).
          4. For each matched market, compute A (per-book arb candidates)
             and B (sharp/proxy directional) EV-at-fill at qty.
          5. Write per-market audit. Aggregate cycle summary across leagues.

        League dispatch via _PHASE0_LEAGUE_CLASSIFIERS. Adding a new
        league = sibling classify_<league>_ticker + ticker-prefix
        constants + one dispatch entry; existing leagues untouched.
        """
        self._reload()
        if not self.enabled:
            return
        if not self.has_credentials:
            if logger_agent is not None:
                logger_agent.log_event(
                    self.name, "kalshi_sports_arb_no_api_key",
                    {"strategy": self.name, "division": self.division,
                     "note": "ODDS_API_KEY not set; observer in stub mode"},
                )
            return

        disc_cfg = self._strat_cfg.get("discovery") or {}
        max_series = int(disc_cfg.get("max_series_per_category", 50))
        max_markets = int(disc_cfg.get("max_markets_per_series", 50))
        cache_ttl = float(disc_cfg.get("cache_ttl_sec", 900))
        sizing_cfg = self._strat_cfg.get("sizing_for_ev_calc") or {}
        qty = int(sizing_cfg.get("contracts", 10))

        configured_leagues = [
            str(x).upper()
            for x in (self._strat_cfg.get("leagues") or ["NBA"])
        ]
        sharp_books_cfg = self._strat_cfg.get("sharp_book_preference") or [
            "pinnacle", "draftkings", "fanduel", "betmgm",
        ]
        books_filter = tuple(str(b).lower() for b in sharp_books_cfg)

        # 1. Kalshi discovery (shared across leagues). Build series_filter
        # union across configured leagues so list_markets() pre-filters at
        # the broker layer (avoids the rotating-slice bug — see
        # _PHASE0_LEAGUE_SERIES_FILTER comment).
        series_filter_parts: list[str] = []
        for league in configured_leagues:
            series_filter_parts.extend(_PHASE0_LEAGUE_SERIES_FILTER.get(league, ()))
        series_filter: tuple[str, ...] | None = (
            tuple(series_filter_parts) if series_filter_parts else None
        )

        now = datetime.now(timezone.utc)
        need_refresh = (
            self._discovery_cache is None
            or self._discovery_ts is None
            or (now - self._discovery_ts).total_seconds() > cache_ttl
        )
        if need_refresh:
            try:
                self._discovery_cache = await kalshi_broker.list_markets(
                    categories=("Sports",),
                    max_series_per_category=max_series,
                    max_markets_per_series=max_markets,
                    series_filter=series_filter,
                )
                self._discovery_ts = now
            except Exception as e:
                log.warning("kalshi_sports_arb_observer: discovery failed: %s", e)
                return
        events = (self._discovery_cache.events
                  if self._discovery_cache is not None else [])

        # 2-5. Per-league pipeline. Aggregate counts for the cycle summary.
        per_league_summary: dict[str, dict[str, Any]] = {}
        for league in configured_leagues:
            league_summary = await self._process_league(
                league=league,
                events=events,
                qty=qty,
                books_filter=books_filter,
                logger_agent=logger_agent,
            )
            if league_summary is not None:
                per_league_summary[league] = league_summary

        # Cycle summary — keep top-level totals across leagues for
        # backward-compatible audit-query consumers; per-league
        # breakdown is in `per_league`.
        total_pre = sum(s["markets_pre_filter"] for s in per_league_summary.values())
        total_in_scope = sum(s["n_in_scope"] for s in per_league_summary.values())
        total_out_of_scope = sum(s["n_out_of_scope"] for s in per_league_summary.values())
        total_unmapped = sum(s["n_unmapped"] for s in per_league_summary.values())
        total_observed = sum(s["n_observed"] for s in per_league_summary.values())
        total_no_book = sum(s["n_no_book_match"] for s in per_league_summary.values())
        # Merge out_of_scope prefix dicts across leagues
        merged_oos_prefixes: dict[str, int] = {}
        for s in per_league_summary.values():
            for k, v in s["out_of_scope_prefixes"].items():
                merged_oos_prefixes[k] = merged_oos_prefixes.get(k, 0) + v
        if logger_agent is not None:
            logger_agent.log_event(
                self.name, "kalshi_sports_arb_scan",
                {
                    "strategy": self.name,
                    "division": self.division,
                    "leagues_scanned": list(per_league_summary.keys()),
                    "markets_pre_filter": total_pre,
                    "n_in_scope": total_in_scope,
                    "n_out_of_scope": total_out_of_scope,
                    "n_unmapped": total_unmapped,
                    "n_observed": total_observed,
                    "n_no_book_match": total_no_book,
                    "out_of_scope_prefixes": merged_oos_prefixes,
                    "per_league": per_league_summary,
                    "odds_api_quota_remaining": self._client.quota_remaining,
                    "odds_api_quota_used": self._client.quota_used,
                },
            )

    async def _process_league(
        self,
        *,
        league: str,
        events: list,
        qty: int,
        books_filter: tuple[str, ...],
        logger_agent: Any,
    ) -> dict[str, Any] | None:
        """Filter → fetch → evaluate for one configured league.

        Returns a per-league summary dict (or None if the league has no
        registered classifier). Writes per-market audit rows directly.
        """
        dispatch = _PHASE0_LEAGUE_CLASSIFIERS.get(league)
        if dispatch is None or league not in LEAGUE_TO_SPORT_KEY:
            if logger_agent is not None:
                logger_agent.log_event(
                    self.name, "kalshi_sports_arb_unsupported_league",
                    {"strategy": self.name, "division": self.division,
                     "league": league,
                     "note": "no classifier or sport_key registered"},
                )
            return None
        classifier, kx_prefix = dispatch

        # Filter Kalshi markets for this league.
        in_scope: list[tuple[Any, Any, str]] = []
        n_pre = 0
        n_out_of_scope = 0
        n_unmapped = 0
        out_of_scope_prefixes: dict[str, int] = {}
        for event in events:
            for m in event.markets:
                ticker = m.ticker or ""
                if not ticker.startswith(kx_prefix):
                    continue
                n_pre += 1
                status, market_type = classifier(ticker)
                if status == "out_of_scope":
                    n_out_of_scope += 1
                    prefix = ticker.split("-", 1)[0]
                    out_of_scope_prefixes[prefix] = out_of_scope_prefixes.get(prefix, 0) + 1
                    continue
                if status != "in_scope" or market_type is None:
                    n_unmapped += 1
                    continue
                parsed = parse_sports_ticker(ticker)
                if parsed is None or parsed.league != league:
                    n_unmapped += 1
                    continue
                if parsed.team_a_name is None or parsed.team_b_name is None:
                    n_unmapped += 1
                    continue
                in_scope.append((m, parsed, market_type))

        # Fetch per-book lines from the-odds-api for this sport.
        if in_scope:
            try:
                lines: list[GameLine] = await self._client.get_lines(
                    LEAGUE_TO_SPORT_KEY[league],
                    markets=("h2h", "spreads", "totals"),
                    books=books_filter,
                )
            except Exception as e:
                log.warning(
                    "kalshi_sports_arb_observer: odds_api fetch failed (%s): %s",
                    league, e,
                )
                lines = []
        else:
            lines = []

        lines_by_key: dict[tuple[str, str, str], list[GameLine]] = {}
        for gl in lines:
            key = (gl.market, gl.home_team.lower(), gl.away_team.lower())
            lines_by_key.setdefault(key, []).append(gl)

        n_observed = 0
        n_no_book_match = 0
        for m, parsed, market_type in in_scope:
            game_h2h: GameLine | None = None
            yes_is_home: bool | None = None
            ta = (parsed.team_a_name or "").lower()
            tb = (parsed.team_b_name or "").lower()
            cand = lines_by_key.get(("h2h", ta, tb))
            if cand:
                game_h2h = cand[0]
                yes_is_home = True
            else:
                cand = lines_by_key.get(("h2h", tb, ta))
                if cand:
                    game_h2h = cand[0]
                    yes_is_home = False
            if game_h2h is None or yes_is_home is None:
                n_no_book_match += 1
                if logger_agent is not None:
                    logger_agent.log_event(
                        self.name, "kalshi_sports_arb_unmapped",
                        {"strategy": self.name, "division": self.division,
                         "league": league,
                         "ticker": m.ticker,
                         "team_a_name": parsed.team_a_name,
                         "team_b_name": parsed.team_b_name,
                         "reason": "no_book_h2h_match"},
                    )
                continue

            yes_bid = _kalshi_dollars(getattr(m, "yes_bid", None))
            yes_ask = _kalshi_dollars(getattr(m, "yes_ask", None))
            no_bid = _kalshi_dollars(getattr(m, "no_bid", None))
            no_ask = _kalshi_dollars(getattr(m, "no_ask", None))
            kalshi_quote_invalid = False
            if yes_ask is not None and no_ask is not None:
                total = yes_ask + no_ask
                if not (0.5 <= total <= 1.5):
                    kalshi_quote_invalid = True

            a_candidates: list[_ArbCandidate] = []
            if yes_ask is not None and not kalshi_quote_invalid:
                a_candidates = _evaluate_a_arb_for_ml(
                    kalshi_yes_ask=yes_ask,
                    kalshi_no_ask=no_ask,
                    books_h2h=game_h2h.books,
                    yes_is_home=yes_is_home,
                    qty=qty,
                )
            best_arb = a_candidates[0] if a_candidates else None

            pinnacle_or_proxy = _pick_pinnacle_or_proxy(
                game_h2h.books, home_side="home", away_side="away",
            )
            b_ev_dollars: float | None = None
            sharp_book_used: str | None = None
            used_pinnacle: bool = False
            if pinnacle_or_proxy is not None and yes_ask is not None and not kalshi_quote_invalid:
                book_key, vh, va, is_pinnacle = pinnacle_or_proxy
                model_prob_yes = vh if yes_is_home else va
                k_fee = kalshi_fee(qty, yes_ask)
                leg = LegFill(
                    venue="kalshi", side="yes", qty=qty,
                    price_per_unit=yes_ask, fee=k_fee,
                )
                b_res = compute_ev_at_fill_b_directional(leg, model_prob_yes)
                b_ev_dollars = b_res.ev_dollars
                sharp_book_used = book_key
                used_pinnacle = is_pinnacle

            n_observed += 1
            if logger_agent is not None:
                logger_agent.log_event(
                    self.name, "kalshi_sports_arb_observation",
                    {
                        "strategy": self.name,
                        "division": self.division,
                        "observation_id": uuid.uuid4().hex,
                        "matching_key": {
                            "league": league,
                            "game_date_utc": (game_h2h.commenced_at or "")[:10],
                            "team_home": game_h2h.home_team,
                            "team_away": game_h2h.away_team,
                            "market_type": market_type,
                            "line_value": None,
                            "side": parsed.yes_side,
                            "yes_is_home": yes_is_home,
                        },
                        "ticker": m.ticker,
                        "kalshi_quote": {
                            "yes_bid": yes_bid, "yes_ask": yes_ask,
                            "no_bid": no_bid, "no_ask": no_ask,
                        },
                        "kalshi_quote_invalid": kalshi_quote_invalid,
                        "book_prices": [
                            {"book_key": bp.book_key, "side": bp.side,
                             "line": bp.line, "american": bp.american,
                             "implied_raw": round(bp.implied_raw, 4)}
                            for bp in game_h2h.books
                        ],
                        "qty_used_for_ev": qty,
                        "a_arb_best": (
                            {"book_key": best_arb.book_key,
                             "book_side": best_arb.book_side,
                             "book_american": best_arb.book_american,
                             "ev_dollars": best_arb.ev_dollars,
                             "is_arb": best_arb.is_arb}
                            if best_arb else None
                        ),
                        "a_arb_n_candidates": len(a_candidates),
                        "b_ev_dollars": b_ev_dollars,
                        "sharp_book_used": sharp_book_used,
                        "pinnacle_used": used_pinnacle,
                        "expected_expiration_time": getattr(
                            m, "expected_expiration_time", None,
                        ),
                        "commenced_at": game_h2h.commenced_at,
                    },
                )

        return {
            "markets_pre_filter": n_pre,
            "n_in_scope": len(in_scope),
            "n_out_of_scope": n_out_of_scope,
            "n_unmapped": n_unmapped,
            "n_observed": n_observed,
            "n_no_book_match": n_no_book_match,
            "out_of_scope_prefixes": out_of_scope_prefixes,
        }
