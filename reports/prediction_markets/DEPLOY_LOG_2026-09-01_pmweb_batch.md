# pm_web batch DEPLOYED LIVE — 2026-09-01 (M3-display + M4; M5 held out)

ENV-LEADS, five steps not collapsed. File-by-file box-is-truth graft (prod-live NOT advanced by ledger). pm_web only —
the engine / order path was never touched.

## The five steps (each HALT-authorized; env leads)
1. **OBSERVE `Remote-User` on a live wire request** — captured jack's real browser requests on loopback:8081 (16× `GET /`
   + `/live/...` + css, real browser headers). Identity headers, 18× each: **`Remote-User: jack`** (lowercase),
   `Remote-Name: Jack` (displayname), `Remote-Email: jack@jacksumner.com`, `Remote-Groups: admins`. The distinction
   is exactly why this was owed: `authz.py` reads `Remote-User` (=`jack`), not the capitalized `Remote-Name` or the
   email — so `PM_ADMIN_IDENTITIES=jack` matches the byte that actually arrives. (4 attempts; the first 3 missed the
   window / browser cache — a self-test curl proved the capture worked, and a `?t=<n>` cache-buster made jack's
   request land.)
2. **SET `PM_ADMIN_IDENTITIES=jack`** on `prediction-markets-web.service` via a systemd drop-in (az-root) + daemon-reload;
   confirmed the **resolved** state via `systemctl show -p Environment` (KEY_VAULT_URI preserved — already on the unit).
3. **DEPLOY the code** — manifest reconciled at `e7af7d8` (clean M3-display+M4 cut; the only later pm_web change is the
   M5 link `61b2e8f`, held out). Box matched my documented M2 base-guard exactly (no divergence). 6 files:
   `authz.py` (new) + `app.py`, `subdivision.py`, `pm_accounts.html`, `pm_account.html`, `pm.css` — each sha-verified,
   **forced 644 + perms-asserted**. **Gate-A** = py_compile + `import trading_corp.prediction_markets.web.app` (full
   transitive closure — `authz` is the only new dep; loss_grounding/shard_snapshot/market_describe already present).
4. **Karen `owner_identity='karen'`** — DB-backed (consistent backup), guarded `WHERE owner_identity IS NULL`, **1 row**;
   `kalshi_jack` stays NULL (admin-only).
5. **pm_web RESTART** (az-root) — PID 137407 → **143911**, active, `/healthz` 200, `PM_ADMIN_IDENTITIES=jack` in the
   RUNNING process env. **Engine PID 139938 + arm state UNCHANGED** (order path untouched).

## Post-check (all green)
- **Scoping:** jack (admin) sees BOTH accounts + opens both (200/200); karen sees ONLY hers; no-identity sees nothing;
  **karen → `/account/kalshi_jack` → 403** (visible-but-not-yours); `/account/nope` → 404; no 500s.
- **Write gates (proven by POSTing as karen, server-side):** promote / demote / attach / **refresh** → **403**;
  no-identity POST → 403; **`farm_analyze` → 200** (karen admitted — the ungated promotion judge).
- **Shard section populated (first render outside a runner):** Karen — Shard 0 $25.01, Shard 3 $437.83, Total $462.83,
  "as of 1 min ago · fresh", + the shard-0-direction line "Proceeds returning to shard 3 — shard-0 flat, self-sustains".
  Jack — Shard 0 $0.01, Shard 3 $446.00, Total $446.01, fresh (his snapshot retry completed). Both age-banded fresh
  (5-min writer keeping them current).
- **M5 held out:** the overview shows the pre-M5 "arm/disarm is a CLI action" copy — no engine-console link.

## ★★ M4 CAVEAT — precise scope of "verified"
- **JACK: verified END-TO-END on live.** His real Authelia login was OBSERVED to carry `Remote-User: jack`, and the
  live pm_web scopes him to admin (both accounts, gates admit him). His half is proven by live observation.
- **KAREN: proven on the LIVE DEPLOYED app via a forged `Remote-User: karen` header** (the same mechanism as
  `test_m4_gates`, now exercised on the box — she sees only her account, 403 on jack's, 403 on the gates). This is
  STRONGER than the unit test (it is the deployed app) but is NOT her real end-to-end path: **Karen does not exist in
  Authelia yet**, so her Authelia-login → `Remote-User: karen` → scoped-view chain is UNVERIFIED until she is added
  (tonight; steps in `M4_AUTHELIA_SCOPING` / the Karen-Authelia writeup). **"M4 verified" = jack's real path +
  karen's app-level scoping; it does NOT yet mean karen's real login.**

## Backups + recovery (box)
- Code: `web/app.py`, `subdivision.py`, `web/templates/pm_accounts.html`, `web/templates/pm_account.html`,
  `web/static/pm.css` → `*.bak_pmweb_20260901T180057Z`. DB: `data/prediction_markets.db.bak_owner_20260901T180313Z`.
  Env: `/etc/systemd/system/prediction-markets-web.service.d/pm_admin_identities.conf`.
- Recovery (if ever needed): `rm` the drop-in + daemon-reload (unset env) + restore the `.bak_pmweb_*` files + remove
  `web/authz.py` + `systemctl restart prediction-markets-web` → back to serve-everyone.

## Not in this pass (need the Portal glance + their own window)
Engine M5 (`/pm/arm`) and the PM-side link. The engine bundle (opposed-memory + M3-writer + mig-016) and this pm_web
batch are the two deploys that were ready; both are now live.
