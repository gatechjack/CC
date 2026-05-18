"""Phase C — 5-factor gate factor-contribution analysis.

Answers the questions the Board asked after seeing the initial backtest:
  1. For the 33 5f-fired trades: which factors (and which factor pairs)
     correlate with profitable trades?
  2. For the 15 PA-only trades (PA fired / 5f rejected): what were
     their actual outcomes in the PA arm? Did the 5f gate save money
     or cost money by rejecting them?
  3. On the full 1,796-alert dataset: are any two factors >0.6
     correlated (effective redundancy)?

Reads the existing PA + 5f arm outputs from
`data/backtest_runs/bitunix_<ts>_pa/{ledger,trades}.json` and the
matching `..._five_factor/` directory, plus re-walks all alerts to
recover per-alert factor booleans (which the harness doesn't persist).

Outputs `reports/gate_backtest_2026-05-17_factor_analysis.md` —
findings only; no factor-loosening recommendations.

Usage:
    python scripts/analyze_5f_factor_contribution.py \\
        --pa-dir data/backtest_runs/bitunix_<ts>_pa \\
        --gate-dir data/backtest_runs/bitunix_<ts>_five_factor \\
        --start 2026-04-30 --end 2026-05-17
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from scripts.backtest_btc_accumulator import (  # noqa: E402
    _resample_to_3m, _resample_to_4h, _resample_to_5m, _resample_to_15m,
    build_price_context,
    fetch_alerts_from_prod, fetch_ohlcv_from_coinbase,
)
from scripts.backtest_bitunix_confluence import (  # noqa: E402
    ACCEPTANCE_THRESHOLDS, _shim_cache_at, ctx_config,
)
from trading_corp.agents.strategies.bitunix_confluence import (  # noqa: E402
    BitUnixConfluenceConfig, Side, Tier,
    evaluate_confluence_futures, filter_live_alerts_with_dedupe,
)
from trading_corp.agents.strategies.bitunix_confluence_gate import (  # noqa: E402
    ConfluenceGateConfig, GateDecision, evaluate_confluence_gate,
)
from trading_corp.data.bitunix_price_context import (  # noqa: E402
    build_gate_inputs,
)
import yaml  # noqa: E402

log = logging.getLogger("analyze_5f_factor_contribution")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


FACTOR_NAMES = ("ema_alignment", "vwap", "volatility", "cvd", "volume_z")


def _phi_correlation(a: list[int], b: list[int]) -> float | None:
    """Phi coefficient — Pearson r for two binary 0/1 series.

    Equivalent to Pearson, but for 0/1 inputs gets you the same number
    you'd compute by hand from the 2x2 contingency.
    """
    if len(a) != len(b) or not a:
        return None
    n = len(a)
    sa = sum(a)
    sb = sum(b)
    sab = sum(x * y for x, y in zip(a, b))
    num = n * sab - sa * sb
    den_a = n * sa - sa * sa
    den_b = n * sb - sb * sb
    if den_a <= 0 or den_b <= 0:
        return None
    return num / (den_a ** 0.5 * den_b ** 0.5)


def _walk_all_alerts(
    *, alerts, bars, config, gate_config,
) -> dict[str, dict]:
    """Walk every alert and capture the 5f gate's factor decisions.

    Returns a dict `{ts_iso: {factor_pass: {name: 0/1},
                              gate_decision, gate_score,
                              side_for_eval}}`.

    No cooldown modelling here — we record the gate decision at every
    alert independent of any arm-specific cooldown state. The arm
    ledgers (loaded separately) are ground truth for "did this trade
    actually fire in arm X."
    """
    bars_4h = _resample_to_4h(bars)
    bars_3m = _resample_to_3m(bars)
    bars_5m_r = _resample_to_5m(bars)
    bars_15m_r = _resample_to_15m(bars)
    sorted_alerts = sorted(alerts, key=lambda a: a.ts)
    out: dict[str, dict] = {}

    for a in sorted_alerts:
        ctx = build_price_context(bars, a.ts, ctx_config(config), bars_4h=bars_4h)
        if ctx is None:
            continue
        live = filter_live_alerts_with_dedupe(sorted_alerts, config, a.ts)
        verdict = evaluate_confluence_futures(
            live_alerts=live, price_ctx=ctx, config=config, now=a.ts,
            last_fire_ts_buy=None, last_fire_ts_sell=None,
        )
        # Pick eval side: scorer's verdict if set, else higher raw score
        if verdict.side == Side.BUY:
            side_str = "buy"
        elif verdict.side == Side.SELL:
            side_str = "sell"
        else:
            side_str = (
                "buy" if verdict.breakdown.raw_buy_score
                >= verdict.breakdown.raw_sell_score else "sell"
            )

        shim_3m = _shim_cache_at(bars_3m, a.ts, 180, max_bars=500)
        shim_5m = _shim_cache_at(bars_5m_r, a.ts, 300, max_bars=300)
        shim_15m = _shim_cache_at(bars_15m_r, a.ts, 900, max_bars=250)
        inp = build_gate_inputs(
            shim_3m, shim_5m, shim_15m, side=side_str, config=gate_config,
        )
        gate_result = evaluate_confluence_gate(
            side=side_str, inputs=inp, config=gate_config,
        )
        out[a.ts.isoformat()] = {
            "side_for_eval": side_str,
            "factor_pass": {f.name: (1 if f.passed else 0)
                            for f in gate_result.factors},
            "gate_decision": gate_result.decision.value,
            "gate_score": gate_result.score,
            "verdict_tier": verdict.tier.value,
        }
    return out


def _load_trades(arm_dir: Path) -> list[dict]:
    p = arm_dir / "trades.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _load_ledger(arm_dir: Path) -> list[dict]:
    p = arm_dir / "ledger.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _per_factor_wr_avg_r(
    decisions_by_ts: dict[str, dict], gate_trades: list[dict],
) -> dict:
    """For each factor, restrict to the 5f-arm trades where that factor
    passed at the trade's open_ts; compute WR + avg-R + n.
    """
    out: dict[str, dict] = {}
    for fname in FACTOR_NAMES:
        rs: list[float] = []
        n_tp = 0
        for t in gate_trades:
            d = decisions_by_ts.get(t["open_ts"])
            if d is None or d["factor_pass"].get(fname, 0) != 1:
                continue
            if t.get("realized_r") is None:
                continue
            rs.append(float(t["realized_r"]))
            if t.get("outcome") == "tp":
                n_tp += 1
        out[fname] = {
            "n": len(rs),
            "win_rate_pct": (n_tp / len(rs) * 100.0) if rs else 0.0,
            "avg_r": (sum(rs) / len(rs)) if rs else 0.0,
            "total_r": sum(rs),
        }
    return out


def _pairwise_factor_wr_avg_r(
    decisions_by_ts: dict[str, dict], gate_trades: list[dict],
) -> list[dict]:
    pairs: list[dict] = []
    for i in range(len(FACTOR_NAMES)):
        for j in range(i + 1, len(FACTOR_NAMES)):
            a, b = FACTOR_NAMES[i], FACTOR_NAMES[j]
            rs: list[float] = []
            n_tp = 0
            for t in gate_trades:
                d = decisions_by_ts.get(t["open_ts"])
                if d is None:
                    continue
                fp = d["factor_pass"]
                if fp.get(a, 0) != 1 or fp.get(b, 0) != 1:
                    continue
                if t.get("realized_r") is None:
                    continue
                rs.append(float(t["realized_r"]))
                if t.get("outcome") == "tp":
                    n_tp += 1
            pairs.append({
                "pair": (a, b),
                "n": len(rs),
                "win_rate_pct": (n_tp / len(rs) * 100.0) if rs else 0.0,
                "avg_r": (sum(rs) / len(rs)) if rs else 0.0,
                "total_r": sum(rs),
            })
    return pairs


def _pa_only_trade_outcomes(
    pa_trades: list[dict], pa_ledger: list[dict],
    gate_ledger: list[dict], decisions_by_ts: dict[str, dict],
) -> dict:
    """PA-only = PA-arm `fired=True` at ts AND 5f-arm at the same ts
    either rejected at the gate OR didn't fire (cooldown / SKIP).

    Ground truth from arm ledgers, not the re-walk's fire decisions.
    """
    pa_trades_by_ts = {t["open_ts"]: t for t in pa_trades}
    gate_ledger_by_ts = {e["ts"]: e for e in gate_ledger}

    pa_only: list[dict] = []
    for entry in pa_ledger:
        if not entry.get("fired"):
            continue
        ts = entry["ts"]
        gate_entry = gate_ledger_by_ts.get(ts)
        # If 5f arm also fired at this ts → "both fire", not PA-only.
        if gate_entry is not None and gate_entry.get("fired"):
            continue
        pa_trade = pa_trades_by_ts.get(ts)
        realized_r = pa_trade.get("realized_r") if pa_trade else None
        outcome = pa_trade.get("outcome") if pa_trade else None
        d = decisions_by_ts.get(ts)
        failing_factors = (
            [name for name, passed in d["factor_pass"].items() if passed == 0]
            if d else []
        )
        pa_only.append({
            "ts": ts,
            "tier": entry.get("tier"),
            "side": entry.get("side"),
            "failing_factors": failing_factors,
            "gate_decision_at_ts": d["gate_decision"] if d else "unknown",
            "realized_r": realized_r,
            "outcome": outcome,
        })

    rs = [p["realized_r"] for p in pa_only if p["realized_r"] is not None]
    n_tp = sum(1 for p in pa_only if p.get("outcome") == "tp")
    n_sl = sum(1 for p in pa_only if p.get("outcome") == "sl")
    total_r = sum(rs)
    avg_r = (total_r / len(rs)) if rs else 0.0
    win_rate = (n_tp / len(pa_only) * 100.0) if pa_only else 0.0
    factor_reject_count: dict[str, int] = {f: 0 for f in FACTOR_NAMES}
    for p in pa_only:
        for f in p["failing_factors"]:
            factor_reject_count[f] = factor_reject_count.get(f, 0) + 1

    return {
        "n": len(pa_only),
        "rows": pa_only,
        "n_tp": n_tp, "n_sl": n_sl,
        "win_rate_pct": win_rate,
        "total_r": total_r,
        "avg_r": avg_r,
        "factor_reject_count": factor_reject_count,
    }


def _side_breakdown_trades(trades: list[dict]) -> dict:
    """Side counts + per-side WR + per-side total R over a trade list."""
    out: dict[str, dict] = {}
    for side in ("buy", "sell"):
        rs: list[float] = []
        n_tp = 0
        n_sl = 0
        for t in trades:
            if t.get("side") != side:
                continue
            if t.get("realized_r") is None:
                continue
            rs.append(float(t["realized_r"]))
            if t.get("outcome") == "tp":
                n_tp += 1
            elif t.get("outcome") == "sl":
                n_sl += 1
        n = len(rs)
        out[side] = {
            "n": n,
            "n_tp": n_tp, "n_sl": n_sl,
            "win_rate_pct": (n_tp / n * 100.0) if n else 0.0,
            "total_r": sum(rs),
            "avg_r": (sum(rs) / n) if n else 0.0,
        }
    total = out["buy"]["n"] + out["sell"]["n"]
    out["buy_pct"] = (out["buy"]["n"] / total * 100.0) if total else 0.0
    out["sell_pct"] = (out["sell"]["n"] / total * 100.0) if total else 0.0
    return out


def _side_breakdown_pa_only(pa_only_rows: list[dict]) -> dict:
    return _side_breakdown_trades(pa_only_rows)


def _alert_population_side_baseline(decisions_by_ts: dict[str, dict]) -> dict:
    """Baseline distribution of scorer-fire-eligible side preferences
    across the full alert population. Filters to alerts where the
    scorer's verdict was non-SKIP (those are the alerts the gate could
    have acted on); for those, count the scorer's intended side.
    """
    n_buy = 0
    n_sell = 0
    n_skip = 0
    for d in decisions_by_ts.values():
        tier = d.get("verdict_tier", "SKIP")
        if tier == "SKIP":
            n_skip += 1
            continue
        side = d.get("side_for_eval", "?")
        if side == "buy":
            n_buy += 1
        elif side == "sell":
            n_sell += 1
    total_fire_eligible = n_buy + n_sell
    return {
        "n_fire_eligible": total_fire_eligible,
        "n_skip": n_skip,
        "n_buy": n_buy, "n_sell": n_sell,
        "buy_pct": (n_buy / total_fire_eligible * 100.0) if total_fire_eligible else 0.0,
        "sell_pct": (n_sell / total_fire_eligible * 100.0) if total_fire_eligible else 0.0,
    }


def _factor_correlation_matrix(decisions_by_ts: dict[str, dict]) -> dict:
    columns: dict[str, list[int]] = {f: [] for f in FACTOR_NAMES}
    for d in decisions_by_ts.values():
        fp = d.get("factor_pass") or {}
        for f in FACTOR_NAMES:
            columns[f].append(int(fp.get(f, 0)))

    corrs: dict[tuple[str, str], float | None] = {}
    high_corr: list[tuple[str, str, float]] = []
    for i in range(len(FACTOR_NAMES)):
        for j in range(i + 1, len(FACTOR_NAMES)):
            a, b = FACTOR_NAMES[i], FACTOR_NAMES[j]
            c = _phi_correlation(columns[a], columns[b])
            corrs[(a, b)] = c
            if c is not None and abs(c) > 0.6:
                high_corr.append((a, b, c))
    return {"corrs": corrs, "high_corr": high_corr,
            "n_alerts_used": len(columns[FACTOR_NAMES[0]])}


def write_report(
    *,
    output_path: Path,
    per_factor: dict, pairwise: list[dict],
    pa_only: dict, corr: dict,
    n_5f_fires: int,
    gate_side_breakdown: dict,
    pa_only_side_breakdown: dict,
    alert_pop_baseline: dict,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines += [
        "# 5-Factor Gate — Factor-Contribution Analysis",
        "",
        f"**Window:** 2026-04-30 → 2026-05-17  ·  "
        f"**5f-fired trades analyzed:** {n_5f_fires}  ·  "
        f"**PA-only trades analyzed:** {pa_only['n']}  ·  "
        f"**Alerts used for correlation:** {corr['n_alerts_used']}",
        "",
        "## Q1 — Per-factor WR / avg-R (5f-fired trades only)",
        "",
        "Restricted to the 5f-arm round-trips. Each row = trades where",
        "that factor passed (regardless of which other factors passed).",
        "",
        "| Factor | n | Win rate | Avg R | Total R |",
        "|---|---|---|---|---|",
    ]
    for fname in FACTOR_NAMES:
        s = per_factor[fname]
        lines.append(
            f"| {fname} | {s['n']} | {s['win_rate_pct']:.1f}% | "
            f"{s['avg_r']:+.3f} | {s['total_r']:+.2f} |"
        )

    lines += [
        "",
        "## Q1 (continued) — Pairwise factor WR / avg-R",
        "",
        "Restricted to trades where BOTH factors in the pair passed.",
        "Sorted by total R (highest first).",
        "",
        "| Factor pair | n | Win rate | Avg R | Total R |",
        "|---|---|---|---|---|",
    ]
    pairwise_sorted = sorted(pairwise, key=lambda p: -p["total_r"])
    for p in pairwise_sorted:
        a, b = p["pair"]
        lines.append(
            f"| {a} + {b} | {p['n']} | {p['win_rate_pct']:.1f}% | "
            f"{p['avg_r']:+.3f} | {p['total_r']:+.2f} |"
        )

    lines += [
        "",
        "## Q2 — PA-only trade outcomes (PA fired, 5f rejected)",
        "",
        f"**n = {pa_only['n']}** trades. Outcomes drawn from the PA arm's",
        "`trades.json` — these are the actual trades the PA arm placed,",
        "filtered to those where the 5f gate would have rejected.",
        "",
        f"- TP hits: **{pa_only['n_tp']}**",
        f"- SL hits: {pa_only['n_sl']}",
        f"- Win rate: **{pa_only['win_rate_pct']:.1f}%**",
        f"- Total R: **{pa_only['total_r']:+.2f}**",
        f"- Avg R: {pa_only['avg_r']:+.3f}",
        "",
        "### Did the 5f gate save money or cost money?",
        "",
    ]
    if pa_only["total_r"] > 0.5:
        verdict = (
            f"**The 5f gate COST money on these rejects.** Total R = "
            f"{pa_only['total_r']:+.2f} across {pa_only['n']} trades "
            f"means the PA arm captured profit that the 5f arm gave up."
        )
    elif pa_only["total_r"] < -0.5:
        verdict = (
            f"**The 5f gate SAVED money on these rejects.** Total R = "
            f"{pa_only['total_r']:+.2f} — these trades net-lost in PA arm; "
            f"the 5f gate's rejection avoided that loss."
        )
    else:
        verdict = (
            f"**The 5f gate was approximately neutral on these rejects.** "
            f"Total R = {pa_only['total_r']:+.2f} — the trades 5f vetoed "
            f"were roughly break-even, so the rejection didn't materially "
            f"save or cost money."
        )
    lines.append(verdict)
    lines += [
        "",
        f"### Which factors did the rejecting on these {pa_only['n']} trades?",
        "",
        "| Factor | Rejected count |",
        "|---|---|",
    ]
    for fname in FACTOR_NAMES:
        c = pa_only["factor_reject_count"].get(fname, 0)
        lines.append(f"| {fname} | {c} |")

    lines += [
        "",
        "### Per-trade detail",
        "",
        "| ts (UTC) | tier | side | actual R | outcome | factors that failed |",
        "|---|---|---|---|---|---|",
    ]
    for p in pa_only["rows"]:
        r_str = (
            f"{p['realized_r']:+.3f}"
            if p.get("realized_r") is not None else "—"
        )
        ff = ", ".join(p["failing_factors"]) or "(none)"
        lines.append(
            f"| {p['ts']} | {p['tier']} | {p['side']} | {r_str} | "
            f"{p.get('outcome') or '—'} | {ff} |"
        )

    lines += [
        "",
        "## Q3 — Factor correlation matrix (full 1,796-alert dataset)",
        "",
        f"Phi correlation between binary factor-pass series across "
        f"{corr['n_alerts_used']} alerts where the gate could be evaluated.",
        "",
        "| | " + " | ".join(FACTOR_NAMES) + " |",
        "|---|" + "|".join(["---"] * len(FACTOR_NAMES)) + "|",
    ]
    # Build full symmetric matrix
    for i, a in enumerate(FACTOR_NAMES):
        row = [a]
        for j, b in enumerate(FACTOR_NAMES):
            if i == j:
                row.append("1.00")
            else:
                key = (a, b) if (a, b) in corr["corrs"] else (b, a)
                c = corr["corrs"].get(key)
                row.append(f"{c:.2f}" if c is not None else "—")
        lines.append("| " + " | ".join(row) + " |")

    lines += [
        "",
        "### High-correlation pairs (|phi| > 0.6)",
        "",
    ]
    if corr["high_corr"]:
        for a, b, c in corr["high_corr"]:
            lines.append(
                f"- **{a} ↔ {b}**: phi = {c:+.2f} — effective redundancy; "
                "consider collapsing or dropping one in a future revision."
            )
    else:
        lines.append(
            "None. All factor pairs are below the |phi|=0.6 threshold; "
            "no clear evidence of redundancy. The 5-factor structure is "
            "carrying 5 distinct signals."
        )

    # ── Q5 — Directional asymmetry check ──
    lines += [
        "",
        "## Q5 — Directional asymmetry check",
        "",
        "Hypothesis to test: are the gate's rejections concentrated on",
        "one side? If shorts are overrepresented in the PA-only set",
        "(or longs overrepresented in the 5f-fired set) relative to the",
        "alert-population baseline, the gate has asymmetric directional",
        "behaviour and the per-factor analysis above is contaminated.",
        "",
        "### Side baseline — alert population (scorer-fire-eligible)",
        "",
        f"- Buy intent: {alert_pop_baseline['n_buy']} "
        f"({alert_pop_baseline['buy_pct']:.1f}%)",
        f"- Sell intent: {alert_pop_baseline['n_sell']} "
        f"({alert_pop_baseline['sell_pct']:.1f}%)",
        f"- (SKIPped: {alert_pop_baseline['n_skip']})",
        "",
        "### 5f-fired trades by side",
        "",
        "| Side | n | % of 5f fires | Win rate | Avg R | Total R |",
        "|---|---|---|---|---|---|",
        f"| buy | {gate_side_breakdown['buy']['n']} | "
        f"{gate_side_breakdown['buy_pct']:.1f}% | "
        f"{gate_side_breakdown['buy']['win_rate_pct']:.1f}% | "
        f"{gate_side_breakdown['buy']['avg_r']:+.3f} | "
        f"{gate_side_breakdown['buy']['total_r']:+.2f} |",
        f"| sell | {gate_side_breakdown['sell']['n']} | "
        f"{gate_side_breakdown['sell_pct']:.1f}% | "
        f"{gate_side_breakdown['sell']['win_rate_pct']:.1f}% | "
        f"{gate_side_breakdown['sell']['avg_r']:+.3f} | "
        f"{gate_side_breakdown['sell']['total_r']:+.2f} |",
        "",
        "### PA-only trades by side (PA fired, 5f rejected)",
        "",
        "| Side | n | % of PA-only | Win rate | Avg R | Total R |",
        "|---|---|---|---|---|---|",
        f"| buy | {pa_only_side_breakdown['buy']['n']} | "
        f"{pa_only_side_breakdown['buy_pct']:.1f}% | "
        f"{pa_only_side_breakdown['buy']['win_rate_pct']:.1f}% | "
        f"{pa_only_side_breakdown['buy']['avg_r']:+.3f} | "
        f"{pa_only_side_breakdown['buy']['total_r']:+.2f} |",
        f"| sell | {pa_only_side_breakdown['sell']['n']} | "
        f"{pa_only_side_breakdown['sell_pct']:.1f}% | "
        f"{pa_only_side_breakdown['sell']['win_rate_pct']:.1f}% | "
        f"{pa_only_side_breakdown['sell']['avg_r']:+.3f} | "
        f"{pa_only_side_breakdown['sell']['total_r']:+.2f} |",
        "",
        "### Code audit — side-conditional logic",
        "",
        "Reviewed each `_factor_*` function in",
        "`trading_corp/agents/strategies/bitunix_confluence_gate.py` plus",
        "the input-builder logic in",
        "`trading_corp/data/bitunix_price_context.py`.",
        "",
        "**Factor 1 (EMA alignment).** Lines 395–398. Side-conditional logic:",
        "```",
        "if side == 'buy':",
        "    passed = (e8 > e21 > e50) and slope > 0",
        "elif side == 'sell':",
        "    passed = (e8 < e21 < e50) and slope < 0",
        "```",
        "- The inequality is correctly flipped for sell.",
        "- ONLY the EMA8 slope is checked, NOT all three slopes. This",
        "  deviates from the mental model 'all three slopes negative for",
        "  sell' — it's a documented design choice in my Phase A impl,",
        "  but worth flagging because the user expected the stronger check.",
        "  No asymmetry between sides; both rely on slope_8 only.",
        "",
        "**Factor 2 (VWAP).** Lines 423–426. Correctly flipped for sell:",
        "```",
        "if side == 'buy':",
        "    passed = (px > sv) and (px > pv)",
        "elif side == 'sell':",
        "    passed = (px < sv) and (px < pv)",
        "```",
        "Symmetric. No bug.",
        "",
        "**Factor 3 (Volatility).** Line 453.",
        "`passed = (a > asm) and (bpr >= threshold)` — no `side` reference.",
        "Confirmed direction-agnostic.",
        "",
        "**Factor 4 (CVD).** Side check is correctly flipped",
        "(`slope > 0` for buy, `slope < 0` for sell). HOWEVER, the",
        "underlying slope is suspect:",
        "",
        "`cvd_from_bars_tick_rule` in",
        "`bitunix_price_context.py:260-310` computes",
        "`linregress_slope(deltas)` where `deltas[i] = sign_i * volume_i`",
        "(per-bar signed volume). The module docstring describes CVD as",
        "'cumulative volume delta = running sum of signed volume', but",
        "the implementation does NOT cumsum the deltas. So the slope",
        "measures whether per-bar deltas are increasing over time,",
        "NOT whether the cumulative CVD curve is sloping.",
        "",
        "Real-world impact: in a sustained one-direction move with",
        "roughly constant volume, per-bar deltas look like",
        "`[-v, -v, -v, -v, -v]` (constant negatives) → slope ≈ 0 →",
        "NEITHER buy NOR sell passes. True cumulative CVD would be",
        "`[-v, -2v, -3v, -4v, -5v]` → slope = -v → sell would pass.",
        "",
        "Direction implication: this isn't asymmetric per se (both sides",
        "lose the same way during sustained trends), but it makes F4",
        "systematically miss sustained-trend signals. In the 17-day",
        "window the BTC tape was a sustained down-move; that bias would",
        "appear as Factor 4 under-firing on the sell side specifically",
        "because sells outnumber buys in the alert population.",
        "",
        "**Factor 5 (Volume z-score).** Side-agnostic in the factor",
        "function. `build_gate_inputs` lines 451-461 computes volume_z",
        "from `b.volume` (unsigned) over the 20 prior 3m bars. No",
        "directional logic. No bug.",
        "",
        "### Audit findings summary",
        "",
        "- **No asymmetric side-conditional bug** in factor pass/fail",
        "  logic — F1, F2, F4 all correctly flip the inequality for sell.",
        "- **F4 has a semantic mismatch**: docstring says cumulative CVD,",
        "  implementation uses per-bar delta slope. Systematic under-",
        "  firing of F4 in sustained-trend windows on BOTH sides. The",
        "  17-day window was a sell-dominant tape, so the absolute impact",
        "  on sells is larger than on buys (more alerts in the sell",
        "  population) but the per-alert mechanism is direction-agnostic.",
        "- **F1 EMA only checks slope_8**, not all three slopes. Symmetric.",
        "  Deviation from the Board's mental model; not a bug.",
        "",
    ]

    # Floor-revisit flag
    fire_rate_pct = (n_5f_fires / 1796) * 100.0
    pf_threshold = ACCEPTANCE_THRESHOLDS["min_profit_factor"]
    floor_pct = ACCEPTANCE_THRESHOLDS["fire_rate_pct_range"][0]
    lines += [
        "",
        "## Q4 — Should the 5% fire-rate floor itself be revisited?",
        "",
        "(Independent question from whether any factor should be loosened.)",
        "",
        f"The pre-committed floor (5%) was chosen as a typical sanity",
        f"threshold: any gate firing on fewer than 1 in 20 alerts could be",
        f"signal-free coincidence rather than a real edge.",
        "",
        f"This run: **fire rate = {fire_rate_pct:.2f}%** (well below floor) "
        f"yet **profit factor = 2.01** (well above the {pf_threshold:.2f} "
        f"floor), and **win rate = 48.5%** (above the 45% floor). The gate "
        f"is unusually selective AND unusually profitable.",
        "",
        "**Flag for Board:** the floor was set as a *general* sanity check.",
        "A gate that fires rarely but compounds high PF over many windows",
        "could be a legitimate edge — but it could also be statistical",
        "coincidence on 33 trades. Two avenues to disambiguate:",
        "",
        "1. **Re-run on a longer window** (3–6 months) and check whether",
        "   PF holds. If it does, the floor was wrong for this gate's",
        "   risk profile and should be re-justified separately.",
        "2. **Compare to a random-fires control** at the same fire rate.",
        "   If random PF >> 1.0 on the same trades, this window is just",
        "   easy market and the 5f gate isn't adding value.",
        "",
        "This report does NOT recommend overriding the floor — that's",
        "Board judgment. It flags that the floor decision needs its own",
        "treatment, not bundled into the factor-loosening discussion.",
        "",
        "## Methodology",
        "",
        "- Factor decisions re-computed from the same 1m Coinbase OHLCV +",
        "  resampled 3m/5m/15m caches the backtest harness uses. CVD",
        "  tick-rule fallback in 100% of evals (same as backtest).",
        "- PA-only outcomes pulled from the existing PA arm's",
        "  `trades.json` — these are the trades the PA arm actually",
        "  placed in-simulation, not isolated re-simulations.",
        "- Pairwise WR/avg-R uses unordered factor pairs; a trade where",
        "  factors A + B both passed counts for the (A, B) row regardless",
        "  of which other factors also passed.",
        "- Phi correlation = Pearson r computed on the 0/1 pass series.",
        "",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("Wrote analysis report to %s", output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pa-dir", required=True)
    parser.add_argument("--gate-dir", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--config-path",
                        default=str(_REPO_ROOT / "config" / "strategies.yaml"))
    parser.add_argument("--output",
                        default=str(_REPO_ROOT / "reports"
                                    / "gate_backtest_2026-05-17_factor_analysis.md"))
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start + "T00:00:00+00:00")
    end = datetime.fromisoformat(args.end + "T00:00:00+00:00")
    with open(args.config_path) as f:
        raw = yaml.safe_load(f)
    config = BitUnixConfluenceConfig.from_dict(raw["bitunix_futures"])
    gate_cfg = ConfluenceGateConfig(enabled=True, min_gate_score=3)

    alerts = fetch_alerts_from_prod(start, end, refresh=args.refresh)
    bars = fetch_ohlcv_from_coinbase(start, end, refresh=args.refresh)
    log.info("Walking %d alerts to capture per-alert factor decisions...",
             len(alerts))
    decisions_by_ts = _walk_all_alerts(
        alerts=alerts, bars=bars, config=config, gate_config=gate_cfg,
    )
    log.info("Captured factor decisions for %d alerts", len(decisions_by_ts))

    pa_trades = _load_trades(Path(args.pa_dir))
    pa_ledger = _load_ledger(Path(args.pa_dir))
    gate_trades = _load_trades(Path(args.gate_dir))
    gate_ledger = _load_ledger(Path(args.gate_dir))

    per_factor = _per_factor_wr_avg_r(decisions_by_ts, gate_trades)
    pairwise = _pairwise_factor_wr_avg_r(decisions_by_ts, gate_trades)
    pa_only = _pa_only_trade_outcomes(
        pa_trades, pa_ledger, gate_ledger, decisions_by_ts,
    )
    corr = _factor_correlation_matrix(decisions_by_ts)
    gate_side_breakdown = _side_breakdown_trades(gate_trades)
    pa_only_side_breakdown = _side_breakdown_pa_only(pa_only["rows"])
    alert_pop_baseline = _alert_population_side_baseline(decisions_by_ts)

    write_report(
        output_path=Path(args.output),
        per_factor=per_factor, pairwise=pairwise,
        pa_only=pa_only, corr=corr,
        n_5f_fires=len([t for t in gate_trades if t.get("realized_r") is not None]),
        gate_side_breakdown=gate_side_breakdown,
        pa_only_side_breakdown=pa_only_side_breakdown,
        alert_pop_baseline=alert_pop_baseline,
    )
    print(f"\nAnalysis report: {args.output}")
    print(f"  Per-factor + pairwise WR/avg-R: {len(gate_trades)} 5f trades")
    print(f"  PA-only trades: {pa_only['n']} (total R {pa_only['total_r']:+.2f})")
    print(f"  High-correlation pairs (|phi|>0.6): {len(corr['high_corr'])}")
    print(f"  5f side split: buy {gate_side_breakdown['buy_pct']:.1f}% / sell {gate_side_breakdown['sell_pct']:.1f}%")
    print(f"  PA-only side split: buy {pa_only_side_breakdown['buy_pct']:.1f}% / sell {pa_only_side_breakdown['sell_pct']:.1f}%")
    print(f"  Alert pop baseline: buy {alert_pop_baseline['buy_pct']:.1f}% / sell {alert_pop_baseline['sell_pct']:.1f}%")


if __name__ == "__main__":
    main()
