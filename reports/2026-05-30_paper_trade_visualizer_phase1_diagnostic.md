# Pine Script paper-trade visualizer — Phase 1 diagnostic

Date: 2026-05-30
Branch: `paper-trade-visualizer-2026-05-30`
Scope: read-only schema + prod-sample verification before building exporter (Phase 2) and Pine script (Phase 3).

## 1. `paper_trade_record` schema (confirmed)

Source: `trading_corp/persistence/db.py:118-144` (DDL), `trading_corp/persistence/models.py:263-289` (writer).

Columns the visualizer needs and where each lives:

| Visualizer field      | Actual location                                         |
|-----------------------|---------------------------------------------------------|
| `order_id`            | top-level `order_id` (TEXT PK)                          |
| `entry_ts`            | top-level `ts` (no separate `entry_ts` column)          |
| `side`                | top-level `side` — values `'buy'` / `'sell'`            |
| `entry_price`         | top-level `entry_reference_price`                       |
| `sl_price`            | top-level `stop_price`                                  |
| `tp1_price`           | `extra_json.tp1_price` (flat) and `extra_json.tp_plan[leg=tp1].price` |
| `tp2_price`           | `extra_json.tp2_price` (flat) and `extra_json.tp_plan[leg=tp2].price` |
| `tp3_price`           | `extra_json.tp3_price` (flat) and `extra_json.tp_plan[leg=tp3].price`; also `tp_price` top-level (back-compat alias) |
| `result`              | top-level `result` — `'win'`/`'loss'`/`'expired'`/NULL (=open) |
| `result_ts`           | top-level `result_ts`                                   |
| `result_price`        | top-level `result_price`                                |
| `actual_r_multiple`   | top-level `actual_r_multiple`                           |
| `actual_pnl_dollars`  | top-level `actual_pnl_dollars`                          |
| division filter       | `WHERE division = 'bitunix_futures'`                    |

Note: `tp_plan_version = "v2"` on all sampled rows. Pre-v2 rows would only have the top-level `tp_price` populated.

## 2. Prod sample (5 rows, last 30 days)

Queried via `az vm run-command` on `tc-prod-vm` in `rg-shared-prod`. All 5 sampled rows are sells (BTCUSDT.P), 3 wins / 2 losses, all from 2026-05-28 → 2026-05-29.

Summary across the full window:
- 87 settled bitunix_futures rows (59 win / 25 loss / 3 expired / 0 open)
- All have populated `entry_reference_price`, `stop_price`, `tp_plan` (3 legs), `result_price`, `actual_r_multiple`, `actual_pnl_dollars`.

Example full `extra_json.tp_plan` (order `218b1aae...`):

```json
[
 {"leg":"tp1","fraction":0.25,"target_r":0.968,"price":73461.0317,"stop_action":"move_to_breakeven"},
 {"leg":"tp2","fraction":0.5, "target_r":1.0,  "price":73456.60325,"stop_action":"move_to_tp1"},
 {"leg":"tp3","fraction":0.25,"target_r":2.5,  "price":73251.258125,"stop_action":"trail_atr"}
]
```

## 3. Validator-pair join — anomaly + working recipe

**Anomaly: the original spec's join recipe doesn't work.**

The task brief said "PA validator results live in audit_event rows of kind `pa_validation_decision`, joined by `order_id`." Two problems:

1. `pa_validation_decision` payload has NO `order_id` field. Its payload keys are
   `decision, division, failed, mode, passed, reason, rush_fall_triggered, score_side, score_tier, strategy, trigger_signal, trigger_source`. The validator pass/fail lists ARE here (`passed=['vwap_alignment','volume_confirmation']`, `failed=['structure_alignment']`), but they cannot be joined by `order_id`.

2. The companion event `pa_validation_redeem` DOES carry `order_id`, but covers only **6/87 (7%)** of bitunix paper trades in the current corpus, and on the 3 with redeem rows the indirect join `redeem.original_cached_at + trigger_signal → decision` returned 0 matches.

**Working join (10/10 coverage in probe — adopted for Phase 2):**

```sql
SELECT passed, failed
FROM audit_event ae
WHERE ae.kind = 'pa_validation_decision'
  AND json_extract(ae.payload_json, '$.trigger_signal') = ?  -- from paper_trade extra_json
  AND ae.ts <= ?                                             -- paper_trade_record.ts
  AND ae.ts >= datetime(?, '-600 seconds')
ORDER BY ae.ts DESC
LIMIT 1
```

Validator-name → short tag:

| pa_validation_decision name | short tag |
|-----------------------------|-----------|
| `vwap_alignment`            | `V`       |
| `volume_confirmation`       | `VOL`     |
| `structure_alignment`       | `S`       |

Cluster label format: `passed/-failed` joined with `+`, e.g. `V+VOL/-S`, `V+VOL+S` (all-pass), `V/-VOL-S`.

If no decision row found in the 600s window, the exporter falls back to `trigger_signal` alone as the cluster label (e.g. `mc_a_red_diamond`). This is 100% available because `trigger_signal` is in `extra_json` directly.

## 4. Pine Script version target

Pine v6 is current per TradingView's published docs. The Phase 3 script will target v6 (`//@version=6`) with a README note that v5 users need to:

- replace `//@version=6` with `//@version=5`
- swap any v6-only syntax (e.g. `array.from(...)` works in both; type params like `array<int>` also work in both — minimal change expected).

The exact v6→v5 deltas will be enumerated in `scripts/pinescript/README.md` after the script is written.

## 5. Fork resolution log

| Question | Answer |
|----------|--------|
| Which column is entry_price? | `entry_reference_price` (not `entry_price`) |
| Where do TP1/2/3 live? | `extra_json.tp_plan[]` + flat `extra_json.tpN_price` mirror; prefer flat reads |
| How do we join validator-pair? | `(trigger_signal, ts ≤ pt.ts, ts ≥ pt.ts − 600s)` against `pa_validation_decision` |
| What's the fallback when no decision row matches? | use `trigger_signal` alone |
| Pine version? | v6 primary, v5 noted in README |

No deploy. No prod changes. Read-only investigation only.
