"""Kalshi sports-ticker → the-odds-api game mapping.

Maps Kalshi 2-4 letter team codes to the-odds-api full team names for
the sports we initially cover. Used by the sports scout to bridge
Kalshi market data with bookmaker line data.

Scope (v1 scout): MLB, NBA, NHL, MLS, NFL. Soccer below MLS (Argentinian
/ Brazilian / Liga MX / Saudi PL) and tennis / esports skipped — these
either don't have liquid the-odds-api lines or have ambiguous Kalshi
codes (3-letter PLAYER codes for tennis don't map to teams).

Ticker shape we handle:
  KX{LEAGUE}GAME-{YYMMMDD}{HHMM?}{TEAM1}{TEAM2}-{YES_SIDE}

Examples:
  KXMLBGAME-26MAY112010SEAHOU-SEA  → MLB; teams SEA + HOU; YES = SEA
  KXNBAGAME-26MAY11DETCLE-CLE      → NBA; teams DET + CLE; YES = CLE
  KXMLSGAME-26MAY13STLLAFC-STL     → MLS; teams STL + LAFC; YES = STL
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# ── Kalshi league prefix → the-odds-api sport_key ────────────────────────
LEAGUE_TO_SPORT_KEY: dict[str, str] = {
    "MLB":  "baseball_mlb",
    "NBA":  "basketball_nba",
    "NHL":  "icehockey_nhl",
    "MLS":  "soccer_usa_mls",
    "NFL":  "americanfootball_nfl",
    "EPL":  "soccer_epl",
}


# Each league's Kalshi-code → odds-api-team-name map.
# Codes verified against Kalshi market samples + MLB.com/NBA.com/NHL.com
# standard abbreviations.

MLB_TEAMS: dict[str, str] = {
    "ARI": "Arizona Diamondbacks",   "ATL": "Atlanta Braves",
    "AZ":  "Arizona Diamondbacks",
    "BAL": "Baltimore Orioles",      "BOS": "Boston Red Sox",
    "CHC": "Chicago Cubs",           "CHW": "Chicago White Sox",
    "CWS": "Chicago White Sox",
    "CIN": "Cincinnati Reds",        "CLE": "Cleveland Guardians",
    "COL": "Colorado Rockies",       "DET": "Detroit Tigers",
    "HOU": "Houston Astros",         "KC":  "Kansas City Royals",
    "KCR": "Kansas City Royals",
    "LAA": "Los Angeles Angels",     "LAD": "Los Angeles Dodgers",
    "MIA": "Miami Marlins",          "MIL": "Milwaukee Brewers",
    "MIN": "Minnesota Twins",        "NYM": "New York Mets",
    "NYY": "New York Yankees",       "OAK": "Oakland Athletics",
    "ATH": "Oakland Athletics",      "PHI": "Philadelphia Phillies",
    "PIT": "Pittsburgh Pirates",     "SD":  "San Diego Padres",
    "SDP": "San Diego Padres",       "SEA": "Seattle Mariners",
    "SF":  "San Francisco Giants",   "SFG": "San Francisco Giants",
    "STL": "St. Louis Cardinals",    "TB":  "Tampa Bay Rays",
    "TBR": "Tampa Bay Rays",         "TEX": "Texas Rangers",
    "TOR": "Toronto Blue Jays",      "WSH": "Washington Nationals",
    "WAS": "Washington Nationals",   "WSN": "Washington Nationals",
}

NBA_TEAMS: dict[str, str] = {
    "ATL": "Atlanta Hawks",          "BOS": "Boston Celtics",
    "BKN": "Brooklyn Nets",          "CHA": "Charlotte Hornets",
    "CHI": "Chicago Bulls",          "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks",       "DEN": "Denver Nuggets",
    "DET": "Detroit Pistons",        "GSW": "Golden State Warriors",
    "HOU": "Houston Rockets",        "IND": "Indiana Pacers",
    "LAC": "LA Clippers",            "LAL": "Los Angeles Lakers",
    "MEM": "Memphis Grizzlies",      "MIA": "Miami Heat",
    "MIL": "Milwaukee Bucks",        "MIN": "Minnesota Timberwolves",
    "NOP": "New Orleans Pelicans",   "NYK": "New York Knicks",
    "OKC": "Oklahoma City Thunder",  "ORL": "Orlando Magic",
    "PHI": "Philadelphia 76ers",     "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings",
    "SAS": "San Antonio Spurs",      "TOR": "Toronto Raptors",
    "UTA": "Utah Jazz",              "WAS": "Washington Wizards",
}

NHL_TEAMS: dict[str, str] = {
    "ANA": "Anaheim Ducks",          "BOS": "Boston Bruins",
    "BUF": "Buffalo Sabres",         "CGY": "Calgary Flames",
    "CAR": "Carolina Hurricanes",    "CHI": "Chicago Blackhawks",
    "COL": "Colorado Avalanche",     "CBJ": "Columbus Blue Jackets",
    "DAL": "Dallas Stars",           "DET": "Detroit Red Wings",
    "EDM": "Edmonton Oilers",        "FLA": "Florida Panthers",
    "LAK": "Los Angeles Kings",      "MIN": "Minnesota Wild",
    "MTL": "Montreal Canadiens",     "NSH": "Nashville Predators",
    "NJD": "New Jersey Devils",      "NYI": "New York Islanders",
    "NYR": "New York Rangers",       "OTT": "Ottawa Senators",
    "PHI": "Philadelphia Flyers",    "PIT": "Pittsburgh Penguins",
    "SJS": "San Jose Sharks",        "SEA": "Seattle Kraken",
    "STL": "St Louis Blues",         "TBL": "Tampa Bay Lightning",
    "TOR": "Toronto Maple Leafs",    "UTA": "Utah Hockey Club",
    "VAN": "Vancouver Canucks",      "VGK": "Vegas Golden Knights",
    "WSH": "Washington Capitals",    "WPG": "Winnipeg Jets",
}

MLS_TEAMS: dict[str, str] = {
    "ATL": "Atlanta United FC",      "AUS": "Austin FC",
    "ATX": "Austin FC",              "CHA": "Charlotte FC",
    "CHI": "Chicago Fire FC",        "CIN": "FC Cincinnati",
    "COL": "Colorado Rapids",        "CLB": "Columbus Crew",
    "DAL": "FC Dallas",              "DC":  "D.C. United",
    "HOU": "Houston Dynamo FC",      "LAFC": "Los Angeles FC",
    "LAG": "LA Galaxy",              "MIA": "Inter Miami CF",
    "MIN": "Minnesota United FC",    "MTL": "CF Montréal",
    "NSH": "Nashville SC",           "NE":  "New England Revolution",
    "NYC": "New York City FC",       "NYRB": "New York Red Bulls",
    "NYR": "New York Red Bulls",     "ORL": "Orlando City SC",
    "PHI": "Philadelphia Union",     "POR": "Portland Timbers",
    "RSL": "Real Salt Lake",         "SD":  "San Diego FC",
    "SJ":  "San Jose Earthquakes",   "SEA": "Seattle Sounders FC",
    "STL": "St. Louis City SC",      "TOR": "Toronto FC",
    "VAN": "Vancouver Whitecaps FC",
}

NFL_TEAMS: dict[str, str] = {
    "ARI": "Arizona Cardinals",      "ATL": "Atlanta Falcons",
    "BAL": "Baltimore Ravens",       "BUF": "Buffalo Bills",
    "CAR": "Carolina Panthers",      "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals",     "CLE": "Cleveland Browns",
    "DAL": "Dallas Cowboys",         "DEN": "Denver Broncos",
    "DET": "Detroit Lions",          "GB":  "Green Bay Packers",
    "HOU": "Houston Texans",         "IND": "Indianapolis Colts",
    "JAX": "Jacksonville Jaguars",   "KC":  "Kansas City Chiefs",
    "LV":  "Las Vegas Raiders",      "LAC": "Los Angeles Chargers",
    "LAR": "Los Angeles Rams",       "LA":  "Los Angeles Rams",   # Poly uses bare `la` for the Rams (Chargers=`lac`); Kalshi uses LAR. Safe alias (dry-run 2026-09-06).
    "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings",      "NE":  "New England Patriots",
    "NO":  "New Orleans Saints",     "NYG": "New York Giants",
    "NYJ": "New York Jets",          "PHI": "Philadelphia Eagles",
    "PIT": "Pittsburgh Steelers",    "SF":  "San Francisco 49ers",
    "SEA": "Seattle Seahawks",       "TB":  "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans",       "WAS": "Washington Commanders",
    "WSH": "Washington Commanders",  "LAS": "Las Vegas Raiders",   # Poly spelling variants (Kalshi uses WAS/LV). Safe aliases (dry-run 2026-09-06).
}

# WNBA (added 2026-09-06, rung 1). Maps BOTH venues' codes to the canonical full name -- Polymarket and
# Kalshi disagree on a few (Poly `gsv`/`por` vs Kalshi `GS`/`PDX`), so both are listed (the MLB ARI/AZ
# precedent). 2026 league incl the Golden State Valkyries, Toronto Tempo, Portland Fire expansion sides.
# ★ Cross-venue code aliases are VERIFIED against live tickers in the sub-rung-F dry-run; an unlisted code
# is a SAFE MISS (never a wrong pick).
WNBA_TEAMS: dict[str, str] = {
    "ATL": "Atlanta Dream",          "CHI": "Chicago Sky",
    "CON": "Connecticut Sun",        "CONN": "Connecticut Sun",
    "DAL": "Dallas Wings",           "IND": "Indiana Fever",
    "GS":  "Golden State Valkyries", "GSV": "Golden State Valkyries",
    "LV":  "Las Vegas Aces",         "LVA": "Las Vegas Aces",
    "LA":  "Los Angeles Sparks",     "LAS": "Los Angeles Sparks",
    "MIN": "Minnesota Lynx",         "NY":  "New York Liberty",
    "NYL": "New York Liberty",       "PHX": "Phoenix Mercury",
    "PHO": "Phoenix Mercury",        "POR": "Portland Fire",
    "PDX": "Portland Fire",          "SEA": "Seattle Storm",
    "TOR": "Toronto Tempo",          "WAS": "Washington Mystics",
    "WSH": "Washington Mystics",
}

LEAGUE_TEAMS: dict[str, dict[str, str]] = {
    "MLB": MLB_TEAMS,
    "NBA": NBA_TEAMS,
    "NHL": NHL_TEAMS,
    "MLS": MLS_TEAMS,
    "NFL": NFL_TEAMS,
    "WNBA": WNBA_TEAMS,
}


# ── Ticker parser ────────────────────────────────────────────────────────

# Matches `KX{LEAGUE}GAME-{YYMMMDD}{optional HHMM}{TEAM_BLOB}-{YES_SIDE}`.
# YYMMMDD: 2 digits + 3 letters + 2 digits (e.g., 26MAY11)
# HHMM is optional (NBA tickets omit it; MLB/NHL include it).
_TICKER_RE = re.compile(
    r"^KX(?P<league>[A-Z]+)GAME-"
    r"(?P<date>\d{2}[A-Z]{3}\d{2})"
    r"(?P<time>\d{4})?"
    r"(?P<blob>[A-Z]+)-"
    r"(?P<yes>[A-Z]+)\d*$"   # NBASPREAD has trailing digits ("CLE8")
)


@dataclass(frozen=True)
class ParsedSportsTicker:
    league: str
    date_str: str
    time_str: str | None
    team_a: str            # the YES team's Kalshi code
    team_b: str            # the OTHER team's Kalshi code
    yes_side: str          # echo of team_a unless yes_side is "TIE" / other outcome
    team_a_name: str | None  # the-odds-api full name; None if unmapped
    team_b_name: str | None


def parse_sports_ticker(ticker: str) -> ParsedSportsTicker | None:
    """Parse a Kalshi sports ticker into (league, teams, yes_side).

    Returns None if the ticker doesn't match a known sports format, or
    if the league isn't in our v1 scope, or if the yes_side isn't one
    of the two teams (skip TIE/DRAW for v1).
    """
    if not ticker:
        return None
    m = _TICKER_RE.match(ticker)
    if not m:
        return None
    league = m.group("league")
    if league not in LEAGUE_TEAMS:
        return None
    blob = m.group("blob")
    yes_side = m.group("yes")
    # Use the YES side as anchor to split the blob into two teams.
    if yes_side in ("TIE", "DRAW"):
        return None  # v1 — skip draws
    if yes_side not in blob:
        return None
    # Remove yes_side from blob to get the OTHER team's code.
    # Edge case: yes_side substring appears twice in blob — pick the
    # rsplit ('SEAHOU' with yes='SEA' → split at the END so team_b='HOU').
    # Same for prefix: 'PHIBOS' with yes='PHI' → blob.startswith('PHI')
    # → team_b = blob[3:]. Use both endpoint tests:
    if blob.startswith(yes_side):
        team_b_code = blob[len(yes_side):]
    elif blob.endswith(yes_side):
        team_b_code = blob[:-len(yes_side)]
    else:
        # YES side somewhere in the middle — ambiguous, skip
        return None
    if not team_b_code:
        return None
    team_a_code = yes_side
    teams = LEAGUE_TEAMS[league]
    return ParsedSportsTicker(
        league=league,
        date_str=m.group("date"),
        time_str=m.group("time"),
        team_a=team_a_code,
        team_b=team_b_code,
        yes_side=yes_side,
        team_a_name=teams.get(team_a_code),
        team_b_name=teams.get(team_b_code),
    )


def find_matching_game(
    parsed: ParsedSportsTicker, games: list,
) -> object | None:
    """Find the `GameOdds` whose teams match `parsed` (case-insensitive).

    Returns the matching GameOdds, or None if no match. Both team-name
    pairings are tried (parsed.team_a could be home OR away).
    """
    if parsed.team_a_name is None or parsed.team_b_name is None:
        return None
    a = parsed.team_a_name.lower()
    b = parsed.team_b_name.lower()
    for g in games:
        gh = (g.home_team or "").lower()
        ga = (g.away_team or "").lower()
        if (gh == a and ga == b) or (gh == b and ga == a):
            return g
    return None
