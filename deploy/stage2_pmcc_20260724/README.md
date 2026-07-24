# Stage-2 PMCC sign-flip fix — prod-write artifacts (PREPARED, NOT RUN)

These two SQL scripts are **prepared for operator-authorized Stage 2 only**. Nothing
here has been run against prod. They accompany the Build-A code commits on branch
`pmcc-phase-a-atomic-rollshort-2026-07-22` (items 1/2/4).

The **code** fix (item 1, `brokers/robinhood.py`) stops any NEW combo fill from being
mis-attributed. These scripts repair the two rows that were **already** written wrong
on 2026-07-24 (the first-ever real combo fills), plus long-standing zombie board rows.

| Script | What it does | Blast radius |
|---|---|---|
| `01_fix_signflip_fills.sql` | Sets `proposed_order.fill_price` on the 2 swapped LIVE pairs to the broker-authoritative fills (OPEN 5C@0.03/4C@0.29 → +$26; RKLB 74C@0.03/75C@1.20 → +$117). | Exactly 4 rows (2 pairs). Confirmed: all other filled combo legs are `execution_mode='paper'` (never swapped). |
| `02_void_zombies.sql` | Voids 4 stale `board_approved` zombie rows → `board_rejected` (3×ASTS, 1×CIFR, ≥16d old, no combo_id). | 4 rows. |

**Scope guardrails (per operator, 2026-07-24):**
- `01` corrects ONLY `proposed_order.fill_price` — the field that feeds `_query_prior_rolls`
  → the approval-card "prior netted" line AND the LLM ROLL HISTORY prompt block. It does
  **NOT** rewrite the `position` book (PMCC re-derives positions live) and does **NOT**
  rewrite the immutable `audit_event` log (its `combo_filled.net_actual` stays −0.26/−1.17;
  that is telemetry-only, read only by `ic_telemetry`, and audit logs are not rewritten).
- Both scripts are **idempotent** (absolute sets / `AND status=...` guards) and print
  BEFORE/AFTER + a verification query.

**Run (Stage 2 only):**
```
sqlite3 /home/azureuser/trading_corp/data/trading_corp.db < 01_fix_signflip_fills.sql
sqlite3 /home/azureuser/trading_corp/data/trading_corp.db < 02_void_zombies.sql
```
No engine restart is needed (both are read fresh each scan). WAL mode → each script takes
a brief write lock; run in a quiet window. Take a `.bak` of `trading_corp.db` first per the
standard Gate-A → .bak → apply → verify flow.
