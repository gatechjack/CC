# M5 (Option A, engine-web /pm/arm) -- pre-build findings (2026-09-01, READ-ONLY box probe)

Probe: `cc\pm_engineweb_authz_probe_ro.ps1` (read-only, nothing changed). Answers Jack's two questions + surfaces a
security precondition that must be settled before M5 puts the money-gate control on the engine web.

## Q1 -- does the engine web read `Remote-User` today? NO (code side) -- but the Caddy TRANSPORT already forwards it
- **Source (definitive):** `trading_corp/web/` reads `request.headers` for `content-type` ONLY -- no identity header
  anywhere. First-time plumbing on the CODE side, exactly as Jack suspected.
- **★ But the infrastructure is already there.** `/etc/caddy/Caddyfile`: `trading.jacksumner.com` -> two handles:
  a `@public` bypass (offline/static) and an authenticated `handle { forward_auth localhost:9091 { ...
  copy_headers Remote-User Remote-Groups Remote-Name Remote-Email } reverse_proxy localhost:8000 }`. So **Authelia
  fronts the engine web (:8000) AND Caddy already copy_headers `Remote-User` to it.** The Caddyfile comment says it
  outright: "pass them through to trading_corp so the dashboard could (later) read 'who am I'. **Currently unused on
  the trading_corp side but harmless.**"
- **Consequence for M5:** it needs (a) CODE to read the header + the admin gate -- REUSE `prediction_markets.web.authz`
  (the same pure headers+env module M4 uses; the engine imports everything, so nothing is reimplemented), and (b)
  `PM_ADMIN_IDENTITIES` on the engine unit (env-leads). **NO Caddy change** -- the header already arrives at :8000.
- **Env-leads, but LESS dangerous than M4's.** M4 unset could lock Jack out of the whole PM console. M5 unset just
  makes the NEW arm/disarm control inert (fail-closed -> not-admin -> the button does nothing) -- the CLI arms/disarms
  regardless (R7.d). So the four-step sequence still applies (set `PM_ADMIN_IDENTITIES` on `trading-corp.service` +
  confirm `Remote-User` arrives at :8000 on a live request BEFORE the route enforces), but the failure mode is inert
  UI, not lockout.

## ★ SECURITY PRECONDITION -- :8000 is bound to 0.0.0.0 (ALL interfaces), not localhost
- `ss -ltn`: `LISTEN 0.0.0.0:8000`. The engine web is reachable on every interface, not just via Caddy (localhost).
- **Why it is load-bearing for M5:** the admin gate (and Authelia itself) is only sound if the ONLY path to :8000 is
  through Caddy -- Caddy authenticates via Authelia AND is the thing that sets/strips `Remote-User`. `authz.py` states
  this precondition by name ("NOT a substitute for the proxy stripping client-supplied copies -- that is the proxy's
  job and the precondition"). If a client can reach `VM:8000` directly (bypassing Caddy), they can (1) skip Authelia
  entirely and (2) SPOOF `Remote-User: jack` -> resolve as admin -> **arm/disarm live trading.**
- **Today the stakes are low** (the money gate is CLI-only; :8000 exposes dashboards). **M5 raises them** -- it makes
  arm/disarm reachable on :8000. So BEFORE M5 ships, confirm the Azure NSG blocks external :8000 (typical: only
  80/443/22 open, so the 0.0.0.0 bind is harmless) OR bind :8000 to localhost (a main.py change -> engine restart).
- **Parallel check owed for the pm_web batch:** `authz.py` ASSERTS "pm_web is loopback-only", but that is an assertion,
  not an observation (grep-is-not-a-state-check). Verify :8081's ACTUAL bind + NSG before the M4 deploy too.

## ★ PORT-EXPOSURE RESOLUTION (2026-09-01, `cc\pm_port_exposure_probe_ro` + local reachability probe) -- EFFECTIVELY CLOSED
- **:8081 (pm_web) = `127.0.0.1:8081` -- loopback-only, VERIFIED BY OBSERVATION.** The M4 `authz.py` "pm_web is
  loopback-only" assertion is now confirmed, not assumed -> the M4 pm_web trust model holds; that owed check is CLEARED.
  (:9091 Authelia is also loopback-only; :80/:443 Caddy are public, correct.)
- **:8000 (engine web) = `0.0.0.0:8000`** but **the public Internet CANNOT reach it.** In-box `az` is NOT installed,
  so the NSG rule text was not readable from the box; instead an EFFECTIVE-RESULT probe from a public vantage:
  `443 OPEN, 22 OPEN` (sanity -- host reachable, probe valid), **`8000 FILTERED/timeout`** (SYN dropped, no RST --
  the signature of an NSG/edge DROP on a port that IS listening), `8081/9091` also filtered. So inbound :8000 is
  dropped at the Azure edge; the `0.0.0.0` bind is HARMLESS in practice. No established non-local peer was on :8000
  either (a localhost bind would break nothing, but is unnecessary).
- **Caveat (honest):** the probe is a SINGLE public vantage testing the effective result, not the NSG rule TEXT. It
  definitively rules out the `0.0.0.0/0 -> :8000` case (the spoofing risk), but a narrow SOURCE-SCOPED allow rule
  would not show from one vantage. **Recommend Jack eyeball the Portal NSG inbound rules for :8000 once** (the
  authoritative "read the rules" -- I could not, no in-box az) -- a 30-second confirm, not a blocker.
- **Verdict:** both ports effectively closed -> per the ruling, **M5 proceeds.** The exposure only bites at M5 DEPLOY
  time (when arm/disarm goes on :8000); the build is safe now and the deploy is HALT-gated, so a Portal surprise is
  still catchable before M5 ships.

## Q2 -- does M5 fit the engine bundle (opposed-memory + M3-writer + mig-016 + M5)? RECOMMEND: NO, keep the bundle as-is
- The three bundle items are BUILT + READY (waiting only on the PEAD restart window). M5 is UNBUILT.
- M5 drags two deploy preconditions the other three do NOT: (a) the :8000 exposure must be confirmed closed, (b) the
  env-leads sequence on `trading-corp.service`. Bundling holds three ready things behind the newest, unbuilt one plus
  a security gate -- exactly "the bundle should not wait on the newest thing."
- **Recommendation:** ship opposed-memory + M3-writer + mig-016 on the next PEAD window AS-IS; M5 takes a SUBSEQUENT
  window once it is built + reviewed AND the :8000 posture is confirmed.

## Engine facts (for the build)
- Engine unit = **`trading-corp.service`, MainPID=132470** (matches the memory engine PID -> the live engine). Its
  Environment carries `KEY_VAULT_URI` but **NOT `PM_ADMIN_IDENTITIES`** (env-leads target).
- The engine web runs IN-PROCESS with the engine (xvfb-run python main), so a `/pm/arm` route has NATIVE access to
  `arm.arm/arm.disarm` (the legacy writer) -- the write path is native there, no bridge, no isolation break.
- The worktree shares the repo and CONTAINS `trading_corp/web/` -> M5 builds on THIS branch
  (`pm-multiaccount-2026-09-01`). `app.py`/`routes.py` are SHARED multi-division files -> M5 must be MAXIMALLY
  ADDITIVE (a new `pm_arm_view.py` + minimal wiring) so the box-is-truth file-by-file reconcile is a clean graft that
  cannot revert MACE/PMCC/PEAD live work.

## Proposed M5 shape (for Jack's nod before I build)
- The arm/disarm control RENDERS and SUBMITS on the engine web (`trading.jacksumner.com/pm/arm`), **same-origin** (no
  CORS). The PM UI (`predictions.jacksumner.com`) just LINKS to it ("Manage arm state ->"). Sidesteps cross-domain POST.
- Admin-only via `authz` (fail-closed). GLOBAL arm/disarm (the master); per-sub optional. **The UI MUST NOT clear a
  latch** -- only `arm(require_latch_clear=True)` clears one, and that stays CLI-only (a human must SEE the trigger).
  The CLI stays the authoritative kill path (R7.d) in every case.

## Decisions needed -- ALL RULED by Jack 2026-09-01
1. The :8000 exposure -> CHECK FIRST (done): effectively closed (see PORT-EXPOSURE RESOLUTION). Portal rule-confirm recommended.
2. Bundle sequencing -> DON'T bundle (confirmed). Ship opposed-memory + M3-writer + mig-016 next window; M5 a later one.
3. Shape -> engine-web same-origin (confirmed). Two riders: UI never clears a latch; CLI authoritative. Link honest.

## ★ BUILD COMPLETE (2026-09-01) -- committed, pushed, tested; NOT deployed (HALT-gated)
- **Engine control** (`4620e5b`): `trading_corp/web/pm_arm_view.py` (all logic) + a 2-line additive graft in
  `routes.py` + `templates/pm_arm.html` (extends base.html). Admin-only fail-closed via `authz`; GLOBAL master
  arm/disarm; runs in-process so the write is same-process-consistent with the driver's read. Riders enforced: the
  UI calls `arm.arm()` WITHOUT `require_latch_clear` (LatchedError -> refuse, never clear); the CLI-authoritative
  kill note is always shown. 12 tests (DENY proven by POSTing as a non-admin; the global invocation; latch-refusal;
  rendered states).
- **PM-side honest link** (`61b2e8f`): `pm_accounts.html` shows an ADMIN-ONLY link that plainly LEAVES the PM console
  for the engine console (external-arrow + wording, not a disguised control); `app.py` passes `is_admin`; `.pm-xlink`
  in pm.css. Test: admin sees the link, a non-admin never does.
- **Tests:** 84/84 green locally across M5 + M4 + the updated pre-M4 pm_web suites (a `.venv-webtest` layered over
  the walletops packages -- fastapi/jinja2/httpx/pytest/python-multipart/python-dotenv; walletops itself untouched).
  All run at Gate-A on the box too.

## M5 DEPLOY PLAN (two surfaces, both HALT-gated; NOT bundled with the ready three)
1. **Engine control -> an engine/PEAD window (engine restart).** Preconditions, in order (env-leads): (a) confirm the
   Portal NSG blocks external :8000; (b) SET `PM_ADMIN_IDENTITIES=<jack>` on **`trading-corp.service`** AND confirm
   `Remote-User` arrives at :8000 on a live request; (c) reconcile `routes.py` FILE-BY-FILE against the box (additive
   graft only -- never a wholesale advance) + `pm_arm_view.py`/`pm_arm.html` as new files; (d) Gate-A; (e) restart.
   Unset env -> the control is INERT (fail-closed), not a lockout; the CLI arms regardless.
2. **PM-side link -> the pm_web batch.** ★ ORDERING: the link targets `trading.jacksumner.com/pm/arm`, which 404s
   until the engine control is live. So the link must NOT ship before engine M5. Options: hold the link out of the
   M3-display+M4 batch and ship it in the next pm_web restart AFTER engine M5, OR accept a temporary admin-only 404.
   Recommend the former (a clean, trivial follow-on). This is the one M5 piece that touches the pm_web batch.
