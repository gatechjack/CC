"""Kalshi Sports Scout — read-only observer.

Pulls Kalshi sports markets (MLB, NBA, NHL, MLS, NFL — leagues with
liquid bookmaker coverage on the-odds-api), maps each ticker to a
bookmaker game via team-code lookup, fetches vig-removed median
bookmaker implied probability, and logs the divergence vs Kalshi's
implied price.

**No order emission.** Designed as a 7-day observation pass to validate
edge magnitude before committing to a full trading division build (B/C).

Audit kinds:
  kalshi_sports_scout_scan          — per-cycle summary
  kalshi_sports_observed            — per-market divergence row (mapped + priced)
  kalshi_sports_scout_unmapped      — per-market that couldn't be mapped
  kalshi_sports_scout_no_api_key    — fired once at startup if creds missing

To stay under the-odds-api free tier (500 req/month):
  - Per-sport cache 30 min in OddsAPIClient (one /odds request returns
    ~all games for the sport)
  - Default poll_interval_sec=900 (15 min); per scan we hit at most
    N_LEAGUES distinct sport_keys (~5) for one request each
  - 15min × 5 sports = ~3000 calls/month worst-case; in practice the
    30-min cache halves that → ~1500/month — over free quota; will
    likely need paid tier OR longer poll interval. Tune in deploy.

Configurable knobs (strategies.yaml `kalshi_sports_scout:`):
  enabled, poll_interval_sec, divergence_log_threshold_pct,
  leagues (list of league codes to scout).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from trading_corp.data.odds_api_client import OddsAPIClient, GameOdds
from trading_corp.data.sports_team_mapping import (
    LEAGUE_TO_SPORT_KEY,
    LEAGUE_TEAMS,
    find_matching_game,
    parse_sports_ticker,
)

log = logging.getLogger(__name__)


# Game-moneyline series tickers within Kalshi's "Sports" category. The
# Sports category contains ~2000 series across 50+ market types
# (futures, season wins, playoff brackets, props, props variants per
# league); we want only the four game-h2h series. Exact-match
# semantics (not prefix) so adjacent series like KXNBAGAMES /
# KXNBAGAME7 don't sweep in. NFL game-moneyline series does NOT
# currently exist in Sports (probe 2026-05-23 found only KXNFLGAMETD /
# KXNFLGAMEFG / KXNFLGAMESACK — all props, no game-h2h); re-probe and
# re-add once in-season approaches kick-off.
_SCOUT_SERIES_FILTER: tuple[str, ...] = (
    "KXMLBGAME",
    "KXNBAGAME",
    "KXNHLGAME",
    "KXMLSGAME",
)


class KalshiSportsScoutAgent:
    """Read-only sports observer. Logs divergence; emits no orders.

    Construction: pass `odds_api_key` (or None — agent runs in stub mode
    logging `no_api_key` instead of fetching). The agent owns its own
    OddsAPIClient lifecycle.
    """

    name = "kalshi_sports_scout"

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
                    data = yaml.safe_load(f) or {}
                self._strat_cfg = data.get(self.name) or {}
                self._strat_mtime = sm
        except Exception as e:
            log.warning("kalshi_sports_scout: yaml reload failed: %s", e)
            self._strat_cfg = {}

    @property
    def enabled(self) -> bool:
        self._reload()
        return bool(self._strat_cfg.get("enabled", False))

    @property
    def has_credentials(self) -> bool:
        return self._client.has_credentials

    async def close(self) -> None:
        await self._client.close()

    # ── public scan entry ────────────────────────────────────────────────

    async def run_scan_cycle(
        self, kalshi_broker: Any, *, logger_agent: Any = None,
    ) -> None:
        """One scout cycle. Always returns None (no orders).

        Cycle:
          1. Discover Kalshi sports markets (Sports category)
          2. Parse each ticker; group by league
          3. For each league with parsed markets, fetch bookmaker games
             once (per-sport cache amortizes within and across cycles)
          4. For each Kalshi market, find matching bookmaker game and
             log divergence
        """
        self._reload()
        if not self.enabled:
            return
        if not self.has_credentials:
            if logger_agent is not None:
                logger_agent.log_event(
                    self.name, "kalshi_sports_scout_no_api_key",
                    {"strategy": self.name,
                     "note": "ODDS_API_KEY not set; scout in stub mode"},
                )
            return

        disc_cfg = self._strat_cfg.get("discovery") or {}
        max_series = int(disc_cfg.get("max_series_per_category", 50))
        max_markets = int(disc_cfg.get("max_markets_per_series", 50))
        cache_ttl = float(disc_cfg.get("cache_ttl_sec", 900))
        leagues_filter = self._strat_cfg.get("leagues") or list(LEAGUE_TO_SPORT_KEY.keys())
        leagues_filter = [str(x).upper() for x in leagues_filter]
        div_log_threshold = float(
            self._strat_cfg.get("divergence_log_threshold_pct", 0.0)
        )

        # 1. Discover Sports markets.
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
                    series_filter=_SCOUT_SERIES_FILTER,
                )
                self._discovery_ts = now
            except Exception as e:
                log.warning("kalshi_sports_scout: discovery failed: %s", e)
                return
        events = (self._discovery_cache.events
                  if self._discovery_cache is not None else [])

        # 2. Parse + group by league.
        by_league: dict[str, list[tuple[Any, Any]]] = {}
        n_pre = 0
        n_unmapped = 0
        unmapped_audits: list[dict] = []
        for event in events:
            for m in event.markets:
                n_pre += 1
                parsed = parse_sports_ticker(m.ticker or "")
                if parsed is None:
                    n_unmapped += 1
                    unmapped_audits.append({
                        "strategy": self.name,
                        "ticker": m.ticker, "event_ticker": m.event_ticker,
                        "category": event.category,
                        "reason": "ticker_parse_fail_or_unsupported_league",
                    })
                    continue
                if parsed.league not in leagues_filter:
                    n_unmapped += 1
                    unmapped_audits.append({
                        "strategy": self.name,
                        "ticker": m.ticker, "league": parsed.league,
                        "reason": "league_not_in_scout_filter",
                    })
                    continue
                if parsed.team_a_name is None or parsed.team_b_name is None:
                    n_unmapped += 1
                    unmapped_audits.append({
                        "strategy": self.name,
                        "ticker": m.ticker, "league": parsed.league,
                        "team_a": parsed.team_a, "team_b": parsed.team_b,
                        "reason": "team_code_not_in_mapping",
                    })
                    continue
                by_league.setdefault(parsed.league, []).append((m, parsed))

        # 3. Fetch bookmaker games per league (cached 30 min).
        games_by_league: dict[str, list[GameOdds]] = {}
        for league in by_league.keys():
            sport_key = LEAGUE_TO_SPORT_KEY[league]
            try:
                games_by_league[league] = await self._client.get_games(sport_key)
            except Exception as e:
                log.warning("kalshi_sports_scout: odds_api fetch failed (%s): %s",
                            league, e)
                games_by_league[league] = []

        # 4. Per-market divergence logging.
        n_observed = 0
        n_no_game_match = 0
        for league, items in by_league.items():
            games = games_by_league.get(league, [])
            for m, parsed in items:
                game = find_matching_game(parsed, games)
                if game is None:
                    n_no_game_match += 1
                    if logger_agent is not None:
                        logger_agent.log_event(
                            self.name, "kalshi_sports_scout_unmapped",
                            {"strategy": self.name,
                             "ticker": m.ticker, "league": league,
                             "team_a_name": parsed.team_a_name,
                             "team_b_name": parsed.team_b_name,
                             "reason": "no_game_match_in_odds_api"},
                        )
                    continue
                # Determine which side (home/away) corresponds to our YES.
                yes_is_home = (game.home_team or "").lower() == (parsed.team_a_name or "").lower()
                bookmaker_yes_prob = (
                    game.implied_home if yes_is_home else game.implied_away
                )

                # Kalshi implied (from yes_ask cents); fall back to no_ask.
                yes_ask_cents = getattr(m, "yes_ask", None)
                no_ask_cents = getattr(m, "no_ask", None)
                kalshi_yes = (yes_ask_cents / 100.0) if yes_ask_cents else None
                kalshi_no = (no_ask_cents / 100.0) if no_ask_cents else None
                kalshi_implied_yes = None
                if kalshi_yes is not None and 0 < kalshi_yes < 1:
                    kalshi_implied_yes = kalshi_yes
                elif kalshi_no is not None and 0 < kalshi_no < 1:
                    kalshi_implied_yes = 1.0 - kalshi_no

                if kalshi_implied_yes is None:
                    continue   # no valid implied; skip silently

                divergence_pct = (bookmaker_yes_prob - kalshi_implied_yes) * 100.0
                abs_div = abs(divergence_pct)

                if abs_div < div_log_threshold:
                    continue  # below logging threshold; skip audit

                n_observed += 1
                if logger_agent is not None:
                    logger_agent.log_event(
                        self.name, "kalshi_sports_observed",
                        {
                            "strategy": self.name,
                            "ticker": m.ticker, "league": league,
                            "team_a_code": parsed.team_a,
                            "team_b_code": parsed.team_b,
                            "team_a_name": parsed.team_a_name,
                            "team_b_name": parsed.team_b_name,
                            "yes_side_code": parsed.yes_side,
                            "yes_is_home": yes_is_home,
                            "commenced_at": game.commenced_at,
                            "n_books": game.n_books,
                            "bookmaker_yes_implied": round(bookmaker_yes_prob, 4),
                            "median_vig_pct": round(game.median_vig_pct, 3),
                            "kalshi_implied_yes": round(kalshi_implied_yes, 4),
                            "divergence_pct": round(divergence_pct, 2),
                            "abs_divergence_pct": round(abs_div, 2),
                            "would_fire_buy": "yes" if divergence_pct > 0 else "no",
                            "expected_expiration_time": getattr(
                                m, "expected_expiration_time", None,
                            ),
                        },
                    )

        # 5. Cycle summary
        if logger_agent is not None:
            logger_agent.log_event(
                self.name, "kalshi_sports_scout_scan",
                {
                    "strategy": self.name,
                    "markets_pre_filter": n_pre,
                    "n_unmapped": n_unmapped,
                    "n_no_game_match": n_no_game_match,
                    "n_observed": n_observed,
                    "leagues_scouted": list(by_league.keys()),
                    "odds_api_quota_remaining": self._client.quota_remaining,
                    "odds_api_quota_used": self._client.quota_used,
                },
            )
        # Audit unmapped (only first 10 per cycle to bound payload volume)
        if logger_agent is not None:
            for ua in unmapped_audits[:10]:
                logger_agent.log_event(
                    self.name, "kalshi_sports_scout_unmapped", ua,
                )
