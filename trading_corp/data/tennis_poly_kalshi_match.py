"""Deterministic Polymarket-tennis -> Kalshi matcher (atp/wta match-winner scope).

PAIR-KEYED, not a UFC clone. The postponement probe (2026-09-03) found ~10% of matches differ
by +/-1 day between Poly and Kalshi (BOTH directions), so a (date, single-player) join misses
~10% and risks a wrong-day pick. This matcher keys on the PLAYER-PAIR from the Poly title
"A vs B" within a +/-1 day window: the pair uniquely identifies the match regardless of which
day each venue labels it, AND lets a bare-surname outcome ("Cerundolo") be resolved by the
opponent -- one construct for both the date tolerance and the surname recovery. Wrong-pick-safe:
both players must match + uniqueness; a same-surname pair in one match (Cerundolo brothers) stays
a safe miss.

Scope: MATCH WINNER (moneyline) only -- KXATPMATCH / KXWTAMATCH. No distance/set/game/futures.
Table-tennis series (KXTTMATCH/KXITTF*/KXWTTMATCH) are NEVER reachable: the ticker regex is
anchored to KX(ATP|WTA)MATCH, so a table-tennis ticker cannot enter the index (dedicated test).

Reuses the UFC name logic (accent-fold + first-name-prefix + uniqueness) verbatim -- NOT rebuilt.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field

# reuse the UFC name helpers verbatim (accent-fold, first-name-prefix match, date conversion)
from .ufc_poly_kalshi_match import _norm, kalshi_to_iso_date, match_fighter_name  # noqa: F401

WINDOW_DAYS = 1   # +/- date tolerance (all observed venue divergences were exactly +/-1 day)

# Poly slug: (atp|wta)-{codes}-YYYY-MM-DD[suffix].  A trailing suffix = a prop (out of scope).
_POLY_RE = re.compile(r"^(?P<tour>atp|wta)-(?P<codes>.+?)-(?P<date>\d{4}-\d{2}-\d{2})(?P<suffix>.*)$")
# Kalshi ticker: KX(ATP|WTA)MATCH-{YYMONDD}{BLOB6}-{CODE}. Anchored -> table-tennis can NEVER match.
_K_RE = re.compile(r"^KX(?:ATP|WTA)MATCH-(?P<date>\d{2}[A-Z]{3}\d{2})(?P<blob>[A-Z0-9]{6})-(?P<code>[A-Z0-9]+)$")

COPYABLE_MARKET_TYPES = ("moneyline",)


@dataclass(frozen=True)
class ParsedTennisBet:
    market_type: str            # 'moneyline' | 'non_tennis' | 'prop' | 'unparseable'
    date_iso: str | None
    outcome_name: str | None    # the player the whale bet (may be SURNAME-ONLY)
    player_a: str | None        # from the title "A vs B" (full names)
    player_b: str | None
    fail_reason: str | None = None
    raw: dict = field(default_factory=dict)


def parse_poly_tennis_bet(slug: str, outcome: str, title: str | None = None) -> ParsedTennisBet:
    """slug `(atp|wta)-{codes}-YYYY-MM-DD`; outcome = player name (full OR surname-only);
    title `... : A vs B` supplies the pair (both full names). No title -> pair is None (falls
    back to single-player match, which safely MISSES surname-only outcomes)."""
    raw = {"slug": slug, "outcome": outcome, "title": title}
    s = (slug or "")
    if not (s.startswith("atp-") or s.startswith("wta-")):
        return ParsedTennisBet("non_tennis", None, None, None, None, fail_reason="slug_not_tennis", raw=raw)
    m = _POLY_RE.match(s)
    if not m:
        return ParsedTennisBet("unparseable", None, None, None, None, fail_reason="slug_no_date", raw=raw)
    if m.group("suffix"):
        return ParsedTennisBet("prop", m.group("date"), None, None, None, raw=raw)
    pa = pb = None
    if title and " vs " in title:
        body = title.split(":", 1)[-1]
        parts = body.split(" vs ")
        if len(parts) == 2:
            pa, pb = parts[0].strip(), parts[1].strip()
    oc = (outcome or "").strip()
    return ParsedTennisBet("moneyline", m.group("date"), oc or None, pa, pb, raw=raw)


@dataclass(frozen=True)
class KalshiMatch:
    date_iso: str
    p_a_name: str
    p_b_name: str
    ticker_a: str
    ticker_b: str


def build_kalshi_match_index(markets: list[dict]) -> dict:
    """{date_iso: [KalshiMatch, ...]} from KX(ATP|WTA)MATCH markets. Each match = the 2 YES-side
    tickers sharing a (date, blob). Malformed / non-ATP-WTA-MATCH tickers are skipped -- so a
    table-tennis ticker CANNOT enter (the regex is anchored). A blob with != 2 sides is skipped."""
    by: dict = {}
    for mk in markets:
        tk = (mk.get("ticker") or "").strip()
        ti = (mk.get("title") or "").strip()
        m = _K_RE.match(tk)
        if not m or not ti.endswith(" wins"):
            continue
        nm = ti[:-len(" wins")].strip()
        if not nm:
            continue
        by.setdefault((m.group("date"), m.group("blob")), {})[m.group("code")] = (nm, tk)
    idx: dict = {}
    for (ds, _bl), sides in by.items():
        if len(sides) != 2:
            continue
        (na, ta), (nb, tb) = list(sides.values())
        d = kalshi_to_iso_date(ds)
        if not d:
            continue
        idx.setdefault(d, []).append(KalshiMatch(d, na, nb, ta, tb))
    return idx


@dataclass(frozen=True)
class MatchResult:
    status: str
    confidence: float
    kalshi_ticker: str | None = None
    reason: str | None = None
    leg: str | None = None
    market_type: str | None = None


def _surname(name: str) -> str:
    t = _norm(name).split()
    return t[-1] if t else ""


def _window(date_iso: str) -> list[str]:
    y, mo, d = (int(x) for x in date_iso.split("-"))
    base = _dt.date(y, mo, d)
    return [(base + _dt.timedelta(days=k)).isoformat() for k in range(-WINDOW_DAYS, WINDOW_DAYS + 1)]


def _resolve_side(outcome: str, km: KalshiMatch, pair_pinned: bool):
    """Pick which side the outcome names. Full/fuzzy name first; if that is inconclusive AND the
    match is already pinned by the pair, fall back to SURNAME (safe here -- the match is fixed, so
    surname just picks between two KNOWN players; a same-surname pair returns None = safe miss)."""
    a = match_fighter_name(outcome, km.p_a_name)
    b = match_fighter_name(outcome, km.p_b_name)
    if a and not b:
        return km.ticker_a
    if b and not a:
        return km.ticker_b
    if a and b:
        return None
    if pair_pinned:
        os_ = _surname(outcome); sa = _surname(km.p_a_name); sb = _surname(km.p_b_name)
        if os_ and os_ == sa and os_ != sb:
            return km.ticker_a
        if os_ and os_ == sb and os_ != sa:
            return km.ticker_b
    return None


def match_bet(parsed: ParsedTennisBet, match_index: dict, kalshi_dates, allowed_market_types=COPYABLE_MARKET_TYPES) -> MatchResult:
    mt = parsed.market_type
    if mt not in COPYABLE_MARKET_TYPES:
        if mt == "non_tennis":
            return MatchResult("skip_non_tennis", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
        if mt == "prop":
            return MatchResult("skip_prop", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
        return MatchResult("fail", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
    if mt not in allowed_market_types:
        return MatchResult("skip_market_type_excluded", 0.0, reason="moneyline_not_in_subdivision_market_types", market_type=mt)
    if not parsed.date_iso:
        return MatchResult("fail", 0.0, reason="no_date", market_type=mt)
    if not parsed.outcome_name:
        return MatchResult("fail", 0.0, reason="no_outcome", market_type=mt)

    cand: list[KalshiMatch] = []
    for d in _window(parsed.date_iso):
        cand.extend(match_index.get(d, []))
    if not cand:
        if parsed.date_iso not in kalshi_dates:
            return MatchResult("out_of_window", 0.0, reason="date_outside_kalshi_window", market_type=mt)
        return MatchResult("no_kalshi_contract", 0.0, reason="no_match_in_window", market_type=mt)

    pa, pb = parsed.player_a, parsed.player_b
    if pa and pb:
        paired = [km for km in cand if
                  (match_fighter_name(pa, km.p_a_name) and match_fighter_name(pb, km.p_b_name)) or
                  (match_fighter_name(pa, km.p_b_name) and match_fighter_name(pb, km.p_a_name))]
        # dedupe by match identity (same match can appear once per window-day only if re-listed; guard anyway)
        uniq = {(km.ticker_a, km.ticker_b): km for km in paired}
        if len(uniq) == 1:
            km = next(iter(uniq.values()))
            side = _resolve_side(parsed.outcome_name, km, pair_pinned=True)
            if side:
                return MatchResult("matched", 1.0, kalshi_ticker=side, leg="yes", reason="pair_resolved", market_type=mt)
            return MatchResult("winner_outcome_unresolved", 0.0, reason="outcome_not_either_player_or_same_surname", market_type=mt)
        if len(uniq) > 1:
            return MatchResult("abbrev_collision_ambiguous", 0.5, reason="pair_matches_multiple_in_window", market_type=mt)
        # pair given but not found -> fall through to single-player (no surname recovery -> safe)

    hits = [km for km in cand if match_fighter_name(parsed.outcome_name, km.p_a_name)
            or match_fighter_name(parsed.outcome_name, km.p_b_name)]
    uniq = {(km.ticker_a, km.ticker_b): km for km in hits}
    if len(uniq) == 1:
        km = next(iter(uniq.values()))
        side = _resolve_side(parsed.outcome_name, km, pair_pinned=False)
        if side:
            return MatchResult("matched", 1.0, kalshi_ticker=side, leg="yes", reason="single_resolved", market_type=mt)
        return MatchResult("winner_outcome_unresolved", 0.0, reason="side_unresolved", market_type=mt)
    if len(uniq) > 1:
        return MatchResult("abbrev_collision_ambiguous", 0.5, reason="outcome_matches_multiple", market_type=mt)
    return MatchResult("winner_outcome_unresolved", 0.0, reason="outcome_no_player_in_window", market_type=mt)
