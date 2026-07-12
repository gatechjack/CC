"""Bitunix SFP — "LLM Trade Analysis" dashboard (the 3rd SFP screen, alongside /sfp + /sfp/construct).

DEPLOY TARGET: trading_corp/web/sfp_llm_analysis_view.py  (engine web module — served IN-PROCESS by the
engine's uvicorn app, exactly like sfp_cockpit_view + sfp_construct_cockpit_view). Wire with one line in
trading_corp/web/routes.py register(app):
    from trading_corp.web import sfp_llm_analysis_view
    sfp_llm_analysis_view.register(app)
★Because it's engine-served, adding this route needs a FLAT-GUARDED engine restart (RH-pickle pre-flight)
— same as the /sfp/construct cockpit deploy. Config is untouched (no halt).

PURE DISPLAY. It reads the Market-Context Recorder's OWN db (market_context.db) STRICTLY READ-ONLY
(mode=ro) — the engine is NOT a writer of that file (the recorder is the sole writer), so isolation is
preserved: this screen adds a read-only reader, never a 2nd writer. It shows the stored LLM shadow-logger
rows per trade + the news/sentiment at fire + (once the nightly backfill pairs it) the realized outcome.

★HONESTY DISCIPLINE (same as the construct cockpit):
  * real recorder rows or an honest "accumulating — no verdict yet" state; NEVER fabricated.
  * a grade→outcome VERDICT is GATED behind n>=SIG_N paired fills; below that it shows "accumulating
    n/N — not yet significant" and NEVER implies significance on a handful of trades.
  * the LLM is a LOGGED CHALLENGER to the mechanical spine — a banner states it does NOT gate any trade.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# fastapi imported at MODULE TOP (not lazily) so the `request: Request` handler annotation resolves —
# with `from __future__ import annotations` the annotation is a STRING, and FastAPI's get_type_hints()
# only resolves it against MODULE globals (a lazy import inside register() is invisible → FastAPI would
# treat `request` as a required query param → 422). Mirrors sfp_construct_cockpit_view exactly.
# build_llm_analysis_view (the read-only data layer) does not use fastapi; it stays independently testable.
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

log = logging.getLogger(__name__)

SIG_N = 30                    # paired (LLM-graded + outcome) fills before a grade→R verdict is meaningful
CARD_LIMIT = 60               # most-recent reviewed fires to show


def _mctx_db_path() -> str:
    return os.environ.get("MCTX_DB_PATH",
                          str(Path.home() / "market_context" / "market_context.db"))


def _connect_ro(path: str):
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _fmt(ts_iso):
    try:
        return datetime.fromisoformat(ts_iso).astimezone(timezone.utc).strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        return ts_iso or ""


def build_llm_analysis_view(db_path: str | None = None) -> dict:
    """Read the recorder's llm_shadow + llm_news + trade_outcome rows (READ-ONLY) into a display dict.
    Honest-empty when the recorder db is absent / has no LLM rows yet. Never raises to the route."""
    path = db_path or _mctx_db_path()
    empty = {"has_data": False, "cards": [], "n_graded": 0, "sig_n": SIG_N,
             "news": None, "agg": None, "note": "accumulating — no LLM-reviewed fires yet",
             "db_missing": not Path(path).exists()}
    if not Path(path).exists():
        return empty
    try:
        conn = _connect_ro(path)
    except sqlite3.Error as e:
        log.warning("llm-analysis: cannot open recorder db (%s)", e)
        return empty
    try:
        # trade_fire snapshots that carry ANY llm_shadow row (graded OR a degraded gap — both are honest).
        snaps = conn.execute(
            "SELECT s.snapshot_id, s.snapshot_ts, s.linked_trade_id, s.coin FROM context_snapshot s "
            "WHERE s.snapshot_type='trade_fire' AND EXISTS "
            "(SELECT 1 FROM context_kv k WHERE k.snapshot_id=s.snapshot_id AND k.stream='llm_shadow') "
            "ORDER BY s.snapshot_ts DESC LIMIT ?", (CARD_LIMIT,)).fetchall()
        cards = []
        for s in snaps:
            sid = s["snapshot_id"]
            sh = {r["field"]: dict(r) for r in conn.execute(
                "SELECT field, value_num, value_text, is_stale, source FROM context_kv "
                "WHERE snapshot_id=? AND stream='llm_shadow'", (sid,)).fetchall()}
            nw = {r["field"]: dict(r) for r in conn.execute(
                "SELECT field, coin, value_num, value_text FROM context_kv "
                "WHERE snapshot_id=? AND stream='llm_news' AND (coin IS NULL OR coin=?)",
                (sid, s["coin"])).fetchall()}
            oc = conn.execute(
                "SELECT result, actual_r_multiple FROM trade_outcome WHERE order_id=?",
                (s["linked_trade_id"],)).fetchone()

            def _num(f):
                r = sh.get(f)
                return None if r is None else r["value_num"]

            def _txt(f, d=""):
                r = sh.get(f)
                return d if r is None else (r["value_text"] or d)

            graded = sh.get("llm_grade") is not None and sh["llm_grade"]["value_num"] is not None
            agrees = _num("llm_agrees_detector")
            # per-coin news sentiment for this trade's coin, if present; else the market-wide skew.
            coin_sent = next((r["value_text"] for f, r in nw.items()
                              if f == "coin_sentiment" and r["coin"] == s["coin"]), None)
            cards.append({
                "ts": _fmt(s["snapshot_ts"]), "coin": s["coin"], "order_id": s["linked_trade_id"],
                "graded": graded,
                "grade": int(_num("llm_grade")) if graded else None,
                "llm_regime": _txt("llm_regime"), "detector_regime": _txt("detector_regime"),
                "detector_gate": _txt("detector_gate"), "side": _txt("side"),
                "agrees": (None if agrees is None else bool(agrees)),
                "reasoning": _txt("llm_reasoning"), "caution": _txt("llm_caution"),
                "model": _txt("model"), "prompt_version": _txt("prompt_version"),
                "unavailable_reason": (None if graded else _txt("status", "unavailable")),
                "news_skew": (nw.get("sentiment_skew") or {}).get("value_text") if nw.get("sentiment_skew") else None,
                "news_narrative": (nw.get("dominant_narrative") or {}).get("value_text") if nw.get("dominant_narrative") else None,
                "coin_sentiment": coin_sent,
                "result": oc["result"] if oc else None,
                "r": (round(oc["actual_r_multiple"], 3) if oc and oc["actual_r_multiple"] is not None else None),
            })

        # latest market-wide news snapshot (header panel) — newest llm_news regardless of type.
        news = None
        nrow = conn.execute(
            "SELECT snapshot_id, snapshot_ts FROM context_snapshot s WHERE EXISTS "
            "(SELECT 1 FROM context_kv k WHERE k.snapshot_id=s.snapshot_id AND k.stream='llm_news' "
            "AND k.field='sentiment_skew') ORDER BY snapshot_ts DESC LIMIT 1").fetchone()
        if nrow:
            nk = {(r["field"], r["coin"]): dict(r) for r in conn.execute(
                "SELECT field, coin, value_num, value_text, is_stale FROM context_kv "
                "WHERE snapshot_id=? AND stream='llm_news'", (nrow["snapshot_id"],)).fetchall()}
            def _nv(f):
                r = nk.get((f, None))
                return r["value_text"] if r else None
            news = {
                "ts": _fmt(nrow["snapshot_ts"]), "skew": _nv("sentiment_skew"),
                "magnitude": (nk.get(("sentiment_magnitude", None)) or {}).get("value_num"),
                "narrative": _nv("dominant_narrative"), "catalysts": _nv("notable_catalysts"),
                "feed": _nv("source_feed"), "model": _nv("model"),
                "per_coin": {c: (nk.get(("coin_sentiment", c)) or {}).get("value_text")
                             for c in ("BTC", "ETH", "SOL", "XRP")},
                "stale": bool((nk.get(("sentiment_skew", None)) or {}).get("is_stale")),
            }

        graded_cards = [c for c in cards if c["graded"]]
        paired = [c for c in graded_cards if c["r"] is not None]
        agg = None
        if graded_cards:
            n = len(graded_cards)
            avg_grade = round(sum(c["grade"] for c in graded_cards) / n, 1)
            n_agree = sum(1 for c in graded_cards if c["agrees"])
            agg = {
                "n_graded": n, "avg_grade": avg_grade,
                "agree_pct": round(100 * n_agree / n),
                "n_paired": len(paired), "sig_n": SIG_N,
                "significant": len(paired) >= SIG_N,
                "verdict_note": (f"accumulating {len(paired)}/{SIG_N} paired fills — "
                                 "grade→outcome not yet significant") if len(paired) < SIG_N else "",
            }
            # grade→R split ONLY when significant (never imply an edge on a tiny sample)
            if len(paired) >= SIG_N:
                med = sorted(c["grade"] for c in paired)[len(paired) // 2]
                hi = [c["r"] for c in paired if c["grade"] >= med]
                lo = [c["r"] for c in paired if c["grade"] < med]
                agg["hi_grade_avg_r"] = round(sum(hi) / len(hi), 3) if hi else None
                agg["lo_grade_avg_r"] = round(sum(lo) / len(lo), 3) if lo else None
        return {"has_data": bool(cards), "cards": cards, "n_graded": len(graded_cards),
                "sig_n": SIG_N, "news": news, "agg": agg,
                "note": "" if cards else "accumulating — no LLM-reviewed fires yet", "db_missing": False}
    except sqlite3.Error as e:
        log.warning("llm-analysis: read failed (%s)", e)
        return empty
    finally:
        conn.close()


def register(app: FastAPI) -> None:
    templates = app.state.templates

    import asyncio

    async def _view():
        return await asyncio.to_thread(build_llm_analysis_view)

    @app.get("/sfp/llm", response_class=HTMLResponse)
    async def sfp_llm_analysis(request: Request):
        return templates.TemplateResponse(request, "sfp_llm_analysis.html", {"v": await _view()})

    @app.get("/sfp/llm/partials/body", response_class=HTMLResponse)
    async def sfp_llm_analysis_body(request: Request):
        return templates.TemplateResponse(request, "sfp_llm_analysis/_body.html", {"v": await _view()})

    log.info("SFP LLM Analysis routes registered at /sfp/llm")
