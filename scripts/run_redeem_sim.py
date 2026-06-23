"""PA-redeem-cap simulator — clean run interface for a redeem-wait-cap sweep.

This is a THIN DRIVER over the redeem-cap engine that already lives in
`scripts/backtest_bitunix_confluence.py`
(`run_redeem_cap_backtest` + `_simulate_redeem`, built 2026-06-14). It does
NOT re-implement the mechanism. It wires:

  * the CLEAN corpus  (data/btc_scalping.db `bars_3m`, Bybit BTC/USD),
  * a self-contained, look-ahead-honest SIGNAL STREAM
    (`scripts/research_scoring/synth_ledger.load_synth_ledger` — rising-edge
    alerts derived from the corpus indicator columns, the standard TradingView
    `alertcondition(isconfirmed and not cond[1] and cond)` pattern),

into a single function `run_redeem_sim(cap=C) -> {...}` and a CLI so a later
/goal can sweep `redeem_cap` over the SAME corpus trivially.

WHAT IT MIRRORS (do not confuse with concluding):
  bitunix_futures_observer.run_pa_redeem_loop — a score-valid but PA-rejected
  signal is re-evaluated (score + PA) against fresh bars every 3m bar; it
  ENTERS ("redeem") at the FIRE BAR's real price when PA later passes, it
  EXPIRES when the wait exceeds `cap`, and it DROPS on score-decay (Tier.SKIP)
  per prod parity. cap=0 = enter-at-signal-bar-or-skip (no redeem); cap=inf =
  current uncapped behavior.

HONESTY (load-bearing — prior /goals hit repaint artifacts):
  * Strictly causal. PA re-eval at bar k uses ONLY bars <= k
    (`build_price_context` / `evaluate_pa_validation` are fed the windowed
    corpus as-of bar k; no future bars). Enforced + unit-tested.
  * Redeem fires are priced at the FIRE bar's close, NEVER the stale signal
    price (the entry-timing finding: "paper books stale signal px = optimistic").
  * Real VIP3 per-fill fees (taker 0.09%rt / maker 0.064%rt) via the engine's
    `FeeConfig` round-trip; net-R is gross-R minus the entry-normalised cost.
  * CLEAN corpus only (btc_scalping.db). NEVER the live trading_corp.db
    audit_event records (those are contaminated / book stale px).

THIS TOOL DOES NOT CONCLUDE WHETHER CAPPING HELPS. It only produces honest,
per-cap trade records + aggregates so a /goal can drive the sweep.

PERFORMANCE / SWEEP GUIDANCE (read before driving a sweep):
  The redeem walk re-runs the FULL score+PA pipeline (build_price_context +
  filter_live_alerts_with_dedupe + evaluate_confluence_futures +
  evaluate_pa_validation) for every bar of every PA-reject, up to `cap`. Cost
  is ~O(rejects * cap * alerts). On the full ~22k-bar 3m corpus a deep cap
  (>=30, or 'inf') is impractically slow (>20 min, observed). Shallow caps
  (0..3) over the full corpus run in a few minutes. For a deep-cap or 'inf'
  sweep, SLICE the corpus by window (e.g. weekly via --start/--end) and run
  per slice — the bars_waited tail saturates well within a single week. A
  fuller speedup (memoising the per-bar score/PA context) is left to the /goal
  if it needs full-corpus deep caps.

CLI:
    # single cap
    python scripts/run_redeem_sim.py --cap 3 --start 2026-04-01 --end 2026-04-08
    # sweep (default {0,1,2,3,inf}); 'inf' accepted as a cap token
    python scripts/run_redeem_sim.py --sweep 0,1,2,3,inf --start 2026-04-01 --end 2026-04-08
    # dump per-trade records as JSON
    python scripts/run_redeem_sim.py --cap 30 --json out.json

Programmatic:
    from scripts.run_redeem_sim import run_redeem_sim
    res = run_redeem_sim(cap=3, start="2026-04-01", end="2026-04-08")
    res["n"], res["total_net_R"], res["net_R_per_trade"], res["trades"]
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_SYNTH_DIR = _REPO_ROOT / "scripts" / "research_scoring"
if str(_SYNTH_DIR) not in sys.path:
    sys.path.insert(0, str(_SYNTH_DIR))

import scripts.backtest_bitunix_confluence as _BT  # noqa: E402
from scripts.backtest_bitunix_confluence import (  # noqa: E402
    AlertEvent,
    BitUnixConfluenceConfig,
    run_redeem_cap_backtest,
)
from synth_ledger import load_synth_ledger  # noqa: E402
from dataclasses import replace as _dc_replace  # noqa: E402
from contextlib import contextmanager  # noqa: E402

# Default CLEAN corpus. The btc_scalping.db lives in the MAIN cc dir's data/,
# not in this worktree (the worktree data/ holds only trading_corp.db). Resolve
# in priority order; override with --db. NEVER trading_corp.db (contaminated).
_DEFAULT_DB_CANDIDATES = (
    _REPO_ROOT / "data" / "btc_scalping.db",
    Path(r"C:\Users\AA Incorporado\CC\data\btc_scalping.db"),
    Path.home() / "CC" / "data" / "btc_scalping.db",
)

# cap=inf is represented internally by a very large int (the engine walks
# bar-by-bar and stops at end-of-corpus / score-decay anyway).
_INF_CAP = 10 ** 9


@contextmanager
def _fee_override(taker_pct: float | None):
    """Temporarily override the per-side TAKER fee in the backtest engine.

    ADDITIVE / default-OFF: `taker_pct=None` (the default) yields the engine's
    current behaviour byte-for-byte — this CM is a no-op and does NOT touch the
    module globals. When a rate is given it rebinds the FOUR fee objects the
    engine reads at call time, so BOTH the `fees_too_high_for_risk` GATE
    (`build_v2_plan` reads `_BT._FEES_TK`) and the net-R cost
    (`run_redeem_cap_backtest` reads `_BT._RT_TK` / `_BT._RT_MK`) use the
    corrected rate CONSISTENTLY. The entry leg is always taker
    (`entry_is_taker=True`) so the override also corrects the entry-side cost of
    the maker-exit net-R column. The maker EXIT fee (`maker_fee_pct`) and
    `slippage_pct` are left untouched — only the venue-actual taker rate moves.

    Restores all four globals on exit (even on exception). Single-threaded use
    only (the engine is single-threaded); the sweep loop is sequential.
    """
    if taker_pct is None:
        yield
        return
    if not (taker_pct >= 0.0):
        raise ValueError(f"taker_pct must be >= 0, got {taker_pct!r}")
    saved = (_BT._FEES_TK, _BT._FEES_MK, _BT._RT_TK, _BT._RT_MK)
    try:
        _BT._FEES_TK = _dc_replace(_BT._FEES_TK, taker_fee_pct=taker_pct)
        _BT._FEES_MK = _dc_replace(_BT._FEES_MK, taker_fee_pct=taker_pct)
        _BT._RT_TK = _BT._FEES_TK.round_trip_cost_pct()
        _BT._RT_MK = _BT._FEES_MK.round_trip_cost_pct()
        yield
    finally:
        _BT._FEES_TK, _BT._FEES_MK, _BT._RT_TK, _BT._RT_MK = saved


@contextmanager
def _tp1_mult_override(tp1_mult: float | None):
    """Temporarily override `tp1_min_profit_multiplier` in the backtest engine.

    ADDITIVE / default-OFF: `tp1_mult=None` (the default) is a no-op — the engine
    keeps its current `_SCFG.tp1_min_profit_multiplier` (2.0) byte-for-byte. When
    a value is given it rebinds `_BT._SCFG` (the frozen StrategyConfig the
    fee-gate reads at call time via `build_v2_plan` -> `build_trade_plan(cfg=_SCFG)`),
    so the `fees_too_high_for_risk` gate's TP1 fee-floor
    (`tp1_min_profit_multiplier * round_trip_cost_pct * entry`) uses the new
    multiplier. This is the OTHER half of the COUPLED fee correction (Decision A):
    pairing a lower taker rate with a higher multiplier holds the fee-floor — and
    therefore the gate's skip set and TP1 placement — constant.

    Restores `_SCFG` on exit (even on exception). Single-threaded use only (the
    engine is single-threaded; the sweep loop is sequential). Composes with
    `_fee_override` (they touch disjoint globals).
    """
    if tp1_mult is None:
        yield
        return
    if not (tp1_mult >= 0.0):
        raise ValueError(f"tp1_mult must be >= 0, got {tp1_mult!r}")
    saved = _BT._SCFG
    try:
        _BT._SCFG = _dc_replace(_BT._SCFG, tp1_min_profit_multiplier=tp1_mult)
        yield
    finally:
        _BT._SCFG = saved


def _resolve_db(db: str | Path | None) -> Path:
    if db is not None:
        p = Path(db)
        if not p.exists():
            raise FileNotFoundError(f"corpus db not found: {p}")
        if p.name == "trading_corp.db":
            raise ValueError(
                "refusing trading_corp.db — it is the contaminated live DB; "
                "use the clean btc_scalping.db corpus"
            )
        return p
    for c in _DEFAULT_DB_CANDIDATES:
        if c.exists():
            return c
    raise FileNotFoundError(
        "btc_scalping.db not found in any default location: "
        + ", ".join(str(c) for c in _DEFAULT_DB_CANDIDATES)
        + " — pass --db explicitly"
    )


def _parse_cap(tok: str | int | float) -> int:
    """'inf'/'∞'/None/<0 -> _INF_CAP ; else int >= 0."""
    if tok is None:
        return _INF_CAP
    if isinstance(tok, (int, float)):
        if isinstance(tok, float) and math.isinf(tok):
            return _INF_CAP
        return _INF_CAP if int(tok) < 0 else int(tok)
    s = str(tok).strip().lower()
    if s in ("inf", "infinity", "∞", "none", "-1"):
        return _INF_CAP
    v = int(s)
    return _INF_CAP if v < 0 else v


def _cap_label(cap: int) -> str:
    return "inf" if cap >= _INF_CAP else str(cap)


def _load_3m_bars(db_path: Path, start: datetime, end: datetime) -> list[dict]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT ts, open, high, low, close, volume FROM bars_3m "
            "WHERE ts >= ? AND ts < ? ORDER BY ts",
            (int(start.timestamp()), int(end.timestamp())),
        ).fetchall()
    finally:
        con.close()
    return [
        {
            "ts": datetime.fromtimestamp(ts, tz=timezone.utc),
            "open": o, "high": h, "low": l, "close": c, "volume": v or 0.0,
        }
        for ts, o, h, l, c, v in rows
    ]


def _bars_3m_span(db_path: Path) -> tuple[datetime, datetime]:
    """Full inclusive span of the bars_3m table (for default windowing)."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        mn, mx = con.execute("SELECT min(ts), max(ts) FROM bars_3m").fetchone()
    finally:
        con.close()
    return (
        datetime.fromtimestamp(mn, tz=timezone.utc),
        datetime.fromtimestamp(mx, tz=timezone.utc),
    )


def load_inputs(
    db_path: Path, start: datetime, end: datetime
) -> tuple[list[AlertEvent], list[dict], BitUnixConfluenceConfig]:
    """Load the clean 3m corpus + windowed synth alert stream + prod config.

    Returned alerts are the synth (rising-edge) stream restricted to [start,end)
    — self-contained on the clean corpus, no prod-alert cache required (the
    worktree's data/historical_alerts/ is empty).
    """
    bars = _load_3m_bars(db_path, start, end)
    synth = load_synth_ledger(db_path)
    alerts = [
        AlertEvent(ts=a.ts, signal_name=a.signal_name, tf=a.tf)
        for a in synth
        if start <= a.ts < end
    ]
    cfg_raw = yaml.safe_load((_REPO_ROOT / "config" / "strategies.yaml").read_text())
    config = BitUnixConfluenceConfig.from_dict(cfg_raw["bitunix_futures"])
    return alerts, bars, config


def run_redeem_sim(
    cap: int | float | str | None = _INF_CAP,
    *,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    db: str | Path | None = None,
    structure_tf: str = "4h",
    fee_mode: str = "taker",
    max_slip_pt: float | None = None,
    taker_pct: float | None = None,
    tp1_mult: float | None = None,
    _preloaded: tuple | None = None,
) -> dict:
    """Run the redeem-cap simulator at a single `cap` over the clean corpus.

    Args:
      cap: redeem wait cap in 3m bars. 0 = no redeem (enter at signal bar iff
        PA passes immediately, else skip); N = allow up to N bars of waiting;
        'inf'/None/<0 = uncapped (current behaviour). The engine still drops a
        redeem on score-decay (Tier.SKIP) regardless of cap (prod parity).
      start, end: UTC dates 'YYYY-MM-DD' (or datetimes). Default = full bars_3m
        span of the corpus.
      db: corpus path. Default = first existing btc_scalping.db candidate.
      structure_tf: PA structure-alignment timeframe ('4h' = prod, '1h' = proposal).
      fee_mode: 'taker' (prod SL exits, 0.09%rt) or 'maker' (0.064%rt) — selects
        which net-R column is reported as `net_R`. Both are always recorded
        per-trade.
      max_slip_pt: max-slippage entry guard in price points (default None = OFF).
        Reject a REDEEM entry when |fire_price - signal_bar_close| > max_slip_pt.
        First-pass fires are never affected (slip == 0). Composes with `cap`.
      taker_pct: per-side TAKER fee override (FRACTION, e.g. 0.00019 = venue-
        actual VIP3 Fee-Discount-Card rate). Default None = engine default
        (0.0004). Applies CONSISTENTLY to the fees_too_high_for_risk GATE and the
        net-R cost (see `_fee_override`). ADDITIVE: None reproduces prior runs.
      tp1_mult: `tp1_min_profit_multiplier` override (e.g. 3.75 = the COUPLED
        Decision-A value). Default None = engine default (2.0). Scales the TP1
        fee-floor in the fees_too_high_for_risk gate. ADDITIVE: None reproduces
        prior runs. The COUPLED fix pairs taker_pct=0.00019 with tp1_mult=3.75 so
        2.0*0.0009 == 3.75*0.00048 == 0.0018 -> identical fee-floor / skip set.
      _preloaded: (alerts, bars, config) to reuse across a sweep (internal).

    Returns a dict (the sweep contract):
      {
        cap, cap_label, window, fee_mode, structure_tf,
        n_score_fire, n_pa_pass, n_pa_reject,
        n_first_pass, n_redeem, n_redeem_drop (cap-expiry + score-decay +
            opposite-side, COMBINED — the engine does not separate them),
        n_plan_skip,
        n (walked / R-resolved trades), n_fires_total (incl plan_skip/open),
        total_net_R, net_R_per_trade, gross_R_per_trade,
        win_rate_pct (DIAGNOSTIC ONLY), max_bars_waited,
        bars_waited_hist: {k: count},
        trades: [ {signal_ts, entry_ts, redeemed, bars_waited, entry_bar_price,
                   side, result, gross_R, net_R, net_R_taker, net_R_maker,
                   filled_legs, skip_reason} ],
      }
    All R values net-of-cost are finite or null (null only for unwalked
    outcomes: plan_skip / still-open). No NaN.
    """
    cap_i = _parse_cap(cap)
    if fee_mode not in ("taker", "maker"):
        raise ValueError("fee_mode must be 'taker' or 'maker'")

    if _preloaded is not None:
        alerts, bars, config, win = _preloaded
    else:
        db_path = _resolve_db(db)
        s_full, e_full = _bars_3m_span(db_path)
        s = _to_dt(start) if start is not None else s_full
        # end is exclusive in the loader; default to last-bar + 1s to include it
        e = _to_dt(end) if end is not None else (
            datetime.fromtimestamp(int(e_full.timestamp()) + 1, tz=timezone.utc)
        )
        alerts, bars, config = load_inputs(db_path, s, e)
        win = (s, e)

    with _fee_override(taker_pct), _tp1_mult_override(tp1_mult):
        fires, summ = run_redeem_cap_backtest(
            alerts=alerts, bars=bars, config=config, pa_config=None,
            redeem_cap=cap_i, structure_tf=structure_tf, arm_name=_cap_label(cap_i),
            max_slip_pt=max_slip_pt,
        )

    trades = []
    for f in fires:
        net_r = f.net_r_taker if fee_mode == "taker" else f.net_r_maker
        # f.ts is the ENTRY (fire) bar. The ORIGINAL signal bar is bars_waited
        # 3m bars earlier (0 for a first-pass fire). Surface both unambiguously.
        entry_dt = datetime.fromisoformat(f.ts)
        signal_dt = entry_dt - timedelta(seconds=180 * f.bars_waited)
        trades.append({
            "signal_ts": signal_dt.isoformat(),   # ORIGINAL signal-bar ts, ISO-UTC
            "entry_ts": f.ts,                      # FIRE/entry-bar ts (== signal_ts iff bars_waited==0)
            "redeemed": f.redeemed,
            "bars_waited": f.bars_waited,
            "entry_bar_price": f.entry,            # FIRE-bar price, NOT stale signal-bar price
            "side": f.side,
            "result": f.outcome,                   # win | loss | open | plan_skip
            "gross_R": f.gross_r,
            "net_R": net_r,
            "net_R_taker": f.net_r_taker,
            "net_R_maker": f.net_r_maker,
            "filled_legs": f.filled,
            "skip_reason": f.skip_reason,
        })

    walked = [t for t in trades if t["net_R"] is not None]
    net_vals = [t["net_R"] for t in walked]
    gross_vals = [t["gross_R"] for t in walked if t["gross_R"] is not None]
    wins = sum(1 for t in walked if t["result"] == "win")

    bw_hist = dict(sorted(Counter(f.bars_waited for f in fires).items()))

    # n_expired (cap-exhausted) and n_score_decay_drop are both folded into the
    # engine's n_redeem_drop. We surface the combined drop plus the breakdown
    # we *can* derive: at cap=0 every drop is a no-redeem skip; at cap>0 a drop
    # is either cap-expiry or score-decay (engine does not separate them, so we
    # report the aggregate honestly rather than guessing).
    out = {
        "cap": None if cap_i >= _INF_CAP else cap_i,
        "cap_label": _cap_label(cap_i),
        "window": [win[0].date().isoformat(), win[1].date().isoformat()],
        "fee_mode": fee_mode,
        "structure_tf": structure_tf,
        "taker_pct": taker_pct if taker_pct is not None else _BT._FEES_TK.taker_fee_pct,
        "tp1_mult": tp1_mult if tp1_mult is not None else _BT._SCFG.tp1_min_profit_multiplier,
        # funnel
        "n_score_fire": summ["n_score_fire"],
        "n_pa_pass": summ["n_pa_pass"],
        "n_pa_reject": summ["n_pa_reject"],
        "n_first_pass": summ["n_first_pass_fire"],
        "n_redeem": summ["n_redeem_fire"],
        "n_redeem_drop": summ["n_redeem_drop"],   # cap-expiry + score-decay (combined)
        "n_slip_guard_drop": summ.get("n_slip_guard_drop", 0),  # redeems cut by max-slip guard
        "max_slip_pt": max_slip_pt,
        "n_plan_skip": summ["n_plan_skip"],
        # trades
        "n": len(walked),                          # walked (R-resolved) trades
        "n_fires_total": len(fires),               # incl plan_skip / open
        "total_net_R": sum(net_vals) if net_vals else 0.0,
        "net_R_per_trade": (sum(net_vals) / len(net_vals)) if net_vals else 0.0,
        "gross_R_per_trade": (sum(gross_vals) / len(gross_vals)) if gross_vals else 0.0,
        "win_rate_pct": (wins / len(walked) * 100.0) if walked else 0.0,  # DIAGNOSTIC
        "max_bars_waited": summ["max_bars_waited"],
        "bars_waited_hist": bw_hist,
        "trades": trades,
    }
    return out


def run_sweep(
    caps,
    *,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    db: str | Path | None = None,
    structure_tf: str = "4h",
    fee_mode: str = "taker",
    max_slip_pt: float | None = None,
    taker_pct: float | None = None,
    tp1_mult: float | None = None,
) -> list[dict]:
    """Run the SAME corpus at multiple caps (inputs loaded once, reused)."""
    db_path = _resolve_db(db)
    s_full, e_full = _bars_3m_span(db_path)
    s = _to_dt(start) if start is not None else s_full
    e = _to_dt(end) if end is not None else (
        datetime.fromtimestamp(int(e_full.timestamp()) + 1, tz=timezone.utc)
    )
    alerts, bars, config = load_inputs(db_path, s, e)
    preloaded = (alerts, bars, config, (s, e))
    results = []
    for c in caps:
        results.append(run_redeem_sim(
            cap=c, structure_tf=structure_tf, fee_mode=fee_mode,
            max_slip_pt=max_slip_pt, taker_pct=taker_pct, tp1_mult=tp1_mult,
            _preloaded=preloaded,
        ))
    return results


def _to_dt(d: str | datetime) -> datetime:
    if isinstance(d, datetime):
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(d + "T00:00:00+00:00")


def _print_sweep_table(results: list[dict]) -> None:
    print()
    print("=== PA-REDEEM-CAP SWEEP (engine validation -- NOT a cap verdict) ===")
    print(f"window={results[0]['window']}  fee_mode={results[0]['fee_mode']}  "
          f"structure_tf={results[0]['structure_tf']}  "
          f"taker_pct={results[0].get('taker_pct')}  "
          f"tp1_mult={results[0].get('tp1_mult')}")
    hdr = ("cap", "first_pass", "redeem", "drop", "plan_skip", "walked",
           "net_R/trade", "total_net_R", "win%(diag)", "max_bw")
    print("{:>5} {:>10} {:>7} {:>6} {:>9} {:>6} {:>11} {:>11} {:>10} {:>6}".format(*hdr))
    for r in results:
        print("{:>5} {:>10} {:>7} {:>6} {:>9} {:>6} {:>+11.4f} {:>+11.3f} {:>10.1f} {:>6}".format(
            r["cap_label"], r["n_first_pass"], r["n_redeem"], r["n_redeem_drop"],
            r["n_plan_skip"], r["n"], r["net_R_per_trade"], r["total_net_R"],
            r["win_rate_pct"], r["max_bars_waited"],
        ))
    print()
    print("NOTE: trade count (first_pass + redeem) is monotone non-decreasing in cap.")
    print("NOTE: win% is a DIAGNOSTIC only; the decision metric is net-R per trade.")
    print("This tool does NOT conclude whether capping helps -- it feeds a /goal sweep.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--cap", default=None,
                   help="single redeem cap in 3m bars (0=no-redeem, N, or 'inf')")
    g.add_argument("--sweep", default=None,
                   help="comma list of caps, e.g. '0,1,2,3,inf' (default this set)")
    ap.add_argument("--start", default=None, help="UTC start YYYY-MM-DD (default corpus start)")
    ap.add_argument("--end", default=None, help="UTC end YYYY-MM-DD exclusive (default corpus end)")
    ap.add_argument("--db", default=None, help="corpus db path (default btc_scalping.db)")
    ap.add_argument("--structure-tf", choices=["4h", "1h"], default="4h")
    ap.add_argument("--fee-mode", choices=["taker", "maker"], default="taker")
    ap.add_argument("--max-slip-pt", type=float, default=None,
                    help="max-slippage entry guard in price points (default OFF); "
                         "rejects a redeem whose |fill - signal_close| exceeds it")
    ap.add_argument("--taker-pct", type=float, default=None,
                    help="per-side TAKER fee override as a FRACTION (default = engine "
                         "0.0004); e.g. 0.00019 = venue-actual VIP3 Fee-Discount-Card. "
                         "Applies to BOTH the fees_too_high_for_risk gate and net-R.")
    ap.add_argument("--tp1-mult", type=float, default=None,
                    help="tp1_min_profit_multiplier override (default = engine 2.0); "
                         "e.g. 3.75 = COUPLED Decision-A value. Scales the TP1 fee-floor "
                         "in the fees_too_high_for_risk gate. Pair with --taker-pct to "
                         "hold the fee-floor constant (coupled correction).")
    ap.add_argument("--json", default=None, help="write full results (incl per-trade) to this path")
    args = ap.parse_args()

    if args.sweep is not None:
        caps = [_parse_cap(t) for t in args.sweep.split(",")]
    elif args.cap is not None:
        caps = [_parse_cap(args.cap)]
    else:
        caps = [0, 1, 2, 3, _INF_CAP]   # default validation sweep

    results = run_sweep(
        caps, start=args.start, end=args.end, db=args.db,
        structure_tf=args.structure_tf, fee_mode=args.fee_mode,
        max_slip_pt=args.max_slip_pt, taker_pct=args.taker_pct,
        tp1_mult=args.tp1_mult,
    )
    _print_sweep_table(results)

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2, default=str),
                                   encoding="utf-8")
        print(f"\nwrote per-trade results -> {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
