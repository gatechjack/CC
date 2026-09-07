"""Deterministic Polymarket-CS2 -> Kalshi matcher (cs2 match/series-winner scope).

PAIR-KEYED like tennis, but the name join is EXACT-NORMALIZED, NEVER FUZZY. Esports org
display names are short and collide by design between a parent org and its academy / women's /
junior / regional roster:  ENCE vs ENCE Academy,  FURIA vs FURIA fe,  MOUZ vs MOUZ NXT,
G2 vs G2 Ares,  and every  ex-<Org>  (a disbanded lineup).  These are DISTINCT ENTITIES and a
fuzzy (substring / first-token / prefix) match would place a real order on the wrong team --
cs2's Cerundolo case. So the ONLY name equality here is: accent-fold + lowercase + strip
punctuation + collapse whitespace, then compare for EXACT equality (optionally through a small,
data-verified alias table for genuine cross-venue display diffs). "ENCE" != "ENCE Academy".

Join key (like tennis, for the same reason -- Asia-Pacific matches straddle UTC midnight so the
two venues can label a match +/-1 day apart):

  (match_date, frozenset{canon(org_a), canon(org_b)})  within a +/- 1 day window, uniqueness-guarded.

Both sides derive to org display names:
  * Kalshi:      title = "{Org} wins"  (KXCS2GAME; the two YES tickers share a (date, blob))
  * Polymarket:  title = "Counter-Strike: {A} vs {B} (BOn) - {event}", outcome = the org bet on.

Scope: MONEYLINE (match / series winner) ONLY.  An outcome that is not one of the two title
sides is a MAP-HANDICAP, a SPREAD ("Legacy (+1.5)"), or a TOTAL ("Games Total: O/U 2.5") -->
classified non_moneyline and NEVER placed (right-event-wrong-market-type is a stop, not a copy).
Map-winner markets (slug `...-gameN`, title "... - Map N Winner") are likewise out of scope --
they have no KXCS2GAME (whole-match) counterpart.

Reuses the UFC pure helpers (accent-fold `_norm`, `kalshi_to_iso_date`) verbatim -- the SAME
folding used live for ufc/tennis; folding only strips diacritics, it never merges two orgs.
Does NOT use `match_fighter_name` -- that is the fuzzy first-name-prefix logic, wrong for cs2.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field

# reuse UFC's accent-fold normaliser + date converter verbatim (pure, already live)
from .ufc_poly_kalshi_match import _norm, kalshi_to_iso_date  # noqa: F401

WINDOW_DAYS = 1  # +/- date tolerance (UTC-midnight straddle for AP matches; tennis-proven construct)

COPYABLE_MARKET_TYPES = ("moneyline",)

# Poly slug: cs2-{codes}-YYYY-MM-DD[-gameN].  A trailing suffix (-gameN) = a MAP bet (out of scope).
_POLY_RE = re.compile(r"^cs2-(?P<codes>.+?)-(?P<date>\d{4}-\d{2}-\d{2})(?P<suffix>.*)$")
# Kalshi ticker: KXCS2GAME-{YYMONDD}{HHMM}{BLOB}-{CODE}.  Anchored to KXCS2GAME.
_K_RE = re.compile(r"^KXCS2GAME-(?P<date>\d{2}[A-Z]{3}\d{2})(?P<blob>[A-Z0-9]+)-(?P<code>[A-Z0-9]+)$")

# ── Alias table (data-verified cross-venue display diffs ONLY) ───────────────────────────────
# normalized-Poly-name -> normalized-Kalshi-name.  Built from REAL two-venue names (the dry-run
# surfaces a candidate; it is added ONLY when the SAME opponent on the SAME date confirms the two
# names are one team).  Deliberately tiny: 322/514 real moneyline orgs already join with NO alias
# because both venues publish full display names.  Every entry here is a proven same-team rename,
# NOT a guess.  An academy/women's/junior/ex- variant is NEVER aliased to its parent.
#
# Each entry below is a same-team UNIFICATION verified against the 2026-09-06 dry-run: the org
# appears under two display forms across (and often within) the two venues, and the two forms are
# the SAME roster (formal-vs-short name, spacing, or a "Team"/"Esports" suffix).  Mapping every
# variant to one canonical form is safe because the key is the EXACT full normalized string -- an
# academy/junior/women's variant ("b8 academy") is a different string and is NEVER touched.
# Deliberately kept STABLE only: sponsor-prefix renames (e.g. "BET-M 33" -> "33") and a Kalshi-side
# encoding glitch ("Honved" storing as "honv d") are LEFT as accepted safe-misses (volatile /
# fragile) rather than aliased -- a miss is acceptable, a wrong pick is not.
CS2_ALIASES: dict[str, str] = {
    "b8 esports": "b8",              # Kalshi "B8"/"B8 Esports" (both) = Poly "B8"; "B8 Academy" stays distinct
    "betboom team": "betboom",       # Poly "BetBoom Team" = Kalshi "BetBoom"
    "themongolz": "the mongolz",     # Poly "TheMongolz" (concat) = Kalshi "The Mongolz"
    "liquid": "team liquid",         # Poly "Liquid" = Kalshi "Team Liquid" (uniquely one org)
    "sinners": "sinners esports",    # Poly "Sinners" = Kalshi "SINNERS Esports"
    "kaleido gaming": "kaleido",     # Poly "Kaleido Gaming" = Kalshi "Kaleido"
}


def _canon(name: str) -> str:
    """Exact-match key: accent-fold + lowercase + strip-punct + collapse ws, then alias.
    NEVER fuzzy. 'FURIA fe' -> 'furia fe' (stays distinct from 'furia')."""
    n = _norm(name or "")
    return CS2_ALIASES.get(n, n)


@dataclass(frozen=True)
class ParsedCs2Bet:
    market_type: str            # 'moneyline' | 'map' | 'non_moneyline' | 'non_cs2' | 'unparseable'
    date_iso: str | None
    outcome_name: str | None    # the org the whale bet
    org_a: str | None           # from the title "A vs B"
    org_b: str | None
    fail_reason: str | None = None
    raw: dict = field(default_factory=dict)


def _title_pair(title: str | None):
    """'Counter-Strike: A vs B (BO3) - event' -> ('A','B').  Map/total titles -> (None,None)."""
    if not title:
        return (None, None, "no_title")
    t = title
    if t.lower().startswith("counter-strike:"):
        t = t.split(":", 1)[1].strip()
    if re.search(r"-\s*Map\s+\d+\s+Winner", title, re.I):
        return (None, None, "map_title")
    pair = re.split(r"\s*\(BO", t, 1)[0].strip()
    pair = re.split(r"\s+-\s+", pair, 1)[0].strip()   # drop a trailing ' - event' if no (BOn)
    parts = re.split(r"\s+vs\.?\s+", pair, flags=re.I)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        return (None, None, "no_pair")
    return (parts[0].strip(), parts[1].strip(), None)


def parse_poly_cs2_bet(slug: str, outcome: str, title: str | None = None) -> ParsedCs2Bet:
    """Classify a Poly cs2 bet.  Moneyline REQUIRES: a match-winner slug (no -gameN), a parseable
    'A vs B' title, AND an outcome that EXACTLY (canon) equals one of the two sides.  Anything else
    is map / non_moneyline (handicap, spread, total) and is never placed."""
    raw = {"slug": slug, "outcome": outcome, "title": title}
    s = (slug or "")
    if not s.startswith("cs2-"):
        return ParsedCs2Bet("non_cs2", None, None, None, None, fail_reason="slug_not_cs2", raw=raw)
    m = _POLY_RE.match(s)
    if not m:
        return ParsedCs2Bet("unparseable", None, None, None, None, fail_reason="slug_no_date", raw=raw)
    if m.group("suffix"):
        return ParsedCs2Bet("map", m.group("date"), None, None, None, fail_reason="map_slug_suffix", raw=raw)
    a, b, why = _title_pair(title)
    if why == "map_title":   # a map-winner title without the -gameN slug suffix (defensive) -> still a map bet
        return ParsedCs2Bet("map", m.group("date"), None, None, None, fail_reason="map_title", raw=raw)
    if not a:
        return ParsedCs2Bet("non_moneyline", m.group("date"), (outcome or None), None, None,
                            fail_reason=why or "no_pair", raw=raw)
    oc = (outcome or "").strip()
    co = _canon(oc)
    if co and (co == _canon(a) or co == _canon(b)):
        return ParsedCs2Bet("moneyline", m.group("date"), oc, a, b, raw=raw)
    # a match title but the outcome is not one of the two teams -> handicap / spread / prop
    return ParsedCs2Bet("non_moneyline", m.group("date"), oc or None, a, b,
                        fail_reason="outcome_not_a_side", raw=raw)


@dataclass(frozen=True)
class KalshiCs2Match:
    date_iso: str
    org_a: str
    org_b: str
    ticker_a: str
    ticker_b: str


def build_kalshi_match_index(markets: list[dict]) -> dict:
    """{date_iso: [KalshiCs2Match, ...]} from KXCS2GAME markets. Each match = the two YES-side
    tickers sharing a (date, blob). Non-KXCS2GAME tickers cannot enter (anchored regex). A (date,
    blob) with != 2 sides, or the two sides canon-equal (a data glitch), is skipped."""
    by: dict = {}
    for mk in markets:
        tk = (mk.get("ticker") or "").strip()
        ti = (mk.get("title") or "").strip()
        # prefer the explicit yes_sub_title org name; fall back to 'title' minus ' wins'
        org = (mk.get("yes_sub_title") or mk.get("yes") or "").strip()
        m = _K_RE.match(tk)
        if not m:
            continue
        if not org:
            if ti.endswith(" wins"):
                org = ti[:-len(" wins")].strip()
        if not org:
            continue
        by.setdefault((m.group("date"), m.group("blob")), {})[m.group("code")] = (org, tk)
    idx: dict = {}
    for (ds, _bl), sides in by.items():
        if len(sides) != 2:
            continue
        (na, ta), (nb, tb) = list(sides.values())
        if _canon(na) == _canon(nb):
            continue
        d = kalshi_to_iso_date(ds)
        if not d:
            continue
        idx.setdefault(d, []).append(KalshiCs2Match(d, na, nb, ta, tb))
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
    base = _dt.date(y, mo, d)
    return [(base + _dt.timedelta(days=k)).isoformat() for k in range(-WINDOW_DAYS, WINDOW_DAYS + 1)]


def _resolve_side(outcome: str, km: KalshiCs2Match):
    """EXACT-canon side pick.  No fuzzy fallback: the outcome must canon-equal exactly ONE Kalshi
    side.  Academy/fe/ex- differences make the wrong side unequal -> a safe miss, never a mis-route."""
    co = _canon(outcome)
    a = (co == _canon(km.org_a))
    b = (co == _canon(km.org_b))
    if a and not b:
        return km.ticker_a
    if b and not a:
        return km.ticker_b
    return None


def match_bet(parsed: ParsedCs2Bet, match_index: dict, kalshi_dates,
              allowed_market_types=COPYABLE_MARKET_TYPES) -> MatchResult:
    mt = parsed.market_type
    if mt not in COPYABLE_MARKET_TYPES:
        if mt == "non_cs2":
            return MatchResult("skip_non_cs2", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
        if mt == "map":
            return MatchResult("skip_map", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
        if mt == "non_moneyline":
            return MatchResult("skip_non_moneyline", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
        return MatchResult("fail", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
    if mt not in allowed_market_types:
        return MatchResult("skip_market_type_excluded", 0.0,
                           reason="moneyline_not_in_subdivision_market_types", market_type=mt)
    if not parsed.date_iso:
        return MatchResult("fail", 0.0, reason="no_date", market_type=mt)
    if not parsed.outcome_name:
        return MatchResult("fail", 0.0, reason="no_outcome", market_type=mt)
    pa, pb = parsed.org_a, parsed.org_b
    if not (pa and pb):
        return MatchResult("fail", 0.0, reason="no_pair", market_type=mt)

    cand: list[KalshiCs2Match] = []
    for d in _window(parsed.date_iso):
        cand.extend(match_index.get(d, []))
    if not cand:
        if parsed.date_iso not in kalshi_dates:
            return MatchResult("out_of_window", 0.0, reason="date_outside_kalshi_window", market_type=mt)
        return MatchResult("no_kalshi_contract", 0.0, reason="no_match_in_window", market_type=mt)

    ca, cb = _canon(pa), _canon(pb)
    if ca == cb:
        return MatchResult("fail", 0.0, reason="degenerate_pair", market_type=mt)
    want = frozenset((ca, cb))
    paired = [km for km in cand if frozenset((_canon(km.org_a), _canon(km.org_b))) == want]
    uniq = {(km.ticker_a, km.ticker_b): km for km in paired}
    if len(uniq) == 1:
        km = next(iter(uniq.values()))
        side = _resolve_side(parsed.outcome_name, km)
        if side:
            return MatchResult("matched", 1.0, kalshi_ticker=side, leg="yes",
                               reason="pair_resolved", market_type=mt)
        return MatchResult("winner_outcome_unresolved", 0.0,
                           reason="outcome_not_either_org", market_type=mt)
    if len(uniq) > 1:
        return MatchResult("pair_collision_ambiguous", 0.5,
                           reason="pair_matches_multiple_in_window", market_type=mt)
    # pair not found in the window -> is EITHER org present at all (absence vs off-by-a-name)?
    present = {c for km in cand for c in (_canon(km.org_a), _canon(km.org_b))}
    if ca in present or cb in present:
        return MatchResult("no_kalshi_contract", 0.0, reason="one_org_present_other_absent", market_type=mt)
    return MatchResult("no_kalshi_contract", 0.0, reason="pair_absent_from_window", market_type=mt)
