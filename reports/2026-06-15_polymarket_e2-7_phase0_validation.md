# E2·7 Phase 0 prep — go/no-go validation (read-only)

Date: 2026-06-15 · Agent role: **read-only** (no prod writes, no installs, no merges).
Validated against local repo `C:\Users\AA Incorporado\cc`, main HEAD `27861f8` (== origin/main).
Scope: the Phase 0 gate of `E2·7 Live-Track Runbook` ("0.3 smoke green, 0.4 CHANGED=0 post-pin,
0.5 verified, prep branch on main").

## VERDICT: **NO-GO** — one hard blocker (0.4 / 0.5)

The deploy artifact the script installs (`deploy/polymarket_e1/requirements.lock`) still pins
`setuptools==82.0.1` — the exact version that breaks `web3 6.11`'s `pkg_resources` import (the
documented deploy blocker). The `setuptools<81` fix was applied only to the **root**
`requirements.lock` (`fe0666a`, → `80.10.2`); it was never propagated to the deploy artifact.
`deploy_e1_lock.sh` installs the stale lock. Do not run Phase 1 until the deploy lock is regenerated.

Everything else checks out. The fix already exists in a clean, byte-promotable form (see remediation).

---

## Per-item checklist

| # | Item | Status | Evidence |
|---|---|---|---|
| 0.1 | Push base to origin | ✅ GO (base drifted) | main `27861f8` == origin/main (in sync, 0 ahead/0 behind). Runbook's base `299b40c` is an **ancestor** of HEAD — main has moved past it (bitunix P2 close-out commits) and is already pushed. The "push 299b40c, 4 ahead" instruction is stale/superseded. |
| 0.2 | Lock artifacts staged + prep branch on main | ⚠️ PARTIAL | `deploy_e1_lock.sh`, `pm_e1_lock_diff.py`, `pm_copy_state_check.py`, `requirements.lock`, `requirements.txt` all live under `deploy/polymarket_e1/` on branch `polymarket-op-track-prep-2026-06-14` (tip `1143ef2`), **unmerged** — as the runbook says. NOTE: the runbook's "requirements.lock on main" conflates two different files — see below. **Do NOT merge the prep branch as-is** (it carries the stale lock + a md5 gate pinned to it). |
| 0.3 | `--require-hashes` smoke (off-prod, linux) | ⛔ PENDING (operator) | Not run. Must be run **against the corrected lock** (md5 `a47fc9…`), not the staged stale one. Can't be exercised from this Windows box. |
| 0.4 | `e1_lock_input.txt` pin update + regen lock, CHANGED=0 | ⛔ **BLOCKER** | The deploy lock still has `setuptools==82.0.1`. `e1_lock_input.txt` is **not in git anywhere** — it is the ephemeral lock-gen input the facts report references (`…/e1_lock_input.txt:1-141`); the pin update it calls for was never carried into the deploy artifact. |
| 0.5 | `setuptools<81` on main + reflected in lock | ⚠️ SPLIT | Root `requirements.lock` (main): `setuptools==80.10.2` ✅. Deploy `requirements.lock` (prep, the install target): `setuptools==82.0.1` ❌. The constraint is honored on the wrong copy. |
| 0.6 | Venv shared + Bitunix unit identified | ✅ documented (live confirm = operator) | From `deploy_e1_lock.sh` + facts report + deploy_log: unit `trading-corp.service`, shared venv `/home/azureuser/trading_corp/venv/` (Python 3.12.13), Bitunix live on it. Not live-verified from here (read-only `systemctl` confirm is the operator's, or I can run a read-only SSH check on request). |

---

## The blocker, in detail

`deploy_e1_lock.sh` installs `$BASE/requirements.lock` after scp from `deploy/polymarket_e1/`.
That staged lock is the **pre-fix** lock:

```
diff  deploy/polymarket_e1/requirements.lock (prep, stale)  -->  requirements.lock (main root, fixed)
3403,3405c3403,3405
< setuptools==82.0.1   + 2 hashes (…7d872682…, …a59e3626…)
---
> setuptools==80.10.2  + 2 hashes (…8b0e9d10…, …95b30ddf…)
```

That is the **only** difference between the two locks (both 4265 lines, 3557 hash lines, all 4 E1
packages present: py-clob-client 0.17.5, py-order-utils 0.3.2, web3 6.11.0, eth-account 0.13.1).

What happens if deployed as-is, either way it's wrong:
- If prod still has setuptools 82.0.1 → `pm_e1_lock_diff.py` shows CHANGED=0, the deploy "succeeds"
  additively, and the broken setuptools 82 survives → `web3` import fails at the cutover restart.
- If prod already has 80.10.2 (root-lock fix deployed) → the additive-only guard sees
  `setuptools: 80.10.2 -> 82.0.1` (a CHANGE) and **aborts** the deploy (exit 3).

md5s (LF-normalized):
- Stale deploy lock: `4edfca041dad220f54e4e5d3b269a2f1` ← what `EXP_LOCK_MD5` currently pins.
- Fixed root lock:  `a47fc93e2103bd4687ac8bd8717759c4` ← what it must become.
- `requirements.txt` (input spec) is **unaffected** — it has no setuptools line; md5
  `2aee61909bc22cf4fdf6f68ca5166fa3` stays. (Staged txt md5 verified == gate ✅.)

---

## Remediation (operator — mechanical, no lock regeneration needed)

The fix already exists as a byte-promotable file; no `uv pip compile` re-run required.

1. Replace `deploy/polymarket_e1/requirements.lock` with the root `requirements.lock` (`fe0666a`).
   They differ only in the setuptools block; promoting the root copy *is* the regenerated lock.
2. In `deploy/polymarket_e1/deploy_e1_lock.sh`, change `EXP_LOCK_MD5`:
   `4edfca041dad220f54e4e5d3b269a2f1` → `a47fc93e2103bd4687ac8bd8717759c4`.
   (`EXP_TXT_MD5` unchanged.)
3. Re-run 0.3 (`--require-hashes` smoke, off-prod linux) against the corrected lock. Abort the
   deploy if it fails — do not debug a hashed-install failure against the live venv.
4. Then 0.4 CHANGED=0 holds for the right reason (setuptools matches the deploy target), and the
   additive guard won't false-abort.

These are repo edits + a merge → operator's per §4 (CLAUDE.md `82fda13`: agent SSH read-only,
operator runs prod writes/installs/restarts/flip). Flagging, not performing.

---

## Runbook precision notes (non-blocking)

- "Base: main `299b40c`" is stale — actual main is `27861f8` (299b40c is an ancestor); already pushed.
- "requirements.lock on main / in git (verified additive…)" conflates the **root** lock (fixed,
  not the install target) with the **deploy** lock (`deploy/polymarket_e1/`, the install target,
  stale). The additive/CHANGED=0 claim was verified against the deploy lock as of 2026-06-14 — but
  that lock carries the setuptools regression.
- The lock is a **full-environment** lock (137 prod-freeze pkgs + 4 E1), not E1-only — so
  CHANGED=0 only holds while prod still matches the 2026-06-14 freeze. Re-run `pm_e1_lock_diff.py`
  fresh on prod before deploy; the deploy script re-guards and aborts on any drift.
