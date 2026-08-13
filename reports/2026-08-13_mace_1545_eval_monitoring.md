# MACE 15:45 ET entry-eval monitoring — 2026-08-13

First scheduled eval after the overflow-dup / IVR-fetch / eval-logging fix
(prod-live `e113843`). Read-only observation; no prod mutation, no order action.

## Environment (verified read-only via SSH)
- Engine **PID 697735**, active, `NRestarts=0`, started **14:41:50 ET** (= the
  PMCC defect-resolution deploy; operator-confirmed, unrelated to MACE).
- `config_hash = fe177fcd3882` (unchanged by the 14:41 restart → MACE code/config intact).
- `/mace` HTTP 200 · execution_mode=live · standby:no · auto_execute:yes · enabled:yes.
- 4 MACE loops online (daily-slots, manage, reconcile, weekly-calendar).
- ExecStart carries `--live-divisions … robinhood_mace`.
- Deployed budget config (mace.yaml, bumped 2026-08-12): `weekly_new_rungs_per_symbol: 2`,
  `max_rungs_per_symbol: 5`, `max_contracts: 1`, `rung_risk_pct: 0.055`. Universe [SPY, GLD] both enabled.

## Timeline (ET)
- 15:40:15 — equity snapshot: **E = $3840.45** (cash $4140.49; open condor mark reduces settled-cash E).
- 15:47:18 — entry eval written (per-symbol `mace_entry_eval` rows for SPY + GLD).
- 15:47:18 — `mace_entry_start` SPY 746-743-807-810 x1.
- 15:49:42 — `mace_entry_fill` SPY credit **0.91**, attempts **2**, max_risk **$209**, order `6a7e1fd4`.
- 15:49:42 — `mace_pt_synthetic` pt_debit **0.46** (T9 synthetic, pt_order_id NULL).
- 15:50:12 — `mace_daily_summary`: equity 3840.45, open **2**, day_pnl 0, breakers [].

## Per-symbol decision
| Symbol | Decision | Reason | IVR (x100) | Credit | Max risk | Contracts |
|---|---|---|---|---|---|---|
| SPY | ENTERED → FILLED | passed all filters | 26.81 (>=25) | mid 0.95 -> filled 0.91 | $209 | 1 |
| GLD | SKIPPED | `no_wing` (wings unlisted at $3 width) | 28.33 | — | — | 0 |

GLD IVR (28.33) cleared the floor; its skip was purely the unconditional wing-listing gate.

## Four validations — ALL PASS
1. **No duplicate entry** — SPY entered once (1 rung, 1 order, `entered:1/placed:1`). GLD forfeited
   on `no_wing`; both eval rows `overflow:false`; no third rung, no `mace_entry_error`. The exact
   08-12 defect (overflow re-route onto the entered symbol) did NOT recur.
2. **Per-symbol logging** — `mace_entry_eval` rows for BOTH symbols with entered/skip_reason/ivr/
   credit_mid/max_risk. GLD's "why no GLD" now legible (no_wing).
3. **IVR gating active** — `mace_iv_history` populated real values: SPY 26.81 (atm 0.1472),
   GLD 28.33 (atm 0.2392), source tastytrade_market_metrics. ivr_status ok both. No `mace_ivr_outage`,
   no "asyncio.run() cannot be called from a running event loop".
4. **Entry mechanics** — 1 contract; credit 0.91 >= 0.90 floor (0.30x$3); ladder attempts 2 (<=5);
   fake-fill guard held (booked on confirmed fill); max_risk $209 in band [150,250]; T9 synthetic PT registered.

## Resulting open rungs: 2 (both SPY, both 2026-W33)
- 742/739/802/805 @ 0.93 (entered 08-12)
- 746/743/807/810 @ 0.91 (entered 08-13)

Within deployed weekly budget (2/wk) and capacity (5). 0 closures in W33. GLD 0 rungs.

## Other divisions / errors
- All wired healthy at the 14:41 boot: bitunix_sfp (live), PEAD (scan+manager+reconciler), MACE,
  PMCC (scan+approval-reconciler), Kalshi (arb/temporal/LLM/sports/copy).
- No PMCC/PEAD errors since boot. Engine-wide window scan clean (only benign pykalshi 404 not_found;
  pre-existing fidelity-playwright + polymarket-429 unchanged).
- PMCC defect-resolution deploy (14:41) caused no disruption.

## Telegram cross-check (operator)
Journal-truth 15:50 summary: **equity $3840.45 · open 2 · day P&L $0 · breakers none.** Plus a
`✅ MACE ENTRY SPY 2026-09-25 746/743P 807/810C x1 — credit $0.91` notification ~15:49:42.
Compare against the received Telegram; flag any mismatch.

## Observations (not problems)
- Eval wrote at 15:47:18 vs `eval_time_et 15:45` (~2 min); snapshot on-time 15:40:15. Likely off-loop
  IVR fetch + 2-symbol chain-build latency. Well inside the 15:58 cutoff (filled 15:49:42). Glance if it grows.
- Manage loop is a silent-no-op logger (0 lines across a full prior-boot 09:35–14:41 window); `/mace`
  mark shows `—` (in-memory only). No fault evidence; demonstrably worked 08-12 (fill + PT).
