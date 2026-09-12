"""Assemble the live sub-division (game-card) context for pm_web (UI rewrite, Scope A/F).

Joins THREE read-only sources, all already isolated from the order path:
  * the DB order JOURNAL (subdivision.live_orders / live_positions) -- what we hold / settled / closed,
  * the cached sports FEED slate (ui_cache <- feed_mlb) -- the box score, keyed by (ET date, DH#, team-set),
  * the cached Kalshi MARKS (ui_cache <- marks) -- current BID per ticker, for current value.

Every number carries provenance + its own age; anything missing degrades to a defined empty state (feed
"unavailable", value "no mark") -- NEVER $NaN / $0 / cost-standing-in-for-value, and NEVER a wrong game.

Terminal states (from the journal, not from scores):
  open     -- net still held; current value = contracts x held-leg BID.
  settled  -- Kalshi resolved it (close_source in {settlement, settlement_void}); realized/won are booked.
  exit     -- we followed a whale out before settlement (is_exit, close_source NULL); realized booked.
  opposed  -- two whales took opposite sides and the guard closed it (close_source 'opposed'); the engine does
              NOT book a realized on this path -> shown as "not booked", never a guessed number.

Pure assembly (build_live_context) takes already-fetched journal rows + slate + marks, so it unit-tests with no
DB and no network. Per the brief we do NOT render a "settled during a live game" note (the settlement-ts<->game
-state join is unbuilt); settled positions render with note=None.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..market_describe import describe_market
from ...data.mlb_poly_kalshi_match import kalshi_to_iso_date
from ...data.sports_team_mapping import MLB_TEAMS, NBA_TEAMS, NHL_TEAMS, NFL_TEAMS, WNBA_TEAMS
from ...data.cfb_teams import CFB_TEAMS    # Item 2 (2026-09-12): the structural matcher's team maps are DATA-side
from ...data.sports_structural_match import LEAGUES as _STRUCT_LEAGUES, parse_poly_bet as _parse_poly_bet  # Item 2: Poly slug decode (stdlib+data-only)
from . import feed_mlb, marks as marks_mod, milestones as milestones_mod   # (stdlib-only) -> pm_web stays standalone
from .. import leg_audit        # canonical leg-audit state constants (shared with the fill-watch runner -> no drift)

KINDS = ("moneyline", "total", "spread")
KIND_LABEL = {"moneyline": "ML", "total": "TOT", "spread": "SPR"}
# Item 2: SPORT (Kalshi series prefix, after KX and before the market-type) -> the team-code map. The structural
# matcher (data/sports_structural_match.py LEAGUES) is the source of these; we reuse the SAME data-side maps so
# pm_web's label decode can never diverge from the matcher's. Longest prefix wins (WNBA before NBA).
_SPORT_TEAM_MAP = {"MLB": MLB_TEAMS, "NFL": NFL_TEAMS, "NBA": NBA_TEAMS, "NHL": NHL_TEAMS,
                   "WNBA": WNBA_TEAMS, "NCAAF": CFB_TEAMS}


def _team_map_for_ticker(ticker):
    """The per-league team-code map for a Kalshi ticker (via its series prefix KX<SPORT><TYPE>), or None if the
    sport has no map (tennis/ufc/fed -- no two-team structural ticker). Longest prefix wins so KXWNBA* != KXNBA*."""
    series = str(ticker or "").split("-", 1)[0].upper()
    body = series[2:] if series.startswith("KX") else series
    for sport in sorted(_SPORT_TEAM_MAP, key=len, reverse=True):
        if body.startswith(sport):
            return _SPORT_TEAM_MAP[sport]
    return None
RETENTION_HOURS = 24
POLL_INTERVAL_SECONDS = 60
_SETTLE_SOURCES = ("settlement", "settlement_void")


# ── ticker -> game key + market kind + compact label ─────────────────────────────────────────────────────────
_STEM_RE = re.compile(r"^(\d{2}[A-Z]{3}\d{2})(\d{4})?([A-Z0-9]+)$")


def _split_team_blob(blob: str, team_map: dict = MLB_TEAMS):
    """A team blob 'AWAYHOME' (e.g. SDCIN, SEABOS, MIZZKU) -> (away_code, home_code) using `team_map` as the split
    oracle (both halves must be known clubs). Concatenation order is away+home (verified MLB SDCIN=SD@CIN). FAIL
    CLOSED (Item 2): returns None unless EXACTLY ONE split point yields two mapped codes -- an AMBIGUOUS blob (two
    valid splits) degrades to no-matchup rather than guessing (never a wrong game/team on a real-money row)."""
    hits = [(blob[:k], blob[k:]) for k in range(2, len(blob) - 1)
            if blob[:k] in team_map and blob[k:] in team_map]
    return hits[0] if len(hits) == 1 else None


def game_key_from_ticker(ticker: str):
    """Any KXMLBGAME/KXMLBTOTAL/KXMLBSPREAD ticker -> the canonical game key (date_iso, HHMM, DH#,
    frozenset{names}) that feed_mlb.feed_game_key produces -- so a position joins its feed game. None if the stem
    is not a two-club MLB game. Uses the shared STEM (ticker.split('-')[1]); the market type differs by prefix,
    the game stem does not."""
    parts = str(ticker or "").split("-")
    if len(parts) < 2:
        return None
    m = _STEM_RE.match(parts[1])
    if not m:
        return None
    date_str, hhmm, blob = m.group(1), m.group(2), m.group(3)
    game_no = None
    gm = re.search(r"G(\d)$", blob)
    if gm:
        game_no = int(gm.group(1))
        blob = blob[:gm.start()]
    sp = _split_team_blob(blob)
    if sp is None:
        return None
    date_iso = kalshi_to_iso_date(date_str) or date_str
    return (date_iso, hhmm, game_no, frozenset({MLB_TEAMS[sp[0]], MLB_TEAMS[sp[1]]}))


def _kind(ticker: str) -> str:
    series = str(ticker or "").upper().split("-", 1)[0]
    if "SPREAD" in series:
        return "spread"
    if "TOTAL" in series:
        return "total"
    if "GAME" in series or "MONEY" in series:
        return "moneyline"
    return series.lower()


def _spread_other(ticker: str, team_code: str) -> str | None:
    """The OTHER club's code on a spread ticker (the one that is NOT the anchor `team_code`), or None. Used to
    name the underdog side we back when we hold the NO leg of a spread."""
    a, h = _ordered_teams(ticker)
    if team_code == a:
        return h
    if team_code == h:
        return a
    return None


def _held_team_code(ticker: str, held_leg: str | None) -> str:
    """The MLB moneyline team WE HOLD, leg-aware (codes only, upper): the YES club for a YES/absent leg, the OTHER
    club for a NO leg. Falls back to the yes suffix if the pair does not resolve. Used by _short_label's ML branch
    (so the compact label names the side we hold, not just the market's YES side) and by the event-block score-line
    marker, so 'who we're cheering for' is unmistakable."""
    parts = str(ticker or "").split("-")
    yes = (parts[2] if len(parts) > 2 else "").upper()
    if str(held_leg).lower() != "no":
        return yes
    a, h = _ordered_teams(ticker)
    a, h = (a or "").upper(), (h or "").upper()
    if yes == a and h:
        return h
    if yes == h and a:
        return a
    return yes


def _short_label(ticker: str, kind: str, held_leg: str | None) -> str:
    """A compact bet label for the card slot, DERIVED FROM THE TICKER + HELD LEG and carrying DIRECTION (Jack's
    ruling): TOTAL shows over/under as a sign on the strike -- '+8.5' (Over, the YES leg) / '-8.5' (Under, the NO
    leg); SPREAD shows the sign + the team backed -- '-1.5 ATL' (YES = the anchor team lays the spread) /
    '+1.5 SD' (NO = the other team gets it); MONEYLINE shows the YES club abbr. strike = N - 0.5 (the Kalshi
    total/spread convention, see market_describe). leg is None on a SETTLED slot -> the line/anchor is shown
    WITHOUT a fabricated side. Falls back to the raw suffix when the ticker does not parse."""
    parts = str(ticker or "").split("-")
    suffix = parts[2] if len(parts) > 2 else ""
    leg = str(held_leg).lower() if held_leg else None
    if kind == "total":
        mt = re.match(r"^(\d+)$", suffix)
        if mt:
            strike = "%.1f" % (int(mt.group(1)) - 0.5)
            if leg == "yes":
                return "+" + strike                       # Over (YES)
            if leg == "no":
                return "-" + strike                       # Under (NO)
            return strike                                 # settled/unknown side -> line only, no fabricated direction
        # legacy 'O8.5'/'U8.5' suffix form, if ever present
        mo = re.match(r"^([OU])([0-9.]+)$", suffix)
        if mo:
            return ("+" if mo.group(1) == "O" else "-") + mo.group(2)
    if kind == "spread":
        ms = re.match(r"^([A-Z]{2,})(\d+)$", suffix)          # Item 2: >=2 chars -> CFB 4-char codes (MIZZ7) decode too
        if ms:
            team_code, n = ms.group(1), int(ms.group(2))
            strike = "%.1f" % (n - 0.5)
            other = _spread_other(ticker, team_code)
            if leg == "no" and other:
                return "+%s %s" % (strike, other)         # the OTHER team gets +strike (the underdog side)
            return "-%s %s" % (strike, team_code)         # yes/None -> the anchor team lays -strike (favourite)
    if kind == "moneyline":
        # the team WE HOLD, leg-aware: YES club for a YES/absent leg, the OTHER club for a NO leg. This makes the
        # compact label carry DIRECTION for ML too (like TOT/SPR) -- who we hold, not just the market's YES side.
        return _held_team_code(ticker, held_leg)
    return suffix or "—"


# ── time helpers ─────────────────────────────────────────────────────────────────────────────────────────────
def _et_date(ts) -> str | None:
    if not ts:
        return None
    return feed_mlb.utc_to_eastern(datetime.fromtimestamp(int(ts), tz=timezone.utc)).strftime("%Y-%m-%d")


def _et_hhmm_from_key(hhmm: str | None) -> str | None:
    if not hhmm or len(hhmm) != 4:
        return None
    return "%s:%s ET" % (hhmm[:2], hhmm[2:])


_MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _fmt_et_datetime(date_iso: str | None, hhmm: str | None) -> str | None:
    """Scheduled first pitch as 'Sep 2 · 6:40 PM ET' -- from the Kalshi ticker's ET date + HHMM (so it renders
    even with the sports feed down). Short form (no weekday, item 4). Date-only if HHMM is missing; time-only if
    the date does not parse; None if neither is usable."""
    d = None
    if date_iso:
        try:
            dt = datetime.strptime(str(date_iso), "%Y-%m-%d")
            d = "%s %d" % (_MON[dt.month - 1], dt.day)
        except (ValueError, TypeError):
            d = None
    t = None
    s = str(hhmm or "")
    if len(s) == 4 and s.isdigit():
        h, mi = int(s[:2]), int(s[2:])
        if h <= 23 and mi <= 59:
            t = "%d:%02d %s ET" % (h % 12 or 12, mi, "AM" if h < 12 else "PM")
    if d and t:
        return "%s · %s" % (d, t)
    return d or t or None


# ── per-(ticker, wallet) copy aggregate: terminal state + realized ──────────────────────────────────────────
def _pos_aggregates(orders: list, open_by_ticker_wallet: set):
    """Aggregate the journal per (ticker, wallet): terminal state + booked realized + close timestamps, so each
    entry row and each card slot can be labelled without re-deriving. `open_by_ticker_wallet` is the set of
    (ticker, wallet) currently net-held (from live_positions_by_whale)."""
    agg: dict = {}
    for o in orders:
        key = (o.get("ticker"), o.get("wallet"))
        a = agg.setdefault(key, {"entry_contracts": 0.0, "realized": 0.0, "has_realized": False,
                                 "won": None, "settled_ts": None, "exit_ts": None, "exit_price": None,
                                 "has_settlement": False, "has_opposed": False, "has_exit": False})
        filled = (o.get("outcome_status") == "filled")
        if not o.get("is_exit"):
            if filled:
                a["entry_contracts"] += float(o.get("fill_count") or 0.0)
            continue
        # exit row
        cs = o.get("close_source")
        if cs in _SETTLE_SOURCES:
            a["has_settlement"] = True
            a["settled_ts"] = o.get("settled_ts") or o.get("response_ts")
            if o.get("won") is not None:
                a["won"] = bool(o.get("won"))
        elif cs == "opposed":
            a["has_opposed"] = True
        else:
            a["has_exit"] = True
            a["exit_ts"] = o.get("response_ts") or o.get("submitted_ts")
            a["exit_price"] = o.get("fill_price")
        if o.get("realized_pnl") is not None:
            a["realized"] += float(o.get("realized_pnl"))
            a["has_realized"] = True
    for key, a in agg.items():
        if key in open_by_ticker_wallet:
            a["state"] = "open"
        elif a["has_opposed"]:
            a["state"] = "opposed"
        elif a["has_settlement"]:
            a["state"] = "settled"
        elif a["has_exit"]:
            a["state"] = "exit"
        else:
            a["state"] = "open"          # entries with no close row yet -> still open
    return agg


# ── per-ticker card slot (aggregated across whales) ─────────────────────────────────────────────────────────
def _ticker_settlement(orders: list) -> dict:
    """Per-ticker settlement rollup for the CARD (across whales): realized sum + won + settled_ts. Only
    settlement closes (not whale-exit / opposed) count as a settled card slot."""
    out: dict = {}
    for o in orders:
        if o.get("is_exit") and o.get("close_source") in _SETTLE_SOURCES:
            t = o.get("ticker")
            r = out.setdefault(t, {"realized": 0.0, "won": None, "settled_ts": None, "contracts": 0.0})
            if o.get("realized_pnl") is not None:
                r["realized"] += float(o.get("realized_pnl"))
            if o.get("won") is not None:
                r["won"] = bool(o.get("won"))
            r["contracts"] += float(o.get("fill_count") or 0.0)   # settled quantity -> payout value on the card
            r["settled_ts"] = o.get("settled_ts") or o.get("response_ts")
    return out


def _settled_leg(orders_for_ticker) -> str | None:
    """The side we HELD into settlement, from the ENTRY fills' outcome_leg (the ticker leg we bought) -- the SAME
    yes/no direction a live slot derives from its net position. None when no filled entry records a leg, or when
    entries span BOTH legs (genuinely ambiguous) -> the settled slot then shows the line WITHOUT a fabricated
    sign, never a guessed one."""
    legs = set()
    for o in (orders_for_ticker or []):
        if not o.get("is_exit") and o.get("outcome_status") == "filled":
            leg = str(o.get("outcome_leg") or "").lower()
            if leg in ("yes", "no"):
                legs.add(leg)
    return next(iter(legs)) if len(legs) == 1 else None


def _whale_tag(whales) -> dict | None:
    """The compact 'copied from' tag for a slot that HAS a position (open OR settled -- Jack's rulings 2026-09-04:
    attribution is no longer drawer-only, and a SETTLED slot shows it too). `whales` is the ordered set of whale
    labels (display name, else wallet) for this ticker -- journal-sourced (net-open holders for a live slot; the
    entry-fill copiers for a settled slot), NEVER inferred. Returns the FIRST label plus the count of ADDITIONAL
    whales (`extra`), for a fixed-width 'First +N' render; the template right-truncates `first` with an ellipsis so
    the slot never changes shape. None only when the set is empty (an UNHELD slot -> no whale). The full untruncated
    list rides on `all` (and stays complete in the trade drawer)."""
    ws = [w for w in (whales or []) if w]
    if not ws:
        return None
    return {"first": ws[0], "extra": len(ws) - 1, "all": ws}


def _entry_whales(orders_for_ticker) -> list:
    """The distinct whales that COPIED this ticker, from its ENTRY fills (is_exit=0, filled) -- the SAME journal rows
    the drawer reads, ordered by wallet (matching live_positions_by_whale's order). Used for a SETTLED slot / settled
    positions row, whose net-open holder set is empty (the position closed) but whose entry rows still name the
    copiers. Label = user_name else wallet. Nothing inferred."""
    seen: dict = {}
    for o in (orders_for_ticker or []):
        if not o.get("is_exit") and o.get("outcome_status") == "filled":
            wal = o.get("wallet") or ""
            if wal and wal not in seen:
                seen[wal] = o.get("user_name") or wal
    return [seen[w] for w in sorted(seen)]


def _build_slot(ticker, kind, open_pos, settle, mark, settled_leg=None, whales=None):
    """One card bet-slot: open (live value = contracts x held-leg bid) or settled (won/lost). open_pos is the
    live_positions row for this ticker (or None); settle is the per-ticker settlement rollup (or None). settled_leg
    is the side held into settlement (from _settled_leg) -> a settled slot carries the SAME directional shorthand
    (over/under, spread sign+team) a live slot does; None -> the line without a sign. `whales` (journal-sourced:
    net-open holders for a live slot, entry-fill copiers for a settled slot) drives the 'copied from' tag on BOTH."""
    if open_pos is not None:
        leg = open_pos.get("held_leg")
        bid = marks_mod.bid_for_leg(mark, leg)
        contracts = open_pos.get("contracts")
        value = (contracts * bid) if (bid is not None and contracts is not None) else None
        return {"kind": kind, "kind_label": KIND_LABEL.get(kind, kind.upper()),
                "short": _short_label(ticker, kind, leg), "desc": describe_market(ticker, leg),
                "ticker": ticker, "held_leg": leg, "contracts": contracts,
                "avg_fill": open_pos.get("avg_price"), "cost": open_pos.get("cost_basis_usd"),
                "fee": open_pos.get("fees_usd"), "settled": False, "won": None, "realized": None,
                "settled_at": None, "current_value": value, "value_known": value is not None,
                "bid": bid, "whales": list(whales or []), "whale_tag": _whale_tag(whales)}
    if settle is not None:
        won = settle.get("won")
        contracts = settle.get("contracts")
        # a settled position's card value is its PAYOUT: $1.00 per contract if won, $0.00 if lost (realized P&L is
        # a distinct number, shown in the drawer). value_known only when we actually know the win/loss.
        payout = (contracts if won else 0.0) if (won is not None and contracts is not None) else None
        # a settled slot SHOWS its copied-from whale too (Jack's ruling 2026-09-04) -- from the entry-fill copiers.
        return {"kind": kind, "kind_label": KIND_LABEL.get(kind, kind.upper()),
                "short": _short_label(ticker, kind, settled_leg), "desc": describe_market(ticker, settled_leg),
                "ticker": ticker, "held_leg": settled_leg, "contracts": contracts, "avg_fill": None, "cost": None,
                "fee": None, "settled": True, "won": won, "realized": settle.get("realized"),
                "settled_at": settle.get("settled_ts"), "current_value": payout,
                "value_known": payout is not None, "bid": None,
                "whales": list(whales or []), "whale_tag": _whale_tag(whales)}
    return None


# ── the game card ────────────────────────────────────────────────────────────────────────────────────────────
def _feed_block(gs, now_ts: int) -> dict:
    """Render-ready feed block from a feed_mlb.GameState (or None -> unavailable). Live-only fields are None when
    not in progress. `age_sec` lets the template band freshness; a final game never goes stale."""
    if gs is None:
        return {"available": False, "status": "unavailable", "source": None, "age_sec": None, "started": False,
                "note": "Sports feed unavailable -- no score, inning or count is shown, because none is current."}
    live = gs.is_live
    # STARTED = the game has actually produced play (score/innings). Pre-game (preview/postponed/delayed that has
    # not begun) is NOT started, so no score digits are rendered -- 'not started' is not 'game over'. A suspended
    # game HAS started (it carries a partial score), so it counts as started.
    started = (gs.status in ("in_progress", "final", "suspended")
               or any(x is not None for x in (list(gs.linescore_away) + list(gs.linescore_home))))
    return {"available": True, "status": gs.status, "source": gs.source, "started": started,
            "age_sec": max(0, now_ts - gs.fetched_ts), "final": gs.is_final, "live": live,
            "inning": gs.inning, "half": gs.half, "outs": gs.outs, "balls": gs.balls, "strikes": gs.strikes,
            "bases": list(gs.bases) if gs.bases else [], "last_play": gs.last_play,
            "linescore_away": list(gs.linescore_away), "linescore_home": list(gs.linescore_home),
            "away": {"abbr": gs.away.abbr, "name": gs.away.name, "record": gs.away.record, "score": gs.away.score},
            "home": {"abbr": gs.home.abbr, "name": gs.home.name, "record": gs.home.record, "score": gs.home.score},
            "note": None}


def _retain_anchor_ts(slots: list, gs) -> int | None:
    """Retention anchor: the LATER of game end and the last settlement. We only have a reliable end time from a
    settlement ts (feed carries no end ts here), so anchor on the max settled_at; None if nothing settled yet."""
    tss = [s["settled_at"] for s in slots if s.get("settled") and s.get("settled_at")]
    return max(tss) if tss else None


def _ordered_teams(ticker: str):
    """(away_code, home_code) from a ticker's stem blob (away+home order), or (None, None). Ticker-derived so the
    matchup resolves even when the sports feed is down. Item 2 (2026-09-12): now works for EVERY structural sport
    with a team map (mlb/nfl/nba/nhl/wnba/cfb) via _team_map_for_ticker -- fail-closed (None) for a sport with no
    map (tennis/ufc/fed) or an ambiguous/unmapped split, so a matchup is never invented."""
    parts = str(ticker or "").split("-")
    if len(parts) < 2:
        return (None, None)
    team_map = _team_map_for_ticker(ticker)
    if team_map is None:
        return (None, None)
    m = _STEM_RE.match(parts[1])
    if not m:
        return (None, None)
    blob = m.group(3)
    gm = re.search(r"G(\d)$", blob)
    if gm:
        blob = blob[:gm.start()]
    return _split_team_blob(blob, team_map) or (None, None)


def _card(game_key, tickers, orders_by_ticker, open_by_ticker, settle_by_ticker, marks, gs, now_ts,
          whales_by_ticker=None):
    away_code, home_code = _ordered_teams(sorted(tickers)[0]) if tickers else (None, None)
    whales_by_ticker = whales_by_ticker or {}
    slots = []
    by_kind = {}
    for tk in sorted(tickers):
        kind = _kind(tk)
        # whale set: net-open holders for a live slot; the entry-fill copiers for a settled slot (its net-open set
        # is empty once closed). Both are journal-sourced -- see _entry_whales.
        whales = whales_by_ticker.get(tk) if open_by_ticker.get(tk) is not None \
            else _entry_whales(orders_by_ticker.get(tk))
        slot = _build_slot(tk, kind, open_by_ticker.get(tk),
                           settle_by_ticker.get(tk), (marks or {}).get(tk),
                           _settled_leg(orders_by_ticker.get(tk)), whales)
        if slot is not None and kind not in by_kind:   # one slot per kind on the card
            by_kind[kind] = slot
            slots.append(slot)
    n_settled = sum(1 for s in slots if s["settled"])
    n_live = sum(1 for s in slots if not s["settled"])
    open_cost = sum((s["cost"] or 0.0) for s in slots if not s["settled"])
    open_vals = [s["current_value"] for s in slots if not s["settled"]]
    value_known = any(v is not None for v in open_vals)
    open_value = sum(v for v in open_vals if v is not None) if value_known else None
    realized = sum((s["realized"] or 0.0) for s in slots if s["settled"])
    complete = bool(slots) and all(s["settled"] for s in slots)
    anchor = _retain_anchor_ts(slots, gs)
    drops_in_h = None
    if complete and anchor:
        drops_in_h = max(0, round((anchor + RETENTION_HOURS * 3600 - now_ts) / 3600.0))
    # scheduled first pitch (item 1): the ticker's ET date+HHMM is the source (renders feed-down). If the JOINED
    # feed game carries a DIFFERENT scheduled time, we show the FEED's time and flag the mismatch for the drawer --
    # never silently overriding one with the other.
    tk_date, tk_hhmm = game_key[0], game_key[1]
    feed_date = gs.date_iso if gs else None
    feed_hhmm = gs.hhmm_et if gs else None
    use_feed = bool(gs and feed_hhmm)
    start_display = _fmt_et_datetime(feed_date if use_feed else tk_date, feed_hhmm if use_feed else tk_hhmm)
    time_mismatch = None
    if gs and feed_hhmm and tk_hhmm and (feed_hhmm != tk_hhmm or (feed_date and tk_date and feed_date != tk_date)):
        time_mismatch = {"ticker": _fmt_et_datetime(tk_date, tk_hhmm),
                         "feed": _fmt_et_datetime(feed_date, feed_hhmm), "source": gs.source}
    return {"key": list(game_key), "feed": _feed_block(gs, now_ts),
            "slots_by_kind": {KIND_LABEL[k]: by_kind.get(k) for k in KINDS},
            "matchup_away": away_code, "matchup_home": home_code,
            "start_hhmm": _et_hhmm_from_key(game_key[1]), "date_iso": game_key[0],
            "start_display": start_display, "time_mismatch": time_mismatch,
            "n_settled": n_settled, "n_live": n_live, "mixed": n_settled > 0 and n_live > 0,
            "complete": complete, "drops_in_h": drops_in_h,
            "open_cost": open_cost, "open_value": open_value, "value_known": value_known,
            "realized": realized, "anchor_ts": anchor}


def _dropped(card, now_ts: int) -> bool:
    if not card["complete"] or not card["anchor_ts"]:
        return False
    return (now_ts - card["anchor_ts"]) > RETENTION_HOURS * 3600


# ── trade drawer rows (one per ENTRY fill; realized attributed pro-rata across a copy's entries) ─────────────
def _trade_rows(orders: list, agg: dict, marks: dict, slate_games: dict, mismatch_by_gk: dict, now_ts: int) -> list:
    rows = []
    for o in orders:
        if o.get("is_exit") or o.get("outcome_status") != "filled":
            continue                                  # drawer lists the COPIES (entry fills); closes fold in below
        tk = o.get("ticker")
        gk = game_key_from_ticker(tk)
        a = agg.get((tk, o.get("wallet"))) or {}
        state = a.get("state", "open")
        contracts = float(o.get("fill_count") or 0.0)
        share = (contracts / a["entry_contracts"]) if a.get("entry_contracts") else 0.0
        leg = str(o.get("outcome_leg") or "").lower()
        realized = None
        if state in ("settled", "exit") and a.get("has_realized"):
            realized = a["realized"] * share          # pro-rata this copy's share (sums to the position total)
        value_now = None
        if state == "open":
            bid = marks_mod.bid_for_leg((marks or {}).get(tk), leg)
            value_now = (contracts * bid) if bid is not None else None
        # tolerant join (match_in_slate), so the matchup resolves even when the feed's start time skews from the
        # ticker's; the per-game feed<->ticker time mismatch (item 1) rides on the row so the drawer flags it.
        gs = feed_mlb.match_in_slate(slate_games, gk[0], gk[3], gk[1], gk[2]) if (gk and slate_games) else None
        # Item 2 (2026-09-12): matchup resolves from the FEED (MLB) OR, when there is no feed, the ticker decode
        # (market_matchup) -- so a cfb/nfl drawer row names the game too, never a raw ticker. `label` = the TAGGED
        # signed shorthand ('TOT +51.5') from the ONE shared formatter, exactly as the positions table shows it.
        kind = _kind(tk)
        matchup = ("%s @ %s" % (gs.away.abbr, gs.home.abbr)) if gs else market_matchup(tk)
        short = _short_label(tk, kind, leg)
        label, _ = format_market_label(matchup, kind, short, None)
        rows.append({
            "order_id": o.get("id"), "ticker": tk, "kind": kind,
            "kind_label": KIND_LABEL.get(kind, kind.upper()),
            "desc": describe_market(tk, leg), "matchup": matchup,
            "short": short, "label": label,
            "whale_wallet": o.get("wallet"), "whale_name": o.get("user_name"),
            "whale_label": o.get("user_name") or o.get("wallet"),
            "leg": leg, "contracts": contracts,
            "submitted": o.get("submitted_price"), "fill": o.get("fill_price"), "fee": o.get("fee"),
            "entry_ts": o.get("response_ts") or o.get("submitted_ts"),
            "exit_ts": a.get("exit_ts"), "exit_price": a.get("exit_price"), "settled_ts": a.get("settled_ts"),
            "status": state, "won": a.get("won"),
            "value_now": value_now, "value_known": value_now is not None,
            "time_mismatch": mismatch_by_gk.get(gk) if gk else None,
            "realized": realized, "realized_booked": state != "opposed",
            "slippage_cents": (round((float(o["fill_price"]) - float(o["submitted_price"])) * 100)
                               if o.get("fill_price") is not None and o.get("submitted_price") is not None else None),
        })
    return rows


# ── category detection + journal totals + the non-MLB positions view ─────────────────────────────────────────
def _is_mlb_category(category, orders) -> bool:
    """Which view to render. An explicit `category` wins ('mlb' -> game cards; anything else -> a positions table).
    With NO category (older callers / unit tests that predate the arg) fall back to detecting an MLB game ticker,
    so existing MLB behaviour is byte-unchanged."""
    if category is not None:
        return str(category).strip().lower() == "mlb"
    return any(game_key_from_ticker(o.get("ticker")) is not None for o in (orders or []))


def _realized_today(orders, today_et) -> float:
    """Realized P&L booked TODAY (ET) from the SETTLEMENT-close journal rows -- category-AGNOSTIC (no sport parse),
    anchored on the close row's settled/response ts. The same rows the card path sums, so an MLB same-day
    settlement is unchanged, while a non-MLB page (no games) still gets an honest realized-today."""
    total = 0.0
    for o in (orders or []):
        if o.get("is_exit") and o.get("close_source") in _SETTLE_SOURCES and o.get("realized_pnl") is not None:
            ts = o.get("settled_ts") or o.get("response_ts")
            if ts and _et_date(ts) == today_et:
                total += float(o.get("realized_pnl"))
    return total


def _journal_summary(open_positions, orders, marks, now_ts) -> dict:
    """The NON-MLB summary strip -- TOTALS come straight from the JOURNAL / open positions, NEVER the sport parser
    (item 1): at-cost, unsettled position count, current value + coverage, realized-today. There is no game feed
    for these categories, so the game cells are 0 and `has_game_feed` is False -> the 'games held' cell renders the
    honest 'no game feed · N open positions' alternative. `settled_today` counts SETTLEMENT-close rows dated today
    (the same rows realized-today sums), so the count and the P&L are consistent for a category with no cards."""
    val = value_positions(open_positions or [], marks)
    unsettled_cost = sum(float(p.get("cost_basis_usd") or 0.0) for p in (open_positions or []))
    today = _et_date(now_ts)
    settled_today = sum(1 for o in (orders or [])
                        if o.get("is_exit") and o.get("close_source") in _SETTLE_SOURCES
                        and _et_date(o.get("settled_ts") or o.get("response_ts")) == today)
    return {
        # game-level (no game feed for a non-MLB category -> 0)
        "n_active": 0, "n_complete": 0, "settled_today": settled_today, "has_game_feed": False,
        # position-level totals (from the journal / open positions -- the parser never touches these)
        "n_open_positions": len(open_positions or []),
        "unsettled_cost": unsettled_cost,
        "unsettled_value": val["value"], "unsettled_value_known": val["known"],
        "unsettled_priced": val["n_priced"], "unsettled_total": val["n_total"],
        "realized_today": _realized_today(orders or [], today),
    }


def _base_label(ticker, kind, category) -> str:
    """The always-present base label for a non-MLB row -- NEVER a raw ticker (Item 3.1 floor). Used ONLY when the
    ticker has no two-team matchup (tennis/ufc/fed) or the decode fails closed. "<CATEGORY> <MARKET-TYPE>" for a
    KNOWN market type ("CFB TOT"); the bare "<CATEGORY>" otherwise -- because `_kind`'s fallback for a
    non-ML/TOT/SPR series is the raw series ("kxatpmatch"), and appending its upper-case would LEAK 'KX' into the
    very label this floor exists to keep ticker-free (Item 2, 2026-09-12: found by the tennis cold-cache test)."""
    cat = str(category or "").upper()
    if kind in KIND_LABEL:
        return ("%s %s" % (cat, KIND_LABEL[kind])).strip()
    return cat or "MARKET"


# ── Item 2 (2026-09-12): the ONE canonical structural-market label (matchup + signed shorthand). ONE implementation
# used by the non-MLB positions table, the trade drawer's market column, AND the Farm whale paper-trade list -- no
# second copy. FAIL CLOSED everywhere: a ticker with no two-team map (tennis/ufc/fed) or an ambiguous split yields
# matchup=None, and the caller shows the honest single label, never an invented matchup or a raw ticker. ──────────
def market_matchup(ticker) -> str | None:
    """'AWAY @ HOME' (team codes, away+home Kalshi convention) for a structural two-team ticker, or None (fail-closed
    for tennis/ufc/fed and any ambiguous/unmapped blob). Ticker-derived -> resolves without a sports feed."""
    a, h = _ordered_teams(ticker)
    return ("%s @ %s" % (a, h)) if (a and h) else None


def structural_game_key(ticker):
    """A stable per-GAME key for grouping a structural sub-division's rows: (matchup, event-date) so a game's
    moneyline/total/spread rows (different market-type prefixes, same stem) group under ONE header. None -> the row
    has no decodable game (tennis/ufc/fed) and stays ungrouped."""
    mu = market_matchup(ticker)
    if mu is None:
        return None
    parts = str(ticker or "").split("-")
    m = _STEM_RE.match(parts[1]) if len(parts) > 1 else None
    date_iso = kalshi_to_iso_date(m.group(1)) if m else None
    return (mu, date_iso)


def format_market_label(matchup, kind, short, title):
    """The SHARED formatter (Ruling: same rule on the positions table, the drawer, and the Farm paper list). Returns
    (primary, secondary): PRIMARY is the signed shorthand with the market-type tag ('SPR -6.5 MIZZ'); SECONDARY is
    the Kalshi/Poly title (enrichment), or the matchup when there is no title. NEVER a raw ticker."""
    tag = KIND_LABEL.get(kind, (kind or "").upper()) if kind else ""
    primary = ("%s %s" % (tag, short)).strip() if short else (tag or "market")
    secondary = title or matchup or None
    return (primary, secondary)


def poly_market_label(category, slug, outcome, title=None):
    """The SAME label rule as the Kalshi positions table + trade drawer, applied to a Polymarket PAPER bet (the Farm
    whale paper-trade list). ONE formatter (format_market_label); the only difference is the SOURCE decode -- a Poly
    slug is parsed by the engine's canonical `parse_poly_bet` (data-side, no re-implementation) instead of a Kalshi
    ticker. Returns (matchup, primary, secondary) or (None, None, None) FAIL-CLOSED for a non-structural category
    (tennis/ufc/cs2/soccer -- no LEAGUES entry), a prop/unparseable slug, or an unresolved side -> the caller keeps
    its honest title/slug display, never an invented matchup. matchup + shorthand are ticker-free by construction."""
    cfg = _STRUCT_LEAGUES.get(str(category or "").lower())
    if cfg is None:                                        # tennis/ufc/cs2/soccer/... -> no two-team structural decode
        return (None, None, None)
    pb = _parse_poly_bet(slug or "", outcome or "", cfg, title)
    if pb.market_type not in KINDS or pb.away_code is None or pb.home_code is None:
        return (None, None, None)                          # non_moneyline/non_sport/unparseable -> honest fallback
    if pb.away_name is None or pb.home_name is None:       # a code off the map -> no guessed matchup
        return (None, None, None)
    matchup = "%s @ %s" % (pb.away_code, pb.home_code)     # away+home, the same convention as market_matchup
    short = None
    if pb.market_type == "moneyline":
        short = pb.away_code if pb.side == "away" else pb.home_code if pb.side == "home" else None
    elif pb.market_type == "total" and pb.line is not None and pb.leg in ("yes", "no"):
        short = ("+" if pb.leg == "yes" else "-") + ("%.1f" % pb.line)   # Over(YES)=+line / Under(NO)=-line
    elif pb.market_type == "spread" and pb.line is not None and pb.leg in ("yes", "no") and pb.anchor_side:
        anchor = pb.away_code if pb.anchor_side == "away" else pb.home_code
        other = pb.home_code if pb.anchor_side == "away" else pb.away_code
        strike = "%.1f" % pb.line
        short = ("-%s %s" % (strike, anchor)) if pb.leg == "yes" else ("+%s %s" % (strike, other))
    primary, secondary = format_market_label(matchup, pb.market_type, short, title)
    return (matchup, primary, secondary)


def _positions_view(orders, open_by_ticker, settle_by_ticker, agg, marks, whales_by_ticker,
                    titles=None, category=None, now_ts=None) -> dict:
    """The non-MLB category view: a positions TABLE (active = open; complete = settled/exit/opposed). Each row's
    human name (`desc`) comes from the PERSISTED title map (Item 3.1: survives a failed/partial poll) else the
    category+market-type base label (Item 3.1 floor -- NEVER describe_market's '<type>:<ticker>', which embedded the
    raw ticker). An open row carries the held-leg BID value + the mark's own `as_of`/`age_sec` so the template bands
    it amber past the stale threshold; `ever_priced` is True when a title has EVER resolved for the ticker (so 'no
    mark' shows ONLY for a ticker that has never returned a bid, not for one whose latest poll dropped it)."""
    titles = titles or {}
    active, complete = [], []
    all_tickers = {o.get("ticker") for o in (orders or []) if o.get("ticker")}
    for tk in sorted(all_tickers):
        kind = _kind(tk)
        whales = whales_by_ticker.get(tk, [])
        ptitle = titles.get(tk)                          # PERSISTED title (never evicted) -- survives a failed poll
        mu = market_matchup(tk); gkey = structural_game_key(tk)   # Item 2: ticker-derived matchup + game grouping
        op = open_by_ticker.get(tk)
        if op is not None:
            leg = op.get("held_leg"); mk = (marks or {}).get(tk)
            bid = marks_mod.bid_for_leg(mk, leg); contracts = op.get("contracts")
            value = (contracts * bid) if (bid is not None and contracts is not None) else None
            mk_as_of = getattr(mk, "as_of", None) if mk is not None else None
            age_sec = (int(now_ts) - int(mk_as_of)) if (now_ts is not None and mk_as_of is not None) else None
            short = _short_label(tk, kind, leg)
            primary, secondary = format_market_label(mu, kind, short, ptitle)   # Item 2: shorthand FIRST, title second
            active.append({"ticker": tk, "kind": kind, "kind_label": KIND_LABEL.get(kind, kind.upper()),
                           "desc": primary if mu else (ptitle or _base_label(tk, kind, category)), "sub": secondary,
                           "short": short, "matchup": mu, "game_key": gkey, "market_title": ptitle,
                           "held_leg": leg, "contracts": contracts, "cost": op.get("cost_basis_usd"),
                           "avg_fill": op.get("avg_price"), "fee": op.get("fees_usd"), "current_value": value,
                           "value_known": value is not None, "bid": bid, "as_of": mk_as_of, "age_sec": age_sec,
                           "ever_priced": bool(ptitle) or value is not None, "settled": False, "status": "open",
                           "whales": whales, "whale_tag": _whale_tag(whales)})
            continue
        # not open -> a terminal (settled / exit / opposed) row, if any close exists for it
        settle = settle_by_ticker.get(tk)
        # per-ticker terminal state: prefer a settlement; else the recorded per-(ticker,wallet) state
        states = {a.get("state") for (t, _w), a in agg.items() if t == tk}
        if settle is None and not (states & {"exit", "opposed", "settled"}):
            continue                                # no live/settled position (all off the books) -> not a row
        tk_orders = [o for o in (orders or []) if o.get("ticker") == tk]
        settled_leg = _settled_leg(tk_orders)
        won = settle.get("won") if settle else None
        contracts = settle.get("contracts") if settle else None
        payout = (contracts if won else 0.0) if (won is not None and contracts is not None) else None
        status = "settled" if settle is not None else ("opposed" if "opposed" in states else "exit")
        # a settled/closed row shows its copied-from whale too (Jack 2026-09-04) -- from the entry-fill copiers,
        # since the net-open holder set is empty once the position closed.
        cw = _entry_whales(tk_orders)
        short = _short_label(tk, kind, settled_leg)
        primary, secondary = format_market_label(mu, kind, short, ptitle)
        complete.append({"ticker": tk, "kind": kind, "kind_label": KIND_LABEL.get(kind, kind.upper()),
                         "desc": primary if mu else (ptitle or _base_label(tk, kind, category)), "sub": secondary,
                         "short": short, "matchup": mu, "game_key": gkey, "market_title": ptitle,
                         "held_leg": settled_leg,
                         "contracts": contracts, "cost": None, "avg_fill": None, "fee": None,
                         "current_value": payout, "value_known": payout is not None, "bid": None, "settled": True,
                         "won": won, "realized": (settle.get("realized") if settle else None),
                         "settled_at": (settle.get("settled_ts") if settle else None),
                         "status": status, "whales": cw, "whale_tag": _whale_tag(cw)})
    active.sort(key=lambda r: r["ticker"]); complete.sort(key=lambda r: r["ticker"])
    return {"active": active, "complete": complete, "n_active": len(active), "n_complete": len(complete),
            "active_groups": _group_by_game(active), "complete_groups": _group_by_game(complete)}


def _group_by_game(rows) -> list:
    """Group structural-sport rows BY GAME (Item 2.1): one entry per matchup, in first-seen order, so a game's
    moneyline/total/spread rows sit together under an 'AWAY @ HOME - date - start|unavailable' header. A row with no
    matchup (tennis/ufc/fed, or a fail-closed decode) is its OWN header-less group (single row, no invented game).
    start = the ticker's HHMM where it carries one (date-only sports -> None -> the template says 'start time
    unavailable'); date is from the game key."""
    groups: list = []
    by_key: dict = {}
    for r in rows:
        gk = r.get("game_key")
        if gk is None:                                   # tennis/ufc/fed -> ungrouped single row (no game header)
            groups.append({"matchup": None, "date": None, "start": None, "rows": [r]})
            continue
        g = by_key.get(gk)
        if g is None:
            start = parse_ticker_start(_category_hint(r.get("ticker")), r.get("ticker"))
            g = by_key[gk] = {"matchup": gk[0], "date": gk[1], "start": start, "rows": []}
            groups.append(g)
        g["rows"].append(r)
    return groups


def _category_hint(ticker) -> str:
    """The lower-case category implied by a Kalshi ticker's sport prefix (for parse_ticker_start's LIVE_CAPABLE
    gate). NCAAF->cfb; else the sport token lower-cased (mlb/nfl/nba/nhl/wnba). None-safe."""
    series = str(ticker or "").split("-", 1)[0].upper()
    body = series[2:] if series.startswith("KX") else series
    for sport in sorted(_SPORT_TEAM_MAP, key=len, reverse=True):
        if body.startswith(sport):
            return "cfb" if sport == "NCAAF" else sport.lower()
    return ""


# ── top-level assembly ───────────────────────────────────────────────────────────────────────────────────────
def build_live_context(*, orders: list, open_positions: list, open_positions_by_whale: list,
                       slate, marks_result, now_ts: int, category: str | None = None,
                       titles: dict | None = None, poll_status: dict | None = None) -> dict:
    """Pure assembly: journal rows + open positions + a feed SlateResult + a MarksResult -> the template context.
    No DB, no network -- the route fetches those and passes them in (so this unit-tests directly).

    ★ MULTI-CATEGORY (2026-09-04): the SUMMARY totals (at-cost / count / value+coverage / realized-today) come from
    the JOURNAL for EVERY category -- they NEVER depend on the sport parser, so a non-MLB sub-division no longer shows
    0 while its drawer holds a trade. MLB still renders game CARDS; a non-MLB category renders a positions TABLE
    (`mode`). MLB is byte-unchanged (same card path, same summary values -- locked by test_live_view_mlb_regression)."""
    marks = (marks_result.marks if marks_result is not None else {}) or {}
    slate_games = (slate.games if slate is not None else {}) or {}
    open_by_ticker = {p["ticker"]: p for p in (open_positions or [])}
    open_by_ticker_wallet = {(p["ticker"], p["wallet"]) for p in (open_positions_by_whale or [])}
    settle_by_ticker = _ticker_settlement(orders or [])
    agg = _pos_aggregates(orders or [], open_by_ticker_wallet)
    whales_by_ticker: dict = {}
    for p in (open_positions_by_whale or []):
        whales_by_ticker.setdefault(p["ticker"], []).append(p.get("user_name") or p.get("wallet"))

    is_mlb = _is_mlb_category(category, orders)
    cards: list = []
    positions_view = None
    mismatch_by_gk: dict = {}

    if is_mlb:
        # ── MLB game-cards view (UNCHANGED, item 6) ──────────────────────────────────────────────────────────
        # group tickers by game
        tickers_by_game: dict = {}
        orders_by_ticker: dict = {}
        for o in (orders or []):
            tk = o.get("ticker")
            if not tk:
                continue
            orders_by_ticker.setdefault(tk, []).append(o)
            gk = game_key_from_ticker(tk)
            if gk is not None:
                tickers_by_game.setdefault(gk, set()).add(tk)
        for gk, tks in tickers_by_game.items():
            gs = feed_mlb.match_in_slate(slate_games, gk[0], gk[3], gk[1], gk[2]) if slate_games else None
            card = _card(gk, tks, orders_by_ticker, open_by_ticker, settle_by_ticker, marks, gs, now_ts,
                         whales_by_ticker)
            if card["time_mismatch"]:                    # per-game feed<->ticker start-time skew -> flagged in the drawer
                mismatch_by_gk[gk] = card["time_mismatch"]
            # INTENTIONAL (board-accepted 2026-09-02, fix-pass item 7 -- do NOT "fix" this back): a game whose
            # positions are ALL off the books draws no empty "not held" card. Its trades still appear in the drawer.
            if (card["n_settled"] + card["n_live"]) == 0:
                continue
            if not _dropped(card, now_ts):
                cards.append(card)
        cards.sort(key=lambda c: (c["complete"], c["start_hhmm"] or ""))

        n_active = sum(1 for c in cards if not c["complete"])
        n_complete = len(cards) - n_active
        live_cost = sum(c["open_cost"] for c in cards)
        live_val_known = any(c["value_known"] for c in cards)
        live_val = sum((c["open_value"] or 0.0) for c in cards if c["value_known"]) if live_val_known else None
        # mark COVERAGE (always shown, per Jack): how many OPEN bet-slots across the board have a bid vs the total.
        open_slots = [s for c in cards for s in c["slots_by_kind"].values() if s and not s["settled"]]
        unsettled_total = len(open_slots)
        unsettled_priced = sum(1 for s in open_slots if s["value_known"])
        today = _et_date(now_ts)
        realized_today = sum(c["realized"] for c in cards if c["date_iso"] == today)
        settled_today = sum(c["n_settled"] for c in cards if c["date_iso"] == today)
        summary = {"n_active": n_active, "n_complete": n_complete, "unsettled_cost": live_cost,
                   "unsettled_value": live_val, "unsettled_value_known": live_val_known,
                   "unsettled_priced": unsettled_priced, "unsettled_total": unsettled_total,
                   "realized_today": realized_today, "settled_today": settled_today,
                   # additive-only (item 1/2): the two keys the shared template also reads on a non-MLB page.
                   "has_game_feed": True, "n_open_positions": len(open_positions or [])}
    else:
        # ── non-MLB positions view (item 2): a positions table + journal totals that NEVER use the sport parser ─
        positions_view = _positions_view(orders or [], open_by_ticker, settle_by_ticker, agg, marks,
                                         whales_by_ticker, titles=titles, category=category, now_ts=now_ts)
        summary = _journal_summary(open_positions or [], orders or [], marks, now_ts)

    trades = _trade_rows(orders or [], agg, marks, slate_games, mismatch_by_gk, now_ts)

    # MARK-POLL STATUS (Item 3.3): distinct from the SPORTS-feed status. `marks_ok` is THIS poll's result; a failed or
    # partial mark poll (marks_ok False / mark_error set) drives the "refresh failed Nm ago - showing last mark" note
    # beside the coverage label. `mark_age_sec` is the age of the last refresh. Defaults from marks_result when the
    # caller (a direct unit test) does not pass a snapshot-derived poll_status.
    ps = poll_status or {}
    marks_ok = ps.get("marks_ok", (marks_result.ok if marks_result is not None else False))
    mark_refreshed_ts = ps.get("refreshed_ts")
    mark_error = ps.get("last_error", (marks_result.error if marks_result is not None else None))
    mark_age_sec = (int(now_ts) - int(mark_refreshed_ts)) if mark_refreshed_ts is not None else None

    return {
        "mode": "mlb_cards" if is_mlb else "positions",
        "category": category,
        "cards": cards,
        "positions_view": positions_view,
        "summary": summary,
        "trades": trades,
        "feed_meta": {"ready": slate is not None, "source": (slate.source if slate else None),
                      "ok": (slate.ok if slate else False),
                      "marks_ok": marks_ok,
                      "as_of": (slate.as_of if slate else None), "has_game_feed": is_mlb},
        "mark_status": {"ok": marks_ok, "error": mark_error, "refreshed_ts": mark_refreshed_ts,
                        "age_sec": mark_age_sec},
        "poll_interval": POLL_INTERVAL_SECONDS, "retention_hours": RETENTION_HOURS,
    }


def value_positions(positions, marks) -> dict:
    """Value a set of open positions at contracts x held-leg BID using the cached marks. Honest about coverage:
    `value` sums only the positions we have a mark for, `complete` is True only when EVERY open position is
    priced, `known` is True when at least one is. The caller shows the value with a 'partial' caveat when
    n_priced < n_total, or 'no mark' when nothing is priced -- never a $0 that means 'unpriced'."""
    marks = marks or {}
    total = len(positions or [])
    priced = 0
    val = 0.0
    for p in (positions or []):
        bid = marks_mod.bid_for_leg(marks.get(p.get("ticker")), p.get("held_leg"))
        if bid is not None and p.get("contracts") is not None:
            priced += 1
            val += p["contracts"] * bid
    return {"value": val if priced else None, "n_priced": priced, "n_total": total,
            "complete": total > 0 and priced == total, "known": priced > 0}


def build_from_cache(*, orders, open_positions, open_positions_by_whale, cache, now_ts: int,
                     category: str | None = None) -> dict:
    """Convenience wrapper: pull the relevant slate(s) + marks from the ui_cache and assemble. The cards can span
    two ET dates (a night game + retention), so we merge both windowed slates' games into one lookup. `category`
    is threaded through so a non-MLB sub-division renders its positions view instead of the (empty) MLB cards."""
    snap = cache.snapshot()
    merged_games = {}
    for slate in snap.slates.values():
        merged_games.update(slate.games)
    # a synthetic SlateResult carrying the merged game map + the freshest source/as_of
    src = next((s.source for s in snap.slates.values() if s.ok), None)
    merged = feed_mlb.SlateResult(_et_date(now_ts) or "", merged_games, bool(merged_games), src,
                                  snap.refreshed_ts)
    # Item 3.3: hand the render the PERSISTED titles + the mark-poll status (ok/error/refresh-age) off the snapshot,
    # so a failed/partial poll shows "refresh failed - showing last mark" and never blanks a known name.
    poll_status = {"marks_ok": (snap.marks.ok if snap.marks is not None else False),
                   "refreshed_ts": snap.refreshed_ts, "last_error": snap.last_error}
    return build_live_context(orders=orders, open_positions=open_positions,
                              open_positions_by_whale=open_positions_by_whale, slate=merged,
                              marks_result=snap.marks, now_ts=now_ts, category=category,
                              titles=snap.titles, poll_status=poll_status) | {"warming": not snap.ready}


# ── LIVE SUB-DIVISIONS TILE PAGE (Phase 2, 2026-09-07) ─────────────────────────────────────────────────────────
# A PURE assembler (no DB / no network): the app loader fetches the journal + arm + heartbeat + mark-cache data and
# hands it here to build the segmented tile context. Testable in isolation. Honesty rules preserved: realized is
# NEVER open value; open shows THREE separate figures (count / at-cost / current value + 'N of M priced'); arm and
# liveness are shown SEPARATELY (arm = should it trade; liveness = is the engine actually evaluating it); an ARMED
# sub whose driver is STALE/NEVER is the divergence R1 exists to surface -> it rides the page-top alarm strip.

_LV_ALARM_STATES = ("STALE", "NEVER")


def _ts_age(iso_ts, now_ts):
    """Age (seconds) of an ISO8601 agent_state write-timestamp vs now_ts, for the arm badge's age chip. None when
    absent/unparseable (the badge then draws no chip). Pure."""
    if not iso_ts:
        return None
    try:
        import datetime as _dt
        return int(now_ts) - int(_dt.datetime.fromisoformat(str(iso_ts)).timestamp())
    except Exception:
        return None


# ── LIVE SUB-DIVISIONS REDESIGN (Claude Design port, 2026-09-10) ───────────────────────────────────────────────
# A per-account tile page: activity state (LIVE/UPCOMING/SETTLED/INACTIVE/UNATTACHED), ET-calendar money windows,
# a LIVE event block, per-account tabs (scoped by viewer). PURE assembler -- the loader fetches journal/arm/
# heartbeat/mark/feed data and hands it here. Honesty rules preserved: realized is NEVER open value; cost and
# current value are distinct keys; an unpriced position is "no mark", never $0; arm and liveness are separate; an
# armed sub whose driver reads STALE/NEVER rides the page-top alarm strip; a name is NEVER a raw ticker (R2).

_ET = ZoneInfo("America/New_York")

# Category -> sport family (the design's MOCK.sports display metadata). A code absent here renders a monogram.
# Extend to a new league = one entry + static/logos/<CODE>.png. LIVE_CAPABLE = categories that can honestly know
# an event has STARTED: MLB (game feed) + the ones whose ticker carries an HHMM start (inventory item 10).
SPORTS = {
    "mlb": "Baseball", "atp": "Tennis", "wta": "Tennis", "ufc": "MMA", "nfl": "Football",
    "cfb": "College football", "nba": "Basketball", "wnba": "Basketball", "nhl": "Hockey",
    "cs2": "Esports", "epl": "Soccer", "ucl": "Soccer", "uel": "Soccer", "lal": "Soccer",
    "fl1": "Soccer", "sea": "Soccer", "bun": "Soccer", "mls": "Soccer", "bra": "Soccer",
    "mex": "Soccer", "fed": "Rate decisions",
}
# Categories whose Kalshi ticker CAN carry an HHMM start right after the date -- parse_ticker_start tries this
# source FIRST for all of them. mlb + cs2 carry it RELIABLY (mlb also has a game feed); the STRUCTURAL sports
# (nfl/cfb/wnba/nba/nhl) carry it only SOMETIMES -- in practice their tickers are date+teams with NO HHMM (measured
# 2026-09-12: 0/794 cfb, 0/60 nfl), so they fall back to the milestone index (see MILESTONE_START_CATEGORIES).
# ★★ INTERDEPENDENCE -- READ THIS LIST AND MILESTONE_START_CATEGORIES TOGETHER: a structural category is in
# LIVE_CAPABLE for the HHMM-FIRST attempt AND must ALSO be milestone-eligible below; a structural category dropped
# from BOTH lists is served by NEITHER source and reads UPCOMING while underway (the exact cfb/nfl defect fixed
# 2026-09-12). Do NOT edit one list without checking the other.
LIVE_CAPABLE = frozenset({"mlb", "cs2", "nfl", "nba", "nhl", "wnba", "cfb"})
# HHMM/feed-AUTHORITATIVE: reliably carry an HHMM (cs2) or have a game feed (mlb) -> NEVER a milestone fallback
# (a milestone must not bypass the mlb feed; an mlb ticker with no HHMM stays UNKNOWN, not borrowed from a milestone).
# ★ A NEW HHMM/feed sport added to LIVE_CAPABLE MUST also be added HERE -- otherwise it auto-becomes milestone-
# eligible below (benign, since HHMM-first still wins for a reliably-HHMM sport, but not the intent). The default
# polarity is deliberate: a new STRUCTURAL sport added to LIVE_CAPABLE alone becomes milestone-eligible = CORRECT.
_HHMM_AUTHORITATIVE = frozenset({"mlb", "cs2"})
# Categories eligible for the Kalshi MILESTONE start FALLBACK (2026-09-12): the date-only sports with no ticker HHMM
# and no feed -- tennis (atp/wta), MMA (ufc) and the soccer leagues -- PLUS the STRUCTURAL sports
# (= LIVE_CAPABLE minus the HHMM/feed-authoritative mlb+cs2 = nfl/cfb/wnba/nba/nhl), whose tickers carry no HHMM in
# practice so their HHMM-first attempt yields nothing. ★★ start_ts_for_ticker tries HHMM FIRST, so this is a pure
# FALLBACK: a structural game that DOES carry an HHMM still uses it, and mlb/cs2 (excluded here) stay HHMM/feed-
# authoritative -- so a stray/mis-swept milestone can never override the mlb feed or a real cs2 HHMM. fed is in
# NEITHER list -> no start state. Derived from SPORTS + LIVE_CAPABLE so a new soccer league (in SPORTS) or a new
# structural sport (in LIVE_CAPABLE) is covered automatically -- see the INTERDEPENDENCE note on LIVE_CAPABLE above.
MILESTONE_START_CATEGORIES = frozenset(
    {"atp", "wta", "ufc"} | {c for c, v in SPORTS.items() if v == "Soccer"} | (LIVE_CAPABLE - _HHMM_AUTHORITATIVE))
# Coarse categories retired for finer ones (R7): a sub on one can never trade -> the dashed orphan tile.
RETIRED_CATEGORIES = frozenset({"soccer"})
_ARM_DISPLAY = {"armed": "ARMED", "disarmed": "DISARMED", "absent": "NEVER ARMED", "unavailable": "STATE UNAVAILABLE"}
_ACTIVITY_ORDER = {"LIVE": 0, "UPCOMING": 1, "SETTLED": 2, "INACTIVE": 3, "UNATTACHED": 4}
_MIDDOT = "·"


def sport_family(category) -> str:
    return SPORTS.get(str(category or "").lower(), "-")


def et_window_cutoffs(now_ts: int) -> dict:
    """UNIX start of the current ET CALENDAR today / week / month (R4: calendar periods, NOT rolling). Week starts
    MONDAY 00:00 ET (ISO); flipping to Sunday is a one-line change. DST-correct: boundaries are built in
    America/New_York then converted to UNIX, so a 23h/25h DST day still starts at ET midnight."""
    et = datetime.fromtimestamp(int(now_ts), tz=timezone.utc).astimezone(_ET)
    today = et.replace(hour=0, minute=0, second=0, microsecond=0)
    week = today - timedelta(days=today.weekday())
    month = today.replace(day=1)
    return {"today": int(today.timestamp()), "week": int(week.timestamp()), "month": int(month.timestamp())}


_START_RE = re.compile(r"^KX[A-Z0-9]+-(\d{2}[A-Z]{3}\d{2})(\d{4})")


def parse_ticker_start(category, ticker) -> int | None:
    """Unix ts of the event start ENCODED IN THE TICKER (YYMONDD + HHMM, ET) or None (inventory item 10). Only
    Only LIVE_CAPABLE categories are checked for an HHMM (mlb/cs2 carry it reliably; the structural sports only
    sometimes). Date-only sports (tennis/ufc/soccer/fed) are not LIVE_CAPABLE -> None here (they take the milestone/
    none path in start_ts_for_ticker). A structural ticker that OMITS the HHMM (cfb/nfl in practice) -> None HERE,
    then start_ts_for_ticker falls back to the Kalshi milestone (2026-09-12); mlb with no HHMM stays UNKNOWN (feed)."""
    if str(category or "").lower() not in LIVE_CAPABLE:
        return None
    m = _START_RE.match(str(ticker or "").upper())
    if not m:
        return None
    iso, hhmm = kalshi_to_iso_date(m.group(1)), m.group(2)
    if not iso or not hhmm:
        return None
    try:
        y, mo, d = (int(x) for x in iso.split("-"))
        h, mi = int(hhmm[:2]), int(hhmm[2:])
        if h > 23 or mi > 59:
            return None
        return int(datetime(y, mo, d, h, mi, tzinfo=_ET).timestamp())
    except (ValueError, TypeError):
        return None


def start_ts_for_ticker(category, ticker, starts=None) -> int | None:
    """The best available START time (unix) for a held ticker. FIRST the ticker's own HHMM where it carries one
    (LIVE_CAPABLE); ELSE, for a MILESTONE_START_CATEGORIES category, the Kalshi milestone start for its event ticker.
    HHMM-FIRST, so a game that carries an HHMM uses it; the milestone FALLBACK then serves the date-only sports
    (tennis/ufc/soccer) AND the STRUCTURAL sports (cfb/nfl/wnba/nba/nhl) whose tickers carry no HHMM (2026-09-12 fix).
    mlb + cs2 are HHMM/feed-AUTHORITATIVE and NEVER borrow a milestone (an mlb ticker with no HHMM stays UNKNOWN rather
    than bypassing the feed); fed is excluded entirely. None -> start UNKNOWN -> the caller stays honest (UPCOMING); a
    T00:00:00Z placeholder milestone is already None (so a placeholder reads time-unknown, never midnight)."""
    st = parse_ticker_start(category, ticker)
    if st is not None:
        return st
    if str(category or "").lower() in MILESTONE_START_CATEGORIES:
        return milestones_mod.start_for_event_ticker(starts, ticker)
    return None


def name_market(ticker, leg, mark, feed_game, category) -> tuple:
    """A MEANINGFUL market name (R2), NEVER a raw ticker. Priority: MLB feed matchup ("NYY @ BAL") -> cached Kalshi
    market title (Mark.title) -> market_describe (MLB) -> "<CATEGORY> <market type>". Returns (name, named_ok);
    named_ok is False ONLY on the last fallback, so the caller can log the R2 exception."""
    if feed_game is not None and getattr(feed_game, "away", None) and getattr(feed_game, "home", None):
        a, h = feed_game.away.abbr, feed_game.home.abbr
        if a and h:
            return ("%s @ %s" % (a, h), True)
    title = getattr(mark, "title", None) if mark is not None else None
    if title:
        return (str(title), True)
    md = describe_market(ticker, leg)
    if md and md != "-" and str(ticker or "") not in md:      # describe_market's fallback embeds the ticker -> skip
        return (md, True)
    kind = _kind(ticker)
    mtype = kind if kind in KINDS else "market"
    return ("%s %s" % (str(category or "").upper(), mtype), False)


def _event_underway(category, tickers, feed_games, marks, now_ts, starts=None) -> bool:
    """Is an event this sub holds a position on CURRENTLY underway? MLB (with a feed): AUTHORITATIVE -- the feed says
    in_progress for a held game (the feed knows in-progress vs final, which a start time alone cannot). Everything
    else: a held ticker's START has passed AND the market is not finalized, where the start comes from the ticker's
    HHMM (LIVE_CAPABLE) or the Kalshi milestone (the cross-category feed -- tennis/ufc/soccer). An UNKNOWN start
    (date-only ticker with no milestone, or a placeholder) -> stays UPCOMING (honest -- never a fabricated LIVE).
    NOTE: with no live scores in scope, "underway" means STARTED-and-still-held; a game that has ended but not yet
    settled reads LIVE until we settle it -- the honest limit of a start-times-only feed."""
    cat = str(category or "").lower()
    if cat == "mlb" and feed_games:
        for t in tickers:
            gk = game_key_from_ticker(t)
            if gk is not None:
                gs = feed_mlb.match_in_slate(feed_games, gk[0], gk[3], gk[1], gk[2])
                if gs is not None and gs.is_live:
                    return True
        return False
    for t in tickers:
        st = start_ts_for_ticker(cat, t, starts)
        if st is not None and st <= int(now_ts) and getattr((marks or {}).get(t), "status", None) != "finalized":
            return True
    return False


def _score_detail(gs) -> str | None:
    """'<HALF> <inning> DOT <outs> out DOT <balls>-<strikes>' for a live MLB game, guarding the None fields at an
    inning break (MID/END have no outs/count)."""
    if gs is None or gs.inning is None:
        return None
    bits = []
    if gs.half:
        bits.append("%s %d" % (gs.half, gs.inning))
    if gs.outs is not None:
        bits.append("%d out" % gs.outs)
    if gs.balls is not None and gs.strikes is not None:
        bits.append("%d-%d" % (gs.balls, gs.strikes))
    return (" %s " % _MIDDOT).join(bits) if bits else None


def _event_rows(positions, marks):
    """The held positions as named bet rows (kind label + terse market + current value at bid), for the LIVE
    event block. Open positions are 'live' (valued at bid); cost is never shown as value."""
    rows = []
    for p in (positions or []):
        tk, leg = p.get("ticker"), p.get("held_leg")
        mk = (marks or {}).get(tk)
        kind = _kind(tk)
        bid = marks_mod.bid_for_leg(mk, leg)
        contracts = p.get("contracts")
        value = (contracts * bid) if (bid is not None and contracts is not None) else None
        rows.append({"kind": KIND_LABEL.get(kind, (kind or "").upper()[:3] or "-"),
                     "market": _short_label(tk, kind, leg),
                     "value": value, "value_known": value is not None, "state": "live"})
    return rows


def _live_event(category, positions, feed_games, marks, now_ts, starts=None):
    """The LIVE tile's event block -- ONE compact row PER UNDERWAY GAME the sub holds a position on. FIX
    (2026-09-11): each row carries that game's scoreboard (MLB) or market label (non-MLB) and ONLY that game's held
    positions -- the prior version attached EVERY open position to a single underway game (jack/mlb showed a PHI
    position on the TB@ATL block). Positions are GROUPED by the same ticker->game join the card page uses
    (game_key_from_ticker); a position whose ticker joins no game is OMITTED, never guessed onto one (FIX 3) -- it
    stays counted on the OPEN line. Positions on games that are NOT underway are also not in the block. Rows are
    ordered most-recently-started first, capped at 3, with `more` = the overflow ('+N more live' -> detail page).
    The held ML team is marked in the score line (away_ours/home_ours) so who we're cheering for is unmistakable.
    Non-MLB rows carry a market label + positions (no scoreboard -- scores are out of scope); the start comes from
    the ticker HHMM or the Kalshi milestone (2026-09-12), so tennis/ufc/soccer now populate the block once underway.
    Returns None if no underway game."""
    if not positions:
        return None
    cat = str(category or "").lower()
    groups: dict = {}                                  # group key -> {start, gs, positions, ticker}
    for p in positions:
        tk, leg = p.get("ticker"), p.get("held_leg")
        if cat == "mlb":
            gk = game_key_from_ticker(tk)
            if gk is None:
                continue                               # FIX 3: unjoinable ticker -> omit, never attach to a game
            gs = feed_mlb.match_in_slate(feed_games, gk[0], gk[3], gk[1], gk[2]) if feed_games else None
            if gs is None or not gs.is_live:
                continue                               # only UNDERWAY games ride the block
            g = groups.get(gk)
            if g is None:
                g = groups[gk] = {"start": parse_ticker_start(cat, tk) or 0, "gs": gs, "positions": [], "ticker": tk}
            g["positions"].append(p)
        else:
            st = start_ts_for_ticker(cat, tk, starts)   # ticker HHMM (LIVE_CAPABLE) or Kalshi milestone start
            if st is None or st > int(now_ts) or getattr((marks or {}).get(tk), "status", None) == "finalized":
                continue                               # not underway (no start, future, or already settled)
            key = tk.rsplit("-", 1)[0]                  # the match stem (strip the leg/side suffix) == the event ticker
            g = groups.get(key)
            if g is None:
                g = groups[key] = {"start": st, "gs": None, "positions": [], "ticker": tk}
            g["positions"].append(p)
    if not groups:
        return None
    # FEATURED game = the one CLOSEST TO SETTLING (Item 1.2, DETERMINISTIC): baseball latest inning, then most outs;
    # tie -> most held positions; tie -> away code A->Z. A non-MLB game has no inning/outs (0,0) and falls to the
    # held-count then code tiebreak. The rest are OTHER underway games, each a single compact chip (Item 1.3).
    # Bounding the block to ONE featured game + <=3 chips gives the LIVE tile a FIXED height regardless of the game
    # count (Item 1.1) -- the CSS caps + clips it; this just supplies a bounded, deterministically-ordered structure.
    def _settling_key(g):
        gs = g["gs"]
        inn = (getattr(gs, "inning", None) or 0) if gs is not None else 0
        outs = (getattr(gs, "outs", None) or 0) if gs is not None else 0
        a_code, _h = _ordered_teams(g["ticker"])
        return (-inn, -outs, -len(g["positions"]), (a_code or "").upper())
    ranked = sorted(groups.values(), key=_settling_key)     # featured first (closest to settling), then the chips
    feat_g, others = ranked[0], ranked[1:]

    our = next((_held_team_code(p.get("ticker"), p.get("held_leg")) for p in feat_g["positions"]
                if _kind(p.get("ticker")) == "moneyline"), None)
    featured = {"has_scoreboard": feat_g["gs"] is not None, "label": None, "away": None, "home": None,
                "home_lead": False, "away_ours": False, "home_ours": False, "detail": None, "age_sec": None,
                "positions": _event_rows(feat_g["positions"], marks)[:3],
                "more_positions": max(0, len(feat_g["positions"]) - 3)}
    gs = feat_g["gs"]
    if gs is not None:
        asc = "" if gs.away.score is None else str(gs.away.score)
        hsc = "" if gs.home.score is None else str(gs.home.score)
        featured["away"] = ("%s %s" % (gs.away.abbr or "-", asc)).strip()
        featured["home"] = ("%s %s" % (gs.home.abbr or "-", hsc)).strip()
        featured["label"] = "%s @ %s" % (gs.away.abbr or "-", gs.home.abbr or "-")
        featured["home_lead"] = (gs.home.score or 0) > (gs.away.score or 0)
        featured["detail"] = _score_detail(gs)
        featured["age_sec"] = getattr(gs, "age_sec", None)
        a_code, h_code = _ordered_teams(feat_g["ticker"])   # ticker-space away/home -> position-based marker (feed-abbr safe)
        featured["away_ours"] = bool(our and our == (a_code or "").upper())
        featured["home_ours"] = bool(our and our == (h_code or "").upper())
    else:
        p0 = feat_g["positions"][0]
        featured["label"], _ = name_market(p0.get("ticker"), p0.get("held_leg"), (marks or {}).get(p0.get("ticker")), None, cat)

    chips = []                                              # each OTHER underway game -> one line: matchup + <=2 pairs
    for g in others[:3]:
        ggs = g["gs"]
        if ggs is not None:
            clabel = "%s@%s" % (ggs.away.abbr or "-", ggs.home.abbr or "-")
        else:
            q0 = g["positions"][0]
            clabel, _ = name_market(q0.get("ticker"), q0.get("held_leg"), (marks or {}).get(q0.get("ticker")), None, cat)
        pairs = []
        for p in g["positions"]:
            tk, leg = p.get("ticker"), p.get("held_leg")
            bid = marks_mod.bid_for_leg((marks or {}).get(tk), leg)
            val = (p.get("contracts") * bid) if (bid is not None and p.get("contracts") is not None) else None
            pairs.append({"short": _short_label(tk, _kind(tk), leg), "value": val, "value_known": val is not None})
        chips.append({"label": clabel, "pairs": pairs[:2], "overflow": len(pairs) > 2})
    return {"featured": featured, "others": chips, "more": max(0, len(others) - 3), "n_live": len(groups)}


def _next_event(category, positions, marks, now_ts, starts=None):
    """The UPCOMING tile's NEXT line: the SOONEST held event's label (R2 name) + starts_in. The start comes from the
    ticker HHMM (LIVE_CAPABLE) or the Kalshi milestone (2026-09-12), so tennis/ufc/soccer now show a countdown too;
    where NO start is sourceable (still unknown, or a placeholder) starts_in stays None -> the template says 'start
    time unavailable', never a guessed time."""
    if not positions:
        return None
    best = None
    for p in positions:
        st = start_ts_for_ticker(category, p.get("ticker"), starts)
        if st is not None and (best is None or st < best[0]):
            best = (st, p)
    p = best[1] if best else positions[0]
    tk = p.get("ticker")
    # Item 2 (2026-09-12): the NEXT line carries the SAME matchup + signed shorthand as the positions table when the
    # ticker decodes to a two-team game ("MIZZ @ KU - SPR -6.5 MIZZ"); tennis/ufc/fed (no matchup) fall back to the
    # R2 name_market label. Fail-closed -- never a raw ticker.
    mu = market_matchup(tk)
    if mu:
        kind = _kind(tk)
        primary, _ = format_market_label(mu, kind, _short_label(tk, kind, p.get("held_leg")), None)
        label = "%s · %s" % (mu, primary)
    else:
        label, _ = name_market(tk, p.get("held_leg"), (marks or {}).get(tk), None, category)
    starts_in = (int(best[0]) - int(now_ts)) if best else None
    return {"label": label, "starts_in_seconds": starts_in if (starts_in is not None and starts_in > 0) else None}


def build_subdivisions_context(*, subs, accounts_meta, arm_all, liveness_by_sub, liveness_present, pnl_all,
                               realized_windows, positions_by_sub, last_events, marks, feed_games, now_ts,
                               thin_floor, mark_age_sec, active_account, viewer_role, viewer_account, logo_codes,
                               poll_interval, global_arm, max_order_id, leg_audit_reviews=None, name_exceptions=None,
                               starts=None):
    """Assemble the redesigned Live Sub-divisions context. `subs` = tiles_all rows already SCOPED to the visible
    accounts. Segments the ACTIVE account's tiles by activity (alarm pulled out first, R5), sorts each bucket by
    |today| desc then code, and rolls up the summary bar + tab counts for every visible account. Pure -- no DB, no
    network. `name_exceptions` (a mutable list, optional) collects any (account, category, ticker) whose name fell
    back to the category label so the caller can log the R2 exception. `starts` is the cached Kalshi milestone
    start-time index (event ticker -> unix start); it feeds the cross-category LIVE/UPCOMING clock compare -- None
    means no index (every category behaves exactly as it did before the milestone feed: honest UPCOMING)."""
    arm_subs = (arm_all or {}).get("subs", {})
    tiles_by_acct: dict = {}
    for s in subs:
        aid, cat = s["account_id"], s["category"]
        key = (aid, cat)
        code = cat.upper()
        attached = int(s.get("n_whales") or 0) > 0
        a = arm_subs.get(key) or {"sub_state": "absent", "sub_ts": None, "effective_state": "disarmed"}
        eff_armed = a.get("effective_state") == "armed"
        lv = liveness_by_sub.get(key) if (attached and liveness_present) else None
        is_alarm = bool(eff_armed and lv is not None and getattr(lv, "state", None) in _LV_ALARM_STATES)
        pos = positions_by_sub.get(key) or []
        vp = value_positions(pos, marks)
        p = pnl_all.get(key) or {}
        rw = realized_windows.get(key) or {}
        booked = int(p.get("booked_closes", 0))
        n_live_trades = int(s.get("n_live_trades") or 0)
        has_history = booked > 0 or n_live_trades > 0
        tickers = [x.get("ticker") for x in pos]
        underway = _event_underway(cat, tickers, feed_games, marks, now_ts, starts) if pos else False
        activity = ("UNATTACHED" if not attached
                    else ("LIVE" if (pos and underway) else "UPCOMING") if pos
                    else ("SETTLED" if has_history else "INACTIVE"))
        lc = (last_events.get(key) or {}).get("last_close")
        last_close = None
        if lc:
            won = lc.get("won")
            last_close = {"age_sec": (int(now_ts) - int(lc["ts"])) if lc.get("ts") else None,
                          "result": ("won" if won == 1 else "lost" if won == 0 else None),
                          "realized": lc.get("realized")}
        realized = None
        if attached and (has_history or pos):
            realized = {"today": float(rw.get("today", 0.0)), "week": float(rw.get("week", 0.0)),
                        "month": float(rw.get("month", 0.0)), "all_time": float(rw.get("all_time", 0.0)),
                        "booked_closes": booked, "wins": int(p.get("wins", 0)), "losses": int(p.get("losses", 0)),
                        "unbooked": int(p.get("unbooked_closes", 0)), "thin": 0 < booked < int(thin_floor)}
        openb = None
        if attached and pos:
            openb = {"count": len(pos), "cost": sum(float(x.get("cost_basis_usd") or 0.0) for x in pos),
                     "value": vp["value"], "value_known": vp["known"], "priced": vp["n_priced"],
                     "of": vp["n_total"], "complete": vp["complete"], "mark_age": mark_age_sec}
        event = _live_event(cat, pos, feed_games, marks, now_ts, starts) if activity == "LIVE" else None
        next_event = _next_event(cat, pos, marks, now_ts, starts) if activity == "UPCOMING" else None
        if name_exceptions is not None:
            for x in pos:
                _, ok = name_market(x.get("ticker"), x.get("held_leg"), (marks or {}).get(x.get("ticker")), None, cat)
                if not ok:
                    name_exceptions.append({"account": aid, "category": cat, "ticker": x.get("ticker")})
        tile = {
            "code": code, "category": cat, "account": aid, "activity": activity,
            "orphan": cat in RETIRED_CATEGORIES, "attached": attached,
            "family": sport_family(cat), "has_logo": code in (logo_codes or set()),
            "logo": ("/static/logos/%s.png" % code) if code in (logo_codes or set()) else None,
            "monogram": code[:4],
            "arm_state": _ARM_DISPLAY.get(a.get("sub_state"), (a.get("sub_state") or "").upper()),
            "arm_age": _ts_age(a.get("sub_ts"), now_ts), "effective_armed": eff_armed,
            "liveness": ({"state": lv.state, "label": ("STARVED" if lv.state == "CATEGORY_STARVED" else lv.state),
                          "age_sec": getattr(lv, "age_sec", None), "signals": getattr(lv, "n_signals", None),
                          "orders": getattr(lv, "placed", None), "errors": getattr(lv, "errors", None)} if lv else None),
            "whales": int(s.get("n_whales") or 0), "is_alarm": is_alarm,
            "realized": realized, "open": openb, "event": event, "next_event": next_event,
            "last_close": last_close,
            "href": "/live/%s/%s" % (aid, cat), "sort_today": abs(float(rw.get("today", 0.0))),
        }
        tiles_by_acct.setdefault(aid, []).append(tile)

    def _rollup(aid):
        ts = tiles_by_acct.get(aid, [])
        r = {"today": 0.0, "week": 0.0, "month": 0.0, "all_time": 0.0, "booked": 0, "unbooked": 0,
             "open": 0, "cost": 0.0, "value": 0.0, "priced": 0, "of": 0, "value_known": False, "mark_age": None,
             "n_open_subs": 0, "armed": 0, "alarm": 0,
             "LIVE": 0, "UPCOMING": 0, "SETTLED": 0, "INACTIVE": 0, "UNATTACHED": 0}
        for t in ts:
            if t["realized"]:
                for k in ("today", "week", "month", "all_time"):
                    r[k] += t["realized"][k]
                r["booked"] += t["realized"]["booked_closes"]
                r["unbooked"] += t["realized"]["unbooked"]
            if t["open"]:
                r["open"] += t["open"]["count"]; r["cost"] += t["open"]["cost"]; r["of"] += t["open"]["of"]
                r["priced"] += t["open"]["priced"]; r["n_open_subs"] += 1
                if t["open"]["value"] is not None:
                    r["value"] += t["open"]["value"]; r["value_known"] = True
                if t["open"]["mark_age"] is not None:
                    r["mark_age"] = t["open"]["mark_age"] if r["mark_age"] is None else min(r["mark_age"], t["open"]["mark_age"])
            if t["effective_armed"]:
                r["armed"] += 1
            if t["is_alarm"]:
                r["alarm"] += 1
            r[t["activity"]] += 1
        return r

    def _sortkey(t):
        return (-t["sort_today"], t["code"])

    visible = [m["account_id"] for m in accounts_meta]
    active = active_account if active_account in visible else (visible[0] if visible else None)
    tabs = []
    for m in accounts_meta:
        rr = _rollup(m["account_id"])
        tabs.append({"id": m["account_id"], "name": m.get("account_label") or m["account_id"],
                     "venue": m.get("venue"), "slug": m["account_id"], "n_subs": len(tiles_by_acct.get(m["account_id"], [])),
                     "armed": rr["armed"], "alarm": rr["alarm"], "active": m["account_id"] == active})
    active_tiles = tiles_by_acct.get(active, [])
    alarm_tiles = sorted([t for t in active_tiles if t["is_alarm"]], key=_sortkey)
    sections = {}
    for act in ("LIVE", "UPCOMING", "SETTLED", "INACTIVE", "UNATTACHED"):
        sections[act] = sorted([t for t in active_tiles if not t["is_alarm"] and t["activity"] == act], key=_sortkey)
    alarm_strip = [{"code": t["code"], "state": t["liveness"]["state"] if t["liveness"] else "NEVER",
                    "age_sec": t["liveness"]["age_sec"] if t["liveness"] else None, "href": t["href"]}
                   for t in alarm_tiles]
    # leg-audit safety strip (Rung-3): the engine's independent post-fill leg check, read back for ALL visible
    # accounts (a wrong-side fill is money-critical -> shown regardless of the active tab). The reader already
    # scoped to the viewer + excluded clean/legacy/dry-run; here we only shape it for the template. Three DISTINCT
    # surfaced states -- inversion (loud) / soft / unevaluated -- an unreadable audit is NEVER treated as a pass.
    _la = leg_audit_reviews or {}
    _la_counts = _la.get("counts") or {}
    _la_rows = _la.get("rows", []) if _la.get("schema_ok") else []   # reader already scoped, severity-first, capped
    leg_audit_strip = {
        "schema_ok": bool(_la.get("schema_ok")),
        "total": int(_la.get("total_surfaced", 0) or 0),
        "shown": int(_la.get("shown", len(_la_rows)) or 0),   # < total when capped -> template says "showing N of M"
        "n_inversion": int(_la_counts.get(leg_audit.STATE_INVERSION, 0)),
        "n_soft": int(_la_counts.get(leg_audit.STATE_SOFT, 0)),
        "n_uneval": int(_la_counts.get(leg_audit.STATE_UNEVALUATED, 0)),
        "rows": [{"code": (r.get("category") or "").upper(), "account": r.get("account"),
                  "state": r.get("state"), "state_label": r.get("state_label"),
                  "ticker": r.get("ticker"), "leg": r.get("outcome_leg"),
                  "signal_outcome": r.get("signal_outcome"), "verdict": r.get("leg_audit"),
                  "status": r.get("outcome_status"), "filled": (r.get("outcome_status") == "filled"),
                  "href": "/live/%s/%s" % (r.get("account"), r.get("category"))}
                 for r in _la_rows],
    }
    return {
        "meta": {"global_arm": (global_arm or {}).get("state"), "global_arm_age": (global_arm or {}).get("ts_age"),
                 "poll_interval_seconds": poll_interval, "generated_age_seconds": 0,
                 "viewer_role": viewer_role, "viewer_account": viewer_account,
                 "thin_threshold": int(thin_floor),
                 "tz_note": "US Eastern calendar day / week / month -- the day ends 23:59:59 ET; the week starts Monday.",
                 "week_note": "Week/month/all-time sit close together while the history is young; that is honest."},
        "accounts": [{"id": m["account_id"], "name": m.get("account_label") or m["account_id"],
                      "venue": m.get("venue"), "slug": m["account_id"]} for m in accounts_meta],
        "tabs": tabs, "active_account": active,
        "live_capable": sorted(x.upper() for x in LIVE_CAPABLE),
        "alarm": alarm_tiles, "alarm_strip": alarm_strip, "leg_audit_strip": leg_audit_strip, "sections": sections,
        "summary": _rollup(active) if active else None,
        "liveness_present": liveness_present, "max_order_id": int(max_order_id or 0), "now_ts": now_ts,
    }
