# Runbook — SFP TP-placement fix (2026-06-26)

Branch `bitunix-sfp-division-2026-06-25`. **STAGED, NOT deployed.** Operator (GT_Jack) runs every
prod write/restart/re-arm step; the agent verified the PROD surface read-only and computed all md5
gates. **SFP stays DISARMED (`auto_execute:false`) through this apply** — it does NOT re-arm anything.

## The blocker this fixes
The live SFP `_place` called only `data_exec.place → broker.place_order`, which attaches the B1 `slPrice`
stop ONLY — **`take_profit_price` was never sent to the venue.** So a winning SFP long had nothing to
close it at 2R and rode to the B1 stop (loss) → stop-out-only → the +0.267R edge inverts. (The real TP
machinery, `bitunix.py:1959 place_tpsl_order`, was wired into the FUTURES observer only.)

## The fix (one functional file)
`bitunix_sfp_observer.py`: after the live entry fills + the existing `_write_record`, `_place` now calls a
new `_place_tp_leg` that rests ONE full-qty native `/tpsl/` reduce-only TP leg (OCO with the B1 stop):
- resolves the venue `positionId` via `get_pending_positions()` (wire-symbol + side), mirroring the
  futures observer's `_place_bracket_exits`;
- sizes the single leg via `build_bracket_legs(qty, [{leg:tp1, price:tp, fraction:1.0}])` → inherits the
  0.0003 BTC min-leg floor + the 0-legs→SL-only branch (the SAME `bitunix_bracket.py` already on PROD);
- calls `place_tpsl_order(symbol, position_id, tp_price, tp_qty)` and handles all four outcomes
  (id→track; `""`→idempotent dup, no double-count; `BitunixUntrackedTpslOrder`→flag LOUD; other error→
  fail-soft + LOUD); NO retry loop;
- inline-UPDATEs the entry row's `extra_json` with `bracket_tp_order_id/position_id/tp_qty/tp_price`.

**FAIL-SOFT + LOUD on every failure path:** a TP-place failure NEVER unwinds the filled entry and NEVER
auto-flattens — the entry + B1 stop stay intact (downside capped at the structural stop) and a dedicated
audit (`sfp_tp_*`) + Telegram fire so it is never silent.

Also: comment fixes — `bitunix_sfp_observer.py:10` docstring + `main.py` (~L625) reworded to match reality
(entry + atomic B1 stop, then a post-fill native `/tpsl/` reduce-only TP leg; the false "shared bracket"
phrasing — which is what made the blocker look safe — is gone).

## What ships / what does NOT
| file | method | Gate-A base (prod-pre) | Gate-B target (post) |
|---|---|---|---|
| `agents/divisions/bitunix_sfp_observer.py` | **full-file swap** (prod == branch HEAD) | `b2b856be` | `db831daf` |
| `main.py` | **splice** (cosmetic comment only; prod base + 1 hunk) | `82a01f83` | `1069a6db` |

- **NOT deployed — `bitunix_bracket.py`:** already on PROD, byte-identical to the worktree's synced copy
  (md5 `f4be4e9b`). It is the single source of truth for `build_bracket_legs`; the observer imports it.
- **NOT deployed — the test** (`tests/test_bitunix_sfp_tp_placement.py`): branch-only.
- **NOT touched — sacred/manifest:** `bitunix.py` (`4b00dea2`), the position reconciler, the confluence
  observer. Verified read-only on PROD.
- `main.py` is a **splice** because PROD's `main.py` (`82a01f83`) deliberately differs from the branch's
  full `main.py` (targeted-hunk history) — the staged `main.py` is **PROD's own file + the comment hunk**,
  so the full-file swap is safe (the md5 Gate-A guards against any PROD drift since it was fetched).

## Pre-deploy gates (GREEN)
- New tests `tests/test_bitunix_sfp_tp_placement.py`: **7/7 pass** (full-qty TP at tp_price + extra_json
  records id; positionId-unresolved→SL-only+loud; place_tpsl_order raise→fail-soft+alert+entry intact;
  min-leg-too-small→SL-only+alert; idempotent `""`→no double-count; untracked→flagged LOUD; unsupported
  broker→SL-only).
- Full suite: **28 failed / 0 errors == baseline, ZERO new** (all 28 in the known non-bitunix files:
  robinhood_multi_leg 15, webhooks_return_fast 5, iron_condor 3, tasty_options_iron_condor 3,
  paper_run_tooling 2). Zero bitunix/SFP/bracket failures.
- PROD surface verified read-only: `build_bracket_legs`, `place_tpsl_order` (L1959), and
  `BitunixUntrackedTpslOrder` all present; `place_tpsl_order` signature matches the call.
- `py_compile` clean on both staged files.

## Apply (code-only, NO restart, SFP stays DISARMED)
**1. UPLOAD** (operator, local PowerShell — ONE line):
`powershell -ep bypass -f .\sfp_tpfix_upload.ps1`
(scp's `staged/` → `~/sfp_tpfix_staged` and the apply script → `~/apply_sfp_tp_fix.sh`, LF-normalized.)

**2. APPLY** (operator — ONE line):
`ssh azureuser@trading.jacksumner.com "bash ~/apply_sfp_tp_fix.sh"`
Gate-A (prod-pre == base) → backup `*.bak-pre-sfp-tpfix-2026-06-26` → atomic swap → Gate-B (post ==
target) → `py_compile`. **Aborts before any write on a md5 mismatch.** No restart. The new code is INERT
on disk until a restart loads it.

## Go-live (SEPARATE, operator-gated — NOT part of this apply)
Do these only when ready to re-arm. **STRICT ORDER — restart BEFORE re-arm:** re-arming
(`auto_execute:true`) WITHOUT first restarting on the new code would run the OLD, TP-less observer armed
= the blocker returns.
1. FLAT check (agent, read-only): no open bitunix position in `paper_trade_record` / at the venue.
2. Restart to load new code (operator): `ssh azureuser@trading.jacksumner.com "sudo -n systemctl restart trading-corp"`
3. Boot smoke (agent, read-only): SFP loop online, broker `paper=False`, reconciler bound to
   `bitunix_sfp` + clean, boot-guard passed, FLAT, no tracebacks; new `main.py`/observer loaded.
4. Re-arm (operator, hot, no restart): flip `bitunix_sfp.auto_execute: false → true` in
   `config/strategies.yaml` (block-scoped; `_yaml_auto_execute()` fresh-reads per signal). Backup first.

## Live-validation (first re-armed SFP→BOS trade — keep DISARMED until this passes)
On the first live SFP entry, confirm:
1. **TP rests with an id** — `extra_json.bracket_tp_order_id` is a non-empty venue id (and a
   `sfp_bracket_placed` audit fired); no `sfp_tp_*` failure audit/Telegram.
2. **OCO works** — when price hits 2R the position closes via the TP leg AND the B1 stop auto-cancels
   with NO orphan stop left resting.
3. **Auto-book books the win** — the reconciler/auto-book records `result=win` at ~2R (not a stop-out).

## Rollback
Pre-restart (code only changed on disk, engine still on old code): restore the two
`*.bak-pre-sfp-tpfix-2026-06-26` files — no restart needed, nothing was loaded.
Post-restart: restore the two backups + `sudo -n systemctl restart trading-corp`. SFP was disarmed, so
no trade can have used the new path before go-live.
