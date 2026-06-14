# Polymarket OP-track facts + deploy prep (read-only)

Date: 2026-06-14 · HEAD `72e8dc6` (E1·6+E1·7 merge) · all facts verified by direct
file reads. Agent role: **read-only** — surface facts + prep operator-run scripts.
No keys/funds/signing/approvals/prod-writes/on-chain-writes performed.

---

## OP·A — Key Vault secret names (PCT wallet)

The env-var names are translated to KV secret names by replacing `_`→`-`
(`secrets.py:295`: `kv_name = env_name.replace("_", "-")`). So the operator sets,
in vault **`kv-tc-vtwbowt3wtkpy`** (`https://kv-tc-vtwbowt3wtkpy.vault.azure.net/`):

| Purpose | env var (code) | **KV secret name (set this)** |
|---|---|---|
| PCT signer key | `POLYMARKET_COPY_PRIVATE_KEY` | **`POLYMARKET-COPY-PRIVATE-KEY`** |
| PCT funder addr | `POLYMARKET_COPY_FUNDER_ADDRESS` | **`POLYMARKET-COPY-FUNDER-ADDRESS`** |
| Polygon RPC (shared) | `POLYGON_RPC_URL` | `POLYGON-RPC-URL` (already exists; arb uses it) |

- **Which division resolves them:** division slug **`polymarket_copy_trading`** (PCT),
  via the explicit map `_POLYMARKET_WALLET_ENV` (`secrets.py:114-117`):
  `"polymarket_copy_trading": ("POLYMARKET_COPY_PRIVATE_KEY", "POLYMARKET_COPY_FUNDER_ADDRESS")`.
- **Distinct from legacy arb:** yes. Arb slug `polymarket_arbitrage` →
  `POLYMARKET_PRIVATE_KEY` / `POLYMARKET_FUNDER_ADDRESS` (KV `POLYMARKET-PRIVATE-KEY` /
  `POLYMARKET-FUNDER-ADDRESS`). The two wallets are fully separate; arb keeps its legacy
  names (migration option (i), no KV churn). RPC URL is **shared**, not per-division.
- **Wallet model:** `PolymarketWallet` is `signature_type=EOA, funder == signer`
  (`secrets.py:97-104`) — the funder address is the public address of the private key.
- The two COPY names are already on the redact list (`secrets.py:41-42`) and the
  `expected_env_vars` KV-pull list (`secrets.py:270-271`), so once set in KV they load
  automatically via managed identity.
- Exact `az` set pattern (mirror the arb one in `docs/Deployment notes.txt:1439-1441`),
  **operator-only** — agent does not run these:
  ```
  az keyvault secret set --vault-name kv-tc-vtwbowt3wtkpy --name POLYMARKET-COPY-PRIVATE-KEY    --value "$PK"   --query name -o tsv
  az keyvault secret set --vault-name kv-tc-vtwbowt3wtkpy --name POLYMARKET-COPY-FUNDER-ADDRESS --value "$ADDR" --query name -o tsv
  ```

---

## OP·C — Contracts to approve (Polygon chain 137)

Verified against the E1·7 preflight constants (`trading_corp/brokers/polymarket_live.py:419-422`)
and the installed `py_clob_client 0.17.5` `get_contract_config(137)`
(`config.py:10-14` std, `:23-27` negRisk). Approvals are signed **from the funder EOA**.

| # | Contract | Address | Approval type | Spender / operator |
|---|---|---|---|---|
| 1 | USDC.e (collateral, ERC-20) | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` | `approve(spender, max)` | **CTF Exchange (std)** `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` |
| 2 | USDC.e (collateral, ERC-20) | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` | `approve(spender, max)` | **NegRisk CTF Exchange** `0xC5d563A36AE78145C45a50134d48A1215220f80a` |
| 3 | Conditional Tokens / CTF (ERC-1155) | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` | `setApprovalForAll(operator, true)` | **CTF Exchange (std)** `0x4bFb…982E` |
| 4 | Conditional Tokens / CTF (ERC-1155) | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` | `setApprovalForAll(operator, true)` | **NegRisk CTF Exchange** `0xC5d5…f80a` |
| 5 | **NegRisk Adapter** (carry-forward) | `0x78769D50Be1763ed1CA0D5E878D93f05aabff29e` | likely `approve` (USDC.e) **and** `setApprovalForAll` (CTF) | **NegRisk Adapter** as spender/operator |

Notes:
- The repo constant `_USDC_E` is `0x2791Bca1f2de4661eD88A30C99A7a9449Aa84174` — same
  address, non-EIP-55 case; the broker lowercases it for `eth_call`, so casing is
  functionally irrelevant. Use the checksummed form above when approving in a wallet UI.
- **Preflight checks exactly 4** of these (`polymarket_live.py:561-577`): 2× ERC-20
  allowance (USDC.e → std, → negRisk) + 2× ERC-1155 isApprovedForAll (CTF → std, → negRisk).
- **NegRisk Adapter (#5) is NOT checked by preflight** — explicit carry-forward NOTE at
  `polymarket_live.py:578-582` ("MAY also need approval … flagged UNCERTAIN in the 05-29
  spike; not in the 0.17.5 ContractConfig … not asserted to avoid a false-abort"). It is
  not a field in `py_clob_client 0.17.5`'s `ContractConfig` (only `exchange`, `collateral`,
  `conditional_tokens`). **Recommendation:** Polymarket's own onboarding approves the
  NegRisk Adapter too, so the operator should very likely do the full canonical set
  (6 approvals: USDC.e→{std,negRisk,adapter} + CTF→{std,negRisk,adapter}). Preflight passing
  does **not** prove the adapter is approved — confirm at the $1 shakedown (OP·E). The
  state-check script reports the adapter's current state for visibility.

---

## Lockfile deploy — facts + ⚠️ fork

**Pinned files (in git, NOT installed on prod):**
- `requirements.lock` (repo root) — the **hash-pinned** install target
  (`uv pip compile --generate-hashes`, py3.12 / linux-x86_64; every entry has
  `--hash=sha256:`). E1 pins: `py-clob-client==0.17.5`, `py-order-utils==0.3.2`,
  `web3==6.11.0`, `eth-account==0.13.1` (+ eth-* transitives, pycryptodome, ckzg, etc.).
- `requirements.txt` (repo root) — loose human spec (E1 block at lines 59-70); **not** the
  `--require-hashes` target.

**⚠️ FORK — the lock is a FULL-ENVIRONMENT lock, not E1-only.** It was built from the prod
`pip freeze` (137 packages, exact-pinned) with the 4 E1 packages appended
(`…/e1_lock_input.txt:1-141`). So `pip install --require-hashes -r requirements.lock`
operates on the *entire* venv (anthropic, ccxt, robin-stocks, tastytrade, langgraph, …),
not just the Polymarket deps.
- **Intended to be additive:** because every existing package is pinned to its
  already-installed version, applying it *should* add only the E1 packages + new
  transitives and leave existing versions untouched — **iff prod still matches that freeze.**
- **Must verify first (read-only):** run `pm_e1_lock_diff.py` on prod. Expected result:
  a list of NEW packages, **CHANGED = 0**. Any CHANGED entry = prod drifted → STOP.
  The deploy script re-runs this guard and **aborts** if any installed package would change.

**Prod facts** (from `runbooks/deploy_log.md`): unit `trading-corp.service`; venv
`/home/azureuser/trading_corp/venv/` (Python 3.12.13); install cmd
`venv/bin/pip install --require-hashes -r requirements.lock`.

**Restart required?** To make `py_clob_client` importable by the **running** process: yes —
a long-running Python process won't reliably hot-load newly installed packages; the safe,
precedent-matching path is `sudo systemctl restart trading-corp.service`. **But:**
- The **pip install itself is non-disruptive** (writes to venv only; does not touch the
  running process) — safe to run anytime.
- A restart **bounces ALL live divisions** (Bitunix is live in prod) — must be timed for a
  flat/quiet window, not done blindly.
- **PCT is still `broker: paper` (dormant)** and the broker imports py_clob_client lazily,
  so there is **no need to restart now**. Install the deps now (additive, safe), and defer
  the restart to the PCT live cutover (E2). The deploy script does **not** restart.

---

## Read-only state check (current provisioning)

I cannot determine live state from here without prod + KV + chain access (correctly — I
don't hold the funder/RPC secrets). The operator runs `pm_copy_state_check.py` (read-only;
never reads the private-key value, never prints RPC/PK) to get:
- OP·A: KV presence of the 3 secrets (presence-only),
- OP·B: funder USDC.e balance,
- OP·C: the 4 enforced approvals + the NegRisk Adapter (info).

Until then, treat KV-presence / funding / allowances as **UNKNOWN — run the check**.

---

## Operator paste sequence (≤100 chars/line)

From `C:\Users\AA Incorporado\cc\deploy\polymarket_e1`:
```
cd "C:\Users\AA Incorporado\cc\deploy\polymarket_e1"
scp requirements.lock azureuser@trading.jacksumner.com:/tmp/
scp requirements.txt azureuser@trading.jacksumner.com:/tmp/
scp pm_copy_state_check.py azureuser@trading.jacksumner.com:/tmp/
scp pm_e1_lock_diff.py azureuser@trading.jacksumner.com:/tmp/
```
Read-only checks (safe anytime, no service impact):
```
ssh azureuser@trading.jacksumner.com "cd trading_corp;venv/bin/python /tmp/pm_copy_state_check.py"
ssh azureuser@trading.jacksumner.com "cd trading_corp;venv/bin/python /tmp/pm_e1_lock_diff.py"
```
Deploy — ONLY after the diff shows CHANGED=0 and you accept the full-lock apply:
```
gc deploy_e1_lock.sh -Raw|ssh azureuser@trading.jacksumner.com "tr -d '\r'|bash"
```
Restart — deferred; operator-timed, bounces all live divisions:
```
sudo systemctl restart trading-corp.service
```

Lock md5 (LF-normalized staged copies): `requirements.lock`
`4edfca041dad220f54e4e5d3b269a2f1` · `requirements.txt` `2aee61909bc22cf4fdf6f68ca5166fa3`.

## Remaining OP steps (operator-only)
OP·A set 2 KV secrets · OP·B fund funder EOA in **USDC.e** (not native USDC) · OP·C the
4 (likely 6) approvals · deps deploy (script ready) · OP·E $1 shakedown · then E2 (route
copy loop to live broker) + the timed restart.
