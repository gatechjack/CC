# PLAN — bitunix /tpsl/ TP-leg-fix redeploy (2026-06-19) — PREPARE ONLY

**Status:** package PREPARED. NO prod write / NO deploy / NO restart performed. Agent SSH read-only
(82fda13) used only for the drift check. Execution is a separate operator-gated step.

## Why
Section-B (report `c8a426d`, live trade `cb6b4d4a`) found `place_tpsl_order` crashed all 3 TP legs
with `AttributeError: 'list' object has no attribute 'get'` — it did `(data or {}).get("orderId")` but
the live `/tpsl/place_order` returns a **LIST** (`[{"orderId": ...}]`) while the docs show a dict.
`legs_placed=0` on every multi-leg entry. Fix `8d3d164`: `_extract_tpsl_order_id` parses both shapes;
`place_tpsl_order` raises `BitunixUntrackedTpslOrder` (→ observer `bracket_tp_leg_untracked` audit)
when a POST is accepted but no id is parsed, so a maybe-resting leg is never silently dropped.

**Urgency = CORRECTNESS, not P&L.** The bracket should place tracked TP legs as designed; the fee/stop
findings show managed exits don't change the bear-regime economics, so this is not urgent against P&L.
Fail-soft holds either way (B1 entry MARKET stop + the managed Position SL guard the position).

## Deploy set (derived from the `626e959..8d3d164` diff — NOT assumed)
`git diff --name-status 8d3d164^ 8d3d164` → 4 files; the 3 **deployable prod** files are the set:

| File | role in the fix |
|---|---|
| `trading_corp/brokers/bitunix.py` | `_extract_tpsl_order_id` helper + defensive parse + raises `BitunixUntrackedTpslOrder` |
| `trading_corp/brokers/bitunix_exceptions.py` | **new** `class BitunixUntrackedTpslOrder` |
| `trading_corp/agents/divisions/bitunix_futures_observer.py` | leg-loop catch + `bracket_tp_leg_untracked` audit |

- **`bitunix_exceptions.py` MUST ship** — both `bitunix.py` and the observer now `import
  BitunixUntrackedTpslOrder` from it; shipping the two without it would `ImportError` at load.
- **NOT** `bitunix_position_reconciler.py` (unchanged by `8d3d164`; stays `707c6828`).
- **Excluded:** `tests/test_bitunix_tpsl_rebuild.py` (not deployed). **Forbidden (none present):**
  `main.py` / `db.py` / `models.py` / `logger.py` / `data_exec.py` / cutover / polymarket.

## md5 table + drift result
Base = the DEPLOYED 2026-06-18 `/tpsl/` rebuild state. Target = `8d3d164` LF blobs.
Prod-current pulled read-only 2026-06-19 (PID 2988577 active/running).

| File | deployed-base | prod-current | drift | target (`8d3d164`) |
|---|---|---|---|---|
| `brokers/bitunix.py` | `74aa1b42…098348` | `74aa1b42…098348` | **none** | `00bd03a8…23eff9` |
| `brokers/bitunix_exceptions.py` | `363b044e…96d14` | `363b044e…96d14` | **none** | `62ddd11c…b396c` |
| `agents/divisions/bitunix_futures_observer.py` | `19da15ff…3d59a0` | `19da15ff…3d59a0` | **none** | `f167e456…da5c1` |

**DRIFT RESULT: prod-current == deployed-base for all 3 → NO DRIFT.** Clean to apply. The base md5s for
bitunix.py/observer match the rebuild's deployed VERIFY values, and `bitunix_exceptions.py` prod ==
`626e959` blob (`363b044e`) confirms the `8d3d164` diff is the correct prod transition. Staged tree md5
== target, pure LF (verified). NOT-in-set baselines for VERIFY-A3: main.py `f16e9c24…4e23`, db.py
`a2c2ff46…962c`.

## Apply (operator-gated; `deploy_apply_tpsl_legfix_2026-06-19.sh`)
Drift-gated, targeted, **NO restart in the script**:
1. staged == target (md5) → 2. preflight `py_compile` staged → 3. drift guard (prod == deployed-rebuild
base; ABORT on any drift) → 4. backup `*.bak-pre-tpsl-legfix-2026-06-19` → 5. md5-gated atomic mv →
6. post-mv md5 == target → 7. final `py_compile`.

## Operator remote-mobile flow
1. **Agent** (over SSH, §4-authorized for the sequence): deliver the staged tree to
   `$BASE/_tpsl_legfix_stage` (scp the `stage/` subtree) → stream-run the apply script
   (`Get-Content deploy_apply_tpsl_legfix_2026-06-19.sh -Raw | ssh … "tr -d '\r'|bash"`).
2. **Operator** runs the ONE restart (this box's ssh+sudo does not work; run-command is root/no-pw):
   `az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "systemctl restart trading-corp"`
   Prefer to restart while **flat** (the restart bounces the live bitunix division through a brief flat
   window; correctness-not-urgent, so there is no rush).
3. **Agent** runs `VERIFY.md` A (at restart) and B (on the next live ≥0.0012 BTC multi-leg entry).

## Rollback
`mv` the 3 `*.bak-pre-tpsl-legfix-2026-06-19` files back + operator restart → returns to the current
(TP-legs-untracked-but-fail-soft, SL-only) state. Backups are made by the apply script before any mv.

## Flags (NOT resolved here)
- **`place_position_tpsl` carries the same latent `(data).get` parse pattern** — left UNCHANGED because
  it PASSED live as a dict (Section B #2) and is a hard-stop no-touch. Follow-up, not in this package.
- This redeploy lands the fix on top of the already-deployed rebuild; it does NOT address the unrelated
  open items (P2 result-sign bug; the orphan/managed-exit systemic bug).
