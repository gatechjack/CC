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
