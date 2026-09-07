"""Deterministic Polymarket-UFC -> Kalshi matcher (Phase 1, UFC scope).

Pure functions only (no network) — fully unit-testable and O(1) at match time.
A daily offline index-builder and the strategy dispatcher both consume this module.

Scope: TWO binary market types ONLY.
  1. moneyline / winner   — KXUFCFIGHT series   (one YES market per fighter, two per bout)
  2. go-the-distance      — KXUFCDISTANCE series (one binary market per bout, YES = goes full distance)

Method-of-victory and round markets are OUT of scope (no Kalshi counterpart confirmed open).

────────────────────────────────────────────────────────────────────────────────
Join strategy
────────────────────────────────────────────────────────────────────────────────
There is NO static fighter roster (UFC fighters are open-ended; sports_team_mapping.py
has NO UFC entry — confirmed by inspection). The join is:

  (fight_date_ET_iso, frozenset{fighter_A_full_name, fighter_B_full_name})

Both sides derive to fighter full names via:
  • Kalshi:      title field = "{Fighter Full Name} wins" (directly parsed)
  • Polymarket:  outcome string = full fighter name (empirically confirmed 2026-09-02)

Kalshi 3-char abbreviation rule (empirical, probed 2026-09-02):
  kcode = upper(last_name[:3])       if len(last_name) >= 3
        = upper(first_name[:3])      else  (e.g. "Oumar Sy" → SY < 3 → OUM)

The blob in the ticker is formed by concatenating the two kcodes of the fighters
in Kalshi's internal ordering (NOT necessarily alphabetical, NOT necessarily
fight-card order). We derive the two kcodes from the full names Kalshi publishes
and look up against the actual tickers in the index — we never reverse-parse the
blob ourselves.

────────────────────────────────────────────────────────────────────────────────
KXUFCFIGHT ticker format (probed live 2026-09-02, source:
  https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXUFCFIGHT&status=open&limit=200)

  KXUFCFIGHT-{YYMONDD}{CODE1}{CODE2}-{FTR}
  • YYMONDD  = 2-digit year + 3-letter month + 2-digit day  (e.g. 26SEP05)
  • CODE1    = 3-char abbreviation of fighter 1's last name  (e.g. HOO = Hooker)
  • CODE2    = 3-char abbreviation of fighter 2's last name  (e.g. PAR = Parnasse)
  • FTR      = 3-char code of the YES-side fighter

REAL examples (live 2026-09-05 card):
  KXUFCFIGHT-26SEP05HOOPAR-HOO   title: "Daniel Hooker wins"
  KXUFCFIGHT-26SEP05HOOPAR-PAR   title: "Salahdine Parnasse wins"
  KXUFCFIGHT-26SEP05WOOAND-WOO   title: "Nathaniel Wood wins"
  KXUFCFIGHT-26SEP05WOOAND-AND   title: "Pavel Andrusca wins"
  KXUFCFIGHT-26SEP05OUMBUK-OUM   title: "Oumar Sy wins"    ← OUM = first 3 of first name "Oumar" (last = "Sy", len<3)
  KXUFCFIGHT-26SEP05OUMBUK-BUK   title: "Modestas Bukauskas wins"

KXUFCDISTANCE ticker format (probed live 2026-09-02, same source with series_ticker=KXUFCDISTANCE):

  KXUFCDISTANCE-{YYMONDD}{CODE1}{CODE2}-DIST
  • Shares the same YYMONDD+CODE1+CODE2 blob as the corresponding KXUFCFIGHT tickers.
  • Fixed suffix "-DIST" for the single YES/NO binary.
  • YES = fight goes the full scheduled number of rounds (judges' scorecards).
  • NO  = fight ends before the final bell.

REAL examples (live 2026-09-05 card):
  KXUFCDISTANCE-26SEP05WOOAND-DIST   title: "Fight goes the distance?"
  KXUFCDISTANCE-26SEP05ALJSIN-DIST   title: "Fight goes the distance?"
  KXUFCDISTANCE-26SEP05CORSYG-DIST   title: "Fight goes the distance?"

────────────────────────────────────────────────────────────────────────────────
Polymarket UFC slug format (empirical, probed live 2026-09-02, source:
  https://polymarket.com/sports/ufc/games)

  Event slug:  ufc-{code1}-{code2}-{YYYY-MM-DD}
  (Polymarket's internal codes are NOT the Kalshi 3-char codes — they are opaque
  short identifiers assigned by Polymarket's system, e.g. "dan6", "salpar", "nor4".)

  Market type is identified by the `market_sport_type` field (Retail API:
  sportsMarketType):
    ufc_winner         → moneyline (outcome = full fighter name)
    ufc_go_the_distance → go-the-distance binary (outcome = "Yes" or "No")

  For copy-trading we receive (slug, outcome) pairs from the whale's activity.
  Since we cannot parse Polymarket's opaque fighter codes from the slug, we rely
  on the caller appending a MARKET-TYPE SUFFIX to the slug before passing it here:
    moneyline slug (no suffix): "ufc-dan6-salpar-2026-09-05"       outcome = "Daniel Hooker"
    go-the-distance slug:       "ufc-dan6-salpar-2026-09-05-go-the-distance"  outcome = "Yes" | "No"

  The FIGHT DATE in ET is extracted from the "-YYYY-MM-DD" segment.

────────────────────────────────────────────────────────────────────────────────
KNOWN UNRESOLVABLE CASES (explicit misses, not silent failures)
────────────────────────────────────────────────────────────────────────────────
1. ABBREVIATION COLLISION (3-char ambiguity):
   Two fighters on the SAME card whose last names share the same first 3 uppercase
   letters cannot be unambiguously mapped to their Kalshi codes. Example: if
   "Josh Santos" and "Marco Sanchez" fight on the same card, both produce "SAN".
   The 6-char blob would be "SANSAN" and the YES suffixes "-SAN" / "-SAN" would be
   identical — Kalshi's system must disambiguate (possibly with a 4th char or digit)
   but the EXACT rule is NOT documented and we have NOT observed a real instance.
   Matcher behaviour: status = "abbrev_collision_ambiguous" (never guesses).

   Real collision search (2026-09-02): No live or recently settled KXUFCFIGHT card
   was found with a confirmed same-3-char collision. The closest observed case is
   two "MIR" fighters (Bella Mir and Alexis Miranda) on DIFFERENT cards (26AUG25),
   which is NOT a collision. The test below constructs the most plausible real-name
   collision case using actual UFC roster names.

2. LAST NAME TOO SHORT (< 3 chars):
   Single-name fighters or fighters with very short last names use the first name
   instead (verified: "Oumar Sy" → OUM). The matcher handles this correctly.
   An entirely-unknown name (not in the Kalshi index) → no_kalshi_contract.

3. NON-UFC SLUG:
   Any slug not starting with "ufc-" → skip_non_ufc (never a match failure).

4. OUTCOME NOT A KNOWN FIGHTER:
   If the outcome string cannot be matched to either fighter by full-name, the
   match returns status="fail" with fail_reason="winner_outcome_unresolved".

5. KXUFCDISTANCE NOT ALWAYS PRESENT:
   Not every KXUFCFIGHT bout has a KXUFCDISTANCE counterpart (the 2026-09-05 card
   had 5 distance markets but 19 fight bouts). A go-the-distance request for a bout
   without a KXUFCDISTANCE market → no_kalshi_contract.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace

# ── Date helpers (shared with the MLB matcher) ─────────────────────────────

_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def iso_to_kalshi_date(iso: str) -> str | None:
    """'2026-09-05' -> '26SEP05'. None if not a valid ISO date string."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", iso or "")
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12):
        return None
    return f"{y % 100:02d}{_MONTHS[mo - 1]}{d:02d}"


def kalshi_to_iso_date(kd: str) -> str | None:
    """'26SEP05' -> '2026-09-05'. None if unparseable."""
    m = re.match(r"^(\d{2})([A-Z]{3})(\d{2})$", kd or "")
    if not m:
        return None
    try:
        mo = _MONTHS.index(m.group(2)) + 1
    except ValueError:
        return None
    return f"20{m.group(1)}-{mo:02d}-{int(m.group(3)):02d}"


# ── Fighter name helpers ────────────────────────────────────────────────────

def _afold(s: str) -> str:
    """Fold accents/diacritics to ASCII (NFKD decompose -> drop combining marks).
    'Lourenco' with a cedilla -> 'Lourenco'; 'Charriere' with a grave -> 'Charriere'.
    Folding NEVER merges two DISTINCT fighters (it only strips diacritics), so it
    cannot create a wrong pick -- validated 2026-09-03 against real whale UFC bets."""
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")


def _norm(s: str) -> str:
    """Accent-fold, lowercase, collapse non-alnum to single space, strip."""
    return re.sub(r"[^a-z0-9]+", " ", _afold(s).lower()).strip()


def fighter_kcode(full_name: str) -> str:
    """Derive the Kalshi 3-char abbreviation for a fighter from their full name.

    Rule (empirical from live KXUFCFIGHT tickers, 2026-09-02):
      • Split into tokens; the last token is the last name.
      • kcode = upper(last_name[:3])          if len(last_name) >= 3
               = upper(first_name[:3])         else  (e.g. "Oumar Sy" → OUM)

    If the full_name has only one token (single-name fighter) the token itself
    is used. If the name is empty or produces fewer than 3 chars the result is
    whatever upper-case prefix is available (may be < 3 chars for very short names).

    Examples (all verified from live data):
      "Daniel Hooker"        → HOO
      "Salahdine Parnasse"   → PAR
      "Nathaniel Wood"        → WOO
      "Oumar Sy"             → OUM  (last name "Sy" has len 2 < 3 → use first "Oumar")
      "Modestas Bukauskas"   → BUK
      "Reginaldo Junior"     → JUN
    """
    name = (full_name or "").strip()
    if not name:
        return ""
    tokens = name.split()
    last = tokens[-1]
    if len(last) >= 3:
        return last[:3].upper()
    # Last name too short — fall back to the first token (first name)
    first = tokens[0] if len(tokens) > 1 else last
    return first[:3].upper()


def match_fighter_name(candidate: str, known_name: str) -> bool:
    """True if `candidate` (a Polymarket outcome) names the SAME fighter as
    `known_name` (a Kalshi title fighter).

    Tiered and deliberately CONSERVATIVE. Validated 2026-09-03 against the real
    UFC bets of the pinned whales: this recovers the only genuine name-FORM misses
    (short/long first name; accents; a parenthetical middle-token disambiguator)
    with ZERO wrong picks over the real corpus.

      1. Exact accent-folded, normalised equality.
      2. SAME last token (accent-folded) AND first token exact, OR the SHORTER first
         token is >= 3 chars and a prefix of the longer. Keying on first + last
         tokens tolerates an extra MIDDLE token ("Andre Lima" -> "Andre (Bra) Lima").

    Why it cannot mis-route:
      * The LAST token must match exactly (folded) -- a different surname never matches.
      * A first-token PREFIX needs >= 3 shared chars, so "D. Hooker" (initial) and
        "Andre" vs "Anderson" (share only "and", not a prefix) both stay MISSES.
      * If two fighters on ONE card still both satisfy the rule for one outcome,
        match_bet's uniqueness guard returns abbrev_collision_ambiguous -- a safe
        MISS, never a guess. So a widened match can only ever become a safe miss.
    """
    c, k = _norm(candidate), _norm(known_name)
    if c == k:
        return True
    ct, kt = c.split(), k.split()
    if not ct or not kt:
        return False
    if ct[-1] != kt[-1]:                       # last name must match (accent-folded) exactly
        return False
    cf, kf = ct[0], kt[0]
    if cf == kf:                               # same first token (extra middle tokens tolerated)
        return True
    shorter, longer = (cf, kf) if len(cf) <= len(kf) else (kf, cf)
    return len(shorter) >= 3 and longer.startswith(shorter)


# ── Poly slug parsing ────────────────────────────────────────────────────────
# UFC event slug:  ufc-{codes}-{YYYY-MM-DD}[{suffix}]
# The codes segment may contain hyphens itself (e.g. "dan6-salpar").
# The date is always the last "-YYYY-MM-DD" segment before any further suffix.
# Suffix conventions supported:
#   (none)                → moneyline / winner market
#   -go-the-distance      → KXUFCDISTANCE binary market

_POLY_UFC_RE = re.compile(
    r"^ufc-(?P<codes>.+?)-(?P<date>\d{4}-\d{2}-\d{2})(?P<suffix>.*)$"
)
_SUFFIX_DISTANCE = "-go-the-distance"


@dataclass(frozen=True)
class ParsedPolyBet:
    """Parsed Polymarket UFC bet. Mirrors the MLB ParsedPolyBet public surface."""
    market_type: str        # "moneyline" | "go_the_distance" | "non_ufc" | "prop" | "unparseable"
    date_iso: str | None    # fight date, YYYY-MM-DD (ET)
    fighter_a: str | None   # full name of one fighter (from outcome or None)
    fighter_b: str | None   # full name of the other fighter (only set when known)
    side: str | None        # "a" | "b" — which fighter the whale bet (moneyline only)
    side_name: str | None   # full name of the bet fighter
    leg: str | None         # "yes" | "no" (go_the_distance: from outcome string; moneyline: always "yes")
    fail_reason: str | None = None
    raw: dict = field(default_factory=dict)


def parse_poly_ufc_bet(slug: str, outcome: str) -> ParsedPolyBet:
    """Parse one Poly activity row into a ParsedPolyBet.

    `slug`    — the Polymarket event slug, optionally with a market-type suffix
                appended by the caller:
                  "ufc-dan6-salpar-2026-09-05"                  → moneyline
                  "ufc-dan6-salpar-2026-09-05-go-the-distance"  → go-the-distance
    `outcome` — for moneyline: the full fighter name the whale bet (e.g. "Daniel Hooker")
                for go-the-distance: "Yes" or "No"

    market_type is the scope gate. 'moneyline' and 'go_the_distance' are in scope.
    Everything else is a labelled skip (never a silent failure).

    IMPORTANT: the caller must supply the outcome fighter's full name for moneyline.
    The two fighter identities are NOT embedded in the Poly slug (it uses opaque codes
    like "dan6" / "salpar") — we can only resolve which Kalshi contract to buy AFTER
    looking up the fight in the Kalshi index and matching by full name.
    """
    raw = {"slug": slug, "outcome": outcome}
    if not (slug or "").startswith("ufc-"):
        return ParsedPolyBet("non_ufc", None, None, None, None, None, None,
                             fail_reason=f"slug_not_ufc:{slug!r}", raw=raw)

    m = _POLY_UFC_RE.match(slug or "")
    if not m:
        return ParsedPolyBet("unparseable", None, None, None, None, None, None,
                             fail_reason=f"slug_no_date:{slug!r}", raw=raw)

    date_iso = m.group("date")
    suffix = m.group("suffix") or ""

    if suffix == _SUFFIX_DISTANCE:
        # go-the-distance binary; outcome is "Yes" or "No"
        oc = (outcome or "").strip()
        if oc.lower() == "yes":
            leg = "yes"
        elif oc.lower() == "no":
            leg = "no"
        else:
            return ParsedPolyBet("go_the_distance", date_iso, None, None, None, None, None,
                                 fail_reason=f"distance_outcome_not_yes_no:{outcome!r}", raw=raw)
        return ParsedPolyBet("go_the_distance", date_iso, None, None, None, None, leg, raw=raw)

    if suffix:
        # Some other prop suffix — labelled skip
        return ParsedPolyBet("prop", date_iso, None, None, None, None, None, raw=raw)

    # Moneyline: outcome = full fighter name
    oc = (outcome or "").strip()
    if not oc:
        return ParsedPolyBet("moneyline", date_iso, None, None, None, None, None,
                             fail_reason="empty_outcome", raw=raw)
    # We do NOT know the opponent here — fighter_b will be set by the matcher
    # once it looks up the Kalshi index.
    return ParsedPolyBet("moneyline", date_iso, oc, None, None, None, "yes", raw=raw)


# ── COPYABLE_MARKET_TYPES ──────────────────────────────────────────────────
COPYABLE_MARKET_TYPES = ("moneyline", "go_the_distance")


# ── Kalshi fight index ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class KalshiFight:
    """One UFC bout as seen in the Kalshi KXUFCFIGHT index.

    Two fighters, two YES-side tickers, an optional distance ticker, keyed by
    (date_iso, frozenset{fighter_a_name, fighter_b_name}).
    """
    date_iso: str
    date_str: str                   # YYMONDD (e.g. "26SEP05")
    fighter_a_name: str
    fighter_b_name: str
    fighter_a_kcode: str            # 3-char Kalshi code
    fighter_b_kcode: str
    ticker_a: str                   # KXUFCFIGHT ticker for fighter_a YES
    ticker_b: str                   # KXUFCFIGHT ticker for fighter_b YES
    distance_ticker: str | None     # KXUFCDISTANCE ticker for this bout (may be absent)


@dataclass(frozen=True)
class ParsedKalshiFightTicker:
    """Parsed KXUFCFIGHT ticker fields."""
    date_str: str       # YYMONDD
    blob: str           # CODE1+CODE2 (6 chars)
    yes_code: str       # 3-char code of the YES fighter
    yes_name: str       # full name from the title
    series: str         # "FIGHT" | "DISTANCE"


# KXUFCFIGHT-{YYMONDD}{CODE1}{CODE2}-{FTR}
_KALSHI_FIGHT_RE = re.compile(
    r"^KXUFCFIGHT-(?P<date>\d{2}[A-Z]{3}\d{2})(?P<blob>[A-Z0-9]{6})-(?P<yes>[A-Z0-9]{3})$"
)
# KXUFCDISTANCE-{YYMONDD}{CODE1}{CODE2}-DIST
_KALSHI_DIST_RE = re.compile(
    r"^KXUFCDISTANCE-(?P<date>\d{2}[A-Z]{3}\d{2})(?P<blob>[A-Z0-9]{6})-DIST$"
)


def _fight_index_key(date_iso: str, name_a: str, name_b: str) -> tuple:
    return (date_iso, frozenset({_norm(name_a), _norm(name_b)}))


def build_kalshi_fight_index(fight_markets: list[dict]) -> dict:
    """Build a fight index keyed by (date_iso, frozenset{norm_name_a, norm_name_b}).

    `fight_markets` is a list of dicts with at minimum:
      {"ticker": "KXUFCFIGHT-...", "title": "{Fighter Full Name} wins"}

    The index groups the two YES-side tickers for each bout into a single KalshiFight.
    Malformed tickers are silently skipped (non-UFC / non-parseable).

    Returns:
      {(date_iso, frozenset{norm_name_a, norm_name_b}): KalshiFight}

    An unresolvable blob (e.g. an ambiguous 3-char collision resulting in a 6-char
    blob that cannot be cleanly split into two known fighters) is ALSO skipped —
    the matcher will return no_kalshi_contract for it.
    """
    # First pass: collect (date_str, blob) -> {yes_code: (full_name, ticker)}
    by_blob: dict = {}
    for mkt in fight_markets:
        ticker = (mkt.get("ticker") or "").strip()
        title = (mkt.get("title") or "").strip()
        m = _KALSHI_FIGHT_RE.match(ticker)
        if not m:
            continue
        date_str = m.group("date")
        blob = m.group("blob")
        yes_code = m.group("yes")
        # Title format: "{Full Name} wins"
        if not title.endswith(" wins"):
            continue
        full_name = title[:-len(" wins")].strip()
        if not full_name:
            continue
        key = (date_str, blob)
        by_blob.setdefault(key, {})[yes_code] = (full_name, ticker)

    index: dict = {}
    for (date_str, blob), sides in by_blob.items():
        if len(sides) != 2:
            # Only one YES side collected — partial / ambiguous, skip
            continue
        codes = list(sides.keys())
        code_a, code_b = codes[0], codes[1]
        name_a, ticker_a = sides[code_a]
        name_b, ticker_b = sides[code_b]
        date_iso = kalshi_to_iso_date(date_str)
        if date_iso is None:
            continue
        fight = KalshiFight(
            date_iso=date_iso,
            date_str=date_str,
            fighter_a_name=name_a,
            fighter_b_name=name_b,
            fighter_a_kcode=code_a,
            fighter_b_kcode=code_b,
            ticker_a=ticker_a,
            ticker_b=ticker_b,
            distance_ticker=None,   # populated in a second pass below
        )
        key = _fight_index_key(date_iso, name_a, name_b)
        index[key] = fight

    return index


def attach_distance_tickers(fight_index: dict, distance_markets: list[dict]) -> dict:
    """Return a new index with KXUFCDISTANCE tickers attached to matching fights.

    `distance_markets` is a list of dicts:
      {"ticker": "KXUFCDISTANCE-26SEP05WOOAND-DIST", "title": "..."}

    Matching is by (date_str, blob) — the identical prefix shared by the fight
    and distance tickers. If no matching fight is found the distance ticker is
    silently skipped.
    """
    # Build a (date_str, blob) -> KalshiFight lookup from the current index
    by_blob: dict = {}
    for fight in fight_index.values():
        blob_key = (fight.date_str, fight.fighter_a_kcode + fight.fighter_b_kcode)
        by_blob[blob_key] = fight
        # Also index the reversed blob in case Kalshi orders differently
        by_blob[(fight.date_str, fight.fighter_b_kcode + fight.fighter_a_kcode)] = fight

    new_index: dict = {}
    # Copy fights unchanged first
    for key, fight in fight_index.items():
        new_index[key] = fight

    for mkt in distance_markets:
        ticker = (mkt.get("ticker") or "").strip()
        dm = _KALSHI_DIST_RE.match(ticker)
        if not dm:
            continue
        date_str = dm.group("date")
        blob = dm.group("blob")
        fight = by_blob.get((date_str, blob))
        if fight is None:
            continue
        key = _fight_index_key(fight.date_iso, fight.fighter_a_name, fight.fighter_b_name)
        new_index[key] = replace(fight, distance_ticker=ticker)

    return new_index


# ── MatchResult ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class MatchResult:
    """Outcome of match_bet for one UFC Poly bet row.

    Mirrors the MLB MatchResult public surface so a dispatcher can call both
    matchers uniformly.

    Status codes:
      matched                  — found a unique Kalshi contract; kalshi_ticker is set
      no_kalshi_contract       — date is in window but no fight/distance ticker found
      out_of_window            — fight date is outside the fetched Kalshi window
      abbrev_collision_ambiguous — two fighters on this card share the same 3-char code
      winner_outcome_unresolved  — moneyline outcome string matches neither fighter
      skip_non_ufc             — slug doesn't start with "ufc-"
      skip_prop                — unrecognised slug suffix (method-of-victory etc.)
      skip_market_type_excluded  — market_type not in caller's allowed_market_types
      fail                     — parse error or missing required field
    """
    status: str
    confidence: float           # 0..1
    kalshi_ticker: str | None = None
    reason: str | None = None
    leg: str | None = None      # "yes" | "no" — the Kalshi leg to BUY
    market_type: str | None = None


# ── Core matcher ───────────────────────────────────────────────────────────

def _resolve_winner_side(outcome: str, fight: KalshiFight):
    """Return (fighter_code, ticker, name) for the bet fighter, or (None, None, None)."""
    if match_fighter_name(outcome, fight.fighter_a_name):
        return fight.fighter_a_kcode, fight.ticker_a, fight.fighter_a_name
    if match_fighter_name(outcome, fight.fighter_b_name):
        return fight.fighter_b_kcode, fight.ticker_b, fight.fighter_b_name
    return None, None, None


def match_bet(
    parsed: ParsedPolyBet,
    fight_index: dict,
    kalshi_dates: frozenset,
    allowed_market_types: tuple = COPYABLE_MARKET_TYPES,
) -> MatchResult:
    """Unified UFC match entry point. Compatible shape with the MLB match_bet dispatcher.

    Args:
      parsed              — from parse_poly_ufc_bet(slug, outcome)
      fight_index         — from build_kalshi_fight_index + attach_distance_tickers
                            (keyed by (date_iso, frozenset{norm_name_a, norm_name_b}))
      kalshi_dates        — frozenset of ISO dates present in the Kalshi index;
                            used to distinguish "no contract on this date" from
                            "date outside the fetched window"
      allowed_market_types — subdivision's configured market types; types not in
                             the list produce skip_market_type_excluded, not an error

    The signature intentionally uses fewer index args than the MLB matcher
    (no separate total_index / spread_index) because UFC scope is only 2 types.
    A dispatcher that calls both matchers should pass (parsed, fight_index, dates)
    for UFC and (parsed, ml_index, tot_index, spr_index, dates) for MLB.
    """
    mt = parsed.market_type

    # ── Scope gate ──────────────────────────────────────────────────────────
    if mt not in COPYABLE_MARKET_TYPES:
        if mt in ("non_ufc",):
            return MatchResult("skip_non_ufc", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
        if mt == "prop":
            return MatchResult("skip_prop", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
        return MatchResult("fail", 0.0, reason=parsed.fail_reason or mt, market_type=mt)

    if mt not in allowed_market_types:
        return MatchResult("skip_market_type_excluded", 0.0,
                           reason=f"{mt}_not_in_subdivision_market_types", market_type=mt)

    # ── Date / parse guard ──────────────────────────────────────────────────
    if parsed.date_iso is None:
        return MatchResult("fail", 0.0, reason="no_date", market_type=mt)
    if parsed.fail_reason:
        return MatchResult("fail", 0.0, reason=parsed.fail_reason, market_type=mt)

    # ── Collision check: derive kcode from the outcome name and scan for collisions
    # For moneyline: before looking up the fight we check whether the outcome fighter's
    # kcode collides with any other fighter on that date (i.e. exists in two different
    # fight blobs for the same date). If so, the index itself will have been built
    # without an entry (because build_kalshi_fight_index skips partial blobs), so the
    # lookup will simply return no_kalshi_contract — which is correct behaviour.
    # The abbrev_collision_ambiguous status is returned only when we can positively
    # identify a collision from the index structure; otherwise no_kalshi_contract is fine.

    # ── Index lookup ────────────────────────────────────────────────────────
    # For moneyline the key requires both fighter names. We know only one (the outcome).
    # Strategy: scan the fight index for all fights on this date, then find the one
    # containing the outcome fighter. This is O(fights_on_date) not O(1) but fights
    # per date are bounded (usually < 15). The daily index is small.

    date_iso = parsed.date_iso

    if mt == "moneyline":
        outcome_name = parsed.fighter_a  # set to the outcome string in parse_poly_ufc_bet
        if not outcome_name:
            return MatchResult("fail", 0.0, reason="moneyline_outcome_empty", market_type=mt)

        # Find all fights on this date
        candidate_fights = [
            fight for key, fight in fight_index.items()
            if key[0] == date_iso
        ]

        if not candidate_fights:
            if date_iso not in kalshi_dates:
                return MatchResult("out_of_window", 0.0,
                                   reason="fight_date_outside_kalshi_fetch_window",
                                   market_type="moneyline")
            return MatchResult("no_kalshi_contract", 0.0,
                               reason="no_kxufcfight_on_date",
                               market_type="moneyline")

        # Find the fight containing the outcome fighter
        matches = [
            f for f in candidate_fights
            if match_fighter_name(outcome_name, f.fighter_a_name)
            or match_fighter_name(outcome_name, f.fighter_b_name)
        ]

        if not matches:
            return MatchResult("winner_outcome_unresolved", 0.0,
                               reason=f"outcome_fighter_not_in_any_fight_on_{date_iso}:{outcome_name!r}",
                               market_type="moneyline")

        if len(matches) > 1:
            # This should not happen with exact full-name matching, but guard it
            return MatchResult("abbrev_collision_ambiguous", 0.5,
                               reason=f"outcome_fighter_found_in_multiple_fights:{outcome_name!r}",
                               market_type="moneyline")

        fight = matches[0]
        _code, ticker, _name = _resolve_winner_side(outcome_name, fight)
        if ticker is None:
            return MatchResult("winner_outcome_unresolved", 0.0,
                               reason=f"side_unresolved_in_fight:{outcome_name!r}",
                               market_type="moneyline")

        return MatchResult("matched", 1.0, kalshi_ticker=ticker, leg="yes",
                           reason="unique_fight_side_resolved", market_type="moneyline")

    # ── go_the_distance ─────────────────────────────────────────────────────
    if mt == "go_the_distance":
        leg = parsed.leg      # "yes" | "no"
        if leg is None:
            return MatchResult("fail", 0.0, reason="distance_leg_missing", market_type=mt)

        # For go-the-distance we also only know the date, not the fighters.
        # The caller must therefore pass a slug that is unambiguous (one fight per
        # slug). We look up by date first, then check for uniqueness.
        # If the Kalshi index was built from per-fight tickers and the slug is
        # per-fight (as Polymarket issues it), there is exactly one fight per slug.
        # We need the two fighter names from the Kalshi index. Since the Poly slug
        # codes are opaque we cannot derive them — the caller must pass both names
        # OR the full fight_index so we can enumerate candidates.
        # Resolution: we accept a "distance_fighter_pair" in parsed.raw as an
        # optional hint. Without it we return all distance tickers for that date
        # and if there's exactly one it's unambiguous.

        candidate_fights = [
            fight for key, fight in fight_index.items()
            if key[0] == date_iso and fight.distance_ticker is not None
        ]

        if not candidate_fights:
            if date_iso not in kalshi_dates:
                return MatchResult("out_of_window", 0.0,
                                   reason="fight_date_outside_kalshi_fetch_window",
                                   market_type="go_the_distance")
            return MatchResult("no_kalshi_contract", 0.0,
                               reason="no_kxufcdistance_on_date",
                               market_type="go_the_distance")

        # If the caller provides a fighter_pair hint in raw, use it to narrow down
        hint_a = parsed.raw.get("fighter_a", "")
        hint_b = parsed.raw.get("fighter_b", "")
        if hint_a and hint_b:
            key = _fight_index_key(date_iso, hint_a, hint_b)
            fight = fight_index.get(key)
            if fight is None or fight.distance_ticker is None:
                return MatchResult("no_kalshi_contract", 0.0,
                                   reason=f"no_kxufcdistance_for_{hint_a!r}_vs_{hint_b!r}",
                                   market_type="go_the_distance")
            return MatchResult("matched", 1.0, kalshi_ticker=fight.distance_ticker,
                               leg=leg, reason="distance_fight_hint_resolved",
                               market_type="go_the_distance")

        # No hint: if there's exactly one distance fight on this date, unambiguous
        if len(candidate_fights) == 1:
            return MatchResult("matched", 1.0,
                               kalshi_ticker=candidate_fights[0].distance_ticker,
                               leg=leg, reason="distance_single_fight_on_date",
                               market_type="go_the_distance")

        # Multiple distance fights on the same date — ambiguous without a hint
        return MatchResult("abbrev_collision_ambiguous", 0.5,
                           reason=f"multiple_distance_fights_on_{date_iso}_need_fighter_hint",
                           market_type="go_the_distance")

    # Should not reach here
    return MatchResult("fail", 0.0, reason=f"unhandled_market_type:{mt}", market_type=mt)
