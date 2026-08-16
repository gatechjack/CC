"""Deterministic Polymarket-MLB -> Kalshi-KXMLBGAME matcher (Phase 1, CP1).

Pure functions only (no network) so the whole thing is unit-testable and the
seconds-critical live path is an O(1) dict lookup. The strategy (CP2+) and the
offline daily map-builder both consume this module.

Scope: MLB single-game **moneyline** only. Kalshi offers only KXMLBGAME (game
ML) for live MLB games — no KXMLBSPREAD / KXMLBTOTAL (verified 2026-05-23, see
kalshi_sports_arb_observer). Polymarket totals/spreads/props therefore have no
Kalshi equivalent and are labeled skips, NOT match failures.

Join strategy
-------------
Polymarket and Kalshi use DIFFERENT abbreviations for the same club (Poly slug
`ari`/`cws`/`sd`/`oak` vs Kalshi ticker `AZ`/`CWS`/`SD`/`ATH`). Both map to the
same full club name via `sports_team_mapping.MLB_TEAMS`, so we canonicalize BOTH
sides to the full name and key on (game_date_iso, frozenset{away_name, home_name}).

Poly conventions (empirical, SDTrading 2026-08 sample)
  moneyline : slug == `mlb-{away}-{home}-{YYYY-MM-DD}`  (== event_slug)   outcome = a team
  total     : slug + `-total-{N}pt{M}`                  outcome Over/Under
  spread    : slug + `-spread-{home|away}-1pt5`         outcome = a team (NOT ML!)
  prop      : slug + `-nrfi` etc.
  non-MLB   : slug not starting `mlb-`
The slug suffix — not `outcome` — is the authoritative market-type gate.

Kalshi convention (empirical, live KXMLBGAME)
  KXMLBGAME-{YYMMMDD}{HHMM}{TEAM_BLOB}-{YES_SIDE}  e.g. KXMLBGAME-26AUG161337NYYTOR-NYY
  Two tickers per game (one per side). Doubleheaders: same teams+date, two HHMM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from trading_corp.data.sports_team_mapping import MLB_TEAMS, parse_sports_ticker

# ── Poly slug parsing ──────────────────────────────────────────────────────
# mlb-{away}-{home}-{YYYY-MM-DD}{optional suffix}. Team codes are lowercase
# alnum (2-4 chars). The suffix (if any) marks a non-moneyline market.
_POLY_SLUG_RE = re.compile(
    r"^mlb-(?P<away>[a-z0-9]+)-(?P<home>[a-z0-9]+)-"
    r"(?P<date>\d{4}-\d{2}-\d{2})(?P<suffix>.*)$"
)

_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def iso_to_kalshi_date(iso: str) -> str | None:
    """'2026-08-16' -> '26AUG16'. None if not a valid ISO date string."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", iso or "")
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12):
        return None
    return f"{y % 100:02d}{_MONTHS[mo - 1]}{d:02d}"


def kalshi_to_iso_date(kd: str) -> str | None:
    """'26AUG16' -> '2026-08-16'. None if unparseable."""
    m = re.match(r"^(\d{2})([A-Z]{3})(\d{2})$", kd or "")
    if not m:
        return None
    try:
        mo = _MONTHS.index(m.group(2)) + 1
    except ValueError:
        return None
    return f"20{m.group(1)}-{mo:02d}-{int(m.group(3)):02d}"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def resolve_side(outcome: str, away_name: str, home_name: str) -> str | None:
    """Which of the two known clubs does `outcome` name? 'away'|'home'|None.

    Only ever disambiguates between TWO known full names, so exact / substring /
    shared-token matching is safe (no global nickname collisions)."""
    o = _norm(outcome)
    if not o:
        return None
    a, h = _norm(away_name), _norm(home_name)
    if o == a:
        return "away"
    if o == h:
        return "home"
    # substring (e.g. "athletics" in "oakland athletics")
    a_sub = o in a or a in o
    h_sub = o in h or h in o
    if a_sub and not h_sub:
        return "away"
    if h_sub and not a_sub:
        return "home"
    # shared last token (nickname) — decisive only if it favors exactly one
    ot = set(o.split())
    a_share = bool(ot & set(a.split()))
    h_share = bool(ot & set(h.split()))
    if a_share and not h_share:
        return "away"
    if h_share and not a_share:
        return "home"
    return None


# ── Parsed Poly bet ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ParsedPolyBet:
    market_type: str          # moneyline | total | spread | prop | non_mlb | unparseable
    date_iso: str | None      # game date, YYYY-MM-DD
    away_code: str | None     # Poly slug code, upper
    home_code: str | None
    away_name: str | None     # canonical full name (via MLB_TEAMS)
    home_name: str | None
    side: str | None          # away | home (the club the whale bet), ML only
    side_name: str | None
    fail_reason: str | None = None   # set when market_type in {non_mlb, unparseable} or team unresolved
    raw: dict = field(default_factory=dict)


def parse_poly_mlb_bet(slug: str, outcome: str, title: str = "", event_slug: str = "") -> ParsedPolyBet:
    """Parse one Poly activity row into a ParsedPolyBet.

    market_type is the authoritative scope gate: only 'moneyline' is in Phase-1
    scope. Everything else is a labeled skip (non-failure), except unresolved
    team codes on an otherwise-moneyline slug -> fail_reason set."""
    raw = {"slug": slug, "outcome": outcome, "title": title, "event_slug": event_slug}
    m = _POLY_SLUG_RE.match(slug or "")
    if not m:
        mt = "non_mlb" if not (slug or "").startswith("mlb-") else "unparseable"
        return ParsedPolyBet(mt, None, None, None, None, None, None, None,
                             fail_reason=f"slug_no_game_match:{slug!r}", raw=raw)

    suffix = m.group("suffix")
    date_iso = m.group("date")
    away_code = m.group("away").upper()
    home_code = m.group("home").upper()
    away_name = MLB_TEAMS.get(away_code)
    home_name = MLB_TEAMS.get(home_code)

    if suffix:
        # non-moneyline market. Label by suffix family.
        if suffix.startswith("-total"):
            mt = "total"
        elif suffix.startswith("-spread"):
            mt = "spread"
        else:
            mt = "prop"
        return ParsedPolyBet(mt, date_iso, away_code, home_code, away_name, home_name,
                             None, None, raw=raw)

    # moneyline
    if away_name is None or home_name is None:
        missing = [c for c, n in ((away_code, away_name), (home_code, home_name)) if n is None]
        return ParsedPolyBet("moneyline", date_iso, away_code, home_code, away_name, home_name,
                             None, None, fail_reason=f"unrecognized_team_code:{missing}", raw=raw)

    side = resolve_side(outcome, away_name, home_name)
    side_name = away_name if side == "away" else home_name if side == "home" else None
    return ParsedPolyBet("moneyline", date_iso, away_code, home_code, away_name, home_name,
                         side, side_name, raw=raw)


# ── Kalshi game index ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class KalshiGame:
    date_iso: str
    date_str: str            # YYMMMDD
    time_str: str | None     # HHMM (doubleheader discriminator)
    team_a_code: str         # YES-side team of ticker_a
    team_b_code: str
    team_a_name: str
    team_b_name: str
    ticker_by_side_code: dict  # {team_code: full KXMLBGAME ticker for that YES side}


def _game_key(date_iso: str, name1: str, name2: str):
    return (date_iso, frozenset({name1, name2}))


def build_kalshi_game_index(tickers) -> dict:
    """Group KXMLBGAME tickers into games keyed by (date_iso, frozenset{names}).

    A key mapping to >1 KalshiGame == a doubleheader (same clubs+date, two HHMM).
    Each KalshiGame collects both side tickers (`-{YES}`) so the matcher can pick
    the side the whale bet. Tickers that don't parse / unmapped teams are skipped."""
    # First collect per (date,time,teams) -> {side_code: ticker}
    by_game: dict = {}
    for t in tickers:
        p = parse_sports_ticker(t)
        if p is None or p.team_a_name is None or p.team_b_name is None:
            continue
        date_iso = kalshi_to_iso_date(p.date_str)
        if date_iso is None:
            continue
        gk = (date_iso, p.date_str, p.time_str, frozenset({p.team_a_name, p.team_b_name}))
        by_game.setdefault(gk, {"codes": {p.team_a: p.team_a_name, p.team_b: p.team_b_name},
                               "tickers": {}})
        # ticker's YES side = p.team_a (the code after the final '-')
        by_game[gk]["tickers"][p.team_a] = t

    index: dict = {}
    for (date_iso, date_str, time_str, names), info in by_game.items():
        codes = info["codes"]
        code_list = list(codes)
        # team_a/team_b assignment is arbitrary for a game object; keep both.
        a_code, b_code = code_list[0], (code_list[1] if len(code_list) > 1 else code_list[0])
        game = KalshiGame(
            date_iso=date_iso, date_str=date_str, time_str=time_str,
            team_a_code=a_code, team_b_code=b_code,
            team_a_name=codes[a_code], team_b_name=codes[b_code],
            ticker_by_side_code=dict(info["tickers"]),
        )
        index.setdefault(_game_key(date_iso, game.team_a_name, game.team_b_name), []).append(game)
    return index


# ── Matcher ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class MatchResult:
    status: str          # matched | doubleheader_ambiguous | no_kalshi_contract |
                         # out_of_window | skip_non_ml | skip_non_game | fail
    confidence: float    # 0..1 (match confidence; caller sets the auto-exec threshold)
    kalshi_ticker: str | None = None
    kalshi_candidates: tuple = ()   # >1 => doubleheader candidates (side tickers)
    reason: str | None = None


def _side_ticker(game: KalshiGame, parsed: ParsedPolyBet) -> str | None:
    """The `-{YES=side}` KXMLBGAME ticker for the club the whale bet."""
    if parsed.side_name is None:
        return None
    want = parsed.side_name
    for code, name in ((game.team_a_code, game.team_a_name), (game.team_b_code, game.team_b_name)):
        if name == want:
            return game.ticker_by_side_code.get(code)
    return None


def match_poly_to_kalshi(parsed: ParsedPolyBet, kalshi_index: dict,
                         kalshi_dates: frozenset) -> MatchResult:
    """Map a parsed Poly bet to a real Kalshi KXMLBGAME contract.

    `kalshi_dates` = set of ISO dates present in the Kalshi index; lets us tell
    a genuine "no contract" from "the game predates our fetched Kalshi window".
    Doubleheaders are surfaced as candidates (status doubleheader_ambiguous),
    NOT guessed — the deterministic rule is added only after the convention is
    confirmed on real data."""
    if parsed.market_type != "moneyline":
        if parsed.market_type in ("total", "spread", "prop"):
            return MatchResult("skip_non_ml", 0.0, reason=parsed.market_type)
        return MatchResult("skip_non_game", 0.0, reason=parsed.fail_reason or parsed.market_type)

    if parsed.away_name is None or parsed.home_name is None:
        return MatchResult("fail", 0.0, reason=parsed.fail_reason or "unrecognized_team")
    if parsed.date_iso is None:
        return MatchResult("fail", 0.0, reason="no_date")

    key = _game_key(parsed.date_iso, parsed.away_name, parsed.home_name)
    games = kalshi_index.get(key, [])

    if not games:
        if parsed.date_iso not in kalshi_dates:
            return MatchResult("out_of_window", 0.0, reason="game_date_outside_kalshi_fetch_window")
        return MatchResult("no_kalshi_contract", 0.0, reason="no_kxmlbgame_for_teams_on_date")

    if len(games) > 1:
        cands = tuple(sorted(t for g in games for t in g.ticker_by_side_code.values()))
        return MatchResult("doubleheader_ambiguous", 0.50, kalshi_candidates=cands,
                          reason=f"{len(games)}_games_same_teams_same_date")

    game = games[0]
    ticker = _side_ticker(game, parsed)
    if ticker is None:
        # teams+date matched a unique game but we couldn't resolve which side.
        return MatchResult("matched", 0.80,
                          kalshi_candidates=tuple(sorted(game.ticker_by_side_code.values())),
                          reason="side_unresolved")
    # clean unique match; side resolved. Full confidence, minus a hair if the
    # side was resolved by nickname/substring rather than exact full-name equality.
    conf = 1.0 if _norm(parsed.raw.get("outcome", "")) in (_norm(parsed.away_name), _norm(parsed.home_name)) else 0.97
    return MatchResult("matched", conf, kalshi_ticker=ticker, reason="unique_game_side_resolved")
