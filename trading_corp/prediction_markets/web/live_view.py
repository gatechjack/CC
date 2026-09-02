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


def _short_label(ticker: str, kind: str, held_leg: str | None) -> str:
    """A compact bet label for the card slot (honest, derived from the outcome suffix). ML -> the club abbr we
    are on; TOTAL -> 'O 8.5'/'U 8.5'; SPREAD -> 'SD +1.5' style. Falls back to the raw suffix."""
    parts = str(ticker or "").split("-")
    suffix = parts[2] if len(parts) > 2 else ""
    if kind == "total":
        mt = re.match(r"^([OU])([0-9.]+)$", suffix)
        if mt:
            return ("Over " if mt.group(1) == "O" else "Under ") + mt.group(2)
    if kind == "spread":
        ms = re.match(r"^([A-Z]{2,3})([0-9.]+)$", suffix)
        if ms:
            return "%s %s" % (ms.group(1), ms.group(2))
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


def _build_slot(ticker, kind, open_pos, settle, mark):
    """One card bet-slot: open (live value = contracts x held-leg bid) or settled (won/lost). open_pos is the
    live_positions row for this ticker (or None); settle is the per-ticker settlement rollup (or None)."""
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
                "bid": bid}
    if settle is not None:
        won = settle.get("won")
        contracts = settle.get("contracts")
        # a settled position's card value is its PAYOUT: $1.00 per contract if won, $0.00 if lost (realized P&L is
        # a distinct number, shown in the drawer). value_known only when we actually know the win/loss.
        payout = (contracts if won else 0.0) if (won is not None and contracts is not None) else None
        return {"kind": kind, "kind_label": KIND_LABEL.get(kind, kind.upper()),
                "short": _short_label(ticker, kind, None), "desc": describe_market(ticker, None),
                "ticker": ticker, "held_leg": None, "contracts": contracts, "avg_fill": None, "cost": None,
                "fee": None, "settled": True, "won": won, "realized": settle.get("realized"),
                "settled_at": settle.get("settled_ts"), "current_value": payout,
                "value_known": payout is not None, "bid": None}
    return None


# ── the game card ────────────────────────────────────────────────────────────────────────────────────────────
def _feed_block(gs, now_ts: int) -> dict:
    """Render-ready feed block from a feed_mlb.GameState (or None -> unavailable). Live-only fields are None when
    not in progress. `age_sec` lets the template band freshness; a final game never goes stale."""
    if gs is None:
        return {"available": False, "status": "unavailable", "source": None, "age_sec": None,
                "note": "Sports feed unavailable -- no score, inning or count is shown, because none is current."}
    live = gs.is_live
    return {"available": True, "status": gs.status, "source": gs.source,
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


def _card(game_key, tickers, orders_by_ticker, open_by_ticker, settle_by_ticker, marks, gs, now_ts):
    away_code, home_code = _ordered_teams(sorted(tickers)[0]) if tickers else (None, None)
    slots = []
    by_kind = {}
    for tk in sorted(tickers):
        kind = _kind(tk)
        slot = _build_slot(tk, kind, open_by_ticker.get(tk),
                           settle_by_ticker.get(tk), (marks or {}).get(tk))
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
    return {"key": list(game_key), "feed": _feed_block(gs, now_ts),
            "slots_by_kind": {KIND_LABEL[k]: by_kind.get(k) for k in KINDS},
            "matchup_away": away_code, "matchup_home": home_code,
            "start_hhmm": _et_hhmm_from_key(game_key[1]), "date_iso": game_key[0],
            "n_settled": n_settled, "n_live": n_live, "mixed": n_settled > 0 and n_live > 0,
            "complete": complete, "drops_in_h": drops_in_h,
            "open_cost": open_cost, "open_value": open_value, "value_known": value_known,
            "realized": realized, "anchor_ts": anchor}


def _dropped(card, now_ts: int) -> bool:
    if not card["complete"] or not card["anchor_ts"]:
        return False
    return (now_ts - card["anchor_ts"]) > RETENTION_HOURS * 3600


# ── trade drawer rows (one per ENTRY fill; realized attributed pro-rata across a copy's entries) ─────────────
def _trade_rows(orders: list, agg: dict, marks: dict, feed_by_key: dict, now_ts: int) -> list:
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
        gs = feed_by_key.get(gk) if gk else None
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
            "realized": realized, "realized_booked": state != "opposed",
            "slippage_cents": (round((float(o["fill_price"]) - float(o["submitted_price"])) * 100)
                               if o.get("fill_price") is not None and o.get("submitted_price") is not None else None),
        })
    return rows


# ── top-level assembly ───────────────────────────────────────────────────────────────────────────────────────
def build_live_context(*, orders: list, open_positions: list, open_positions_by_whale: list,
                       slate, marks_result, now_ts: int) -> dict:
    """Pure assembly: journal rows + open positions + a feed SlateResult + a MarksResult -> the template context.
    No DB, no network -- the route fetches those and passes them in (so this unit-tests directly)."""
    marks = (marks_result.marks if marks_result is not None else {}) or {}
    slate_games = (slate.games if slate is not None else {}) or {}
    open_by_ticker = {p["ticker"]: p for p in (open_positions or [])}
    open_by_ticker_wallet = {(p["ticker"], p["wallet"]) for p in (open_positions_by_whale or [])}
    settle_by_ticker = _ticker_settlement(orders or [])
    agg = _pos_aggregates(orders or [], open_by_ticker_wallet)

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
    # a settled/open ticker with no order? tickers come only from orders, so all are covered.

    feed_by_key = {k: v for k, v in slate_games.items()}
    cards = []
    for gk, tks in tickers_by_game.items():
        gs = feed_mlb.match_in_slate(slate_games, gk[0], gk[3], gk[1], gk[2]) if slate_games else None
        card = _card(gk, tks, orders_by_ticker, open_by_ticker, settle_by_ticker, marks, gs, now_ts)
        if not _dropped(card, now_ts):
            cards.append(card)
    cards.sort(key=lambda c: (c["complete"], c["start_hhmm"] or ""))

    n_active = sum(1 for c in cards if not c["complete"])
    n_complete = len(cards) - n_active
    live_cost = sum(c["open_cost"] for c in cards)
    live_val_known = any(c["value_known"] for c in cards)
    live_val = sum((c["open_value"] or 0.0) for c in cards if c["value_known"]) if live_val_known else None
    today = _et_date(now_ts)
    realized_today = sum(c["realized"] for c in cards if c["date_iso"] == today)
    settled_today = sum(c["n_settled"] for c in cards if c["date_iso"] == today)

    trades = _trade_rows(orders or [], agg, marks, feed_by_key, now_ts)

    return {
        "cards": cards,
        "summary": {"n_active": n_active, "n_complete": n_complete, "unsettled_cost": live_cost,
                    "unsettled_value": live_val, "unsettled_value_known": live_val_known,
                    "realized_today": realized_today, "settled_today": settled_today},
        "trades": trades,
        "feed_meta": {"ready": slate is not None, "source": (slate.source if slate else None),
                      "ok": (slate.ok if slate else False),
                      "marks_ok": (marks_result.ok if marks_result is not None else False),
                      "as_of": (slate.as_of if slate else None)},
        "poll_interval": POLL_INTERVAL_SECONDS, "retention_hours": RETENTION_HOURS,
    }


def build_from_cache(*, orders, open_positions, open_positions_by_whale, cache, now_ts: int) -> dict:
    """Convenience wrapper: pull the relevant slate(s) + marks from the ui_cache and assemble. The cards can span
    two ET dates (a night game + retention), so we merge both windowed slates' games into one lookup."""
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
                              marks_result=snap.marks, now_ts=now_ts) | {"warming": not snap.ready}
