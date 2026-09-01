# ACCOUNT-SCOPED-QUERY AUDIT — 2026-09-01 (verified live against TWO accounts)

Run AFTER M1 added `kalshi_karen` so every query executes against two accounts (jack=populated, karen=empty),
not reasoned about against one. Runner: `cc\pm_multiacct_audit_ro.ps1` (read-only). Three-way split, no sweep.

## SCOPED AND VERIFIED (account_id in the query AND confirmed isolated live)
- `subdivision.accounts_overview` — jack: realized −20.2384, 7W/16L, 5 open/25ct/$13.40; **karen: all-zero, 0 subdivs.**
- `subdivision.account_pnl(account_id)` — jack −20.2384 (mlb); **karen 0.0, no cats.**
- `subdivision.subdivision_pnl(account_id, category)` — jack/mlb −20.2384 (23 closed, 5 open); **karen/mlb zeros, no error on the empty account.**
- `subdivision.list_subdivisions` — only jack/mlb tile (n_whales=3, n_live_trades=51); **karen absent (no subdivision → correctly not a tile).**
- `subdivision.live_orders / live_positions / get_subdivision / attached_whales` — all `WHERE account_id=? AND category=?`;
  exercised live via Step C's `/live/kalshi_jack/mlb` render (jack's positions shown); karen → `/live/kalshi_karen/mlb` is a 404 (no subdivision), correct.
- `execution.Journal` (the exposure caps) — **open_usd/orders_today/daily_usd seed from `WHERE account_id=?` and key on `(aid,cat)`/`aid`.** jack: $8.75 / 3 / $8.75; **karen: 0 / 0 / 0 → the sum genuinely filters by account** (gate 5 daily, gate 6 open, gate 8 count all pass `sub.account_id`).
- `arm.read_arm_verdict(account_id, category)` — jack armed (scope=both); **karen disarmed (scope=sub, fail-safe — no arm row).** Keys `arm:{account}:{category}` in the legacy `agent_state`.
- `boot_reconcile.journal_signed_positions(account_id)` — **journal side `WHERE account_id=?`**; jack 5 tickers, karen empty.

## SCOPED BUT UNVERIFIED (account_id present in code; an edge/path I could not exercise now)
- **`list_subdivisions` with two POPULATED accounts** — karen has no subdivision, so only the single-tile case ran. The
  cross-account render (2 tiles, per-tile counts) is code-correct (LEFT JOIN pm_account, GROUP BY account) but UNSEEN.
- **`boot_reconcile` full compare + latch for a 2nd account** — the journal side is verified account-scoped; the VENUE
  side (`fetch_positions`) is caller-injected per-keypair and the driver never reconciles karen, so `compare()`/latch for
  a second account is UNRUN.
- **The exposure cap's PM-EXCLUSIVITY for karen** — account-scoped *within PM's journal* (verified), but BLIND to the
  legacy co-tenant on karen's keypair (PM_REQUIREMENTS R7). A real under-count of true venue exposure if karen ever arms.

## NOT SCOPED (single-account BY CONSTRUCTION — must change before any 2nd account trades)
- **Driver credential resolution** — `main.py:1546` HARDCODES jack's keypair (`secrets.kalshi_api_key_id` /
  `kalshi_private_key_pem`), NOT resolved from `pm_account.secret_ref`. (The `secret_ref='kalshi_karen'` we set is
  correct-and-dormant per main.py:3048, but the PM driver path does not consult it.)
- **The PM driver task** — `main.py:1560-1568` wires ONE loop for `account_id=kalshi_jack` (strategies.yaml). No
  per-account driver exists. ★ This is WHY attach-no is safe: the driver never iterates karen, so her row cannot trade.
- **`boot_reconcile`'s Kalshi/venue side** — reads the whole keypair book (R-c full-account), not PM-filtered; correct
  only while the account is PM-exclusive (by design).

## Verdict
The account-PAGES build (read-only display) rests entirely on the SCOPED-AND-VERIFIED reads — safe to build against
two accounts from the first test. Every gap is in the TRADING path, and all are gated shut by Karen's
add-yes/attach-no + the single-account driver. Before Karen (or any account) can TRADE via PM, three things must be
built: per-account cred resolution (secret_ref → keypair), a per-account driver task, and boot_reconcile's
PM-exclusivity resolved (or the co-tenant removed). File, do not build now.
