# MULTI-USER LOGINS (KAREN + MARC) + PASSKEYS + /live SCOPING + THIRD ACCOUNT -- READ-ONLY INVESTIGATION 2026-09-18

Read-only. Base = origin/prod-live `777e87a5` (git truth); box == prod-live verified CR-stripped on the 5 auth
files (authz.py 3799a80c, app.py 5303952c, shard_snapshot_task.py 956f13c3, utils/secrets.py bc3f8166, main.py
b3e85e1e). Nothing built/changed/deployed. 35 sub-divisions armed + trading throughout.

## HEADLINE
- **Task 2 (/live scoping) is DONE, not open.** The standing "both /live routes UNSCOPED, close before Karen"
  ruling is STALE. The deployed code scopes every read route AND returns 403 on the per-account page.
- **Task 1 (Authelia) = a few specific root config edits (now CONFIRMED by the root read):** WebAuthn is currently
  DISABLED (2FA is TOTP-only), the access_control gates both consoles to `user:jack` only (default-deny), only user
  `jack` exists, and the notifier is FILESYSTEM (enrolment links land in a box file, not email). So Jack must: enable
  webauthn, add access_control rules for karen+marc, create the karen+marc users, restart authelia -- and relay each
  enrolment link off the box (or switch to SMTP). The config is authelia-owned/root-only; Jack ran the read himself.
- **Task 3 (Marc) is NOT pure data.** The multi-account driver is generic, BUT the secret_ref->keypair map is a
  hardcoded whitelist and the secrets loader is a fixed-field dataclass -> Marc needs a small CODE change + vault
  secret + engine restart, THEN the DB rows.

═══════════════════════════════════════════════════════════════════════════════════════════════
## TASK 1 -- AUTHELIA (what is CONFIRMED vs what Jack must read as root)
═══════════════════════════════════════════════════════════════════════════════════════════════
CONFIRMED (from readable sources: systemd unit, world-readable Caddyfile, the binary version):
- **Authelia v4.39.19**, native systemd service (User=`authelia`, `/usr/local/bin/authelia --config
  /etc/authelia/configuration.yml`). Caddy is the reverse proxy; Authelia login UI at `auth.jacksumner.com` (:9091).
- **Factor support in 4.39: TOTP, WebAuthn/FIDO2 (incl. passkeys / passwordless), Duo. NO SMS.** (Jack's
  recollection CONFIRMED against the installed version -- no SMS second factor exists in Authelia.)
- **Routing / ACL front door:** Caddy `forward_auth localhost:9091` gates BOTH consoles:
  - `trading.jacksumner.com` -> trading_corp :8000 (bypass list: TradingView webhooks + health).
  - `predictions.jacksumner.com` -> pm_web :8081. ONLY `/healthz` is public (`@public {path /healthz}`);
    EVERYTHING else is behind forward_auth. On success Caddy `copy_headers Remote-User Remote-Groups Remote-Name
    Remote-Email` to pm_web (copied from Authelia's RESPONSE -> a client cannot spoof them; this is the precondition
    authz.py depends on).

CONFIG INTERNALS -- CONFIRMED (Jack ran the root read 2026-09-19T00:46Z, cc/pm_authelia_root_read.*; structure only):
1. **WebAuthn is DISABLED today.** `webauthn: {disable: true}` (in-config comment: "WebAuthn (security keys /
   passkeys) -- disabled tonight, can enable later"). `totp: {disable: false}`, `default_2fa_method: totp`, no Duo.
   -> Today 2FA is TOTP-ONLY. **PASSKEYS REQUIRE A CONFIG CHANGE: set `webauthn.disable: false` (+ a webauthn block:
   display_name / relying-party) and `systemctl restart authelia`.** This is a prerequisite for the whole passkey plan.
2. **Notifier is FILESYSTEM, not SMTP.** `notifier: {filesystem: ...}` -> links (password-reset, and the
   credential-registration verification link) are WRITTEN TO A FILE on the box (`/var/lib/authelia/notification.txt`,
   currently 0 bytes), NOT emailed. **So Karen/Marc CANNOT self-serve remotely: Jack must read each link off the box
   and relay it.** If true remote self-service is wanted, switch the notifier to SMTP first.
3. **★ access_control = the confirmed BLOCKER.** `default_policy: deny`; the ONLY rules are
   `trading.jacksumner.com` and `predictions.jacksumner.com` -> `policy: two_factor, subject: 'user:jack'`.
   **Karen + Marc are DENIED at Authelia today** (before pm_web ever runs). Jack MUST add access_control rules for
   them (e.g. per-user rules for predictions.jacksumner.com, or a group) + restart authelia.
4. **users_database.yml has ONLY `jack`** (displayname "Jack", email jack@jacksumner.com, disabled:false). No
   `karen`, no `marc` -- BOTH must be created (username + displayname + email + argon2 password hash). NB the
   pm_account.owner_identity='karen' was set in anticipation, but the Authelia user `karen` does not exist yet.
5. authentication_backend = FILE (users_database.yml), watch:false, password_reset enabled (disable:false).
   identity_validation present (the credential-registration link flow). session cookie scoped to the `jacksumner.com`
   APEX (one login spans trading + predictions + auth), authelia_url https://auth.jacksumner.com. storage = SQLite
   (`/var/lib/authelia/db.sqlite3`, holds registered 2FA devices + sessions).

★ ENROLMENT -- HOW A NEW USER WITH NO FACTOR GETS THEIR FIRST PASSKEY (Authelia file-backend model; confirm against
the config above): the user's FIRST factor is the PASSWORD, which the ADMIN provisions in users_database.yml
(username + displayname + email + an argon2 hash Jack generates, or a reset-password email if the notifier +
password reset are enabled). There is NO "no factor at all" chicken-and-egg -- the admin-set password bootstraps
everything. To register the passkey (a SECOND factor, or first-factor if passwordless): the user logs in with the
password, initiates WebAuthn registration, and (Authelia 4.38+ `identity_validation` for credential management)
Authelia sends a ONE-TIME VERIFICATION LINK by the NOTIFIER; the user clicks it, then registers the passkey ON
THEIR DEVICE. So: self-service on first login, gated by an emailed one-time link -> needs (a) the notifier working,
(b) the user's email in users_database.yml, (c) the user + their device present. It does NOT need an existing
second factor.

★ DEVICE CONSTRAINT (set expectations): a passkey is DEVICE-BOUND unless the platform syncs it. Apple (iCloud
Keychain) and Google (Password Manager) sync passkeys across a user's OWN devices, so in practice it follows them
between their phone/tablet/Mac/Chrome. BUT a laptop outside that ecosystem (a Windows laptop not tied to the same
sync, or a browser without a synced passkey provider) needs its OWN passkey registration. So Karen/Marc should
enrol on the device(s) they will actually use, or use a syncing ecosystem.

★ WHO READS THIS + HOW (corrected 2026-09-18): the Authelia config + users_database hold SECRETS (session/JWT/
HMAC/storage-encryption keys, SMTP password, argon2 password hashes). They are root-only (authelia:authelia, 0700)
and are NOT the agent's to dump -- reading them via an az-root escalation was rejected as an unauthorized Production
Read (and is not what "read command-paste-rule" asked for; that was about formatting, not escalation). So JACK reads
them himself in his own root session on the box (he owns Authelia). This is a CHECKLIST of what to look at and what
each answer means -- NOT a command blob to paste (per command-paste-rule, raw multi-line box commands are never
handed for pasting). If Jack instead wants the agent to run a REDACTED, structure-only read via the sanctioned
az-root channel, that is a separate escalation he can explicitly authorize; it is not assumed here.

  In /etc/authelia/configuration.yml, confirm:
   - webauthn: block present + enabled?  (and enable_passkey_login / default_2fa_method if passwordless is wanted)
   - notifier: is it smtp (user gets enrolment links directly = remote) or filesystem (link written to a box file
     -> Jack fetches + relays it)?  -- read the TYPE, not the SMTP password.
   - access_control: do the rules let karen + marc reach predictions.jacksumner.com, or is it user:jack default-deny?
     (THE most likely blocker -- see the risk note.)
   - identity_validation / credential registration: is the emailed one-time-link step for registering a credential on?
  In /etc/authelia/users_database.yml, confirm:
   - does a user `karen` exist (with a displayname + email)?  `marc` must be added.  -- read usernames/emails, NOT
     the password: hash lines.

═══════════════════════════════════════════════════════════════════════════════════════════════
## TASK 2 -- /live SCOPING (the standing "unscoped" ruling is STALE -- it is DONE)
═══════════════════════════════════════════════════════════════════════════════════════════════
DEPLOYED (prod-live 777e87a5, box-matched):
- **`web/authz.py`** (M2/M4/M5, fail-closed): identity from Authelia `Remote-User`/`X-Forwarded-User`/`X-Remote-User`
  (first present); admin from env `PM_ADMIN_IDENTITIES`. `visible_account_ids(identity, is_admin, accounts)`: ADMIN
  sees ALL; non-admin sees ONLY accounts whose `owner_identity == identity`; NULL owner = admin-only; no identity =
  nothing. `can_act_on_account` = owner-or-admin write gate. Fail-closed at every fork (unset env -> nobody admin).
- **Every read route is scoped**: accounts overview `/`, account page, `/live` list, and `/live/{account}/{category}`
  all filter via `visible_account_ids`. The per-account page (`_load_live_subdivision`) returns **`_FORBIDDEN` (403)**
  when the account exists but is not the caller's (404 if it does not exist) -- the code comment: "scoping the tile
  page while leaving this route open would be security theatre." **Both routes the ruling flagged are CLOSED.**
- **Writes** gated: Detach + sizing-LOWER = owner-or-admin; sizing-RAISE = admin-only.
CONFIG/DATA MAKING IT EFFECTIVE (read from the box):
- pm_web service env: **`PM_ADMIN_IDENTITIES=jack`** (SET). So `jack` = admin (sees all, may promote/attach/arm-UI).
- pm_account.owner_identity: **kalshi_karen -> 'karen'**; **kalshi_jack -> NULL** (NULL = admin-only). So:
  - Karen (Authelia identity `karen`, non-admin) sees ONLY kalshi_karen; gets 403 on Jack's /live pages.
  - Jack (admin) sees all accounts.
- NET: **/live scoping is present AND effective RIGHT NOW. No pm_web work is needed before Karen's session.** The
  only precondition is that Karen's Authelia identity is exactly `karen` (matches owner_identity) -- confirm in
  users_database.yml. If her Authelia username differs, set pm_account.owner_identity to match it (a 1-row DB edit).
- Defense-in-depth: Authelia (are you authenticated?) at Caddy + pm_web authz (which accounts may you see?).

═══════════════════════════════════════════════════════════════════════════════════════════════
## TASK 3 -- MARC, THIRD ACCOUNT (confirm/refute + what it actually takes)
═══════════════════════════════════════════════════════════════════════════════════════════════
CONFIRMED (Jack's understanding is right about the machinery): per-account credential resolution + multi-account
driver iteration EXIST. main.py reads `active_driver_subdivisions` (all active accounts), resolves EACH account's
`secret_ref -> keypair`, builds a per-account broker, spawns ONE task per account (`plan_driver_tasks`), fail-closed
(an unmapped secret_ref -> account SKIPPED, never traded on the shared keypair). The old single-hardcoded-jack task
is gone. The shard-snapshot task is likewise per-account-generic. Caps are per-account (per pm_subdivision row);
boot-reconcile runs inside each per-account task -- both generic, NO two-account assumption.

REFUTED (Marc is NOT just a vault secret + a row): TWO hardcoded two-account assumptions surface on a third account:
1. **`shard_snapshot_task._SECRET_REF_KEYPAIR`** is a hardcoded fail-CLOSED WHITELIST = {`kalshi_karen`, `KALSHI`,
   `kalshi_jack`}. An unmapped ref -> (None,None) -> Marc's account SKIPPED. So a new entry
   `"kalshi_marc": ("kalshi_marc_api_key_id", "kalshi_marc_private_key_pem")` is REQUIRED (Jack ruled this whitelist
   "the single most important line" -- an unknown ref must never default to jack's keys; do NOT weaken that, just add).
2. **`utils/secrets.py`** is a FIXED-FIELD dataclass: `kalshi_karen_api_key_id/_pem` are declared fields loaded from
   env `KALSHI_KAREN_*`. Marc needs the same: declare `kalshi_marc_api_key_id/_pem` (~line 186), load from
   `KALSHI_MARC_API_KEY_ID`/`KALSHI_MARC_PRIVATE_KEY_PEM` (~line 407), register-redact the pem (~438).
So MARC = (a) his Kalshi token -> vault secrets `KALSHI_MARC_API_KEY_ID` + `KALSHI_MARC_PRIVATE_KEY_PEM`
(KEY_VAULT_URI is configured); (b) ~6 lines of CODE across those 2 files; (c) an ENGINE RESTART (both files read at
boot -> bounces every division ~3.5 min, time it clear of market opens); THEN (d) a `pm_account` row + his
sub-divisions + attachments + arm (DB writes, per-cycle read, NO restart).

ACCOUNT_ID RECOMMENDATION: **`kalshi_marc`** (venue_firstname, lowercase -- matches `kalshi_jack`/`kalshi_karen`).
It is the pm_account PK, becomes the sub-division PK `(account_id, category)`, and appears in every
`/live/{account}/{category}` URL -- effectively PERMANENT, so match the existing convention exactly. Companions:
`secret_ref='kalshi_marc'` (matches karen's; jack's is the legacy `'KALSHI'`), `owner_identity='marc'` (== his
Authelia username, so pm_web scopes him to only his account), vault names `KALSHI_MARC_*` (matches `KALSHI_KAREN_*`).

═══════════════════════════════════════════════════════════════════════════════════════════════
## ORDER + WHO MUST BE PRESENT
═══════════════════════════════════════════════════════════════════════════════════════════════
PARTS JACK CAN DO ALONE (no Karen/Marc needed) -- ALL config edits are root, on the box, + `systemctl restart authelia`:
- **Enable WebAuthn** in configuration.yml (`webauthn.disable: false` + a webauthn block; optionally
  `default_2fa_method`/passkey-login). PREREQUISITE -- passkeys do not work until this + restart. (Currently TOTP-only.)
- **Add access_control rules** for karen + marc to predictions.jacksumner.com (today it is `user:jack` only,
  default-deny) -- THE blocker; without it they cannot reach the site at all.
- **Create the Authelia users** karen + marc in users_database.yml (username + displayname + email + argon2 password
  hash, e.g. `authelia crypto hash generate argon2`). Only `jack` exists today.
- **Decide the notifier:** filesystem today -> Jack must read each enrolment/reset link from
  `/var/lib/authelia/notification.txt` on the box and relay it. Optionally switch to SMTP for true remote self-serve.
- Confirm/adjust pm_account.owner_identity to match each Authelia username (karen already ='karen'; add ='marc' on
  Marc's row).
- Marc's TRADING enablement end to end: vault secrets, the ~6-line code change (secrets.py + shard_snapshot_task.py),
  deploy + ENGINE RESTART, pm_account row + sub-divisions + arm. (Needs Marc's TOKEN once; not Marc present.)
PARTS THAT NEED THE PERSON + THEIR DEVICE (and Jack relaying the link, since the notifier is filesystem):
- **Karen's first passkey enrolment** -- needs KAREN + her device (self-service after password login + the emailed
  one-time link). Everything else for Karen is already done (she trades + is scoped).
- **Marc's first passkey enrolment** -- needs MARC + his device.
- **Marc's Kalshi API token** -- needs MARC to supply it (once) before the vault secret can be created.

RECOMMENDED ORDER:
0. Jack (ALONE, root, one authelia restart): ENABLE webauthn + ADD access_control rules for karen+marc + CREATE the
   users karen+marc (password hashes) in users_database.yml -> `systemctl restart authelia`. (Config read is DONE.)
   Optionally switch notifier filesystem->smtp here if you want the enrolment links emailed rather than relayed.
1. Jack: task-2 (pm_web scoping) is already done; karen's owner_identity already ='karen'. (ALONE, nothing to do.)
2. KAREN login: with #0 done, Karen logs in (password) + enrols her passkey; if the credential step needs a link,
   Jack reads it from /var/lib/authelia/notification.txt and relays it. (needs KAREN + device [+ Jack relaying]).
   Karen is fully live after this -- no trading change.
3. MARC, in two independent tracks:
   3a. LOGIN: Jack creates Authelia user `marc` + access_control rule (done in #0 if batched) -> Marc enrols his
       passkey, Jack relaying the notification.txt link if needed (needs MARC + device [+ Jack relaying]).
   3b. TRADING: Marc supplies his Kalshi token (needs MARC once) -> Jack: vault + ~6-line code change + deploy +
       ENGINE RESTART (bounces all divisions; time clear of opens) -> pm_account `kalshi_marc`
       (owner_identity='marc') + sub-divisions + attach + arm. Marc sees his account on login once 3b's pm_account
       row exists.

## RUNNERS (all read-only, this session)
cc/pm_multiuser_recon_ro.* (box==prod-live, PM_ADMIN_IDENTITIES, Authelia discovery, pm_account rows),
cc/pm_authelia_config_ro.* (unit + Caddyfile + config-read attempt -> config internals root-only),
cc/pm_caddy_pred_ro.* (verbatim predictions block). Authelia config/users NOT readable as azureuser -> the root
commands above are for Jack.
