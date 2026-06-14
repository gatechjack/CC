# E1·1 — Polymarket live-SDK deps pin + prod-target signing re-spike

**Date:** 2026-06-13
**Branch:** `polymarket-e1-1-deps-respike-2026-06-13` (base `main` `1327764`)
**Mode:** BUILD + VALIDATE on a branch, **UNMERGED**. No deploy (the lockfile is a prod-surface
change — operator-gated, separate). No prod write. No real wallet key, no funds. Disclosure per
`82fda13`.
**Increment:** E1·1 of the E1 build plan (first slice). Adds the live Polymarket SDK deps (pinned)
+ regenerates the prod-target lockfile, **and re-proves EIP-712 signing on the pinned/prod-Python
deps** before anything builds on it. (E1·2+ — the broker mapping/place/cancel — are separate.)

---

## Outcome

**E1·1 passes.** The proven pin set resolves cleanly for the prod target (py3.12/x86_64-linux)
with **zero churn** to prod's existing pins, and re-produces a valid EIP-712 signature on
py3.12. One residual delta (Linux OS for the signing RUN) is carried forward, low-risk, closed at
the deploy smoke — recorded in §Carried-forward so it isn't dropped.

---

## Phase A — pin deps + cross-compile the py3.12/Linux lockfile

**Added to `requirements.txt`** (proven set — matches Polymarket's own MIT `agents` framework +
the 2026-05-29 spike; py-clob-client repo is archived/read-only but the pin works):
```
py-clob-client==0.17.5
py-order-utils==0.3.2
web3==6.11.0
eth-account==0.13.1
```
Construct `ClobClient` with **kwargs always** — the constructor arg order differs across versions
(latest `0.34.6` reorders `host`/`chain_id`/`key`; `0.17.5` is `host, chain_id, key`).

**Lockfile regen (`requirements.lock`)** — followed the documented rule
(`runbooks/session_start_2026_05_24_post_lockfile_correction.md`, memory
`feedback-lockfile-regen-from-running-state`): **compile from prod's pinned freeze, NOT from
`requirements.txt` against PyPI** (the latter once silently bumped 43 packages). Method:
1. Built a fully-pinned input = the **137 existing prod pins** (extracted from `requirements.lock`,
   which is the disk≡lock≡process converged set; June deploys were code-only, no dep drift) **+ the
   4 new pins**.
2. `uv pip compile tmp/e1_lock_input.txt --python-version 3.12 --python-platform
   x86_64-unknown-linux-gnu --generate-hashes -o requirements.lock` (uv 0.11.7). `tmp/` input is
   gitignored (not committed — same convention as the prior lock's tmp freeze input).

**Additions-only — verified against `HEAD`:** `OLD=137 → NEW=160`, **ADDED=23, REMOVED=0,
CHANGED=0**. The new deps force **no bump or removal** of any existing prod pin (the silent-churn
failure mode is absent). The 23 additions are the py-clob-client + web3 + eth-* + crypto subtree:
`py-clob-client 0.17.5, py-order-utils 0.3.2, web3 6.11.0, eth-account 0.13.1, eth-abi 5.2.0,
eth-hash 0.8.0, eth-keys 0.7.0, eth-keyfile 0.9.1, eth-rlp 2.2.0, eth-typing 6.0.0, eth-utils 6.0.0,
hexbytes 1.3.1, rlp 4.1.0, py-ecc 8.0.0, poly-eip712-structs 0.0.1, pycryptodome 3.23.0, ckzg 2.1.7,
bitarray 3.8.1, cytoolz 1.1.0, lru-dict 1.4.1, parsimonious 0.10.0, pyunormalize 17.0.0, regex
2026.5.9`.

**Notable:** `eth-abi` resolved to **stable `5.2.0`**, NOT the `6.0.0b1` beta the 05-29 spike pulled
transitively on py3.14. The beta was a **py3.14 artifact** — on the prod target (py3.12) it does not
appear. The 05-29 "pin the beta transitive" concern is therefore moot; the lockfile pins stable
`eth-abi 5.2.0`.

## Phase B — sign-only re-spike on the pinned/prod-Python deps

Ran the **existing** `scripts/spike_polymarket_signing/spike_sign.py` **unmodified** (zero new
code) in a throwaway **py3.12** venv (`uv venv --python 3.12`; CPython 3.12.13) with the 4 pins
installed (`eth-abi 5.2.0` here too — confirms the beta is gone on 3.12). Ephemeral runtime key
only; **`post_order` never called**.

Result (`SPIKE_EXIT=0`):
- `ClobClient` constructed via **kwargs** (host, chain_id) — OK.
- Live token fetched via `get_sampling_markets()`; `OrderArgs(price=0.50, size=5, BUY)`.
- `create_order()` → **valid signed order**: `makerAmount=2500000` (0.50×5×1e6), `takerAmount=5000000`,
  `signatureType=0` (EOA), `signature=0x4d0918f7…aa761b` (65-byte ECDSA). Arithmetic + shape match
  the 05-29 spike.

**Proves:** the pinned `py_clob_client==0.17.5` + kwargs construction sign correctly on the prod
**Python version (3.12)**.

## Phase C — validation summary

| Check | Result |
|---|---|
| `from py_clob_client.client import ClobClient` / `OrderArgs` on py3.12 | imports OK |
| Lockfile resolves for py3.12/x86_64-linux | `COMPILE_EXIT=0`, 160 pkgs |
| Lockfile additions-only vs prod (no churn) | ADDED=23, REMOVED=0, CHANGED=0 |
| EIP-712 signing on py3.12 (ephemeral key, sign-only) | valid signature, `post_order` not called |

## Carried-forward validation item (do NOT drop)

**The signing RUN was validated on py3.12/Windows, not py3.12/Linux** (no local Linux runtime: WSL
not installed, no Docker; prod is read-only-SSH/no-write). **Residual unproven delta = OS platform
(Linux) for the signing RUN.** Assessed **LOW RISK**: EIP-712 signing is OS-independent pure crypto
(`eth-account`/`coincurve`/`eth-hash` produce deterministic signatures); the pinned set is identical
across OS and `eth-abi` resolves to stable `5.2.0` on both. The Phase A lockfile already proves the
prod-target **resolution** (py3.12/linux). **Final confirmation = the operator-gated prod-deploy
smoke**: a sign-only check on the real Linux box before any live placement (alongside the runbook's
`pip install --dry-run --require-hashes -r requirements.lock` convergence check, which must report
the 23 additions and **zero unexpected** "Would install").

## Phase D — status

- `requirements.txt` (+4 pins) and `requirements.lock` (regenerated, additions-only) committed on
  the branch. Throwaway py3.12 venv + `tmp/e1_lock_input.txt` are scratch (not committed).
- **UNMERGED.** Deploying the lockfile to prod is a **separate operator-gated surgical-deploy** step
  (NOT this session).
- **Hard stops honored:** no code beyond the deps (the spike was run unmodified); ephemeral key
  only; `post_order` never called; no deploy/merge; no prod write; pins resolve + sign on the prod
  Python version.

## Next (separate increments — NOT E1·1)

- **E1·2** — `ProposedOrder → OrderArgs` (token_id from condition_id+outcome_index) + `create_order`
  inside the broker class, mocked/ephemeral, fundless.
- Then E1·3–7 per the plan; operator-only key/funding/allowance + the $1 shakedown last.

---

*E1·1 artifact — committed unmerged on `polymarket-e1-1-deps-respike-2026-06-13`. Builds on the E1
design (`reports/2026-06-13_polymarket_e1_live_broker_design.md`) + the 2026-05-29 spike.*
