# Authelia trading-rule finding — TRADING CORP INFRA change (Jack's, not P2)

**Discovered read-only 2026-08-23 during P2 planning. This is NOT a Prediction Markets build task.** It is a
Trading Corp infrastructure change — same category as the VM geo-migration — **Jack's hands, Jack's timing.**
The P2 build agent must NOT attempt it and must NOT treat it as a build gate (see "Does this block P2?" below).

## The finding
Authelia (`/etc/authelia/configuration.yml`) `access_control`:
```
default_policy: deny
rules:
  - domain: trading.jacksumner.com
    policy: two_factor          # <-- NO subject: any authenticated 2FA user is authorized
```
Users DB = file backend `/etc/authelia/users_database.yml`, **single user `jack` [groups: admins]**. Session
cookie scoped to apex `jacksumner.com` (SSO across sub-domains). Proxy = Caddy; Authelia via
`forward_auth localhost:9091 { uri /api/authz/forward-auth }`.

**Authentication IS authorization here.** The trading rule has no `subject`, so *anyone who can log in* reaches
the **live trading dashboard**. It is safe today only because `jack` is the sole account. **Creating Karen's
login — the prerequisite for her to see `predictions.jacksumner.com` — would hand her the live trading stack**
unless the trading rule is tightened FIRST.

## Does this block the P2 build? NO.
Authelia's existing `two_factor` rule already covers a new `predictions.jacksumner.com` vhost: Jack reaches it,
nobody unauthenticated does. **The P2 site can be built, deployed, and used by Jack long before Karen's login
exists.** What this finding blocks is the **Karen viewer feature**, not the build. The build agent builds `pm_web`
only and does not touch Caddy/Authelia.

## The change, IN ORDER (Jack, before Karen's login) — ordering matters
1. **Add a `subject` to the `trading.jacksumner.com` rule** restricting it to Jack — e.g. `subject: 'group:admins'`
   (or `'user:jack'`).
2. **VERIFY JACK STILL REACHES TRADING.** This is the step people skip. A wrong `subject` string on a
   `default_policy: deny` config **locks Jack out of his own live trading dashboard.** Confirm access before
   proceeding.
3. **Only then create Karen's login** in `users_database.yml` (file backend has `watch: false` → **restart
   Authelia** after editing), in a `pm_viewers` group.
4. **Only then add the `predictions.jacksumner.com` access_control rule** — `policy: two_factor`,
   `subject: 'group:pm_viewers'` (`default_policy: deny` means no rule = nobody, including Jack).
5. **Then the DNS A-record** (`predictions.jacksumner.com → 172.171.189.116`) **+ the Caddy site block**
   (mirror the trading `forward_auth` block → `reverse_proxy localhost:8081`); Caddy auto-provisions the cert.

## Safety
- **Take a rollback copy of the Authelia config before touching it** (`configuration.yml` + `users_database.yml`).
- **Do it from a machine with a separate way back in** (SSH to the box), so a misfiring rule that locks the web
  login out is still recoverable.
- Restart Authelia after user-DB edits (`watch: false`).

Cross-ref: `P2_PLAN.md` §11/§12/§16, `TRANSITION_TO_P2_BUILD_AGENT.md` §5.
