# VERIFY — bitunix /tpsl/ TP-leg-fix redeploy (2026-06-19)

Run AFTER the operator's restart. Read-only; agent verifies. Prod base `/home/azureuser/trading_corp`.
Pre-deploy baseline: PID **2988577** (a PASS shows a NEW PID != 2988577).
Fix commit `8d3d164` on branch `bitunix-tpsl-rebuild-2026-06-18`.

## A. Confirms AT RESTART

### A1. Engine up, new PID
`ssh azureuser@trading.jacksumner.com "systemctl show trading-corp -p MainPID -p ActiveState -p SubState -p NRestarts"`
- [ ] `ActiveState=active`, `SubState=running`
- [ ] `MainPID` != 2988577 (new process)

### A2. Deploy-set files at TARGET md5
`ssh azureuser@trading.jacksumner.com "cd /home/azureuser/trading_corp && md5sum trading_corp/brokers/bitunix.py trading_corp/brokers/bitunix_exceptions.py trading_corp/agents/divisions/bitunix_futures_observer.py"`
- [ ] brokers/bitunix.py == `00bd03a8e8ad2d5ca34767a8d123eff9`
- [ ] brokers/bitunix_exceptions.py == `62ddd11cebc67affd3c0b56d06cb396c`
- [ ] observer == `f167e456fa2f2a2a6edd86fcf93da5c1`

### A3. NO main.py/db.py touch (md5 unchanged from pre-deploy baseline)
`ssh azureuser@trading.jacksumner.com "cd /home/azureuser/trading_corp && md5sum trading_corp/main.py trading_corp/persistence/db.py"`
- [ ] main.py == `f16e9c24f81e65c9eb9d98019eea4e23` (unchanged)
- [ ] db.py == `a2c2ff46b89ec3d30640552db19b962c` (unchanged)
- [ ] reconciler unchanged == `707c682858f40245d06aee9dc8f94e00` (NOT in this set)

### A4. bitunix still the REAL broker + re-arm intact
`ssh azureuser@trading.jacksumner.com "systemctl cat trading-corp | grep ExecStart"`
- [ ] ExecStart still `--live --brokers bitunix --live-divisions bitunix_futures` (re-arm intact)
- [ ] runtime confirms broker `paper=False` (startup log / status line — NOT paper-wrapped)

### A5. Config preserved (deploy touched 0 config files)
- [ ] `execution_mode: live` for bitunix_futures (strategies.yaml)
- [ ] per_account_max_drawdown_pct = **0.99** for bitunix_futures (risk override; DD-cap)
- [ ] B2 maker execution **OFF** (maker key off)
- [ ] staleness-reject gate **loaded/ON** (entry-staleness margin configured)

### A6. Reconciler clean, flat, no orphan
- [ ] startup reconcile clean (`position_state_reconciled`, no halt latched)
- [ ] flat / no orphaned untracked position; no divergence alarm
- [ ] NO `ImportError` / `BitunixUntrackedTpslOrder` import failure in the startup log
      (cross-file import sanity — the new exception loads from `bitunix_exceptions`)

## B. Needs a LIVE multi-leg ENTRY (the point of the fix — observe on the next qualifying trade)

NOT confirmable at restart. The fix only engages on a multi-leg (≥ 0.0012 BTC) entry:
- [ ] TP legs now **TRACK**: `bracket_placed` shows `legs_placed=3` (or the degraded N) with
      `tp_order_ids` populated (tp1/tp2/tp3 → venue ids) — **no `legs_placed=0`**
- [ ] **No** `bracket_tp_leg_failed` with `'list' object has no attribute 'get'` (the repaired crash)
- [ ] **Hardening:** if any leg's POST is accepted but its id can't be parsed, a
      `bracket_tp_leg_untracked` audit fires (leg/price/qty/position_id) — flagged, not swallowed
- [ ] SL-trail + auto-reduce can now exercise on a TP fill (the downstream Section-B PENDINGs)

Note: B confirms ONLY on a live ≥0.0012 BTC entry. A trade that stops out before any TP fill (like
cb6b4d4a) exercises only the placement (legs_placed=3, tracked) — not the SL-trail/auto-reduce.

## If any A-check fails → ROLLBACK
```
cd /home/azureuser/trading_corp
for f in trading_corp/brokers/bitunix.py trading_corp/brokers/bitunix_exceptions.py trading_corp/agents/divisions/bitunix_futures_observer.py; do mv "$f.bak-pre-tpsl-legfix-2026-06-19" "$f"; done
```
then restart: `az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "systemctl restart trading-corp"`
→ returns to the current (TP-legs-untracked-but-fail-soft, SL-only) state.
