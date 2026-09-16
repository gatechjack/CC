"""Deterministic Polymarket-ITF -> Kalshi matcher (ITF match-winner scope; men + women in ONE category).

CLONE of tennis_poly_kalshi_match.py (2026-09-16). ITF (International Tennis Federation) is the year-round
lower tier below ATP/WTA. Polymarket slugs BOTH the men's and women's ITF matches under a SINGLE `itf-`
prefix (`itf-{codes}-YYYY-MM-DD`), while Kalshi splits them into TWO series -- KXITFMATCH (men) and
KXITFWMATCH (women) -- both on shard 3. So ONE category `itf` with ONE matcher covers both series: the
Kalshi ticker regex admits KXITFMATCH *and* KXITFWMATCH, and the pair-keyed uniqueness makes a cross-series
same-surname collision a SAFE MISS (the same construct that keeps the Cerundolo brothers safe in tennis).

PAIR-KEYED, exactly like tennis (NOT a UFC clone): the ~+/-1 day Poly/Kalshi divergence and the surname-only
outcome recovery are both handled by keying on the PLAYER-PAIR from the Poly title "A vs B" within a +/-1 day
window. Wrong-pick-safe: both players must match + uniqueness; a same-surname pair stays a safe miss. ITF
players are lower-profile with MORE transliteration/accent variance than ATP/WTA -- the accent-fold + code
anchor (reused verbatim below) carry that load, and the pair requirement is the primary guard.

Scope: MATCH WINNER (moneyline) only -- KXITFMATCH / KXITFWMATCH. No distance/set/game/futures.
★ SEPARATION BY CONSTRUCTION from atp/wta: the Poly regex is anchored to the `itf-` prefix (an atp-/wta- slug
returns `non_itf`), and the Kalshi ticker regex is anchored to KX(ITFMATCH|ITFWMATCH) (a KXATPMATCH /
KXWTAMATCH ticker can NEVER enter the ITF index, and a KXITF* ticker can never enter the atp/wta index). The
two matchers share NO index and NO regex -- proven by a route-only test that fails if the anchors were shared.

Reuses the UFC name logic (accent-fold + first-name-prefix + uniqueness + code anchor) verbatim -- NOT rebuilt.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field

# reuse the UFC name helpers verbatim (accent-fold, first-name-prefix match, date conversion, code anchor)
from .ufc_poly_kalshi_match import _norm, kalshi_to_iso_date, match_fighter_name, _code_of, labels_code_swapped  # noqa: F401

WINDOW_DAYS = 1   # +/- date tolerance (matches the tennis Poly/Kalshi +/-1 day divergence)

# Poly slug: itf-{codes}-YYYY-MM-DD[suffix].  A trailing suffix = a prop (out of scope).
_POLY_RE = re.compile(r"^itf-(?P<codes>.+?)-(?P<date>\d{4}-\d{2}-\d{2})(?P<suffix>.*)$")
# Kalshi ticker: KX(ITFMATCH|ITFWMATCH)-{YYMONDD}{BLOB6}-{CODE}. Anchored -> atp/wta (and table-tennis) can NEVER match.
_K_RE = re.compile(r"^KX(?:ITFMATCH|ITFWMATCH)-(?P<date>\d{2}[A-Z]{3}\d{2})(?P<blob>[A-Z0-9]{6})-(?P<code>[A-Z0-9]+)$")

# ★ INERT-SHIP TOKEN (2026-09-16): the ITF match-winner is SEMANTICALLY a moneyline, but its copyable token is the
# DISTINCT `itf_moneyline`, NOT plain `moneyline`. Plain `moneyline` is in the legacy default (moneyline,total,spread)
# that a blank/NULL market_types row resolves to -- so if ITF reused it, a freshly-created ITF sub would auto-trade.
# A distinct token that is NOT in the legacy default keeps ITF airtight-INERT until Jack adds `itf_moneyline` to a
# sub's market_types (the RFI `first_inning_run` / F5 `f5` / F1 `race_winner` discipline). Ships OFF even with a sub.
COPYABLE_MARKET_TYPES = ("itf_moneyline",)


@dataclass(frozen=True)
class ParsedItfBet:
    market_type: str            # 'itf_moneyline' | 'non_itf' | 'prop' | 'unparseable'
    date_iso: str | None
    outcome_name: str | None    # the player the whale bet (may be SURNAME-ONLY)
    player_a: str | None        # from the title "A vs B" (full names)
    player_b: str | None
    fail_reason: str | None = None
    raw: dict = field(default_factory=dict)


def parse_poly_itf_bet(slug: str, outcome: str, title: str | None = None) -> ParsedItfBet:
    """slug `itf-{codes}-YYYY-MM-DD`; outcome = player name (full OR surname-only); title `... : A vs B`
    supplies the pair (both full names). No title -> pair is None (falls back to single-player match, which
    safely MISSES surname-only outcomes). A non-`itf-` slug is `non_itf` (an atp-/wta- slug never reaches
    the ITF index -- separation by construction)."""
    raw = {"slug": slug, "outcome": outcome, "title": title}
    s = (slug or "")
    if not s.startswith("itf-"):
        return ParsedItfBet("non_itf", None, None, None, None, fail_reason="slug_not_itf", raw=raw)
    m = _POLY_RE.match(s)
    if not m:
        return ParsedItfBet("unparseable", None, None, None, None, fail_reason="slug_no_date", raw=raw)
    if m.group("suffix"):
        return ParsedItfBet("prop", m.group("date"), None, None, None, raw=raw)
    pa = pb = None
    if title and " vs " in title:
        body = title.split(":", 1)[-1]
        parts = body.split(" vs ")
        if len(parts) == 2:
            pa, pb = parts[0].strip(), parts[1].strip()
    oc = (outcome or "").strip()
    return ParsedItfBet("itf_moneyline", m.group("date"), oc or None, pa, pb, raw=raw)


@dataclass(frozen=True)
class KalshiItfMatch:
    date_iso: str
    p_a_name: str
    p_b_name: str
    ticker_a: str
    ticker_b: str


def build_kalshi_itf_index(markets: list[dict]) -> dict:
    """{date_iso: [KalshiItfMatch, ...]} from KX(ITFMATCH|ITFWMATCH) markets (men AND women merged). Each
    match = the 2 YES-side tickers sharing a (date, blob). Malformed / non-ITF-MATCH tickers are skipped --
    so a KXATPMATCH / KXWTAMATCH / table-tennis ticker CANNOT enter (the regex is anchored). A blob with
    != 2 sides is skipped. Merging the two series is safe: a (date, blob) is unique per Kalshi match, so a
    men's and a women's match never collide on the same key."""
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
        idx.setdefault(d, []).append(KalshiItfMatch(d, na, nb, ta, tb))
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


def _resolve_side(outcome: str, km: KalshiItfMatch, pair_pinned: bool):
    """Pick which side the outcome names. Full/fuzzy name first; if that is inconclusive AND the match is
    already pinned by the pair, fall back to SURNAME (safe here -- the match is fixed, so surname just picks
    between two KNOWN players; a same-surname pair returns None = safe miss).
    ★ CODE-ANCHORED: after the label pick, refuse if the ticker's -CODE says the player belongs to the OTHER
    side (a Kalshi title/code mislabel) -> safe miss. This is the cs2 opponent-buy fix, reused verbatim."""
    if labels_code_swapped(_code_of(km.ticker_a), km.p_a_name, _code_of(km.ticker_b), km.p_b_name):
        return None                                          # (code,name) swapped onto wrong tickers -> safe miss
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


def match_bet(parsed: ParsedItfBet, match_index: dict, kalshi_dates, allowed_market_types=COPYABLE_MARKET_TYPES) -> MatchResult:
    mt = parsed.market_type
    if mt not in COPYABLE_MARKET_TYPES:
        if mt == "non_itf":
            return MatchResult("skip_non_itf", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
        if mt == "prop":
            return MatchResult("skip_prop", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
        return MatchResult("fail", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
    if mt not in allowed_market_types:
        return MatchResult("skip_market_type_excluded", 0.0, reason="itf_moneyline_not_in_subdivision_market_types", market_type=mt)
    if not parsed.date_iso:
        return MatchResult("fail", 0.0, reason="no_date", market_type=mt)
    if not parsed.outcome_name:
        return MatchResult("fail", 0.0, reason="no_outcome", market_type=mt)

    cand: list[KalshiItfMatch] = []
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
