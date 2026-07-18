#!/usr/bin/env python3
"""Drift-gated, idempotent patcher for the RH-auth resilience batch (ITEMS 1/2/3).

Runs ON PROD. Additive changes at unique anchors -> no full-file transfer, no risk of
reverting prod-only content. Modes:
  --dry-run  (default): drift-gate every anchor + print a unified diff per file. No writes.
  --apply             : .bak each file, apply, py_compile. Aborts loudly on any drift/compile fail.
  --files a,b         : restrict to specific files (optional).

Does NOT restart the engine or touch the template (rh_session_panel.html is a NEW file the
deploy runner scp's separately) or main.py's ExecStart. Idempotent: re-running is a no-op
(each op is skipped if its MARKER is already present).

Batch files: utils/secrets.py, brokers/robinhood.py, agents/data_exec.py, web/routes.py, main.py
"""
import argparse
import difflib
import os
import py_compile
import sys

ROOT = "/home/azureuser/trading_corp/trading_corp"

# Base md5 captured 2026-07-18 (drift anchor; --apply re-checks anchors, not md5, so light
# drift is tolerated as long as anchors are intact — mismatch here is informational).
BASE_MD5 = {
    "utils/secrets.py": "851415cc30f950f45f2e4c312d83a4c2",
    "brokers/robinhood.py": "72f7944c73abc2c02a71b3f8644ed53c",
    "agents/data_exec.py": "51281fbdd44096b224c0f9062ac4a3e7",
    "web/routes.py": "1b7611e0b37789a97be19114062061da",
    "main.py": "3faca1f3f047aa7be2916fdfd7907323",
}

# ---- change blocks -----------------------------------------------------------------

SECRETS_TUPLE_ANCHOR = '        "FIDELITY_ACCOUNT",\n'
SECRETS_TUPLE_ADD = (
    '        "FIDELITY_ACCOUNT",\n'
    '        "ROBINHOOD_USERNAME",  # ITEM1-RH-KV\n'
    '        "ROBINHOOD_PASSWORD",\n'
    '        "ROBINHOOD_MFA_SECRET",\n'
)
SECRETS_LOOP_OLD = (
    "    loaded = 0\n"
    "    for env_name in expected_env_vars:\n"
    "        if os.getenv(env_name):\n"
    "            # Already set — env takes precedence so local overrides work\n"
    "            continue\n"
)
SECRETS_LOOP_NEW = (
    "    # ITEM1-KV-AUTHORITATIVE: KV wins for RH creds; unit-env is the break-glass fallback\n"
    "    # (a KV/MI blip must not strand RH auth). All other secrets keep env-precedence.\n"
    '    _KV_AUTHORITATIVE = frozenset({"ROBINHOOD_USERNAME", "ROBINHOOD_PASSWORD", "ROBINHOOD_MFA_SECRET"})\n'
    "    loaded = 0\n"
    "    for env_name in expected_env_vars:\n"
    "        _authoritative = env_name in _KV_AUTHORITATIVE\n"
    "        if os.getenv(env_name) and not _authoritative:\n"
    "            # Already set — env takes precedence so local overrides work\n"
    "            continue\n"
)

ROBINHOOD_IMPORTS_ANCHOR = "import re\n"
ROBINHOOD_IMPORTS_ADD = "import re\nimport os  # ITEM3-RH-AUTH\nimport sys\nimport time\n"

ROBINHOOD_STATE_ANCHOR = "_ACCOUNT_LIST: list[dict] = []   # cached account list from /accounts/ endpoint\n"
ROBINHOOD_STATE_ADD = '''_ACCOUNT_LIST: list[dict] = []   # cached account list from /accounts/ endpoint

# ITEM3-RH-AUTH: process-wide RH auth-health state (shared across all RH divisions)
_AUTH_LOCK = Lock()
_auth_down = False
_auth_down_since = None
_auth_last_good = None
_reauth_last_ts = 0.0
_REAUTH_MIN_INTERVAL_S = 300   # GUARD 1: >= a real outage cools before we hit /login again (429 guard)
_REAUTH_TIMEOUT_S = 15         # GUARD 2: fresh-file validate <1s; a stale-file challenge exceeds this
_auth_alert_hook = None        # set by main.py wiring -> data_exec._on_rh_auth_change


class RobinhoodAuthError(Exception):
    """Marker only; snapshot() self-heals and does NOT raise."""
'''

ROBINHOOD_METHODS_ANCHOR = "    async def disconnect(self) -> None:\n"
ROBINHOOD_METHODS_ADD = '''    # ITEM3-REAUTH: active 401 sentinel + guarded in-process re-login + latch -----------
    async def _auth_is_401(self) -> bool:
        """ACTIVE sentinel — robin_stocks swallows a 401 (returns None/0, no raise); read the
        status ourselves via a raw request (mirrors robin_stocks' own pickle validation)."""
        import robin_stocks.robinhood as rs  # type: ignore
        try:
            res = await asyncio.to_thread(
                rs.helper.request_get,
                rs.urls.positions_url(self._account_number or None),
                "pagination", {"nonzero": "true"}, False,
            )
        except Exception:
            return False
        return getattr(res, "status_code", None) == 401

    def _login_input_neutered(self, mfa_code) -> None:
        """rs.login with stdin -> /dev/null so an sms/email challenge input() raises EOFError
        immediately (GUARD 2 belt-and-suspenders; engine stdin is already systemd-null)."""
        import robin_stocks.robinhood as rs  # type: ignore
        old = sys.stdin
        try:
            sys.stdin = open(os.devnull)
            rs.login(self._username, self._password, mfa_code=mfa_code, store_session=True)
        finally:
            try:
                sys.stdin.close()
            except Exception:
                pass
            sys.stdin = old

    async def _attempt_reauth(self, *, force: bool = False, timeout: float = _REAUTH_TIMEOUT_S) -> bool:
        """ONE guarded in-process re-login off the pickle file. GUARD 1 backoff (auto path) +
        GUARD 2 timeout. force=True (ITEM 2 button) skips backoff, uses a longer timeout."""
        global _LOGIN_DONE, _reauth_last_ts
        now = time.monotonic()
        if not force:
            with _AUTH_LOCK:
                if now - _reauth_last_ts < _REAUTH_MIN_INTERVAL_S:
                    return False   # GUARD 1: don't hammer /login (429 hazard)
                _reauth_last_ts = now
        mfa_code = None
        if self._mfa_secret:
            try:
                import pyotp  # type: ignore
                mfa_code = pyotp.TOTP(self._mfa_secret).now()
            except ImportError:
                pass
        with _LOGIN_LOCK:
            _LOGIN_DONE = False
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._login_input_neutered, mfa_code),
                timeout=timeout,   # GUARD 2
            )
        except Exception as e:  # TimeoutError / EOFError / login error
            log.warning("RH in-process reauth did not complete (%s: %s) — file likely stale, escalating",
                        type(e).__name__, e)
            return False
        if await self._auth_is_401():
            return False
        with _LOGIN_LOCK:
            _LOGIN_DONE = True
        return True

    def _note_auth_state(self, *, down: bool, reason: str) -> None:
        """Update the shared latch; fire the alert hook ONLY on a down/recovered TRANSITION."""
        global _auth_down, _auth_down_since, _auth_last_good
        _ts = datetime.now(timezone.utc).isoformat()
        transition = None
        with _AUTH_LOCK:
            was = _auth_down
            if down and not was:
                _auth_down = True; _auth_down_since = _ts; transition = "down"
            elif (not down) and was:
                _auth_down = False; _auth_last_good = _ts; transition = "recovered"
            elif not down:
                _auth_last_good = _ts
        if transition == "down":
            log.error("rh_auth_failed: Robinhood session DOWN (reason=%s) — NO entries and NO exits on live positions, all RH accounts", reason)
        elif transition == "recovered":
            log.info("rh_auth_recovered: Robinhood session restored (reason=%s)", reason)
        if transition and _auth_alert_hook is not None:
            try:
                asyncio.get_running_loop().create_task(
                    _auth_alert_hook(down, {"reason": reason, "since": _auth_down_since, "last_good": _auth_last_good}))
            except RuntimeError:
                pass

    async def disconnect(self) -> None:
'''

ROBINHOOD_SNAPSHOT_OLD = "        profile = profile or {}\n"
ROBINHOOD_SNAPSHOT_NEW = '''        profile = profile or {}
        # ITEM3-SNAPSHOT: empty profile on a real RH account = the 401 signature. Confirm
        # actively, then try ONE guarded in-process reload (silent recover if the file is fresh).
        if not profile and await self._auth_is_401():
            import robin_stocks.robinhood as rs  # type: ignore
            if await self._attempt_reauth():
                profile = await asyncio.to_thread(
                    rs.profiles.load_portfolio_profile, self._account_number or None) or {}
                self._note_auth_state(down=False, reason="in_process_reauth_ok")
            else:
                self._note_auth_state(down=True, reason="reauth_failed_or_stale_file")
                # fall through: empty profile -> equity 0 / positions [] (existing), NO raise
        elif profile:
            self._note_auth_state(down=False, reason="snapshot_ok")
'''

DATA_EXEC_ANCHOR = "    async def _handle_stale_snapshot(\n"
DATA_EXEC_ADD = '''    async def _on_rh_auth_change(self, down: bool, info: dict) -> None:
        # ITEM3-AUTH-HOOK: RobinhoodBroker auth-state hook, fired ONCE per transition (latch de-dups).
        kind = "rh_auth_failed" if down else "rh_auth_recovered"
        ts = iso(now_utc())
        payload = {"reason": info.get("reason"), "since": info.get("since"),
                   "last_good": info.get("last_good"),
                   "accounts": ["680725082", "461391328", "934310442", "116637293063"], "ts": ts}
        audit_id = self.logger.log_event(actor="data_exec", kind=kind, payload=payload)
        if audit_id is not None:
            try:
                with db.connect(self.logger.db_url) as conn:
                    if conn.execute("SELECT 1 FROM audit_event WHERE id=?", (audit_id,)).fetchone() is None:
                        log.error("%s audit_id=%s could NOT be re-read", kind, audit_id)
            except Exception as e:
                log.warning("audit re-read after %s failed: %s", kind, e)
        if down:
            text = ("RH SESSION DOWN — Robinhood auth failing (401). NO new entries AND NO exits "
                    "on live positions; broker-wide (PEAD / PMCC / IRA / joint). "
                    f"since {info.get('since')}. In-process reload could not recover (pickle likely "
                    "stale) — approve a refresh: dashboard 'Refresh RH pickle' button.")
        else:
            text = f"RH session RECOVERED (auth restored). last good {info.get('last_good')}."
        await self._safety_push(text, audit_path="safety_alert",
                                audit_context={"division": "robinhood", "kind": kind})

    async def _handle_stale_snapshot(
'''

ROUTES_ANCHOR = '        return _render_action_pill(f"PEAD HALTED · {closed} closed")\n'
ROUTES_ADD = r'''        return _render_action_pill(f"PEAD HALTED · {closed} closed")

    # ITEM2-RH-ROUTES: RH session health + refresh button ------------------------------
    _RH_AGENT, _RH_KEY = "robinhood_session", "refresh_status"
    _BUTTON_REAUTH_TIMEOUT_S = 90

    def _rh_health_ctx():
        import os, time
        from trading_corp.brokers import robinhood as _rh
        pickle = os.path.expanduser("~/.tokens/robinhood.pickle")
        try:
            age_s = int(time.time() - os.stat(pickle).st_mtime)
        except OSError:
            age_s = -1
        rec = _db_mod.load_agent_state(_RH_AGENT, _RH_KEY, db_url=deps.db_url)
        st = rec[0] if (rec and isinstance(rec[0], dict)) else {}
        return {"auth_state": "down" if getattr(_rh, "_auth_down", False) else "valid",
                "down_since": getattr(_rh, "_auth_down_since", None),
                "last_good": getattr(_rh, "_auth_last_good", None),
                "pickle_age_s": age_s, "refresh": st.get("state", "idle"),
                "refresh_msg": st.get("msg", "")}

    @app.get("/api/rh/session-health", response_class=HTMLResponse)
    async def rh_session_health(request: Request):
        return templates.TemplateResponse(request, "rh_session_panel.html", {"health": _rh_health_ctx()})

    @app.post("/api/rh/refresh-session", response_class=HTMLResponse)
    async def rh_refresh_session(request: Request):
        db_url = deps.db_url
        rec = _db_mod.load_agent_state(_RH_AGENT, _RH_KEY, db_url=db_url)
        if rec and isinstance(rec[0], dict) and rec[0].get("state") in ("running", "push_sent"):
            return templates.TemplateResponse(request, "rh_session_panel.html", {"health": _rh_health_ctx()})
        broker = deps.data_exec.brokers.get("robinhood_pead") or next(
            (b for k, b in deps.data_exec.brokers.items() if k.startswith("robinhood")), None)

        async def _run():
            try:
                _db_mod.set_agent_state(_RH_AGENT, _RH_KEY,
                    {"state": "push_sent", "msg": "approve the push on your phone", "ts": _now_iso()}, db_url=db_url)
                ok = False
                if broker is not None and hasattr(broker, "_attempt_reauth"):
                    ok = await broker._attempt_reauth(force=True, timeout=_BUTTON_REAUTH_TIMEOUT_S)
                _db_mod.set_agent_state(_RH_AGENT, _RH_KEY,
                    {"state": "valid" if ok else "failed",
                     "msg": "" if ok else "login not confirmed (push not approved / sms-email / creds)",
                     "ts": _now_iso()}, db_url=db_url)
            except Exception as e:  # noqa: BLE001
                _db_mod.set_agent_state(_RH_AGENT, _RH_KEY,
                    {"state": "failed", "msg": str(e), "ts": _now_iso()}, db_url=db_url)

        _db_mod.set_agent_state(_RH_AGENT, _RH_KEY, {"state": "running", "msg": "", "ts": _now_iso()}, db_url=db_url)
        asyncio.create_task(_run())
        return templates.TemplateResponse(request, "rh_session_panel.html", {"health": _rh_health_ctx()})
'''

MAIN_ANCHOR = "    data_exec.safety_notifier = channel\n"
MAIN_ADD = ('    data_exec.safety_notifier = channel\n'
            '    from trading_corp.brokers import robinhood as _rh_mod  # ITEM3-WIRE\n'
            '    _rh_mod._auth_alert_hook = data_exec._on_rh_auth_change\n')

# (path, marker, kind, a, b)  kind: 'insert' uses replace(a->b) once; 'replace' same. Both are
# str.replace with count=1 after an idempotency + single-occurrence gate.
OPS = [
    ("utils/secrets.py", "ITEM1-RH-KV", SECRETS_TUPLE_ANCHOR, SECRETS_TUPLE_ADD),
    ("utils/secrets.py", "ITEM1-KV-AUTHORITATIVE", SECRETS_LOOP_OLD, SECRETS_LOOP_NEW),
    ("brokers/robinhood.py", "ITEM3-RH-AUTH", ROBINHOOD_IMPORTS_ANCHOR, ROBINHOOD_IMPORTS_ADD),
    ("brokers/robinhood.py", "ITEM3-RH-AUTH: process-wide", ROBINHOOD_STATE_ANCHOR, ROBINHOOD_STATE_ADD),
    ("brokers/robinhood.py", "ITEM3-REAUTH", ROBINHOOD_METHODS_ANCHOR, ROBINHOOD_METHODS_ADD),
    ("brokers/robinhood.py", "ITEM3-SNAPSHOT", ROBINHOOD_SNAPSHOT_OLD, ROBINHOOD_SNAPSHOT_NEW),
    ("agents/data_exec.py", "ITEM3-AUTH-HOOK", DATA_EXEC_ANCHOR, DATA_EXEC_ADD),
    ("web/routes.py", "ITEM2-RH-ROUTES", ROUTES_ANCHOR, ROUTES_ADD),
    ("main.py", "ITEM3-WIRE", MAIN_ANCHOR, MAIN_ADD),
]


def process(apply: bool, only):
    files = {}
    for path, *_ in OPS:
        files.setdefault(path, None)
    ok = True
    for path in files:
        if only and path not in only:
            continue
        full = os.path.join(ROOT, path)
        with open(full, "r") as f:
            orig = f.read()
        new = orig
        for p, marker, a, b in OPS:
            if p != path:
                continue
            if marker in new:
                print(f"  [skip] {path}: '{marker}' already present (idempotent)")
                continue
            n = new.count(a)
            if n != 1:
                print(f"  [DRIFT] {path}: anchor for '{marker}' found {n}x (expected 1) — ABORT")
                ok = False
                continue
            new = new.replace(a, b, 1)
            print(f"  [ok]   {path}: applied '{marker}'")
        if new == orig:
            continue
        if not apply:
            diff = difflib.unified_diff(orig.splitlines(True), new.splitlines(True),
                                        f"a/{path}", f"b/{path}")
            sys.stdout.writelines(diff)
            print()
        else:
            with open(full + ".bak_rhauth", "w") as f:
                f.write(orig)
            with open(full, "w") as f:
                f.write(new)
            try:
                py_compile.compile(full, doraise=True)
                print(f"  [APPLIED+COMPILED] {path} (.bak_rhauth saved)")
            except py_compile.PyCompileError as e:
                print(f"  [COMPILE FAIL] {path}: {e} — restore .bak_rhauth!")
                ok = False
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--files", default="")
    a = ap.parse_args()
    only = set(x.strip() for x in a.files.split(",") if x.strip())
    print(f"=== RH-auth batch patcher ({'APPLY' if a.apply else 'DRY-RUN'}) ===")
    ok = process(a.apply, only)
    print("=== OK ===" if ok else "=== FAILURES ABOVE — do NOT restart ===")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
