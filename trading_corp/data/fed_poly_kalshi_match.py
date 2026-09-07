"""Deterministic Polymarket-FED -> Kalshi matcher (FOMC rate-decision bucket).

fed is the ODD ONE: no teams, no players, no game date -- none of the mlb/ufc/tennis/structural/cs2/
soccer shapes applies. The join is EVENT (the FOMC meeting) + BUCKET (the rate-change outcome).

Kalshi KXFEDDECISION-{YYMON}-{BUCKET}, five buckets per meeting (from yes_sub_title):
  H0  = "Fed maintains rate"  (Hike rates by 0bps)   -- no change
  H25 = "Hike 25bps"          (hike EXACTLY 25)
  H26 = "Hike >25bps"         (hike MORE than 25: 50/75/...)
  C25 = "Cut 25bps"           (cut EXACTLY 25)
  C26 = "Cut >25bps"          (cut MORE than 25)

Polymarket phrases the same as a Yes/No binary. The transform is a SEMANTIC parse of the title's
DIRECTION + bps MAGNITUDE + the '+'/'>' modifier:
  "no change" / "0 bps"                 -> H0
  "increase/hike/raise ... 25 bps"      -> H25   ;  "... 50/75 bps" (no '+') -> H26 (>25)
  "decrease/cut/lower ... 25 bps"       -> C25   ;  "... 50/50+/75+ bps"     -> C26 (>25)

★ COARSE = GATE (a right-event-wrong-bucket is a STOP, per Jack). A phrasing that SPANS two buckets
is NEVER placed: "25+ bps" (spans =25 and >25), or a hike/cut with NO magnitude ("Fed rate hike?"),
or a "N times in a year" count market, or a multi-meeting parlay. Political / Fed-chair / dissent /
personnel markets are EXCLUDED. CPI is a separate deferred workstream.

★ THE ALIAS LESSON APPLIES HERE TOO (no teams, but the phrasing->bucket map IS a transform): a wrong
bucket map would look internally coherent every single time (the PSG precedent). So (a) the parse
reads the EXPLICIT bps+direction from the title -- no inference; (b) COARSE is gated aggressively;
(c) the Kalshi bucket-code is validated against its OWN yes_sub_title (independent evidence) in
build_bucket_index -- a code/label mismatch is skipped, not trusted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

COPYABLE_MARKET_TYPES = ("bucket",)   # a single-meeting, single-bucket Yes/No

_MONTHS = {"january": "JAN", "february": "FEB", "march": "MAR", "april": "APR", "may": "MAY",
           "june": "JUN", "july": "JUL", "august": "AUG", "september": "SEP", "october": "OCT",
           "november": "NOV", "december": "DEC"}
_MONTH_ALT = {"jan": "JAN", "feb": "FEB", "mar": "MAR", "apr": "APR", "jun": "JUN", "jul": "JUL",
              "aug": "AUG", "sep": "SEP", "sept": "SEP", "oct": "OCT", "nov": "NOV", "dec": "DEC"}
_MON_RE = "(january|february|march|april|may|june|july|august|september|october|november|december)"
# Kalshi: KXFEDDECISION-{YY}{MON}-{BUCKET}
_K_RE = re.compile(r"^KXFEDDECISION-(?P<mtg>\d{2}[A-Z]{3})-(?P<bucket>H0|H25|H26|C25|C26)$")

# expected bucket-code semantics, for the independent-evidence check against yes_sub_title
_BUCKET_SEMANTICS = {
    "H0": lambda s: ("maintain" in s or "0bps" in s or "no change" in s),
    "H25": lambda s: ("hike" in s and "25bps" in s and ">" not in s),
    "H26": lambda s: ("hike" in s and ">" in s),
    "C25": lambda s: ("cut" in s and "25bps" in s and ">" not in s),
    "C26": lambda s: ("cut" in s and ">" in s),
}


def _parse_meeting(title: str) -> str | None:
    t = title or ""
    m = re.search(_MON_RE + r"\s+(\d{4})", t, re.I)
    if m:
        return "%s%s" % (m.group(2)[2:], _MONTHS[m.group(1).lower()])
    m2 = re.search(r"(\d{4})\s+" + _MON_RE, t, re.I)
    if m2:
        return "%s%s" % (m2.group(1)[2:], _MONTHS[m2.group(2).lower()])
    return None


@dataclass(frozen=True)
class ParsedFedBet:
    market_type: str            # 'bucket' | 'coarse' | 'political' | 'non_fed' | 'unparseable'
    meeting: str | None         # 'YYMON'
    bucket: str | None          # H0 | H25 | H26 | C25 | C26
    leg: str | None             # 'yes' | 'no'
    fail_reason: str | None = None
    raw: dict = field(default_factory=dict)


def parse_poly_bet(slug: str, outcome: str, title: str | None = None) -> ParsedFedBet:
    raw = {"slug": slug, "outcome": outcome, "title": title}
    t = (title or "").strip()
    tl = t.lower()
    sl = (slug or "").lower()
    # fed bets carry varied slugs (will-.../no-change-.../fed-...) but all reference the Fed/rates. Gate
    # on the SUBJECT (slug+title), not a slug prefix -- and reject a genuinely non-fed bet fed here by the
    # category router. (Chair/nominate bets DO mention "fed" -> they pass here, then hit the political skip.)
    if not any(w in tl or w in sl for w in ("fed", "federal reserve", "interest rate", "fomc",
                                            "rate hike", "rate cut", "basis point")):
        return ParsedFedBet("non_fed", None, None, None, fail_reason="not_a_fed_subject", raw=raw)
    # political / personnel / procedural -> EXCLUDE
    if any(w in tl for w in ("chair", "nominate", "nomination", "powell", "trump", "resign", "fire ",
                             "fired", "replace", "governor", "dissent", "vote", "lisa cook", "confirm")):
        return ParsedFedBet("political", None, None, None, fail_reason="political_or_personnel", raw=raw)
    # multi-meeting parlays / "N times in a year" count markets -> GATE (not a single-bucket bet)
    if re.search(r"\b(times|cuts|hikes|decisions)\b.*\b(in|during)\b.*\d{4}", tl) or \
       re.search(r"\d+\s+(times|cuts|hikes)\b", tl) or tl.count("meeting") > 1 or "→" in t or \
       re.search(r"pause.*pause", tl):
        return ParsedFedBet("coarse", None, None, None, fail_reason="parlay_or_count", raw=raw)

    leg = "yes" if outcome == "Yes" else ("no" if outcome == "No" else None)
    mtg = _parse_meeting(t)
    hold = any(w in tl for w in ("no change", "maintain", "unchanged", "keep rates", "hold rates", "leave rates"))
    hike = any(w in tl for w in ("increase", "hike", "raise"))
    cut = any(w in tl for w in ("decrease", "cut", "lower", "reduce"))
    mm = re.search(r"(\d+)\s*\+?\s*bps", tl)
    mag = int(mm.group(1)) if mm else None
    plus = bool(re.search(r"\d+\s*\+\s*bps", tl)) or (">" in t and "bps" in tl) or "or more" in tl or "at least" in tl

    def bucket(b):
        if leg is None:
            return ParsedFedBet("unparseable", mtg, b, None, fail_reason="outcome_not_yes_no", raw=raw)
        return ParsedFedBet("bucket", mtg, b, leg, raw=raw)

    if hold and not (hike or cut):
        return bucket("H0")
    if cut and not hike:
        d = "C"
    elif hike and not cut:
        d = "H"
    else:
        return ParsedFedBet("unparseable", mtg, None, None, fail_reason="direction_unclear", raw=raw)
    if mag is None:
        return ParsedFedBet("coarse", mtg, None, None, fail_reason="no_magnitude_spans_buckets", raw=raw)
    if mag == 0:
        return bucket("H0")                              # "hike by 0 bps" == no change
    if mag == 25 and not plus:
        return bucket(d + "25")
    if mag == 25 and plus:
        return ParsedFedBet("coarse", mtg, None, None, fail_reason="25plus_spans_25_and_gt25", raw=raw)
    if mag > 25:
        return bucket(d + "26")                          # 50 / 50+ / 75+ all -> >25 bucket
    # mag < 25 and != 0 (rare, e.g. 10bps) -> no Kalshi bucket -> safe miss
    return ParsedFedBet("unparseable", mtg, None, None, fail_reason="sub25_magnitude_no_bucket", raw=raw)


def build_bucket_index(markets: list[dict]) -> dict:
    """{meeting: {bucket: ticker}} from KXFEDDECISION markets. ★ independent-evidence check: the ticker's
    bucket CODE is validated against its OWN yes_sub_title -- a code/label mismatch (a Kalshi relabel) is
    SKIPPED, never trusted (the PSG lesson: don't trust a code the gate would also use)."""
    idx: dict = {}
    for mk in markets:
        tk = (mk.get("ticker") or "").strip()
        sub = (mk.get("yes_sub_title") or "").strip().lower()
        m = _K_RE.match(tk)
        if not m:
            continue
        code = m.group("bucket")
        check = _BUCKET_SEMANTICS.get(code)
        if check and sub and not check(sub):
            continue                                     # code says one thing, label says another -> skip
        idx.setdefault(m.group("mtg"), {})[code] = tk
    return idx


@dataclass(frozen=True)
class MatchResult:
    status: str
    confidence: float
    kalshi_ticker: str | None = None
    reason: str | None = None
    leg: str | None = None
    market_type: str | None = None


def match_bet(parsed: ParsedFedBet, bucket_index: dict, kalshi_meetings,
              allowed_market_types=COPYABLE_MARKET_TYPES) -> MatchResult:
    mt = parsed.market_type
    if mt != "bucket":
        if mt == "non_fed":
            return MatchResult("skip_non_fed", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
        if mt == "political":
            return MatchResult("skip_political", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
        if mt == "coarse":
            return MatchResult("skip_coarse", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
        return MatchResult("skip_unparseable", 0.0, reason=parsed.fail_reason or mt, market_type=mt)
    if mt not in allowed_market_types:
        return MatchResult("skip_market_type_excluded", 0.0, reason="bucket_not_in_subdivision_market_types", market_type=mt)
    if not parsed.meeting:
        return MatchResult("fail", 0.0, reason="no_meeting", market_type=mt)
    if parsed.leg not in ("yes", "no"):
        return MatchResult("fail", 0.0, reason="no_leg", market_type=mt)
    mkts = bucket_index.get(parsed.meeting)
    if not mkts:
        if parsed.meeting not in kalshi_meetings:
            return MatchResult("out_of_window", 0.0, reason="meeting_not_listed_by_kalshi", market_type=mt)
        return MatchResult("no_kalshi_contract", 0.0, reason="meeting_present_no_buckets", market_type=mt)
    tk = mkts.get(parsed.bucket)
    if not tk:
        return MatchResult("no_kalshi_contract", 0.0, reason="bucket_%s_absent_for_meeting" % parsed.bucket, market_type=mt)
    return MatchResult("matched", 1.0, kalshi_ticker=tk, leg=parsed.leg, reason="bucket_%s" % parsed.bucket, market_type=mt)
