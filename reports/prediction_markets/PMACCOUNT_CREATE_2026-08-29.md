# pm_account row created -- 'kalshi_jack' (Jack's KALSHI) -- 2026-08-29

**The FIRST money-layer DATA write to live: one `pm_account` row, the prerequisite for the Jack-MLB sub-division.
Fail-closed, one row, verified. NO sub-division, NO attachment, NO order, nothing armed. The promote is a
separate authorization.**

## The row (INSERT, verified)
```
INSERT INTO pm_account (account_id, venue, secret_ref, owner_identity, label, active, created_ts, updated_ts)
VALUES ('kalshi_jack', 'kalshi', 'KALSHI', NULL, 'Jack KALSHI', 1, 1787974948, 1787974948);
```
Verified back exactly: `account_id='kalshi_jack' venue='kalshi' secret_ref='KALSHI' owner_identity=NULL
label='Jack KALSHI' active=1 created_ts=updated_ts=1787974948`. `pm_account` count 0 -> 1; **exactly one row**.

## ★ secret_ref='KALSHI' is currently DECORATIVE (Jack's note, for the record)
NOTHING reads `secret_ref` yet -- not the deployed `execution.py` (grep: zero references; inert until R7) and not
`promote_to_live` (it only checks `account_id` + `active=1`). It becomes **load-bearing at R7**, when the PM broker
resolves credentials. Set correctly now (costs nothing, avoids an R7 surprise): the legacy convention
(`main.py:2998-3007`) resolves `secret_ref='kalshi_karen'` -> Karen keys, **anything-else/unset -> the shared
`KALSHI_API_KEY_ID`/`KALSHI_PRIVATE_KEY_PEM`** (Jack's original account). `'KALSHI'` is the db.py-documented reference
NAME, never a key value.

## ★ account_id='kalshi_jack' is PERMANENT (reasoning, for the record)
It is the `pm_account` PK, becomes the `pm_subdivision` PK `(account_id, category)`, and appears in every
`/live/{account_id}/mlb` URL -- case-sensitive (the route strips but does NOT lowercase). There is NO delete
(ruling 2). So it is effectively permanent. `'kalshi_jack'` scales to `'kalshi_karen'` and matches the secret_ref
convention where `main.py` already special-cases `kalshi_karen`.

## Fail-closed procedure (runner `cc\pm_account_create.*`)
1. **Timing guard** -- poll gap (the PM DB is written by the `*/30` poller); ran 03:42:27Z, 1053s to next poll.
2. **STOP-if-not-empty** -- verified `pm_account`=0 (and subdivision/attachment/order=0) BEFORE any write. Had it been
   non-empty, the runner aborts with NO insert (something else would have written it -- a finding).
3. **Gate-1 DB backup FIRST** -- online backup from a `mode=ro` source ->
   `~/pm_account_create_bak_20260829T034227Z/prediction_markets.db`; `integrity_check=ok`, `version=11`, sha256
   `17b66fad2c7c93823b2ca1cabc8d330d4f83a5419cb1fb087f7e53b1fd77df59`. (Rollback material.)
4. **INSERT one row** (rw, busy_timeout=10s).
5. **Verify** exactly one row with the intended values + `pm_subdivision`/`pm_subdivision_attachment`/
   `pm_subdivision_order` all still 0 + schema 11 + all other counts unchanged. PASS.

## Post-verify beyond the table (observed on the box)
- **`/farm/mlb` Promote button is now a real POST form** -- 10 `action="/live/kalshi_jack/mlb/attach/{wallet}"` forms
  (one per the 10 pinned+active mlb whales, incl. SDTrading + xifutloong3), all labelled **"Promote > Jack KALSHI"**;
  **0** "no live account" spans remaining. No pm_web restart needed -- `active_accounts()` is read live per request.
- **`/live` still honest-empty** (HTTP 200, 1710 bytes, unchanged) -- **an account is NOT a sub-division**; the tile
  appears only on the FIRST promote (which auto-creates the sub-division). Zero `kalshi_jack` tiles.
- arm still **DISARMED** (0 `pm_live` rows in legacy `agent_state`); healthz schema 11; **engine 53046 / pm_web 59422
  unchanged -- NO restart.**

## State + what's next
Live PM DB: `pm_account` = 1 (`kalshi_jack`, active), all other money tables still 0. `origin/prod-live` unchanged
`c88beea`. **The PROMOTE (click "Promote > Jack KALSHI" on SDTrading or xifutloong3 -> auto-creates the
(kalshi_jack, mlb) sub-division + attaches the whale, writing 2 mapping rows, NO order) is a SEPARATE authorization.**
Nothing armed, nothing placed. R5.5 / R7 / R8 remain unauthorized.
