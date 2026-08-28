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
from dataclasses import dataclass, field, replace

from trading_corp.data.sports_team_mapping import MLB_TEAMS

# ── Poly slug parsing ──────────────────────────────────────────────────────
# mlb-{away}-{home}-{YYYY-MM-DD}{optional suffix}. Team codes are lowercase
# alnum (2-4 chars). The suffix (if any) marks a non-moneyline market.
_POLY_SLUG_RE = re.compile(
    r"^mlb-(?P<away>[a-z0-9]+)-(?P<home>[a-z0-9]+)-"
    r"(?P<date>\d{4}-\d{2}-\d{2})(?P<suffix>.*)$"
)
# Stage 3 R2 -- non-moneyline suffix parsers (established from live PM-DB slugs 2026-08-28):
#   total  suffix: -total-{W}pt{F}               -> line W.F (e.g. -total-8pt5 -> 8.5)
#   spread suffix: -spread-{home|away}-{W}pt{F}  -> anchor side + run-line W.F (all 161 observed = 1pt5)
_POLY_TOTAL_RE  = re.compile(r"^-total-(?P<w>\d+)pt(?P<f>\d+)$")
_POLY_SPREAD_RE = re.compile(r"^-spread-(?P<anchor>home|away)-(?P<w>\d+)pt(?P<f>\d+)$")


def _poly_line(w: str, f: str) -> float:
    return float("%s.%s" % (w, f))

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
    side: str | None          # away | home (the club the whale bet); ML + spread (the outcome team)
    side_name: str | None
    fail_reason: str | None = None   # set when market_type in {non_mlb, unparseable} or team unresolved
    raw: dict = field(default_factory=dict)
    # Stage 3 R2 (total/spread): the strike + the Kalshi leg to BUY, established from live data.
    line: float | None = None        # total line (e.g. 8.5) or spread run-line (1.5); None for moneyline/prop
    leg: str | None = None           # 'yes' | 'no' -- the Kalshi leg (total: Over->yes; spread: outcome==anchor->yes)
    anchor_side: str | None = None   # spread only: 'home'|'away' -- the -line ANCHOR team (the Kalshi market's team)


def parse_poly_mlb_bet(slug: str, outcome: str, title: str = "", event_slug: str = "") -> ParsedPolyBet:
    """Parse one Poly activity row into a ParsedPolyBet.

    market_type is the authoritative scope gate: only 'moneyline' is in Phase-1
    scope. Everything else is a labeled skip (non-failure), except unresolved
    team codes on an otherwise-moneyline slug -> fail_reason set."""
    raw = {"slug": slug, "outcome": outcome, "title": title, "event_slug": event_slug}
    m = _POLY_SLUG_RE.match(slug or "")
    if not m:
        # mlb- prefix but not a single-game slug => MLB futures/series/awards
        # (World Series, division, MVP, season wins). Not a non-failure other-sport.
        mt = "mlb_non_game" if (slug or "").startswith("mlb-") else "non_mlb"
        return ParsedPolyBet(mt, None, None, None, None, None, None, None,
                             fail_reason=f"slug_no_game_match:{slug!r}", raw=raw)

    suffix = m.group("suffix")
    date_iso = m.group("date")
    away_code = m.group("away").upper()
    home_code = m.group("home").upper()
    away_name = MLB_TEAMS.get(away_code)
    home_name = MLB_TEAMS.get(home_code)

    if suffix:
        # non-moneyline market. Label by suffix family; extract line + Kalshi leg where the suffix parses.
        if suffix.startswith("-total"):
            tm = _POLY_TOTAL_RE.match(suffix)
            if tm is None:
                return ParsedPolyBet("total", date_iso, away_code, home_code, away_name, home_name,
                                     None, None, fail_reason=f"unparseable_total_suffix:{suffix!r}", raw=raw)
            line = _poly_line(tm.group("w"), tm.group("f"))
            o = (outcome or "").strip().lower()
            leg = "yes" if o == "over" else "no" if o == "under" else None   # Kalshi KXMLBTOTAL YES = Over
            fr = None if leg else f"total_outcome_not_over_under:{outcome!r}"
            return ParsedPolyBet("total", date_iso, away_code, home_code, away_name, home_name,
                                 None, None, fail_reason=fr, raw=raw, line=line, leg=leg)
        if suffix.startswith("-spread"):
            sm = _POLY_SPREAD_RE.match(suffix)
            if sm is None:
                return ParsedPolyBet("spread", date_iso, away_code, home_code, away_name, home_name,
                                     None, None, fail_reason=f"unparseable_spread_suffix:{suffix!r}", raw=raw)
            if away_name is None or home_name is None:
                miss = [c for c, n in ((away_code, away_name), (home_code, home_name)) if n is None]
                return ParsedPolyBet("spread", date_iso, away_code, home_code, away_name, home_name,
                                     None, None, fail_reason=f"unrecognized_team_code:{miss}", raw=raw)
            line = _poly_line(sm.group("w"), sm.group("f"))
            anchor_side = sm.group("anchor")                       # 'home'|'away' == the -line ANCHOR team
            out_side = resolve_side(outcome, away_name, home_name)  # which club the whale actually bet
            if out_side is None:
                return ParsedPolyBet("spread", date_iso, away_code, home_code, away_name, home_name,
                                     None, None, fail_reason=f"spread_outcome_unresolved:{outcome!r}",
                                     raw=raw, line=line, anchor_side=anchor_side)
            # outcome==anchor -> "anchor -line" -> KXMLBSPREAD {anchor} YES; else "other +line" -> {anchor} NO.
            leg = "yes" if out_side == anchor_side else "no"
            return ParsedPolyBet("spread", date_iso, away_code, home_code, away_name, home_name,
                                 out_side, (away_name if out_side == "away" else home_name),
                                 raw=raw, line=line, leg=leg, anchor_side=anchor_side)
        # prop / unknown suffix -> labelled non-moneyline (NEVER silently moneyline).
        return ParsedPolyBet("prop", date_iso, away_code, home_code, away_name, home_name,
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


# ── Kalshi KXMLBGAME ticker parser (DH-aware) ──────────────────────────────
# KXMLBGAME-{YYMMMDD}{HHMM}{TEAM_BLOB}[G{n}]-{YES}. The DH suffix `G1`/`G2` on
# the team blob is the real doubleheader discriminator (verified live 2026-08-15:
# STLCING1/STLCING2, TBBOSG1/TBBOSG2, MILSTLG1/MILSTLG2), alongside distinct HHMM.
# We DON'T reuse sports_team_mapping.parse_sports_ticker here because its `[A-Z]+`
# blob silently drops these DH tickers (they'd read as no_contract). That module
# stays byte-unchanged; this sibling parser adds DH awareness for MLB only.
_KALSHI_MLB_RE = re.compile(
    r"^KXMLBGAME-(?P<date>\d{2}[A-Z]{3}\d{2})(?P<time>\d{4})?"
    r"(?P<mid>[A-Z0-9]+)-(?P<yes>[A-Z]+)\d*$"
)


@dataclass(frozen=True)
class ParsedKalshiTicker:
    date_str: str
    time_str: str | None
    yes_code: str            # YES-side team code
    other_code: str          # the other team's code
    yes_name: str
    other_name: str
    game_no: int | None      # 1/2 for doubleheaders, else None


def parse_kalshi_mlb_ticker(ticker: str) -> ParsedKalshiTicker | None:
    """Parse a KXMLBGAME ticker (DH-aware). None if it isn't a two-team MLB game
    (e.g. the AL-vs-NL all-star ticker, whose 'teams' aren't clubs)."""
    m = _KALSHI_MLB_RE.match(ticker or "")
    if not m:
        return None
    mid, yes = m.group("mid"), m.group("yes")
    if yes in ("TIE", "DRAW"):
        return None
    game_no = None
    dm = re.search(r"G(\d)$", mid)      # trailing G<digit> == doubleheader game number
    if dm:
        game_no = int(dm.group(1))
        mid = mid[:dm.start()]
    # yes-anchored split of the remaining blob into the two team codes.
    if mid.startswith(yes):
        other = mid[len(yes):]
    elif mid.endswith(yes):
        other = mid[:-len(yes)]
    else:
        return None
    if not other:
        return None
    yes_name, other_name = MLB_TEAMS.get(yes), MLB_TEAMS.get(other)
    if yes_name is None or other_name is None:
        return None
    return ParsedKalshiTicker(m.group("date"), m.group("time"), yes, other,
                             yes_name, other_name, game_no)


def game_key_and_side(ticker: str):
    """(game_key, side_code, date_str) for a KXMLBGAME ticker, or None if it isn't a
    two-team MLB game ticker.

    The `game_key` is IDENTICAL for BOTH side tickers of one game — it keys on
    (date_iso, HHMM, doubleheader-number, the UNORDERED team-name pair), so
    `...BALTB-BAL` and `...BALTB-TB` collapse to the same key while the two games of
    a doubleheader (distinct G-number / HHMM) stay distinct. `side_code` is the YES
    team code, which distinguishes the two sides of the SAME game. `date_str` is the
    ticker's YYMMMDD prefix, handy for a cheap same-date SQL prefix scan.

    Pure; reuses `parse_kalshi_mlb_ticker`. This is what the executor's first-side-wins
    conflict gate uses to tell 'opposite side of a game I already took' from
    'same-side stacking'."""
    p = parse_kalshi_mlb_ticker(ticker)
    if p is None:
        return None
    date_iso = kalshi_to_iso_date(p.date_str) or p.date_str
    game_key = (date_iso, p.time_str, p.game_no, frozenset({p.yes_name, p.other_name}))
    return game_key, p.yes_code, p.date_str


# ── Kalshi game index ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class KalshiGame:
    date_iso: str
    date_str: str            # YYMMMDD
    time_str: str | None     # HHMM (doubleheader discriminator)
    game_no: int | None      # doubleheader game number (1/2), else None
    team_a_code: str
    team_b_code: str
    team_a_name: str
    team_b_name: str
    ticker_by_side_code: dict  # {team_code: full KXMLBGAME ticker for that YES side}
    stem: str = ""             # {YYMONDD}{HHMM}{TEAMBLOB}[G{n}] -- shared verbatim with KXMLBTOTAL/KXMLBSPREAD (R2 join key)


def _game_key(date_iso: str, name1: str, name2: str):
    return (date_iso, frozenset({name1, name2}))


def build_kalshi_game_index(tickers) -> dict:
    """Group KXMLBGAME tickers into games keyed by (date_iso, frozenset{names}).

    A key mapping to >1 KalshiGame == a doubleheader (same clubs+date; distinct
    G-number + HHMM). Each KalshiGame collects both side tickers (`-{YES}`) so the
    matcher can pick the side the whale bet. Non-club tickers are skipped."""
    # collect per (date, time, game_no, teams) -> {side_code: ticker}
    by_game: dict = {}
    for t in tickers:
        p = parse_kalshi_mlb_ticker(t)
        if p is None:
            continue
        date_iso = kalshi_to_iso_date(p.date_str)
        if date_iso is None:
            continue
        gk = (date_iso, p.date_str, p.time_str, p.game_no,
              frozenset({p.yes_name, p.other_name}))
        by_game.setdefault(gk, {"codes": {p.yes_code: p.yes_name, p.other_code: p.other_name},
                               "tickers": {},
                               "stem": (t.split("-")[1] if t.count("-") >= 2 else "")})
        by_game[gk]["tickers"][p.yes_code] = t

    index: dict = {}
    for (date_iso, date_str, time_str, game_no, names), info in by_game.items():
        codes = info["codes"]
        code_list = list(codes)
        a_code, b_code = code_list[0], (code_list[1] if len(code_list) > 1 else code_list[0])
        game = KalshiGame(
            date_iso=date_iso, date_str=date_str, time_str=time_str, game_no=game_no,
            team_a_code=a_code, team_b_code=b_code,
            team_a_name=codes[a_code], team_b_name=codes[b_code],
            ticker_by_side_code=dict(info["tickers"]),
            stem=info.get("stem", ""),
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
    # Stage 3 R2 -- carried so the executor never re-derives the leg (the $163.84-class risk):
    leg: str | None = None          # 'yes' | 'no' -- the Kalshi leg to BUY (moneyline via match_bet -> 'yes')
    strike: float | None = None     # total/spread line on a matched total/spread
    market_type: str | None = None  # moneyline | total | spread (echoed for logging/routing)


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


# ══════════════════════════════════════════════════════════════════════════════
# Stage 3 R2 (2026-08-28): THREE-DIMENSION extension -- (game, market_type, line).
# Moneyline above is UNCHANGED (the first-live path; the legacy poly_kalshi copy-trader consumes
# match_poly_to_kalshi directly -- its signature and behaviour are untouched). This section ADDS TOTALS
# (KXMLBTOTAL) and SPREADS (KXMLBSPREAD) with an EXACT-STRIKE-ONLY guard, all pure + fundless.
#
# Formats ESTABLISHED FROM LIVE DATA (2026-08-28 data-gather), NOT assumed:
#   Kalshi total : KXMLBTOTAL-{stem}-{N}          YES = Over.   floor_strike = N - 0.5.
#   Kalshi spread: KXMLBSPREAD-{stem}-{TEAM}{N}   YES = "{TEAM} wins by over (N-0.5)". per-team, per-strike.
#   `stem` = {YYMONDD}{HHMM}{TEAMBLOB}[G{n}] is SHARED verbatim across KXMLBGAME/KXMLBTOTAL/KXMLBSPREAD for one
#   game -- so we resolve the game via the moneyline index (date+teams) and JOIN totals/spreads by stem.
# ══════════════════════════════════════════════════════════════════════════════

COPYABLE_MARKET_TYPES = ("moneyline", "total", "spread")

_KALSHI_TOTAL_RE  = re.compile(r"^KXMLBTOTAL-(?P<stem>[A-Z0-9]+)-(?P<n>\d+)$")
_KALSHI_SPREAD_RE = re.compile(r"^KXMLBSPREAD-(?P<stem>[A-Z0-9]+)-(?P<team>[A-Z]+)(?P<n>\d+)$")


def _strike_from_n(n: str) -> float:
    """The trailing integer N in a KXMLBTOTAL/KXMLBSPREAD ticker encodes strike = N - 0.5 (verified live:
    KXMLBTOTAL '-9' -> floor_strike 8.5; KXMLBSPREAD '-TOR2' -> floor_strike 1.5). Half-run ladder only; a
    Poly half-integer line therefore matches an EXACT Kalshi strike or nothing (never a rounded neighbour)."""
    return int(n) - 0.5


def parse_kalshi_total_ticker(ticker: str):
    """(stem, strike) for a KXMLBTOTAL ticker, else None."""
    m = _KALSHI_TOTAL_RE.match(ticker or "")
    if not m:
        return None
    return m.group("stem"), _strike_from_n(m.group("n"))


def parse_kalshi_spread_ticker(ticker: str):
    """(stem, team_code, strike) for a KXMLBSPREAD ticker, else None. team_code = the YES team (wins by over)."""
    m = _KALSHI_SPREAD_RE.match(ticker or "")
    if not m:
        return None
    return m.group("stem"), m.group("team"), _strike_from_n(m.group("n"))


def build_kalshi_total_index(total_tickers) -> dict:
    """{stem: {strike: ticker}} for KXMLBTOTAL tickers. Joined to a game via the moneyline index's stem."""
    idx: dict = {}
    for t in total_tickers:
        p = parse_kalshi_total_ticker(t)
        if p is None:
            continue
        stem, strike = p
        idx.setdefault(stem, {})[strike] = t
    return idx


def build_kalshi_spread_index(spread_tickers) -> dict:
    """{stem: {(team_code, strike): ticker}} for KXMLBSPREAD tickers (per-team, per-strike)."""
    idx: dict = {}
    for t in spread_tickers:
        p = parse_kalshi_spread_ticker(t)
        if p is None:
            continue
        stem, team, strike = p
        idx.setdefault(stem, {})[(team, strike)] = t
    return idx


def _resolve_unique_game(parsed: ParsedPolyBet, moneyline_index: dict, kalshi_dates: frozenset):
    """Resolve a total/spread bet's GAME via the moneyline index (same (date, teams) key). Returns
    (KalshiGame, None) on a unique game, else (None, MatchResult) mirroring the moneyline miss states."""
    if parsed.away_name is None or parsed.home_name is None:
        return None, MatchResult("fail", 0.0, reason=parsed.fail_reason or "unrecognized_team",
                                 market_type=parsed.market_type)
    if parsed.date_iso is None:
        return None, MatchResult("fail", 0.0, reason="no_date", market_type=parsed.market_type)
    games = moneyline_index.get(_game_key(parsed.date_iso, parsed.away_name, parsed.home_name), [])
    if not games:
        if parsed.date_iso not in kalshi_dates:
            return None, MatchResult("out_of_window", 0.0, reason="game_date_outside_kalshi_fetch_window",
                                     market_type=parsed.market_type)
        return None, MatchResult("no_kalshi_contract", 0.0, reason="no_kxmlbgame_for_teams_on_date",
                                 market_type=parsed.market_type)
    if len(games) > 1:
        return None, MatchResult("doubleheader_ambiguous", 0.50,
                                 reason=f"{len(games)}_games_same_teams_same_date",
                                 market_type=parsed.market_type)
    return games[0], None


def _match_total(parsed, moneyline_index, total_index, kalshi_dates) -> MatchResult:
    if parsed.line is None or parsed.leg is None:
        return MatchResult("fail", 0.0, reason=parsed.fail_reason or "total_line_or_leg_missing",
                           market_type="total")
    game, miss = _resolve_unique_game(parsed, moneyline_index, kalshi_dates)
    if miss is not None:
        return miss
    ticker = total_index.get(game.stem, {}).get(parsed.line)
    if ticker is None:
        # EXACT STRIKE ONLY -- never round to a neighbour (the far-tail case). Labelled skip.
        return MatchResult("no_kalshi_strike", 0.0, reason=f"no_total_strike_{parsed.line}",
                           strike=parsed.line, market_type="total")
    return MatchResult("matched", 1.0, kalshi_ticker=ticker, leg=parsed.leg, strike=parsed.line,
                       market_type="total", reason="exact_total_strike")


def _match_spread(parsed, moneyline_index, spread_index, kalshi_dates) -> MatchResult:
    if parsed.line is None or parsed.leg is None or parsed.anchor_side is None:
        return MatchResult("fail", 0.0, reason=parsed.fail_reason or "spread_line_leg_or_anchor_missing",
                           market_type="spread")
    game, miss = _resolve_unique_game(parsed, moneyline_index, kalshi_dates)
    if miss is not None:
        return miss
    anchor_name = parsed.away_name if parsed.anchor_side == "away" else parsed.home_name
    anchor_code = None
    for code, name in ((game.team_a_code, game.team_a_name), (game.team_b_code, game.team_b_name)):
        if name == anchor_name:
            anchor_code = code
            break
    if anchor_code is None:
        return MatchResult("fail", 0.0, reason=f"anchor_team_not_in_kalshi_game:{anchor_name!r}",
                           market_type="spread")
    ticker = spread_index.get(game.stem, {}).get((anchor_code, parsed.line))
    if ticker is None:
        return MatchResult("no_kalshi_strike", 0.0, reason=f"no_spread_strike_{anchor_code}_{parsed.line}",
                           strike=parsed.line, market_type="spread")
    return MatchResult("matched", 1.0, kalshi_ticker=ticker, leg=parsed.leg, strike=parsed.line,
                       market_type="spread", reason="exact_spread_strike")


def match_bet(parsed: ParsedPolyBet, moneyline_index: dict, total_index: dict, spread_index: dict,
              kalshi_dates: frozenset,
              allowed_market_types=COPYABLE_MARKET_TYPES) -> MatchResult:
    """Unified 3-dimension match. `allowed_market_types` = the sub-division's `market_types` (R1) -- a copyable
    type NOT in it is a LABELLED SKIP (`skip_market_type_excluded`), never an error; a non-copyable type
    (prop / futures / non-mlb) is `skip_non_ml` / `skip_non_game`. Moneyline DELEGATES to the unchanged
    match_poly_to_kalshi (leg forced 'yes' -- a moneyline copy BUYS YES on the bet team's KXMLBGAME ticker).
    Totals/spreads are EXACT-STRIKE-ONLY and carry the Kalshi `leg` so the executor never re-derives it."""
    mt = parsed.market_type
    if mt not in COPYABLE_MARKET_TYPES:
        return MatchResult("skip_non_ml" if mt == "prop" else "skip_non_game", 0.0,
                           reason=parsed.fail_reason or mt, market_type=mt)
    if mt not in allowed_market_types:
        return MatchResult("skip_market_type_excluded", 0.0,
                           reason=f"{mt}_not_in_subdivision_market_types", market_type=mt)
    if mt == "moneyline":
        # UNCHANGED legacy path (poly_kalshi consumes match_poly_to_kalshi directly -- kept BYTE-IDENTICAL).
        # A clean moneyline match BUYS YES on the bet team's KXMLBGAME ticker; stamp leg/market_type HERE so the
        # legacy function's return stays untouched (leg only when a single ticker resolved).
        r = match_poly_to_kalshi(parsed, moneyline_index, kalshi_dates)
        leg = "yes" if (r.status == "matched" and r.kalshi_ticker is not None) else None
        return replace(r, leg=leg, market_type="moneyline")
    if mt == "total":
        return _match_total(parsed, moneyline_index, total_index, kalshi_dates)
    return _match_spread(parsed, moneyline_index, spread_index, kalshi_dates)


def liquidity_ok(market: dict, min_liquidity_usd: float = 20.0, max_spread_cents: int = 5) -> bool:
    """PURE liquidity-floor gate applied AT MATCH TIME (guard K4). The pure matcher has only tickers, so the
    executor passes the resolved Kalshi market dict here after match_bet returns a ticker. Reads
    `liquidity_dollars` + `yes_bid_dollars`/`yes_ask_dollars`; rejects a thin or one-sided book. Defaults are
    conservative placeholders -- the sub-division config drives the real floor.
    ★ MUST BE RE-CHECKED AT EXIT: a strike liquid at entry can be thin at exit (K4)."""
    if not isinstance(market, dict):
        return False
    try:
        liq = float(market.get("liquidity_dollars") or 0.0)
        bid = float(market.get("yes_bid_dollars") or 0.0)
        ask = float(market.get("yes_ask_dollars") or 0.0)
    except (TypeError, ValueError):
        return False
    if liq < float(min_liquidity_usd):
        return False
    if bid <= 0.0 or ask <= 0.0:
        return False                                  # not two-sided -> untradeable
    return 0 <= round((ask - bid) * 100) <= int(max_spread_cents)
