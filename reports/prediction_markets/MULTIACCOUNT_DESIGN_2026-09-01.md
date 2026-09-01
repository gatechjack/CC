# MULTI-ACCOUNT — design + rulings (2026-09-01)

Branch `pm-multiaccount-2026-09-01` (off `f1e9978`). This is the main build after the settlement walk. It is a
different KIND of work — UI + data modelling — and Jack said the **full UI rewrite WAITS behind multi-account**
(the account layer changes navigation and what a page MEANS). So this build is a NAV + DATA change re-using the
existing visual language (same `pm.css` components), not a visual rewrite.

## What the platform is TODAY (mapped read-only, cites in the git history of this doc's commit)
- **pm_account** (migration 010): `account_id` PK, `venue`, `secret_ref` (a KeyVault NAME, never a value),
  `owner_identity` (TEXT, nullable, **never enforced anywhere**), `label`, `active`, timestamps. One row today: `kalshi_jack`.
- **pm_subdivision** = `(account_id, category)` + config + caps; `pm_subdivision_order` = the journal (carries
  `realized_pnl`, `won`, `close_source`, `settled_ts` since migration 015).
- **pm_web** is a SINGLE-FILE FastAPI app (`web/app.py`), templates extend `pm_shell.html` (nav = Dashboard |
  Farm League | Live sub-divisions), styled by hand-authored `static/pm.css` (dark theme, `.pm-tilegrid`,
  `.pm-table`, `.pm-gain/.pm-loss`, `.pm-btn`). Reads are `subdivision.*` helpers; **standalone by design** — no
  broker, no secrets, no order path.
- **Arm state** lives in the LEGACY DB `data/trading_corp.db`, table `agent_state`, actor `pm_live`, keys
  `arm:global` + `arm:{account}:{category}`. `effective_armed = global AND sub`. `arm.read_status()` is the
  read-only display API; `arm.arm()/disarm()` write via `set_agent_state`. Driver re-reads per ~7s cycle.

## ★ TWO ARCHITECTURAL FINDINGS that shape the build (not config flips)
1. **pm_web holds NO trading creds** — the Stage-5 KV fetch is Anthropic-only, least-privilege (Jack ruled). So
   pm_web **cannot** do a live Kalshi balance read. "Shard-aware balance display" therefore needs the ENGINE to
   persist per-shard balance snapshots that pm_web reads. (This also finally lands backlog item #2, in the right place.)
2. **Authelia identity is entirely unwired** — no handler reads `Remote-User`/`X-Forwarded-User`; the app trusts
   the proxy and serves everything. owner_identity scoping + an admin-only arm control require FIRST-TIME header
   plumbing plus an explicit definition of "who is admin."

---

## PHASES (each independently deployable; HALT gates marked)
- **M0 — foundations (ruling-independent, NO deploy):** `market_describe.py` (plain-language, interim item a) +
  per-account/subdivision realized-P&L aggregation (interim item b underneath the account page) + unit tests. ← THIS TURN.
- **M1 — Karen as 2nd `pm_account` row** (add-yes, attach-NO). A LIVE PM-DB write → **HALT for Jack**. Tooling: `pm_account_create.ps1`.
- **M2 — account pages:** `/accounts` overview + `/account/{id}` (P&L, win/loss, balances across its sub-divisions),
  re-using M0. Read-only render. Deploy → **HALT**.
- **M3 — shard-aware balance:** engine writes `pm_shard_balance_snapshot`; pm_web reads latest. Engine change +
  restart → **HALT** (coordinate with the shared engine).
- **M4 — Authelia identity + owner_identity scoping + admin list.** Deploy → **HALT**.
- **M5 — UI arm/disarm (admin-only, GLOBAL), CLI stays authoritative.** Deploy → **HALT**. The R7.d STOP path must
  never depend on pm_web being up — the UI only writes the same `agent_state` the CLI does; the CLI is unaffected.

---

## ★ RULINGS JACK OWES — options + my recommendation (I keep building M0 around them)

**R1 — Does the account page REPLACE the dashboard or SIT ABOVE it?**
- (a) SIT ABOVE — add `/accounts` as a new top-of-hierarchy; leave Dashboard/Live/Farm as-is (least disruption).
- (b) REPLACE — `/` becomes the accounts overview; per-account → sub-divisions supersedes the old "Live" list; Farm
  stays a nav peer. Same visual language, new grouping.
- **Recommend (b), done minimally.** With >1 account the natural landing IS "which account?", and the old Dashboard
  is a thin 2-card menu the account layer subsumes. (b) is the honest end-state and is a nav/data change, not the
  deferred visual rewrite. (a) is the safe fallback if you want zero disruption to muscle memory this week.

**R2 — P&L basis: realized-only, or realized + open mark-to-market?**
- (a) Realized-only (SUM `realized_pnl` from the journal) + open positions shown at COST basis. Free, always
  available, credential-free.
- (b) + open mark-to-market — needs live venue prices per page load, which pm_web CANNOT do without creds (finding 1).
- **Recommend (a).** Realized-only + cost-basis (both already in the journal), labelled honestly ("cost, not mark").
  Mark-to-market becomes a later add-on riding the M3 snapshot mechanism (the engine can write position marks too).

**R3 — What does Karen see of the shared Farm League, and can she promote?**
- (a) Full Farm League, READ-ONLY (no promote/demote/analyze/attach). (b) Full peer (can promote + analyze).
- **Recommend (a).** Farm League is communal research, so she sees it; but promote/attach are live-money roster
  changes and Analyze SPENDS — keep those admin-only. This also pre-empts the backlog footgun (an unconfirmed roster
  edit on an armed division once >1 person can reach the UI).

**R4 — Does Karen see the global arm state at all?**
- (a) Nothing (arm is not her concern — her account has no PM subdivision armed). (b) Global arm state read-only.
- **Recommend (a) for now.** Karen's account carries NO PM whale attachment (your ruling), so the PM arm state does
  not gate her trading — showing it would be noise. Revisit if she ever gets a PM subdivision.

**R5 (discovered) — How is "admin" determined** for the arm control + write-actions (owner_identity is new)?
- (a) `is_admin` flag / a `pm_operator` table. (b) A config/env list of admin Authelia identities checked against
  the `Remote-User` header. (c) Any authenticated user (status quo — unacceptable once Karen logs in).
- **Recommend (b).** A small explicit admin-identity list (env `PM_ADMIN_IDENTITIES` or a settings constant), no
  schema change, checked against Authelia's header. Jack = admin, Karen = not.

**R6 (discovered/architectural) — Shard-aware balance display source** (finding 1)?
- (a) Engine writes `pm_shard_balance_snapshot` (per-shard, timestamped); pm_web reads the latest.
- (b) Give pm_web scoped kalshi read creds → live read per page load (breaks least-privilege; new credential surface).
- **Recommend (a).** Keeps pm_web credential-free, gives a balance history for free, and a per-cycle/per-N-min
  snapshot is plenty fresh for a display. This is the shard-money-mgmt backlog item #2 landed correctly.

---

## ★ RULINGS — FINAL (Jack, 2026-09-01) — these BIND the build; recommendations above are superseded where they differ
- **R1 = REPLACE, minimally.** `/` becomes the accounts overview; per-account → sub-divisions supersedes the Live
  list; Farm stays a nav peer. Same visual language — NOT the deferred rewrite.
- **R2 = REALIZED-ONLY + open-at-cost.** Both already in the journal, neither needs creds. Mark-to-market rides M3.
- **R3 = Karen RUNS ANALYZE.** (Adjusted from the rec.) Analyze is the promotion JUDGE — how anyone decides a whale
  is worth copying; locking Karen out makes her account something Jack operates, not something she OWNS. Spend is
  bounded by the existing **$20/day cap** (the control that already exists for this). **Promote + attach STAY
  admin-only** (they move money). **★ FLAG, do NOT build unasked:** if Jack later wants Analyze spend attributable,
  per-identity tracking against the cap is a small addition — offer it, don't build it.
- **R4 = Karen SEES the global arm STATE (read-only); the CONTROL is admin-only.** (Adjusted.) "No PM subdivision
  today" is the kind of *true-today* that goes silently wrong — the global arm is GLOBAL; if she ever gets a
  subdivision, a state she can't see governs whether it trades. Visibility costs nothing; a hidden global switch is
  a nasty surprise later. Show state, hide the toggle.
- **R5 = `PM_ADMIN_IDENTITIES` vs the Authelia header. ★ FAIL CLOSED.** Header absent OR config unset → **NOT-ADMIN**,
  never admin. An unwired identity layer defaulting to admin is the worst possible default, and this is first-time
  plumbing — the default must be deny.
- **R6 = engine writes `pm_shard_balance_snapshot`, pm_web reads the latest. ★ THE DISPLAY MUST SHOW THE SNAPSHOT'S
  AGE.** A stale balance shown as current is the exact shape that killed Karen's division — surface "as of Nmin ago"
  and stale-band it (green/amber/red on age), never present a stale number as live.

---

## Interim items (folded in, cost almost nothing here)
- **Plain-language market descriptions** — `market_describe.py`: `KXMLBGAME` → "Padres vs Reds — Padres to win
  (Aug 31)"; `KXMLBTOTAL-…-9` → "Padres vs Reds — Over 8.5"; `KXMLBSPREAD-…-SD2` → "Padres −1.5"; anything else →
  honest fallback (market type + raw ticker). Pure, no order path — imports only the pure team-mapping + parsers.
- **Realized P&L per position** — R-d books `realized_pnl` on each close; the account/subdivision aggregation
  surfaces it (per-subdivision realized total + per-closed-position realized). The /live per-order realized already
  renders; the account page adds the roll-up.
