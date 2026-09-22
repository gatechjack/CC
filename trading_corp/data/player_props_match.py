"""Player-prop matcher core (2026-09-22), shared by the NFL (sports_structural_match) and MLB
(mlb_poly_kalshi_match) paths -- Phase B/C.

MAP (Poly slug stat token -> Kalshi series), built by querying Kalshi's live catalog against the whales' real
prop slugs (pm_propmap_ro): see POLY_PROP_STATS. Kalshi prop ticker shape:
    KX{SERIES}-{game-stem}-{PLAYER-CODE}[-{STRIKE}]
    PLAYER-CODE = {TEAM}{FIRST-INITIAL}{LASTNAME}{JERSEY}   e.g. GBKJOHNSON26 = GB / K / JOHNSON / #26.
Binding: game via the caller's structural game index (stem); player BY CODE within that game
(team + first-initial + lastname); the jersey disambiguates same-name players -> a same-name collision with no
jersey signal from Poly is a SAFE MISS. Strike: Poly N.5 -> Kalshi (N+1)+ rung, EXACT-STRIKE-ONLY (no rung = safe
miss, never the nearest). Leg: Over->yes / Under->no (ladder) or Yes->yes / No->no (binary), from the OUTCOME
(resolution-derived), never the slug. An unrecognised stat token FAILS CLOSED (returns None -> the caller's
non_moneyline skip) and is reported.

Pure stdlib -> importable by pm_web's standalone guard as well as the engine.
"""
from __future__ import annotations

import math
import re
import unicodedata

# Poly slug stat token -> (Kalshi series, kind). 'ladder' = N.5 -> (N+1)+ strike; 'binary' = 1+/yes-no, no strike.
_NFL_STATS = {
    "ryd": ("KXNFLRSHYDS", "ladder"),
    "recyd": ("KXNFLRECYDS", "ladder"),
    "pyd": ("KXNFLPASSYDS", "ladder"),
    "ptd": ("KXNFLPASSTDS", "ladder"),
    "rec": ("KXNFLREC", "ladder"),
    "anytime-td": ("KXNFLANYTD", "binary"),
    "first-td": ("KXNFLFIRSTTD", "binary"),
}
_MLB_STATS = {
    "k": ("KXMLBKS", "ladder"),
    "hr": ("KXMLBHR", "ladder"),
    "outs": ("KXMLBOUTS", "ladder"),
    "tb": ("KXMLBTB", "ladder"),
    "hrr": ("KXMLBHRR", "ladder"),
    "hits": ("KXMLBHIT", "ladder"),
}
_STATS_BY_CAT = {"nfl": _NFL_STATS, "mlb": _MLB_STATS}
# every Kalshi prop series this module knows -> its category (for the index builder + fill-watch)
PROP_SERIES = {s: cat for cat, m in _STATS_BY_CAT.items() for (s, _k) in m.values()}
# and -> its kind ('ladder'|'binary'); the full-name (title) gate applies ONLY to ladder series (binary first-td/anytd
# titles are team D/ST or "No Touchdown", not a player name -- see _title_name_key).
PROP_SERIES_KIND = {s: k for _c, m in _STATS_BY_CAT.items() for (s, k) in m.values()}
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}          # dropped from BOTH sides before comparing
_LADDER_RE = re.compile(r"^(?P<player>.+)-(?P<w>\d+)pt(?P<f>\d+)$")


def _fold(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", (s or "")).encode("ascii", "ignore").decode("ascii").lower())


def poly_full_key(player_slug: str):
    """A Poly player slug -> (FULL first name, lastname), both folded [a-z0-9]; suffixes dropped, hyphenated surnames
    concatenated. Unlike poly_player_key this keeps the WHOLE first name (not just the initial) so 'kaleb-johnson'
    and 'kevin-johnson' are DISTINGUISHED. None if unparseable."""
    toks = [t for t in (player_slug or "").split("-") if t]
    toks = [t for t in toks if t.lower() not in _NAME_SUFFIXES] or toks
    if len(toks) < 2:
        return None
    first = _fold(toks[0])
    lastname = _fold("".join(toks[1:]))
    if not first or not lastname:
        return None
    return (first, lastname)


def _title_name_key(title: str):
    """A Kalshi LADDER prop title/subtitle ('Josh Allen: 90+', 'Carlos Rodón: 16+ Outs Recorded?') -> (FULL first,
    lastname) folded, or None. The player name is the run BEFORE the first ':' -- verified across every ladder series
    2026-09-22. Rejects a name that contains a digit or is a single token (fail-closed so a non-player title -> None)."""
    if not title:
        return None
    head = title.split(":", 1)[0].strip()
    if not head or any(ch.isdigit() for ch in head):
        return None
    words = [w for w in re.split(r"\s+", head) if w]
    words = [w for w in words if w.lower().rstrip(".") not in _NAME_SUFFIXES] or words
    if len(words) < 2:
        return None
    first = _fold(words[0])
    lastname = _fold("".join(words[1:]))
    if not first or not lastname:
        return None
    return (first, lastname)


def _first_compatible(a: str, b: str) -> bool:
    """Two folded first names are the SAME person's if equal, or one is a >=3-char prefix of the other (Cam/Cameron,
    Alex/Alexander). Kaleb vs Kevin are NEITHER -> incompatible -> different players. A <3-char first requires equality
    (so 'j' never matches 'josh')."""
    if not a or not b:
        return False
    if a == b:
        return True
    lo, hi = (a, b) if len(a) <= len(b) else (b, a)
    return len(lo) >= 3 and hi.startswith(lo)


def poly_player_key(player_slug: str):
    """A Poly player slug ('cam-skattebo', 'jaxon-smith-njigba', 'marvin-harrison-jr') -> (initial, lastname),
    folded [a-z0-9], name-suffix (Jr/Sr/II..) dropped, hyphenated surnames concatenated. None if unparseable."""
    toks = [t for t in (player_slug or "").split("-") if t]
    toks = [t for t in toks if t.lower() not in _NAME_SUFFIXES] or toks
    if len(toks) < 2:
        return None
    initial = _fold(toks[0])[:1]
    lastname = _fold("".join(toks[1:]))
    if not initial or not lastname:
        return None
    return (initial, lastname)


def kalshi_player_key(player_code: str, team_a_code: str, team_b_code: str):
    """Kalshi PLAYER-CODE + the game's two team codes -> (initial, lastname). Strips the leading team code
    (longest-first) and the trailing jersey digits. None if the code does not start with either team."""
    pc = (player_code or "").upper()
    team = None
    for t in sorted((c for c in (team_a_code, team_b_code) if c), key=len, reverse=True):
        if pc.startswith(t.upper()):
            team = t.upper(); break
    if team is None:
        return None
    rest = pc[len(team):]
    rest = re.sub(r"\d+$", "", rest)                            # drop trailing jersey
    toks = [t for t in re.split(r"[^A-Za-z]", rest) if t]
    core = "".join(toks)
    core = _fold(core)
    if len(core) < 2:
        return None
    # drop a trailing name-suffix if present (e.g. HARRISONJR -> HARRISON) only when it leaves a real surname
    for suf in ("iii", "ii", "iv", "jr", "sr"):
        if core.endswith(suf) and len(core) - len(suf) >= 2:
            core = core[:-len(suf)]; break
    return (core[:1], core[1:])


def parse_prop_suffix(category: str, suffix: str, outcome: str, title: str = None):
    """The slug suffix AFTER the game (e.g. '-ryd-cam-skattebo-49pt5', '-anytime-td-josh-allen') -> a dict
    {stat, series, kind, player_slug, line, strike, leg, over} or None (unrecognised stat token -> caller SKIP).
    leg is derived from the OUTCOME (resolution-side), NEVER the slug; None leg -> caller records a safe miss."""
    stats = _STATS_BY_CAT.get(category)
    if not stats or not suffix:
        return None
    body = suffix[1:] if suffix.startswith("-") else suffix
    for tok in sorted(stats, key=len, reverse=True):           # longest-first: 'anytime-td' before nothing; 'recyd' before 'rec'
        if body == tok or body.startswith(tok + "-"):
            series, kind = stats[tok]
            rest = body[len(tok):].lstrip("-")
            o = (outcome or "").strip().lower()
            if kind == "binary":
                player_slug = rest
                leg = "yes" if o in ("yes",) else "no" if o in ("no",) else None
                over = (leg == "yes")
                return {"stat": tok, "series": series, "kind": kind, "player_slug": player_slug,
                        "line": None, "strike": None, "leg": leg, "over": over}
            m = _LADDER_RE.match(rest)
            if not m:
                return {"stat": tok, "series": series, "kind": kind, "player_slug": rest, "line": None,
                        "strike": None, "leg": None}   # unparseable line -> safe miss (caller reports)
            line = int(m.group("w")) + int(m.group("f")) / (10 ** len(m.group("f")))
            if line % 1 == 0:                          # WHOLE-number Poly line -> push hazard (Poly "equal to or
                # exceeds" pushes AT the line, but Kalshi's N+ rung would pay/lose there). Refuse like team_total
                # (mlb_poly_kalshi_match team_total_whole_number_line_push_hazard) -> strike/leg None -> safe miss.
                return {"stat": tok, "series": series, "kind": kind, "player_slug": m.group("player"),
                        "line": line, "strike": None, "leg": None}
            strike = int(math.ceil(line - 1e-9))       # Poly N.5 -> Kalshi (N+1)+  (e.g. 89.5 -> 90)
            leg = "yes" if o == "over" else "no" if o == "under" else None
            return {"stat": tok, "series": series, "kind": kind, "player_slug": m.group("player"),
                    "line": line, "strike": strike, "leg": leg, "over": (leg == "yes")}
    return None                                                # unrecognised stat token -> fail closed


def parse_prop_ticker(ticker: str):
    """A Kalshi prop ticker -> (series, stem, player_code, strike|None) or None. Ladder = trailing -{int};
    binary (ANYTD/FIRSTTD) = no trailing strike."""
    parts = (ticker or "").split("-")
    if len(parts) < 3 or parts[0] not in PROP_SERIES:
        return None
    series, stem = parts[0], parts[1]
    if len(parts) >= 4 and parts[-1].isdigit():
        return (series, stem, parts[2], int(parts[-1]))
    return (series, stem, parts[2], None)


def build_prop_index(tickers, titles: dict = None):
    """{series: {stem: [(player_code, strike|None, ticker, name_key)]}}. name_key = (full first, lastname) parsed from
    the market TITLE (via `titles` {ticker: title/yes_sub_title}) for LADDER series ONLY, else None. The full-name key
    lets match_prop DISTINGUISH same-initial-same-surname opposite-team players (Kaleb vs Kevin Johnson) that the
    code's initial-only key cannot -- the confirmed cross-team wrong-bind. team codes are still applied at match time
    for the code cross-check. `titles` is optional (engine passes it; a code-only build leaves name_key None)."""
    titles = titles or {}
    idx: dict = {}
    for t in tickers or []:
        p = parse_prop_ticker(t)
        if p is None:
            continue
        series, stem, pc, strike = p
        name_key = _title_name_key(titles.get(t)) if PROP_SERIES_KIND.get(series) == "ladder" else None
        idx.setdefault(series, {}).setdefault(stem, []).append((pc, strike, t, name_key))
    return idx


def match_prop(parsed: dict, stem: str, team_a_code: str, team_b_code: str, prop_index: dict):
    """(ticker, leg, reason). reason is set ONLY on a miss (ticker None): unrecognised handled upstream. Misses:
    leg_unresolved | player_not_found | no_kalshi_strike:<rungs> | ambiguous_same_name. Exact-strike, code+NAME-bound.

    Binding (2026-09-22 hardening): a candidate must match the code key (team-stripped initial+lastname) AND, when the
    ticker carries a title-derived name_key (ladder, engine path), the whale's FULL first name must be compatible with
    the ticker's full first name -- a titled entry with an incompatible first name is a DIFFERENT player and is EXCLUDED
    (closes the wrong-team bind where only the opponent's same-surname player is listed). Additionally, if the surviving
    candidates span >1 distinct player code, the bet is ambiguous and REFUSED (closes the both-listed asymmetric-strike
    leak in the code-only fallback)."""
    if parsed is None:
        return (None, None, "not_a_prop")
    if parsed.get("leg") is None:
        return (None, None, "leg_unresolved:%r" % (parsed.get("player_slug"),))
    pkey = poly_player_key(parsed["player_slug"])
    if pkey is None:
        return (None, None, "poly_player_unparseable:%r" % parsed["player_slug"])
    pfull = poly_full_key(parsed["player_slug"])
    entries = prop_index.get(parsed["series"], {}).get(stem, [])
    cand_codes = set()         # distinct player codes that survive code + name gating
    player_rungs = []          # every strike Kalshi lists for the surviving player(s) (for the miss table)
    exact = []                 # (player_code, ticker) at the exact wanted strike
    for pc, strike, t, name_key in entries:
        if kalshi_player_key(pc, team_a_code, team_b_code) != pkey:
            continue                                   # wrong surname/initial (or code not in this game)
        if name_key is not None:                       # titled ladder ticker -> FULL-name gate
            if pfull is None or not (_first_compatible(pfull[0], name_key[0]) and pfull[1] == name_key[1]):
                continue                               # titled but a DIFFERENT player -> exclude (wrong-team close)
        cand_codes.add(pc)
        player_rungs.append(strike)
        if strike == parsed["strike"]:
            exact.append((pc, t))
    if not player_rungs:
        return (None, None, "player_not_found")
    if len(cand_codes) > 1:                             # >1 distinct same-name player survived -> cannot disambiguate
        return (None, None, "ambiguous_same_name:%d" % len(cand_codes))
    if not exact:
        return (None, None, "no_kalshi_strike:want=%s have=%s" % (parsed["strike"], sorted(r for r in player_rungs if r is not None)))
    if len({pc for pc, _t in exact}) > 1:
        return (None, None, "ambiguous_same_name:%d" % len({pc for pc, _t in exact}))
    return (exact[0][1], parsed["leg"], None)
