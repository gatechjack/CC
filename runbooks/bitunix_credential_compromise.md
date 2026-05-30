# BitUnix Credential Compromise Runbook

**Last verified:** 2026-05-30, code surfaces at `origin/main` commit `03f3261`.
Prior bitunix rotation reference: `runbooks/deploy_log.md` §
"2026-05-29 ~19:06 UTC — C-1 bitunix futures KV credential rotation".

**Owner:** operator performs the credential-value steps by hand; this runbook
documents what they do, not the values. **No API key, no API secret, no Azure
Key Vault secret VALUE may be pasted into chat, into commits, into deploy_log
entries, or into this runbook.** Names, paths, vault names, secret names, and
commands only. Standing rule per `[[feedback-secret-never-touches-claude-code]]`.

**Companion runbook:** `runbooks/bitunix_panic_halt.md` documents the halt +
flatten primitives; this runbook cross-references it for the immediate-isolation
step. Read both before acting if the trigger is ambiguous.

---

## What this is / when to use

The procedure for handling **suspected or confirmed compromise of the BitUnix
futures API credentials** (`BITUNIX_FUTURES_API_KEY` + `BITUNIX_FUTURES_API_SECRET`,
KV vault `kv-tc-vtwbowt3wtkpy` secret names `BITUNIX-FUTURES-API-KEY` /
`BITUNIX-FUTURES-API-SECRET`).

A compromise is anything that means the credential pair could be used by
someone other than the running prod service. Sources observed historically:
test-failure leak via `assert x in collection` repr (designed out per
`[[no-membership-assert-on-secret-collections]]`); transcript-paste mistake;
shell-history capture; stolen laptop; suspicious portal activity.

**Err on the side of rotation.** Bitunix portal is REPLACE-ON-CREATE (single
key slot — see `[[bitunix-credential-rotation-2026-05-29]]`), so creating a new
key auto-invalidates the old. The "cost" of rotating-when-you-didn't-need-to is
~10 minutes of operator time + a service restart; the cost of not-rotating-when-
you-needed-to is unbounded.

---

## Decision criteria

| Symptom | Right response |
|---|---|
| Secret VALUE surfaced in chat / transcript / commit / log / Azure activity log | **Rotate.** Treat any leaked value as compromised regardless of perceived blast radius — the prior C-7 + C-1 pattern. |
| Suspicious activity in the BitUnix portal (trades you didn't authorize, login from unknown IP, API-key usage from unknown IP) | **Rotate + investigate** (§ G). Don't just rotate; figure out what happened. |
| Laptop / phone with credential access lost or potentially compromised | **Rotate.** Cost-benefit favors rotate. |
| Suspect compromise but no concrete evidence (gut feeling, near-miss, weird audit pattern) | **Rotate.** § Decision criteria for `bitunix_panic_halt.md` says the same — cheap to do, expensive to under-react. |
| Routine hygiene rotation (no incident) | Follow § A–F same as a compromise; skip § G investigation. |

---

## Section A — Immediate isolation (halt order placement)

**Do this BEFORE rotating.** A rotation invalidates the old key on creation
(REPLACE-ON-CREATE per the 2026-05-29 rotation log), so the running service
will fail-closed on the next snapshot/quote. That's fine for reads, but if
the service was mid-order-placement, the half-state could be ugly.

Halt first per `runbooks/bitunix_panic_halt.md` § A.1 (primary path,
sub-second, no restart):

```bash
BASE=/home/azureuser/trading_corp
TAG=.pre-cred-compromise-$(date -u +%Y%m%dT%H%M%SZ)
sudo cp -p $BASE/config/strategies.yaml $BASE/config/strategies.yaml$TAG
sudo sed -i '/^bitunix_futures:/,/^[a-z]/ s/^  auto_execute: true$/  auto_execute: false/' \
    $BASE/config/strategies.yaml
sudo grep -n -A 4 '^bitunix_futures:' $BASE/config/strategies.yaml | grep auto_execute
```

Expect `auto_execute: false` on `bitunix_futures`.

**Optional — also flatten** if the suspicion is that the attacker is or has
been placing orders (vs. just holding a leaked key): follow `bitunix_panic_halt.md`
§ B.1 (BitUnix UI "Close All Positions"). The UI path doesn't depend on the
API key, so it works even if the rotation is mid-flight.

**Skip this section** only if you're doing a routine hygiene rotation with no
active concern + zero open positions.

---

## Section B — Pre-flight: confirm the load path

Bitunix credentials on prod are **KV-only** (verified 2026-05-29; see
`runbooks/deploy_log.md` § "2026-05-29 ~19:06 UTC ... Prod state for bitunix
remains KV-only"). Prod has no `.env` (only `.env.example`); the
`trading-corp.service` systemd unit Environment has `KEY_VAULT_URI` set, and
`utils/secrets.py::_populate_from_keyvault` populates `os.environ` at startup.

Verify the load path is still KV-only (don't trust the memory; verify):

```bash
# Confirm KV vault is reachable and the secrets exist (no values).
az keyvault secret list \
    --vault-name kv-tc-vtwbowt3wtkpy \
    --query "[?contains(name,'BITUNIX-FUTURES')].{name:name,enabled:attributes.enabled,updated:attributes.updated}" \
    -o table
```

Expect: two rows (`BITUNIX-FUTURES-API-KEY` + `BITUNIX-FUTURES-API-SECRET`),
both `enabled=true`. Note the current `updated` timestamps — they should NOT
advance until you complete § D. (Freshness check § F.1 verifies they
advanced.)

```bash
# Confirm prod has no parallel .env path that would shadow KV.
ssh-or-az-cmd: sudo ls -la /home/azureuser/trading_corp/.env 2>&1 | head -2
# Expect: "No such file or directory" (only .env.example exists on prod).

# Confirm the systemd unit has KEY_VAULT_URI set.
sudo systemctl show trading-corp --property=Environment | tr ' ' '\n' | grep KEY_VAULT_URI
# Expect: KEY_VAULT_URI=https://kv-tc-vtwbowt3wtkpy.vault.azure.net/
```

If `/home/azureuser/trading_corp/.env` exists OR the systemd EnvironmentFile
includes a bitunix env path: prod has drifted from KV-only since the
2026-05-29 audit. Stop, investigate, and update § C of this runbook before
proceeding — the rotation must touch every live load path simultaneously.

**Operator's dev .env (local Windows / WSL):** verify whether the dev `.env`
contains the pre-rotation bitunix credentials. If yes, plan to scrub them in
§ E.1 (name-only grep — never echo values):

```bash
# In the operator's dev checkout root. Counts only, no values.
grep -c '^BITUNIX_FUTURES_API_KEY=' .env || true
grep -c '^BITUNIX_FUTURES_API_SECRET=' .env || true
```

Expect 0/0 after the 2026-05-29 scrub. If 1/1, the prior scrub didn't take
or the operator re-added them locally — re-scrub in § E.1.

---

## Section C — Disable history capture for the rotation window

**Same tier as § B. Do not skip.** The rotation procedure does NOT require
the operator to type credential values into shells — values go from the
BitUnix portal UI directly to the Azure Portal browser form (per the
2026-05-29 pattern). But the AZ CLI commands in § F (verification) and the
`grep` / `sed` commands in § E COULD capture credential context into shell
history, even when not capturing values directly.

### bash (prod VM, for any verification work)

```bash
set +o history
# Verification: should print "off"
shopt -s -o | grep history || echo "off"
```

### PowerShell (operator's local shell)

```powershell
Set-PSReadlineOption -HistorySaveStyle SaveNothing
# Verification: should print "SaveNothing"
(Get-PSReadlineOption).HistorySaveStyle
```

Hard-purge after rotation per `runbooks/tastytrade_oauth_rotation.md` §
Pre-flight 2 "After the rotation is complete — hard history purge" — same
discipline applies here.

---

## Section D — Rotate the credentials

**Operator-only — agent never sees values.** The proven 2026-05-29 pattern
is **portal-to-portal**: operator copies new key from the BitUnix portal
browser tab directly into the Azure Portal browser form. No shell, no
intermediate file, no clipboard manager that syncs to cloud.

### D.1 — Create new BitUnix API key (BitUnix portal)

Log into the BitUnix portal (`https://www.bitunix.com`, operator's account
that owns the prod-used API key). Navigate to the API Management area
(typically under the account / security menu — exact deep-link varies by UI
version; from the landing page after login, find "API Management" or
"API Keys").

**Create a new key with these settings** (matching the 2026-05-29 pattern):

- **Permissions:** trade-enabled, **withdraw-DISABLED**. Withdraw permission
  is never required for the bot's order-path; refusing it caps the
  worst-case blast radius if a future compromise repeats.
- **IP whitelist:** `20.51.145.253` (prod VM egress IP — confirmed
  2026-05-29 via `curl https://api.ipify.org` from the prod VM). If the
  VM's egress IP has changed since, re-confirm before whitelisting:
  ```bash
  # On prod, value-blind:
  curl -s https://api.ipify.org && echo
  ```
- **Label:** `tc-live-engine-YYYY-MM-DD` (operator-chosen, dated; matches
  the 2026-05-29 label pattern `tc-live-engine-2026-05-29`).

**REPLACE-ON-CREATE warning:** the moment the new key is created, the OLD
key is auto-invalidated (no explicit revoke step needed on bitunix; this
is one-key-slot model per `[[bitunix-credential-rotation-2026-05-29]]`).
The running service will hit `code=10003 'Token invalid'` on the next
snapshot/quote call. § A's halt-first is what makes this safe — without it,
the service may be mid-order-placement when the old key dies.

Copy the new **API key** and **API secret** values from the portal into the
OS clipboard / clipboard-buffer (one at a time). **Do not paste them
anywhere except the Azure Portal form in § D.2.**

### D.2 — Write the new pair to Azure Key Vault (Azure Portal browser form)

Operator opens `https://portal.azure.com` → Key vaults →
`kv-tc-vtwbowt3wtkpy` → Secrets → `BITUNIX-FUTURES-API-KEY` → "+ New
version" → paste the new API key value → Create. Then repeat for
`BITUNIX-FUTURES-API-SECRET`.

**Why Portal not CLI:** the 2026-05-29 rotation used the Portal browser
form for both writes; no `az keyvault secret set` was invoked at all. The
Portal form keeps the value in the browser memory + the HTTPS request body
only — it never crosses a shell process, never enters a command-line argv,
never lands in any shell history surface. **This is the path
`[[feedback-secret-never-touches-claude-code]]` makes available.**

**RBAC note (per 2026-05-29):** the vault uses RBAC, not access policies.
The operator self-granted **Key Vault Secrets Officer** role on the vault
to write via Portal. Verify the role is still active on the operator's
identity before starting:

```bash
# Value-blind check; lists role assignments on the vault.
az role assignment list \
    --scope "/subscriptions/$(az account show --query id -o tsv)/resourceGroups/rg-shared-prod/providers/Microsoft.KeyVault/vaults/kv-tc-vtwbowt3wtkpy" \
    --assignee "$(az account show --query user.name -o tsv)" \
    --query "[].roleDefinitionName" -o tsv
```

Expect: `Key Vault Secrets Officer` (or higher) in the output. If absent,
self-assign via the Azure Portal → vault → IAM tab before § D.2.

### D.3 — Capture the new version IDs (operator records)

After § D.2, Azure Portal shows the new version ID for each secret (32-char
hex string at the end of the secret URI). **Record these in
`runbooks/deploy_log.md`** in the rotation entry you'll write at session
close (§ G.4). Format from 2026-05-29:

> `BITUNIX-FUTURES-API-KEY` new version `9b33309a64da4855bb10128243c6b499`
> (2026-05-29T18:58:22Z, enabled).

Version IDs are NOT secret — they're freshness witnesses. Capturing them
into the deploy log lets future verification compare against the
"as-rotated" reference.

---

## Section E — Old credential invalidation + dev .env scrub

### E.1 — BitUnix old-key invalidation (already happened in § D.1)

REPLACE-ON-CREATE means the old key is dead the moment the new key is
created. No separate revoke step. **Verification:** § F.3 below.

### E.2 — Operator's dev `.env` scrub (if § B detected non-zero counts)

If § B's `grep -c` returned 1 or more for either `BITUNIX_FUTURES_API_KEY`
or `BITUNIX_FUTURES_API_SECRET`, the dev .env still carries the now-dead
credentials. Scrub:

```bash
# In the operator's dev checkout root. NO value echo — name-only sed.
sed -i '/^BITUNIX_FUTURES_API_KEY=/d;/^BITUNIX_FUTURES_API_SECRET=/d' .env
# Verify (counts only):
grep -c '^BITUNIX_FUTURES_API_KEY=' .env || true
grep -c '^BITUNIX_FUTURES_API_SECRET=' .env || true
# Spot-check other lines intact (no echo of bitunix lines because they're gone):
wc -l .env
```

Expect: post-scrub `grep -c` returns 0/0; `wc -l` shows N-2 lines vs. the
pre-scrub line count.

**Note on prod:** prod has no `.env` (verified in § B), so no prod-side
scrub. Future bitunix rotations remain pure KV-overwrite + restart — no
prod .env step.

---

## Section F — Verification (system state, not assertion)

The rotation is **storage-complete** after § D + § E. It is not **verified
fresh** until ALL of these pass.

### F.1 — KV version advanced

```bash
az keyvault secret show \
    --vault-name kv-tc-vtwbowt3wtkpy \
    --name BITUNIX-FUTURES-API-KEY \
    --query "{version:id, updated:attributes.updated, enabled:attributes.enabled}"
az keyvault secret show \
    --vault-name kv-tc-vtwbowt3wtkpy \
    --name BITUNIX-FUTURES-API-SECRET \
    --query "{version:id, updated:attributes.updated, enabled:attributes.enabled}"
```

Expect: `updated` timestamps within the rotation window; `id` ends in the
new version GUIDs from § D.3; `enabled: true`. If `updated` is older than
the rotation window, the Portal write didn't land — re-do § D.2.

### F.2 — Restart picks up the new pair

```bash
# Trigger restart (re-fires Robinhood device challenge on operator's phone —
# coordinate before running this).
sudo systemctl restart --no-block trading-corp
# Wait for startup, then check.
sleep 30 && systemctl is-active trading-corp && echo "MainPID: $(systemctl show -p MainPID --value trading-corp)"
```

Expected pattern (per 2026-05-29):
- `is-active`: `active`
- New MainPID differs from pre-restart.
- ~6 min later: `Web command center listening on http://0.0.0.0:8000` in
  the journal; `curl https://trading.jacksumner.com/healthz` returns 200.

### F.3 — New-key auth succeeds + old-key dies

**New-key auth (in-process proof):**
```bash
# Journal scan for the live broker-connect message that confirms a real
# snapshot succeeded with the new KV-sourced key.
sudo journalctl -u trading-corp -n 500 --no-pager | grep -E "BitunixBroker connected|equity=\\\$" | tail -3
```

Expected line shape (per 2026-05-29): `INFO trading_corp.brokers.bitunix: BitunixBroker connected (account=bitunix-futures, equity=$NNN.NN, N positions)`.

**KV-fetch trail:**
```bash
sudo journalctl -u trading-corp -n 500 --no-pager | grep -E "kv-tc-vtwbowt3wtkpy.*BITUNIX-FUTURES" | head -2
```

Expected: `Request URL: 'https://kv-tc-vtwbowt3wtkpy.vault.azure.net/secrets/BITUNIX-FUTURES-API-KEY/?api-version=REDACTED'` (+ SECRET) — confirms the new process pulled the new versions.

**Old-key rejection (synthetic probe, value-blind):**

The agent-side synthetic probe (per 2026-05-29) confirms the rejection
envelope without using real values. On the prod VM:

```bash
# Synthetic probe with FAKE api-key + FAKE secret. Value-blind; confirms
# the rejection PATH independent of any live-key state.
sudo -u azureuser /home/azureuser/trading_corp/venv/bin/python -c '
import asyncio, httpx
async def main():
    h = {"api-key":"FAKEFAKEFAKEFAKE","sign":"deadbeef","nonce":"00000000","timestamp":"1700000000000"}
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get("https://fapi.bitunix.com/api/v1/futures/account?marginCoin=USDT", headers=h)
        print(f"http={r.status_code}, body={r.json()}")
asyncio.run(main())
'
```

Expected: `http=200, body={'code': 10003, 'msg': 'Token invalid', ...}` (or
similar — same envelope as a real-but-dead key). Confirms the
authentication rejection path is alive.

If you want to additionally verify the OLD key specifically died (vs. just
"some key is dead"), check the journal for old-PID failures recorded
between the rotation moment and the restart (the 2026-05-29 pattern caught
the old process emitting `code=10003 msg='Token invalid'` three times in
the seconds before the restart, while it was still trying to use the now-
dead old key):

```bash
sudo journalctl -u trading-corp --since "10 minutes ago" --no-pager | \
    grep -E "code=10003|Token invalid" | head -5
```

Expected: a few `code=10003` lines BEFORE the new MainPID, none AFTER.

### F.4 — Redaction filter coverage (already in place; re-confirm)

Per `[[bitunix-credential-rotation-2026-05-29]]`, both forms of redaction
are wired:

- **KEY=value form:** `BITUNIX_FUTURES_API_KEY` and `BITUNIX_FUTURES_API_SECRET`
  in `_SECRET_KEY_NAMES` at `trading_corp/utils/secrets.py:31-32`.
- **Literal-value form:** `register_redact_literal` called for both at
  `load_secrets()` time (the C-1 commit `55da299` from the 2026-05-29
  rotation — present on main).

Quick re-confirm (value-blind):
```bash
grep -n 'BITUNIX_FUTURES_API' trading_corp/utils/secrets.py
```

Expect: both names in `_SECRET_KEY_NAMES` (~ lines 31-32) AND in
`expected_env_vars` (~ lines 256-257) AND in the `Secrets` dataclass
loader (~ lines 334-335). No code change needed unless a line is missing,
which would itself be a regression worth filing.

### F.5 — Real-broker-write end-to-end (deferred today)

The only proof that the new credentials authenticate **for writes** is a
real `place_order` against bitunix. Today on prod, `place_order` is still
a `NotImplementedError` stub (prod = `4985bbe + 03:57 sed-overlay`,
pre-Stage-1-merge), so this check is deferred until the first prod-deploy
of `main` (gated on P1 (a) + (b) + (c)). The reads-only verification in
F.1–F.4 is sufficient for read-only paper-mode operation.

**When that prod-deploy happens:** the post-rotation Stage-1 first-trade
HITL gate (`HITL_FIRST_N_LIVE_ORDERS = 10` in
`bitunix_futures_observer.py:228`) is the live write verification — the
first 10 live orders route to HITL approval; an operator-approved order
that fills confirms the credentials end-to-end.

---

## Section G — Investigation (compromise scenarios only — skip for routine hygiene)

If this rotation was triggered by a suspected or confirmed compromise (not
a routine hygiene rotation), determine what happened:

### G.1 — Trade history review

In the BitUnix portal, Futures → Order History → filter the last 7 days
(or longer if the compromise window is suspected to be older). Look for:

- Orders the bot did NOT log in `audit_event` (bot-logged orders carry
  `clientId = tc-<order.id>`; BitUnix UI shows clientId on each order).
- Orders outside the bot's symbol whitelist (today: `BTC/USDT.P` only per
  `config/strategies.yaml:1024`).
- Orders outside the bot's normal hours / cadence.
- Withdraw attempts (should be zero — the key has withdraw-DISABLED).

### G.2 — Audit-log cross-check

```bash
sudo sqlite3 /home/azureuser/trading_corp/data/trading_corp.db \
    "SELECT ts, kind, json_extract(payload_json, '\$.order_id') AS order_id,
            json_extract(payload_json, '\$.symbol') AS symbol,
            json_extract(payload_json, '\$.side') AS side
     FROM audit_event
     WHERE (json_extract(payload_json, '\$.strategy') = 'bitunix_futures'
            OR json_extract(payload_json, '\$.division') = 'bitunix_futures')
       AND kind IN ('filled','would_have_placed','webhook_received')
       AND ts >= datetime('now', '-7 days')
     ORDER BY ts DESC;"
```

Cross-reference with G.1's portal order history. **Bot-recorded orders
should appear in BOTH places.** Portal-only orders (in G.1, not in this
query) are unauthorized — confirms compromise. Audit-only orders (here,
not in G.1) are a sign of broker-side data loss or a bot-bug — file
separately.

### G.3 — Leak-surface scrub

If the compromise source was a leak (value surfaced in a transcript,
commit, log file, Azure activity log):

1. Find the leak surface. Use the stable bitunix key shape (32+ hex chars
   for `api_key`, longer for `api_secret`) as a regex to grep the
   surface. **Do NOT paste the value itself into the grep — use a
   structural regex.**
2. If the surface is editable (operator's local files, a deletable cloud
   resource): scrub.
3. If the surface is append-only (Azure activity log, Slack/Telegram
   history without admin delete): **record the leak forensic trail** in
   `runbooks/deploy_log.md` (leak surface, values still-or-no-longer
   visible, remediation timeline). The compromised key is dead; the
   leaked-value entry in the log is a historical record, not an active
   risk.

### G.4 — Deploy-log entry

Write a `## YYYY-MM-DD HH:MM UTC — Bitunix credential rotation
(<routine | post-compromise>)` entry in `runbooks/deploy_log.md` with:

- **Commits:** any code on the rotation branch (typically `n/a` —
  rotations are KV-only).
- **Triggered by:** the incident OR "routine hygiene per operator schedule."
- **KV writes:** the new version IDs from § D.3 + their `updated`
  timestamps.
- **Key scope:** the trade-enabled/withdraw-DISABLED/IP-whitelisted
  details from § D.1.
- **Restart:** PID change + journal-confirmed broker-reconnect.
- **Verification:** the F.1–F.4 results.
- **Leak forensics** (if compromise): G.3 results.
- **No secret values entered the Claude Code session** — load-bearing
  statement matching the 2026-05-29 entry.

---

## Section H — Resume

Resume order placement only after **all** of:

- [ ] § F.1–F.4 all green (KV version advanced, restart picked up new
      pair, new-key auth succeeds, old-key dies, redaction coverage
      intact).
- [ ] If a compromise: § G.1–G.3 complete; root cause known or
      explicitly accepted-as-unknown-but-rotated.
- [ ] § G.4 deploy_log entry written + committed.
- [ ] `runbooks/bitunix_panic_halt.md` § Resume conditions all met
      (positions flat, audit clean, etc.).

Then flip `auto_execute` back to true per `runbooks/bitunix_panic_halt.md`
§ E.1:

```bash
BASE=/home/azureuser/trading_corp
sudo sed -i '/^bitunix_futures:/,/^[a-z]/ s/^  auto_execute: false$/  auto_execute: true/' \
    $BASE/config/strategies.yaml
sudo grep -n -A 4 '^bitunix_futures:' $BASE/config/strategies.yaml | grep auto_execute
```

Expect `auto_execute: true`. Bot resumes accepting strategy proposals.
First real order after resume is the live-rotation acceptance — operator
watches the dashboard activity rail and verifies the first
`would_have_placed` or `filled` row uses the new credentials (via the
journal's `BitunixBroker connected ... equity=...` line being recent +
F.3's KV-fetch URL pattern).

---

## Don't

- **Don't** rotate before halting per § A. Mid-order-placement
  invalidation of the old key produces an ugly half-state.
- **Don't** type secret values into any shell. The Portal-to-Portal
  pattern (§ D.2) is the proven path. The tastytrade rotation runbook
  documents `read -rs` + `az keyvault secret set --file <(printf '%s' "$VAR")`
  as a fallback if you absolutely must use CLI — but for bitunix on
  Azure, the Portal form is always available and is the simpler safe
  path.
- **Don't** skip § B's load-path verification. If `/etc/...` or
  `~/.env` has drifted to include bitunix vars since the 2026-05-29
  audit, the rotation must touch every live path simultaneously or
  the running service stays on stale credentials.
- **Don't** echo secret values via `cat`, `echo`, or shell
  interpolation — even for "verification." The synthetic-FAKE-creds
  probe in F.3 proves the rejection path without using real values.
- **Don't** use `bash source` / `. file` / `set -a; . file` against
  any env file holding the new credentials — bash echoes the offending
  line on syntax error; `az vm run-command` captures stderr. See
  `[[feedback-never-bash-source-env-files]]`.
- **Don't** declare rotation done after Portal write alone. § F's
  end-to-end probes are what prove the new pair flows from KV → process
  → broker → bitunix server.
- **Don't** edit this runbook without Board approval (CLAUDE.md § 4 —
  runbooks are a recovery contract). Append-only updates with a
  `Revision history` tail are the path if the procedure changes
  materially.
- **Don't** assume the next rotation's portal URL or RBAC role
  assignments are unchanged — re-verify § D.1's portal navigation and
  § D.2's RBAC role at the start of each rotation. Portals change UI;
  RBAC roles can be removed.

---

## Related

- `runbooks/bitunix_panic_halt.md` — halt + flatten procedure that
  this runbook's § A cross-references.
- `runbooks/tastytrade_oauth_rotation.md` — sister rotation runbook;
  forms the discipline template that this runbook's § C (history
  disable) + § D.2 (no-shell value handling) + § F (verification, not
  assertion) all inherit from.
- `runbooks/deploy_log.md` § "2026-05-29 ~19:06 UTC — C-1 bitunix
  futures KV credential rotation" — the canonical reference
  rotation; this runbook's verification commands are derived from
  what worked there.
- `[[c1-per-portal-rotation-discipline]]` — 12-step pattern reusable
  across portals; bitunix-specific REPLACE-ON-CREATE notes here
  feed into the per-portal observation slot in that memory.
- `[[bitunix-credential-rotation-2026-05-29]]` — completion record
  + the REPLACE-ON-CREATE finding.
- `[[feedback-secret-never-touches-claude-code]]` — the
  Portal-to-Portal pattern that § D.2 codifies.
- `[[no-membership-assert-on-secret-collections]]` — the test-failure
  leak pattern that triggered the apify P1 elevation; relevant
  context for "how compromises happen."
- `[[feedback-never-bash-source-env-files]]` — the bash-source-leak
  surface that the Portal pattern entirely avoids.
- `trading_corp/utils/secrets.py:31-32,256-257,334-335` — the three
  places the bitunix env-var names appear in the loader path; § F.4
  re-confirms these.

---

## Revision history

- 2026-05-30 — initial version (P1 gate (b) closure, architectural-review
  Finding #2 Readiness #11). Procedure codifies the 2026-05-29 bitunix C-1
  rotation pattern as a repeatable runbook. Code surfaces verified at
  `origin/main` commit `03f3261`. Live-write end-to-end verification
  (§ F.5) deferred until first prod-deploy of `main` lands `place_order`
  on prod (currently gated on remaining P1 items).
