# ★ EMERGENCY STOP — Prediction Markets live trading (operator card)

**When something is wrong and you need live orders to STOP. Keep this reachable. Run from the repo root on the box.**

---

## THE COMMAND (fastest — works even if pm_web is down; it is a standalone script hitting the persisted state)

- **MASTER KILL — all accounts + categories at once (use this if unsure):**
  ```
  PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global
  ```
- **One sub-division:**
  ```
  PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --account kalshi_jack --category mlb
  ```

## HOW FAST
Persisted the instant the command returns (sub-second, autocommit). The driver RE-READS the kill before EVERY
order, so the next order is blocked **at the next order boundary — within ~7 s** (`poll_sec`) worst case. It does
not wait for the current cycle to finish.

## WHAT SURVIVES (the irreducible one-order window)
One order ALREADY POSTED to Kalshi at the instant the kill lands is **NOT recalled** — at most a single in-flight
order. Everything after it is blocked. This is the cost of an asynchronous kill against a synchronous POST; it is
not a bug. To flatten a position already open, **close it BY HAND on Kalshi** — the disarm blocks the engine's own
exit too (off is off).

## SURVIVES A RESTART
Yes. The kill is persisted; a restarted engine comes up DISARMED (the fail-safe default) and does **not** resume.
Re-arming is an explicit human act (`pm_cli live-arm`).

## AUTOMATIC KILLS (no human needed)
The driver self-disarms + LATCHES on: a **401/403 auth failure** (disarms the ACCOUNT + flags open positions for
manual exit), **≥3 consecutive order errors**, the **orders/day ceiling**, or a **boot-reconcile mismatch**. A
latched kill STAYS off until `pm_cli live-arm --clear-latch` — you must SEE the trigger before re-arming.

## IF THE CLI CANNOT WRITE (box DB locked/broken)
Stop the whole engine (needs az-root):
```
systemctl stop trading-corp
```
This halts ALL divisions. The persisted-state fail-safe means an unreadable arm row already reads as DISARMED.

## VERIFY IT STOPPED
```
PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-status --account kalshi_jack --category mlb
```
→ `effective_armed: false`. (Read-only; works pm_web-down.) And `pm_subdivision_order` stops gaining `dry_run=0` rows.

---
*Full detail: `R7_PLAN_2026-08-29.md` §13 (procedure) + §14 (the kill-switch proof). Built + proven at R7.d,
2026-08-29 — NOT yet armed; no live order has ever been placed.*
