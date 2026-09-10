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

# reuse mlb's league-agnostic helpers verbatim (accent/case fold, two-known-name side resolve, date conv,
# strike-ladder decode, and the Poly total/spread SUFFIX parsers) -- so the port is byte-identical by import,
# not by re-implementation. `_strike_from_n` is the N-0.5 half-integer ladder (uniform across every category,
# live-proven 2026-09-10: KXNFLTOTAL/KXWNBATOTAL/KXNCAAFTOTAL/soccer/MLB all decode floor_strike = N-0.5).
from .mlb_poly_kalshi_match import (  # noqa: F401
    _norm, resolve_side, kalshi_to_iso_date, iso_to_kalshi_date,
    _strike_from_n, _poly_line, _POLY_TOTAL_RE, _POLY_SPREAD_RE,
)
from .sports_team_mapping import MLB_TEAMS, NBA_TEAMS, NHL_TEAMS, NFL_TEAMS, WNBA_TEAMS
from .cfb_teams import CFB_TEAMS   # US college football: 269 real two-venue codes -> 151 schools (built, not hand-typed)

# Rung "spread/total" (2026-09-10): totals + spreads generalized from mlb's own 3-type matcher, series-
# parameterized. moneyline path stays BYTE-IDENTICAL (test_mlb_equivalence); totals/spreads are EXACT-STRIKE-ONLY
# and reproduce mlb's total/spread path field-for-field (test_mlb_equivalence_total_spread).
COPYABLE_MARKET_TYPES = ("moneyline", "total", "spread")


@dataclass(frozen=True)
class StructuralLeague:
    """One league's config: the Poly slug prefix, the Kalshi per-game series, the code->full-name map.

    `total_series`/`spread_series` are the Kalshi full-game over/under + handicap series (e.g. KXNFLTOTAL /
    KXNFLSPREAD). None -> that market type is not built for this league (any total/spread slug is a SAFE skip:
    empty index -> no_kalshi_strike). Setting them does NOT by itself copy anything -- the sub-division's
    `market_types` column is the enable gate (moneyline-only sub -> total/spread -> skip_market_type_excluded)."""
    category: str
    poly_prefix: str            # lowercase Poly slug prefix, e.g. "nfl"
    game_series: str            # Kalshi moneyline series, e.g. "KXNFLGAME"
    team_map: dict              # UPPER team code -> canonical full name (BOTH venues map into this)
    has_doubleheader: bool = False   # only mlb (kept inert for the others so mlb-config == mlb)
    total_series: str | None = None   # Kalshi full-game total series, e.g. "KXNFLTOTAL" (None = not built)
    spread_series: str | None = None  # Kalshi full-game spread series, e.g. "KXNFLSPREAD" (None = not built)


@dataclass(frozen=True)
class ParsedBet:
    market_type: str            # moneyline | total | spread | non_moneyline | non_sport | unparseable
    date_iso: str | None
    away_code: str | None
    home_code: str | None
    away_name: str | None
    home_name: str | None
    side: str | None            # away | home (the team the whale bet); spread: the outcome team
    side_name: str | None
    fail_reason: str | None = None
    raw: dict = field(default_factory=dict)
    # total/spread only (mirrors mlb ParsedPolyBet): the strike + the Kalshi LEG to BUY + the spread anchor.
    line: float | None = None        # total line (e.g. 44.5) or spread handicap (e.g. 6.5); None for moneyline/prop
    leg: str | None = None           # 'yes' | 'no' -- Kalshi leg (total: Over->yes/Under->no; spread: outcome==anchor->yes)
    anchor_side: str | None = None   # spread only: 'home'|'away' -- the -line ANCHOR team (== the Kalshi spread ticker's team)


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
    stem: str = ""              # {YYMONDD}{HHMM}{TEAMBLOB}[G{n}] -- shared verbatim with KX{X}TOTAL/KX{X}SPREAD (join key)


@dataclass(frozen=True)
class MatchResult:
    status: str                 # matched | doubleheader_ambiguous | date_window_ambiguous | no_kalshi_contract |
                                # out_of_window | no_kalshi_strike | skip_non_moneyline | skip_non_game | skip_market_type_excluded | fail
    confidence: float
    kalshi_ticker: str | None = None
    kalshi_candidates: tuple = ()
    reason: str | None = None
    leg: str | None = None
    market_type: str | None = None
    strike: float | None = None      # total/spread only: the matched line (echoed so the executor never re-derives it)


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
    date_iso = m.group("date")
    away_code = m.group("away").upper()
    home_code = m.group("home").upper()
    away_name = cfg.team_map.get(away_code)
    home_name = cfg.team_map.get(home_code)
    suffix = m.group("suffix")
    if suffix:
        # `-total-{W}pt{F}` and `-spread-{home|away}-{W}pt{F}` are COPYABLE (exact-strike). EVERY other suffix
        # (props: -nrfi, -1h-*, -team-total-*, -corners-*, -first-half-*, -set-*, -map-* ...) is a labelled
        # non_moneyline SKIP -- NEVER matched. The prefix test is anchored (`startswith`) so a prop that merely
        # CONTAINS 'total'/'spread' (e.g. '-team-total-', '-corners-total-', '-1h-spread-') falls through to skip.
        if suffix.startswith("-total"):
            tm = _POLY_TOTAL_RE.match(suffix)
            if tm is None:                                          # e.g. '-total-foo' -> not the canonical line -> skip
                return ParsedBet("non_moneyline", date_iso, away_code, home_code, None, None, None, None,
                                 fail_reason="unparseable_total_suffix:%r" % suffix, raw=raw)
            line = _poly_line(tm.group("w"), tm.group("f"))
            o = (outcome or "").strip().lower()
            leg = "yes" if o == "over" else "no" if o == "under" else None   # Kalshi KX{X}TOTAL YES = Over (NO = Under)
            fr = None if leg else "total_outcome_not_over_under:%r" % outcome
            return ParsedBet("total", date_iso, away_code, home_code, away_name, home_name, None, None,
                             fail_reason=fr, raw=raw, line=line, leg=leg)
        if suffix.startswith("-spread"):
            sm = _POLY_SPREAD_RE.match(suffix)
            if sm is None:                                          # e.g. multi-segment spread prop -> skip
                return ParsedBet("non_moneyline", date_iso, away_code, home_code, None, None, None, None,
                                 fail_reason="unparseable_spread_suffix:%r" % suffix, raw=raw)
            if away_name is None or home_name is None:
                miss = [c for c, n in ((away_code, away_name), (home_code, home_name)) if n is None]
                return ParsedBet("spread", date_iso, away_code, home_code, away_name, home_name, None, None,
                                 fail_reason="unrecognized_team_code:%s" % miss, raw=raw)
            line = _poly_line(sm.group("w"), sm.group("f"))
            anchor_side = sm.group("anchor")                        # 'home'|'away' == the -line ANCHOR team
            out_side = resolve_side(outcome, away_name, home_name)  # which club the whale actually bet
            if out_side is None:                                    # names collide/unresolved -> SAFE miss, never a guess
                return ParsedBet("spread", date_iso, away_code, home_code, away_name, home_name, None, None,
                                 fail_reason="spread_outcome_unresolved:%r" % outcome, raw=raw,
                                 line=line, anchor_side=anchor_side)
            # outcome==anchor -> "anchor -line" -> KX{X}SPREAD {anchor} YES; else "other +line" -> {anchor} NO.
            leg = "yes" if out_side == anchor_side else "no"
            return ParsedBet("spread", date_iso, away_code, home_code, away_name, home_name,
                             out_side, (away_name if out_side == "away" else home_name),
                             raw=raw, line=line, leg=leg, anchor_side=anchor_side)
        # prop / unknown suffix -> labelled non_moneyline (NEVER silently moneyline or a match).
        return ParsedBet("non_moneyline", date_iso, away_code, home_code, None, None, None, None,
                         fail_reason="non_moneyline_suffix:%r" % suffix, raw=raw)
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
        by_game.setdefault(gk, {"codes": {yes_code: yes_name, other_code: other_name}, "tickers": {},
                               "stem": (t.split("-")[1] if t.count("-") >= 2 else "")})  # shared with TOTAL/SPREAD
        by_game[gk]["tickers"][yes_code] = t
    index: dict = {}
    for (date_iso, date_str, time_str, game_no, _names), info in by_game.items():
        codes = info["codes"]
        cl = list(codes)
        a_code, b_code = cl[0], (cl[1] if len(cl) > 1 else cl[0])
        game = KalshiGame(date_iso, date_str, time_str, game_no, a_code, b_code,
                          codes[a_code], codes[b_code], dict(info["tickers"]), stem=info.get("stem", ""))
        index.setdefault(_game_key(date_iso, game.team_a_name, game.team_b_name), []).append(game)
    return index


# ── Kalshi total/spread ticker parse + index (mlb's R2 shape, series-parameterized) ───────────────
# Kalshi total : {TOTAL_SERIES}-{stem}-{N}          YES = Over.   floor_strike = N - 0.5.
# Kalshi spread: {SPREAD_SERIES}-{stem}-{TEAM}{N}   YES = "{TEAM} wins by over (N-0.5)". per-team, per-strike.
# `stem` is SHARED verbatim with the game series -> resolve the game via the game index, JOIN total/spread by stem.
def _kalshi_total_re(series: str):
    return re.compile(r"^%s-(?P<stem>[A-Z0-9]+)-(?P<n>\d+)$" % re.escape(series))


def _kalshi_spread_re(series: str):
    return re.compile(r"^%s-(?P<stem>[A-Z0-9]+)-(?P<team>[A-Z]+)(?P<n>\d+)$" % re.escape(series))


def parse_kalshi_total_ticker(ticker: str, cfg: StructuralLeague):
    """(stem, strike) for a {cfg.total_series} ticker, else None. strike = N - 0.5 (mlb's ladder, imported)."""
    if not cfg.total_series:
        return None
    m = _kalshi_total_re(cfg.total_series).match(ticker or "")
    if not m:
        return None
    return m.group("stem"), _strike_from_n(m.group("n"))


def parse_kalshi_spread_ticker(ticker: str, cfg: StructuralLeague):
    """(stem, team_code, strike) for a {cfg.spread_series} ticker, else None. team_code = the YES team (wins by over)."""
    if not cfg.spread_series:
        return None
    m = _kalshi_spread_re(cfg.spread_series).match(ticker or "")
    if not m:
        return None
    return m.group("stem"), m.group("team"), _strike_from_n(m.group("n"))


def build_total_index(tickers, cfg: StructuralLeague) -> dict:
    """{stem: {strike: ticker}} for total tickers. Joined to a game via the game index's shared stem."""
    idx: dict = {}
    for t in tickers:
        p = parse_kalshi_total_ticker(t, cfg)
        if p is None:
            continue
        stem, strike = p
        idx.setdefault(stem, {})[strike] = t
    return idx


def build_spread_index(tickers, cfg: StructuralLeague) -> dict:
    """{stem: {(team_code, strike): ticker}} for spread tickers (per-team, per-strike)."""
    idx: dict = {}
    for t in tickers:
        p = parse_kalshi_spread_ticker(t, cfg)
        if p is None:
            continue
        stem, team, strike = p
        idx.setdefault(stem, {})[(team, strike)] = t
    return idx


def _prev_iso(date_iso):
    """`YYYY-MM-DD` minus one day, or None if unparseable. Used ONLY for the night-game date fallback below."""
    try:
        import datetime as _dt
        y, m, d = (int(x) for x in str(date_iso).split("-"))
        return (_dt.date(y, m, d) - _dt.timedelta(days=1)).isoformat()
    except Exception:
        return None


def _resolve_structural_game(game_index: dict, date_iso, a_name, b_name, allow_prev_day: bool):
    """Resolve the Kalshi game(s) for (date, {a,b}). EXACT (date, teams) FIRST -- unchanged. Then, for a
    one-game-per-window league (`allow_prev_day` True), a `(date-1, teams)` FALLBACK: Polymarket dates a game slug
    by the UTC kickoff date while Kalshi dates the ticker by the US-LOCAL date, so a NIGHT game (kickoff past
    midnight UTC) is listed on Kalshi one day earlier -> Kalshi_date = Poly_date - 1 (confirmed live 2026-09-10:
    NE@SEA Poly 09-10 / Kalshi 26SEP09; DAL@NYG Poly 09-14 / Kalshi 26SEP13; DEN@KC Poly 09-15 / Kalshi 26SEP14).
    The fallback is ASYMMETRIC (only -1; UTC is never BEHIND US-local, so a +1 case cannot occur) and fires ONLY
    when the exact date misses.

    ★ UNIQUENESS GUARD (the whole basis for widening): if the SAME team pair has games on BOTH `date` AND `date-1`,
    return them together with ambiguous=True so the caller REFUSES -- a widened window must never PICK the wrong
    game, only recover an unambiguous one. This requires the pair to play on two consecutive days, which does not
    happen in nfl/nba/nhl/wnba/cfb (one meeting per several days -- proven per-category by the dry-run). MLB
    (doubleheaders) passes allow_prev_day=False -> EXACT ONLY -> unchanged + never widened (a +-1 day on top of
    MLB's unsolved same-day DH ambiguity would be the wrong direction). Returns (games, resolved_date, ambiguous)."""
    g0 = game_index.get(_game_key(date_iso, a_name, b_name), [])
    if not allow_prev_day:
        return g0, date_iso, False                        # doubleheader league (mlb-config): EXACT ONLY, unchanged
    d1 = _prev_iso(date_iso)
    g1 = game_index.get(_game_key(d1, a_name, b_name), []) if d1 else []
    if g0 and g1:
        return (list(g0) + list(g1)), None, True          # both days -> AMBIGUOUS -> caller refuses (never picks)
    if g0:
        return g0, date_iso, False                         # exact hit -> byte-identical to pre-fix behaviour
    if g1:
        return g1, d1, False                               # night-game recovery (Kalshi lists it one day earlier)
    return [], None, False


def _resolve_unique_game(parsed: ParsedBet, game_index: dict, kalshi_dates, cfg: StructuralLeague):
    """Resolve a total/spread bet's GAME via the SHARED _resolve_structural_game (exact + -1-day night-game
    recovery + uniqueness guard) -- SAME resolver as the moneyline path, so total/spread INHERIT the date fix.
    Returns (KalshiGame, None) on a unique game, else (None, MatchResult) mirroring the moneyline miss states."""
    if parsed.away_name is None or parsed.home_name is None:
        return None, MatchResult("fail", 0.0, reason=parsed.fail_reason or "unrecognized_team",
                                 market_type=parsed.market_type)
    if parsed.date_iso is None:
        return None, MatchResult("fail", 0.0, reason="no_date", market_type=parsed.market_type)
    allow_prev = not cfg.has_doubleheader
    games, _rdate, ambiguous = _resolve_structural_game(game_index, parsed.date_iso, parsed.away_name, parsed.home_name, allow_prev)
    if ambiguous:
        return None, MatchResult("date_window_ambiguous", 0.50,
                                 reason="teams_have_games_on_both_date_and_prior_day", market_type=parsed.market_type)
    if not games:
        _d1 = _prev_iso(parsed.date_iso)
        if parsed.date_iso not in kalshi_dates and (not allow_prev or _d1 not in kalshi_dates):
            return None, MatchResult("out_of_window", 0.0, reason="game_date_outside_kalshi_fetch_window",
                                     market_type=parsed.market_type)
        return None, MatchResult("no_kalshi_contract", 0.0,
                                 reason="no_game_for_teams_on_date_or_prior_day" if allow_prev else "no_game_for_teams_on_date",
                                 market_type=parsed.market_type)
    if len(games) > 1:
        return None, MatchResult("doubleheader_ambiguous", 0.50,
                                 reason="%d_games_same_teams_same_date" % len(games),
                                 market_type=parsed.market_type)
    return games[0], None


def _match_total(parsed: ParsedBet, game_index: dict, total_index: dict, kalshi_dates, cfg: StructuralLeague) -> MatchResult:
    # LEG: Over -> 'yes', Under -> 'no' (set in parse). notional/price downstream read this leg.
    if parsed.line is None or parsed.leg is None:
        return MatchResult("fail", 0.0, reason=parsed.fail_reason or "total_line_or_leg_missing", market_type="total")
    game, miss = _resolve_unique_game(parsed, game_index, kalshi_dates, cfg)   # shared resolver -> inherits -1 recovery
    if miss is not None:
        return miss
    ticker = total_index.get(game.stem, {}).get(parsed.line)
    if ticker is None:
        # EXACT STRIKE ONLY -- a neighbour line is a DIFFERENT bet, never a rounded match. Labelled miss.
        return MatchResult("no_kalshi_strike", 0.0, reason="no_total_strike_%s" % parsed.line,
                           strike=parsed.line, market_type="total")
    return MatchResult("matched", 1.0, kalshi_ticker=ticker, leg=parsed.leg, strike=parsed.line,
                       market_type="total", reason="exact_total_strike")


def _match_spread(parsed: ParsedBet, game_index: dict, spread_index: dict, kalshi_dates, cfg: StructuralLeague) -> MatchResult:
    # LEG: outcome==anchor -> 'yes' (anchor wins by over line), else 'no'. Anchor team from the SLUG, side from OUTCOME.
    if parsed.line is None or parsed.leg is None or parsed.anchor_side is None:
        return MatchResult("fail", 0.0, reason=parsed.fail_reason or "spread_line_leg_or_anchor_missing",
                           market_type="spread")
    game, miss = _resolve_unique_game(parsed, game_index, kalshi_dates, cfg)   # shared resolver -> inherits -1 recovery
    if miss is not None:
        return miss
    anchor_name = parsed.away_name if parsed.anchor_side == "away" else parsed.home_name
    anchor_code = None
    for code, name in ((game.team_a_code, game.team_a_name), (game.team_b_code, game.team_b_name)):
        if name == anchor_name:
            anchor_code = code
            break
    if anchor_code is None:
        return MatchResult("fail", 0.0, reason="anchor_team_not_in_kalshi_game:%r" % anchor_name, market_type="spread")
    ticker = spread_index.get(game.stem, {}).get((anchor_code, parsed.line))
    if ticker is None:
        return MatchResult("no_kalshi_strike", 0.0, reason="no_spread_strike_%s_%s" % (anchor_code, parsed.line),
                           strike=parsed.line, market_type="spread")
    return MatchResult("matched", 1.0, kalshi_ticker=ticker, leg=parsed.leg, strike=parsed.line,
                       market_type="spread", reason="exact_spread_strike")


def _side_ticker(game: KalshiGame, parsed: ParsedBet):
    if parsed.side_name is None:
        return None
    for code, name in ((game.team_a_code, game.team_a_name), (game.team_b_code, game.team_b_name)):
        if name == parsed.side_name:
            return game.ticker_by_side_code.get(code)
    return None


def match_bet(parsed: ParsedBet, game_index: dict, kalshi_dates, cfg: StructuralLeague,
              allowed_market_types=COPYABLE_MARKET_TYPES, *, total_index=None, spread_index=None) -> MatchResult:
    """Structural match across moneyline + total + spread. Moneyline path is BYTE-IDENTICAL to rung 1
    (test_mlb_equivalence); total/spread are EXACT-STRIKE-ONLY and reproduce mlb's total/spread path
    (test_mlb_equivalence_total_spread). A non-copyable type (prop/non-sport) or a copyable type NOT in the
    sub's `market_types` is a labelled SKIP, never a match. `total_index`/`spread_index` default to {} -> a
    league with no total/spread series (or an empty in-season fetch) yields no_kalshi_strike, a SAFE miss;
    the total/spread market type is gated on `allowed_market_types` (moneyline-only sub -> skip)."""
    mt = parsed.market_type
    if mt not in COPYABLE_MARKET_TYPES:
        if mt == "non_moneyline":
            return MatchResult("skip_non_moneyline", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
        return MatchResult("skip_non_game", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
    if mt not in allowed_market_types:
        return MatchResult("skip_market_type_excluded", 0.0,
                           reason="%s_not_in_subdivision_market_types" % mt, market_type=mt)
    if mt == "total":
        return _match_total(parsed, game_index, total_index or {}, kalshi_dates, cfg)
    if mt == "spread":
        return _match_spread(parsed, game_index, spread_index or {}, kalshi_dates, cfg)
    # ── moneyline: exact (date,teams) + the -1-day night-game recovery (shared _resolve_structural_game). This block
    # is IDENTICAL to the DEPLOYED date-join file (rung date-join, box 572b3f9f) -- the rebase preserves it verbatim. ──
    if parsed.away_name is None or parsed.home_name is None:
        return MatchResult("fail", 0.0, reason=parsed.fail_reason or "unrecognized_team", market_type=mt)
    if parsed.date_iso is None:
        return MatchResult("fail", 0.0, reason="no_date", market_type=mt)
    allow_prev = not cfg.has_doubleheader
    games, rdate, date_ambiguous = _resolve_structural_game(
        game_index, parsed.date_iso, parsed.away_name, parsed.home_name, allow_prev)
    if date_ambiguous:
        cands = tuple(sorted(t for g in games for t in g.ticker_by_side_code.values()))
        return MatchResult("date_window_ambiguous", 0.50, kalshi_candidates=cands,
                           reason="teams_have_games_on_both_date_and_prior_day", market_type=mt)
    if not games:
        _d1 = _prev_iso(parsed.date_iso)
        if parsed.date_iso not in kalshi_dates and (not allow_prev or _d1 not in kalshi_dates):
            return MatchResult("out_of_window", 0.0, reason="game_date_outside_kalshi_fetch_window", market_type=mt)
        return MatchResult("no_kalshi_contract", 0.0,
                           reason="no_game_for_teams_on_date_or_prior_day" if allow_prev else "no_game_for_teams_on_date",
                           market_type=mt)
    if len(games) > 1:
        cands = tuple(sorted(t for g in games for t in g.ticker_by_side_code.values()))
        return MatchResult("doubleheader_ambiguous", 0.50, kalshi_candidates=cands,
                           reason="%d_games_same_teams_same_date" % len(games), market_type=mt)
    game = games[0]
    _rec = "_via_prevday" if (rdate is not None and rdate != parsed.date_iso) else ""
    ticker = _side_ticker(game, parsed)
    if ticker is None:
        return MatchResult("matched", 0.80, kalshi_candidates=tuple(sorted(game.ticker_by_side_code.values())),
                           reason="side_unresolved" + _rec, market_type=mt, leg=None)
    conf = 1.0 if _norm(parsed.raw.get("outcome", "")) in (_norm(parsed.away_name), _norm(parsed.home_name)) else 0.97
    return MatchResult("matched", conf, kalshi_ticker=ticker, reason="unique_game_side_resolved" + _rec,
                       leg="yes", market_type=mt)


# ── the league registry (team maps: mlb/nba/nhl/nfl exist; wnba/cfb land in later sub-rungs) ──────
# total/spread series live-verified 2026-09-10 (full Kalshi Sports catalog + live /markets probe): nfl/wnba/cfb
# return open markets; nba/nhl series exist but are off-season (0 live markets now -> a SAFE empty index until in
# season, no code change needed then). mlb's total/spread series are set so the equivalence test covers all three
# types -- production mlb still uses its OWN module (mlb_poly_kalshi_match), untouched.
LEAGUES: dict = {
    "mlb":  StructuralLeague("mlb", "mlb", "KXMLBGAME", MLB_TEAMS, has_doubleheader=True,
                             total_series="KXMLBTOTAL", spread_series="KXMLBSPREAD"),   # oracle for the equivalence test
    "nfl":  StructuralLeague("nfl", "nfl", "KXNFLGAME", NFL_TEAMS,
                             total_series="KXNFLTOTAL", spread_series="KXNFLSPREAD"),
    "nba":  StructuralLeague("nba", "nba", "KXNBAGAME", NBA_TEAMS,
                             total_series="KXNBATOTAL", spread_series="KXNBASPREAD"),
    "nhl":  StructuralLeague("nhl", "nhl", "KXNHLGAME", NHL_TEAMS,
                             total_series="KXNHLTOTAL", spread_series="KXNHLSPREAD"),
    "wnba": StructuralLeague("wnba", "wnba", "KXWNBAGAME", WNBA_TEAMS,
                             total_series="KXWNBATOTAL", spread_series="KXWNBASPREAD"),
    "cfb":  StructuralLeague("cfb", "cfb", "KXNCAAFGAME", CFB_TEAMS,
                             total_series="KXNCAAFTOTAL", spread_series="KXNCAAFSPREAD"),
}
