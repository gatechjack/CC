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

from ..market_describe import describe_market
from ...data.mlb_poly_kalshi_match import kalshi_to_iso_date
from ...data.sports_team_mapping import MLB_TEAMS
from . import feed_mlb, marks as marks_mod

KINDS = ("moneyline", "total", "spread")
KIND_LABEL = {"moneyline": "ML", "total": "TOT", "spread": "SPR"}
RETENTION_HOURS = 24
POLL_INTERVAL_SECONDS = 60
_SETTLE_SOURCES = ("settlement", "settlement_void")


# ── ticker -> game key + market kind + compact label ─────────────────────────────────────────────────────────
_STEM_RE = re.compile(r"^(\d{2}[A-Z]{3}\d{2})(\d{4})?([A-Z0-9]+)$")


def _split_team_blob(blob: str):
    """A team blob 'AWAYHOME' (e.g. SDCIN, SEABOS, NYYLAA, CWSHOU) -> (away_code, home_code) using MLB_TEAMS as
    the split oracle (both halves must be known clubs). None if no unique valid split -> the game degrades to
    unavailable rather than mis-joining. Concatenation order is away+home (verified: SDCIN=SD@CIN)."""
    for k in range(2, len(blob) - 1):
        a, b = blob[:k], blob[k:]
        if a in MLB_TEAMS and b in MLB_TEAMS:
            return a, b
    return None


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
        ms = re.match(r"^([A-Z]{2,3})(\d+)$", suffix)
        if ms:
            team_code, n = ms.group(1), int(ms.group(2))
            strike = "%.1f" % (n - 0.5)
            other = _spread_other(ticker, team_code)
            if leg == "no" and other:
                return "+%s %s" % (strike, other)         # the OTHER team gets +strike (the underdog side)
            return "-%s %s" % (strike, team_code)         # yes/None -> the anchor team lays -strike (favourite)
    if kind == "moneyline":
        # the suffix is the YES club; if we hold the NO leg the bet is the OTHER club -- describe_market resolves it.
        return (suffix or "").upper()
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
    """(away_code, home_code) from a ticker's stem blob (away+home order), or (None, None). Lets the card show the
    matchup from the TICKER even when the sports feed is down (the teams are ticker-derived, not feed-derived)."""
    parts = str(ticker or "").split("-")
    if len(parts) < 2:
        return (None, None)
    m = _STEM_RE.match(parts[1])
    if not m:
        return (None, None)
    blob = m.group(3)
    gm = re.search(r"G(\d)$", blob)
    if gm:
        blob = blob[:gm.start()]
    return _split_team_blob(blob) or (None, None)


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
        matchup = ("%s @ %s" % (gs.away.abbr, gs.home.abbr)) if gs else None
        rows.append({
            "order_id": o.get("id"), "ticker": tk, "kind": _kind(tk),
            "kind_label": KIND_LABEL.get(_kind(tk), _kind(tk).upper()),
            "desc": describe_market(tk, leg), "matchup": matchup,
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


def _positions_view(orders, open_by_ticker, settle_by_ticker, agg, marks, whales_by_ticker) -> dict:
    """The non-MLB category view: a positions TABLE (active = open; complete = settled/exit/opposed), each row with
    ticker, honest description (the market title if we have a mark carrying one, else describe_market's
    '<type>:<ticker>' fallback), side, contracts, cost basis, current value or 'no mark', status, and the copied
    whale(s). Same bid-valuation + coverage the cards use -- never $0 for an unpriced position, never cost as value."""
    active, complete = [], []
    all_tickers = {o.get("ticker") for o in (orders or []) if o.get("ticker")}
    for tk in sorted(all_tickers):
        kind = _kind(tk)
        whales = whales_by_ticker.get(tk, [])
        op = open_by_ticker.get(tk)
        if op is not None:
            leg = op.get("held_leg"); mk = (marks or {}).get(tk)
            bid = marks_mod.bid_for_leg(mk, leg); contracts = op.get("contracts")
            value = (contracts * bid) if (bid is not None and contracts is not None) else None
            title = getattr(mk, "title", None) if mk is not None else None
            active.append({"ticker": tk, "kind": kind, "kind_label": KIND_LABEL.get(kind, kind.upper()),
                           "desc": (title or describe_market(tk, leg)), "market_title": title, "held_leg": leg,
                           "contracts": contracts, "cost": op.get("cost_basis_usd"), "avg_fill": op.get("avg_price"),
                           "fee": op.get("fees_usd"), "current_value": value, "value_known": value is not None,
                           "bid": bid, "settled": False, "status": "open", "whales": whales,
                           "whale_tag": _whale_tag(whales)})
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
        complete.append({"ticker": tk, "kind": kind, "kind_label": KIND_LABEL.get(kind, kind.upper()),
                         "desc": describe_market(tk, settled_leg), "market_title": None, "held_leg": settled_leg,
                         "contracts": contracts, "cost": None, "avg_fill": None, "fee": None,
                         "current_value": payout, "value_known": payout is not None, "bid": None, "settled": True,
                         "won": won, "realized": (settle.get("realized") if settle else None),
                         "settled_at": (settle.get("settled_ts") if settle else None),
                         "status": status, "whales": cw, "whale_tag": _whale_tag(cw)})
    active.sort(key=lambda r: r["ticker"]); complete.sort(key=lambda r: r["ticker"])
    return {"active": active, "complete": complete, "n_active": len(active), "n_complete": len(complete)}


# ── top-level assembly ───────────────────────────────────────────────────────────────────────────────────────
def build_live_context(*, orders: list, open_positions: list, open_positions_by_whale: list,
                       slate, marks_result, now_ts: int, category: str | None = None) -> dict:
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
                                         whales_by_ticker)
        summary = _journal_summary(open_positions or [], orders or [], marks, now_ts)

    trades = _trade_rows(orders or [], agg, marks, slate_games, mismatch_by_gk, now_ts)

    return {
        "mode": "mlb_cards" if is_mlb else "positions",
        "category": category,
        "cards": cards,
        "positions_view": positions_view,
        "summary": summary,
        "trades": trades,
        "feed_meta": {"ready": slate is not None, "source": (slate.source if slate else None),
                      "ok": (slate.ok if slate else False),
                      "marks_ok": (marks_result.ok if marks_result is not None else False),
                      "as_of": (slate.as_of if slate else None), "has_game_feed": is_mlb},
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
    return build_live_context(orders=orders, open_positions=open_positions,
                              open_positions_by_whale=open_positions_by_whale, slate=merged,
                              marks_result=snap.marks, now_ts=now_ts, category=category) | {"warming": not snap.ready}
