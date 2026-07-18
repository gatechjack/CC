"""Standalone daily RH re-login — GENTLE-ON-EXPIRY (ITEM 1). Runs from a systemd timer
(rh-relogin.service/.timer), NOT from the engine.

Behavior (operator decision 2026-07-18): call rs.login on the EXISTING pickle. robin_stocks
fast-paths (validates the cached token, NO device push) when it is still valid, and only
does a full login (-> device-approval push the operator approves) when the token has
actually EXPIRED. So this pushes ONLY when needed, not daily.

Why gentle (not forced-daily): this account has NO MFA. Forcing a full login daily would
mint a new device_token every day — exactly the pattern that makes RH escalate to an
sms/email challenge, the ONE path our headless automation can't answer (Finding A). And
the timer's job is now small: ITEM 3's in-process reload is the real self-heal; the timer
just keeps the pickle FILE non-stale so ITEM 3 can reload from it.

Finding B: refreshing this FILE does NOT refresh the RUNNING engine's in-memory session
(proven 2026-07-18). The engine adopts a fresh token via ITEM 3's in-process reload (on a
detected 401), the ITEM 2 dashboard button, or a restart.

Finding A / no-TTY: the service runs StandardInput=null, so if RH DOES issue an sms/email
challenge, rs.login's input() raises EOFError -> clean nonzero exit (OnFailure can alert),
never a hang.

Safety: robin_stocks writes the pickle only on a SUCCESSFUL login and does NOT delete it on
a failed validation (store_session=True), so a failed/unapproved push leaves the prior
pickle intact — no atomic-swap dance needed.

Exit 0 = session valid (fast-path no-op) or refreshed OK; nonzero = login failed/expired
and not re-approved.
"""
import os
import sys

import robin_stocks.robinhood as rs

from trading_corp.utils.secrets import load_secrets

PICKLE = os.path.expanduser("~/.tokens/robinhood.pickle")


def _log(msg: str) -> None:
    print(f"[rh_daily_relogin] {msg}", flush=True)


def main() -> int:
    s = load_secrets()
    if not (s.robinhood_username and s.robinhood_password):
        _log("FAIL: RH creds not available from load_secrets (check KV ROBINHOOD-* + expected_env_vars)")
        return 2
    code = None
    if getattr(s, "robinhood_mfa_secret", None):   # this account has no MFA -> stays None
        import pyotp
        code = pyotp.TOTP(s.robinhood_mfa_secret).now()

    before = os.stat(PICKLE).st_mtime if os.path.isfile(PICKLE) else 0
    _log("checking RH session (gentle: pushes ONLY if the token has expired) — if a device "
         "prompt appears on the RH app, APPROVE it (~60s)")
    try:
        rs.login(s.robinhood_username, s.robinhood_password, mfa_code=code, store_session=True)
        prof = rs.profiles.load_account_profile(account_number="680725082") or {}
        if prof.get("account_number") != "680725082":
            raise RuntimeError("logged in but 680725082 not reachable")
    except EOFError:
        _log("FAIL: RH issued an sms/email challenge (needs interactive code) — cannot refresh headlessly")
        return 3
    except Exception as e:  # noqa: BLE001
        _log(f"FAIL: {type(e).__name__}: {e}")
        return 1

    after = os.stat(PICKLE).st_mtime if os.path.isfile(PICKLE) else 0
    if after > before:
        _log("OK: token was expired -> refreshed (fresh pickle written). Running engine adopts "
             "it via ITEM 3 reload / ITEM 2 button / restart.")
    else:
        _log("OK: session still valid — no refresh needed, no push.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
