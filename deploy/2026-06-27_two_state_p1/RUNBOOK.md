# Piece 1 two-state collapse — DEPLOY RUNBOOK (Board-authorized 2026-06-27)

Deploys the two-state collapse (bitunix_futures → HALTED-INERT; replay disabled; SFP stays trading) onto
prod. **Targeted-hunk** apply (see drift note) — NOT a file copy. Operator runs every prod step.

## ★ DRIFT NOTE (why targeted-hunk, not file-copy)
The `bitunix-sfp-division-2026-06-25` branch **diverges from prod by hundreds of lines**: prod's `main.py`
(4568 L) and `bitunix_futures_observer.py` (4104 L) carry directly-deployed fixes the branch never had
(ref-vs-fill, D3 role-fix, P2 sign-fix, /tpsl/ rebuild, D4 guard, pead wiring). A full-file copy of the
branch versions would **revert live real-money fixes**. So the staged files = **prod's current blobs +
ONLY the Piece-1 hunks** (`git diff dea1dfd 01a1df9` applied onto the prod blobs; 12/14 hunks via zero-fuzz
`patch`, 2 hand-applied at prod context). `prod-vs-staged.diff` in this dir is the proof: it shows exactly
the Piece-1 changes and nothing else.

## Integrity md5 (LF)
| file | PROD now (pre-deploy) | STAGED (deploy target) |
|---|---|---|
| trading_corp/main.py | `1069a6db98da8cffbf34bb8f365bc4e6` | `698cd083d484296ac6f991224fdac376` |
| …/bitunix_futures_observer.py | `2647fccc630c8acacbe0d5a32f05b1c8` | `dd64a7f4f6a16ed7cf9c2051f612fc31` |
| config/strategies.yaml | `281b373f033dbcf23fc0176372470e1e` | `0cd6e45d758c8e6d226302d4055bce44` |

**BYTE-UNCHANGED (NOT deployed, must stay):** `bitunix_sfp_observer.py` `18da45f2…`, `bitunix_sfp.py`
`5c71a103…`, `bitunix_position_reconciler.py` `3a23610c…`.

**Config preserved:** `bitunix_sfp.execution_mode: live` is KEPT (only `mode: trading` added). `bitunix_futures`
gets `mode: halted`. No `--live-divisions` change (unit untouched; mode is separate).

## Steps (operator, from C:\Users\AA Incorporado\cc)
1. **Review** `deploy/2026-06-27_two_state_p1/PIECE1_prod_vs_staged.diff` (it is exactly Piece 1).
2. **Apply** (md5-gated; backs up live → `~/p1_bak_2026-06-27/*.bak-pre-twostate-2026-06-27`; places files;
   re-verifies; proves SFP/recon untouched; **does NOT restart**):
   `powershell -ep bypass -f .\p1_apply.ps1`
   → abort if any `md5sum: … FAILED`. Confirm the byte-unchanged trio prints `18da45f2 / 5c71a103 / 3a23610c`.
3. **Restart** (NOPASSWD systemctl; prints new MainPID):
   `powershell -ep bypass -f .\p1_restart.ps1`
4. **Verify**:
   `powershell -ep bypass -f .\p1_verify.ps1`

## Verify expectations (step 4)
- `ActiveState=active`, new `MainPID` (≠ 3641539), `NRestarts` incremented.
- Boot markers present: `bitunix_sfp mode gate: mode=trading trading=True`, `bitunix_sfp 15m loop spawned`,
  `bitunix_futures HALTED — pa-redeem loop NOT started`, `paper_trade_replay boot catch-up SKIPPED`.
- NO `paper_trade_replay_loop` start, NO `Traceback`/`ImportError`/`unexpected keyword`, NO `REFUSING TO START`.
- Deployed md5 == staged (`698cd083 / dd64a7f4 / 0cd6e45d`); SFP/recon trio == `18da45f2 / 5c71a103 / 3a23610c`.
- Account FLAT (SFP blind until Piece 2 IP swap — expected). Bitunix egress still 403 until Piece 2.

## Rollback
`ssh azureuser@trading.jacksumner.com` then copy `~/p1_bak_2026-06-27/*.bak-pre-twostate-2026-06-27` back over
the three live paths, then `sudo -n systemctl restart trading-corp`. (Inbound/unit never changed.)

## After Piece 1 verifies
→ Piece 2 (NAT-gw IP swap, your Azure op) → verify public-kline 200 + key re-bind → Piece 3 (ws hybrid).
