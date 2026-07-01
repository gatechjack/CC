# Deploy: kill SOL/XRP paper trade + expire stuck SOL paper row — 2026-06-30

Backlog #27 next-session priority (1). Drops SOL + XRP from the `bitunix_sfp`
division so they stop generating no-resolve `arm:watch` paper rows, and expires
the one stuck SOL paper row. **BTC + ETH stay `arm:trading` / live, unchanged.**

## What changes (3 lines in `config/strategies.yaml`, targeted-hunk)
```
- symbols: ["BTC/USDT.P", "ETH/USDT.P", "SOL/USDT.P", "XRP/USDT.P"]
+ symbols: ["BTC/USDT.P", "ETH/USDT.P"]
- "SOL/USDT.P": { bos_tf: "3m", arm: "watch" }   # removed from symbol_modes
- "XRP/USDT.P": { bos_tf: "3m", arm: "watch" }   # removed from symbol_modes
```

### ★ Why `symbols:` MUST change too (not just `symbol_modes:`)
The handoff said "drop from `symbol_modes`." Doing ONLY that is UNSAFE. The
observer iterates `config.symbols` and calls `mode_for(wire)`, which defaults to
**`(detection_tf, "trading")`** for any symbol absent from `symbol_modes`
(`bitunix_sfp_observer.py:140`). So removing SOL/XRP from `symbol_modes` while
leaving them in `symbols:` would flip them from PAPER (`arm:watch`) to **LIVE
untuned trading** — the opposite of intent. Correct kill = remove from BOTH lists.

### Record-only capture is intentionally KEPT
`main.py:408` builds a hardcoded 4-coin (BTC/SOL/ETH/XRP) 15m/3m RECORD-ONLY
cache set, independent of the traded `symbols:` list. Removing SOL/XRP from
`symbols:` does NOT stop data capture — SOL/XRP bars keep archiving, which is the
raw material Priority 2 (SOL SFP tuning) needs. No change to that dict.

## DB change
Expire the one stuck SOL paper row (paper-sim retired → no resolver; it sits
`result=NULL` forever). Idempotent, NULL-guarded:
```
UPDATE paper_trade_record SET result='expired',
  result_ts=strftime('%Y-%m-%dT%H:%M:%S+00:00','now')
WHERE order_id='e450302a-a7b0-4181-9d06-eb722c201fbb' AND result IS NULL;
```
Row confirmed on prod 2026-06-30: SOL/USDT.P buy, `sfp_real_3m_bos`,
entry 71.01 / SL 69.60899 / TP 73.81202, paper, result=NULL. It is the ONLY open
`bitunix_sfp` row (BTC/ETH flat). No XRP paper rows ever existed.

## Access facts (verified 2026-06-30, read-only SSH)
- `config/strategies.yaml` + `trading_corp.db` are **azureuser-owned rw** → edited
  directly, NO sudo. (The root-owned `--live-divisions` unit is NOT touched.)
- `sudo -n systemctl` **works** (NOPASSWD). `sudo -n sqlite3` **fails** ("password
  required") for the non-interactive agent/runner session → all DB ops use PLAIN
  `sqlite3` (owner rw). The old `tl1_*`/`sfp_*` flat-guards used `sudo -n sqlite3`
  and would have aborted; `kp_restart.sh` uses plain `sqlite3`.

## Drift-gate anchors (md5, LF blobs)
- prod/main `strategies.yaml` PRE  = `740d1a027da61322faa8a85c62173c78`
- prod/main `strategies.yaml` POST = `1ec7832bab862c66fff5f04513428675`
  (this branch's committed blob == POST anchor — main↔prod parity guaranteed)

## Operator steps
1. **Apply** (drift-gate + edit config + expire row; NO restart):
   `powershell -ep bypass -f .\kp_apply.ps1`
   Expect: `md5 OK` → `edit applied + all assertions passed` → new md5
   `1ec7832b…` → SOL row `result=expired` → `open … rows remaining: 0`.
   Abort codes: 2=drift (prod md5 != PRE anchor, nothing changed), 4=edit failed
   (auto-restored from backup).
2. **Restart** (flat-guarded; config takes effect here):
   `powershell -ep bypass -f .\kp_restart.ps1`
   Expect: `SFP FLAT (0 open live rows) - restarting` → new PID. Code 3 =
   not-flat abort (a BTC/ETH live trade is open) → re-run when flat.
3. Agent runs read-only bootsmoke (`kp_bootsmoke.sh`): engine active, SFP wired
   to BTC+ETH only, both reconcilers clean, 0 tracebacks.

## Rollback
- Config: `cp strategies.yaml.bak-pre-killpaper-2026-06-30 strategies.yaml` +
  flat-guarded restart. (Or git: revert this branch's commit.)
- SOL row: harmless; to un-expire, `UPDATE … SET result=NULL, result_ts=NULL
  WHERE order_id='e450302a-…'` (not recommended — the row never resolves anyway).

## Files
- `kp_apply.sh` / `kp_restart.sh` / `kp_bootsmoke.sh` — remote bash (copies here for
  the record; operator runs the `.ps1` runners in the cc root that stream these).
