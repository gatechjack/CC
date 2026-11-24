# Batch 1 result -- Caddy + Authelia upgrade (2026-11-24, EXECUTED)

Executed via `az vm run-command` (root) running the staged+hash-verified `secrev_batch1.sh`.
trading-corp was NOT restarted. Rollback point: Azure snapshot `tc-prod-os-20261124` +
pinned binary backup `/home/azureuser/secrev_batch1_backup_20261124T040003Z` (ORIGINAL 2.11.4/4.39.19).

## Result
- **Caddy 2.11.4 -> 2.11.9**: new binary extracted from deb (usr/bin/caddy), `caddy validate` of the live
  Caddyfile PASSED with the new binary, deb installed, graceful `systemctl reload`. caddy active v2.11.9.
- **Authelia 4.39.19 -> 4.39.28**: tarball + combined `checksums.sha256` -> sha256 MATCH
  (e8ad9ff965cbf3b93138945e13cb4b7874c999c254772b2bb77952863177699c). Binary `authelia` v4.39.28.
  `validate-config` classification (key names only): IGNORED identity_validation.reset_password.jwt_secret +
  storage.encryption_key (both injected via the service inline Environment=, not readable/visible to a
  standalone validate; confirmed the only two errors). 0 BLOCKING errors -> proceed.
  Swap -> start -> HEALTH GATE: /api/health 200 within 30s AND errors-since-start=0 -> HEALTHY.

## Independent read-only verify (post-run)
- caddy=v2.11.9 active; authelia=v4.39.28 active; trading-corp=active.
- authelia /api/health = 200.
- authelia level=error SINCE 04:28:39Z restart = 0 (the last-50 "14" were pre-restart 408 i/o-timeouts).
- https://trading.jacksumner.com/ -> 302 redirect to auth.jacksumner.com (forward_auth working).
- GET /webhook/tradingview/lord-otter -> 405 (reachable, method-gated).
- malformed Host header -> 400 (edge still rejects BadHost vectors).

## Remaining
- Jack to log in once at https://trading.jacksumner.com to confirm password+TOTP end-to-end.
- Rollback (if ever needed): `az vm run-command ... --scripts "bash /home/azureuser/secrev_batch1_rollback.sh"`
  restores the PINNED original 4.39.19 + prior caddy deb/Caddyfile.
