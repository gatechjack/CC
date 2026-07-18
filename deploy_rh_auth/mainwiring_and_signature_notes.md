# Two small edits that tie ITEM 3 together (RESTART-gated, batch)

## 1. main.py — wire the alert hook (ONE line)
Where `data_exec.safety_notifier` is set (broker agent cited ~main.py:1069), add:

```python
    data_exec.safety_notifier = channel                     # existing
+   from trading_corp.brokers import robinhood as _rh       # ITEM 3
+   _rh._auth_alert_hook = data_exec._on_rh_auth_change      # broker fires this on 401 down/recovered
```

## 2. robinhood.py — `_attempt_reauth` signature (for the ITEM 2 button)
The robinhood_py.patch defines `_attempt_reauth(self)`. Bump the signature so the button
route can force a full login with a longer push window:

```python
    async def _attempt_reauth(self, *, force: bool = False, timeout: float = _REAUTH_TIMEOUT_S) -> bool:
        global _LOGIN_DONE, _reauth_last_ts
        now = time.monotonic()
        if not force:                                        # GUARD 1 backoff (auto path only)
            with _AUTH_LOCK:
                if now - _reauth_last_ts < _REAUTH_MIN_INTERVAL_S:
                    return False
                _reauth_last_ts = now
        # ... unchanged: reset _LOGIN_DONE, TOTP-if-mfa, wait_for(to_thread(_login_input_neutered), timeout) ...
```
Auto path (snapshot) calls `_attempt_reauth()` (force=False, 15s). Button calls
`_attempt_reauth(force=True, timeout=90)`. GUARD 2 (timeout + stdin-null) applies to both.

## No-MFA note (this account has no MFA)
`robinhood_mfa_secret` resolves to None everywhere (KV has no ROBINHOOD-MFA-SECRET). Every
path already guards on it (`if self._mfa_secret:` / `if getattr(s,'robinhood_mfa_secret',None):`),
so `mfa_code=None` is passed and RH uses its device-approval **app prompt** (polled,
headless-safe — what the 7/18 manual refresh used). No code change for no-MFA. GUARD 2 still
covers the sms/email edge (input() -> EOFError under systemd-null stdin + the hard timeout).
