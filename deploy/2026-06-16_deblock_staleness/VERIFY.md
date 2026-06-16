# Post-restart verification — deblock + staleness bundle (4 layers)

Run after the operator restarts `trading-corp`. All commands read-only (agent SSH 82fda13).
Prior state to diff against: PID 2797287 (xvfb wrapper) / engine child, up since 2026-06-16 04:55 UTC.

## Layer (a) — standard deploy health
- [ ] **New PID** (restart happened): `systemctl show trading-corp -p MainPID -p ActiveEnterTimestamp -p NRestarts -p SubState`
      → MainPID != 2797287, fresh ActiveEnterTimestamp, SubState=running.
- [ ] **md5s match TARGET** (on prod, in `/home/azureuser/trading_corp`):
      `md5sum trading_corp/agents/divisions/bitunix_futures_observer.py trading_corp/agents/risk.py trading_corp/main.py trading_corp/persistence/db.py`
      → `eec6bda6…` / `49a1c796…` / `f733e374…` / `d56e0639…`.
- [ ] **Live config preserved** in `config/strategies.yaml`:
      `grep -nE "execution_mode: live|staleness_gate:|enabled: true|margin_seconds: 120" config/strategies.yaml | head`
      → `execution_mode: live` present; `staleness_gate:` exactly once; `margin_seconds: 120`.
- [ ] **broker paper=False / kalshi-disable / DD-cap / B2 maker OFF preserved** (these are NOT touched by this
      bundle — confirm no collateral change): `grep -nE "maker_entry_enabled|per_account_max_drawdown_pct|execution_mode:" config/strategies.yaml`
      → `maker_entry_enabled: false` (B2 OFF), DD-cap unchanged (0.99 override / 15% global per current config).
- [ ] **Startup clean** — no traceback: `journalctl -u trading-corp -n 120 --no-pager | grep -iE "Traceback|Error|Exception" | head`.
- [ ] **Staleness gate loaded** (C startup log line): `journalctl -u trading-corp --since "-10 min" | grep -i "staleness-reject gate"`
      → `BitUnix staleness-reject gate (C): enabled=True margin_s=120`.
- [ ] **Reconciler clean** on the new code (after bar-cache warmup, ~14 min): `journalctl -u trading-corp --since "-20 min" | grep -iE "position_state_reconciled|divergence" | tail`.

## Layer (b) — deps activation  [CONDITIONAL — only if a deps install was ALSO done this flat window]
This bundle changes **no dependencies**. The engine has not restarted since 04:55, so the E1 deps lock (E2.7)
has not been activated on the live venv. **If the operator runs THIS bundle only**, the venv is unchanged →
**skip layer (b)**. **If the operator also installs the E1 deps lock in the same window**, then verify:
- [ ] No import traceback: `journalctl -u trading-corp -n 200 --no-pager | grep -iE "pkg_resources|web3|setuptools|ModuleNotFound|ImportError"` → empty.
- [ ] Polymarket stays PAPER (engine runs `--brokers bitunix`; polymarket inert) — no live polymarket placement.
- [ ] Rollback (deps): restore `*.pre-e1-lock-20260616-1448` + `pip install -r /tmp/pip_freeze_pre_e1lock.txt` then restart.

## Layer (c) — index migration (db.py)
- [ ] **Index exists** post-startup:
      `sqlite3 "file:/home/azureuser/trading_corp/data/trading_corp.db?mode=ro" "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='audit_event';"`
      → both `ix_audit_event_ts` AND `ix_audit_event_actor_kind`.
- [ ] **Plan flips SCAN→SEARCH** (a kept on-loop reader):
      `sqlite3 "file:…?mode=ro" "EXPLAIN QUERY PLAN SELECT a.ts FROM audit_event a WHERE a.actor='polymarket_arbitrage' AND a.kind='would_have_placed' LIMIT 1;"`
      → `SEARCH … USING INDEX ix_audit_event_actor_kind`, no `SCAN audit_event`.
- [ ] **Startup was not a hang** — the index build over ~1.19M rows is one-time (~seconds). Confirm the engine
      reached its normal ready logs shortly after start (compare ActiveEnterTimestamp to first agent-tick log).

## Layer (d) — freeze-fix confirmation (the point of A)
- [ ] **No audit_event SCAN remains in the per-order risk path**: post-A `risk.py` has zero `audit_event`
      queries — `grep -c audit_event trading_corp/agents/risk.py` → 0 (only removal-comment lines, if any).
- [ ] **No multi-minute journal-silence gaps** (the freeze symptom was 6–13 min silence ~74 min apart):
      watch `journalctl -u trading-corp -f` across a polymarket-order-emission window (or review the next ~90 min)
      → continuous activity, no CPU-bound stall. Pre-fix runs showed `risk_approved`/`pending_approval_added`
      bursts then silence; post-fix should show no such stall.
- [ ] **(optional, definitive)** if the operator added the py-spy NOPASSWD drop-in: during any suspected stall,
      `sudo py-spy dump` on the engine child shows NO `_evaluate_polymarket` / `audit_event` frame.

## Sign-off
- [ ] All of (a), (c), (d) pass; (b) N/A or pass. Record new PID + md5s in the deploy log; update memory
      (bitunix-deblock + staleness) to DEPLOYED+VERIFIED. Rollback artifacts: `*.bak-pre-deblock-2026-06-16`.
