"""Deterministic Polymarket-soccer -> Kalshi matcher (3-way result: team-win + draw), per league.

STRUCTURAL VARIANT. Polymarket lists a soccer game as separate Yes/No binaries:
  * team-win:  slug `{lg}-{away}-{home}-{date}-{teamcode}`, title "Will {Team} win on {date}?",
               outcome Yes/No  -> Kalshi "{Team} wins" market, YES/NO leg (No = draw-or-lose,
               which is exactly the Kalshi NO leg -- a clean 1:1).
  * draw:      slug `{lg}-{a}-{h}-{date}-draw`, title "Will {A} vs {B} end in a draw?", outcome
               Yes/No  -> Kalshi TIE market ("Tie is the result"), YES/NO leg.
Kalshi KX{LG}GAME lists THREE markets per game -- "{A} wins", "{B} wins", "Tie is the result" --
the three sharing a (date, blob). BOTH venues settle 90-MINUTE REGULATION (Kalshi rule text; Poly
likewise), so the UCL/UEL knockout extra-time/penalties divergence DISSOLVES -- a draw after 90 is
a draw on both. Totals (`-total-`, "O/U") and spreads (`-spread-`, "Spread:") are OUT of scope.

Club names join by EXACT base-normalize (accent-fold + lowercase + strip-punct + collapse) through a
per-league, data-verified alias table (soccer_teams.py) -- NEVER fuzzy. The alias table is built from
REAL two-venue names and HARD collision-checked: no two DIFFERENT clubs may map to one Kalshi target.
Named collisions kept distinct: Ligue 1 "Paris"(PSG)/"Paris FC", MLS "Los Angeles F"(LAFC)/"Los
Angeles G"(Galaxy), and cross-league Inter Milan / Inter Miami (different leagues, different tables).

Reuses the UFC accent-fold `_norm` idea via its own base(); reuses `kalshi_to_iso_date`. +/-1 day
window (AP/late kickoffs straddle UTC midnight; the tennis/cs2-proven construct).
"""
from __future__ import annotations

import datetime as _dt
import re
import unicodedata
from dataclasses import dataclass, field

from .ufc_poly_kalshi_match import kalshi_to_iso_date  # pure date helper, reused verbatim

WINDOW_DAYS = 1
COPYABLE_MARKET_TYPES = ("moneyline",)   # the 3-way result (win or draw); totals/spreads excluded

_WIN_RE = re.compile(r"^Will (?P<team>.+?) win on \d{4}-\d{2}-\d{2}\?$")
_DRAW_RE = re.compile(r"^Will (?P<a>.+?) vs\.? (?P<b>.+?) end in a draw\?$", re.I)
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# Kalshi ticker: KX{LEAGUE}GAME-{YYMONDD}{BLOB}-{CODE}
_K_RE = re.compile(r"^KX[A-Z0-9]+GAME-(?P<date>\d{2}[A-Z]{3}\d{2})(?P<blob>[A-Z0-9]+)-(?P<code>[A-Z0-9]+)$")


def _base(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.casefold()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@dataclass(frozen=True)
class SoccerLeague:
    category: str          # our sub-division category (poly prefix), e.g. 'epl'
    poly_prefix: str       # slug prefix, e.g. 'epl'
    game_series: str       # Kalshi series, e.g. 'KXEPLGAME'
    aliases: dict          # {poly_base -> kalshi_base}, data-verified, collision-checked

    def canon(self, name: str) -> str:
        b = _base(name)
        return self.aliases.get(b, b)


@dataclass(frozen=True)
class ParsedSoccerBet:
    market_type: str            # 'moneyline' | 'non_moneyline' | 'non_soccer' | 'unparseable'
    kind: str | None            # 'win' | 'draw'
    date_iso: str | None
    team: str | None            # win: the team; draw: None
    pair_a: str | None          # draw: team A
    pair_b: str | None          # draw: team B
    leg: str | None             # 'yes' | 'no' (from outcome)
    fail_reason: str | None = None
    raw: dict = field(default_factory=dict)


def parse_poly_bet(slug: str, outcome: str, cfg: SoccerLeague, title: str | None = None) -> ParsedSoccerBet:
    raw = {"slug": slug, "outcome": outcome, "title": title}
    s = slug or ""
    if not s.startswith(cfg.poly_prefix + "-"):
        return ParsedSoccerBet("non_soccer", None, None, None, None, None, None,
                               fail_reason="slug_not_%s" % cfg.poly_prefix, raw=raw)
    dm = _DATE_RE.search(s)
    date_iso = dm.group(1) if dm else None
    t = title or ""
    # totals / spreads -> out of scope
    if re.search(r"-total-", s) or "O/U" in t:
        return ParsedSoccerBet("non_moneyline", None, date_iso, None, None, None, None,
                               fail_reason="total", raw=raw)
    if re.search(r"-spread-", s) or t.startswith("Spread:"):
        return ParsedSoccerBet("non_moneyline", None, date_iso, None, None, None, None,
                               fail_reason="spread", raw=raw)
    oc = (outcome or "").strip()
    leg = "yes" if oc == "Yes" else ("no" if oc == "No" else None)
    mw = _WIN_RE.match(t)
    md = _DRAW_RE.match(t)
    if mw:
        if leg is None:
            return ParsedSoccerBet("non_moneyline", "win", date_iso, mw.group("team"), None, None, None,
                                   fail_reason="win_outcome_not_yes_no", raw=raw)
        return ParsedSoccerBet("moneyline", "win", date_iso, mw.group("team"), None, None, leg, raw=raw)
    if md:
        if leg is None:
            return ParsedSoccerBet("non_moneyline", "draw", date_iso, None, md.group("a"), md.group("b"), None,
                                   fail_reason="draw_outcome_not_yes_no", raw=raw)
        return ParsedSoccerBet("moneyline", "draw", date_iso, None, md.group("a"), md.group("b"), leg, raw=raw)
    return ParsedSoccerBet("non_moneyline", None, date_iso, None, None, None, None,
                           fail_reason="title_not_win_or_draw", raw=raw)


@dataclass(frozen=True)
class SoccerGame:
    date_iso: str
    clubs: frozenset          # frozenset of the two canon club names
    side_ticker: dict         # {canon club -> "{club} wins" ticker}
    tie_ticker: str | None


def build_game_index(markets: list[dict], cfg: SoccerLeague) -> dict:
    """{date_iso: [SoccerGame]} from KX{LG}GAME markets. Each game = the (date, blob) group: its two
    win-legs (yes_sub_title = club) + the TIE market. A group without exactly 2 distinct win-legs is
    skipped (a draw needs both; a win needs its own leg). Non-KX{LG}GAME tickers cannot enter."""
    by: dict = {}
    for mk in markets:
        tk = (mk.get("ticker") or "").strip()
        org = (mk.get("yes_sub_title") or mk.get("yes") or "").strip()
        # UCL/UEL/UECL label the 90-minute-regulation market "Reg Time: {Club}" (knockouts/qualifiers
        # that could go to ET/penalties). Poly settles 90-min regulation too, so the Reg-Time market is
        # the CORRECT counterpart -- strip the prefix and treat it like any other 90-min result market.
        # League-phase games have no prefix (no ET -> plain IS 90-min). Groups are uniform (never both).
        if org[:10].lower() == "reg time: ":
            org = org[10:].strip()
        m = _K_RE.match(tk)
        if not m:
            continue
        by.setdefault((m.group("date"), m.group("blob")), []).append((m.group("code"), org, tk))
    idx: dict = {}
    for (ds, _bl), legs in by.items():
        d = kalshi_to_iso_date(ds)
        if not d:
            continue
        side = {}
        tie_ticker = None
        for _code, org, tk in legs:
            if _base(org) == "tie":
                tie_ticker = tk
            elif org:
                side[cfg.canon(org)] = tk
        if len(side) != 2:
            continue                      # need exactly the two win-legs to identify the game
        idx.setdefault(d, []).append(SoccerGame(d, frozenset(side), side, tie_ticker))
    return idx


@dataclass(frozen=True)
class MatchResult:
    status: str
    confidence: float
    kalshi_ticker: str | None = None
    reason: str | None = None
    leg: str | None = None
    market_type: str | None = None


def _window(date_iso: str) -> list[str]:
    y, mo, d = (int(x) for x in date_iso.split("-"))
    b = _dt.date(y, mo, d)
    return [(b + _dt.timedelta(days=k)).isoformat() for k in range(-WINDOW_DAYS, WINDOW_DAYS + 1)]


def match_bet(parsed: ParsedSoccerBet, game_index: dict, kalshi_dates, cfg: SoccerLeague,
              allowed_market_types=COPYABLE_MARKET_TYPES) -> MatchResult:
    mt = parsed.market_type
    if mt != "moneyline":
        if mt == "non_soccer":
            return MatchResult("skip_non_soccer", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
        if mt == "non_moneyline":
            return MatchResult("skip_non_moneyline", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
        return MatchResult("fail", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
    if mt not in allowed_market_types:
        return MatchResult("skip_market_type_excluded", 0.0,
                           reason="moneyline_not_in_subdivision_market_types", market_type=mt)
    if not parsed.date_iso:
        return MatchResult("fail", 0.0, reason="no_date", market_type=mt)
    if parsed.leg not in ("yes", "no"):
        return MatchResult("fail", 0.0, reason="no_leg", market_type=mt)

    cand: list[SoccerGame] = []
    for d in _window(parsed.date_iso):
        cand.extend(game_index.get(d, []))
    if not cand:
        if parsed.date_iso not in kalshi_dates:
            return MatchResult("out_of_window", 0.0, reason="date_outside_kalshi_window", market_type=mt)
        return MatchResult("no_kalshi_contract", 0.0, reason="no_game_in_window", market_type=mt)

    if parsed.kind == "win":
        ct = cfg.canon(parsed.team)
        if not ct:
            return MatchResult("fail", 0.0, reason="empty_team", market_type=mt)
        hits = [g for g in cand if ct in g.side_ticker]
        uniq = {tuple(sorted(g.side_ticker.values())): g for g in hits}
        if len(uniq) == 1:
            g = next(iter(uniq.values()))
            return MatchResult("matched", 1.0, kalshi_ticker=g.side_ticker[ct], leg=parsed.leg,
                               reason="win_resolved", market_type=mt)
        if len(uniq) > 1:
            return MatchResult("collision_ambiguous", 0.5, reason="team_in_multiple_games_in_window", market_type=mt)
        return MatchResult("no_kalshi_contract", 0.0, reason="team_absent_from_window", market_type=mt)

    # draw
    ca, cb = cfg.canon(parsed.pair_a), cfg.canon(parsed.pair_b)
    if not ca or not cb or ca == cb:
        return MatchResult("fail", 0.0, reason="degenerate_pair", market_type=mt)
    want = frozenset((ca, cb))
    hits = [g for g in cand if g.clubs == want]
    uniq = {g.tie_ticker or tuple(sorted(g.side_ticker.values())): g for g in hits}
    if len(uniq) == 1:
        g = next(iter(uniq.values()))
        if not g.tie_ticker:
            return MatchResult("no_kalshi_contract", 0.0, reason="game_found_but_no_tie_market", market_type=mt)
        return MatchResult("matched", 1.0, kalshi_ticker=g.tie_ticker, leg=parsed.leg,
                           reason="draw_resolved", market_type=mt)
    if len(uniq) > 1:
        return MatchResult("collision_ambiguous", 0.5, reason="pair_in_multiple_games_in_window", market_type=mt)
    return MatchResult("no_kalshi_contract", 0.0, reason="pair_absent_from_window", market_type=mt)


# ── league registry (Tier-A domestic, by whale volume). aliases from soccer_teams.py. UCL/UEL and the
# tail are LISTED DEFERRALS (see the report) until their team maps are built. ────────────────────────
from . import soccer_teams as _ST  # noqa: E402

_SERIES = {
    "epl": "KXEPLGAME", "lal": "KXLALIGAGAME", "fl1": "KXLIGUE1GAME", "sea": "KXSERIEAGAME",
    "bun": "KXBUNDESLIGAGAME", "mls": "KXMLSGAME", "bra": "KXBRASILEIROGAME", "mex": "KXLIGAMXGAME",
    # Tier-B European competitions (by whale volume: ucl #2, uel #5). Their Kalshi markets use the
    # "Reg Time:" 90-minute-regulation leg for knockouts (stripped in build_game_index) = Poly's 90-min.
    "ucl": "KXUCLGAME", "uel": "KXUELGAME",
}
LEAGUES = {cat: SoccerLeague(cat, cat, _SERIES[cat], _ST.SOCCER_TEAMS.get(cat, {})) for cat in _SERIES}
