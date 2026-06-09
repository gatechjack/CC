# Auth lockout recovery — `trading.jacksumner.com`

You can't log into the dashboard. Find your scenario below and execute.
**SSH is the master recovery key.** All recovery paths assume you can
still reach the VM via SSH from an IP the NSG allows on port 22.
**NSG allowlist pattern (as of 2026-06-09):** a stable home-network rule
for normal access, plus temporary `/32` rules named
`temp-vpn-trip-until-<date>` for trip/VPN access. Current trip rule:
`temp-vpn-trip-until-2026-06-19` = Surfshark static VPN `92.119.177.22/32`.
(The old home Comcast IP `98.231.16.63/32`, dated 2026-04-30, is stale —
do not rely on it.)

If the home IP has changed (Xfinity rotated it) AND you can't log in:
update the NSG rule first — see [§ SSH unreachable](#ssh-unreachable).

---

## Quick triage

| Symptom | Scenario | Time to recovery |
|---|---|---|
| Phone died / lost / wiped → can't get TOTP code | [A](#scenario-a-lost-phone-still-know-password) | ~3 min |
| Typed password wrong too many times → "incorrect username or password" | Wait 5 min (regulation lockout). Then try again. | 5 min |
| Forgot password but still have phone | [B](#scenario-b-forgot-password-still-have-phone) | ~5 min |
| Forgot password AND lost phone | [C](#scenario-c-lost-both) | ~10 min |
| `auth.jacksumner.com` itself won't load | [D](#scenario-d-authelia-down) | varies |
| Can't even SSH | [E](#scenario-e-ssh-unreachable) | varies |

---

## Scenario A — Lost phone, still know password

You can authenticate with username + password (1FA), but TOTP (2FA) is
gone. The fix is to delete the existing TOTP record so Authelia's next
login flow takes you back through enrollment.

```bash
# 1. SSH in
ssh azureuser@trading.jacksumner.com

# 2. Delete the TOTP entry for jack
sudo sqlite3 /var/lib/authelia/db.sqlite3 \
    "DELETE FROM totp_configurations WHERE username='jack';"

# 3. Confirm it's gone (should return 0 rows)
sudo sqlite3 /var/lib/authelia/db.sqlite3 \
    "SELECT username FROM totp_configurations;"

# 4. Restart Authelia (clears any cached state)
sudo systemctl restart authelia
```

Then in your browser:

1. Go to `https://trading.jacksumner.com` → bounced to login
2. Enter username + password → Authelia says "register your second factor"
3. Pick **One-Time Password** → identity-verification email is generated
4. SSH in again and read the verification code:
   ```bash
   sudo tail -50 /var/lib/authelia/notification.txt
   ```
   Look for the most recent `One-Time Code` block (8 alphanumeric chars).
5. Type the code into the dialog → Verify
6. Scan the new QR into your authenticator app → enter 6-digit code →
   confirm enrollment

You're back in.

---

## Scenario B — Forgot password, still have phone

Authelia has a self-service password reset, but it sends the reset link
via the **filesystem notifier**, not real email (until SMTP is wired up
— see `BACKLOG.md` → "Real SMTP for Authelia notifications").

1. Browser: go to `https://auth.jacksumner.com` → click **Reset password?**
2. Enter username `jack` → submit
3. SSH in:
   ```bash
   ssh azureuser@trading.jacksumner.com
   sudo tail -50 /var/lib/authelia/notification.txt
   ```
   Look for the reset link — it'll look like
   `https://auth.jacksumner.com/reset-password?token=<long-string>`.
4. Open that URL in your browser → enter new password → submit.
5. Log in normally with the new password + your TOTP code.

Update your password manager with the new password immediately.

---

## Scenario C — Lost both

No phone, no password. We have to manually reset the password hash and
clear the TOTP state from the VM. End result: password reset to a value
you choose now, TOTP needs re-enrollment on next login.

```bash
# 1. SSH in
ssh azureuser@trading.jacksumner.com

# 2. Generate a new argon2id hash. Replace NEW_PASSWORD_HERE with your
#    chosen password — pick something strong, this is also gating live
#    trading.
sudo /usr/local/bin/authelia crypto hash generate argon2 \
    --variant argon2id --iterations 3 --memory 65536 \
    --parallelism 4 --key-size 32 --salt-size 16 \
    --password "NEW_PASSWORD_HERE"
# Copy the line that starts with "Digest: $argon2id$..."

# 3. Edit users_database.yml — replace the existing password: line with
#    the new hash. Watch out for the YAML quoting (use double quotes).
sudo vi /etc/authelia/users_database.yml

# 4. Clear the TOTP record so next login forces re-enrollment.
sudo sqlite3 /var/lib/authelia/db.sqlite3 \
    "DELETE FROM totp_configurations WHERE username='jack';"

# 5. Restart Authelia
sudo systemctl restart authelia
```

Then log in (username + new password), enroll TOTP fresh as in Scenario A
steps 3–6.

---

## Scenario D — Authelia down

`auth.jacksumner.com` returns 502 / connection refused / certificate error.

```bash
ssh azureuser@trading.jacksumner.com

# Status check — is Authelia running at all?
sudo systemctl status authelia --no-pager | head -10

# Recent logs — what's it complaining about?
sudo journalctl -u authelia -n 50 --no-pager

# Common fixes:
sudo systemctl restart authelia
# Confirm it's listening on 9091
sudo ss -tlnp | grep 9091
```

If Authelia stays broken: temporarily remove the forward_auth gate from
Caddy so the dashboard is accessible directly while you fix Authelia.
**This drops you to no-auth on a public IP — only do this with the
trading-corp service ALSO stopped, so nothing live is exposed.**

```bash
# 1. Stop trading-corp first to prevent any unauthenticated access to
#    real broker data
sudo systemctl stop trading-corp

# 2. Swap to the pre-Authelia Caddyfile we backed up on 2026-04-30
sudo cp /etc/caddy/Caddyfile.pre-authelia.bak /etc/caddy/Caddyfile
sudo systemctl reload caddy

# 3. Now fix Authelia at your leisure. When fixed, restore:
#    (assuming you saved the Authelia-enabled Caddyfile somewhere — if not,
#    re-create it from the runbooks/auth_caddyfile.md template or this
#    session's transcript).
sudo systemctl start trading-corp
```

If Authelia's binary itself is broken (corrupted, version mismatch),
re-download from
`https://github.com/authelia/authelia/releases/latest` and reinstall to
`/usr/local/bin/authelia` (matches the install path used at setup).

---

## Scenario E — SSH unreachable

This is the worst case. SSH is gated by Azure NSG rules on port 22.
**As of 2026-06-09** access is via a temporary rule
`temp-vpn-trip-until-2026-06-19` allowing the Surfshark static VPN IP
`92.119.177.22/32` (pattern: a stable home-network rule plus temp `/32`
`temp-vpn-trip-until-<date>` rules for trip access; the prior
`98.231.16.63/32` from 2026-04-30 is stale). If your allowed source IP
changes and you can't reach the VM, you must update the NSG rule
**from outside the VM**.

You'll need either Azure Portal or `az` CLI from a machine that's
already authenticated to your subscription:

```bash
# Find the new home IP
curl -s https://ifconfig.me

# Update the NSG rule (replace NEW.IP.HERE with the value above)
az network nsg rule update \
    --resource-group rg-shared-prod \
    --nsg-name tc-prod-nsg \
    --name AllowSSHFromHome \
    --source-address-prefix NEW.IP.HERE/32

# Confirm
az network nsg rule show \
    --resource-group rg-shared-prod \
    --nsg-name tc-prod-nsg \
    --name AllowSSHFromHome \
    --query sourceAddressPrefix -o tsv
```

Wait ~30 seconds for the NSG update to propagate, then retry SSH.

---

## Don't get locked out

Periodic checks (do these monthly):

1. **Confirm SSH still works from home.** Just `ssh azureuser@trading.jacksumner.com 'echo ok'`. If it fails, IP rotated — fix per Scenario E.
2. **Confirm `az` CLI on your laptop is still authenticated.** Run `az account show`. If session expired, run `az login` so you're not scrambling for credentials during a real lockout.
3. **Confirm the password manager entry for `auth.jacksumner.com` is current.** If you ever change the password, update it everywhere immediately.
4. **Smoke-test the lockout recovery once.** Pick a quiet weekend. Walk through Scenario A on purpose (delete your TOTP, re-enroll). Builds the muscle memory.

---

## Quick reference: paths, services, files

| Thing | Where |
|---|---|
| Authelia config | `/etc/authelia/configuration.yml` |
| Authelia user DB | `/etc/authelia/users_database.yml` |
| Authelia secrets | `/etc/authelia/secrets/{jwt,session,storage_encryption}` |
| Authelia state | `/var/lib/authelia/db.sqlite3` |
| Authelia notifications | `/var/lib/authelia/notification.txt` |
| Authelia binary | `/usr/local/bin/authelia` (v4.39.19, installed 2026-04-30) |
| Authelia systemd | `/etc/systemd/system/authelia.service` |
| Caddy config | `/etc/caddy/Caddyfile` |
| Caddy backup (pre-auth) | `/etc/caddy/Caddyfile.pre-authelia.bak` |
| Trading corp service | `/etc/systemd/system/trading-corp.service` |
| Azure resource group | `rg-shared-prod` |
| NSG name | `tc-prod-nsg` |
| Public IP | `20.51.145.253` (static, won't change) |
