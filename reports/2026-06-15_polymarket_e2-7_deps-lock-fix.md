# E2·7 deps blocker remediation — propagate setuptools<81 fix to the deploy lock

Date: 2026-06-15 · Branch `e2-7-deps-lock-fix-2026-06-15` (off main `27861f8`) · Agent role:
**read-only-prod / repo-edits** per §4 (no installs, no deploys, no restarts, no merge, read-only SSH).
Companion to the Phase 0 validation (branch `polymarket-e2-7-phase0-validation-2026-06-15`).

## What was done (repo edits + verification only)

1. **Materialized the deploy dir on a main-based branch.** `deploy/polymarket_e1/` does **not** exist
   on main — it lives only on the unmerged prep branch `polymarket-op-track-prep-2026-06-14` (`1143ef2`,
   not in main's history). To honor "base = current main `27861f8`," the dir was checked out from prep
   onto this branch (`git checkout <prep> -- deploy/polymarket_e1`). `.gitattributes` (`* text eol=lf`)
   guarantees LF on checkout, so the md5 gates stay valid cross-platform.

2. **Promoted the fixed root lock to the deploy target.** `deploy/polymarket_e1/requirements.lock`
   replaced with the content of root `requirements.lock` (`fe0666a`, the `setuptools<81` fix). Pre-promotion
   assertion (Step 1 of the remediation) passed: both locks 4265 lines, all 4 E1 packages present, and the
   diff was **exactly** the setuptools block (lines 3403–3405) — nothing else. Post-promotion the deploy
   lock is byte-identical to the root lock (`diff` exit 0).

3. **Bumped the gate.** `EXP_LOCK_MD5` in `deploy_e1_lock.sh`:
   `4edfca041dad220f54e4e5d3b269a2f1` → `a47fc93e2103bd4687ac8bd8717759c4`. `EXP_TXT_MD5` unchanged.

**Verification (not on faith):**
- deploy lock LF md5 = `a47fc93e2103bd4687ac8bd8717759c4`; `setuptools==80.10.2`.
- script `EXP_LOCK_MD5` == deploy lock md5 ✅; script `EXP_TXT_MD5` == deploy txt md5 (`2aee619…`) ✅.

## ⚠️ Step 4 — the additive guard now ABORTS on the intended downgrade (operator decision required)

The fix that makes the lock correct also makes it trip `deploy_e1_lock.sh`'s additive-only guard
(and `pm_e1_lock_diff.py`). The guard aborts (exit 3) if **any** installed package would change version.

**Baseline:** the lock was built from prod's `pip freeze` (137 pkgs) + 4 E1 pkgs appended; the original
lock pinned `setuptools==82.0.1`, i.e. **prod currently has setuptools 82.0.1 installed** (the deps were
never deployed; the fix is repo-only). Against the corrected lock:

| package | installed (prod) | corrected lock | guard verdict |
|---|---|---|---|
| setuptools | 82.0.1 | **80.10.2** | **CHANGED (downgrade) → ABORT** |
| py-clob-client + eth-* transitives | (absent) | new | NEW — additive, fine |
| all other 136 freeze pkgs | matches | matches | same |

So `pm_e1_lock_diff.py` will report **CHANGED=1** (setuptools 82.0.1→80.10.2), not 0, and the deploy
script will abort before installing. The downgrade is **intended**; the guard just can't tell it apart
from unexpected drift. It must be handled so it **neither silently passes a stale lock nor blocks the
intended downgrade.**

### Handling options
- **A (recommended) — narrow whitelist in the guard.** Teach the guard to allow exactly
  `setuptools: 82.0.1 -> 80.10.2` (an explicit, auditable exception) while **any other** CHANGED package
  still aborts. One gated run; drift-detection preserved for everything else; the exception lives in the
  deploy artifact. Cost: a small behavioral edit to the guard (must be precise) — a deploy-safety change,
  so it's the operator's call to approve. **Implemented 2026-06-15 (operator-directed) — see Update below.**
- **B — operator pre-downgrades setuptools, then deploy normally.** `pip install --require-hashes`
  setuptools 80.10.2 first (use the lock's hashes); then prod == lock, guard sees CHANGED=0, deploy is
  fully additive. No guard code change. Cost: an extra venv mutation outside the gated atomic flow, to be
  sequenced into the flat window.
- **Rejected:** bypassing the guard entirely (loses drift detection — the exact protection that's wanted),
  or reverting the lock to 82.0.1 (reintroduces the web3 6.11 `pkg_resources` breaker).

**Recommendation:** Option A — **implemented** (operator-directed, 2026-06-15). Details in Update below.

## Update (2026-06-15) — Option A implemented: scoped additive-guard exception

Encoded a **named, exact-tuple** exception in **both** copies of the guard (they're separate: the
read-only `pm_e1_lock_diff.py` and `deploy_e1_lock.sh`'s self-contained inline heredoc that runs on prod):

```
ALLOWED_CHANGES = {("setuptools", "82.0.1", "80.10.2")}   # web3 6.11 pkg_resources fix (fe0666a), one-time
```

- **Scoped, not blanket.** Only the exact `(name, from, to)` transition is allowed. A setuptools change
  with a different prod baseline (≠82.0.1) or different target (≠80.10.2) does **not** match → stays in
  `changed` → still aborts. Any other CHANGED package still aborts unchanged.
- **Loud.** The deploy guard prints `ALLOWED EXCEPTION (web3 6.11 pkg_resources fix, scoped): setuptools:
  82.0.1 -> 80.10.2` and an `ADDITIVE OK — N new, 1 allowed exception(s), 0 unexpected changes` summary, so
  the exception is visible in deploy output and never silent.
- **`pm_e1_lock_diff.py` refactored** to a pure, testable `classify()`; its I/O moved under `__main__`
  (import is now side-effect-free). Its output now shows the setuptools line under "ALLOWED EXCEPTIONS"
  rather than a false NON-ADDITIVE/STOP.
- **Tested** — `tests/test_e1_lock_additive_guard.py`, 8 cases, all pass:
  5 against `classify` (allowed downgrade passes; unrelated CHANGED aborts; non-matching baseline aborts;
  non-matching target aborts; allowlist is an exact 1-tuple) + 3 that execute the **actual
  `deploy_e1_lock.sh` heredoc bytes** asserting exit 0 on the allowed downgrade and exit 3 on a real change
  / non-matching setuptools (catches divergence between the two copies).
- **md5 gate unchanged** — these edits don't touch `requirements.lock`; `EXP_LOCK_MD5` still == the deploy
  lock's md5 (`a47fc93e…`), re-verified.

**Net:** the branch is now self-consistent and deployable through the gated script in a single run; the
intended downgrade no longer trips the guard, and drift detection is fully preserved for everything else.
Still operator-gated: 0.3 smoke, install, restart, flip, and the merge.

**Verify-at-deploy:** confirm prod's live setuptools (read-only) before deploy —
`venv/bin/python -c "import setuptools;print(setuptools.__version__)"`. Expected 82.0.1 (→ guard handling
needed). If it already reads 80.10.2, CHANGED=0 and no handling is needed.

## Branch / merge topology (operator)
- This branch carries the **corrected** `deploy/polymarket_e1/` (6 files) on a current-main base. Merging
  it lands the corrected deploy artifacts on main **and** satisfies runbook 0.2 ("prep branch on main") for
  the deploy dir in one unit.
- It **supersedes the prep branch's `deploy/` dir** (stale lock + old md5). Do **not** also merge prep's
  `deploy/` — it would conflict. The prep branch's `reports/2026-06-14_..._facts.md` and the facts content
  are not on this branch; cherry-pick/merge separately if you want them on main (no conflict — this branch
  doesn't touch that path).

## Out of scope (operator-gated)
- 0.3 `--require-hashes` smoke — **drafted** as `deploy/polymarket_e1/smoke_e1_lock.sh` (ready to run),
  but EXECUTION is operator-only: the lock is hash-pinned for `x86_64-linux / py3.12` (lock header), so it
  can't run on the agent's Windows/py3.14 box, and the only linux box in reach is prod (agent SSH is
  read-only — no venv-create / pip-install access). Operator runs it on prod-in-a-scratch-dir or a
  throwaway linux box: `bash smoke_e1_lock.sh [lock]` — fresh empty venv, hashed install, asserts the 4 E1
  pkgs + setuptools 80.10.2, then imports web3+pkg_resources (the proof the downgrade fixes the blocker),
  tears down. Touches only an mktemp scratch dir; never the prod venv/service.
- The install, the single restart, the live flip, and merging this branch. All §4 operator steps.
