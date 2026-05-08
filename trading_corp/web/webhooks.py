"""TradingView webhook routes.

Single endpoint right now: POST /webhook/tradingview/lord-otter.
Future TV-driven strategies hang off the same path prefix.

Security model:
  1. Shared secret in JSON body, constant-time compared. The secret
     env var name comes from `lord_otter.webhook_secret_env` in
     strategies.yaml (default LORD_OTTER_WEBHOOK_SECRET).
  2. IP allowlist of TradingView's published webhook IPs.
  3. Replay protection — reject if `time` is older than 60s or
     more than 60s in the future (clock skew tolerance).
  4. Body size limit of 4KB (FastAPI default is generous; we cap explicitly).

Order-routing model (Phase 1):
  - Webhook → LordOtterAgent.on_alert() → maybe a ProposedOrder
  - ProposedOrder → risk_agent.evaluate() → maybe approved + resized
  - If approved AND auto_execute=true → data_exec.place()
  - If approved AND auto_execute=false → log "would_have_placed",
    Telegram-notify, do NOT place.
  - All paths write to the audit log with a structured payload so
    later analysis can replay decisions.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse

from trading_corp.persistence import db as _db
from trading_corp.persistence.models import AccountState, PaperTradeRecord, StrategyState
from trading_corp.utils.time import iso, now_utc

log = logging.getLogger(__name__)

# TradingView's published webhook source IPs (as of 2025/2026).
# Update if TV publishes new ones; allow override via env for testing.
_TV_WEBHOOK_IPS = {
    "52.89.214.238",
    "34.212.75.30",
    "54.218.53.128",
    "52.32.178.7",
}

# Maximum body size we'll accept on a webhook POST. Real TV alerts
# are well under 1KB; cap at 4KB to swallow any reasonable expansion
# while rejecting obvious abuse.
_MAX_BODY_BYTES = 4 * 1024

# How much clock skew to tolerate on the `time` field.
#
# TradingView's `{{time}}` placeholder is the BAR OPEN time, not the
# alert-fire time. With "Once Per Bar Close" trigger the alert fires
# at bar close, so the apparent skew is exactly bar_duration:
#   1m bars  →  60s
#   3m bars  → 180s
#   5m bars  → 300s
#   15m bars → 900s
#   1h bars  → 3600s
#
# A 60s window rejected every 3m+ alert with a 400 "timestamp skew".
# Bump to 1200s (20 min) to support 1m through 15m bars cleanly with
# headroom. The shared secret remains the primary auth defense; the
# replay window only narrows the window for an attacker who already
# captured a valid signed body. At this scope, 20-min replay tolerance
# is fine.
_REPLAY_WINDOW_SEC = 1200


def register(app: FastAPI) -> None:
    """Register webhook routes against the FastAPI app.

    Called from web/routes.py:register() so all routes (UI + webhooks)
    end up on the same FastAPI instance with the same `deps`.
    """
    deps = app.state.deps

    # Announce IP-check state at boot so it's obvious which mode the
    # webhook is in. Without this, `LORD_OTTER_DISABLE_IP_CHECK` mismatches
    # silently reject all traffic with 403, which is hard to debug from
    # the client side.
    ip_disabled = os.getenv("LORD_OTTER_DISABLE_IP_CHECK") == "1"
    log.warning(
        "lord-otter webhook IP allowlist: %s. "
        "Allowed IPs without env override: TV's published webhook IPs + localhost.",
        "DISABLED (relying on shared secret only)" if ip_disabled
        else "ENFORCED",
    )

    @app.post("/webhook/tradingview/lord-otter")
    async def lord_otter_webhook(
        request: Request, background_tasks: BackgroundTasks,
    ) -> JSONResponse:
        """Receive a TradingView alert and route it through Lord Otter agent.

        **Return-fast architecture (2026-05-02):** the synchronous phase
        of this handler does only validation + the `webhook_received`
        audit row, then dispatches the heavy processing
        (broker snapshot → agent.on_alert → research consult → risk
        gate → place/notify) onto a FastAPI background task and returns
        HTTP 200 in well under TradingView's 10s timeout. Background
        task writes its own audit rows for each branch (alert_ignored,
        risk_rejected, would_have_placed, filled, execution_error)
        and Telegram-notifies on terminal states. If the background
        task crashes, an `agent_error` audit row is written and the
        Board is notified via Telegram.

        Response shape (all 200/4xx/5xx happen in the SYNC phase):
          200 + {"status":"accepted", "signal":"..."} when validation passes
          401 / 403 on auth failures
          400 on malformed body
          503 on misconfiguration (agent not wired, server-side secret unset)
        """
        agent = getattr(deps, "lord_otter_agent", None)
        if agent is None:
            log.warning("lord-otter webhook hit but agent not wired in deps")
            return JSONResponse(
                {"status": "error", "reason": "lord_otter agent not configured"},
                status_code=503,
            )

        # ------------------------------------------------------------------
        # 1. IP allowlist (skipped if the request comes from localhost so
        #    you can curl it yourself for testing). For production, you
        #    can also set LORD_OTTER_DISABLE_IP_CHECK=1 to bypass when
        #    behind a reverse proxy that mangles client IPs.
        # ------------------------------------------------------------------
        client_ip = (request.client.host if request.client else "") or ""
        if not _is_ip_allowed(client_ip):
            log.warning("lord-otter webhook rejected: IP %s not in allowlist", client_ip)
            _audit_rejected(deps, "ip_blocked", client_ip, b"")
            return JSONResponse(
                {"status": "rejected", "reason": "source IP not allowed"},
                status_code=403,
            )

        # ------------------------------------------------------------------
        # 2. Body size + JSON parsing.
        #    Lenient parsing: TV alert bodies sometimes get an alert-name
        #    prefix (e.g. "Large Water sell 3m. {...}"). Strict json.loads
        #    rejects that with 400, and we lose the signal silently. So we
        #    extract the JSON substring (first { ... last }) before parsing
        #    as a fallback. We log a warning so we know the alert body has
        #    a prefix issue worth fixing — but the signal still gets through.
        # ------------------------------------------------------------------
        raw = await request.body()
        if len(raw) > _MAX_BODY_BYTES:
            log.warning("lord-otter webhook rejected: body %d bytes > cap", len(raw))
            _audit_rejected(deps, "body_too_large", client_ip, raw)
            return JSONResponse(
                {"status": "rejected", "reason": "body too large"},
                status_code=400,
            )

        payload, parse_warning = _lenient_json_parse(raw)
        if payload is None:
            log.warning("lord-otter webhook rejected: bad JSON (raw=%r)", raw[:200])
            _audit_rejected(deps, "malformed_json", client_ip, raw)
            return JSONResponse(
                {"status": "rejected", "reason": "malformed JSON"},
                status_code=400,
            )
        if not isinstance(payload, dict):
            _audit_rejected(deps, "json_not_object", client_ip, raw)
            return JSONResponse(
                {"status": "rejected", "reason": "JSON body must be an object"},
                status_code=400,
            )
        if parse_warning:
            log.warning(
                "lord-otter webhook: lenient JSON recovery applied (%s) — "
                "alert body has extraneous text outside JSON; consider cleaning the alert message.",
                parse_warning,
            )

        # ------------------------------------------------------------------
        # 3. Shared-secret check (constant time)
        # ------------------------------------------------------------------
        expected = os.getenv(agent.webhook_secret_env, "") or ""
        provided = str(payload.get("secret", "") or "")
        if not expected:
            log.error(
                "lord-otter webhook misconfigured: env var %s is empty",
                agent.webhook_secret_env,
            )
            # Audit-trail hole fix (2026-05-01): write a row so the
            # Board sees misconfiguration in the dashboard, not just in
            # the systemd journal. Caught a real-world Cypher outage
            # that ran for 7+ days without a single audit row.
            _audit_rejected(deps, "server_side_secret_unset", client_ip, raw)
            return JSONResponse(
                {"status": "error", "reason": "server-side secret not set"},
                status_code=503,
            )
        if not hmac.compare_digest(expected, provided):
            log.warning("lord-otter webhook rejected: bad secret")
            _audit_rejected(deps, "bad_secret", client_ip, raw)
            return JSONResponse(
                {"status": "rejected", "reason": "auth failed"},
                status_code=401,
            )

        # ------------------------------------------------------------------
        # 4. Replay protection
        # ------------------------------------------------------------------
        ts = _parse_ts(payload.get("time"))
        now = now_utc()
        skew_sec = abs((now - ts).total_seconds())
        if skew_sec > _REPLAY_WINDOW_SEC:
            log.warning(
                "lord-otter webhook rejected: ts skew %.1fs > %ds",
                skew_sec, _REPLAY_WINDOW_SEC,
            )
            _audit_rejected(
                deps, f"timestamp_skew_{int(skew_sec)}s", client_ip, raw,
            )
            return JSONResponse(
                {"status": "rejected", "reason": f"timestamp skew {skew_sec:.0f}s"},
                status_code=400,
            )

        # ------------------------------------------------------------------
        # 5. Symbol normalization (BTCUSD → BTC/USD for ccxt unified)
        # ------------------------------------------------------------------
        raw_ticker = str(payload.get("ticker", "") or "").upper()
        symbol = _normalize_symbol(raw_ticker)
        payload["symbol"] = symbol  # stash normalized form for the agent

        # ------------------------------------------------------------------
        # 6. ALWAYS audit the inbound alert (raw + normalized).
        #    Do this before agent dispatch so we have a record even if
        #    the agent throws.
        # ------------------------------------------------------------------
        deps.logger_agent.log_event(
            actor="lord_otter", kind="webhook_received",
            payload={
                # `strategy` + `division` tags let the dashboard's per-division
                # activity rail (`_query_division_activity`) match these events
                # against the coinbase_spot division. Without them, lord_otter
                # events are correctly written but invisible in the UI.
                "strategy": "lord_otter",
                "division": agent.division,
                "signal": payload.get("signal"),
                "ticker": raw_ticker,
                "symbol": symbol,
                "price": payload.get("price"),
                "time": payload.get("time"),
                "interval": payload.get("interval"),
                "client_ip": client_ip,
            },
        )

        # ------------------------------------------------------------------
        # 7. Dispatch heavy processing onto a background task and return
        #    HTTP 200 immediately. TradingView's 10s timeout no longer
        #    matters — even if the research-firm consult takes 30s, we've
        #    already responded.
        # ------------------------------------------------------------------
        background_tasks.add_task(
            _process_lord_otter_alert,
            deps=deps, agent=agent, payload=payload, symbol=symbol,
        )
        return JSONResponse({
            "status": "accepted",
            "signal": payload.get("signal"),
            "symbol": symbol,
        })

    # ============================================================
    # Market Cypher webhook — second TV-driven agent.
    # Architectural twin of the Lord Otter handler above. Same flow:
    # IP check → body+JSON → secret → replay window → symbol normalize
    # → audit-inbound → snapshot broker → agent.on_alert → risk gate →
    # place vs notify. Strategy-specific differences live INSIDE the
    # agent (signal vocab, tier classifier); the webhook plumbing is
    # parallel.
    # ============================================================
    cypher_ip_disabled = os.getenv("MARKET_CYPHER_DISABLE_IP_CHECK") == "1"
    log.warning(
        "market-cypher webhook IP allowlist: %s. "
        "Allowed IPs without env override: TV's published webhook IPs + localhost.",
        "DISABLED (relying on shared secret only)" if cypher_ip_disabled
        else "ENFORCED",
    )

    @app.post("/webhook/tradingview/market-cypher")
    async def market_cypher_webhook(
        request: Request, background_tasks: BackgroundTasks,
    ) -> JSONResponse:
        """Receive a TradingView alert and route through Market Cypher agent.

        Same return-fast architecture as `lord_otter_webhook` (see that
        handler's docstring for the rationale). Synchronous phase
        validates + audits webhook_received; the heavy processing
        (broker snapshot → on_alert → research consult → risk gate →
        place/notify) runs as a FastAPI background task. TV gets a fast
        200 in <500ms regardless of downstream load.

        Response shape (all 200/4xx/5xx happen in the SYNC phase):
          200 + {"status":"accepted", "signal":"..."} when validation passes
          401 / 403 on auth failures, 400 on malformed body
          503 on misconfiguration (agent not wired, server-side secret unset)
        """
        agent = getattr(deps, "market_cypher_agent", None)
        if agent is None:
            log.warning("market-cypher webhook hit but agent not wired in deps")
            return JSONResponse(
                {"status": "error", "reason": "market_cypher agent not configured"},
                status_code=503,
            )

        # 1. IP allowlist (same `_is_ip_allowed` helper but checks the
        #    Cypher-specific env var override flag).
        client_ip = (request.client.host if request.client else "") or ""
        if not _is_ip_allowed_cypher(client_ip):
            log.warning("market-cypher webhook rejected: IP %s not in allowlist", client_ip)
            _audit_rejected(
                deps, "ip_blocked", client_ip, b"",
                actor="market_cypher", strategy_name="market_cypher",
                log_prefix="market-cypher",
            )
            return JSONResponse(
                {"status": "rejected", "reason": "source IP not allowed"},
                status_code=403,
            )

        # 2. Body size + lenient JSON parse
        raw = await request.body()
        if len(raw) > _MAX_BODY_BYTES:
            log.warning("market-cypher webhook rejected: body %d bytes > cap", len(raw))
            _audit_rejected(
                deps, "body_too_large", client_ip, raw,
                actor="market_cypher", strategy_name="market_cypher",
                log_prefix="market-cypher",
            )
            return JSONResponse(
                {"status": "rejected", "reason": "body too large"},
                status_code=400,
            )

        payload, parse_warning = _lenient_json_parse(raw)
        if payload is None:
            log.warning("market-cypher webhook rejected: bad JSON (raw=%r)", raw[:200])
            _audit_rejected(
                deps, "malformed_json", client_ip, raw,
                actor="market_cypher", strategy_name="market_cypher",
                log_prefix="market-cypher",
            )
            return JSONResponse(
                {"status": "rejected", "reason": "malformed JSON"},
                status_code=400,
            )
        if not isinstance(payload, dict):
            _audit_rejected(
                deps, "json_not_object", client_ip, raw,
                actor="market_cypher", strategy_name="market_cypher",
                log_prefix="market-cypher",
            )
            return JSONResponse(
                {"status": "rejected", "reason": "JSON body must be an object"},
                status_code=400,
            )
        if parse_warning:
            log.warning(
                "market-cypher webhook: lenient JSON recovery applied (%s)",
                parse_warning,
            )

        # 3. Shared-secret check
        expected = os.getenv(agent.webhook_secret_env, "") or ""
        provided = str(payload.get("secret", "") or "")
        if not expected:
            log.error(
                "market-cypher webhook misconfigured: env var %s is empty",
                agent.webhook_secret_env,
            )
            # Audit-trail hole fix (2026-05-01): write a row so the
            # Board sees misconfiguration in the dashboard. The original
            # silent 503 path masked a 7-day Cypher outage.
            _audit_rejected(
                deps, "server_side_secret_unset", client_ip, raw,
                actor="market_cypher", strategy_name="market_cypher",
                log_prefix="market-cypher",
            )
            return JSONResponse(
                {"status": "error", "reason": "server-side secret not set"},
                status_code=503,
            )
        if not hmac.compare_digest(expected, provided):
            log.warning("market-cypher webhook rejected: bad secret")
            _audit_rejected(
                deps, "bad_secret", client_ip, raw,
                actor="market_cypher", strategy_name="market_cypher",
                log_prefix="market-cypher",
            )
            return JSONResponse(
                {"status": "rejected", "reason": "auth failed"},
                status_code=401,
            )

        # 4. Replay protection (same _REPLAY_WINDOW_SEC — already
        #    sized to handle 4h bars).
        ts = _parse_ts(payload.get("time"))
        now = now_utc()
        skew_sec = abs((now - ts).total_seconds())
        # Cypher operates on 4h/1D bars — 4h = 14400s, 1D = 86400s.
        # The default 1200s window would reject every Cypher alert.
        # Use a wider window for Cypher specifically: 25h (covers 1D bars
        # with healthy headroom).
        cypher_replay_window = 25 * 3600
        if skew_sec > cypher_replay_window:
            log.warning(
                "market-cypher webhook rejected: ts skew %.1fs > %ds",
                skew_sec, cypher_replay_window,
            )
            _audit_rejected(
                deps, f"timestamp_skew_{int(skew_sec)}s", client_ip, raw,
                actor="market_cypher", strategy_name="market_cypher",
                log_prefix="market-cypher",
            )
            return JSONResponse(
                {"status": "rejected", "reason": f"timestamp skew {skew_sec:.0f}s"},
                status_code=400,
            )

        # 5. Symbol normalization
        raw_ticker = str(payload.get("ticker", "") or "").upper()
        symbol = _normalize_symbol(raw_ticker)
        payload["symbol"] = symbol

        # 6. Always audit inbound (mirrors Otter's behavior).
        deps.logger_agent.log_event(
            actor="market_cypher", kind="webhook_received",
            payload={
                "strategy": "market_cypher",
                "division": agent.division,
                "signal": payload.get("signal"),
                "ticker": raw_ticker,
                "symbol": symbol,
                "price": payload.get("price"),
                "time": payload.get("time"),
                "interval": payload.get("interval"),
                "client_ip": client_ip,
            },
        )

        # 7. Dispatch heavy processing onto a background task and return
        #    HTTP 200 immediately. See the parallel comment in
        #    lord_otter_webhook for the architectural rationale.
        background_tasks.add_task(
            _process_market_cypher_alert,
            deps=deps, agent=agent, payload=payload, symbol=symbol,
        )
        return JSONResponse({
            "status": "accepted",
            "signal": payload.get("signal"),
            "symbol": symbol,
        })


# ----------------------------------------------------------------------
# Background-processing helpers (return-fast architecture, 2026-05-02)
# ----------------------------------------------------------------------
#
# These run after the webhook handler has already responded HTTP 200 to
# TradingView. They do the heavy lifting: broker snapshot, agent state
# machine, research-firm consult (multi-LLM, can be 15-30s), risk gate,
# Telegram notify, and (when auto_execute=true) actual order placement.
#
# Whatever happens here, the caller is gone. Outcomes are observable
# only via:
#   - audit_event rows (alert_ignored, risk_rejected, would_have_placed,
#     filled, execution_error, agent_error)
#   - Telegram notifications (would_have_placed, filled, research veto,
#     and the catch-all background-crash notify added below)
#
# Any unhandled exception MUST be caught + audited + Telegram-notified
# so a silent crash can't masquerade as "TV got 200, I'm done." The
# wrapping `try/except` at the bottom of each function is the contract.


async def _process_lord_otter_alert(
    *, deps: Any, agent: Any, payload: dict, symbol: str,
) -> None:
    """Background processing of a Lord Otter TV alert.

    Runs after the webhook handler returned 200 to TradingView. Writes
    audit rows for every decision branch (alert_ignored, risk_rejected,
    would_have_placed, filled, execution_error). Telegram-notifies the
    Board on terminal states. On any unhandled exception, writes an
    `agent_error` audit row + Telegram-notifies, so silent crashes are
    impossible.
    """
    try:
        # ── Broker snapshot for sizing + held-qty lookup ─────────────
        snap = None
        account_equity = 0.0
        held_qty: dict[str, float] = {}
        broker = (
            deps.data_exec.brokers.get(agent.division)
            if deps.data_exec else None
        )
        if broker is not None:
            try:
                snap = await broker.snapshot()
                account_equity = float(getattr(snap, "equity", 0.0) or 0.0)
                for pos in (getattr(snap, "positions", []) or []):
                    sym = getattr(pos, "symbol", "")
                    if sym:
                        held_qty[sym] = float(getattr(pos, "qty", 0) or 0)
            except Exception as e:
                log.warning(
                    "lord-otter: snapshot failed for sizing/held lookup: %s", e,
                )

        # ── Agent state machine ───────────────────────────────────────
        try:
            order, decision = agent.on_alert(
                payload,
                account_equity=account_equity,
                held_qty=held_qty,
            )
        except Exception as e:
            log.exception("lord-otter agent.on_alert raised")
            deps.logger_agent.log_event(
                actor="lord_otter", kind="agent_error",
                payload={
                    "strategy": "lord_otter",
                    "division": agent.division,
                    "signal": payload.get("signal"), "error": str(e),
                },
            )
            return

        if order is None:
            log.info("lord-otter ignored: %s [signal=%s symbol=%s]",
                     decision, payload.get("signal"), symbol)
            deps.logger_agent.log_event(
                actor="lord_otter", kind="alert_ignored",
                payload={
                    "strategy": "lord_otter",
                    "division": agent.division,
                    "signal": payload.get("signal"),
                    "symbol": symbol,
                    "reason": decision,
                },
            )
            return

        # ── Research firm TradeConfirmation consult (Phase 1e) ───────
        from trading_corp.agents.research.trade_confirmation_consult import (
            consult_research_for_trade_confirmation,
        )
        consult = await consult_research_for_trade_confirmation(
            order=order,
            payload=payload,
            research_firm=getattr(deps, "research_firm", None),
            logger_agent=deps.logger_agent,
            division_slug="lord_otter",
            asset_class="crypto_spot",
            account_equity=account_equity,
        )
        if consult.decision == "skip":
            await _telegram_notify(
                deps,
                (
                    f"\U0001F6D1 lord-otter: research vetoed "
                    f"{order.side} {order.symbol}\n"
                    f"{consult.rationale}"
                ),
                log_prefix="lord-otter",
            )
            return
        order = consult.order  # type: ignore[assignment]
        if consult.verdict_kind == "conditional":
            log.info(
                "lord-otter: research applied modifications: %s",
                consult.applied_changes,
            )

        # ── Risk gate ─────────────────────────────────────────────────
        if broker is None:
            log.warning("lord-otter: broker for division=%s not registered",
                        agent.division)
            deps.logger_agent.log_event(
                actor="lord_otter", kind="execution_error",
                payload={"order_id": order.id, "reason": "broker not registered"},
            )
            return

        account = AccountState(
            account=getattr(snap, "account", agent.division) if snap else agent.division,
            equity=account_equity or 100_000.0,
            peak_equity=account_equity or 100_000.0,
        )
        strat_state = StrategyState(strategy=order.strategy)
        regime = "unknown"
        if deps.trend_agent is not None:
            try:
                regime = getattr(deps.trend_agent.read(), "regime", "unknown") or "unknown"
            except Exception:
                regime = "unknown"

        verdict = deps.risk_agent.evaluate(order, account, strat_state, regime, None)
        order.risk_reason = verdict.reason

        if verdict.verdict == "reject":
            order.status = "risk_rejected"
            deps.logger_agent.log_proposed_order(order)
            deps.logger_agent.log_event(
                actor="risk", kind="risk_rejected",
                payload={
                    "order_id": order.id, "symbol": order.symbol,
                    "reason": verdict.reason, "via": "lord_otter_webhook",
                    "tier": (order.extra or {}).get("tier"),
                },
            )
            log.info("lord-otter risk-rejected: %s [%s]", verdict.reason, order.id)
            return

        if verdict.verdict == "resize" and verdict.new_qty is not None:
            log.info(
                "lord-otter risk resized: %s qty %s → %s",
                order.symbol, order.qty, verdict.new_qty,
            )
            order.qty = float(verdict.new_qty)

        # ── Place vs notify ───────────────────────────────────────────
        if not agent.auto_execute:
            order.status = "would_have_placed"
            deps.logger_agent.log_proposed_order(order)
            deps.logger_agent.log_event(
                actor="lord_otter", kind="would_have_placed",
                payload={
                    "strategy": "lord_otter",
                    "division": agent.division,
                    "order_id": order.id,
                    "symbol": order.symbol,
                    "side": order.side,
                    "qty": order.qty,
                    "rationale": order.rationale,
                    "tier": (order.extra or {}).get("tier"),
                    "decision": decision,
                },
            )
            _record_paper_trade(deps, order, "lord_otter", agent)
            await _telegram_notify(
                deps,
                _format_would_have_placed_msg(order, decision),
            )
            return

        # auto_execute=true → fire it
        order.status = "board_approved"
        order.board_reason = "lord_otter auto_execute=true"
        deps.logger_agent.log_proposed_order(order)
        deps.logger_agent.log_event(
            actor="board", kind="board_approved",
            payload={
                "order_id": order.id, "symbol": order.symbol,
                "via": "lord_otter_webhook", "auto_execute": True,
                "tier": (order.extra or {}).get("tier"),
            },
        )

        try:
            fill = await deps.data_exec.place(order, division=agent.division)
            log.info(
                "lord-otter placed: %s %s qty=%s fill=$%.2f venue=%s",
                order.side, order.symbol, order.qty, fill.price, fill.venue,
            )
            await _telegram_notify(
                deps,
                _format_filled_msg(order, fill, decision),
            )
        except Exception as e:
            log.warning("lord-otter place(%s) failed: %s", order.id, e)
            deps.logger_agent.log_event(
                actor="data_exec", kind="execution_error",
                payload={
                    "order_id": order.id, "symbol": order.symbol,
                    "error": str(e), "via": "lord_otter_webhook",
                },
            )

    except Exception as e:
        # Catch-all so a background crash never goes silent. Audit row
        # + Telegram so the Board sees it.
        log.exception("lord-otter background processing crashed")
        try:
            deps.logger_agent.log_event(
                actor="lord_otter", kind="agent_error",
                payload={
                    "strategy": "lord_otter",
                    "division": getattr(agent, "division", "unknown"),
                    "signal": (payload or {}).get("signal"),
                    "error": str(e),
                    "phase": "background_processing",
                },
            )
        except Exception:
            log.exception("lord-otter background: even the audit-write failed")
        try:
            await _telegram_notify(
                deps,
                f"⚠️ lord-otter background crash on "
                f"signal={(payload or {}).get('signal')!r}: {type(e).__name__}: {e}",
                log_prefix="lord-otter",
            )
        except Exception:
            pass


async def _process_market_cypher_alert(
    *, deps: Any, agent: Any, payload: dict, symbol: str,
) -> None:
    """Background processing of a Market Cypher TV alert.

    Architectural twin of `_process_lord_otter_alert` — see that function's
    docstring for the rationale. Differences are strategy-specific
    audit-row tags ('market_cypher' vs 'lord_otter') and Telegram-notify
    formatters; the orchestration is identical.
    """
    try:
        # ── Broker snapshot ──────────────────────────────────────────
        snap = None
        account_equity = 0.0
        held_qty: dict[str, float] = {}
        broker = (
            deps.data_exec.brokers.get(agent.division)
            if deps.data_exec else None
        )
        if broker is not None:
            try:
                snap = await broker.snapshot()
                account_equity = float(getattr(snap, "equity", 0.0) or 0.0)
                for pos in (getattr(snap, "positions", []) or []):
                    sym = getattr(pos, "symbol", "")
                    if sym:
                        held_qty[sym] = float(getattr(pos, "qty", 0) or 0)
            except Exception as e:
                log.warning(
                    "market-cypher: snapshot failed for sizing/held lookup: %s", e,
                )

        # ── Agent state machine ──────────────────────────────────────
        try:
            order, decision = agent.on_alert(
                payload,
                account_equity=account_equity,
                held_qty=held_qty,
            )
        except Exception as e:
            log.exception("market-cypher agent.on_alert raised")
            deps.logger_agent.log_event(
                actor="market_cypher", kind="agent_error",
                payload={
                    "strategy": "market_cypher",
                    "division": agent.division,
                    "signal": payload.get("signal"), "error": str(e),
                },
            )
            return

        if order is None:
            log.info("market-cypher ignored: %s [signal=%s symbol=%s]",
                     decision, payload.get("signal"), symbol)
            deps.logger_agent.log_event(
                actor="market_cypher", kind="alert_ignored",
                payload={
                    "strategy": "market_cypher",
                    "division": agent.division,
                    "signal": payload.get("signal"),
                    "symbol": symbol,
                    "reason": decision,
                },
            )
            return

        # ── Research firm consult ────────────────────────────────────
        from trading_corp.agents.research.trade_confirmation_consult import (
            consult_research_for_trade_confirmation,
        )
        consult = await consult_research_for_trade_confirmation(
            order=order,
            payload=payload,
            research_firm=getattr(deps, "research_firm", None),
            logger_agent=deps.logger_agent,
            division_slug="market_cypher",
            asset_class="crypto_spot",
            account_equity=account_equity,
        )
        if consult.decision == "skip":
            await _telegram_notify(
                deps,
                (
                    f"\U0001F6D1 market-cypher: research vetoed "
                    f"{order.side} {order.symbol}\n"
                    f"{consult.rationale}"
                ),
                log_prefix="market-cypher",
            )
            return
        order = consult.order  # type: ignore[assignment]
        if consult.verdict_kind == "conditional":
            log.info(
                "market-cypher: research applied modifications: %s",
                consult.applied_changes,
            )

        # ── Risk gate ────────────────────────────────────────────────
        if broker is None:
            log.warning("market-cypher: broker for division=%s not registered",
                        agent.division)
            deps.logger_agent.log_event(
                actor="market_cypher", kind="execution_error",
                payload={"order_id": order.id, "reason": "broker not registered"},
            )
            return

        account = AccountState(
            account=getattr(snap, "account", agent.division) if snap else agent.division,
            equity=account_equity or 100_000.0,
            peak_equity=account_equity or 100_000.0,
        )
        strat_state = StrategyState(strategy=order.strategy)
        regime = "unknown"
        if deps.trend_agent is not None:
            try:
                regime = getattr(deps.trend_agent.read(), "regime", "unknown") or "unknown"
            except Exception:
                regime = "unknown"

        verdict = deps.risk_agent.evaluate(order, account, strat_state, regime, None)
        order.risk_reason = verdict.reason

        if verdict.verdict == "reject":
            order.status = "risk_rejected"
            deps.logger_agent.log_proposed_order(order)
            deps.logger_agent.log_event(
                actor="risk", kind="risk_rejected",
                payload={
                    "order_id": order.id, "symbol": order.symbol,
                    "reason": verdict.reason, "via": "market_cypher_webhook",
                    "tier": (order.extra or {}).get("tier"),
                },
            )
            log.info("market-cypher risk-rejected: %s [%s]", verdict.reason, order.id)
            return

        if verdict.verdict == "resize" and verdict.new_qty is not None:
            log.info(
                "market-cypher risk resized: %s qty %s → %s",
                order.symbol, order.qty, verdict.new_qty,
            )
            order.qty = float(verdict.new_qty)

        # ── Place vs notify ──────────────────────────────────────────
        if not agent.auto_execute:
            order.status = "would_have_placed"
            deps.logger_agent.log_proposed_order(order)
            deps.logger_agent.log_event(
                actor="market_cypher", kind="would_have_placed",
                payload={
                    "strategy": "market_cypher",
                    "division": agent.division,
                    "order_id": order.id,
                    "symbol": order.symbol,
                    "side": order.side,
                    "qty": order.qty,
                    "rationale": order.rationale,
                    "tier": (order.extra or {}).get("tier"),
                    "decision": decision,
                },
            )
            _record_paper_trade(deps, order, "market_cypher", agent)
            await _telegram_notify(
                deps,
                _format_would_have_placed_msg_cypher(order, decision),
                log_prefix="market-cypher",
            )
            return

        # auto_execute=true → fire it
        order.status = "board_approved"
        order.board_reason = "market_cypher auto_execute=true"
        deps.logger_agent.log_proposed_order(order)
        deps.logger_agent.log_event(
            actor="board", kind="board_approved",
            payload={
                "order_id": order.id, "symbol": order.symbol,
                "via": "market_cypher_webhook", "auto_execute": True,
                "tier": (order.extra or {}).get("tier"),
            },
        )

        try:
            fill = await deps.data_exec.place(order, division=agent.division)
            log.info(
                "market-cypher placed: %s %s qty=%s fill=$%.2f venue=%s",
                order.side, order.symbol, order.qty, fill.price, fill.venue,
            )
            await _telegram_notify(
                deps,
                _format_filled_msg_cypher(order, fill, decision),
                log_prefix="market-cypher",
            )
        except Exception as e:
            log.warning("market-cypher place(%s) failed: %s", order.id, e)
            deps.logger_agent.log_event(
                actor="data_exec", kind="execution_error",
                payload={
                    "order_id": order.id, "symbol": order.symbol,
                    "error": str(e), "via": "market_cypher_webhook",
                },
            )

    except Exception as e:
        log.exception("market-cypher background processing crashed")
        try:
            deps.logger_agent.log_event(
                actor="market_cypher", kind="agent_error",
                payload={
                    "strategy": "market_cypher",
                    "division": getattr(agent, "division", "unknown"),
                    "signal": (payload or {}).get("signal"),
                    "error": str(e),
                    "phase": "background_processing",
                },
            )
        except Exception:
            log.exception("market-cypher background: audit-write failed")
        try:
            await _telegram_notify(
                deps,
                f"⚠️ market-cypher background crash on "
                f"signal={(payload or {}).get('signal')!r}: {type(e).__name__}: {e}",
                log_prefix="market-cypher",
            )
        except Exception:
            pass


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _is_ip_allowed(ip: str) -> bool:
    """Allowlist check — accepts TV's IPs, localhost, and an env override."""
    if not ip:
        return False
    if os.getenv("LORD_OTTER_DISABLE_IP_CHECK") == "1":
        return True
    if ip in _TV_WEBHOOK_IPS:
        return True
    if ip in {"127.0.0.1", "::1", "localhost"}:
        return True
    # ngrok / Cloudflare Tunnel / reverse proxy: most strip client IP and
    # show their tunnel egress IP. If you're using one of these, set
    # LORD_OTTER_DISABLE_IP_CHECK=1 in .env. The shared secret is the
    # primary defense in those cases.
    return False


def _is_ip_allowed_cypher(ip: str) -> bool:
    """Same as `_is_ip_allowed` but reads `MARKET_CYPHER_DISABLE_IP_CHECK`
    instead of the Lord-Otter env. Independent kill-switches per agent
    so we can tighten or loosen IP enforcement on each separately."""
    if not ip:
        return False
    if os.getenv("MARKET_CYPHER_DISABLE_IP_CHECK") == "1":
        return True
    if ip in _TV_WEBHOOK_IPS:
        return True
    if ip in {"127.0.0.1", "::1", "localhost"}:
        return True
    return False


def _parse_ts(raw: Any) -> datetime:
    if raw is None:
        return now_utc()
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        s = str(raw).replace("Z", "+00:00")
        ts = datetime.fromisoformat(s)
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    except Exception:
        return now_utc()


def _normalize_symbol(ticker: str) -> str:
    """Map TV ticker style to ccxt unified ('BTC/USD').

    TV typically sends 'BTCUSD' (no separator) or 'COINBASE:BTCUSD'.
    Strip exchange prefix, then insert the slash.
    """
    if not ticker:
        return ticker
    if ":" in ticker:
        ticker = ticker.split(":", 1)[1]
    if "/" in ticker:
        return ticker.upper()
    # 6-char tickers ending in USD/USDT/USDC: split base/quote
    for quote in ("USDT", "USDC", "USD"):
        if ticker.endswith(quote) and len(ticker) > len(quote):
            return f"{ticker[:-len(quote)]}/{quote}"
    return ticker.upper()


def _record_paper_trade(deps, order, strategy: str, agent) -> None:
    """Write the structured paper_trade_record row at would_have_placed time.

    Phase B (BACKLOG.md 2026-05-01): captures the trade specs alongside the
    audit row so the Phase C replay job can JOIN against price history and
    populate result fields. Failures here are logged but don't abort the
    push — the audit_event row is still the source of truth, this table is
    a denormalized convenience.
    """
    try:
        max_hold = int(getattr(agent, "max_hold_seconds", 0) or 0) or None
        record = PaperTradeRecord.from_order(
            order,
            strategy=strategy,
            division=agent.division,
            max_hold_seconds=max_hold,
        )
        _db.insert_paper_trade_record(record.to_db_row(), db_url=deps.db_url)
    except Exception as e:
        log.warning("paper_trade_record write failed for %s: %s", order.id, e)


def _format_would_have_placed_msg(order, decision: str) -> str:
    """Build a Telegram-Markdown-safe push message.

    Phase A enrichment (BACKLOG.md 2026-05-01): renders the full trade
    card — entry, stop, take-profit, R:R, expected P&L — instead of
    the prior one-liner. Stop + TP fields are populated by
    `_build_order` in lord_otter.py; missing fields are gracefully
    omitted so this still works on legacy orders predating the
    enrichment.

    TelegramChannel.push uses parse_mode='Markdown' (legacy, not MarkdownV2).
    Legacy mode is finicky:
      - underscores anywhere in plain text START italic. An unmatched/
        ambiguously-paired underscore raises BadRequest.
      - backslash-escapes inside non-code text don't work.
      - dots and parens in plain text are fine.
    Strategy: only use *bold* and `code` constructs. NO italic. Rationale
    text goes inside backticks so any underscores in it are literal.
    """
    return _format_trade_card(order, decision, header_emoji="🦦", header_name="Lord Otter")


def _format_trade_card(order, decision: str, *, header_emoji: str, header_name: str) -> str:
    """Shared rich-trade-card renderer for Otter + Cypher push.

    Renders entry / stop / TP / risk:reward / expected P&L. Lines for
    missing data degrade silently. Output stays within Telegram's
    legacy-Markdown safe surface (no italics, signal text in backticks).
    """
    extra = order.extra or {}
    tier = (extra.get("tier") or "?").upper()
    side = order.side.upper()
    sym = order.symbol
    qty = order.qty
    sig = extra.get("source_signal", "?")
    pct = float(extra.get("size_pct_equity", 0) or 0) * 100
    notional = float(extra.get("notional_target", 0) or 0)

    entry = extra.get("entry_reference_price")
    stop_price = extra.get("stop_price")
    stop_basis = extra.get("stop_basis", "?")
    stop_distance_pct = float(extra.get("stop_distance_pct", 0) or 0) * 100
    max_dollar_risk = float(extra.get("max_dollar_risk", 0) or 0)

    tp_price = extra.get("take_profit_price")
    tp_r = float(extra.get("tp_r_multiple", 0) or 0)
    tp_pct = float(extra.get("tp_distance_pct", 0) or 0) * 100
    expected_gain = float(extra.get("expected_gain_if_tp_hit", 0) or 0)

    lines: list[str] = [f"{header_emoji} *{header_name} — {tier}*"]
    lines.append(f"signal: `{sig}`")

    # Entry line — fall back to the order shape if entry_reference_price
    # missing (legacy orders).
    if entry is not None:
        lines.append(f"would *{side}* `{qty}` `{sym}` @ ~`${float(entry):,.2f}`")
    else:
        lines.append(f"would *{side}* `{qty}` `{sym}` market")

    if notional > 0:
        lines.append(f"  size: {pct:.2f}% equity (`${notional:,.2f}` notional)")
    else:
        lines.append(f"  size: {pct:.2f}% equity")

    # Stop line
    if stop_price is not None:
        sign = "-" if order.side == "buy" else "+"
        lines.append(
            f"📍 Stop: `${float(stop_price):,.2f}` "
            f"({sign}{stop_distance_pct:.2f}%, basis: `{stop_basis}`)"
        )

    # TP line — present only when computed
    if tp_price is not None:
        sign = "+" if order.side == "buy" else "-"
        lines.append(
            f"🎯 Target: `${float(tp_price):,.2f}` "
            f"({sign}{tp_pct:.2f}%, {tp_r:.1f}R)"
        )

    # Risk:Reward summary
    if max_dollar_risk > 0 and expected_gain > 0:
        rr = expected_gain / max_dollar_risk
        lines.append(
            f"💵 Risk: `-${max_dollar_risk:,.2f}`  →  "
            f"Reward: `+${expected_gain:,.2f}`  (R:R = 1:{rr:.1f})"
        )
    elif max_dollar_risk > 0:
        lines.append(f"💵 Risk: `-${max_dollar_risk:,.2f}`")

    lines.append("(auto-execute is off — no order placed)")
    lines.append(f"`{decision}`")
    return "\n".join(lines)


def _format_filled_msg(order, fill, decision: str) -> str:
    """See `_format_would_have_placed_msg` for parse-mode caveats."""
    extra = order.extra or {}
    tier = (extra.get("tier") or "?").upper()
    return (
        f"🦦 *Lord Otter — {tier}* ✅\n"
        f"*{order.side.upper()}* `{order.qty}` `{order.symbol}` "
        f"@ ${fill.price:,.2f}\n"
        f"venue: `{fill.venue}`\n"
        f"`{decision}`"
    )


def _format_would_have_placed_msg_cypher(order, decision: str) -> str:
    """Cypher's would-have-placed message. Same parse-mode rules as Otter's.

    Phase A enrichment (BACKLOG.md 2026-05-01): full trade card via the
    shared `_format_trade_card` helper. Falls back gracefully on legacy
    orders missing the TP/stop fields.
    """
    return _format_trade_card(order, decision, header_emoji="🔮", header_name="Market Cypher")


def _format_filled_msg_cypher(order, fill, decision: str) -> str:
    extra = order.extra or {}
    tier = (extra.get("tier") or "?").upper()
    return (
        f"🔮 *Market Cypher — {tier}* ✅\n"
        f"*{order.side.upper()}* `{order.qty}` `{order.symbol}` "
        f"@ ${fill.price:,.2f}\n"
        f"venue: `{fill.venue}`\n"
        f"`{decision}`"
    )


def _lenient_json_parse(raw: bytes) -> tuple[dict | None, str]:
    """Try to parse JSON from a webhook body, with prefix/suffix tolerance.

    Returns (parsed_payload, warning_message). `warning_message` is empty
    when the body was clean JSON, or a description when we had to recover
    from extraneous text.

    Real-world TradingView alert bodies sometimes look like:
        Large Water sell 3m. {"secret":"...","signal":"water_sell_large",...}
    The leading text breaks strict json.loads. We fall back to extracting
    the substring from the first '{' to the matching last '}' and try again.

    On total failure returns (None, ""). Caller treats that as malformed.
    """
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None, ""

    # Fast path: clean JSON
    try:
        return json.loads(text), ""
    except json.JSONDecodeError:
        pass

    # Recovery path: find the outermost {...} substring and parse THAT.
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace < 0 or last_brace <= first_brace:
        return None, ""
    candidate = text[first_brace : last_brace + 1]
    try:
        parsed = json.loads(candidate)
        prefix_len = first_brace
        suffix_len = len(text) - (last_brace + 1)
        warning = (
            f"recovered JSON from {len(text)}B body; "
            f"stripped {prefix_len}B prefix, {suffix_len}B suffix"
        )
        return parsed, warning
    except json.JSONDecodeError:
        return None, ""


def _audit_rejected(
    deps: Any,
    reason: str,
    client_ip: str,
    raw: bytes,
    *,
    actor: str = "lord_otter",
    strategy_name: str = "lord_otter",
    log_prefix: str = "lord-otter",
) -> None:
    """Write a webhook_rejected row to the audit log.

    `actor` / `strategy_name` / `log_prefix` parameterize the helper so
    multiple TV-driven agents (Lord Otter, Market Cypher, …) share a
    single rejection-audit code path. Defaults preserve the original
    Lord-Otter behavior so existing call sites don't change.
    """
    try:
        if not deps or not getattr(deps, "logger_agent", None):
            return
        snippet = raw[:500].decode("utf-8", errors="replace") if raw else ""
        deps.logger_agent.log_event(
            actor=actor, kind="webhook_rejected",
            payload={
                "strategy": strategy_name,
                "division": "coinbase_spot",  # default; division dispatch hadn't happened yet
                "reason": reason,
                "client_ip": client_ip,
                "raw_body_snippet": snippet,
            },
        )
    except Exception as e:
        # Never let audit-logging failures cascade — log and move on.
        log.warning("%s: webhook_rejected audit failed: %s", log_prefix, e)


async def _telegram_notify(deps: Any, msg: str, *, log_prefix: str = "lord-otter") -> None:
    """Best-effort Telegram push. `log_prefix` lets multiple TV agents
    share this helper while keeping their log lines distinguishable.

    Failure modes we surface (NOT silently swallow):
      - parse_mode rejection (Markdown ambiguity) — BadRequest
      - chat not started — Forbidden
      - bot token revoked — Unauthorized
    """
    channel = getattr(deps, "telegram_channel", None)
    if channel is None:
        log.warning("%s: no telegram channel wired; skipping notify", log_prefix)
        return
    try:
        await channel.push(msg)
        log.info("%s: telegram push sent", log_prefix)
    except Exception as e:
        log.warning(
            "%s telegram push failed (%s: %s); retrying as plain text",
            log_prefix, type(e).__name__, e,
        )
        try:
            plain = (
                msg.replace("*", "").replace("`", "").replace("_", " ")
            )
            await channel.push(plain)
        except Exception as e2:
            log.warning("%s telegram retry also failed: %s", log_prefix, e2)
