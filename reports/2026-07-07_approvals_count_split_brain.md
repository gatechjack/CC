# Approvals "59 pending but blank screen" — root cause

**Date:** 2026-07-07
**Branch:** dashboard-tile-reorg-2026-07-07 (off origin/main 1d2a714)
**Status:** Diagnosis complete (code-level, definitive). Prod-composition query optional.

## Symptom
The "Pending Approvals" stat card on the Overview shows **59**, but clicking it (or
the HITL tile) opens `/approvals` with a **blank list — nothing to approve**.

## Root cause: count/list split-brain
The count and the list read **two independent sources of truth**:

| Surface | Source | Value |
|---|---|---|
| "Pending Approvals" stat card | DB query `_query_pending_approvals` | **59** |
| Telegram `/pending` | same DB query | 59 |
| `/approvals` page list | in-process `deps.pending_registry.list_pending()` | **empty** |
| HITL tile "pending" | `deps.pending_registry.pending_count()` | **0** |

### The DB count (59)
`web/data.py:1153` —
```sql
SELECT id, ts, strategy, symbol, side, qty, rationale
FROM proposed_order
WHERE status='risk_approved'
ORDER BY ts DESC
```
No time bound, no resolution filter → counts **every all-time `proposed_order` row
still sitting at `status='risk_approved'`**. `build_command_center` sets
`pending_approvals=len(pending)` (`data.py:787`).

### The in-memory registry (0 / blank)
`/approvals` (`routes.py:1640`) renders `deps.pending_registry.list_pending()`; the
HITL tile calls `pending_registry.pending_count()` (`data.py:1653`). The registry is
constructed **empty** at `main.py:313` and is **never rehydrated from the DB** (the
only `rehydrate` in the tree is an unrelated polymarket audit cache). So after every
process start it holds only approvals registered *by the current process's live HITL
interrupt flow*.

### Why the 59 rows are stuck
Orders reach `status='risk_approved'` in the graph (`ceo_graph.py:361`). The status
only advances to a terminal state (`board_approved`/`board_rejected`/`filled`/
`cancelled` — schema `db.py:59`) when a board decision is **recorded on that row**.
Any path that places/decides without writing that status back — auto-execute market
orders, paper mode, divisions that don't use the web HITL registry — plus any row
from a prior process lifetime, leaves the DB row at `risk_approved` permanently.
Nothing sweeps them. They accumulate → 59.

**The blank `/approvals` screen is actually correct** (nothing is live-pending). The
misleading artifact is the **59 count**.

## Implication for the HITL ↔ Pending-Approvals tile merge
The merged tile should headline the **registry** `pending_count()` (the actually-
actionable count that matches `/approvals`), NOT the DB `risk_approved` count. Doing
so resolves the "59 but blank" contradiction for the tile for free.

## Fix options (the DB `59` itself)
- **A — display-honest (minimal):** point the count at the registry so the tile/card
  match `/approvals`. Leaves 59 stale rows in the DB (harmless; Telegram `/pending`
  still shows them until B).
- **B — DB hygiene:** one-time sweep advancing the 59 stuck `risk_approved` rows to a
  terminal status (e.g. `expired`/`cancelled`) + fix the lifecycle so future rows
  don't stick. Touches prod data. Makes DB and registry agree at ~0.
- **C — rehydrate registry from DB on startup:** makes the 59 clickable. Almost
  certainly **wrong** — these are stale, not actionable; would resurface 59 old
  orders as live "pending" clicks.

**Recommendation:** A for the tile now (as part of the merge); B as a separate small
cleanup after confirming the 59's composition.

## Optional confirmation (read-only, prod)
To see exactly what the 59 are (which strategies / how old):
```sql
SELECT strategy, COUNT(*) n, MIN(ts) oldest, MAX(ts) newest
FROM proposed_order WHERE status='risk_approved' GROUP BY strategy ORDER BY n DESC;
```
Run against the prod DB (NOPASSWD sqlite3 for trading-corp is available).
