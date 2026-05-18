"""Iron-condor daily digest.

Cron-able summary the operator runs once per market day during the
90-day paper run. Combines the step-13 telemetry queries + today's
activity from the audit_event table + the live open-IC registry into
a markdown digest piped to stdout (or `--out path/to/file.md`).

Usage:

  # Today's digest (default).
  python -m trading_corp.scripts.ic_daily_digest

  # A specific day.
  python -m trading_corp.scripts.ic_daily_digest --date 2026-05-17

  # Write to a file.
  python -m trading_corp.scripts.ic_daily_digest --out runbooks/daily/2026-05-17.md

The digest covers, for the named day:
  1. Combo activity — proposed / approved / rejected / unfilled counts.
  2. Currently-open ICs (from agent_state.open_ics).
  3. Closed combos today (from ic_lifecycle_closed audit) + realized P&L.
  4. Cumulative paper-run P&L (since the first ic_lifecycle_closed event).
  5. Scan-filter counters today (which symbols got filtered, why).
  6. Slippage events today + cumulative.
  7. Circuit-breaker status (consecutive_losses, paused_until).
  8. Recent ERROR-level audit events (best-effort surface; surfaces
     anything tagged with severity=high in payload).

Exit code 0 always (digest is informational, never blocks).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

from trading_corp.agents.ic_telemetry import (
    adjustment_outcome_stats,
    combo_pnl_report,
    combo_slippage_stats,
    scan_filter_counters,
    win_rate_by_ivr,
)
from trading_corp.persistence import db


STRATEGY_SLUG = "robinhood_joint_iron_condor"
DIVISION_SLUG = "robinhood_joint"
DEFAULT_DB_URL = "sqlite:///data/trading_corp.db"


# ---------------------------------------------------------------------------
# Per-day primitives — read directly from audit_event for "today's activity"
# ---------------------------------------------------------------------------


def _day_bounds(day: date) -> tuple[str, str]:
    """ISO-8601 [start, end) bounds for a single UTC day."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _count_audit_kinds(
    db_url: str, day: date,
    *,
    kinds: list[str],
    actors: list[str] | None = None,
) -> dict[str, int]:
    start, end = _day_bounds(day)
    sql = (
        "SELECT kind, COUNT(*) AS n FROM audit_event "
        "WHERE ts >= ? AND ts < ? AND kind IN ({}) "
    ).format(",".join("?" * len(kinds)))
    params: list[Any] = [start, end] + list(kinds)
    if actors:
        sql += "AND actor IN ({}) ".format(",".join("?" * len(actors)))
        params += actors
    sql += "GROUP BY kind"
    with db.connect(db_url) as conn:
        rows = conn.execute(sql, params).fetchall()
    out = {k: 0 for k in kinds}
    for r in rows:
        out[r["kind"]] = int(r["n"])
    return out


def _open_ics(db_url: str) -> list[dict]:
    rec = db.load_agent_state(STRATEGY_SLUG, "state", db_url=db_url)
    if rec is None:
        return []
    state, _ts = rec
    if not isinstance(state, dict):
        return []
    open_ics = state.get("open_ics") or {}
    out: list[dict] = []
    for cid, ic in open_ics.items():
        out.append({
            "combo_id": cid,
            "symbol": ic.get("symbol"),
            "expiration": ic.get("expiration"),
            "credit_at_entry": ic.get("credit_at_entry"),
            "ivr_at_entry": ic.get("ivr_at_entry"),
            "contracts": ic.get("contracts"),
            "adjustment_count": ic.get("adjustment_count", 0),
            "opened_ts": ic.get("opened_ts"),
        })
    return out


def _circuit_breaker_state(db_url: str) -> dict:
    rec = db.load_agent_state(STRATEGY_SLUG, "state", db_url=db_url)
    if rec is None:
        return {}
    state, _ts = rec
    return (state.get("circuit_breaker") if isinstance(state, dict) else {}) or {}


def _closed_combos_for_day(db_url: str, day: date) -> list[dict]:
    start, end = _day_bounds(day)
    with db.connect(db_url) as conn:
        rows = conn.execute(
            "SELECT ts, payload_json FROM audit_event "
            "WHERE kind = 'ic_lifecycle_closed' AND ts >= ? AND ts < ? "
            "ORDER BY ts ASC",
            (start, end),
        ).fetchall()
    out = []
    for r in rows:
        try:
            p = json.loads(r["payload_json"])
        except (TypeError, ValueError):
            continue
        out.append({"ts": r["ts"], **p})
    return out


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _fmt_money(x) -> str:
    if x is None:
        return "—"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    return f"${v:,.2f}"


def render_digest(*, day: date, db_url: str) -> str:
    open_ics = _open_ics(db_url)
    cb = _circuit_breaker_state(db_url)
    counts = _count_audit_kinds(
        db_url, day,
        kinds=[
            "combo_proposed",
            "combo_rejected_by_risk",
            "board_combo_approved",
            "board_combo_rejected",
            "combo_filled",
            "combo_unfilled",
            "ic_lifecycle_closed",
        ],
    )
    closed_today = _closed_combos_for_day(db_url, day)
    realized_today_dollars = sum(
        float(c.get("realized_pnl_dollars") or 0) for c in closed_today
    )

    start, end = _day_bounds(day)
    pnl_summary = combo_pnl_report(db_url=db_url)["summary"]
    ivr_report = win_rate_by_ivr(db_url=db_url)
    adjust_report = adjustment_outcome_stats(db_url=db_url)
    scan_today = scan_filter_counters(
        date_iso=day.isoformat(), db_url=db_url,
    )
    slip_today = combo_slippage_stats(
        start_ts=start, end_ts=end, db_url=db_url,
    )
    slip_cum = combo_slippage_stats(db_url=db_url)

    lines: list[str] = []
    lines.append(f"# IC Daily Digest — {day.isoformat()}")
    lines.append("")
    lines.append(f"Strategy: `{STRATEGY_SLUG}`  ·  Division: `{DIVISION_SLUG}`")
    lines.append("")

    # 1. Today's combo activity
    lines.append("## Today's combo activity")
    lines.append("")
    lines.append(f"| Kind | Count |")
    lines.append(f"|---|---:|")
    for k in (
        "combo_proposed", "combo_rejected_by_risk",
        "board_combo_approved", "board_combo_rejected",
        "combo_filled", "combo_unfilled", "ic_lifecycle_closed",
    ):
        lines.append(f"| `{k}` | {counts.get(k, 0)} |")
    lines.append("")

    # 2. Open ICs
    lines.append(f"## Open ICs ({len(open_ics)})")
    lines.append("")
    if open_ics:
        lines.append("| Combo | Symbol | Expiry | Credit | IVR | Contracts | Adj |")
        lines.append("|---|---|---|---:|---:|---:|---:|")
        for ic in open_ics:
            cid = (ic["combo_id"] or "")[:8]
            lines.append(
                f"| `{cid}` | {ic['symbol']} | {ic['expiration']} | "
                f"{_fmt_money(ic.get('credit_at_entry'))} | "
                f"{ic.get('ivr_at_entry') or '—'} | "
                f"{ic.get('contracts') or '—'} | "
                f"{ic.get('adjustment_count', 0)} |"
            )
    else:
        lines.append("_No open ICs._")
    lines.append("")

    # 3. Closed today
    lines.append(f"## Closed today ({len(closed_today)})")
    lines.append("")
    if closed_today:
        lines.append("| Combo | Symbol | Close kind | IVR | Adj | Realized |")
        lines.append("|---|---|---|---:|---:|---:|")
        for c in closed_today:
            cid = (c.get("combo_id") or "")[:8]
            lines.append(
                f"| `{cid}` | {c.get('symbol', '?')} | "
                f"{c.get('close_kind', '?')} | "
                f"{c.get('ivr_at_entry') or '—'} | "
                f"{c.get('adjustment_count', 0)} | "
                f"{_fmt_money(c.get('realized_pnl_dollars'))} |"
            )
        lines.append("")
        lines.append(f"**Realized P&L today: {_fmt_money(realized_today_dollars)}**")
    else:
        lines.append("_No combo closes today._")
    lines.append("")

    # 4. Cumulative summary
    lines.append("## Cumulative (paper-run-to-date)")
    lines.append("")
    lines.append(f"- Realized combos:    **{pnl_summary['realized_count']}**  "
                 f"(open: {pnl_summary['open_count']})")
    lines.append(f"- Wins / losses:      "
                 f"**{pnl_summary['win_count']} / {pnl_summary['loss_count']}**  ·  "
                 f"win rate "
                 f"{(pnl_summary['win_rate'] or 0)*100:.1f}%")
    lines.append(f"- Mean win:           {_fmt_money(pnl_summary['mean_win'])}")
    lines.append(f"- Mean loss:          {_fmt_money(pnl_summary['mean_loss'])}")
    lines.append(f"- Expectancy/combo:   {_fmt_money(pnl_summary['expectancy'])}")
    lines.append(f"- **Total realized:** {_fmt_money(pnl_summary['total_realized'])}")
    lines.append("")

    # 5. Scan filters today
    lines.append("## Scan filters today")
    lines.append("")
    if scan_today["total_filtered"] > 0:
        lines.append(f"Total filtered passes today: **{scan_today['total_filtered']}**")
        lines.append("")
        lines.append("| Reason | Count |")
        lines.append("|---|---:|")
        for reason, n in sorted(
            scan_today["totals_by_reason"].items(),
            key=lambda x: x[1], reverse=True,
        ):
            lines.append(f"| `{reason}` | {n} |")
    else:
        lines.append("_No symbols filtered today (either all passed or no scan ran)._")
    lines.append("")

    # 6. Slippage
    lines.append("## Slippage")
    lines.append("")
    lines.append(f"- Today: **{slip_today['summary']['n']}** combo fills, "
                 f"mean {_fmt_money(slip_today['summary']['mean_slippage'])}, "
                 f"max {_fmt_money(slip_today['summary']['max_slippage'])}.")
    lines.append(f"- Cumulative: **{slip_cum['summary']['n']}** combo fills, "
                 f"mean {_fmt_money(slip_cum['summary']['mean_slippage'])}, "
                 f"p90 {_fmt_money(slip_cum['summary']['p90_slippage'])}.")
    lines.append("")

    # 7. Circuit breaker
    lines.append("## Circuit breaker")
    lines.append("")
    paused_until = cb.get("paused_until")
    if paused_until:
        lines.append(f"⚠️ PAUSED until **{paused_until}**")
    lines.append(f"- consecutive_losses: {cb.get('consecutive_losses', 0)}")
    lines.append(f"- drawdown_hwm:       {_fmt_money(cb.get('drawdown_hwm'))}")
    recent = cb.get("recent_pnl") or []
    if recent:
        lines.append(f"- recent P&L tail:    {recent[-5:]}")
    lines.append("")

    # 8. IVR / adjustment health
    lines.append("## IVR-bucketed win rate (cumulative)")
    lines.append("")
    lines.append("| Bucket | Count | Win rate | Mean P&L |")
    lines.append("|---|---:|---:|---:|")
    for b in ivr_report["buckets"]:
        wr = b["win_rate"]
        wr_s = f"{wr*100:.1f}%" if wr is not None else "—"
        lines.append(
            f"| {b['label']} | {b['count']} | {wr_s} | "
            f"{_fmt_money(b['mean_pnl_dollars'])} |"
        )
    lines.append("")

    lines.append("## Adjusted vs unadjusted (cumulative)")
    lines.append("")
    a = adjust_report["adjusted"]; u = adjust_report["unadjusted"]
    lines.append(f"| | Adjusted | Unadjusted |")
    lines.append(f"|---|---:|---:|")
    lines.append(f"| Count | {a['count']} | {u['count']} |")
    lines.append(
        f"| Win rate | "
        f"{(a['win_rate'] or 0)*100:.1f}% | "
        f"{(u['win_rate'] or 0)*100:.1f}% |"
    )
    lines.append(f"| Mean P&L | {_fmt_money(a['mean_pnl'])} | "
                 f"{_fmt_money(u['mean_pnl'])} |")
    lines.append(f"| Total P&L | {_fmt_money(a['total_pnl'])} | "
                 f"{_fmt_money(u['total_pnl'])} |")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="ic-daily-digest",
        description="Daily digest for the IC paper run.",
    )
    p.add_argument("--db", default=DEFAULT_DB_URL)
    p.add_argument("--date", help="YYYY-MM-DD (default: today UTC)")
    p.add_argument("--out", help="write to file instead of stdout")
    args = p.parse_args(argv)

    if args.date:
        try:
            d = date.fromisoformat(args.date)
        except ValueError:
            print(f"ERROR: invalid --date {args.date!r} (want YYYY-MM-DD)",
                  file=sys.stderr)
            return 2
    else:
        d = datetime.now(timezone.utc).date()

    text = render_digest(day=d, db_url=args.db)
    if args.out:
        from pathlib import Path
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        # Use ASCII output to avoid Windows console encoding issues; the
        # only non-ASCII char in our template is the ⚠️ which we conditionally
        # emit only when paused.
        try:
            print(text)
        except UnicodeEncodeError:
            sys.stdout.buffer.write(text.encode("utf-8"))
            sys.stdout.write("\n")
    return 0


if __name__ == "__main__":   # pragma: no cover
    sys.exit(main())
