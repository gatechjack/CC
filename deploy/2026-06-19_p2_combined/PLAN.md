# PLAN — P2 combined redeploy (2026-06-19) — PREPARE ONLY

**Status:** PREPARED. NO prod write / NO deploy / NO restart. Agent SSH read-only (82fda13) used only for
the drift re-confirm. Execution is operator-gated.

## What ships
The P2 delta (classifier fix + maker/taker recording) stacked on the already-live /tpsl/ TP-leg legfix,
to branch tip `d83e877`. **CODE-ONLY, 5 files.** The `mc_a_yellow_x` config change is **DEFERRED** (separate
operator config-edit — see below). The record-correction SQL is a separate operator step.

## Deploy set — 5 files (diff `8d3d164..d83e877`, the deployed-legfix → tip)
| File | which fix | which fix added it |
|---|---|---|
| `trading_corp/brokers/bitunix.py` | maker/taker (roleType, FillEvent.role threading) | P2 (on top of legfix) |
| `trading_corp/agents/divisions/bitunix_futures_observer.py` | `$.entry_role` stamp | P2 |
| `trading_corp/agents/divisions/bitunix_position_reconciler.py` | classifier (result/exit_kind), role mix | P2 |
| `trading_corp/agents/divisions/bitunix_bracket.py` | `classify_result` / `classify_exit_kind` (pure) | P2 |
| `trading_corp/persistence/models.py` | `FillEvent.role` (additive) | P2 — **§4 override, coupled w/ bitunix.py** |

**Excluded:** `bitunix_exceptions.py` (prod already at target `62ddd11c` from the legfix — no change).
**NOT in set / forbidden:** main.py, db.py, cutover, polymarket, **config/strategies.yaml** (deferred).

## md5 table + drift result
Base = the DEPLOYED legfix state (`8d3d164` blobs). Target = `d83e877` LF blobs. Prod re-confirmed
read-only 2026-06-19 (PID 3046486) — **prod-current == base for all 5 → NO DRIFT.**

| File | base = prod-current | target (`d83e877`) |
|---|---|---|
| bitunix.py | `00bd03a8…23eff9` | `3f68473a…1c280` |
| observer | `f167e456…da5c1` | `a31a10f1…42f8` |
| reconciler | `707c6828…f94e00` | `bd06ea28…e9c0a` |
| bitunix_bracket.py | `bd639224…ce0a5b` | `f4be4e9b…2c56` |
| models.py | `a781b495…cd5f5` | `d7561d3c…9c4ce` |

Staged tree md5 == target, **pure LF** (CR-byte count 0 — an earlier `grep -c $'\r'` over-reported; the
blobs are LF, confirmed by `tr -cd '\r' | wc -c` == 0 and asis-md5 == LF-md5). VERIFY-A3 baselines: main.py
`f16e9c24`, db.py `a2c2ff46`, strategies.yaml **`569c38f8` (must stay untouched)**.

## models.py — one-time §4 Board-override (reasoning recorded)
`models.py` is normally forbidden, but the tip `bitunix.py` **requires** it: it constructs `FillEvent(role=…)`
and `_observe_fill` returns a 5-tuple, so deploying `bitunix.py` without the tip `models.py` → `TypeError`
on the next fill. The change is **additive-only** (`role: str = ""`; verified the legfix..tip models.py diff is
exactly that one field + comment), backward-compatible (paper/robinhood/tasty/coinbase/fidelity constructors
omit it → default `""`; full regression 28F+3E == baseline, 0 new). The override is **conditioned on it being
safely additive** — VERIFY-A4 confirms no `FillEvent`/`role` binding error on the first post-deploy fill.
`bitunix.py` + `models.py` **ship COUPLED**; the apply script's coupling guard refuses to apply one without the
other.

## config DEFERRED (NOT in this package)
Prod `config/strategies.yaml` = `569c38f8`, which has DRIFTED from the repo branch config (`64c4bc79`/tip
`11a3cfad`) — it carries live operator settings (execution_mode, DD-cap 0.99, kalshi divisions). A full-file
deploy would clobber them. The `mc_a_yellow_x` removal is immaterial to the result/exit_kind skew (the
diagnosis said so), so it is deferred to a **separate, targeted one-line operator config-edit** against prod's
actual `569c38f8` (e.g. via az run-command), later. **This package touches ZERO config files.**

## Apply (operator-gated; `deploy_apply_p2_combined_2026-06-19.sh`)
Drift-gated, targeted, **NO restart**: staged==target → no-config assert → preflight `py_compile` → drift
guard (prod==legfix base; ABORT on drift) → **coupling guard (bitunix.py+models.py together)** → backup
`*.bak-pre-p2-combined-2026-06-19` → md5-gated atomic mv (5) → post-mv md5 == target → final `py_compile`.

## Operator remote-mobile flow
1. **Agent** (SSH, §4-authorized): deliver the staged tree to `$BASE/_p2_combined_stage` → stream-run the
   apply script.
2. **Operator** runs the ONE restart (az run-command, root/no-pw; prefer flat):
   `az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "systemctl restart trading-corp"`
3. **Agent** runs `VERIFY.md` A (at restart) + B (next live trade).

## Rollback
`mv` the 5 `*.bak-pre-p2-combined-2026-06-19` files back + operator restart → returns to the legfix state.

## Flagged follow-ups (NOT in this package)
- `mc_a_yellow_x` config edit (targeted, separate).
- Record-correction SQL `deploy/2026-06-19_p2_record_correction/` (operator runs independently).
- `place_position_tpsl` + `exit_method='server_side_sl_B1'` latent same-pattern hard-codes.
