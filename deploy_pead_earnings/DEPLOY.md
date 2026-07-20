# pead-earnings-watcher — deploy (HOT, no engine bounce)

Isolated side-process for the PEAD "Upcoming Earnings" panel. Mirrors `market-context-recorder`:
separate process, writes ONLY its own `~/pead_earnings/earnings_watch.db`, reads the engine DB
`mode=ro`, never imports the engine trade path, never restarts the engine. Driven by a systemd
**timer** (oneshot, 2x/day). Prod VM TZ = Etc/UTC.

## Files
- `pead_earnings_watcher.py` — the watcher (modes: `--check`, `--once`/default, `--dry-run`).
- `earnings_watch_db.py` — own DB schema + `connect_rw` / `connect_engine_ro(mode=ro)` helpers.
- `pead-earnings-watcher.service` — Type=oneshot; ExecStart runs `--once`.
- `pead-earnings-watcher.timer` — OnCalendar 11:00 + 21:00 UTC (~pre-market / post-close ET), Persistent.

## Reuses prod pure modules (via `PYTHONPATH=/home/azureuser/trading_corp`)
`pead_signal` (screen + SUE) and `earnings_provider` (EODHD fundamentals) — so the screen + SUE are
byte-identical to the engine. Universe parse + `business_days` are replicated verbatim.

## Deploy steps (as azureuser unless noted root)
1. `mkdir -p ~/pead_earnings` ; scp the 4 source files (NOT `__pycache__`) to `~/pead_earnings/`.
2. GATE 1 — plumbing/isolation, NO external per-name calls:
   `cd ~/pead_earnings && PYTHONPATH=/home/azureuser/trading_corp KEY_VAULT_URI=https://kv-tc-vtwbowt3wtkpy.vault.azure.net/ /home/azureuser/trading_corp/venv/bin/python pead_earnings_watcher.py --check`
   Expect: schema ready, engine_db mode=ro, held count, universe=3207, eodhd_key_loaded=True.
3. GATE 2 — bounded live smoke (few names, real calendar+screen+SUE+DB write):
   `... PEAD_MAX_NAMES=8 python pead_earnings_watcher.py --once`  → summary JSON, rows upserted.
4. Full refresh: `... python pead_earnings_watcher.py --once` (processes all in-universe reporters).
5. Install units (ROOT via Azure Run Command):
   `cp ~/pead_earnings/pead-earnings-watcher.{service,timer} /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now pead-earnings-watcher.timer`
6. Verify: `systemctl list-timers pead-earnings-watcher.timer` (next fire), and
   `sqlite3 ~/pead_earnings/earnings_watch.db "select phase,count(*) from earnings_watch group by phase;"`.

Engine (`trading-corp.service`) is NOT touched at any step.

## Rollback
- Stop scheduling: `systemctl disable --now pead-earnings-watcher.timer` (root).
- Full remove: also `rm /etc/systemd/system/pead-earnings-watcher.{service,timer}`, `daemon-reload`,
  `rm -rf ~/pead_earnings`. The engine is unaffected either way (no shared state; engine DB was read-only).
