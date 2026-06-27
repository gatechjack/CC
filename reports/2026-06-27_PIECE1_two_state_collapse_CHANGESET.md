# Piece 1 — Two-state collapse CHANGESET (for review, 2026-06-27)

**Commit `01a1df9`** on `bitunix-sfp-division-2026-06-25` (worktree `bitunix-sfp-2026-06-25`).
**Built + tested + staged. NOT deployed** (operator-gated, sequenced). Deploys do NOT collapse:
Piece 1 deploy → verify → Piece 2 IP swap → verify → Piece 3 deploy → verify.

## Decisions implemented (all locked decisions)
- **mode = trading | halted; FAIL-SAFE default HALTED** — enforced at the orchestration layer (main.py),
  not in the byte-unchanged SFP files.
- **`bitunix_futures` → `mode: halted`** (HALTED-INERT) and **`bitunix_sfp` → `mode: trading`** (explicit
  opt-in so the fail-safe default does NOT silently halt the live BTC edge on restart).
- **`robinhood_pead` AUDITED — unaffected.** The `mode` flag is **bitunix-scoped**: main.py reads it only
  from the `bitunix_futures` block (`_bx_block`) and the `bitunix_sfp` block (`_sfp_raw`). `robinhood_pead`
  has no `mode` key and is gated by its own `enabled`/`auto_execute` path — no pead config change, it keeps
  running live. (Confirmed: `pead.mode = <<absent>>`; no global mode-gating was introduced.)
- **Replay DISABLED** (operator's call — paper obsolete), retained behind a one-line revert.

## Change surface (4 files changed, 1 new test file)
| File | Change |
|---|---|
| `config/strategies.yaml` | `+2`: `bitunix_futures.mode: halted`, `bitunix_sfp.mode: trading` |
| `trading_corp/main.py` | `+92/-23`: read `mode` (fail-safe halted default outside the try); `_futures_halted`/`_sfp_trading`; pass `halted=_futures_halted` to the futures observer ctor; gate the SFP 15m loop start on `mode==trading` (+ explicit HALTED-INERT log otherwise); gate the pa-redeem loop on `not _futures_halted`; **disable replay** (boot catch-up + periodic loop + live-exit executor) behind `_REPLAY_ENABLED = False`; guard the shutdown cancel for `replay_task=None` |
| `trading_corp/agents/divisions/bitunix_futures_observer.py` | `+21`: `halted` ctor param (default **False** = back-compat for existing tests) + `self._halted`; short-circuit guards at `observe_alert`, `observe_and_decide`, `run_pa_redeem_loop` (inert BEFORE scoring / `would_have_placed` / `paper_trade_record`) |
| `tests/test_boot_smoke.py` | `+37`: boot guard asserts shipped YAML + the **exact** main.py predicates → SFP arms, futures halts; and main.py source actually gates the SFP loop on `_sfp_trading`, passes `halted=`, and ships `_REPLAY_ENABLED = False` |
| `tests/test_bitunix_two_state_collapse.py` | **new**: short-circuit proofs (`_observe_alert_inner` never reached; no ledger append; `insert_paper_trade_record` never called; pa-redeem never fires) + fail-safe mode predicate table + shipped-YAML contract |

## Hard-constraint proofs
- **★ SFP byte-UNCHANGED** (diff vs HEAD = identical): `bitunix_sfp_observer.py`, `bitunix_sfp.py`,
  `bitunix_position_reconciler.py`. The SFP loop gate lives entirely in main.py (reads `mode` from raw YAML,
  not via `BitunixSfpConfig`), so the live path is provably untouched. Re-verify md5 vs PROD at deploy.
- **★ Boot smoke asserts the live edge survives the fail-safe default**: `test_boot_smoke.py ::
  test_two_state_sfp_comes_up_trading_and_replay_disabled` — SFP `mode=trading` + loop gated on it, futures
  halted, replay off. This is the guard you asked for.
- **Full suite == baseline**: **28 failed, 2854 passed, 17 skipped, 0 errors** (`pytest`, ~6m37s). The 28
  failures are the pre-existing baseline, ALL in files I did not touch (iron_condor / robinhood_multi_leg /
  tasty_options / webhooks_return_fast / paper_run_tooling). Two spot-checked as environment/fixture, not my
  change: webhooks `'_Deps' object has no attribute 'bitunix_observer'` (test stub), paper_run_tooling
  `no such table: agent_state` (uninitialised DB). **0 new failures, 0 errors.**
- New + targeted tests: **53/53 pass** (two-state + boot smoke + execution_mode regression).

## What HALTED-INERT does at runtime (futures), and what stays
- Futures observer is still **constructed** (loaded, flip-ready) but its 3 entry paths return immediately →
  no scoring, no `would_have_placed`, no `paper_trade_record`, no ledger append. pa-redeem loop not started.
- Replay loop + boot catch-up + live-exit fork OFF → **zero Bitunix historical-kline traffic from replay**
  (the IP-flag burst on egress restore is removed before Piece 2). Also removes the latent replay live-exit
  fork that could otherwise race SFP's venue brackets (net safety win).
- **Untouched & still running** (SFP needs them): `paper_trade_record` table, the reconciler, and ALL
  `LiveBarCache` poll tasks (3m/HTF/capture). SFP 15m loop runs (mode: trading).

## Deferred (flag for your call) — dashboard HALTED-INERT badge
Deliberately **NOT** in this changeset. Rationale: `bitunix_futures` was already **paper, never live**, so the
"misread frozen paper panels as live" risk is minimal; the badge touches the **5419-line shared `web/data.py`**,
adding review surface + regression risk to an otherwise surgical, fully-proven Piece 1. **Options:** (a) tiny
isolated follow-up commit now, or (b) fold into Piece 3's dashboard work. Your call — say the word.

## Deploy notes (for when you deploy Piece 1)
- **★ Drift-gate must PRESERVE prod's `bitunix_sfp.execution_mode: live`.** The worktree ships
  `execution_mode: paper` (branch default); prod is `live`. `mode` (trading/halted) is INDEPENDENT of
  `execution_mode` (paper/live). The staged config for prod must be `bitunix_sfp: {mode: trading,
  execution_mode: live}` and `bitunix_futures: {mode: halted}` — confirm against the live prod YAML before restart.
- Piece 1 is a **code+config change → engine RESTART**. Safe now (SFP is blind+flat). On restart: futures
  loads halted, SFP loop spawns (mode: trading), reconciler + bar-cache up, **no** replay task, **no**
  pa-redeem task. Drift-gate vs PROD md5 (main.py / futures observer / strategies.yaml); md5 the 3
  byte-unchanged files == PROD.
- Bitunix egress after Piece 1 restart = live `LiveBarCache` polls only (still 403 until Piece 2 IP swap).

## Status / next
- **Piece 1: DONE — awaiting your review** (this doc + `git show 01a1df9`).
- **Piece 2:** staged Azure NAT-gw runbook (`98cffc1`), no code — your op step after Piece 1 verify.
- **Piece 3 (ws hybrid):** NOT started — per your instruction I hold the deep build until you've reviewed
  Piece 1. It remains draft-until-Piece-2-lands.
