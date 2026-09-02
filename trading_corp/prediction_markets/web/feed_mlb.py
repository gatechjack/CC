"""MLB sports-feed adapter for pm_web (UI rewrite, Scope D). StatsAPI primary, ESPN fallback.

STANDALONE by construction (the pm_web import discipline): imports ONLY the stdlib + the data-layer team
map (`trading_corp.data.sports_team_mapping.MLB_TEAMS`) + the Kalshi ticker parser
(`trading_corp.data.mlb_poly_kalshi_match`). NO engine/main/agents/brokers import; NO credentials. It fetches
two PUBLIC, key-less feeds (statsapi.mlb.com, site.api.espn.com), verified reachable from the box 2026-09-01.

WHY THIS SHAPE (the honesty contract, treated as spec):
  * Every game we render must be the RIGHT game. The join is on (ET calendar date, doubleheader number, the
    UNORDERED canonical team-name pair) -- NEVER on a raw abbreviation (Kalshi/StatsAPI use AZ/CWS/ATH; ESPN
    uses ARI/CHW/OAK -- all resolve to the same MLB_TEAMS full name, so a set of full names is the safe key).
  * Kalshi tickers encode the START TIME AND CALENDAR DATE IN EASTERN TIME. StatsAPI/ESPN report UTC, which
    rolls the date over for night games (a 10:10pm ET game on Sep 2 is 02:10Z on Sep 3). So we convert the
    feed's UTC start to ET before keying -- otherwise a late game would join to the wrong date, or not at all.
  * Any fetch/parse failure, or a game the feed simply does not carry, degrades to ABSENT (the caller renders
    "feed unavailable"). We NEVER invent a game, and never show a stale value as current -- the caller bands
    each value by its own age. A `SlateResult` always states its source, its as_of, and whether it is ok.

Pure parse/join functions (no network) are separated from the thin urllib fetch layer so the join logic is
unit-testable against captured fixtures (the named failure modes: team-code mismatch, DST rollover,
doubleheaders, postponed/suspended, feed-down).
"""
from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from trading_corp.data.sports_team_mapping import MLB_TEAMS

log = logging.getLogger(__name__)

_STATSAPI = "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=%s&hydrate=linescore,team,game(seriesStatus)"
_STATSAPI_LASTPLAY = ("https://statsapi.mlb.com/api/v1.1/game/%s/feed/live"
                      "?fields=liveData,plays,currentPlay,result,description,about,halfInning,inning")
_ESPN = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard?dates=%s"
# ESPN 403s a browser (Chrome) User-Agent but serves a curl-style one (observed 2026-09-02). StatsAPI is
# indifferent. A plain non-browser UA is the tested-good choice; ESPN is best-effort fallback regardless.
_UA = "curl/8.4.0"


# ── team canonicalization ───────────────────────────────────────────────────────────────────────────────────
def canonical_team(code: str | None) -> str | None:
    """A source's team abbreviation -> the canonical MLB club name (via MLB_TEAMS), or None if unknown. MLB_TEAMS
    already carries every variant we see across sources (AZ/ARI, CWS/CHW, ATH/OAK, WSH/WAS/WSN, SD/SDP), so one
    lookup canonicalizes Kalshi, StatsAPI and ESPN codes alike. Unknown -> None so a mystery code degrades the
    game to unavailable rather than joining it to the wrong club."""
    if not code:
        return None
    return MLB_TEAMS.get(str(code).strip().upper())


# ── Eastern-time conversion (Kalshi's ticker convention) ─────────────────────────────────────────────────────
# zoneinfo('America/New_York') is authoritative for EDT/EST and every DST transition (fix-pass item 6, replacing
# an earlier hand-rolled rule). The Linux box has the system IANA tz database; Windows test hosts get it from the
# `tzdata` package.
_ET = ZoneInfo("America/New_York")


def utc_to_eastern(dt_utc: datetime) -> datetime:
    """UTC-aware (or naive-UTC) datetime -> naive Eastern wall-clock datetime (the timezone Kalshi tickers use)."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(_ET).replace(tzinfo=None)


def _parse_iso_utc(s: str | None) -> datetime | None:
    """StatsAPI/ESPN 'YYYY-MM-DDTHH:MM:SSZ' (or with offset) -> UTC-aware datetime, or None."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def eastern_key_parts(start_utc_iso: str | None):
    """(date_iso_ET, HHMM_ET) for a feed UTC start, or (None, None). This is the ET calendar date + start
    minute that the Kalshi ticker encodes -- the two time components of the join key."""
    dt = _parse_iso_utc(start_utc_iso)
    if dt is None:
        return None, None
    et = utc_to_eastern(dt)
    return et.strftime("%Y-%m-%d"), et.strftime("%H%M")


def feed_game_key(date_iso: str | None, away_name: str | None, home_name: str | None,
                  hhmm_et: str | None, game_no: int | None):
    """The canonical join key, IDENTICAL in shape to mlb_poly_kalshi_match.game_key_and_side()'s game_key:
    (date_iso, HHMM, doubleheader_no, frozenset{away_name, home_name}). None if either club is unknown."""
    if not (date_iso and away_name and home_name):
        return None
    return (date_iso, hhmm_et, game_no, frozenset({away_name, home_name}))


def match_in_slate(games: dict, date_iso: str, team_set: frozenset, hhmm: str | None, game_no: int | None):
    """Find the feed GameState for a Kalshi position, given the ticker's (date, team-set, HHMM, DH-number).
    Match strategy, safest-first -- we would rather show 'feed unavailable' than the WRONG game:
      1. EXACT key match (date + HHMM + game_no + team-set) -- the normal path.
      2. Start-time skew tolerance: exactly ONE feed game for (date, team-set) and NOT a doubleheader
         -> return it (Kalshi's ticket minute occasionally differs by a minute from the feed's scheduled
         start; team-set + date already uniquely identify a non-doubleheader game).
      3. Doubleheader: if the ticker carries a game_no, take the feed game with the same game_no.
    Anything ambiguous -> None (the caller degrades to feed-unavailable)."""
    exact = games.get((date_iso, hhmm, game_no, team_set))
    if exact is not None:
        return exact
    cands = [g for k, g in games.items() if k[0] == date_iso and k[3] == team_set]
    if game_no is None and len(cands) == 1:
        return cands[0]
    if game_no is not None:
        gn = [g for g in cands if g.game_no == game_no]
        if len(gn) == 1:
            return gn[0]
    return None


# ── the normalized game state a card renders ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TeamState:
    abbr: str | None
    name: str | None            # short club name for display (e.g. "Padres")
    record: str | None          # "71-66" or None
    score: int | None


@dataclass(frozen=True)
class GameState:
    """One game's feed-derived state, source-tagged and time-stamped. `status` is one of:
    preview | in_progress | final | postponed | suspended | delayed | unknown. Live-only fields
    (inning/half/outs/balls/strikes/bases/last_play) are None/empty unless status == in_progress."""
    key: tuple
    date_iso: str
    hhmm_et: str | None
    game_no: int | None
    source: str                 # 'statsapi' | 'espn'
    fetched_ts: int             # unix seconds when this slate was fetched (the value's own age)
    game_pk: str | None         # StatsAPI gamePk (enables the optional last-play enrichment); None for ESPN
    status: str
    away: TeamState
    home: TeamState
    inning: int | None
    half: str | None            # 'TOP' | 'BOT' | 'MIDDLE' | 'END'
    outs: int | None
    balls: int | None
    strikes: int | None
    bases: tuple                # (first, second, third) booleans; empty tuple when not live
    linescore_away: tuple       # per-inning runs, None-padded for un-played half-innings
    linescore_home: tuple
    last_play: str | None

    @property
    def is_live(self) -> bool:
        return self.status == "in_progress"

    @property
    def is_final(self) -> bool:
        return self.status == "final"


@dataclass(frozen=True)
class SlateResult:
    """The whole day's feed read. `games` maps feed_game_key -> GameState. `ok` is False when BOTH feeds failed
    (games is then empty and every card degrades to feed-unavailable). `source`/`as_of` state provenance+age."""
    date_iso: str
    games: dict
    ok: bool
    source: str | None
    as_of: int | None
    error: str | None = None


# ── StatsAPI parse (primary) ─────────────────────────────────────────────────────────────────────────────────
_STATUS_MAP = {
    "in progress": "in_progress", "live": "in_progress", "manager challenge": "in_progress",
    "warmup": "preview", "pre-game": "preview", "scheduled": "preview", "preview": "preview",
    "final": "final", "game over": "final", "completed early": "final", "final: tied": "final",
    "delayed": "delayed", "delayed start": "delayed",
    "postponed": "postponed",
}


def _map_status(detailed: str | None, abstract: str | None) -> str:
    d = (detailed or "").strip().lower()
    if d.startswith("suspended"):
        return "suspended"
    if d.startswith("postponed"):
        return "postponed"
    if d.startswith("delayed"):
        return "delayed"
    if d in _STATUS_MAP:
        return _STATUS_MAP[d]
    a = (abstract or "").strip().lower()
    if a == "live":
        return "in_progress"
    if a == "final":
        return "final"
    if a == "preview":
        return "preview"
    return "unknown"


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _record(team_side: dict) -> str | None:
    lr = (team_side or {}).get("leagueRecord") or {}
    w, l = lr.get("wins"), lr.get("losses")
    return f"{w}-{l}" if w is not None and l is not None else None


def _statsapi_linescore(ls: dict):
    innings = (ls or {}).get("innings") or []
    away, home = [], []
    for inn in innings:
        a = (inn.get("away") or {}).get("runs")
        h = (inn.get("home") or {}).get("runs")
        away.append(a if isinstance(a, int) else None)
        home.append(h if isinstance(h, int) else None)
    return tuple(away), tuple(home)


def parse_statsapi_schedule(sched_json: dict, *, now_ts: int) -> dict:
    """StatsAPI /schedule?hydrate=linescore,team JSON -> {feed_game_key: GameState}. Games whose clubs do not
    resolve to canonical names are DROPPED (they cannot be joined safely). Pure; no network."""
    out: dict = {}
    for date_block in (sched_json or {}).get("dates", []) or []:
        for g in date_block.get("games", []) or []:
            teams = g.get("teams") or {}
            away_t = (teams.get("away") or {}).get("team") or {}
            home_t = (teams.get("home") or {}).get("team") or {}
            away_name = canonical_team(away_t.get("abbreviation"))
            home_name = canonical_team(home_t.get("abbreviation"))
            date_iso, hhmm = eastern_key_parts(g.get("gameDate"))
            game_no = _int_or_none(g.get("gameNumber")) if (g.get("doubleHeader") in ("Y", "S")) else None
            key = feed_game_key(date_iso, away_name, home_name, hhmm, game_no)
            if key is None:
                continue
            ls = g.get("linescore") or {}
            status = _map_status((g.get("status") or {}).get("detailedState"),
                                 (g.get("status") or {}).get("abstractGameState"))
            live = status == "in_progress"
            off = (ls.get("offense") or {})
            bases = (("first" in off), ("second" in off), ("third" in off)) if live else ()
            half = None
            if live:
                st = (ls.get("inningState") or ls.get("inningHalf") or "").strip().lower()
                half = {"top": "TOP", "bottom": "BOT", "middle": "MIDDLE", "end": "END"}.get(st,
                        "TOP" if st.startswith("t") else "BOT" if st.startswith("b") else None)
            la, lh = _statsapi_linescore(ls)
            out[key] = GameState(
                key=key, date_iso=date_iso, hhmm_et=hhmm, game_no=game_no, source="statsapi",
                fetched_ts=now_ts, game_pk=str(g.get("gamePk")) if g.get("gamePk") is not None else None,
                status=status,
                away=TeamState(away_t.get("abbreviation"), away_t.get("teamName") or away_t.get("name"),
                               _record(teams.get("away")), _int_or_none((teams.get("away") or {}).get("score"))),
                home=TeamState(home_t.get("abbreviation"), home_t.get("teamName") or home_t.get("name"),
                               _record(teams.get("home")), _int_or_none((teams.get("home") or {}).get("score"))),
                inning=_int_or_none(ls.get("currentInning")) if live else None,
                half=half,
                outs=_int_or_none(ls.get("outs")) if live else None,
                balls=_int_or_none(ls.get("balls")) if live else None,
                strikes=_int_or_none(ls.get("strikes")) if live else None,
                bases=bases, linescore_away=la, linescore_home=lh, last_play=None)
    return out


# ── ESPN parse (fallback) ────────────────────────────────────────────────────────────────────────────────────
_ESPN_STATE = {"pre": "preview", "in": "in_progress", "post": "final"}


def _espn_status(comp_status: dict) -> str:
    t = (comp_status or {}).get("type") or {}
    name = (t.get("name") or "").upper()
    if "POSTPONED" in name:
        return "postponed"
    if "SUSPENDED" in name:
        return "suspended"
    if "DELAY" in name:
        return "delayed"
    return _ESPN_STATE.get((t.get("state") or "").lower(), "unknown")


def parse_espn_scoreboard(espn_json: dict, *, now_ts: int) -> dict:
    """ESPN scoreboard JSON -> {feed_game_key: GameState}. Fallback path; same key discipline as StatsAPI.
    ESPN gives count/bases/last-play inline in `situation` (one call for the whole slate) but uses ARI/CHW/OAK
    codes -- canonical_team() reconciles them. Pure; no network."""
    out: dict = {}
    for ev in (espn_json or {}).get("events", []) or []:
        comps = ev.get("competitions") or []
        if not comps:
            continue
        comp = comps[0]
        competitors = comp.get("competitors") or []
        away_c = next((c for c in competitors if c.get("homeAway") == "away"), None)
        home_c = next((c for c in competitors if c.get("homeAway") == "home"), None)
        if not (away_c and home_c):
            continue
        away_abbr = ((away_c.get("team") or {}).get("abbreviation"))
        home_abbr = ((home_c.get("team") or {}).get("abbreviation"))
        away_name = canonical_team(away_abbr)
        home_name = canonical_team(home_abbr)
        date_iso, hhmm = eastern_key_parts(comp.get("date") or ev.get("date"))
        # ESPN marks a doubleheader's second game with doubleheader==2 (rare in payload); default None.
        game_no = _int_or_none(comp.get("doubleheader"))
        key = feed_game_key(date_iso, away_name, home_name, hhmm, game_no)
        if key is None:
            continue
        status = _espn_status(comp.get("status") or ev.get("status") or {})
        live = status == "in_progress"
        sit = comp.get("situation") or {}
        bases = ((bool(sit.get("onFirst")), bool(sit.get("onSecond")), bool(sit.get("onThird")))
                 if live else ())
        st = comp.get("status") or {}
        half = None
        if live:
            half = "TOP" if (st.get("type") or {}).get("shortDetail", "").lower().startswith("top") else "BOT"
        la, lh = _espn_linescore(away_c), _espn_linescore(home_c)
        out[key] = GameState(
            key=key, date_iso=date_iso, hhmm_et=hhmm, game_no=game_no, source="espn",
            fetched_ts=now_ts, game_pk=None, status=status,
            away=TeamState(away_abbr, (away_c.get("team") or {}).get("shortDisplayName"),
                           _espn_record(away_c), _int_or_none(away_c.get("score"))),
            home=TeamState(home_abbr, (home_c.get("team") or {}).get("shortDisplayName"),
                           _espn_record(home_c), _int_or_none(home_c.get("score"))),
            inning=_int_or_none(st.get("period")) if live else None,
            half=half,
            outs=_int_or_none(sit.get("outs")) if live else None,
            balls=_int_or_none(sit.get("balls")) if live else None,
            strikes=_int_or_none(sit.get("strikes")) if live else None,
            bases=bases, linescore_away=la, linescore_home=lh,
            last_play=((sit.get("lastPlay") or {}).get("text") if live else None))
    return out


def _espn_linescore(competitor: dict):
    return tuple(_int_or_none(x.get("value")) for x in (competitor.get("linescores") or []))


def _espn_record(competitor: dict) -> str | None:
    recs = competitor.get("records") or []
    for r in recs:
        if r.get("type") in ("total", "ytd") or r.get("name") in ("overall", "All Splits"):
            return r.get("summary")
    return recs[0].get("summary") if recs else None


# ── thin fetch layer + orchestration ─────────────────────────────────────────────────────────────────────────
def _http_get_json(url: str, *, timeout: float = 12.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310 -- fixed https feed hosts
        return json.loads(resp.read().decode("utf-8"))


def fetch_slate(date_iso: str, *, now_ts: int, http_get=_http_get_json) -> SlateResult:
    """Fetch the day's slate: StatsAPI first, ESPN on failure/empty. `date_iso` is the ET calendar date
    ('YYYY-MM-DD'). `http_get` is injectable for tests. NEVER raises -- any failure yields an ok=False slate
    with an empty game map, and the caller degrades every card to feed-unavailable."""
    compact = date_iso.replace("-", "")
    try:
        js = http_get(_STATSAPI % date_iso)
        games = parse_statsapi_schedule(js, now_ts=now_ts)
        if games:
            return SlateResult(date_iso, games, True, "statsapi", now_ts)
        log.info("pm feed: StatsAPI returned 0 games for %s -- trying ESPN", date_iso)
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError, OSError) as exc:
        log.warning("pm feed: StatsAPI failed for %s (%s) -- trying ESPN", date_iso, type(exc).__name__)
    try:
        js = http_get(_ESPN % compact)
        games = parse_espn_scoreboard(js, now_ts=now_ts)
        return SlateResult(date_iso, games, bool(games), "espn", now_ts,
                           error=None if games else "both feeds empty")
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError, OSError) as exc:
        log.warning("pm feed: ESPN also failed for %s (%s) -- feed unavailable", date_iso, type(exc).__name__)
        return SlateResult(date_iso, {}, False, None, now_ts, error=type(exc).__name__)


def fetch_last_play(game_pk: str, *, timeout: float = 8.0, http_get=_http_get_json) -> str | None:
    """Best-effort last-play text for ONE StatsAPI game (a trimmed feed/live read via fields=). Only called for
    held LIVE games, so the whole-slate primary read stays a single request. None on any failure."""
    if not game_pk:
        return None
    try:
        js = http_get(_STATSAPI_LASTPLAY % game_pk, timeout=timeout)
        cur = (((js or {}).get("liveData") or {}).get("plays") or {}).get("currentPlay") or {}
        desc = (cur.get("result") or {}).get("description")
        return desc.strip() if isinstance(desc, str) and desc.strip() else None
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError, OSError):
        return None
