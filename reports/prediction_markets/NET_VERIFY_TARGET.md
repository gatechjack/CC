# Net-verify target (Job 3, CORRECTED 2026-08-22 per Jack's reconciliation Issue 2) — ✅ PASSED

**Status: RUN + PASSED 2026-08-22 (Step 5).** Executed against the deployed `data/prediction_markets.db`
via a from-scratch predicate (NOT importing `ingest.py`), driver `runners/pm_net_verify_sdtrading.py`.

## ✅ RESULT (2026-08-22, Step 5 + re-sync re-check)
- **First pass (Step 5 snapshot):** DB n_resolved=468 vs INDEP=469; net delta −$10,080.74; cost delta
  −$10,607.15; **roi matched to 4 dp (cost 0.9018 vs 0.9019, notional 0.4583 vs 0.4584)**; n_excluded both 0.
  The delta was exactly ONE resolved row — diagnosed as live-whale timing (SDTrading is a LIVE MLB whale;
  one position resolved between the Step-4 backfill snapshot [511 rows] and the Step-5 verify pull [512 rows]).
- **Re-check (re-backfill SDTrading → re-verify same snapshot, `pk_pm_sdt_reverify.ps1`):** after syncing the
  DB to the current snapshot (512 rows / 469 mlb, verdict complete, suspect 0):

  ```
  n_resolved   DB=469 INDEP=469
  net_realized DB=4202330.6183 INDEP=4202330.6183 delta=-0.0000
  cost_basis   DB=4659502.3177 INDEP=4659502.3177 delta=-0.0000
  VERDICT net_match=True cost_match=True nres_match=True
  ```

  **The independent from-scratch sum equals the DB to the cent** on net_realized AND cost_basis AND row count.
  This proves parse → ingest → store → rollup arithmetic and the §3A predicate wiring. The 1-row Step-5
  delta was timing, not logic. **§12 net-verify PASS.**
- **Net-loser check (§12 "a net-loser shows negative ROI"):** confirmed independently in the scoreboard —
  `0x71edffd0d70a` unknown: roiC −5.3% / roiN −4.8% / net −$161,357 (negative ROI on a negative net). PASS.
- **Clause-(b) exclusions on SDTrading = 0** (as predicted under the demoted invariant); the 6 `pnl_anomaly`
  rows are FLAGGED, present, and scoreable (not excluded) — confirmed by suspect=0 / anomaly=6 on backfill.

---

**Original target + method (committed pre-run, retained for provenance):**

**Status when written: NOT run** (needs ingested data; executes at Step-3/Step-4). This doc commits the target + method.
**Superseded pick:** the earlier version made Kickstand7 (Fed) the primary net-verify — WRONG per §12
(which requires a BINARY-market whale, non-suspect rows) and doubly wrong now that live data shows the Fed
quarantine fires (`QUARANTINE_RECONCILE_2026-08-22.md`). Corrected below.

## PRIMARY §12 net-verify target = SDTrading (MLB) — Jack's ruling
- Wallet: **`0x16bb9951a36fce71e2ef57890b786145e0ba8492`**, name **SDTrading**, live-loop MLB whale
  (`poly_kalshi_mlb/live_whales`), a genuine 1-of-12 roster member.
- Category: **mlb** — BINARY single-game moneylines (two-outcome), the §12-required market type.
- Measured (read-only reconciliation 2026-08-22): 505 closed positions, mlb=462, **7 suspect total (5 mlb)**.

## ★ SDTrading is CLEAN again under the demoted invariant (Task 4 confirm) — but the ROI denominator is wrong
With clause (a) DEMOTED to a flag (Task 2 ruling), SDTrading's 5 former "exclusions" are no longer
excluded — they are `pnl_anomaly`-flagged but SCOREABLE. Its clause-(b) exclusions are **0**. So SDTrading
is the clean-baseline binary whale §12 wants. **SDTrading REMAINS the primary net-verify target.**
BUT the Task-1 finding (`ROI_DENOMINATOR_FINDING_2026-08-22.md`) means the net-verify must ALSO check the
denominator: `/closed-positions total_bought` is the NOTIONAL (shares), not cost; real cost =
`total_bought * avg_price` = `/activity` BUY (proven to the dollar). So `roi = realized/total_bought` is
return-on-notional. The method below verifies the NET (realized) reconciles AND documents the notional-vs-cost
ROI gap, so the net-verify does not bless an on-notional ROI as if it were on-cost.

## Method (from-scratch reimplementation — a real cross-check, not importing ingest.py)
1. Read-only pull SDTrading's raw `/closed-positions` (all pages), independently.
2. Reimplement the §3A predicate FROM SCRATCH (EPS = max($1, 1%*total_bought); clause (a) loss-exceeds-cost;
   clause (b) zero-cost/nonzero; event-group propagation by `event_slug`). Do NOT import `ingest.py`.
3. **Three reconciliations (all must be stated):**
   - **(A) FULL net** — independent sum of `realized_pnl` over ALL mlb rows == DB naive-all sum. Proves the
     parse -> ingest -> store arithmetic with NO quarantine confounding. THIS is the true whale performance.
   - **(B) SCOREABLE net** — independent sum over pnl_suspect=0 mlb rows == DB `net_realized_pnl`; independent
     n_excluded/excluded_pnl == DB. Proves the §3A predicate + rollup wiring.
   - **(C) THE GAP = (A) - (B)** — under the demoted invariant this should now be ~0 for SDTrading (clause-(b)
     exclusions = 0; the former clause-(a) rows are flagged but scoreable). Confirm gap==0 and that the
     `pnl_anomaly` rows are present-but-not-excluded.
   - **(D) ROI DENOMINATOR** — independently compute `cost = SUM(total_bought * avg_price)` and confirm it
     matches `SUM(/activity BUY)` (proving `total_bought` is notional); report BOTH `roi_on_notional =
     net/total_bought` (current) and `roi_on_cost = net/cost` (proposed). Do NOT bless the on-notional ROI.
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
