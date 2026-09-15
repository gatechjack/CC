"""Deterministic Polymarket-F1 -> Kalshi matcher (Phase 2). RACE-WINNER scope built + provable;
HEAD-TO-HEAD is a board decision (KXF1H2H is EMPTY on Kalshi -> INCONCLUSIVE, see the deploy manifest).

Pure functions only (no network) — unit-testable and O(races_on_date) at match time. Models the
single-entity precedents (mlb `_match_rfi` one-binary-per-event, fed event+bucket) + the UFC/boxing
name bind, adapted to an F1 race that is a FIELD of ~20 per-driver binaries under one event.

────────────────────────────────────────────────────────────────────────────────
SCOPE — RACE WINNER only (behind the `race_winner` market_types token). NOT sprint-winner, NOT
pole/qualifying, NOT podium/fastest-lap/red-flag/winning-margin/constructor (all are Poly F1 props
we do not copy) and NOT the constructors' championship (KXF1CONSTRUCTORS, OUT).

Probed live 2026-09-14:
- Kalshi winner: `KXF1RACE-{COUNTRY}GP{YY}-{DRIVERCODE3}` per-driver binary, e.g. KXF1RACE-ITAGP26-VER,
  title "{Full Name} to finish in first", yes_sub_title "Max Verstappen", DRIVERCODE = 3-char (VER, GAS,
  ALO). event_ticker = KXF1RACE-{COUNTRY}GP{YY} (country+year, NO month/day). exchange_index = 0 (shard 0).
  22 open / 110 settled. ~20-22 drivers per race event.
- Poly winner sub-market: `f1-{gp}-grand-prix-winner-{driver}-{YYYY-MM-DD}` (a negRisk field of 28 per-driver
  Yes/No sub-markets), e.g. `f1-italian-grand-prix-winner-gasly-2026-09-06`, question "Will Pierre Gasly win
  the 2026 F1 Italian Grand Prix?", outcome "Yes"/"No". The DRIVER is in the slug (surname) AND the title
  (full name); the DATE is in the slug.

────────────────────────────────────────────────────────────────────────────────
THE JOIN — by DATE, not a GP-name->country-code map (a deliberate, evidence-backed choice)
────────────────────────────────────────────────────────────────────────────────
The Kalshi event_ticker carries only {COUNTRY}GP{YY} (no month/day), but each KXF1RACE market carries a
close_time whose date == the race date (verified: ITAGP26 close 2026-09-06 == Poly italian winner 2026-09-06;
DUTGP26 2026-08-23; BELGP26 2026-07-19; SPAGP26 close 2026-09-14 UTC == Poly spanish 2026-09-13 -> +1 UTC
boundary). F1 runs exactly ONE race per day, so the race date is a UNIQUE event key -> a date-join (with a
+/-1 day tolerance for the UTC race-end boundary) is unique and avoids the GP-name->country map entirely.
That map would be fragile precisely where it matters (Poly lists BOTH "spanish" and "catalunya" grands prix;
mapping each name to a Kalshi country code is guesswork). The date is in both feeds and never collides.

DRIVER bind: the driver full name from the title (preferred) or the surname from the slug, matched to the
Kalshi yes_sub_title. Unique within a race (F1 surnames are distinct). CODE-ANCHORED per market: the matched
ticker's OWN 3-char code must abbreviate the driver's surname, else refuse (a single-market mislabel guard;
there is no pair to swap in a field market, so the boxing/cs2 pairwise labels_code_swapped does not apply).
"""
from __future__ import annotations

import re
import datetime as _dt
from dataclasses import dataclass, field

from .ufc_poly_kalshi_match import _afold, _norm, kalshi_to_iso_date  # noqa: F401  (shared, stdlib-pure)

# Poly winner sub-market slug. The literal "-grand-prix-winner-" (NOT "-grand-prix-sprint-winner-",
# "-sprint-qualifying-pole-winner-", "-winning-margin-") is what distinguishes the race winner from the props.
_POLY_F1_WINNER_RE = re.compile(
    r"^f1-(?P<gp>.+?)-grand-prix-winner-(?P<driver>[a-z0-9]+)-(?P<date>\d{4}-\d{2}-\d{2})$"
)
# Title: "Will {Full Name} win the [2026 F1] {GP} Grand Prix?"
_F1_TITLE_RE = re.compile(r"^\s*will\s+(?P<driver>.+?)\s+win\s+the\s+.*grand\s+prix", re.I)
_NAME_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})

WINDOW_DAYS = 1                       # +/-1 day tolerance for the UTC race-end boundary (races are >= 1 week apart)
COPYABLE_MARKET_TYPES = ("race_winner",)


@dataclass(frozen=True)
class ParsedPolyBet:
    """Parsed Polymarket F1 bet. `non_f1` (wrong sport) / `non_winner` (an F1 prop we do not copy) /
    `unparseable` are labelled skips, never silent failures."""
    market_type: str          # "race_winner" | "non_f1" | "non_winner" | "unparseable"
    date_iso: str | None      # race date, YYYY-MM-DD (the slug's -YYYY-MM-DD segment)
    driver_slug: str | None   # driver surname token from the slug (always present for a winner slug)
    driver_full: str | None   # driver full name from the title (preferred bind; None if title absent/unparsed)
    leg: str | None           # "yes" | "no" — from the Yes/No outcome (a No-on-driver copies the Kalshi NO leg)
    fail_reason: str | None = None
    raw: dict = field(default_factory=dict)


def parse_poly_f1_bet(slug: str, outcome: str, title: str = None) -> ParsedPolyBet:
    raw = {"slug": slug, "outcome": outcome}
    s = (slug or "")
    if not s.startswith("f1-"):
        return ParsedPolyBet("non_f1", None, None, None, None,
                             fail_reason="slug_not_f1:%r" % slug, raw=raw)
    m = _POLY_F1_WINNER_RE.match(s)
    if not m:
        # an F1 slug that is NOT the race winner (sprint-winner / pole / podium / margin / constructor / rain prop)
        return ParsedPolyBet("non_winner", None, None, None, None,
                             fail_reason="f1_slug_not_race_winner:%r" % slug, raw=raw)
    oc = (outcome or "").strip().lower()
    leg = "yes" if oc == "yes" else "no" if oc == "no" else None
    tm = _F1_TITLE_RE.match(title or "")
    driver_full = tm.group("driver").strip() if tm else None
    if leg is None:
        return ParsedPolyBet("race_winner", m.group("date"), m.group("driver"), driver_full, None,
                             fail_reason="winner_outcome_not_yes_no:%r" % outcome, raw=raw)
    return ParsedPolyBet("race_winner", m.group("date"), m.group("driver"), driver_full, leg, raw=raw)


# ── Kalshi F1 race index ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class KalshiF1Driver:
    full_name: str
    code: str                 # the ticker's OWN 3-char code (VER, GAS)
    ticker: str


@dataclass(frozen=True)
class KalshiF1Race:
    date_iso: str
    event_ticker: str         # KXF1RACE-{COUNTRY}GP{YY}
    drivers: tuple            # tuple[KalshiF1Driver]


_KALSHI_F1_RE = re.compile(r"^KXF1RACE-(?P<event>[A-Z0-9]+)-(?P<driver>[A-Z0-9]+)$")


def _surname_tokens(name: str) -> list:
    toks = _norm(name).split()
    while len(toks) > 1 and toks[-1] in _NAME_SUFFIXES:
        toks = toks[:-1]
    return toks


def build_kalshi_f1_index(markets: list) -> dict:
    """{date_iso: KalshiF1Race} from KXF1RACE markets. `markets` = dicts with {ticker, yes_sub_title,
    close_date_iso} (close_date_iso = the UTC date of the market's close_time, the race date). Markets are
    grouped by event_ticker; each event's race_date is the modal close date of its drivers. One race per day,
    so keyed by date. Malformed tickers / no yes_sub_title / no close date are skipped."""
    by_event: dict = {}
    for mk in markets:
        ticker = (mk.get("ticker") or "").strip()
        m = _KALSHI_F1_RE.match(ticker)
        if not m:
            continue
        name = (mk.get("yes_sub_title") or "").strip()
        cd = (mk.get("close_date_iso") or "").strip()
        if not name or not cd:
            continue
        ev = by_event.setdefault(m.group("event"), {"date_votes": {}, "drivers": []})
        ev["date_votes"][cd] = ev["date_votes"].get(cd, 0) + 1
        ev["drivers"].append(KalshiF1Driver(full_name=name, code=m.group("driver"), ticker=ticker))

    index: dict = {}
    for event, ev in by_event.items():
        if not ev["drivers"] or not ev["date_votes"]:
            continue
        date_iso = max(ev["date_votes"].items(), key=lambda kv: kv[1])[0]   # modal close date = the race date
        # Two events resolving on the same modal date would collide (never happens for F1); keep the first, skip a dup.
        if date_iso in index:
            continue
        index[date_iso] = KalshiF1Race(date_iso=date_iso,
                                       event_ticker="KXF1RACE-" + event,
                                       drivers=tuple(ev["drivers"]))
    return index


# ── MatchResult ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class MatchResult:
    status: str               # matched | no_kalshi_contract | out_of_window | driver_ambiguous |
                              # winner_outcome_unresolved | skip_non_f1 | skip_non_winner |
                              # skip_market_type_excluded | fail
    confidence: float
    kalshi_ticker: str | None = None
    reason: str | None = None
    leg: str | None = None
    market_type: str | None = None


def _code_subsequences_surname(code: str, full_name: str) -> bool:
    """The ticker's OWN 3-char code must be an in-order subsequence of the driver's surname (folded) --
    a single-market mislabel guard (VER <= verstappen ; GAS <= gasly). Fail-closed: not a subsequence -> refuse."""
    c = re.sub(r"[^a-z0-9]", "", _afold(code or "").lower())
    toks = _surname_tokens(full_name)
    surname = toks[-1] if toks else ""
    i = 0
    for ch in surname:
        if i < len(c) and ch == c[i]:
            i += 1
    return bool(c) and i == len(c)


def _resolve_driver(parsed: ParsedPolyBet, race: KalshiF1Race):
    """Return (KalshiF1Driver, None) or (None, why). Bind by the SLUG SURNAME (always present), disambiguated
    by the TITLE full name if the surname matches >1 driver. F1 surnames are distinct, so a surname normally
    resolves uniquely; a genuine collision without a disambiguating title is a SAFE MISS (driver_ambiguous)."""
    surname = _surname_tokens(parsed.driver_slug or "")
    surname = surname[-1] if surname else ""
    by_surname = [d for d in race.drivers if (_surname_tokens(d.full_name)[-1:] or [""])[0] == surname] if surname else []
    cands = by_surname
    if len(cands) > 1 and parsed.driver_full:
        full = _surname_tokens(parsed.driver_full)
        refined = [d for d in cands if _surname_tokens(d.full_name) == full
                   or ((_surname_tokens(d.full_name)[-1:] or [""])[0] == (full[-1:] or [""])[0]
                       and (_surname_tokens(d.full_name)[:1] or [""])[0] == (full[:1] or [""])[0])]
        if len(refined) == 1:
            cands = refined
    if not cands and parsed.driver_full:                       # surname miss -> try the title full name directly
        full = _surname_tokens(parsed.driver_full)
        cands = [d for d in race.drivers if _surname_tokens(d.full_name) == full]
    if not cands:
        return None, "driver_not_in_race"
    if len(cands) > 1:
        return None, "driver_ambiguous"
    d = cands[0]
    if not _code_subsequences_surname(d.code, d.full_name):    # single-market mislabel guard -> refuse
        return None, "code_mislabel"
    return d, None


def _window(date_iso: str) -> list:
    y, mo, d = (int(x) for x in date_iso.split("-"))
    base = _dt.date(y, mo, d)
    return [(base + _dt.timedelta(days=k)).isoformat() for k in range(-WINDOW_DAYS, WINDOW_DAYS + 1)]


def match_bet(
    parsed: ParsedPolyBet,
    race_index: dict,
    kalshi_dates: frozenset,
    allowed_market_types: tuple = COPYABLE_MARKET_TYPES,
) -> MatchResult:
    mt = parsed.market_type
    if mt not in COPYABLE_MARKET_TYPES:
        if mt == "non_f1":
            return MatchResult("skip_non_f1", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
        if mt == "non_winner":
            return MatchResult("skip_non_winner", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
        return MatchResult("fail", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
    if mt not in allowed_market_types:
        return MatchResult("skip_market_type_excluded", 0.0,
                           reason="race_winner_not_in_subdivision_market_types", market_type=mt)
    if parsed.date_iso is None:
        return MatchResult("fail", 0.0, reason="no_date", market_type=mt)
    if parsed.leg is None:
        return MatchResult("fail", 0.0, reason=parsed.fail_reason or "winner_leg_missing", market_type=mt)

    races = [race_index[d] for d in _window(parsed.date_iso) if d in race_index]
    if not races:
        # a race exists in the window's dates within the fetched set? kalshi_dates is the index's dates.
        if not any(d in kalshi_dates for d in _window(parsed.date_iso)):
            return MatchResult("out_of_window", 0.0, reason="race_date_outside_kalshi_window", market_type=mt)
        return MatchResult("no_kalshi_contract", 0.0, reason="no_kxf1race_on_date", market_type=mt)
    if len(races) > 1:                                          # two races within +/-1 day (never for F1) -> safe miss
        return MatchResult("driver_ambiguous", 0.5, reason="multiple_races_in_window", market_type=mt)

    driver, why = _resolve_driver(parsed, races[0])
    if driver is None:
        if why == "driver_ambiguous":
            return MatchResult("driver_ambiguous", 0.5,
                               reason="driver_matches_multiple:%r" % (parsed.driver_slug,), market_type=mt)
        return MatchResult("winner_outcome_unresolved", 0.0,
                           reason="%s:%r" % (why, parsed.driver_full or parsed.driver_slug), market_type=mt)
    return MatchResult("matched", 1.0, kalshi_ticker=driver.ticker, leg=parsed.leg,
                       reason="race_driver_resolved", market_type="race_winner")
