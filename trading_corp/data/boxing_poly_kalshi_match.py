"""Deterministic Polymarket-Boxing -> Kalshi matcher (Phase 1, MONEYLINE-ONLY).

Pure functions only (no network) — fully unit-testable and O(bouts_on_date) at match time.
A clone of the UFC matcher's STRUCTURE (fight index keyed by date + fighter-pair; code-anchored
label bind) reduced to the ONE market type Polymarket actually offers for boxing.

────────────────────────────────────────────────────────────────────────────────
SCOPE — WINNER (moneyline) ONLY.  Do NOT "complete" this with method / go-the-distance / rounds.
────────────────────────────────────────────────────────────────────────────────
Probed live 2026-09-14 (gamma tag_slug=boxing, 100 events): Polymarket boxing is WINNER-ONLY —
0 of 100 events carry more than one market. There is NO Polymarket go-the-distance / method /
round / draw market to COPY. Kalshi DOES list KXBOXINGMOV (method + a first-class -DRAW) and
KXBOXINGDISTANCE, but with no Poly source they are uncopyable dead code — exactly the UFC
"round-of-victory / decision: no Poly source" deferral. Boxing is moneyline, full stop.

★ THE DRAW DIVERGENCE (board-ruled 2026-09-14 = ACCEPT, matcher unchanged): 6 of 92 two-fighter
bouts (6.5%, ~6x UFC) resolved a DRAW on Poly ([0.5,0.5] refund ~50%) while Kalshi KXBOXING
resolves the copied "A wins" YES market to NO = 100% loss. The board accepted the ~3.25%-of-stake
drag because the matcher is nearly free and the category stays optional; this is NOT a ruling that
3.25% is fine in general — if boxing is ever enabled and trades volume the hedge question reopens
on evidence. Measurement recorded in reports/prediction_markets/BOXING_PROBE_FINDINGS_2026-09-14.md.

────────────────────────────────────────────────────────────────────────────────
KXBOXING winner ticker (probed live 2026-09-14):
  KXBOXING-{YYMONDD}{BLOB}-{YESCODE}
  • YYMONDD = 2-digit year + 3-letter month + 2-digit day (e.g. 26OCT10)
  • BLOB    = the two fighters' surname-codes concatenated (VARIABLE length, 5-6 chars each — NOT
              UFC's fixed 3; e.g. SANDOVCOLLAZ = SANDOV+COLLAZ, SCHOFIBAHDI = SCHOFI+BAHDI[5])
  • YESCODE = the YES-side fighter's surname-code (the final '-' delimits it -> no blob split needed)
  Bind the fighter FULL name via `yes_sub_title` ("Ricardo Rafael Sandoval"); fall back to the
  title "{Full Name} wins" minus " wins". exchange_index = 0 (shard 0, like UFC/MMA).
  REAL: KXBOXING-26SEP12GARCIAMORALE-GARCIA yes_sub_title "Sean Garcia" (won) / -MORALE "Abraham Morales"

Polymarket boxing slug (probed live 2026-09-14):
  {zuffa|boxing}-{code1}-{code2}-{YYYY-MM-DD}[noise]
  • TWO copyable prefixes: `zuffa-` (Zuffa Boxing promo, 63/100) and `boxing-` (29/100). Opaque
    short codes like UFC's (garci1, moral1), NO -vs- separator. Date = the last -YYYY-MM-DD segment.
  • Moneyline OUTCOME is a SURNAME ("Garcia"), NOT the full name UFC uses -> the ONE real bind
    change vs the UFC clone (see match_boxing_name). Novelty prefixes (mvp/brand/glory[kickboxing]/
    will-...-single-fighter) are NOT boxing here — a two-fighter matcher cannot touch them.

────────────────────────────────────────────────────────────────────────────────
THE SURNAME-BIND RISK (board-flagged 2026-09-14) and how it stays a SAFE MISS, never a mis-pick
────────────────────────────────────────────────────────────────────────────────
A surname is less specific than a full name; this family's failure mode is binding to the WRONG
competitor. Three independent guards, all fail-closed:
  1. CODE-ANCHOR (labels_code_swapped, the cs2/FED lesson): the ticker's OWN -CODE must abbreviate
     its yes_sub_title better than the swapped assignment, else the bout is REFUSED (safe miss).
  2. CROSS-BOUT UNIQUENESS: if the outcome surname matches fighters in >1 bout on the date (two
     same-surname fighters on the card), the match is abbrev_collision_ambiguous — a SAFE MISS.
  3. WITHIN-BOUT UNIQUENESS: if the outcome surname matches BOTH fighters of the one bout
     (a same-surname bout), it is abbrev_collision_ambiguous — a SAFE MISS.
Proven in tests/prediction_markets/test_boxing_match.py (the same-card same-surname collision cases).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Shared, byte-for-byte reused from the UFC matcher (the cs2 precedent for shared helpers):
#   _norm (accent-fold+lower+collapse), kalshi_to_iso_date, _code_of, labels_code_swapped (code-anchor).
from .ufc_poly_kalshi_match import _norm, kalshi_to_iso_date, _code_of, labels_code_swapped  # noqa: F401


# ── Poly slug parsing ────────────────────────────────────────────────────────
# {zuffa|boxing}-{codes}-{YYYY-MM-DD}[noise].  codes may contain hyphens; date is the last -YYYY-MM-DD.
_POLY_BOXING_RE = re.compile(
    r"^(?:zuffa|boxing)-(?P<codes>.+?)-(?P<date>\d{4}-\d{2}-\d{2})(?P<suffix>.*)$"
)
_NOISE_RE = re.compile(r"^(?:-\d+)+$")          # trailing dedup fragments Poly appends on a slug collision
_BOXING_PREFIXES = ("zuffa-", "boxing-")
# Name suffixes dropped before surname comparison so "Sean Garcia Jr" surname-matches "Garcia".
_NAME_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})

COPYABLE_MARKET_TYPES = ("moneyline",)


@dataclass(frozen=True)
class ParsedPolyBet:
    """Parsed Polymarket boxing bet. Mirrors the public surface of the UFC/cs2 ParsedPolyBet."""
    market_type: str          # "moneyline" | "non_boxing" | "prop" | "unparseable"
    date_iso: str | None      # bout date, YYYY-MM-DD (the slug's last -YYYY-MM-DD segment)
    outcome_name: str | None  # the surname (or name) the whale bet -- the winner-side identity
    leg: str | None           # "yes" (moneyline is always a YES-on-the-bet-fighter copy)
    fail_reason: str | None = None
    raw: dict = field(default_factory=dict)


def parse_poly_boxing_bet(slug: str, outcome: str, title: str = None) -> ParsedPolyBet:
    """Parse one Poly boxing activity/position row into a ParsedPolyBet.

    `title` is accepted for dispatch parity with the other matchers but is NOT used for the bind
    (the outcome surname + date + the Kalshi index resolve the side; the title carries no
    information the outcome does not, and depending on it would add a parse-fragility surface).
    """
    raw = {"slug": slug, "outcome": outcome}
    s = (slug or "")
    if not any(s.startswith(p) for p in _BOXING_PREFIXES):
        return ParsedPolyBet("non_boxing", None, None, None,
                             fail_reason="slug_not_boxing:%r" % slug, raw=raw)
    m = _POLY_BOXING_RE.match(s)
    if not m:
        return ParsedPolyBet("unparseable", None, None, None,
                             fail_reason="slug_no_date:%r" % slug, raw=raw)
    date_iso = m.group("date")
    suffix = m.group("suffix") or ""
    # A pure trailing "-<digits>" run is Poly slug-collision NOISE, not a market suffix -> still moneyline.
    if suffix and not _NOISE_RE.match(suffix):
        # Boxing is winner-only; any real suffix is a market we do not copy -> a labelled skip, never a fail.
        return ParsedPolyBet("prop", date_iso, None, None, raw=raw)
    oc = (outcome or "").strip()
    if not oc:
        return ParsedPolyBet("moneyline", date_iso, None, None,
                             fail_reason="empty_outcome", raw=raw)
    return ParsedPolyBet("moneyline", date_iso, oc, "yes", raw=raw)


# ── Fighter name matching (surname-tolerant) ─────────────────────────────────

def _surname_tokens(name: str) -> list:
    """Accent-folded tokens reduced so the LAST token is the real surname, tolerant of the TWO live Kalshi
    yes_sub_title shapes on the same card (probed 2026-09-14):
      - "First Last"  ("Sean Garcia")      -> ['sean','garcia'] (surname = last)
      - "Surname Initial."  ("Cortes A.", "Ramirez J. C.", "Magsayo M.")  -> the surname LEADS and a trailing
        single-letter initial follows; 7 of 300 KXBOXING markets use this. Strip the trailing initial(s) so the
        surname (leading token) becomes the last remaining token -> a whale's "Cortes" then binds to "Cortes A.".
    Also strips trailing generational suffixes ('Sean Garcia Jr' -> ['sean','garcia']). Only TRAILING single-letter
    tokens are dropped (a leading initial like "J Smith" keeps 'smith' as the surname); a genuine surname is never
    a single letter, so this cannot strip a real surname. The board-flagged risk (a looser parse mis-binding a
    competitor) does NOT apply: this only RE-LOCATES which existing token is the surname; a different surname still
    fails the last-token equality gate, and a same-surname bout is still caught by the collision guards (proven in
    tests: the 4 Surname-Initial near-misses now bind, the Garcia collision STILL safe-misses, 0 wrong picks)."""
    toks = _norm(name).split()
    while len(toks) > 1 and toks[-1] in _NAME_SUFFIXES:      # drop trailing generational suffixes (jr/sr/ii..)
        toks = toks[:-1]
    while len(toks) > 1 and len(toks[-1]) == 1:              # drop trailing single-letter INITIALS ("Cortes A." -> "Cortes")
        toks = toks[:-1]
    return toks


def match_boxing_name(candidate: str, known_full_name: str) -> bool:
    """True if `candidate` (a Polymarket outcome, typically a SURNAME) names the SAME fighter as
    `known_full_name` (a Kalshi yes_sub_title full name).

    Surname-tolerant (the boxing difference from UFC, where the outcome is a full name):
      1. Exact accent-folded, suffix-stripped equality.
      2. The LAST (surname) token must match exactly (folded). A single-token outcome (surname
         only) that matches the surname is ACCEPTED — its safety comes from the cross-bout and
         within-bout uniqueness guards in match_bet (a same-surname collision -> safe miss, never a
         pick), NOT from this predicate.
      3. A multi-token outcome additionally needs first-token compatibility (the UFC-strict rule:
         exact, or the shorter first token >= 3 chars is a prefix of the longer) — so a full-name
         outcome cannot mis-bind to a different fighter sharing only the surname.
    A different surname NEVER matches (rule 2 gate), so this cannot mis-route across surnames.
    """
    c = _surname_tokens(candidate)
    k = _surname_tokens(known_full_name)
    if not c or not k:
        return False
    if c == k:
        return True
    if c[-1] != k[-1]:                     # surname must match (accent-folded) exactly
        return False
    if len(c) == 1:                        # surname-only outcome -> accept (uniqueness guards catch collisions)
        return True
    cf, kf = c[0], k[0]                     # multi-token: require first-token compatibility (UFC-strict)
    if cf == kf:
        return True
    shorter, longer = (cf, kf) if len(cf) <= len(kf) else (kf, cf)
    return len(shorter) >= 3 and longer.startswith(shorter)


# ── Kalshi boxing bout index ─────────────────────────────────────────────────
@dataclass(frozen=True)
class KalshiBout:
    """One boxing bout as seen in the Kalshi KXBOXING index: two fighters, two YES-side tickers,
    keyed by (date_iso, frozenset{norm_name_a, norm_name_b})."""
    date_iso: str
    date_str: str                  # YYMONDD (e.g. "26OCT10")
    name_a: str
    name_b: str
    code_a: str                    # YES-side surname-code from ticker_a (the ticker's OWN code)
    code_b: str
    ticker_a: str                  # KXBOXING ticker for name_a YES
    ticker_b: str


# KXBOXING-{YYMONDD}{BLOB}-{YESCODE}.  BLOB + YESCODE are variable-length [A-Z0-9]; the final '-'
# delimits YESCODE, so no positional blob split is needed (group by (date, blob), collect the two codes).
_KALSHI_BOXING_RE = re.compile(
    r"^KXBOXING-(?P<date>\d{2}[A-Z]{3}\d{2})(?P<blob>[A-Z0-9]+)-(?P<yes>[A-Z0-9]+)$"
)


def _bout_key(date_iso: str, name_a: str, name_b: str) -> tuple:
    return (date_iso, frozenset({_norm(name_a), _norm(name_b)}))


def build_kalshi_boxing_index(markets: list) -> dict:
    """Build {(date_iso, frozenset{norm_a, norm_b}): KalshiBout} from KXBOXING winner markets.

    `markets` = list of dicts with at least {"ticker", "title", "yes_sub_title"}. The fighter FULL
    name is bound from `yes_sub_title` (fallback: title minus " wins"). Two YES-side tickers sharing
    a (date_str, blob) form one bout. Malformed / non-KXBOXING tickers, a (date,blob) with != 2
    sides, or a bout whose two folded names are equal (a data glitch) are silently skipped.
    """
    by_blob: dict = {}
    for mk in markets:
        ticker = (mk.get("ticker") or "").strip()
        m = _KALSHI_BOXING_RE.match(ticker)
        if not m:
            continue
        name = (mk.get("yes_sub_title") or "").strip()
        if not name:
            title = (mk.get("title") or "").strip()
            if title.endswith(" wins"):
                name = title[:-len(" wins")].strip()
        if not name:
            continue
        by_blob.setdefault((m.group("date"), m.group("blob")), {})[m.group("yes")] = (name, ticker)

    index: dict = {}
    for (date_str, _blob), sides in by_blob.items():
        if len(sides) != 2:
            continue                                   # partial / ambiguous -> skip (matcher returns no_kalshi_contract)
        (code_a, (name_a, ticker_a)), (code_b, (name_b, ticker_b)) = list(sides.items())
        if _norm(name_a) == _norm(name_b):
            continue                                   # degenerate (same fighter both sides) -> skip
        date_iso = kalshi_to_iso_date(date_str)
        if date_iso is None:
            continue
        index[_bout_key(date_iso, name_a, name_b)] = KalshiBout(
            date_iso=date_iso, date_str=date_str,
            name_a=name_a, name_b=name_b, code_a=code_a, code_b=code_b,
            ticker_a=ticker_a, ticker_b=ticker_b,
        )
    return index


# ── MatchResult (same public surface as the UFC/cs2 matchers) ────────────────
@dataclass(frozen=True)
class MatchResult:
    status: str                    # matched | no_kalshi_contract | out_of_window |
                                   # abbrev_collision_ambiguous | winner_outcome_unresolved |
                                   # skip_non_boxing | skip_prop | skip_market_type_excluded | fail
    confidence: float
    kalshi_ticker: str | None = None
    reason: str | None = None
    leg: str | None = None         # "yes" — the Kalshi leg to BUY
    market_type: str | None = None


def _resolve_side(outcome: str, bout: KalshiBout):
    """Return (ticker, None) for the bet fighter's YES side, or (None, why). CODE-ANCHORED: refuse a
    bout whose (code,label) is swapped onto the wrong tickers (a Kalshi mislabel). A surname that
    matches BOTH fighters (a same-surname bout) is ambiguous -> (None,'ambiguous_both') = safe miss."""
    if labels_code_swapped(bout.code_a, bout.name_a, bout.code_b, bout.name_b):
        return None, "code_swapped"
    a = match_boxing_name(outcome, bout.name_a)
    b = match_boxing_name(outcome, bout.name_b)
    if a and b:
        return None, "ambiguous_both"
    if a:
        return bout.ticker_a, None
    if b:
        return bout.ticker_b, None
    return None, "outcome_neither"


def match_bet(
    parsed: ParsedPolyBet,
    bout_index: dict,
    kalshi_dates: frozenset,
    allowed_market_types: tuple = COPYABLE_MARKET_TYPES,
) -> MatchResult:
    """Unified boxing match entry point. Compatible shape with the UFC/cs2 match_bet dispatchers.

    Args:
      parsed               — from parse_poly_boxing_bet(slug, outcome[, title])
      bout_index           — from build_kalshi_boxing_index (keyed by (date_iso, frozenset{names}))
      kalshi_dates         — frozenset of ISO dates present in the index (distinguishes "no contract
                             on this date" from "date outside the fetched window")
      allowed_market_types — the sub-division's configured market types; a type not in the list ->
                             skip_market_type_excluded (INERT-ship gate), never an error
    """
    mt = parsed.market_type

    # ── Scope gate ──────────────────────────────────────────────────────────
    if mt not in COPYABLE_MARKET_TYPES:
        if mt == "non_boxing":
            return MatchResult("skip_non_boxing", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
        if mt == "prop":
            return MatchResult("skip_prop", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
        return MatchResult("fail", 0.0, reason=parsed.fail_reason or mt, market_type=mt)

    if mt not in allowed_market_types:
        return MatchResult("skip_market_type_excluded", 0.0,
                           reason="moneyline_not_in_subdivision_market_types", market_type=mt)

    # ── Date / parse guard ──────────────────────────────────────────────────
    if parsed.date_iso is None:
        return MatchResult("fail", 0.0, reason="no_date", market_type=mt)
    if parsed.fail_reason:
        return MatchResult("fail", 0.0, reason=parsed.fail_reason, market_type=mt)
    outcome = parsed.outcome_name
    if not outcome:
        return MatchResult("fail", 0.0, reason="moneyline_outcome_empty", market_type=mt)

    date_iso = parsed.date_iso
    bouts_on_date = [b for key, b in bout_index.items() if key[0] == date_iso]
    if not bouts_on_date:
        if date_iso not in kalshi_dates:
            return MatchResult("out_of_window", 0.0,
                               reason="bout_date_outside_kalshi_fetch_window", market_type=mt)
        return MatchResult("no_kalshi_contract", 0.0, reason="no_kxboxing_on_date", market_type=mt)

    # A surname matching fighters in >1 bout on the date (two same-surname fighters on the card)
    # is a SAFE MISS -- never guess which bout (board-flagged surname risk).
    matches = [b for b in bouts_on_date
               if match_boxing_name(outcome, b.name_a) or match_boxing_name(outcome, b.name_b)]
    if not matches:
        return MatchResult("winner_outcome_unresolved", 0.0,
                           reason="outcome_not_in_any_bout_on_%s:%r" % (date_iso, outcome), market_type=mt)
    if len(matches) > 1:
        return MatchResult("abbrev_collision_ambiguous", 0.5,
                           reason="outcome_surname_in_multiple_bouts:%r" % outcome, market_type=mt)

    bout = matches[0]
    ticker, why = _resolve_side(outcome, bout)
    if ticker is None:
        if why == "ambiguous_both":
            # the outcome surname matches BOTH fighters of the one bout (a same-surname bout) -> safe miss
            return MatchResult("abbrev_collision_ambiguous", 0.5,
                               reason="outcome_surname_matches_both_fighters:%r" % outcome, market_type=mt)
        return MatchResult("winner_outcome_unresolved", 0.0,
                           reason="side_unresolved_in_bout:%s:%r" % (why, outcome), market_type=mt)

    return MatchResult("matched", 1.0, kalshi_ticker=ticker, leg="yes",
                       reason="unique_bout_side_resolved", market_type="moneyline")
