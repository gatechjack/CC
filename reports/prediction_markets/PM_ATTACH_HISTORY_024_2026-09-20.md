# PM ENGINE — ATTACHMENT SPAN HISTORY (migration 024): WIRED END-TO-END — 2026-09-20

**STATUS: INVESTIGATION, READ-ONLY. No writes, no restart, engine untouched. VERDICT: migration 024 is wired end to
end — the writers (attach + detach) are DEPLOYED, ATOMIC, and FLOWING; the box event table has 28 real rows and its
span history AGREES with the attachment state (0 disagreements). NO REPAIR NEEDED.** Everything below is verified
against THE BOX (not the record), per the standing "the box is the answer" rule. Branch
`pm-attach-history-024-2026-09-20` off `origin/prod-live` @ `d1b93ce7`.

Evidence runners (cc/, all RO): `pm_attach024_probe_ro.{ps1,py}` (farm_actions sha + atomic markers, db.py 024,
driver query, 024 table + jack/mlb spans), `pm_attach024_consistency_ro.py` (event-vs-attachment atomicity check).
Raw output: `cc/renders_tile/attach024_probe.txt`, `attach024_consistency.txt`.

--------------------------------------------------------------------------------
## FINDING 1 — the 024 DDL (table / columns / indexes; additive-only)

`db.py:996` `MIGRATION_024` (registered `db.py:1040` `(24, MIGRATION_024)`), on the box (schema head **24**, DDL
present — probe F1 BOX):

    CREATE TABLE IF NOT EXISTS pm_subdivision_attachment_event (
      id INTEGER PRIMARY KEY,            -- rowid alias; append-only
      account_id TEXT NOT NULL, category TEXT NOT NULL, wallet TEXT NOT NULL,
      action TEXT NOT NULL,              -- 'attach' | 'detach'
      source TEXT,                       -- provenance (promote_to_live | detach_from_live)
      actor  TEXT,                       -- owner_identity that performed it (pm_web); NULL/'cli' otherwise
      ts     INTEGER NOT NULL )          -- event time (== the attach added_ts / detach removed_ts)
    CREATE INDEX IF NOT EXISTS ix_pm_subattach_event ON pm_subdivision_attachment_event(account_id, category, wallet, ts)

**Additive-only:** `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS` — no ALTER, no data change to any
existing table. New standalone table; engine-neutral (db.py:993-995: "the DRIVER never reads it").

--------------------------------------------------------------------------------
## FINDING 2 — the WRITERS are wired, atomic, and DEPLOYED (question 2 = YES, verified from the code, not the note)

**Both writers append an event row IN THE SAME TRANSACTION as the active flip** (`farm_actions.py`, git prod-live ==
box, CR-sha16 **`23f91d44e59275ec`** on both — the box runs this exact file byte-for-byte):

- **`promote_to_live` (attach + RE-attach)** — `farm_actions.py:113`. `BEGIN IMMEDIATE` (132) → UPSERT
  `pm_subdivision_attachment` (active=1, removed_ts=NULL; the UPDATE omits added_ts so a re-attach preserves the
  original) (152-156) → **event INSERT** (161-164) `if not already` (a brand-new attach OR a re-attach-after-detach —
  the exact new span the single UPSERT row would otherwise lose; an idempotent repeat logs nothing) → `COMMIT` (165);
  `except: ROLLBACK; raise` (166-168).
- **`detach_from_live`** — `farm_actions.py:173`. `BEGIN IMMEDIATE` (185) → `UPDATE ... SET active=0, removed_ts=?
  WHERE ... active=1` (187-190) → **event INSERT** (191-194) `if cur.rowcount` (a REAL detach only; a no-op detach
  logs nothing) → `COMMIT` (195); `except: ROLLBACK; raise` (196-198).
- **Reader for the UI:** `read_attachment_events` (`farm_actions.py:203`) — events oldest→newest; pair each 'attach'
  with the next 'detach' (a trailing unpaired 'attach' = the current open span). Honest-empty pre-024.

**★ THE DETACH NON-ATOMICITY (the skeptic's build-time finding) IS FIXED AND DEPLOYED.** The comment at
`farm_actions.py:180-183` is the fix verbatim: "the active=0 flip AND the append-only 'detach' event MUST land
ATOMICALLY — the connection is autocommit (isolation_level=None), so bare statements would commit SEPARATELY and a
crash between them could leave the log with an unpaired open span. Wrap BOTH in ONE BEGIN IMMEDIATE...COMMIT." The box
file is byte-identical to this (sha match), so the DEPLOYED detach is the atomic version — not assumed, confirmed. And
proven on real data (Finding 3 consistency: 0 disagreements).

**Who loads farm_actions (deployability):** `web/app.py` (pm_web Promote/Detach routes), `scripts/pm_cli.py` (crons),
`search_run.py` (promote/demote flow). **The engine does NOT import it** (grep of `main.py` / `live_driver.py` /
`execution.py` = none). So farm_actions is pm_web/pm_cli-owned; a change to it is pm_web-deployable (pm_web restart +
next pm_cli cron), **no engine restart**. (Moot here — no change is needed.)

--------------------------------------------------------------------------------
## FINDING 3 — BOX STATE: the table is POPULATED and CONSISTENT (wired-and-working, not empty, not broken)

**`pm_subdivision_attachment_event` ROW COUNT = 28** (probe F3 BOX). Real attach/detach events since 024 landed
(earliest event ts `1789707043` = 2026-09-20, the 024-landing day), actor `jack`, across atp / cfb / itf / mex / cs2 /
uel / mlb / epl / wnba / wta / nfl. So the writers are FLOWING — this is not the "only the DDL landed" case.

**Atomicity on real data (`pm_attach024_consistency_ro.py`):** across all **28** distinct (account, category, wallet)
event keys — **0 last-event-vs-active mismatches, 0 orphan events, 0 non-alternating sequences.** Every whale's last
event action agrees with its attachment `active` flag; every event has an attachment row. **The span history agrees
with the attachment state** — the exact disagreement the skeptic warned about does not exist on the box. (Spot check:
jack/mlb `0x41b4cd88` has an 'attach' event at ts `1789887664` and its attachment row is active=1, added_ts=`1789887664`
— they match.)

**★ NO MULTI-SPAN EXAMPLE EXISTS YET (a finding, not a fault):** 28 keys / 28 rows = exactly ONE event per key so far
— no (account, category, wallet) has been detached AND re-attached since 024. So every logged whale currently has a
single span-boundary (an open attach or a closing detach), which the UI would render as one span — same as the
attachment row shows today. Multiple spans will accumulate the first time a whale is detached then re-attached on the
same sub-division AFTER 024; the machinery is proven correct and ready for it.

**jack/mlb's three "formerly-live" whales have NO event rows — because they pre-date 024:**

| whale | attachment row (active / added_ts / removed_ts) | event rows |
|---|---|---|
| `0x16bb9951…` | 0 / 1787975891 / 1788468918 | none (detached ~2026-09 **before** 024) |
| `0x767a7964…` | 0 / 1788469260 / 1789224656 | none (before 024) |
| `0xfb07f485…` | 0 / 1789345709 / 1789345739 | none (before 024) |

Their detaches (removed_ts up to `1789224656`) all predate the earliest event (`1789707043`). This is expected — a
deploy changes what is written FROM NOW ON, not what already happened. It is the backfill question (Finding 4), not a
writer bug.

--------------------------------------------------------------------------------
## FINDING 4 — BACKFILL: propose, do NOT run; and be honest about what it CANNOT recover

**The exact single span already exists** for every attachment row in `pm_subdivision_attachment.added_ts/removed_ts`
(the roster UI reads it today, Deploy 12). So a backfill's only job is to make the event table the uniform single
source for the UI.

**PROPOSAL (idempotent one-off, Jack-gated, NOT run):** for every existing `pm_subdivision_attachment` row, seed ONE
event pair from the ATTACHMENT ROW — an `attach` at `added_ts`, and (if active=0) a `detach` at `removed_ts` —
`source='backfill'`, actor NULL. Idempotent via a guard (skip a (key) that already has any event, or any 'backfill'
row). This is EXACT (copied from the attachment row, not inferred) for the current-or-last span, and it is exactly
what the UI already shows, so it is honest.

**★ WHAT IT CANNOT RECOVER (say it plainly):**
1. **Pre-024 attach → detach → re-attach MULTI-SPAN history is GONE from every source.** The re-attach UPSERT
   (`farm_actions.py:155`) sets `active=1, removed_ts=NULL` and keeps the ORIGINAL `added_ts`, so the intermediate
   detach + re-attach boundaries were overwritten in the attachment row and were never in the event table. No backfill
   can reconstruct them. A backfilled whale that was actually attached-detached-reattached will show a SINGLE span (its
   original added_ts to its last removed_ts), which is a simplification, not a lie — but it is NOT the true history.
2. **The journal is NOT a valid source for span boundaries — inferring from it would MIS-STATE the span.** The journal
   has first/last COPY timestamps, which are not attach/detach times. Proven on the box (jack/mlb `0x16bb9951`): the
   attachment span is added `1787975891` → removed `1788468918`, but the journal copy window is first `1788128073` →
   last `1788447719` — the whale was **attached ~1.75 days before its first copy** and **detached ~5.9 h after its
   last copy**. A whale attached for a week that copied nothing leaves NO journal trace at all; a detach the day after
   the last copy is indistinguishable from one a month later. **A reconstructed span that looks exact but is inferred
   is worse than a gap** — so the backfill must seed from the ATTACHMENT ROW (exact) and MUST NOT infer from the
   journal. If any inferred value were ever used it would have to be labelled `approximate`/`inferred` and never shown
   as an exact date; the recommendation is simply not to infer.

**Recommendation:** the backfill is OPTIONAL and low-value (single-span whales already render correctly from the
attachment row; it recovers no lost multi-span). Run it only if Jack wants the event table to be the sole UI source;
otherwise the UI can read the attachment row for pre-024 whales and the event log for post-024 spans. **Jack's ruling
required; not run.**

--------------------------------------------------------------------------------
## FINDING 5 — the driver's per-cycle roster query is UNAFFECTED

`live_driver.py:1378` `SELECT wallet FROM pm_subdivision_attachment WHERE account_id=? AND category=? AND active=1` —
keys on `active=1`, reads only the attachment table. `live_driver.py` does not reference
`pm_subdivision_attachment_event` at all (probe F5 BOX: False). 024 is engine-neutral; the armed driver is untouched
by it and by any future backfill (the engine never reads the event table).

--------------------------------------------------------------------------------
## REPAIR / BLAST RADIUS

**No repair needed** — the writers are wired, atomic, deployed (box == git), and flowing consistently. The only
optional action is the Finding-4 backfill, which is a pm_web/pm_cli-side INSERT into the pm_web-owned event table
(engine never reads it), idempotent, no restart — and it recovers no lost multi-span history. Both require Jack's
ruling; nothing was run.

--------------------------------------------------------------------------------
## HAND-OFF TO THE UI WORKSTREAM (once you want multi-span in the roster Tenure cell)

- **Table:** `pm_subdivision_attachment_event` (append-only). **Columns:** `id, account_id, category, wallet, action
  ('attach'|'detach'), source, actor, ts`.
- **Ordered-spans reader (already built):** `farm_actions.read_attachment_events(conn, account_id, category,
  wallet=None)` → events for the (account, category[, wallet]) **ordered oldest→newest (ts, id)**. Build spans by
  pairing each `attach` with the NEXT `detach`; a trailing unpaired `attach` is the CURRENT open span. Empty list
  pre-024 (never 500s).
- **Rendering:** the Deploy-12 roster Tenure cell is already structured as a `.rt-spans` list — render one
  `<span class="rt-span">` per paired span (start–end, or "attached <start> · N days" for the open span). **Today most
  whales have ≤1 event, so it renders one span (same as now); the list grows as re-attaches occur.** For a whale with
  NO events (pre-024, e.g. jack/mlb's three formerly-live), fall back to the attachment row's added_ts/removed_ts (the
  exact single span) — or, if the Finding-4 backfill is run, the event log covers them too. Label a whale that has an
  attach event but predates any detach event honestly as its current span; never synthesize a boundary the log lacks.
