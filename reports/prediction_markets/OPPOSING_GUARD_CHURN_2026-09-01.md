# OPPOSING-GUARD CHURN / FEE-LOOP INVESTIGATION — 2026-09-01 (read-only; established, not assumed)

Triggered by the M2 post-check surfacing `opposed=2` overnight. Jack flagged a potential UNBOUNDED fee loop: the
guard fires on the WHALE'S opposing SIGNAL (which persists as long as whale A still holds its side on Polymarket),
not on "we hold the opposing side" — so a flickering signal could enter-and-close a market forever.

## The data — cid `0x19a016da` (SDCIN Sept-1 moneyline), the one contested market
| id | ticker | wallet | leg | is_exit | close | when |
|---|---|---|---|---|---|---|
| 13 | SDCIN-SD (oidx0) | 0x16bb | yes | 0 (entry) | — | Aug31 18:58Z |
| 52 | SDCIN-SD (oidx0) | 0x16bb | yes | 1 | **opposed** | Sep1 06:13:44Z |
| 54 | SDCIN-CIN (oidx1) | 0x684b | yes | 0 (entry) | — | Sep1 10:14:20Z |
| 55 | SDCIN-CIN (oidx1) | 0x684b | yes | 1 | **opposed** | Sep1 10:14:28Z (next cycle) |

**Enter-close cycles so far: ONE per side** (SD 1+1, CIN 1+1). NOT repeating. Venue confirms both sides now **0.00**
(flat). The NO-leg (separate market, KXMLBTOTAL SDCIN-10) reads **position_fp = −5.00** (held; not part of this pair).

**Why CIN entered at 10:14 despite the guard's "skip":** `detect_opposing_closes` (execution.py:689) contests a cid
only when the opposition is present THE SAME CYCLE (`held ∪ incoming ≥ 2 oidx`). At 06:13 both signals were present →
SD closed, CIN skipped (clean). By 10:14:20 SD's signal had flickered OFF and we were flat → CIN was NOT contested →
entered. At 10:14:28 SD's signal returned → CIN contested → closed. The flicker slips one entry through per side.

## Is it BOUNDED? — YES, but by an INCIDENTAL mechanism (the important finding)
**Gate-4 dedup bounds it.** `execution.py:451` derives a coid stable per `(wallet, cid, oidx, ticker, leg)`;
`Journal.already_placed(coid)` (seeded from the journal) → `skip:duplicate`. So once a `(cid,oidx)` is entered
(id54's coid is now in `_placed_coids`), it CANNOT be re-entered — the same coid re-derives and is deduped. **Max
churn = 1 enter + 1 close per (cid,oidx) = 2 round-trips per opposing pair, then re-entry is blocked forever.** So it
is NOT the unbounded K9 re-POST loop.

**★ BUT the bound is INCIDENTAL, not deliberate — and it is COUPLED to the R7.h gap.** The very dedup that bounds
this is the SAME stable-coid dedup that causes the R7.h "missed re-entry" gap (a whale that exits+re-enters the same
(cid,oidx) is refused). **The filed R7.h fix — key entries on an /activity tx_hash so legitimate re-entry is allowed
— would make each re-entry derive a DIFFERENT coid → the dedup would no longer block → the fee loop would become
UNBOUNDED.** So R7.h and this fee loop cannot be resolved independently: fixing R7.h reopens the loop unless an
opposed-memory is added first.

There is NO explicit cooldown and NO opposed-memory today. The market is "remembered as opposed" ONLY implicitly, via
the per-(cid,oidx) coid dedup.

## PROPOSED FIX SHAPE (Jack to rule; do NOT build yet)
1. **An opposed-memory:** when the guard contests a cid (goes flat), RECORD it (a per-(account, category, cid)
   "contested/off-the-books" marker — small table or journal flag). While the marker stands, SKIP every entry on that
   cid (both sides), regardless of signal flicker — the DELIBERATE version of "an opposed market stays opposed", and a
   bound INDEPENDENT of the dedup.
2. **Clear the marker on resolution, not on flicker:** simplest + safest = the marker stands until the market
   SETTLES (an opposed market stays off the books until it is over). A flicker-based clear risks re-arming churn.
3. **Detect on a signal WINDOW, not a single cycle** (or let the marker cover the flicker) so an entry can't slip
   through between opposition flickers.
4. **★ ORDERING:** the opposed-memory must land BEFORE (or with) the R7.h tx_hash re-entry fix — else fixing R7.h
   reopens the unbounded loop. File them as coupled.

## Severity now
LOW-and-bounded today (2 small round-trips on one pair; net cost a few cents + ~4 fees). The finding is the LATENT
coupling to R7.h, not a live runaway. Division still armed, no latch, order path healthy.

---

## BUILT (2026-09-01, Jack RULED build-now, ahead of R7.h) — the opposed-memory
`execution.account_opposed_cids(conn, account, category)` = {cid with a `close_source='opposed'` row} (journal-
derived — the opposed close IS the record; NO marker table → NO dead rows). `detect_opposing_closes` gains an
`opposed_cids` param: a cid in it is CONTESTED regardless of the same-cycle signal union, so a flicker never lets a
side back in. `live_driver` passes `account_opposed_cids(...)` into the guard each cycle. Journal-derived ⇒
**restart-durable** (the memory survives an engine bounce).

### Adversarial review (pointed at the coupling, per Jack)
- **Does R7.h still create a loop after this?** NO — proven. The guard drops an opposed cid's entries from `kept`
  in the DRIVER, upstream of the chokepoint's gate-4 coid dedup. A re-entry with a BRAND-NEW `signal_id`
  (simulating R7.h's /activity-tx_hash key) is still skipped (`test_opposed_memory_independent_of_coid_survives_r7h`).
  The bound is keyed on the market being CONTESTED, not on the coid → independent of the dedup. R7.h becomes safe.
- **Same-side agreement blocked?** NO — only cids with an opposed CLOSE enter the memory; same-side stacking has
  entries but no opposed close, so it flows even with an unrelated opposed cid present (test).
- **Flicker fixed?** YES — side A held → B incoming → close A, skip B; then B incoming with A's signal GONE and us
  flat → still skipped (test). Without the memory the same input re-enters B (the bug, reproduced in the test).
- **Clear on settlement / dead rows?** The memory is inert after settlement (a resolved market emits no entry
  signals), and there is NO marker table — the opposed-close rows are permanent, correct trade history, not markers
  that outlive their markets. Minor future-hygiene: the DISTINCT-cid set grows slowly over time (cheap; a GC of
  opposed cids past their game date could be added later, not needed now).
- **Spurious closes?** NO — the closes loop only emits for HELD outcomes with a co-present signal; an opposed cid we
  hold nothing on yields a pure SKIP, no close. DISARM still blocks any opposed close (unchanged chokepoint).

### Deploy = ENGINE change (execution.py + live_driver.py) → all-divisions restart; coordinate with PEAD; HALT.
Tests: `test_opposing_close_r5.py` +4 (flicker / same-side / coid-independent / account_opposed_cids); all logic
verified locally, full suite runs on the box at deploy.
