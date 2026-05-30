# Paper-trade visualizer for TradingView

Display-only review tool. Renders historical bitunix_futures
`paper_trade_record` rows onto a TradingView chart as entry / SL / TP
horizontal lines, outcome-coloured boxes, and entry/exit labels so the
operator can scan for patterns where the model performs well vs poorly.

Touches nothing in the live engine. The Python exporter is read-only
against `data/trading_corp.db`; the Pine script is a static-array
indicator with no broker side.

## Files

- `paper_trade_visualizer.pine` — the Pine v6 indicator script. Copy
  this into TradingView's Pine editor.
- `paper_trades_pine.txt` — generated paste block (re-generated each
  time the exporter is run). The shipped copy in git is a sample from
  the last 7 days of prod data (14 bitunix_futures trades, 11W/3L).
- `../export_paper_trades_to_pinescript.py` — Python exporter that
  writes `paper_trades_pine.txt` from the local SQLite DB.

## Quickstart

1. **Regenerate the paste block** from a recent window (default 30
   days):

   ```powershell
   .\scripts\run_capped.ps1 python scripts\export_paper_trades_to_pinescript.py `
       --db data\trading_corp.db `
       --since 30d `
       --division bitunix_futures `
       --out scripts\pinescript\paper_trades_pine.txt
   ```

   Other windows: `--since 7d`, `--since 24h`, or an ISO timestamp
   like `--since 2026-05-01T00:00:00+00:00`. `--until` accepts ISO
   (defaults to now).

2. **Copy the paste block** into the Pine script. Open
   `paper_trade_visualizer.pine` and replace everything between

   ```pine
   // === BEGIN GENERATED PASTE BLOCK ===
   …
   // === END GENERATED PASTE BLOCK ===
   ```

   with the contents of `paper_trades_pine.txt` (which includes its
   own BEGIN/END marker lines).

3. **Load in TradingView**:
   - Open `BTCUSDT.P` on Bitunix (or any exchange that publishes the
     pair); the visualizer is intended for the 3m timeframe but works
     on any.
   - Pine Editor → New blank indicator → paste the full script →
     Save → Add to chart.
   - The drawings render once on the first bar visible in the chart's
     loaded history. Scroll/zoom to the trade time window to see them.

## Inputs (TradingView indicator settings panel)

| Input | Default | Notes |
|---|---|---|
| Show last N trades | 50 | Max 100 with TP lines on (Pine `max_lines_count`=500 ÷ 5 lines/trade). Disable TP lines to view ~250. |
| Show entry labels (order_id + side + validators) | true | order_id (8 chars), ▲ (buy) / ▼ (sell), validator-pair tag. |
| Show exit labels (R-multiple + $PnL) | true | Signed R-multiple, signed $PnL. Hidden for still-open trades. |
| Show TP1/TP2/TP3 lines | true | Off cuts draw cost from 5 to 2 lines/trade. |

## Validator-pair tag legend

Generated from `audit_event` rows of kind `pa_validation_decision`,
matched to each trade via the 600s-pre-entry / same-`trigger_signal`
recipe documented in
`reports/2026-05-30_paper_trade_visualizer_phase1_diagnostic.md`.

| Validator name (DB)    | Short tag |
|------------------------|-----------|
| `vwap_alignment`       | `V`       |
| `volume_confirmation`  | `VOL`     |
| `structure_alignment`  | `S`       |

Format: `passed/-failed`, joined with `+`. Examples:

| Tag           | Meaning                                       |
|---------------|-----------------------------------------------|
| `V+VOL+S`     | all three passed                              |
| `V+VOL/-S`    | VWAP + volume passed, structure failed        |
| `-VOL`        | none passed, volume explicitly failed         |
| (trigger name)| no `pa_validation_decision` row in the 600s window — fallback shows the raw `trigger_signal` (e.g. `mc_a_redx`) |

## Sentinels (Pine encodes "open"/"missing" as 0)

| Field                | Sentinel value | Pine handling                                       |
|----------------------|----------------|-----------------------------------------------------|
| `g_result_ts[i]`     | `0`            | open trade → extend lines/box to `timenow`          |
| `g_result_price[i]`  | `0.0`          | falls back to entry price for the outcome box       |
| `g_tp1/tp2/tp3[i]`   | `0.0`          | TP leg not present (pre-v2 single-leg row) → line not drawn |
| `g_result[i]`        | `"open"`       | exit label suppressed                                |

## Pine version

- Primary target: **Pine v6** (current default in TradingView).
- v5 fallback: change `//@version=6` → `//@version=5`. Then:
  - `array.new<int>()` → `array.new_int()` (same for `float`, `string`)
  - `var array<int> g_entry_ts = array.from(...)` → `var int[] g_entry_ts = array.from(...)`
  - `array.from()` exists in v5; signature compatible.
  - Everything else (line.new/box.new/label.new/inputs/colors/loop) is
    identical between v5 and v6 for what this script uses.
  - `timenow` is available in both.

## Adding a new division

The exporter accepts `--division <name>`. It assumes the same column
layout as `paper_trade_record` and the same `extra_json` shape (flat
`tp1/tp2/tp3_price` keys or a `tp_plan[]` list, plus a `trigger_signal`
key). Other divisions can be plotted with the same Pine script — no
script change required.

## Regeneration cadence

There is no auto-refresh. Re-run the exporter and re-paste whenever
you want fresh trades on the chart. The script does not poll the DB
from Pine (TradingView sandbox doesn't permit it); the static-array
approach is the entire reason this tool is feasible at all.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Nothing renders on chart | The trade timestamps are outside the chart's loaded history window. Scroll back to the trade dates, or reduce `Show last N`. |
| Pine error "Variable already declared" | The paste block was appended instead of replacing the marker-delimited region. Re-paste, replacing the entire BEGIN→END block. |
| Pine error "Too many lines" | `Show last N` × 5 > 500 with TP lines on; turn TPs off or lower N. |
| Validator-pair tag shows `mc_a_redx` etc. instead of `V+VOL+S` | No `pa_validation_decision` row in the 600s window before entry — the exporter falls back to `trigger_signal`. This is expected behaviour, not a bug. |
| Exporter prints `Wrote 0 trades` | Window is empty OR `data/trading_corp.db` isn't synced from prod. Pull a fresher DB, or widen `--since`. |
