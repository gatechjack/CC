# Bitunix live-placement REGRESSION — paper-wrapped since the 01:22 cutover — 2026-06-17

**Read-only investigation.** Symptom: bitunix fires "(live, monitor-mode)" Telegram alerts (qty/fill/order-id)
but places ZERO real orders on BitUnix since the polymarket cutover restart (01:22 UTC).

## VERDICT: bitunix is NOT placing live — it is PAPER-SIMULATING (Y)
Two separate "monitor" concepts; only the second is the cause:
1. **"(live, monitor-mode)" tag = HITL label, RED HERRING.** `is_monitor_mode = current_count >= HITL_FIRST_N_LIVE_ORDERS` (observer.py:2997). HITL was removed (=0) → `is_monitor_mode` is ALWAYS True → the tag is the normal label for every live-path order. It does NOT mean "not placed."
2. **THE REAL CAUSE — paper-wrapped broker from `--live-divisions`.** The bitunix division got a `PaperExecutionBroker`, so `place_order` SIMULATES.

## Mechanism (main.py, prod == local a64a42f == f16e9c24)
- **Slug gate** (main.py:1996-1998): `is_live_division = family_live_capable AND division.slug in (live_divisions or set())`. Comment 1994: *"Empty/absent live_divisions ⇒ nothing arms live."*
- **Factory** (main.py:2068-2071): bitunix branch —
  ```python
  bx = BitunixBroker(...)            # real adapter (reads balance/positions)
  if is_live_division: return bx     # LIVE: places for real
  paper = PaperBroker(account=f"paper_{division.slug}", ...)
  return PaperExecutionBroker(bx, paper)   # NOT selected: balance renders, orders SIMULATE
  ```
  Comment 2058-2060 states it: *"In PAPER mode we wrap with PaperExecutionBroker so the real BitUnix balance/positions render on the dashboard while any orders simulate via PaperBroker."* (This is why `BitunixBroker connected (equity=$265.53)` still logs — the real `bx` reads the balance; only placement is simulated.)
- The observer's `execution_mode` (from `strategies.yaml` bitunix `execution_mode: live`) is INDEPENDENT of `--live-divisions` → it still takes the live path (logs `live_order_placed`, fires "(live)" telegrams) even though its broker is paper. **Two decoupled gates; the cutover flipped only the broker one.**

## Evidence (prod, read-only)
- Live ExecStart: `--live-divisions polymarket_copy_trading` — **`bitunix_futures` NOT listed.**
- Order `44aea431` `filled` audit: **`"venue":"paper"`, `fill_price:100.0`** (synthetic) — simulated, not a real venue fill.
- All **6 post-cutover** bitunix `live_order_placed` entries → simulated (venue=paper). Exit UUIDs are PaperBroker sim ids.
- `PaperExecutionBroker.paper = True` (paper.py:143); its `place_order` (paper.py:194) simulates.
- Division slug = **`bitunix_futures`** (config/divisions.yaml:141).

## Did the cutover cause it? YES. When? At the 01:22 cutover restart.
- Pre-cutover prod `main.py` = `f733e374` (bitunix-only, **no E2.4 slug-gate code**) → bitunix armed live by the old `execution_mode=live` mechanism (real fills 06-14 first-fill, 06-15 trade3 stop-out).
- The 01:22 cutover deployed `main.py = f16e9c24` (**superset WITH the E2.4 slug gate**) AND set ExecStart `--live-divisions polymarket_copy_trading`. Together: the gate became active AND bitunix's slug was excluded → bitunix dropped to the `PaperExecutionBroker`. Every bitunix order since is venue=paper.

## FIX (scope only — NOT applied; operator decision)
Re-arm bitunix for live placement by adding its slug to `--live-divisions`:
```
--live-divisions polymarket_copy_trading bitunix_futures
```
(`nargs="*"`; space- or comma-separated both parse — `_parse_live_divisions`, main.py:160. `--live-divisions` is the last ExecStart token, so appending is unambiguous.) `family_live_capable` already holds (`--brokers bitunix` present, mode LIVE).

**This is an ExecStart / systemd-unit change** (`/etc/systemd/system/trading-corp.service`) → `systemctl daemon-reload` + restart → **OPERATOR sudo** (agent never sudo). **NOT** a config-file change.

## ⚠ CRITICAL interaction with the pending bracket deploy + validation
The bracket validation requires bitunix to place a REAL live order. **Currently it cannot** (paper). If the bracket deploy is restarted with the current ExecStart, bitunix stays paper → the bracket runs in sim → the "first-ever live TP" validation CANNOT happen. ⇒ **The ExecStart re-arm (`+bitunix_futures`) must be combined with the bracket deploy restart** (operator edits the unit ExecStart, then the one restart loads the bracket AND re-arms bitunix). Sequencing decision for the operator.
