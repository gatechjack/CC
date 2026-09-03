# OPPOSED-GUARD NON-CONVERGENCE — read-only diagnosis (2026-09-03). NOT FIXED (fix is Jack's ruling).

**Trigger:** the OPPOSING-PAIR guard logged "NEWLY-contested" for the SAME cid `0x0f58...aad1` ~1816x between
23:10 and ~03:16Z, `opposed_closes=0` every time. Jack ranked this high and asked: did the opposed-memory fail, is
it keyed on the resolution rather than the decision, why couldn't it flatten, what did it cost. Read-only throughout
(`cc\pm_opposed_diag_ro.{ps1,sh}`, mode=ro). Code read at branch HEAD `b9d3e16` CR-stripped.

## HEADLINE — the specific hypothesis is REFUTED by evidence; the architectural concern is REAL but LATENT
The memory did **not** fail for this cid. `0x0f58` **IS** in the opposed-memory, because an opposed close **did**
book — ONCE, at 15:35:53Z (pre-restart). Since then the memory has been correctly **suppressing re-ENTRY**. What
fired 1816x is a loud per-cycle **WARNING log**, not a re-trade. So the observed incident is **log noise on a
WORKING memory (Issue 1)** — NOT the "memory never recorded the decision" defect. That defect (Issue 2) is real and
latent in the code, but it is NOT what happened here.

## EVIDENCE (kalshi_jack, cid 0x0f589076...aad1 = KXMLBGAME-26SEP021940MILCHC-MIL)
- Rows for the cid (only 2):
  - `id=98` ENTER oidx0 yes x5 @0.60 wallet 0x684baa57 **resp 2026-09-02T15:35:45Z** (is_exit=0, src=None)
  - `id=99` `is_exit=1 close_source='opposed'` oidx0 x5 @0.59 same wallet **resp 15:35:53Z** (8s later)
- net-open oidx0 = **0.0** (entry 5 - opposed-close 5 = flat since 15:35:53Z).
- `account_opposed_cids('kalshi_jack','mlb')` = **4 cids, and 0x0f58 is one of them** -> the memory HAS it.
- opposed rows EVER: kalshi_jack **5**, kalshi_karen **0**.
- The sampled 1816 guard fires were all cid 0x0f58 (matches SW10's naming + the diag sample); a full distinct-cid
  tally was not run but the mechanism below is proven from the rows + code regardless.

## THE MECHANISM (proven from `execution.detect_opposing_closes` + `account_opposed_cids`, execution.py:714-783)
1. `account_opposed_cids` = `SELECT DISTINCT condition_id ... WHERE close_source='opposed'`. 0x0f58's id=99 opposed
   close puts it in the set from 15:35:53Z on.
2. Every subsequent cycle, wallet 0x684baa57 STILL holds MIL-yes on Polymarket -> `positions_to_entry_signals`
   emits an oidx0 entry -> the cid is in `inc_by_cid`.
3. In `detect_opposing_closes`, `contested = (cid in opposed_cids) or len(held_oidx | inc_oidx) >= 2`. For 0x0f58:
   held_oidx = {} (flat), so it is contested SOLELY via **`cid in opposed_cids`** (the memory) -> the memory IS
   doing its job.
4. `kept = [s for s in entries if s.condition_id not in contested]` -> the incoming MIL-yes entry is EXCLUDED ->
   **re-entry is correctly suppressed** (no order placed). Good.
5. `closes`: iterate `held_outcomes.get(cid, set())` -> empty (flat) -> **no closes -> `opposed_closes=0`**.
6. But line 732 `if _contested: log.warning("... NEWLY-contested -> FLAT (close held + skip both sides); ...")`
   fires EVERY cycle, because the guard has NO branch distinguishing "newly contested this cycle" from "already in
   the opposed-memory (suppressing a flickering re-signal)". It stopped at ~03:16Z when the Polymarket market
   resolved (game settled) and the whale's position/signal vanished.

## JACK'S FOUR QUESTIONS, ANSWERED
- **Q1 Why did the memory not suppress re-detection?** It DID suppress re-ENTRY (the MIL-yes signal was excluded
  from `kept` every cycle; zero re-entry orders). It did not suppress the WARNING LOG: the guard warns for EVERY
  contested cid, including memory-suppressed ones, with no quiet "already handled" path. Re-detection (the log) is
  not re-entry (the trade); re-entry was blocked, the log was not.
- **Q2 Is the memory keyed on the close having happened (resolution not decision)?** YES — `account_opposed_cids`
  filters `close_source='opposed'`, so a cid enters the memory ONLY once an opposed close BOOKS. For 0x0f58 a close
  did book (id=99), so it IS remembered. **The latent defect stands but did not fire here:** a contest that
  generates NO close (see Issue 2) would never book an opposed row -> never enter the memory.
- **Q3 Why did both sides skip / couldn't it flatten?** It DID flatten, once, at 15:35:53Z (id=99). After that the
  account held nothing for the cid, so there was nothing to close -> `closes=[]`, `opposed_closes=0` — because
  `held_outcomes[cid]` is empty (flat), NOT because a close failed at evaluate. The recurring incoming MIL-yes was
  correctly SKIPPED (excluded from `kept`). "close held + skip both sides" is **misleading wording**: nothing was
  held to close, and only the incoming side was skipped. No evaluate skip-reason applies (no close reached evaluate).
- **Q4 What did it cost?** Log noise only: ~1816 WARNING lines (23:10-03:16), plus more before the restart. **NOT
  API load** — the guard is a pure in-memory set check; `account_opposed_cids`/`account_held_outcomes` are per-cycle
  sqlite reads that run regardless of any contest; the whale /positions poll is the normal cadence, not extra. **No
  wrong trade, no un-flattened position** (0x0f58 was flat from 15:35:53Z). Real costs: (a) operator confusion — a
  loud WARNING that reads like a live flatten firing 1816x obscures genuine events; (b) the latent Issue-2 defect
  could, on a DIFFERENT cid, leave a decided-but-uncloseable contest un-flattened (rides to settlement) and
  re-detecting forever.

## TWO DISTINCT ISSUES (keep them separate — different fixes, different severity)
**Issue 1 (OBSERVED, benign): loud per-cycle WARNING on a memory-suppressed cid.** The guard cannot tell "newly
contested" from "already in memory". Fix direction (LOGGING ONLY, no order-path change): warn only on a genuinely
NEW contest (a cid newly entering `contested` via `held|inc>=2`, or where `opposed_closes>0`); for a
memory-suppressed cid emit DEBUG or once-per-cid-per-life. Low risk, but it edits a safety log so it is a ruling.

**Issue 2 (LATENT, real, NOT observed here): the memory records the RESOLUTION, not the DECISION.** Confirmed against
code. When a cid is DECIDED contested but generates NO close (we hold side 0, the contest is created by an incoming
side-1 signal, and no co-present side-0 entry exists to ROUTE the per-wallet close — the side-0 whale exited/flipped),
no `close_source='opposed'` row is written -> the memory never records it. Practical harm in that case: the held
side is **not flattened** (rides to settlement — the guard's FLAT intent is silently defeated for that position),
and it re-detects every cycle. NB: re-ENTRY is still blocked by the `held|inc>=2` path as long as we hold the side;
the memory's unique value is surviving a FLICKER after we go flat, which is exactly what it cannot record here.

### What the memory SHOULD be keyed on (Issue 2), and what it changes
Key it on the **DECISION** (a cid was contested), not the booked close. Concretely: when `detect_opposing_closes`
marks a cid contested, persist a lightweight marker EVEN WHEN `opposed_closes=0`, and have `account_opposed_cids`
read that marker UNION the existing opposed-close rows. Options: a tiny `(account_id, category, condition_id)`
marker table, or a zero-fill sentinel order row. **What it changes vs today's deliberate design:** the current code
comment (execution.py:718-720) explicitly chose "the opposed close IS the record, so there is NO separate marker
table and thus NO rows that outlive their markets." A decision-keyed marker REINTRODUCES a row that persists after
the market settles — but it is INERT (no incoming signal ever references a resolved market's cid again), so it
causes no false contest; the only cost is bounded DB growth (one tiny row per ever-contested market). That is the
tradeoff Jack must weigh: durable decision-memory (correct flatten-and-remember even when the close can't route) vs
zero marker-table rows (today, at the cost of the latent gap). Fixing Issue 2 also touches the ORDER PATH (a write
at contest-decision time), so it earns the same care/box-scratch as any chokepoint change.

## R2 HISTORY SCAN — how often did the harmful shape ACTUALLY occur? (read-only, `cc\pm_opposed_history_ro.{ps1,sh}`)
Jack: "Look for it in the history rather than reasoning about it." Scanned the retained journal **Aug 30 00:00 ->
Sep 3 16:31 (~4.5 days, 574,004 lines)** for every OPPOSING-PAIR warning, cross-referenced each contested cid vs the
DB (entered / opposed-closed / settled).
- **1,815 warnings, only 2 distinct contested cids.** Contests are RARE on MLB (directional copy).
- `0x0f58` (MILCHC-MIL): **FLATTENED** (opp_rows=1, working). **1,796 of the 1,815 warnings were this ONE
  already-flattened cid re-logging** — that IS the Issue-1 noise, quantified.
- 1 cid **NEVER-HELD** (contested, held nothing -> nothing to flatten; benign) — the other 19 warnings.
- **ISSUE-2 occurrences (held a side, NEVER opposed-closed, rode to settlement un-flattened): `0`.** OTHER (held +
  non-opposed exit, or still open): `0`.
- **Conclusion: the harmful Issue-2 shape has occurred ZERO times in ~4.5 days of live MLB.** This REFUTES "it has
  probably already happened" with evidence. It does NOT retire the fix: the defect is real, the window is short, and
  UFC's more two-sided/hedging whales make the no-co-present-entry shape materially more likely than MLB's
  directional copy. Reframe: real latent gap, NOT yet triggered on MLB, fix before UFC widens the exposure -- AND add
  the missing instrumentation (nothing logs an un-flattened contested position today; the fix must make it visible).

## RECOMMENDATION (Jack rules; NOT implemented)
- Both issues are rulings, not patches. Issue 1 is a cheap logging-only change that removes the 1816x noise and the
  misleading "NEWLY"/"close held" wording. Issue 2 is the architectural fix (decision-keyed memory) and is the one
  that actually prevents a future un-flattened contested position; it touches the order path.
- If only one is done first, do **Issue 1** (stops the noise, zero order-path risk) and rule on Issue 2 separately.
- This is INDEPENDENT of B2/multicategory; it affects the live mlb path today on both armed accounts.
