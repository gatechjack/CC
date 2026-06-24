"""Refresh the PROD robin_stocks session pickle (/home/azureuser/.tokens/robinhood.pickle).

Runs a deliberate, watched, device-approved login on the prod box: pulls creds from
KV (load_secrets), generates the TOTP, and calls rs.login(..., store_session=True),
which triggers Robinhood's device-approval challenge. APPROVE THE PROMPT ON THE RH
APP while this runs (it polls ~60s). On success robin_stocks writes a fresh pickle,
and we verify 680725082 is reachable read-only.

Precondition: the 429 rate-limit must have cooled off, or the device-approval poll
429s again. Run when a human is ready to approve on the phone.
"""
import robin_stocks.robinhood as rs

from trading_corp.utils.secrets import load_secrets

s = load_secrets()
code = None
if getattr(s, "robinhood_mfa_secret", None):
    import pyotp
    code = pyotp.TOTP(s.robinhood_mfa_secret).now()

print(">>> Logging in to refresh the prod pickle — APPROVE THE DEVICE PROMPT ON THE RH APP NOW <<<")
try:
    rs.login(s.robinhood_username, s.robinhood_password, mfa_code=code, store_session=True)
except Exception as e:  # noqa: BLE001
    print(f"LOGIN FAILED: {type(e).__name__}: {e}")
    raise SystemExit(1)

prof = rs.profiles.load_account_profile(account_number="680725082") or {}
ok = prof.get("account_number") == "680725082"
print("login result: 680725082 reachable =", ok,
      "| account_number =", prof.get("account_number"),
      "| buying_power =", prof.get("buying_power"))
if not ok:
    print("WARN: logged in but 680725082 not reachable — pickle may still be off.")
    raise SystemExit(2)
print("PROD PICKLE REFRESHED OK — bare rs.login() reuse should now work; v2 probe ready.")
