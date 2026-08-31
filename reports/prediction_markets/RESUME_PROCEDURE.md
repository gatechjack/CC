# ★ RESUME — Prediction Markets live trading after a latch (operator card)

**How to get BACK to trading after the system has latched (or you disarmed). Run from the repo root on the box.
The mirror of `STOP_PROCEDURE.md`.**

> **WHERE YOU ARE RIGHT NOW (2026-08-30):** the `kalshi_jack:mlb` sub is **latched on `count_ceiling`** ("orders/day
> ceiling 1>=1") after the platform's first-ever order filled (id=1). Global is armed; the sub is `armed=false
> latched=true`; `effective_armed=false`; `max_orders_per_day=1`. This is the DESIGNED "place one, inspect" stop.

---

## THE SEQUENCE (order matters — raise the cap BEFORE you clear the latch)

Clearing the latch while `max_orders_per_day` is still `1` and an order already filled today re-latches on the very
next order (gate 8 fires again the same UTC day). So **raise the cap first, then clear-latch + arm.**

### 0. FIRST — confirm you know the position (do not resume on an unverified state)
The whole point of the ceiling was to stop and look. Confirm the reconcile is clean and the open position is
accounted for (R7.g: journal == venue, `position_fp` sign understood) before you re-arm. If in doubt, stay latched.
```
PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-status --account kalshi_jack --category mlb
```

### 1. RAISE `max_orders_per_day` (a config write — there is NO CLI for caps, by design)
Back the money DB up first, then set the new daily cap on the sub-division. `<N>` = the orders/day you actually
want (start small).
```
cp data/prediction_markets.db ~/pm_caps_backup_$(date -u +%Y%m%dT%H%M%SZ).db
PYTHONPATH=. venv/bin/python - <<'PY'
import sqlite3, time
from trading_corp.prediction_markets import db
N = 5                     # <-- the new orders/day cap; edit this
c = sqlite3.connect(db.pm_db_path())
c.execute("UPDATE pm_subdivision SET max_orders_per_day=?, updated_ts=? WHERE account_id='kalshi_jack' AND category='mlb'",
          (N, int(time.time())))
c.commit()
print("max_orders_per_day ->", c.execute("SELECT max_orders_per_day FROM pm_subdivision WHERE account_id='kalshi_jack' AND category='mlb'").fetchone()[0])
PY
```
(The other caps — `per_order_usd_cap` / `daily_usd_cap` / `max_open_usd` / `fixed_stake_usd` — are raised the same
way, same row. They are read PER CYCLE, so no restart is needed for a cap change to take effect.)

### 2. CLEAR THE LATCH + RE-ARM the sub (the ONE place a latch is ever cleared — it forces a human ack)
```
PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-arm --account kalshi_jack --category mlb --clear-latch --by <you>
```
A plain `live-arm` (without `--clear-latch`) REFUSES a latched scope — you must acknowledge the trigger.

### 3. GLOBAL must also be armed (it is now; arm it if you ever master-killed)
```
PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-arm --global --by <you>
```

### 4. VERIFY
```
PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-status --account kalshi_jack --category mlb
```
→ `effective_armed: true`, `latched: false`, `auto_trigger: null`. The driver picks up the new cap + arm state
within one poll (~7 s); no restart needed.

---

## NOTES
- **A restart does NOT resume you.** A restarted engine comes up DISARMED (fail-safe), and a latched sub comes back
  **latched**, not merely disarmed — you still do steps 1–3. (Boot-reconcile must also come up CLEAN; a mismatch
  re-latches `boot_reconcile_mismatch` and you investigate before arming.)
- **Which latch are you clearing?** `live-status` shows `auto_trigger`: `count_ceiling` (orders/day — raise the cap),
  `consecutive_order_errors` (≥3 loud rejects — find the cause), `boot_reconcile_mismatch` (journal≠venue — reconcile
  by hand), `auth_failure` (401/403 — fix creds; open positions were flagged for MANUAL exit). Clear only after you
  understand the trigger.
- **To STOP again:** `STOP_PROCEDURE.md` (`live-disarm --global`).

---
*Sequence proven at R7.i, 2026-08-31 (`test_disarm_r7i.py`): re-arm refuses a `count_ceiling` latch without
`--clear-latch`; the latch survives a restart; disarm blocks the exit of an open position (hand-flatten on Kalshi).*
