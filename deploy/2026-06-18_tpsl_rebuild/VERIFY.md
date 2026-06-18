# VERIFY — bitunix /tpsl/ rebuild deploy (2026-06-18)

Run AFTER the operator's restart. Read-only; agent verifies. Prod base `/home/azureuser/trading_corp`.
Pre-deploy baseline: PID **2926399** (a PASS shows a NEW PID != 2926399).

## A. Confirms AT RESTART

### A1. Engine up, new PID
`ssh azureuser@trading.jacksumner.com "systemctl show trading-corp -p MainPID -p ActiveState -p SubState -p NRestarts"`
- [ ] `ActiveState=active`, `SubState=running`
- [ ] `MainPID` != 2926399 (new process)

### A2. Deploy-set files at TARGET md5
`ssh azureuser@trading.jacksumner.com "cd /home/azureuser/trading_corp && md5sum trading_corp/brokers/bitunix.py trading_corp/agents/divisions/bitunix_futures_observer.py trading_corp/agents/divisions/bitunix_position_reconciler.py"`
- [ ] brokers/bitunix.py == `74aa1b424dcb73840f9f636151098348`
- [ ] observer == `19da15ff4401996ba31e50cf6f3d59a0`
- [ ] reconciler == `707c682858f40245d06aee9dc8f94e00`

### A3. NO main.py/db.py touch (md5 unchanged from pre-deploy)
`ssh azureuser@trading.jacksumner.com "cd /home/azureuser/trading_corp && md5sum trading_corp/main.py trading_corp/persistence/db.py"`
- [ ] both md5s unchanged vs pre-deploy (capture pre-deploy values at apply time; deploy must not touch them)

### A4. bitunix still the REAL broker + re-arm intact
`ssh azureuser@trading.jacksumner.com "systemctl cat trading-corp | grep ExecStart"`
- [ ] ExecStart still `--live --brokers bitunix --live-divisions bitunix_futures` (bitunix_futures present → re-arm intact)
- [ ] runtime confirms broker `paper=False` (startup log / status line — NOT paper-wrapped)

### A5. Config preserved (deploy touched 0 config files)
- [ ] `execution_mode: live` for bitunix_futures (strategies.yaml)
- [ ] per_account_max_drawdown_pct = **0.99** for bitunix_futures (risk override; DD-cap)
- [ ] B2 maker execution **OFF** (maker key off)
- [ ] staleness-reject gate **loaded/ON** (entry-staleness margin configured)

### A6. Reconciler clean, flat, no orphan
- [ ] startup reconcile clean (position_state_reconciled, no halt latched)
- [ ] flat / no orphaned untracked position; no divergence alarm

## B. Needs a LIVE ENTRY (the point of the rebuild — observe on next live trade)

NOT confirmable at restart. Observe on the first post-deploy live entry:
- [ ] TP legs **REST as `/tpsl/` orders** — `get_pending_orders` shows them; **no 30038** reject
- [ ] SL-trail uses **`/tpsl/position/modify_order`** — **NO 404** (the live-confirmed failure on trade 7d1a78dc)
- [ ] Position SL **places + auto-reduces on a partial** fill

## Validation-window flags (operator decides; fail-soft, B1 guards — NOT deploy blockers)
- (a) **B1 entry-stop vs separate Position SL coexistence** — no 30038 when both present.
- (b) **3-leg validation needs ≥ 0.0012 BTC** (TIER_SIZING) to fully exercise the TP ladder.

## If any A-check fails → ROLLBACK
```
cd /home/azureuser/trading_corp
for f in trading_corp/brokers/bitunix.py trading_corp/agents/divisions/bitunix_futures_observer.py trading_corp/agents/divisions/bitunix_position_reconciler.py; do mv "$f.bak-pre-tpsl-rebuild-2026-06-18" "$f"; done
```
then restart: `az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "systemctl restart trading-corp"`
