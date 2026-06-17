# VERIFY — bracket + E2.5 deploy (7 files). Run AFTER the operator restarts.
All commands READ-ONLY (agent SSH 82fda13). Prior PID before restart: 2860513 (01:22 cutover).

## (a) standard deploy health
- [ ] **New PID / running**: `systemctl show trading-corp -p MainPID -p ActiveEnterTimestamp -p SubState -p NRestarts`
      → MainPID != 2860513, fresh ActiveEnterTimestamp, SubState=running.
- [ ] **7 files at TARGET md5** (in `/home/azureuser/trading_corp`):
      `md5sum trading_corp/agents/data_exec.py trading_corp/agents/logger.py trading_corp/persistence/models.py trading_corp/agents/divisions/bitunix_futures_observer.py trading_corp/agents/divisions/bitunix_position_reconciler.py trading_corp/brokers/bitunix.py trading_corp/agents/divisions/bitunix_bracket.py`
      → `51281fbd` / `e625c388` / `a781b495` / `13469b10` / `386cc6c2` / `7a3da849` / `bd639224`.
- [ ] **No startup traceback**: `journalctl -u trading-corp -n 150 --no-pager | grep -iE "Traceback|Error|Exception|ImportError" | head`.

## (b) gate-still-fires (the freeze mitigation must survive THIS deploy too)
- [ ] `journalctl -u trading-corp --since "-10 min" | grep -i "staleness-reject gate"`
      → `BitUnix staleness-reject gate (C): enabled=True margin_s=120`.

## (c) bracket wired
- [ ] `grep -n "_place_bracket_exits" trading_corp/agents/divisions/bitunix_futures_observer.py` → call at ~3237 + def.
- [ ] `grep -n "move_bracket_sls" trading_corp/agents/divisions/bitunix_position_reconciler.py` → present.
- [ ] module imports clean: `trading_corp/venv/bin/python -c "import trading_corp.agents.divisions.bitunix_bracket as b; print(b.__file__)"` → no ImportError.

## (d) E2.5 writes populating + NO write outage (proves models.py shipped + coupling)
- [ ] **No binding/write errors** (the models.py-missing failure mode): `journalctl -u trading-corp --since "-15 min" | grep -iE "binding parameter|:execution_mode|log_proposed_order FAILED|sqlite3.ProgrammingError"` → **empty**.
- [ ] **Writes still succeed** (no outage): `sqlite3 "file:/home/azureuser/trading_corp/data/trading_corp.db?mode=ro" "SELECT COUNT(*) FROM proposed_order WHERE ts > '<restart_ts>';"` → increments after restart (orders still recording).
- [ ] **Column is WRITTEN, not bare default** — distribution of post-restart rows:
      `sqlite3 "file:…?mode=ro" "SELECT execution_mode, COUNT(*) FROM proposed_order WHERE ts > '<restart_ts>' GROUP BY execution_mode;"`
      → paper bitunix activity tags `paper`; **on the next real live bitunix fill, that row tags `live`** (definitive proof the writer populates it). Pre-deploy baseline: all 31,927 / 175 rows were `paper`.

## (e) config preserved (NOT touched by this deploy)
- [ ] `grep -nE "execution_mode: live|maker_entry_enabled" config/strategies.yaml | head` → `execution_mode: live` present; `maker_entry_enabled: false` (B2 OFF).
- [ ] kalshi divisions still disabled (per pre-deploy strategies.yaml — unchanged).
- [ ] **DD-cap 0.99** (the REAL source is risk.yaml, not strategies.yaml): `grep -n -A1 "bitunix_futures:" config/risk.yaml | grep per_account_max_drawdown_pct` → `0.99`.
- [ ] broker paper=False: `journalctl -u trading-corp --since "-15 min" | grep -i "BitunixBroker connected"` (live account) OR confirm `execution_mode: live`.

## (f) cutover files UNTOUCHED + strategies.yaml untouched
- [ ] `md5sum trading_corp/main.py trading_corp/persistence/db.py` → **unchanged**: `f16e9c24f81e65c9eb9d98019eea4e23` / `a2c2ff46b89ec3d30640552db19b962c`.
- [ ] `md5sum config/strategies.yaml` → unchanged from the pre-deploy md5 recorded in PLAN.md.

## (g) reconciler clean / flat (no orphan)
- [ ] `journalctl -u trading-corp --since "-20 min" | grep -iE "position_state_reconciled|divergence|orphan|untracked" | tail` → clean (no orphan/untracked; reconciler resumed).

## Sign-off
- [ ] (a)-(g) pass. Record new PID + 7 md5s in the deploy log; update memory (exit-redesign-rebase → DEPLOYED+VERIFIED).
      Rollback artifacts: `*.bak-pre-bracket-2026-06-17` (6) + delete `bitunix_bracket.py`.
