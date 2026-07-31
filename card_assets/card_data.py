"""card_data.py — map a paper_trade_record row (+ parsed extra_json) to the generator's card_data dict.

Rules per the build brief. HONEST-EMPTY: anything that does not join becomes '' — NEVER fabricated.
For a CLOSED trade the position was PLACED, which means every construct gate passed at entry; the loss is
the EXIT, not a broken stage. So the funnel is ALWAYS all-5 'passed' (no near-miss / failed-stage logic).

DISPLAY/NOTIFICATION ONLY. Read-only helpers; no engine import, no DB writes.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

# FIX C: US Eastern for card timestamps (auto EST/EDT). zoneinfo (stdlib) with a pytz fallback; if
# neither resolves America/New_York, timestamps render honest-empty ('') rather than a wrong/UTC time.
try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    try:
        import pytz  # type: ignore
        _ET = pytz.timezone("America/New_York")
    except Exception:
        _ET = None


# trend_mode wire value -> WITH-TREND chip display label
_TREND_LABEL = {
    "ps_trail30": "PS-TRAIL30",
    "rd": "RD",
    "ema200": "EMA200",
}


def read_trend_mode_map(yaml_path) -> dict:
    """READ-ONLY parse of config/strategies.yaml -> bitunix_sfp.trend_mode (wire-keyed dict).

    The value in the yaml is an inline-flow mapping, e.g.:
        trend_mode: { BTCUSDT: ps_trail30, ETHUSDT: rd, SOLUSDT: ema200, XRPUSDT: rd }

    Prefer PyYAML if available; fall back to a tiny line/brace parser so this never hard-depends on
    yaml being installed in the box runtime. Returns {} on any failure (caller degrades to '').
    """
    p = Path(yaml_path).expanduser()
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return {}

    # --- Try PyYAML (full-file parse; robust to block- or flow-style) ---
    try:
        import yaml  # type: ignore
        doc = yaml.safe_load(text)
        tm = (doc or {}).get("bitunix_sfp", {}).get("trend_mode")
        if isinstance(tm, dict) and tm:
            return {str(k): str(v) for k, v in tm.items()}
    except Exception:
        pass

    # --- Fallback: locate the bitunix_sfp block, then its trend_mode line, parse the {..} inline map ---
    try:
        lines = text.splitlines()
        in_block = False
        for line in lines:
            stripped = line.strip()
            # top-level key (no leading indent) named bitunix_sfp:
            if line and not line[0].isspace() and stripped.startswith("bitunix_sfp:"):
                in_block = True
                continue
            # left the block on the next top-level key
            if in_block and line and not line[0].isspace() and stripped.endswith(":"):
                break
            if in_block and stripped.startswith("trend_mode:"):
                brace = stripped[stripped.index(":") + 1:].strip()
                brace = brace.lstrip("{").rstrip("}")
                out = {}
                for pair in brace.split(","):
                    if ":" not in pair:
                        continue
                    k, v = pair.split(":", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k:
                        out[k] = v
                return out
    except Exception:
        pass
    return {}


def _wire_from_symbol(symbol: str) -> str:
    """'BTC/USDT.P' -> 'BTCUSDT' (strip the /...P suffix)."""
    if not symbol:
        return ""
    return symbol.split("/")[0] + "USDT" if "/" in symbol else symbol.replace("/", "").split(".")[0]


def _pair_display(symbol: str) -> str:
    """'BTC/USDT.P' -> 'BTCUSDT'."""
    if not symbol:
        return ""
    base = symbol.split("/")[0]
    return f"{base}USDT"


def _fmt_price(v):
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return ""


def _fmt_swept(v):
    # FIX B (2026-07-12): a swept level is a PRICE (always positive). The SFP detector stores it
    # NEGATED for shorts (the reflect_neg short convention), which leaked a minus sign onto the card
    # (e.g. "-77.0"). Render the ABSOLUTE price; the magnitude was already correct.
    try:
        return f"{abs(float(v)):,.1f}"
    except (TypeError, ValueError):
        return ""


def _fmt_et(iso):
    """FIX C: format a stored UTC ISO timestamp in US Eastern (ET, auto EST/EDT), e.g. 'Jul 12, 2:14 PM
    ET'. Honest-empty ('') on any parse/tz failure — never a wrong or UTC-labeled-ET time."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if _ET is None:
            return ""
        dt = dt.astimezone(_ET)
        h12 = dt.hour % 12 or 12
        ap = "AM" if dt.hour < 12 else "PM"
        return f"{dt.strftime('%b')} {dt.day}, {h12}:{dt.minute:02d} {ap} ET"
    except Exception:
        return ""


def _get(extra: dict, *keys):
    """First present, non-None value among keys."""
    for k in keys:
        if k in extra and extra[k] is not None:
            return extra[k]
    return None


def build_card_data(row: dict, trend_mode_map: dict) -> dict:
    """Map one paper_trade_record row -> the generator's card_data dict.

    row: sqlite row as a dict (columns of paper_trade_record). extra_json may be a raw JSON string
    (as stored) or an already-parsed dict.
    trend_mode_map: wire-keyed dict from read_trend_mode_map (e.g. {'BTCUSDT': 'ps_trail30', ...}).
    """
    extra_raw = row.get("extra_json")
    if isinstance(extra_raw, str):
        try:
            extra = json.loads(extra_raw) if extra_raw else {}
        except (json.JSONDecodeError, TypeError):
            extra = {}
    elif isinstance(extra_raw, dict):
        extra = extra_raw
    else:
        extra = {}

    symbol = row.get("symbol") or ""
    pair = _pair_display(symbol)
    wire = _wire_from_symbol(symbol)

    # side: side_semantic (LONG/SHORT); fallback from side buy->LONG / sell->SHORT
    ss = extra.get("side_semantic")
    if ss:
        side = str(ss).upper()
    else:
        raw_side = (row.get("side") or "").lower()
        side = "LONG" if raw_side == "buy" else ("SHORT" if raw_side == "sell" else "")

    # leverage: f"{int(leverage)}x"
    lev = extra.get("leverage")
    try:
        leverage = f"{int(float(lev))}x" if lev is not None else ""
    except (TypeError, ValueError):
        leverage = ""

    # r_result: f"{actual_r_multiple:+.2f}R"
    r_mult = row.get("actual_r_multiple")
    try:
        r_val = float(r_mult)
        r_result = f"{r_val:+.2f}R"
    except (TypeError, ValueError):
        r_val = None
        r_result = ""

    # outcome / badge — win->TARGET, loss->STOPPED, cross-checked against extra.exit_kind
    result = (row.get("result") or "").lower()
    exit_kind = (extra.get("exit_kind") or "").lower()
    if exit_kind == "stop":
        outcome_badge = "STOPPED"
    elif exit_kind in ("tp", "target"):
        outcome_badge = "TARGET"
    elif result == "win":
        outcome_badge = "TARGET"
    elif result == "loss":
        outcome_badge = "STOPPED"
    else:
        outcome_badge = ""
    outcome = "win" if result == "win" else "loss"

    # FIX C: entry fill time + which exit HIT (shows the close time) vs MISSED (strike-through). ET.
    # Driven by the ACTUAL outcome (exit_kind via outcome_badge), never assumed.
    entry_time = _fmt_et(row.get("ts"))
    close_time = _fmt_et(row.get("result_ts"))
    _hit = "stop" if outcome_badge == "STOPPED" else ("tp" if outcome_badge == "TARGET" else "")
    stop_time = close_time if _hit == "stop" else ""
    tp_time = close_time if _hit == "tp" else ""
    stop_struck = (_hit == "tp")   # the STOP did not fill -> strike it
    tp_struck = (_hit == "stop")   # the TAKE-PROFIT did not fill -> strike it

    # prices
    entry_price = _get(extra, "actual_entry_fill_price", "entry_reference_price")
    if entry_price is None:
        entry_price = row.get("entry_reference_price")
    stop_price = row.get("stop_price")
    tp_price = row.get("tp_price")

    entry = _fmt_price(entry_price)
    take_profit = _fmt_price(tp_price)
    stop = _fmt_price(stop_price)

    # ROI% — TRUE RETURN ON CAPITAL COMMITTED (audit 2026-07-18): ROI% = net PnL / MARGIN POSTED * 100.
    #   margin = qty * actual_entry_fill_price / leverage   (margin is NOT a stored field — computed here)
    # Confirmed EXACT on SOL 64f246f6: -22.43 / 93.83 = -23.9% (operator's own figures).
    # ★LEVERAGE IS PER-TRADE — read extra_json.leverage from THIS row (the `lev` resolved above). A
    #  2026-07-07 SOL trade ran at 10x, NOT 30x; hard-coding 30 silently yields a wrong margin + wrong ROI.
    # ★R IS UNCHANGED and DIFFERENT from ROI: R = PnL / max_dollar_risk (a risk-multiple); ROI = PnL /
    #  margin (return on capital). They are separate numbers — do NOT collapse ROI back into R*100.
    # Numerator = net PnL: prefer net_realized_usd (net of fees); fall back to actual_pnl_dollars.
    # Denominator uses `entry_price` (prefers actual_entry_fill_price, resolved above). Honest-empty ('')
    # if qty / entry fill / leverage / net PnL is missing or non-positive.
    roi_pct = ""
    try:
        net_pnl = extra.get("net_realized_usd")
        if net_pnl is None:
            net_pnl = row.get("actual_pnl_dollars")
        qty_v = abs(float(row.get("qty")))
        entry_fill_v = float(entry_price)   # actual entry fill (actual_entry_fill_price preferred)
        lev_v = float(lev)                  # per-trade leverage from extra_json.leverage — NEVER 30 assumed
        margin_posted = qty_v * entry_fill_v / lev_v
        roi = float(net_pnl) / margin_posted * 100.0
        roi_pct = f"{roi:+.1f}%"
    except (TypeError, ValueError, ZeroDivisionError):
        roi_pct = ""

    # funnel — all-5 passed for a closed (placed) trade
    # pattern: swing_mode two_candle -> 'Two-Candle SFP'
    swing_mode = (extra.get("swing_mode") or extra.get("sfp_mode_swing") or "two_candle")
    pattern_val = "Two-Candle SFP" if "two_candle" in str(swing_mode).lower() or True else str(swing_mode)
    # (construct is fixed two_candle; keep 'Two-Candle SFP' as the display for a placed SFP)
    pattern_val = "Two-Candle SFP"

    swept_val = _fmt_swept(extra.get("swept_swing_level"))

    # with_trend: the coin's LIVE trend_mode via trend_mode_map[wire] -> display label
    tm_wire = (trend_mode_map or {}).get(wire, "")
    with_trend_val = _TREND_LABEL.get(str(tm_wire), str(tm_wire).upper() if tm_wire else "")

    # bos: f"{bos_tf} CONFIRMED"
    bos_tf = extra.get("bos_tf")
    bos_val = f"{bos_tf} CONFIRMED" if bos_tf else ""

    funnel = {
        "pattern":     {"value": pattern_val,  "state": "passed"},
        "swept_level": {"value": swept_val,    "state": "passed"},
        "with_trend":  {"value": with_trend_val, "state": "passed"},
        "fresh_inst":  {"value": "YES",        "state": "passed"},
        "bos":         {"value": bos_val,      "state": "passed"},
    }

    return {
        "outcome": outcome,
        "pair": pair,
        "side": side,
        "leverage": leverage,
        "r_result": r_result,
        "roi_pct": roi_pct,
        "outcome_badge": outcome_badge,
        "entry": entry,
        "take_profit": take_profit,
        "stop": stop,
        "entry_time": entry_time,
        "take_profit_time": tp_time,
        "stop_time": stop_time,
        "take_profit_struck": tp_struck,
        "stop_struck": stop_struck,
        "funnel": funnel,
    }
