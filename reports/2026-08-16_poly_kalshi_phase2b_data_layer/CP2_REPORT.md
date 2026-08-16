# Phase 2b CP2 report — mark-to-market poller + volatile cache (bounded history)

**Status: BUILT, NOT DEPLOYED. Checkpoint STOP — awaiting operator review before CP3.**
Branch `poly-kalshi-phase2b-cp2-2026-08-16` (off CP1); `main.py`/`db.py` matched `origin/prod-live`
at branch creation (tracing live code).

## Live-money / live-loop status (lead)
- **Zero live activity.** No order placed, no prod mutation, no restart. Branch-only; the running
  engine (PID 756639) is untouched — the poller starts only on a future operator-run deploy/restart.
- **Shared files byte-unchanged** — empty diff vs `origin/prod-live` on the 3 shared files.

## What CP2 delivers
A server-side **~60s mark poller** that computes live unrealized P&L on the open poly_kalshi
positions and caches it in two **volatile** tables the dashboard reads broker-free — mirroring the
`mace_rung_live` idiom + the kalshi equity-snapshot loop pattern. **Marks never touch `audit_event`.**

- **`db.py`** (+28): two idempotent (`CREATE TABLE IF NOT EXISTS`) volatile tables —
  `poly_kalshi_mark_live` (one row per open position, `INSERT OR REPLACE` on `order_id`:
  `yes_mid`/`unrealized`/`unrealized_pct`/`mark_ts`) and `poly_kalshi_mark_history` (bounded rolling
  yes-mid series per position → the price sparkline) + an index.
- **`trading_corp/agents/poly_kalshi_marks.py`** (NEW, 150): the poller —
  `_fetch_open_positions` (broker-free; the CP3 OPEN gate: placed ENTRY rows with a persisted
  `order_id`, not resolved), `run_mark_cycle` (prune closed → `broker.quote()` each open ticker →
  `unrealized=(yes_mid−fill_price)×fill_count` → write live+history+prune to cap 60),
  `_mark_loop` + `start_poly_kalshi_mark_loop`.
- **`main.py`** (+9): spawns `start_poly_kalshi_mark_loop(db_url, kalshi_broker_for_resolver,
  interval_sec=60)` alongside the kalshi equity loops (reuses the funded kalshi read-broker — quotes
  are public), with `else`-None + shutdown-cancel wired like the sibling tasks.

## Design points (surfaced)
- **Load:** N `quote()` per 60s (N = open positions, a handful) — negligible vs the resolver's
  ~350/hr burst (per the CP-plan scoping).
- **Quote miss** (0.0/error): leaves the prior row in place; staleness is judged off `mark_ts` — a
  miss never fabricates a value (mace principle).
- **Prune-on-resolve:** each cycle drops mark rows for positions no longer open, so the volatile
  tables track OPEN positions only.
- **Broker choice:** the funded `kalshi_arbitrage` read-broker (same one the resolver uses). Quotes
  are public market data, so any Kalshi read-broker returns the same yes-mid — no coupling to the
  live loop's own broker.

## Evidence
- **73 passed / 0 failed** (marks suite + executor + copy_trader + mlb_match + reconciliation),
  incl. 8 new mark tests: schema · open-position gate (resolved + pre-CP3 excluded) · unrealized math
  · bounded history (cap 60) · prune-on-resolve · quote-miss-leaves-prior-row · quote-exception-survived
  · **never-writes-audit_event**.
- `py_compile` OK on `main.py` / `poly_kalshi_marks.py` / `db.py`.
- Shared files byte-unchanged; changes confined to 4 files (+326/−0, all additive).

## Data-contract impact
`current_yes_mid` / `unrealized_pnl` / `unrealized_pct` / `mark_ts`+`stale` / `price_sparkline` move
from **NEEDS-BUILD(CP2)** to **AVAILABLE** in the volatile tables (once deployed + the poller ticks).
CP3 wires the broker-free dashboard read + join.

## NOT done (do not proceed without your go)
- **CP3** — broker-free `data.py` read joining open rows + trigger (CP1) + marks/sparkline (CP2) +
  a poly_kalshi live partial route + `hx-trigger="every 60s"` + copy-moment feed. Not started.
- Deploy — none. CP2 deploys with the Phase-2b set on an operator-run runner (drift-gate + patch of
  `db.py`/`main.py` + the new module; the `CREATE TABLE IF NOT EXISTS` migration runs at
  `init_db`/restart; the poller starts on that restart).

## Next
Your review. On go: CP3 (dashboard read + live refresh) in a fresh worktree.
