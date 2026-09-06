"""Generic Polymarket -> Kalshi STRUCTURAL matcher for two-team, one-game-per-day sports.

Rung 1 (2026-09-06): the shared matcher for nfl, nba, nhl, wnba, cfb -- the way atp/wta share
the tennis matcher. MONEYLINE ONLY (Jack ruled 2026-09-06: total/spread wait for the in-season
strike-encoding probe). mlb KEEPS ITS OWN MODULE (`mlb_poly_kalshi_match`) UNTOUCHED -- its live
moneyline+total+spread path is byte-identical by construction (8 armed subs trade on it), exactly
as ufc stayed separate when atp/wta got tennis.

THE JOIN (identical to mlb's moneyline path, parameterized):
  Poly slug `{prefix}-{away}-{home}-{YYYY-MM-DD}[suffix]`; suffix => a non-moneyline market (SKIP).
  Kalshi ticker `{GAME_SERIES}-{YYMMMDD}{HHMM?}{TEAMBLOB}[G{n}]-{YES}`; two YES-side tickers per game.
  Both sides canonicalize their team CODE -> full name via a per-league team map, and the join key is
  (game_date_iso, frozenset{away_name, home_name}) -- so venue code differences (Poly `sea` vs Kalshi
  `SEA`) collapse to the same key. resolve_side picks the side the whale bet.

★ ACCEPTANCE (B2 shape): `generic(mlb-config)` reproduces `mlb_poly_kalshi_match.match_poly_to_kalshi`
  BYTE-IDENTICALLY on real mlb data (test_structural_match::test_mlb_equivalence). The generalized
  path EQUALS the direct original call, asserted -- the evidence the generalization is faithful for the
  new sports (which have no independent oracle).

★ DOUBLEHEADER-AWARE BUT INERT: only mlb has two games for the same teams on one date (`has_doubleheader`
  True). nfl/nba/nhl/wnba/cfb play one game per matchup per day, so the DH G-suffix parse + the
  `doubleheader_ambiguous` branch never fire for them -- but they are kept so mlb-config == mlb exactly.

★ SAFE-MISS on ambiguity/unmapped (the wrong-pick guard, the standing lens): an unmapped team code, a
  TIE/DRAW ticker, or a side that resolves to neither team -> a labelled MISS, NEVER a guessed ticker.
  Reuses mlb's `_norm` / `resolve_side` / `kalshi_to_iso_date` verbatim (NOT rebuilt).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# reuse mlb's league-agnostic helpers verbatim (accent/case fold, two-known-name side resolve, date conv)
from .mlb_poly_kalshi_match import _norm, resolve_side, kalshi_to_iso_date, iso_to_kalshi_date  # noqa: F401
from .sports_team_mapping import MLB_TEAMS, NBA_TEAMS, NHL_TEAMS, NFL_TEAMS, WNBA_TEAMS

COPYABLE_MARKET_TYPES = ("moneyline",)


@dataclass(frozen=True)
class StructuralLeague:
    """One league's config: the Poly slug prefix, the Kalshi per-game series, the code->full-name map."""
    category: str
    poly_prefix: str            # lowercase Poly slug prefix, e.g. "nfl"
    game_series: str            # Kalshi moneyline series, e.g. "KXNFLGAME"
    team_map: dict              # UPPER team code -> canonical full name (BOTH venues map into this)
    has_doubleheader: bool = False   # only mlb (kept inert for the others so mlb-config == mlb)


@dataclass(frozen=True)
class ParsedBet:
    market_type: str            # moneyline | non_moneyline | non_sport | unparseable
    date_iso: str | None
    away_code: str | None
    home_code: str | None
    away_name: str | None
    home_name: str | None
    side: str | None            # away | home (the team the whale bet)
    side_name: str | None
    fail_reason: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class KalshiGame:
    date_iso: str
    date_str: str
    time_str: str | None
    game_no: int | None
    team_a_code: str
    team_b_code: str
    team_a_name: str
    team_b_name: str
    ticker_by_side_code: dict   # {team_code: full KX{X}GAME ticker for that YES side}


@dataclass(frozen=True)
class MatchResult:
    status: str                 # matched | doubleheader_ambiguous | no_kalshi_contract | out_of_window |
                                # skip_non_moneyline | skip_non_game | skip_market_type_excluded | fail
    confidence: float
    kalshi_ticker: str | None = None
    kalshi_candidates: tuple = ()
    reason: str | None = None
    leg: str | None = None
    market_type: str | None = None


# ── Poly slug parse (mlb's moneyline shape, prefix-parameterized) ────────────────────────────────
def _poly_re(prefix: str):
    return re.compile(r"^%s-(?P<away>[a-z0-9]+)-(?P<home>[a-z0-9]+)-(?P<date>\d{4}-\d{2}-\d{2})(?P<suffix>.*)$"
                      % re.escape(prefix))


def parse_poly_bet(slug: str, outcome: str, cfg: StructuralLeague, title: str = None) -> ParsedBet:
    """Parse one Poly bet for `cfg`'s league. Only a suffix-free single-game slug is MONEYLINE (in
    scope); a suffix (`-total-...`, `-spread-...`, props) is a labelled non-moneyline SKIP; a slug not
    starting `{prefix}-` is non_sport. Unrecognized team codes on a moneyline slug -> fail_reason set
    (a SAFE miss, never a guessed side). `title` is accepted for signature-parity with tennis; unused."""
    raw = {"slug": slug, "outcome": outcome}
    m = _poly_re(cfg.poly_prefix).match(slug or "")
    if not m:
        mt = "non_moneyline" if (slug or "").startswith(cfg.poly_prefix + "-") else "non_sport"
        return ParsedBet(mt, None, None, None, None, None, None, None,
                         fail_reason="slug_no_game_match:%r" % slug, raw=raw)
    if m.group("suffix"):
        # a total/spread/prop market -- out of rung-1 (moneyline) scope; labelled skip, never a match.
        return ParsedBet("non_moneyline", m.group("date"), m.group("away").upper(), m.group("home").upper(),
                         None, None, None, None, fail_reason="non_moneyline_suffix:%r" % m.group("suffix"), raw=raw)
    date_iso = m.group("date")
    away_code = m.group("away").upper()
    home_code = m.group("home").upper()
    away_name = cfg.team_map.get(away_code)
    home_name = cfg.team_map.get(home_code)
    if away_name is None or home_name is None:
        missing = [c for c, n in ((away_code, away_name), (home_code, home_name)) if n is None]
        return ParsedBet("moneyline", date_iso, away_code, home_code, away_name, home_name,
                         None, None, fail_reason="unrecognized_team_code:%s" % missing, raw=raw)
    side = resolve_side(outcome, away_name, home_name)
    side_name = away_name if side == "away" else home_name if side == "home" else None
    return ParsedBet("moneyline", date_iso, away_code, home_code, away_name, home_name, side, side_name, raw=raw)


# ── Kalshi ticker parse + game index (mlb's DH-aware shape, series-parameterized) ─────────────────
def _kalshi_re(game_series: str):
    return re.compile(r"^%s-(?P<date>\d{2}[A-Z]{3}\d{2})(?P<time>\d{4})?(?P<mid>[A-Z0-9]+)-(?P<yes>[A-Z]+)\d*$"
                      % re.escape(game_series))


def parse_kalshi_ticker(ticker: str, cfg: StructuralLeague):
    """(date_str, time_str, yes_code, other_code, yes_name, other_name, game_no) or None. Mirrors
    mlb's parse_kalshi_mlb_ticker: yes-anchored blob split, TIE/DRAW -> None (skip), DH G-suffix stripped
    only when `has_doubleheader`, unmapped codes -> None (safe miss)."""
    m = _kalshi_re(cfg.game_series).match(ticker or "")
    if not m:
        return None
    mid, yes = m.group("mid"), m.group("yes")
    if yes in ("TIE", "DRAW"):
        return None
    game_no = None
    if cfg.has_doubleheader:
        dm = re.search(r"G(\d)$", mid)
        if dm:
            game_no = int(dm.group(1)); mid = mid[:dm.start()]
    if mid.startswith(yes):
        other = mid[len(yes):]
    elif mid.endswith(yes):
        other = mid[:-len(yes)]
    else:
        return None
    if not other:
        return None
    yes_name, other_name = cfg.team_map.get(yes), cfg.team_map.get(other)
    if yes_name is None or other_name is None:
        return None
    return (m.group("date"), m.group("time"), yes, other, yes_name, other_name, game_no)


def _game_key(date_iso: str, name1: str, name2: str):
    return (date_iso, frozenset({name1, name2}))


def build_game_index(tickers, cfg: StructuralLeague) -> dict:
    """{(date_iso, frozenset{names}): [KalshiGame]} -- a key with >1 game is a doubleheader (mlb only).
    Collects both side tickers per game. Non-two-team / unmapped / TIE tickers are skipped (safe)."""
    by_game: dict = {}
    for t in tickers:
        p = parse_kalshi_ticker(t, cfg)
        if p is None:
            continue
        date_str, time_str, yes_code, other_code, yes_name, other_name, game_no = p
        date_iso = kalshi_to_iso_date(date_str)
        if date_iso is None:
            continue
        gk = (date_iso, date_str, time_str, game_no, frozenset({yes_name, other_name}))
        by_game.setdefault(gk, {"codes": {yes_code: yes_name, other_code: other_name}, "tickers": {}})
        by_game[gk]["tickers"][yes_code] = t
    index: dict = {}
    for (date_iso, date_str, time_str, game_no, _names), info in by_game.items():
        codes = info["codes"]
        cl = list(codes)
        a_code, b_code = cl[0], (cl[1] if len(cl) > 1 else cl[0])
        game = KalshiGame(date_iso, date_str, time_str, game_no, a_code, b_code,
                          codes[a_code], codes[b_code], dict(info["tickers"]))
        index.setdefault(_game_key(date_iso, game.team_a_name, game.team_b_name), []).append(game)
    return index


def _side_ticker(game: KalshiGame, parsed: ParsedBet):
    if parsed.side_name is None:
        return None
    for code, name in ((game.team_a_code, game.team_a_name), (game.team_b_code, game.team_b_name)):
        if name == parsed.side_name:
            return game.ticker_by_side_code.get(code)
    return None


def match_bet(parsed: ParsedBet, game_index: dict, kalshi_dates, cfg: StructuralLeague,
              allowed_market_types=COPYABLE_MARKET_TYPES) -> MatchResult:
    """Moneyline-only structural match (mirrors mlb.match_poly_to_kalshi's moneyline path + the market-type
    gate). A non-moneyline market is a labelled SKIP; a doubleheader is surfaced ambiguous (NEVER guessed);
    an unresolved side returns matched-but-side_unresolved with candidates (the executor gates it)."""
    mt = parsed.market_type
    if mt != "moneyline":
        if mt == "non_moneyline":
            return MatchResult("skip_non_moneyline", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
        return MatchResult("skip_non_game", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
    if "moneyline" not in allowed_market_types:
        return MatchResult("skip_market_type_excluded", 0.0,
                           reason="moneyline_not_in_subdivision_market_types", market_type=mt)
    if parsed.away_name is None or parsed.home_name is None:
        return MatchResult("fail", 0.0, reason=parsed.fail_reason or "unrecognized_team", market_type=mt)
    if parsed.date_iso is None:
        return MatchResult("fail", 0.0, reason="no_date", market_type=mt)
    games = game_index.get(_game_key(parsed.date_iso, parsed.away_name, parsed.home_name), [])
    if not games:
        if parsed.date_iso not in kalshi_dates:
            return MatchResult("out_of_window", 0.0, reason="game_date_outside_kalshi_fetch_window", market_type=mt)
        return MatchResult("no_kalshi_contract", 0.0, reason="no_game_for_teams_on_date", market_type=mt)
    if len(games) > 1:
        cands = tuple(sorted(t for g in games for t in g.ticker_by_side_code.values()))
        return MatchResult("doubleheader_ambiguous", 0.50, kalshi_candidates=cands,
                           reason="%d_games_same_teams_same_date" % len(games), market_type=mt)
    game = games[0]
    ticker = _side_ticker(game, parsed)
    if ticker is None:
        return MatchResult("matched", 0.80, kalshi_candidates=tuple(sorted(game.ticker_by_side_code.values())),
                           reason="side_unresolved", market_type=mt, leg=None)
    conf = 1.0 if _norm(parsed.raw.get("outcome", "")) in (_norm(parsed.away_name), _norm(parsed.home_name)) else 0.97
    return MatchResult("matched", conf, kalshi_ticker=ticker, reason="unique_game_side_resolved",
                       leg="yes", market_type=mt)


# ── the league registry (team maps: mlb/nba/nhl/nfl exist; wnba/cfb land in later sub-rungs) ──────
LEAGUES: dict = {
    "mlb":  StructuralLeague("mlb", "mlb", "KXMLBGAME", MLB_TEAMS, has_doubleheader=True),   # oracle for the equivalence test
    "nfl":  StructuralLeague("nfl", "nfl", "KXNFLGAME", NFL_TEAMS),
    "nba":  StructuralLeague("nba", "nba", "KXNBAGAME", NBA_TEAMS),
    "nhl":  StructuralLeague("nhl", "nhl", "KXNHLGAME", NHL_TEAMS),
    "wnba": StructuralLeague("wnba", "wnba", "KXWNBAGAME", WNBA_TEAMS),
    # "cfb":  StructuralLeague("cfb", "cfb", "KXNCAAFGAME", CFB_TEAMS),     # sub-rung C (the hard map)
}
