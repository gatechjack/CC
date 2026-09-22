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
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}          # dropped from BOTH sides before comparing
_LADDER_RE = re.compile(r"^(?P<player>.+)-(?P<w>\d+)pt(?P<f>\d+)$")


def _fold(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", (s or "")).encode("ascii", "ignore").decode("ascii").lower())


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
            strike = int(math.ceil(line - 1e-9)) if (line % 1) else int(line) + 1  # Poly N.5 -> Kalshi (N+1)+
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


def build_prop_index(tickers):
    """{series: {stem: [(player_code, strike|None, ticker)]}} -- team codes NOT needed here; the player key is
    derived at match time using the resolved game's two team codes (so the index is self-contained)."""
    idx: dict = {}
    for t in tickers or []:
        p = parse_prop_ticker(t)
        if p is None:
            continue
        series, stem, pc, strike = p
        idx.setdefault(series, {}).setdefault(stem, []).append((pc, strike, t))
    return idx


def match_prop(parsed: dict, stem: str, team_a_code: str, team_b_code: str, prop_index: dict):
    """(ticker, leg, reason). reason is set ONLY on a miss (ticker None): unrecognised handled upstream. Misses:
    leg_unresolved | player_not_found | no_kalshi_strike:<rungs> | ambiguous_same_name. Exact-strike, code-bound."""
    if parsed is None:
        return (None, None, "not_a_prop")
    if parsed.get("leg") is None:
        return (None, None, "leg_unresolved:%r" % (parsed.get("player_slug"),))
    pkey = poly_player_key(parsed["player_slug"])
    if pkey is None:
        return (None, None, "poly_player_unparseable:%r" % parsed["player_slug"])
    entries = prop_index.get(parsed["series"], {}).get(stem, [])
    player_rungs = []          # every strike Kalshi lists for THIS player in THIS game (for the miss table)
    exact = []
    for pc, strike, t in entries:
        if kalshi_player_key(pc, team_a_code, team_b_code) == pkey:
            player_rungs.append(strike)
            if strike == parsed["strike"]:
                exact.append(t)
    if not player_rungs:
        return (None, None, "player_not_found")
    if not exact:
        return (None, None, "no_kalshi_strike:want=%s have=%s" % (parsed["strike"], sorted(r for r in player_rungs if r is not None)))
    if len(exact) > 1:
        return (None, None, "ambiguous_same_name:%d" % len(exact))
    return (exact[0], parsed["leg"], None)
