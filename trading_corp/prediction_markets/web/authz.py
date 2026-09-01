"""pm_web authorization -- owner-identity + admin resolution (multi-account M2/M4/M5 foundation, 2026-09-01).

★ FAIL CLOSED (R5, Jack ruled): if the Authelia identity header is ABSENT, or `PM_ADMIN_IDENTITIES` is unset/
empty, the answer is NOT-ADMIN -- never admin. This is FIRST-TIME identity plumbing; an unwired identity layer
defaulting to admin is the worst possible default, so the default is DENY. Consequence: UI admin controls (the
M5 arm/disarm toggle, promote/attach, Analyze-spend if ever gated) are INERT until PM_ADMIN_IDENTITIES is
configured -- and the CLI stays the authoritative arm/disarm path regardless (R7.d STOP must never depend on the UI).

TRUST MODEL: pm_web is loopback-only behind Authelia+Caddy (see app.py docstring), so ONLY the proxy reaches it
and the forwarded identity header is trustworthy. Authelia forwards the authenticated user as `Remote-User`
(some proxy configs use `X-Forwarded-User` / `X-Remote-User`); we accept any of them, first present wins. This is
NOT a substitute for the proxy stripping client-supplied copies -- that is the proxy's job and the precondition.

Pure helpers (identity/admin resolution) are separated from the FastAPI `request` wrapper so they unit-test
without fastapi. Read-only: reads headers + env, writes nothing.
"""
from __future__ import annotations

import os

# lower-cased; FastAPI's request.headers is case-insensitive, and we also lower-case keys in the pure helper.
_IDENTITY_HEADERS = ("remote-user", "x-forwarded-user", "x-remote-user")
_ADMIN_ENV = "PM_ADMIN_IDENTITIES"


def parse_admin_identities(env_val: str | None) -> frozenset[str]:
    """Comma/space-separated admin identity list -> a set. Unset/empty -> EMPTY set (fail-closed: nobody admin)."""
    if not env_val:
        return frozenset()
    parts = [p.strip() for p in env_val.replace(",", " ").split()]
    return frozenset(p for p in parts if p)


def identity_from_headers(headers) -> str | None:
    """The authenticated identity from the first present Authelia header, or None. `headers` is any case-
    insensitive-ish mapping; we probe both the exact and lower-cased key so a plain dict works in tests too."""
    if headers is None:
        return None
    def _get(k):
        # FastAPI Headers is case-insensitive; a plain dict is not -> try a few spellings.
        for cand in (k, k.title(), k.upper()):
            try:
                v = headers.get(cand)
            except Exception:
                v = None
            if v:
                return v
        return None
    for h in _IDENTITY_HEADERS:
        v = _get(h)
        if v and str(v).strip():
            return str(v).strip()
    return None


def is_admin_identity(identity: str | None, admins: frozenset[str]) -> bool:
    """Pure admin test. TRUE only if a non-empty identity is in a non-empty admin set. Every other case
    (identity None, admin set empty) -> FALSE. This is the fail-closed core."""
    return bool(identity) and bool(admins) and identity in admins


# ── FastAPI request wrappers (thin; delegate to the pure helpers) ──────────────────────────────────────────────
def current_identity(request) -> str | None:
    """The logged-in Authelia identity for this request, or None (unauthenticated at the app layer)."""
    return identity_from_headers(getattr(request, "headers", None))


def is_admin(request) -> bool:
    """Is the requester an admin? Fail-closed: no header -> no identity -> not admin; PM_ADMIN_IDENTITIES unset
    -> empty set -> not admin. The CLI remains the authoritative kill path irrespective of this."""
    return is_admin_identity(current_identity(request), parse_admin_identities(os.getenv(_ADMIN_ENV)))
