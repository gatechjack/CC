# Next-session prompt — Polymarket copy-trading (post 2026-07-07 reassignment)

Paste the block below to start the next session.

---

Pick up the **Polymarket copy-trading** division. Last session (2026-07-07) we measured the edge, reassigned
the whale roster, deployed two supporting fixes, and reset the dashboard metrics. Full context:
`reports/2026-07-07_polymarket_copy_edge_analysis.md`, `runbooks/deploy_log.md` (2026-07-07 entry), BACKLOG.md
Priority 2 top block, and memory `polymarket-copy-edge-nogo-2026-07-07`.

**State (all verified live 2026-07-07):**
- Copy division is **PAPER** (`auto_execute:false`, `standby:true`, 0 live fills ever). No real money at risk.
- **New 15-whale roster** live in `agent_state(polymarket_copy_trader, selected_whales)` (7 kept winners + 8
  realized-edge adds; 11 losers removed). Hand-curated — do NOT blindly `refresh --algo-select` (the algo still
  ranks makers/losers high). Old roster backed up `/tmp/pm_roster_backup.json`.
- **option-c realized scorer** deployed (prod = c14e786) and **item-1 `copy_quote_price`** slippage logging
  deployed (prod = 2f92049a, commit d1a874f). Both live; engine PID 97179.
- **Dashboard metrics_epoch = `2026-07-07T20:00:54+00:00`** → all pm panels scoped to entries from then (fresh
  slate; 6,281 pre-epoch RTs preserved/hidden).
- ⚠ `main`/`origin/main` (f0c6224) do NOT have option-c or item-1 — they live only on branch
  `polymarket-copy-quote-price-2026-07-07` (pushed). Reconciling main↔prod is a separate task.

**THE TASK (the decisive test):** after enough forward paper trades have accumulated & resolved on the NEW
roster (give it a few days), **re-measure the forward copy edge** — the clean answer to "does a properly-selected
roster have a real copy edge?" Concretely (read-only prod via `runprod.ps1 <script.sh>`, Azure Run Command,
`sqlite3 -readonly /home/azureuser/trading_corp/data/trading_corp.db`):
1. Forward copy P&L: `polymarket_round_trips WHERE division='polymarket_copy_trading' AND entry_ts >=
   '2026-07-07T20:00:54'` → blended dollar ROI = SUM(realized_pnl)/SUM(notional), WR, n, per-whale, by-settle-vs-sell.
2. **Slippage (new, item-1):** for post-epoch `would_have_placed` rows, compare `whale_entry_price` vs
   `copy_quote_price` in the audit payload → quantify how much the whale edge erodes from entry lag. This is the
   number we could not measure before.
3. Verdict: if the new roster's forward settle-derived ROI is clearly positive net of slippage → the EU-egress /
   go-live question reopens (infra is ready: broker built, wallet funded 119.98 USDC.e, gate is just
   `auto_execute`). If flat/negative → copy-trading stays shelved or iterate roster/latency.

**Levers if edge is marginal:** tighten the 60s poll or move to the CLOB websocket (`wss://ws-subscriptions-clob.polymarket.com`)
to cut the 10–60s+ feed lag = the slippage source (not blockchain-limited). Consider deploying E2.3 flat-$1 sizing
(prod still on old $1/$2/$5 tier ladder).

Confirm the division is healthy first (engine up, still paper, roster intact), then propose the measurement plan.
