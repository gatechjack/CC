"""Shared SUB-GAME matcher core -- written ONCE, applied TWICE (F5 in mlb_poly_kalshi_match, first-half in
sports_structural_match). A sub-game family (MLB first-5-innings / structural first-half) has three sub-markets on
its OWN Kalshi series: WINNER (3-way incl TIE), SPREAD (per-team, per-strike), TOTAL (per-strike). All join to the
game by the SHARED stem (the {series}-{stem}-... segment that KX{X}GAME/TOTAL/SPREAD/F5/1H all carry for one game).

★ ROUTE-ONLY BY CONSTRUCTION (the load-bearing safety): the join functions read ONLY the sub-game index passed in.
The caller passes ONLY the sub-game series' index (never the full-game index), so a full-game ticker is UNREACHABLE
from a sub-game bet -- a first-half Over 23.5 can never bind the full-game Over 23.5, which is a different market.
This is a property of WHICH index is passed, not a preference; test_*_route_only proves it by sharing the full-game
index in and asserting the (wrong) full-game bind DOES occur -- so the test has teeth and would catch a wiring that
ever shared the indices.

★ 3-WAY: the winner index is keyed by side code OR the literal 'TIE'. A draw/tie bet resolves side_key='TIE' and
can bind ONLY the TIE ticker; a team bet resolves side_key=<team code> and binds ONLY that team's ticker. A tie can
never reach a two-way team ticker because the key differs.

Pure + stdlib-only (regex). No network, no venue import.
"""
from __future__ import annotations

import re

TIE_KEY = "TIE"


def _win_re(series: str):
    # {series}-{stem}-{SIDE}  where SIDE is a team code or TIE. The required '-' after {series} disambiguates a
    # winner series (KXNFL1H) from its sibling spread/total series (KXNFL1HSPREAD/1HTOTAL), which have no '-' there.
    return re.compile(r"^%s-(?P<stem>[A-Z0-9]+)-(?P<side>[A-Z]+)$" % re.escape(series))


def _total_re(series: str):
    return re.compile(r"^%s-(?P<stem>[A-Z0-9]+)-(?P<n>\d+)$" % re.escape(series))


def _spread_re(series: str):
    return re.compile(r"^%s-(?P<stem>[A-Z0-9]+)-(?P<team>[A-Z]+)(?P<n>\d+)$" % re.escape(series))


def build_win_index(tickers, series: str) -> dict:
    """{stem: {side_key: ticker}} for a sub-game WINNER series ({series}-{stem}-{TEAM|TIE}). 3-way: TIE is a key."""
    rx = _win_re(series); idx: dict = {}
    for t in tickers:
        m = rx.match(t or "")
        if m:
            idx.setdefault(m.group("stem"), {})[m.group("side")] = t
    return idx


def build_total_index(tickers, series: str) -> dict:
    """{stem: {strike: ticker}} for a sub-game TOTAL series ({series}-{stem}-{N}; strike = N - 0.5, half-run ladder)."""
    rx = _total_re(series); idx: dict = {}
    for t in tickers:
        m = rx.match(t or "")
        if m:
            idx.setdefault(m.group("stem"), {})[int(m.group("n")) - 0.5] = t
    return idx


def build_spread_index(tickers, series: str) -> dict:
    """{stem: {(team_code, strike): ticker}} for a sub-game SPREAD series ({series}-{stem}-{TEAM}{N}; strike N-0.5)."""
    rx = _spread_re(series); idx: dict = {}
    for t in tickers:
        m = rx.match(t or "")
        if m:
            idx.setdefault(m.group("stem"), {})[(m.group("team"), int(m.group("n")) - 0.5)] = t
    return idx


# ── the ROUTE-ONLY joins: each reads ONLY the sub-game index handed in (never a full-game index) ──────────────
def join_win(stem: str, side_key: str, win_index: dict):
    """The winner ticker for (stem, side_key) where side_key is a team code or TIE_KEY, else None. win_index ONLY."""
    return (win_index or {}).get(stem, {}).get(side_key)


def join_total(stem: str, strike, total_index: dict):
    """The total ticker for (stem, exact strike), else None (EXACT-STRIKE ONLY). total_index ONLY."""
    return (total_index or {}).get(stem, {}).get(strike)


def join_spread(stem: str, team_code: str, strike, spread_index: dict):
    """The spread ticker for (stem, team_code, exact strike), else None (EXACT). spread_index ONLY."""
    return (spread_index or {}).get(stem, {}).get((team_code, strike))
