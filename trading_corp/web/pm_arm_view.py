"""M5 (2026-09-01) -- the ENGINE-WEB arm/disarm control plane at /pm/arm. Jack ruled Option A: the arm WRITE lives
on the side that owns the engine + the legacy DB, NEVER on pm_web (which is credential-free + isolation-guarded and
must NEVER write arm state -- arm.py documents that by name; see the `assumed-mechanism-was-deliberately-never-built`
lens). This module runs IN the engine process, so `arm.arm/arm.disarm` here write the SAME default-resolved legacy
`agent_state` the engine driver READS at gate-1 -- same-process CWD => guaranteed consistency, no bridge, no
isolation break.

Scope: the GLOBAL master arm/disarm (one kill disarms everything). Admin-only, FAIL-CLOSED, via the SAME
`prediction_markets.web.authz` primitive M4 uses (identity from the Authelia `Remote-User` header Caddy already
forwards to :8000; admin from `PM_ADMIN_IDENTITIES` -- which must be set on `trading-corp.service`, env-leads).

TWO HARD RIDERS (Jack 2026-09-01):
  1. THE UI NEVER CLEARS A LATCH. arm.arm() is called WITHOUT require_latch_clear, so a LATCHED global (or an
     UNREADABLE latch state -- fail-safe) raises LatchedError and we REFUSE, telling the operator to acknowledge via
     the CLI (`--clear-latch`). Only an acknowledged human CLI arm ever clears a latch -- the structural invariant.
  2. THE CLI STAYS THE AUTHORITATIVE KILL PATH (R7.d). `pm_cli live-disarm --global` works even if every web
     surface is down; this page is a CONVENIENCE, never the kill switch. The page copy says so.

Additive by construction: all logic is here; routes.py gains only `pm_arm_view.register(app)` (2 lines), so the
box-is-truth file-by-file reconcile of the SHARED routes.py is a clean graft that cannot revert other divisions.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from trading_corp.prediction_markets import arm
from trading_corp.prediction_markets.web import authz

log = logging.getLogger(__name__)

# The operator-facing outcome of a POST, surfaced as a banner on the redirected GET (PRG). Kept as a small closed
# vocabulary so the template renders a known message, never a raw exception.
_NOTICES = {
    "armed": ("GLOBAL ARM engaged -- live trading is now permitted (subject to each sub-division's own arm + caps).", "warn"),
    "disarmed": ("GLOBAL DISARM engaged -- all live placement is stopped.", "gain"),
    "latched_refused": ("Refused: a LATCHED auto-disarm is set (or the latch state is unreadable). The UI will NOT "
                        "clear a latch -- acknowledge the trigger via the CLI: pm_cli live-arm --global --clear-latch.", "loss"),
    "noop": ("No action taken (unrecognized request).", "muted"),
}


def register(app: FastAPI) -> None:
    templates = app.state.templates

    def _forbid_if_not_admin(request: Request):
        """FAIL-CLOSED admin gate (same primitive as M4). No identity / PM_ADMIN_IDENTITIES unset -> not admin ->
        403. The button is a hint; THIS is the boundary. Applied to BOTH the GET (the control page) and the POST."""
        if not authz.is_admin(request):
            return PlainTextResponse(
                "forbidden: arm/disarm is an admin-only action. The authoritative kill path is the CLI: "
                "pm_cli live-disarm --global",
                status_code=403,
            )
        return None

    def _global_state() -> dict:
        """Read-only GLOBAL arm state for display (never a write). arm.read_status() with no account/category
        returns the master row + its armed bool; fail-safe DISARMED on any read error."""
        st = arm.read_status()
        row = st.get("global") or {}
        return {
            "global_armed": bool(st.get("global_armed")),
            "latched": bool(row.get("latched")),
            "auto_trigger": row.get("auto_trigger"),
            "reason": row.get("reason"),
            "by": row.get("by"),
            "ts": row.get("ts"),
            "source": row.get("source"),
        }

    @app.get("/pm/arm", response_class=HTMLResponse)
    async def pm_arm_page(request: Request):
        forbidden = _forbid_if_not_admin(request)
        if forbidden is not None:
            return forbidden
        notice_key = (request.query_params.get("notice") or "").strip()
        notice = _NOTICES.get(notice_key)
        ctx = {"request": request, "identity": authz.current_identity(request),
               "notice_text": notice[0] if notice else None, "notice_tone": notice[1] if notice else None,
               **_global_state()}
        return templates.TemplateResponse(request, "pm_arm.html", ctx)

    @app.post("/pm/arm", response_class=HTMLResponse)
    async def pm_arm_action(request: Request):
        forbidden = _forbid_if_not_admin(request)
        if forbidden is not None:
            return forbidden
        form = await request.form()
        action = (form.get("action") or "").strip().lower()
        identity = authz.current_identity(request) or "web"
        notice = "noop"
        if action == "disarm":
            # DISARM must ALWAYS proceed (the kill direction). Preserves any existing latch (arm.disarm never clears).
            arm.disarm(None, None, reason="operator_disarm_web", by=identity, source="web", global_=True)
            log.warning("pm_arm: GLOBAL DISARM via web by identity=%s", identity)
            notice = "disarmed"
        elif action == "arm":
            try:
                # ★ RIDER 1: NO require_latch_clear. A latched (or unreadable) global -> LatchedError -> refuse.
                arm.arm(None, None, by=identity, source="web", global_=True, require_latch_clear=False)
                log.warning("pm_arm: GLOBAL ARM via web by identity=%s", identity)
                notice = "armed"
            except arm.LatchedError:
                log.warning("pm_arm: GLOBAL ARM REFUSED (latched) via web by identity=%s", identity)
                notice = "latched_refused"
        # PRG: 303 back to the GET page so a refresh re-GETs (double-submit-safe) and the banner shows the outcome.
        return RedirectResponse("/pm/arm?notice=%s" % notice, status_code=303)
