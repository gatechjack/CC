"""Plain-language market descriptions for Kalshi MLB tickers (pm_web display -- interim item a, 2026-09-01).

PURE + read-only. Imports only the pure team-mapping + ticker parsers and subdivision.market_type_from_ticker
(no broker, no secrets, no DB, no order path) so pm_web stays standalone. Turns a raw Kalshi ticker (+ optional
held leg 'yes'/'no') into a human sentence.

HONEST FALLBACK: anything it cannot confidently parse returns the market type + the raw ticker -- it never
fabricates or mis-labels a matchup (the same discipline as subdivision.market_type_from_ticker showing an
unclassified series verbatim). A team blob that does not split UNIQUELY into two known codes falls back rather
than guessing.

Ticker formats (established from live data -- see mlb_poly_kalshi_match.py, NOT assumed):
  KXMLBGAME-{stem}-{YES}        moneyline; the suffix names the YES-side team.
  KXMLBTOTAL-{stem}-{N}         total; YES = Over; strike = N - 0.5.
  KXMLBSPREAD-{stem}-{TEAM}{N}  spread; YES = TEAM wins by over (N - 0.5).
  stem = {YYMONDD}{HHMM}{TEAMBLOB}[G{n}] -- shared verbatim across the three series for one game.
"""
from __future__ import annotations

import re

from trading_corp.data.sports_team_mapping import MLB_TEAMS
from trading_corp.data.mlb_poly_kalshi_match import parse_kalshi_mlb_ticker
from .subdivision import market_type_from_ticker

_MONTHS = {"JAN": "Jan", "FEB": "Feb", "MAR": "Mar", "APR": "Apr", "MAY": "May", "JUN": "Jun",
           "JUL": "Jul", "AUG": "Aug", "SEP": "Sep", "OCT": "Oct", "NOV": "Nov", "DEC": "Dec"}
_DATE_RE = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})$")
# stem = date + optional HHMM + team blob (letters) + optional trailing G<digit>
_STEM_RE = re.compile(r"^\d{2}[A-Z]{3}\d{2}(?:\d{4})?(?P<blob>[A-Z]+)(?:G\d)?$")
_TOTAL_RE = re.compile(r"^KXMLBTOTAL-(?P<stem>[A-Z0-9]+)-(?P<n>\d+)$")
_SPREAD_RE = re.compile(r"^KXMLBSPREAD-(?P<stem>[A-Z0-9]+)-(?P<team>[A-Z]+)(?P<n>\d+)$")


def _fmt_date(date_str) -> str | None:
    m = _DATE_RE.match(str(date_str or ""))
    if not m:
        return None
    mon = _MONTHS.get(m.group(2))
    return "%s %d" % (mon, int(m.group(3))) if mon else None


def _fmt_time(time_str) -> str | None:
    s = str(time_str or "")
    if len(s) != 4 or not s.isdigit():
        return None
    h, mi = int(s[:2]), int(s[2:])
    if h > 23 or mi > 59:
        return None
    ap = "am" if h < 12 else "pm"
    return "%d:%02d%s ET" % (h % 12 or 12, mi, ap)


def _when(date_str, time_str) -> str | None:
    d, t = _fmt_date(date_str), _fmt_time(time_str)
    return ("%s %s" % (d, t)) if (d and t) else (d or t or None)


def _split_two_team_codes(blob):
    """(code_a, code_b) iff the blob splits UNIQUELY into two known MLB codes (2-3 letters each). None on 0 or
    >1 valid splits -> the caller falls back rather than guessing an ambiguous matchup."""
    valid = [(blob[:i], blob[i:]) for i in range(2, len(blob) - 1)
             if blob[:i] in MLB_TEAMS and blob[i:] in MLB_TEAMS]
    return valid[0] if len(valid) == 1 else None


def _teams_from_stem(stem):
    m = _STEM_RE.match(str(stem or ""))
    return _split_two_team_codes(m.group("blob")) if m else None


def describe_market(ticker, leg=None) -> str:
    """Human description of a Kalshi MLB market, plus the side we hold if `leg` is 'yes'/'no'. Honest fallback
    ('<market type>: <raw ticker>') for anything not confidently parseable. Pure -- no I/O."""
    t = str(ticker or "").upper()
    leg = str(leg).lower() if leg is not None else None
    if leg not in ("yes", "no", None):
        leg = None
    mtype = market_type_from_ticker(t)
    fallback = ("%s: %s" % (mtype, ticker)) if ticker else "-"

    if t.startswith("KXMLBGAME-"):
        p = parse_kalshi_mlb_ticker(t)
        if p is None:
            return fallback
        matchup = "%s vs %s" % (p.yes_name, p.other_name)
        backed = p.other_name if leg == "no" else p.yes_name   # yes/None name the YES side; no names the other
        parts = [x for x in (_when(p.date_str, p.time_str), ("G%d" % p.game_no) if p.game_no else None) if x]
        tail = (" (%s)" % ", ".join(parts)) if parts else ""
        return "%s -- %s to win%s" % (matchup, backed, tail)

    m = _TOTAL_RE.match(t)
    if m:
        strike = int(m.group("n")) - 0.5
        teams = _teams_from_stem(m.group("stem"))
        matchup = ("%s vs %s" % (MLB_TEAMS[teams[0]], MLB_TEAMS[teams[1]])) if teams else None
        if leg == "yes":
            side = "Over %.1f runs" % strike
        elif leg == "no":
            side = "Under %.1f runs" % strike
        else:
            side = "total %.1f runs (YES = Over)" % strike
        return ("%s -- %s" % (matchup, side)) if matchup else ("total: %s" % side)

    m = _SPREAD_RE.match(t)
    if m:
        strike = int(m.group("n")) - 0.5
        team = m.group("team")
        tname = MLB_TEAMS.get(team, team)
        teams = _teams_from_stem(m.group("stem"))
        other_code = None
        if teams:
            other_code = teams[1] if teams[0] == team else teams[0] if teams[1] == team else None
        other = MLB_TEAMS.get(other_code) if other_code else None
        if leg == "no" and other:
            side = "%s +%.1f" % (other, strike)
        else:
            side = "%s -%.1f" % (tname, strike)            # yes/None = the anchor team covering -strike
        return ("%s vs %s -- %s" % (tname, other, side)) if other else ("spread: %s" % side)

    return fallback
