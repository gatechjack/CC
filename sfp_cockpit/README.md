# Bitunix SFP Cockpit — dashboard (v2)

Dark trader-cockpit for the live `bitunix_sfp` division, matching the attached v2 PDF.
The whole point is **data-readiness discipline**: every panel is tagged by whether real
data exists, and mock data is **unmistakable** (dashed amber border + corner ribbon) so a
placeholder can never be read as live truth.

## View it
Open **`sfp_cockpit_preview.html`** in a browser (self-contained; sample data baked in for
iterating the look). A copy is on `Desktop\bitunix_reports\sfp_cockpit\`.

## Files
| file | what |
|---|---|
| `sfp_cockpit_preview.html` | standalone design preview (open in a browser) |
| `trading_corp/web/sfp_cockpit_view.py` | FastAPI routes + queries + mock fns (the data-discipline core) |
| `trading_corp/web/static/sfp_cockpit.css` | the v2 theme (shared by shell + fragments) |
| `trading_corp/web/templates/sfp_cockpit.html` | shell (full page; HTMX includes the fragments) |
| `trading_corp/web/templates/sfp_cockpit/_header.html` | header: runtime badge + R summary |
| `…/_recon.html` | live-vs-backtest strip |
| `…/_state_board.html` | per-coin cards (R-journey, candles, chips) |
| `…/_mode_split.html` | REAL vs CONSIDERABLE |
| `…/_near_miss.html` | near-miss list (MOCK) |
| `…/_equity.html` | division cum-R sparkline |

## Wire-up (one line)
In `trading_corp/web/routes.py` `register(app)`:
```python
from trading_corp.web import sfp_cockpit_view
sfp_cockpit_view.register(app)
```
Then the cockpit is at **`/sfp`**. Each panel HTMX-polls its own fragment every ~5s
(`/sfp/partials/<panel>`); the only JS is the UTC clock + 15m-bar countdown — panel updates
ride HTMX swaps + CSS transitions (no animation loops), per spec.

## Data-readiness tiers (the contract)
**TIER A — real data NOW** (renders an *honest empty* state until SFP has rows; SFP just went
live with zero closed trades, so today these read `—`, never a fabricated number):
- R-journey (entry/stop from the open SFP row; target = entry+2R; current = latest
  `bitunix_bar_history` close, **symbol-keyed**; unrealized R = (cur−entry)/(entry−stop);
  MFE = running max((high−entry)/R) since entry; to-target/to-stop % chips; marker color
  shifts blue→green on the breakeven crossing; CSS-transition easing on the ~5s poll).
- Mode split (win% + avg-R grouped by `extra_json.sfp_mode`).
- Win@2R / Avg-R / Today / Week / Cum-R (closed SFP records).
- Per-coin 15m bar strips (`bitunix_bar_history`, symbol-keyed).
- Division equity = **cumulative R** sparkline from closed trades.

**TIER B — MOCK (dashed ribbon)** — no real source yet; **BLOCKED on an observer watch-state
emit** (`fired_bar_ts, mode, swept_level, swept_wick, bos_watch_level, status`). Served by
`_mock_*()` in the view; wire to real reads when the emit ships:
- SFP-armed watch overlay + countdown, Near-Miss panel, BOS-confirm rate, swept/BOS overlay lines.

**TIER C — DANGEROUS, scoped SFP-only** (this is the cross-division bleed that caused a false
alarm — never render shared-account / corp-wide data):
- Position hydration: every query is `WHERE division='bitunix_sfp'`. **No** fallback to the
  shared bitunix account snapshot or corp-wide events. No SFP position → an honest
  `no SFP position` state, never borrowed from another division.
- LIVE/PAPER badge: reads the **actual runtime** — the registered `bitunix_sfp` broker's
  `paper` flag AND the hot `auto_execute` switch — not a static label or record bookkeeping.
  States: `LIVE·REAL CAPITAL` / `PAPER` / `DISARMED (auto_execute=false)` / `NO BROKER`.

## New elements (catch the failures we hit)
- **TP-@-venue chip** — reads `extra_json.bracket_tp_order_id`: `✓ TP @ venue · id <n>` if
  present, **`✗ TP MISSING`** (loud red) if empty on an open position. Surfaces the exact
  blocker SFP just had (TP never placed → stop-out-only) instantly.
- **OCO/orphan chip** — `OCO ✓ · 1 stop + 1 tp` vs **`ORPHAN STOP`** (red, stop-only).
  Record-side early-warning; authoritative orphan detection stays the reconciler's signed-venue job.
- **SFP loop heartbeat** — `loop ~Nm (proxy)`. Currently a **proxy** (latest bar `inserted_at`
  age) and labeled as such; a true "loop last evaluated" needs an observer heartbeat emit
  (`agent_state` write per `process_once`).
- **PAPER/LIVE tag on every row** (coin cards + near-miss rows).

## SQL the routes run (all scoped to `division='bitunix_sfp'`)
- Closed metrics: `SELECT COUNT(*), SUM(result='win'), AVG(actual_r_multiple), SUM(actual_r_multiple) FROM paper_trade_record WHERE division='bitunix_sfp' AND result IN ('win','loss')` (+ `result_ts >=` day/week starts).
- Mode split: same, `GROUP BY json_extract(extra_json,'$.sfp_mode')`.
- Equity curve: `… WHERE division='bitunix_sfp' AND result IN ('win','loss') AND actual_r_multiple IS NOT NULL ORDER BY result_ts ASC` (cum-summed in Python).
- Open position (TIER C): `… WHERE division='bitunix_sfp' AND result IS NULL ORDER BY ts DESC LIMIT 1` — **no shared fallback**.
- Bar strip / latest close / MFE: `SELECT … FROM bitunix_bar_history WHERE symbol=? AND timeframe='15m' …` — **symbol-keyed** (relies on the 2026-06-26 symbol-key migration).

## Follow-ups to make Tier-B real
1. Observer watch-state emit (per the armed lifecycle): unblocks the armed-watch card, near-miss,
   BOS-confirm rate, swept/BOS overlay lines.
2. Observer loop-heartbeat emit (`agent_state`): turns the heartbeat proxy into a true tick age.
3. (Optional) division-scoped dollar equity accounting if a `$` figure is wanted — deliberately
   omitted now because the shared-broker snapshot is NOT division equity.
