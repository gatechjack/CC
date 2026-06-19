# VERIFY — P2 combined redeploy (2026-06-19)

Run AFTER the operator's restart. Read-only; agent verifies. Prod base `/home/azureuser/trading_corp`.
Pre-deploy baseline: PID **3046486** (the deployed legfix). A PASS shows a NEW PID != 3046486.
Branch tip `d83e877` on `bitunix-tpsl-rebuild-2026-06-18`. 5 files, NO config.

## A. Confirms AT RESTART

### A1. Engine up, new PID
`ssh azureuser@trading.jacksumner.com "systemctl show trading-corp -p MainPID -p ActiveState -p SubState -p NRestarts"`
- [ ] `ActiveState=active`, `SubState=running`
- [ ] `MainPID` != 3046486 (new process)

### A2. Deploy-set files at TARGET md5
`ssh … "cd /home/azureuser/trading_corp && md5sum trading_corp/brokers/bitunix.py trading_corp/agents/divisions/bitunix_futures_observer.py trading_corp/agents/divisions/bitunix_position_reconciler.py trading_corp/agents/divisions/bitunix_bracket.py trading_corp/persistence/models.py"`
- [ ] bitunix.py == `3f68473a4ddfe27ca035308414c1c280`
- [ ] observer == `a31a10f1445f0263389c377c41f742f8`
- [ ] reconciler == `bd06ea281a853687fad8d0a6831e9c0a`
- [ ] bitunix_bracket.py == `f4be4e9b8af36afac9a2489ebeb42c56`
- [ ] **models.py == `d7561d3c95530f74071ab195d239c4ce`** (the coupled override file)
- [ ] bitunix_exceptions.py still `62ddd11c…` (NOT in set — unchanged from legfix)

### A3. NO main/db/config touch
`ssh … "cd /home/azureuser/trading_corp && md5sum trading_corp/main.py trading_corp/persistence/db.py config/strategies.yaml"`
- [ ] main.py == `f16e9c24f81e65c9eb9d98019eea4e23` (unchanged)
- [ ] db.py == `a2c2ff46b89ec3d30640552db19b962c` (unchanged)
- [ ] **strategies.yaml STILL `569c38f8…` (UNTOUCHED — yellow_x deferred to a separate config-edit)**

### A4. models.py coupling — NO binding error (the override condition)
- [ ] startup log shows **NO `ImportError` / `TypeError` / `FillEvent` / `role` binding error**
      (the E2.5-style no-write-outage check: the new bitunix.py imports + `FillEvent(role=…)` load clean)
- [ ] classifier functions importable: bitunix_position_reconciler imported `classify_result` /
      `classify_exit_kind` from bitunix_bracket with no ImportError

### A5. Re-arm + config preserved (deploy touched 0 config)
- [ ] ExecStart still `--live --brokers bitunix --live-divisions bitunix_futures`; broker `paper=False`
- [ ] `execution_mode: live`, per_account_max_drawdown_pct **0.99**, B2 maker **OFF**, staleness gate ON

### A6. Reconciler clean, flat, no orphan
- [ ] startup reconcile clean (`position_state_reconciled` / `restart-resume matched=0 orphan=0`), no halt

## B. Needs a LIVE entry/close (observe on the next live trade)

- [ ] **maker/taker:** the next fill constructs `FillEvent(role=…)` with **NO TypeError**; the record gets
      `$.entry_role` (and on close `$.exit_role` + `$.maker_taker_mix`). (Entry is 'taker' while B2 OFF.)
- [ ] **P2 classifier:** the next close derives `result` via NET — a genuine **win books `result=win`**
      (not the old hard-coded 'loss') — and `exit_kind` via order-id match (a TP close → `tp`, a stop → `stop`,
      ambiguous → `unknown`, **never defaulting to `stop`**); mirrored to `$.autobook_level_type` + `$.exit_kind`.
- [ ] **bracket:** `classify_*` behave as built (covered by the regression; live is the confirmation).

Note: B confirms only on live trades. The **record-correction SQL** (`deploy/2026-06-19_p2_record_correction/`)
and the **yellow_x config edit** are SEPARATE operator steps — not part of this deploy.

## If any A-check fails → ROLLBACK
```
cd /home/azureuser/trading_corp
for f in trading_corp/brokers/bitunix.py trading_corp/agents/divisions/bitunix_futures_observer.py trading_corp/agents/divisions/bitunix_position_reconciler.py trading_corp/agents/divisions/bitunix_bracket.py trading_corp/persistence/models.py; do mv "$f.bak-pre-p2-combined-2026-06-19" "$f"; done
```
then restart via az run-command → returns to the legfix state (PID-family 3046486 code).
