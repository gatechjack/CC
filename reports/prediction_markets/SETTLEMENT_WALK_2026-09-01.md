# PM SETTLEMENT WALK — 2026-09-01 (read-only; nothing changed)

Runner: `cc\pm_settlement_walk2_ro.ps1` (v2 — adds shard ARITHMETIC + the 4 named risks broken out + the
3-pair total; supersedes v1 whose baseline was stale at 495.19). Observed `2026-09-01T04:04:04Z`. Baseline =
SW5 22:15Z (shard-3 $455.25, shard-0 $0.0081; SW5 max_id 24 → "since SW5" == id>24). Companion: `cc\pm_cubs_key_check_ro.ps1`.

State at walk time: engine 127578 / pm_web 124014 (both NRestarts 0, NO restart yet), schema 15, effective_armed=True
latched=False, `close_source='opposed'`=0, NO-leg fills=0.

---

## [A] Each settlement-close walked INDIVIDUALLY — 17 booked rows (risks 3 + 4)

**16 of 17 reconcile EXACTLY** (realized booked == recomputed from that position's own entries, keyed per
`(wallet, condition_id, outcome_index)`), all full 5-contract closes — **no partial or zero fill anywhere**. The
recompute is `realized = net_open × (settled_val − avg_cost)`; every post-fix row matched to <1e-6.

**The ONE exception is EXPLAINED, not a defect — id=8 (the Cubs), the pre-fix settlement:**
- id=1 (Cubs entry): oidx=1, cid=`0x9c62c626…`, 1ct @0.60.
- id=8 (Cubs settlement): **cid=NULL, oidx=None**, realized **−0.6084**, won=0, close='settlement'.
- Settlement closes with NULL cid/oidx = **1** (only id=8); with cid+oidx = **16** (all the rest).
- id=8 was booked 2026-08-31 16:33Z — **before** the 21:33Z *a-write-must-satisfy-every-view* fix that stamps
  cid/oidx onto settlement closes. My recompute keys on (cid,oidx) → finds n=0 entries → recomputes 0.0000 → the
  MISMATCH flag. The **−0.6084 is correct** (hand-validated in R7.g: Cubs entry avg 0.6084 × 1ct loss) and the Cubs
  is netted flat by ticker, so the null key is harmless (the Cubs is in no opposing pair). This is a **direct
  data-validation of the [[a-write-must-satisfy-every-view]] lens**: the single un-walkable-by-(cid,oidx) row is
  exactly the one row that predates the fix; every row after it carries both keys.

### The four named risks, answered one at a time
1. **Two settling in the same scan window** — 5 windows each booked 2 closes at the same `settled_ts`
   (01:27:31 ids 28,31 · 01:41:51 ids 37,38 · 01:42:51 ids 32,33 · 01:47:41 ids 34,35 · 01:57:31 ids 40,41). Each
   row was recomputed independently in [A] and matched → **no cross-window interference.**
2. **A settlement racing an Option-D exit (double-close)** — TRUE double-close test = **>1 terminal close per
   `(wallet, cid, oidx)` = [] (none).** Two tickers show 2 closes at ticker level — **NYMTB-TB** (0x16bb99 avg
   0.6282 + 0x2dc13c avg 0.6463) and **SEABOS-SEA** (0x16bb99 + 0x684baa) — but each resolves to **distinct
   wallets** = the 2-whale same-side stack, which is correct copy behaviour, NOT a double-close.
3. **A partial or zero fill in the set** — **NONE**; every close was the full 5ct (`partial/zero-fill flags: NONE`).
4. **Realized vs the wrong entry price at N=5** — every 5ct close's realized == recomputed-from-own-entries (avg
   cost derived per position). N=5 arithmetic is exact across all 16 post-fix rows.

Boot_reconcile: engine unrestarted since 21:37:14Z (NRestarts 0) → last verdict reconciled=True latched=False stands.
Still-held (journal_signed, 9 keys / 50ct): BALCOL-BAL 5, BALCOL-COL 5, BALCOL-11 5, NYYLAA-LAA 5, PHIAZ-AZ **10**
(2-whale stack), SDCIN-SD 5 (Sep-1 game), TORCLE-CLE 5, SEABOS-SEA 5 (Sep-1 game), MILCHC-CHC 5.

---

## [B] ★ SHARD-PROCEEDS — ESTABLISHED BY ARITHMETIC (not inferred from direction): RETURN-TO-3

| quantity | value |
|---|---|
| post-SW5 entry-spend **E** (id>24, entries) | **$11.6590** |
| post-SW5 settlement gross-credit **C** (5 wins × $5, 11 losses × $0) | **$25.0000** |
| **EXPECTED** shard-3 delta if proceeds return to shard 3 = C − E | **+$13.3410** |
| **OBSERVED** shard-3 delta (455.25 → 468.5897) | **+$13.3397** |
| **OBSERVED** shard-0 delta (0.0081 → 0.0081) | **+$0.0000 (flat)** |

**The arithmetic CLOSES to $0.0013** (a tenth of a cent — baseline rounding on the 2-dp SW5 read), and **shard-0 is
exactly flat**. This is not a direction inferred from a sign; it is `C − E` matching the observed shard-3 delta to a
tenth of a cent while shard-0 does not move. **Proceeds RETURN to shard 3.** Venue cross-check: 56 MLB settlements on
`/portfolio/settlements`, `revenue` in cents matches our bookings (e.g. ATHTEX-8 result=yes rev=500=$5, MIAWSH-WSH
result=yes rev=500, ATHTEX-ATH result=no rev=0).

**What this de-risks (per Jack):** the three-day-runway / "Karen's silent death" concern **evaporates** — the funding
shard replenishes from wins, $150/day is sustainable on shard 3, and a daily shard top-up does **NOT** become an
operational task. Multi-account's shard-aware balance display should still SHOW the per-shard split, but the "healthy
total while the funding shard silently empties" alarm is no longer the live threat it was under a sweep hypothesis.

**Separate observation (P&L, not sharding):** summed realized over all 17 settled closes = **−$15.08**. That is
strategy drawdown on a small, chalk-heavy sample (return-per-dollar is negative here) — a *bankroll* question visible
in the total, distinct from the *sharding-mechanism* question answered above. Flagging it so the negative realized is
on the record; it is not a defect and not a sharding failure.

---

## [C] The three opposing pairs' locked losses — ONE number

| pair | status | legs (realized) | locked net |
|---|---|---|---|
| **SDCIN moneyline** | ✅ settled | SD +2.0080 / CIN −2.1425 | **−0.1345 (13.5¢)** |
| **MIAWSH** | ✅ settled | WSH +2.5065 / MIA −2.5935 | **−0.0870 (8.7¢)** |
| **BALCOL** | ✅ settled (2026-09-01 05:14Z) | BAL +2.2565 / COL −2.3435 (ids 48/50) | **−0.0870 (8.7¢)** |

**★ TOTAL across all THREE settled pairs = −$0.3085 (30.85¢) — [C] CLOSED.** This is the measured cost of the
requirements-miss (the three opposing pairs that formed before the guard shipped). (BALCOL-11 total lost −2.793
separately — a same-side bet, NOT part of the moneyline pair.) **Doc correction (Jack):** the SW5 doc's "BALCOL ~8.7¢"
was mis-attributed — 8.7¢ is **MIAWSH's** *and* (coincidentally) BALCOL's realized; SDCIN cost 13.5¢. The measured
cost is **higher** than the single-pair estimate implied, which **strengthens** the case for the opposing-side guard.

## Post-note (2026-09-01): the FIRST NO position + a 4th (guard-handled) opposing pair
- **NO-leg PROVEN:** the copy engine copied a whale's Under (`KXMLBTOTAL-…SDCIN-10`, leg=no, 5ct) — the platform's
  first NO position. Authenticated venue read: **position_fp = −5.00** == journal −5 → **NO=negative CONFIRMED on real
  data**; boot_reconcile's NO branch is no longer inference (it will reconcile −5==−5 clean, not false-latch).
- **A 4th opposing pair** (SDCIN Sept-1 moneyline, `0x19a016da`) was CLOSED by the guard to flat (not left to settle,
  unlike the 3 pre-existing). It exposed a bounded-churn / fee-loop question — see `OPPOSING_GUARD_CHURN_2026-09-01.md`.
