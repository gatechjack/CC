# Net-verify target (Job 3, CORRECTED 2026-08-22 per Jack's reconciliation Issue 2) — QUEUED

**Status: NOT run** (needs ingested data; executes at Step-3/Step-4). This doc commits the target + method.
**Superseded pick:** the earlier version made Kickstand7 (Fed) the primary net-verify — WRONG per §12
(which requires a BINARY-market whale, non-suspect rows) and doubly wrong now that live data shows the Fed
quarantine fires (`QUARANTINE_RECONCILE_2026-08-22.md`). Corrected below.

## PRIMARY §12 net-verify target = SDTrading (MLB) — Jack's ruling
- Wallet: **`0x16bb9951a36fce71e2ef57890b786145e0ba8492`**, name **SDTrading**, live-loop MLB whale
  (`poly_kalshi_mlb/live_whales`), a genuine 1-of-12 roster member.
- Category: **mlb** — BINARY single-game moneylines (two-outcome), the §12-required market type.
- Measured (read-only reconciliation 2026-08-22): 505 closed positions, mlb=462, **7 suspect total (5 mlb)**.

## ★ SDTrading is NOT exclusion-free — the net-verify must reckon with the clause-(a) defect
The reconciliation proved the 5 MLB exclusions are **clause (a) FALSE POSITIVES on real losing bets**
(e.g. `mlb-sd-bos-2026-04-03` tb=26158.69 rp=-27962.12) — `/closed-positions total_bought` understates
cost on scale-ins, so real losses read as "loss exceeds cost." Therefore a "scoreable-rows-only net
matches independent sum" check would PASS while the scoreboard is WRONG (real losses dropped). The method
below reconciles the FULL sum too, so the net-verify EXPOSES the defect instead of passing over it.

## Method (from-scratch reimplementation — a real cross-check, not importing ingest.py)
1. Read-only pull SDTrading's raw `/closed-positions` (all pages), independently.
2. Reimplement the §3A predicate FROM SCRATCH (EPS = max($1, 1%*total_bought); clause (a) loss-exceeds-cost;
   clause (b) zero-cost/nonzero; event-group propagation by `event_slug`). Do NOT import `ingest.py`.
3. **Three reconciliations (all must be stated):**
   - **(A) FULL net** — independent sum of `realized_pnl` over ALL mlb rows == DB naive-all sum. Proves the
     parse -> ingest -> store arithmetic with NO quarantine confounding. THIS is the true whale performance.
   - **(B) SCOREABLE net** — independent sum over pnl_suspect=0 mlb rows == DB `net_realized_pnl`; independent
     n_excluded/excluded_pnl == DB. Proves the §3A predicate + rollup wiring.
   - **(C) THE GAP = (A) - (B)** — enumerate the excluded rows and CONFIRM they are real single-game losses
     wrongly dropped by clause (a). This QUANTIFIES the §13A(f) scoreboard bias for a copy-relevant whale.
4. **PASS criteria:** (A) and (B) both reconcile to the cent AND (C) is explained (each excluded row is a
   real loss, not a phantom). A net-loser must show negative ROI on the FULL net. This closes §13A(a)
   (UFC reconciliation) positively via a clean binary whale, per §12.

## Step-3 preview wallet stays Kickstand7 — rationale CORRECTED to match the data
Kickstand7 `0xd1acd3925d895de9aec98ff95f3a30c5279d08d5` remains the Step-3 single-wallet checkpoint —
now on EVIDENCE, not assumption:
- genuine 1-of-12 roster member; largest Fed footprint (83 rows);
- **exercises the quarantine on live data** — 104 suspect (72 clause-b negRisk phantoms + 3 Fed incl a dust
  leg propagating to 2 winners; nba-mvp/ufc-281 futures). My earlier "quarantine fires on Kickstand7 Fed"
  claim was UNEVIDENCED when written (I reasoned it from negRisk shape; it also CONTRADICTED the then-record
  "Fed proven clean"). The probe confirms it fires — and in doing so exposes the clause-(a) defect. So Step 3
  is now a DIAGNOSTIC checkpoint: inspect whether clause (a)/propagation OVER-exclude before trusting ranking.

## Quarantine's first live exercise (Jack's question — answered)
No longer fixture-only: the actual ingest code (row invariant + event-group propagation) ran on live rows in
the read-only reconciliation (propagation fired 29x on Kickstand7, 1x on each MLB whale). No contrived wallet
needed. But the first exercise REVEALS the clause-(a) defect (§13A(f)) — so the ranking must not be trusted
until that is resolved; ingestion (data capture, advisory `pnl_suspect`) is safe.

## Validation gap (honest)
Nothing here has run against a deployed DB. The reconciliation numbers above are read-only-probe measured
(not from a backfill). The net-verify itself is queued to Step-3/Step-4.

Cross-ref: `QUARANTINE_RECONCILE_2026-08-22.md`, P1_PLAN §3A + §12 + §13A(a)/(f), DEPLOY_SEQUENCE.md.
