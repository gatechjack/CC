# M4 — owner_identity scoping + Authelia wiring (design, 2026-09-01)

First-time wiring of an identity layer that has never been used, on a console for a LIVE armed trading division.
The load-bearing risk is a lockout; the design is built around not creating one.

## ★ OBSERVED (not assumed): the header Authelia forwards
`/etc/caddy/Caddyfile`: `predictions.jacksumner.com` -> `reverse_proxy localhost:8081`, behind
`forward_auth localhost:9091 { copy_headers Remote-User Remote-Groups Remote-Name Remote-Email }`. So **Authelia sets
`Remote-User` (+ Remote-Groups/Name/Email) on success and Caddy forwards them to pm_web.** `authz.identity_from_headers`
already probes `remote-user` FIRST -> the scoping half INHERITS the primitive; nothing is reimplemented. (Runner:
`cc\pm_authelia_header_probe_ro.ps1`.) Two unknowns remain, both DEPLOY-time values, not code: **Jack's and Karen's
Authelia usernames** (the probe could not read the users file) and **`PM_ADMIN_IDENTITIES` is ABSENT on the unit**.

## Fail-closed scoping (built: authz.visible_account_ids + tests)
Inherits the fail-closed default at EVERY fork: an ADMIN sees ALL accounts; a non-admin sees ONLY accounts whose
`owner_identity == their identity`; a **NULL/unowned account is ADMIN-ONLY**; **no identity (header absent) sees
NOTHING**. No branch grants on absence. Proven, including the lockout case below.

## ★ Jack's session / the lockout / the recovery (your #2)
- Today pm_web reads NO identity header and serves everyone Authelia admits. If M4 enforces scoping and
  `PM_ADMIN_IDENTITIES` is unset (as it is now), **even Jack sees NOTHING** -- his account has a NULL owner and he is
  not admin (proven: `test_visible_accounts_request_wrapper`, config-unset branch -> `set()`). That is the lockout.
- **So the deploy is ENV-LEADS (same shape as migration-leads):** set `PM_ADMIN_IDENTITIES=<jack's Authelia
  username>` on the pm_web unit AND confirm `Remote-User` actually carries that username on a real request, BEFORE
  the scoping code enforces. Order: (1) confirm the header value live; (2) set the env; (3) deploy the code; (4)
  verify Jack (admin) sees ALL and Karen sees ONLY hers -- do not restart into enforcement until (1)+(2) hold.
- **Recovery if the header is wrong:** `pm_cli` still works (arm/disarm -- the authoritative kill path NEVER depends
  on pm_web, R7.d). The PAGES would be dead. So enforcement stays OFF until `Remote-User` is confirmed; rollback =
  unset `PM_ADMIN_IDENTITIES` + revert the scoping code + restart -> back to serve-everyone. No trading is affected
  either way (the driver + arm state are engine/CLI, not pm_web).

## owner_identity population (your #3)
- NULL on both rows today. **NULL = admin-only** (your instinct, adopted): an unowned account is visible only to
  admins, never to everyone.
- **kalshi_jack: stays NULL** -- Jack is admin (via PM_ADMIN_IDENTITIES), so he sees it; no write needed.
- **kalshi_karen: `owner_identity = <Karen's Authelia username>`** -- a live PM-DB UPDATE (HALT-gated, backed up +
  verified like the account-create), so Karen's non-admin identity resolves to her account. One row, one column.
- So population is: nothing for Jack (admin covers it) + one authorized DB write for Karen.

## Karen's access (per the rulings, wired via is_admin + the scoping)
- Her ACCOUNT PAGE: visible (owner scoping). Farm League: **read-only**. **CAN run Analyze** (no gate -- it is the
  promotion judge; spend bounded by the $20/day cap). **CANNOT promote / attach / demote** (admin-only gate -- to
  add on those POST routes; today they are ungated). **SEES the global arm STATE** (read-only badge, already), **no
  control** (no arm control exists on any page). The account overview shows only accounts she may see.

## Build state + what remains
- ★ BUILT: `authz.visible_account_ids` / `visible_accounts` (fail-closed) + 6 tests (incl the lockout demonstration).
- REMAINS (code): app.py filters `_load_accounts_overview` + 403s `_load_account` for a non-visible account; the
  admin gate on the promote/attach/demote POST routes; owner_identity carried on the account rows the scoping reads.
- REMAINS (deploy, ENV-LEADS + HALT): confirm the live `Remote-User` value; set `PM_ADMIN_IDENTITIES`; the karen
  owner_identity DB write. NEEDS from Jack: his + Karen's Authelia usernames.
- This is part of the pm_web BATCH (M3-display + M4 + M5) -> one restart.
