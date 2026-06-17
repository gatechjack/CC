# Deploy plan — bitunix bracket-exit + E2.5 activation (7 files) — 2026-06-17

PREPARE-only package. Solo deploy onto the post-cutover base (polymarket done; monitoring-only session).
Rebased code tip **b077b66**; prep committed on `bitunix-bracket-exit-rebased-2026-06-17`. **Operator greenlit E2.5
activation** (execution_mode writes go live with this deploy; reader audit clean — no column filters; polymarket green).
Agent SSH read-only except the §4-authorized apply; operator runs the single restart.

## Scope
- **#3** lock-resilient fill-registration (orphan-prevention) · **#5-B/C** exit-guard exemptions · exchange-resting
  **bracket** exit (TP ladder + SL-move) · **E2.5** execution_mode write-side activation.
- 7 files. **Never** `main.py` / `db.py` (cutover's; already live) / `strategies.yaml` (bracket has no yaml flag).

## Dual-mode drift guard + md5 table (verified at build; drift re-checked = CLEAN)
| File | gate mode | BASE a64a42f | PROD-CURRENT (gate) | TARGET b077b66 |
|---|---|---|---|---|
| agents/data_exec.py | **prod-current** (E2.5 trio) | 1804ef54 | **e3e4cca7** | 51281fbd |
| agents/logger.py | **prod-current** (E2.5 trio) | e8b54f8f | **2938e089** | e625c388 |
| persistence/models.py | **prod-current** (E2.5 trio) | a781b495 | **96cf31c4** | a781b495 |
| agents/divisions/bitunix_futures_observer.py | prod==base | eec6bda6 | eec6bda6 | 13469b10 |
| agents/divisions/bitunix_position_reconciler.py | prod==base | bf048cd1 | bf048cd1 | 386cc6c2 |
| brokers/bitunix.py | prod==base | 70f7904f | 70f7904f | 7a3da849 |
| agents/divisions/bitunix_bracket.py | **create** (absent) | — | ABSENT | bd639224 |

- E2.5 trio gated to **prod-current** = the operator-approved deviation from prod==base (prod is pre-E2.5; the
  delta their full-file ships is exactly E2.5 + the bitunix #3/#5-C core). The apply ABORTS if any of these ≠ the
  listed current (prod moved since prep → STOP+surface).
- **COUPLING (write-outage guard):** data_exec + agents/logger + models.py ship **together** or not at all. The new
  `log_proposed_order` INSERT binds `:execution_mode`; prod models.py (f66722e, pre-E2.5) lacks it in `to_db_row()`
  → shipping logger without models = every proposed_order write raises. The apply asserts all three staged + that
  `agents/logger.py` (not `path_logger/logger.py`) defines `log_proposed_order`.

## Untouched-file baselines (VERIFY (f) compares against these; deploy must NOT change them)
- `config/strategies.yaml` = **569c38f8** · `trading_corp/main.py` = **f16e9c24** · `trading_corp/persistence/db.py` = **a2c2ff46** · `config/risk.yaml` = **994f40c6** (DD-cap 0.99 source).

## Delivery + run (eventual execution — agent drives SSH; operator runs ONLY the restart)
1. **Stage** (agent): deliver the 7 staged files to prod `$BASE/_bracket_e25_stage/` mirroring `trading_corp/...`.
   The byte-exact LF targets are committed under `staged/` here (md5 == TARGET, `-text` in .gitattributes). E.g.
   `scp -r deploy/2026-06-17_bracket/staged/trading_corp azureuser@trading.jacksumner.com:/home/azureuser/trading_corp/_bracket_e25_stage/`
2. **Apply (no restart)** — stream the script:
   `Get-Content deploy/2026-06-17_bracket/deploy_apply_bracket_e25_2026-06-17.sh -Raw | ssh azureuser@trading.jacksumner.com "tr -d '\r'|bash"`
   → stages-gate → preflight compile → coupling guard → drift guard → backup → atomic-mv (6) + create (1) → re-verify md5 → STOP.
3. **Restart (operator only):** `ssh -t azureuser@trading.jacksumner.com sudo systemctl restart trading-corp`
4. **VERIFY:** run `VERIFY.md` (a)-(g).

## Rollback (operator; also the only bracket-OFF — no kill-switch, accepted)
```
cd /home/azureuser/trading_corp
for f in trading_corp/agents/data_exec.py trading_corp/agents/logger.py trading_corp/persistence/models.py \
         trading_corp/agents/divisions/bitunix_futures_observer.py \
         trading_corp/agents/divisions/bitunix_position_reconciler.py trading_corp/brokers/bitunix.py; do
  mv "$f.bak-pre-bracket-2026-06-17" "$f"; done
rm -f trading_corp/agents/divisions/bitunix_bracket.py
sudo systemctl restart trading-corp
```
Reverts E2.5 too → execution_mode column reverts to bare default 'paper' (tolerated; the reader audit showed no
reader filters on the column, so reverting is reader-safe).

## Guarantees
- No restart inside the apply script. No `main.py`/`db.py`/`strategies.yaml` in the payload. Coupling enforced.
  Validation trade (post-deploy, DD-safe per the 0.99 risk.yaml override) is a SEPARATE operator step.
