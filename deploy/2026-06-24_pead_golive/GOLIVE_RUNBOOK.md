# PEAD GO-LIVE — runbook (2026-06-24, unattended-tomorrow)

Deploys the FRACTIONAL build (bb9741a) + Flag-2 reconcile (6228a0b) onto the prod
import tree `/home/azureuser/trading_corp`, flips the 4 go-live gates, ONE restart.
After this, PEAD scans pre-open (8:30-9:25 ET) and trades autonomously (no HITL).

**Board-gate:** the operator runs every prod write/restart. The agent staged +
drift-checked + boot-smoke-scripted everything; nothing has been written to prod yet.

**PRECONDITION:** Bitunix FLAT at restart time (the only coupling is the shared
process restart). Confirmed appears flat at staging; re-confirm in step 1.

**The 4 go-live flips (all in this deploy):**
1. divisions.yaml `robinhood_pead standby: true -> false`  (payload)
2. ExecStart `--live-divisions ... robinhood_pead`          (systemd unit, step 4)
3. strategies.yaml `robinhood_pead auto_execute: false -> true` (payload)
4. EODHD via KV (KEY_VAULT_URI already set) — verified in boot-smoke

## SEQUENCE (run the cc\*.ps1 runners in order; check output between each)

### 1 — PRE-FLIGHT (read-only): Bitunix flat + RH session validity
`powershell -ep bypass -f .\golive_1_preflight.ps1`
- Must show Bitunix flat (0 open) and the RH pickle session reachable to 680725082.
- If the pickle is NOT reachable -> do step 2. If reachable, step 2 is optional but
  RECOMMENDED tonight (fresh pickle = safe unattended through tomorrow's open).

### 2 — AUTH (gate 2): refresh the RH pickle (device approval) — DO TONIGHT
`powershell -ep bypass -f .\golive_2_pickle.ps1`
- Runs pickle_refresh.py on prod. **APPROVE THE DEVICE PROMPT ON THE RH APP** when it
  says to (it polls ~60s). On success it prints "680725082 reachable = True".
- Why: the engine's connect() does rs.login(store_session=True) — SAFE on a valid
  pickle (reuses it), CORRUPTING only on an expired one. A fresh pickle now = the
  restart reuses it, and the in-process session stays valid through tomorrow's open.
- If it 429s: wait for the rate-limit to cool, retry. Do NOT restart with a stale
  pickle if step 1 showed it unreachable.

### 3 — STAGE + APPLY FILES (drift-gate + GATE 1): no sudo
`powershell -ep bypass -f .\golive_3_apply.ps1`
- scp the package to prod /tmp/pead_golive, then runs apply_files.sh + preserve_check.sh.
- apply_files.sh: DRIFT-GATE (prod 8 files must match the staging baseline md5 — ABORTS
  if prod changed since staging) -> backup *.bak-pre-golive-2026-06-24 -> copy -> verify
  target md5. preserve_check.sh: GATE 1 (strategies.yaml changed ONLY in robinhood_pead;
  bitunix fee-coupled intact; divisions.yaml only the standby flip).
- If either ABORTS -> STOP, report, nothing is half-applied to break Bitunix.

### 4 — UNIT FLIP + RESTART (sudo): the ONE restart
`powershell -ep bypass -f .\golive_4_unit_restart.ps1`
- sudo: add robinhood_pead to ExecStart --live-divisions, daemon-reload, then
  `systemctl restart trading-corp`. (Re-confirms Bitunix flat first; holds if not.)

### 5 — BOOT-SMOKE (gate 3): ~45s after restart
`powershell -ep bypass -f .\golive_5_bootsmoke.ps1`
- Runs bootsmoke.sh. Needs ALL hard checks GREEN:
  Bitunix: paper=False, ExecStart keeps bitunix_futures, fee-coupled intact, no
  FillEvent/traceback. PEAD: in --live-divisions, standby:false, auto_execute:true,
  pending_order table created, RH logged in to 680725082, EODHD key loads, /telemetry/
  pead 200. Engine: NRestarts+1, healthz LIVE.
- **If ANY hard check FAILS -> ROLL BACK (step R), do not leave a broken engine.**

### DONE
PEAD is live. Tomorrow 8:30-9:25 ET it scans, places GFD fractional buys (queued to
the open), the reconcile loop confirms each fill at/after 9:30 and writes the
position; exits run on the manage loop. No HITL — by design.

### R — ROLLBACK (only if boot-smoke fails)
`powershell -ep bypass -f .\golive_rollback.ps1`
- Restores the 8 files from backup + sudo-reverts ExecStart + restart -> back to the
  prior INERT state (clean inert beats broken live overnight).

## NOTES
- Auth path (gate 2): engine connect() = rs.login(store_session=True), broker bound to
  680725082 (divisions.yaml account_filter). Fresh pickle (step 2) is the stable path.
- Account ~$75 (BP ~$56); position_pct 0.10 -> ~$7.50/name, max_concurrent 10. Bounded
  risk. Flag-2 is built+unit-tested but runs LIVE for the first time tomorrow (accepted).
- Rollback files: *.bak-pre-golive-2026-06-24 on prod. Unit backup printed by step 4.
