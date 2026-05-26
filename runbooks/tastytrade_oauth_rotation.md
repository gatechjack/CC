# Tastytrade OAuth Rotation Runbook

**Owner:** operator performs the credential-value steps by hand; this
runbook documents what they do, not the values. No Client Secret, no
refresh token, no JWT body, no full URLs containing `?code=...` may be
pasted into chat, into commits, into deploy_log entries, or into this
runbook. Names, paths, and commands only.

**Surfaces this rotation affects** (both must verify post-rotation):
- IC division candidate grader (`tastytrade_provider.py` consumes ATM
  IV, spot, option chains for the IC grader's 8-gate path; gate 7
  term-structure is the load-bearing user).
- Tasty Options division (`brokers/tastytrade.py` + the same provider;
  Phase 1 paper observation clock is running).

A rotation is not done until **both** surfaces have been verified live.

---

## What this is

The Tastytrade OAuth model: a long-lived **Client Secret** (issued by
the TT developer portal against a named OAuth app) plus a **refresh
token** (a JWT issued by the OAuth grant flow against that Client
Secret). The SDK exchanges the refresh token for a short-lived session
token on each call.

The pair is atomic. A refresh token only authenticates against the
exact Client Secret it was granted under. Rotating one without the
other produces a token that the SDK accepts in shape but the server
rejects on use — the failure modes in § Failure-chain diagnosis.

A rotation is required when:
- The Client Secret has been leaked or is suspected leaked.
- The refresh token has been revoked Tastytrade-side (visible as
  `invalid_grant: Grant revoked` in logs).
- The OAuth app's allowed scopes need to widen (e.g. `read` →
  `read+trade` to enable Phase 2 of tasty_options).
- Routine rotation per the operator's credential-hygiene schedule.

---

## Pre-flight 1 — confirm the loading chain

Before rotating, know **which place(s) the running prod process reads
the credentials from**, because rotation must update every place
simultaneously or the running service will go stale.

Three possible load paths (verify which are live; rotation must touch
all live paths):

1. **Azure Key Vault** (`TASTYTRADE-PROVIDER-SECRET` /
   `TASTYTRADE-REFRESH-TOKEN`) — fetched at app startup by
   `utils/secrets.py::_populate_from_keyvault`, written into
   `os.environ` of the running process. Verified-wired-as-of-this-write:
   both names are in `_SECRET_KEY_NAMES` (`utils/secrets.py:56-57`) and
   `expected_env_vars` (`utils/secrets.py:226-227`).

2. **systemd `EnvironmentFile`** on the prod VM —
   `/etc/trading-corp/tastytrade.env` (mode 600, root:root), referenced
   from `/etc/systemd/system/trading-corp.service.d/override.conf`. May
   still be live in parallel to the KV path; the
   `[[feedback-tastytrade-env-vars-bypass-kv]]` step 4
   (`shred -u /etc/trading-corp/tastytrade.env` after KV path
   confirmed) may or may not have been executed. **Verify before
   rotating.**

3. **Operator's Windows User registry**
   (`HKCU:\Environment\TASTYTRADE_PROVIDER_SECRET` /
   `TASTYTRADE_REFRESH_TOKEN`) — local dev / verification scripts read
   these via the in-process PowerShell env. NOT a prod load path, but
   prod-verification scripts run locally depend on it being in sync.

Verification commands (read-only, value-safe — they print PATHS and
PRESENCE, not VALUES):

```powershell
# Local registry presence (User scope). Prints "True" / "False" only.
[bool][Environment]::GetEnvironmentVariable("TASTYTRADE_PROVIDER_SECRET", "User")
[bool][Environment]::GetEnvironmentVariable("TASTYTRADE_REFRESH_TOKEN",  "User")
```

```bash
# Prod (run via az vm run-command invoke, NOT bash source — see
# [[feedback-never-bash-source-env-files]]).
sudo test -f /etc/trading-corp/tastytrade.env && echo "env-file: present" || echo "env-file: absent"
sudo grep -c '^TASTYTRADE_' /etc/trading-corp/tastytrade.env  # prints LINE COUNT only, never values
sudo systemctl cat trading-corp.service | grep -i environmentfile  # prints the EnvironmentFile= reference if active
```

```bash
# Prod KV presence (no values).
az keyvault secret list --vault-name <vault-name> --query "[?contains(name,'TASTYTRADE')].{name:name,enabled:attributes.enabled}" -o table
```

If the env file is `absent` AND the systemd override has no
`EnvironmentFile=` line: KV-only. If the env file is `present` and the
override references it: parallel (KV + EnvironmentFile shadow), and
both must be rotated.

---

## Pre-flight 2 — disable shell history for the rotation window

**Same tier as Pre-flight 1. Do not skip.** The rotation procedure
in § The atomic 2-step rotation involves shell commands carrying the
new Client Secret and refresh token as inline data (heredoc bodies,
`read`-captured variables, `setx` value arguments). Every shell that
runs these commands captures them by default into a history surface:

- `bash` writes each command line to `~/.bash_history` on shell exit
  (or live with `HISTFILE` syncing). Heredoc bodies are included.
- PowerShell's PSReadLine writes each command to
  `$env:APPDATA\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt`
  in real time. `Get-History` also surfaces the in-session history.
- `az vm run-command invoke` captures both stdout and stderr of the
  invoked command into the Azure activity log, indefinitely.

A rotation runbook whose own commands leak the new secret into a
logged surface is self-defeating — same class as the 2026-05-22
bash-source-via-stderr leak (Symptom F below). Close history capture
**before** the first Step 2 command runs, restore after.

### bash (the prod-VM shell)

```bash
# Disable history for the current shell. Persists until shell exit
# or until `set -o history` is run.
set +o history

# Verification: should print "off"
shopt -s -o | grep history || echo "off"
```

### PowerShell (operator's local shell)

```powershell
# Stop PSReadLine from saving any further commands to the on-disk
# history file. Persists for the current PS session only.
Set-PSReadlineOption -HistorySaveStyle SaveNothing

# Verification: should print "SaveNothing"
(Get-PSReadlineOption).HistorySaveStyle
```

Optionally, clear the in-session history buffer too — useful if the
operator just typed an `az login` or any command containing
credentials of any kind in the window where this rotation runs:

```powershell
Clear-History
```

### Azure CLI (used in Step 2 sub-step 1)

`az` commands sent through `az vm run-command invoke` capture
stdout/stderr to the Azure activity log. **Run `az keyvault secret
set` from a local shell directly authenticated to Azure (`az login`),
NOT via `az vm run-command invoke` from the prod VM.** Local
invocation keeps the secret out of the activity log entirely; the
local shell history is already gated by the bash / PSReadLine step
above.

### After the rotation is complete — hard history purge (mandatory)

`set +o history` and `Set-PSReadlineOption -HistorySaveStyle SaveNothing`
disable **future** writes. Neither retroactively scrubs anything that
may have already been buffered or flushed to disk before they took
hold (PSReadLine writes incrementally; bash's in-memory history can be
syncable to `HISTFILE` mid-session via `PROMPT_COMMAND='history -a'`
or similar operator-local config). Belt-and-suspenders: purge
explicitly.

### bash

```bash
history -c                # clear in-memory history buffer
history -w                # overwrite HISTFILE with the (now empty) buffer
set -o history            # re-enable history capture for future commands
```

The `-c && -w` pair is the load-bearing combination — `-c` alone
clears memory but leaves the on-disk file unchanged; the next shell
exit could merge old + new and re-materialize the purged entries.

### PowerShell

```powershell
Clear-History             # purge the in-session Get-History buffer

# Also purge the on-disk PSReadLine history file if SaveNothing was
# enabled mid-session (entries before that may have already flushed):
$psr = (Get-PSReadlineOption).HistorySavePath
if (Test-Path $psr) { Clear-Content $psr }

Set-PSReadlineOption -HistorySaveStyle SaveIncrementally   # PS default
```

`Clear-History` clears only the in-session buffer; the on-disk file
at `(Get-PSReadlineOption).HistorySavePath` is a separate surface
that PSReadLine writes to in real time. `Clear-Content` truncates it
in place without changing perms.

### Verify the purge took

```bash
history | wc -l           # expect: 1 (just the `history` command itself)
test -s "$HISTFILE" && echo "WARN: HISTFILE non-empty" || echo "HISTFILE empty"
```

```powershell
(Get-History).Count       # expect: 0
$psr = (Get-PSReadlineOption).HistorySavePath
if ((Get-Item $psr -ErrorAction SilentlyContinue).Length -gt 0) {
    "WARN: PSReadLine history file non-empty: $psr"
} else { "PSReadLine history empty" }
```

### Fallback if anything looks wrong

Close the shell entirely. The next shell launches with default
history behavior AND a fresh process, which guarantees no stale
in-memory state can re-flush. This is the brute-force complement to
the purge above, not a replacement — do both for any rotation
involving a leak-class symptom (Symptom F).

---

## The atomic 2-step rotation

Atomicity rule: the Client Secret and the refresh token landed in
storage **must come from the same OAuth bootstrap session against the
same OAuth app config**. Cross-pollination between sessions produces
the `Client secret mismatch` failure mode below.

### Step 1 — OAuth bootstrap (operator, by hand)

Performed once per rotation, all in one sitting. Do not pause between
sub-steps or come back tomorrow — the matched pair is only matched at
the moment it leaves the TT portal.

1. **Open a standard browser (Edge or Chrome).** Not a privacy-hardened
   browser — see `[[feedback-oauth-use-standard-browser]]`. The TT
   redirect URL carries the `?code=...` query param in the address
   bar; privacy browsers can strip it silently and the bootstrap stalls
   undebuggably.

2. **In `developer.tastytrade.com`:** open the OAuth app config and
   confirm the **allowed scopes** match the intended use. For
   IC-division-only use today, `read` is sufficient. For tasty_options
   Phase 2 (order placement), `read+trade` is required. Widening the
   scope here is a separate decision; if widening, save the app config
   first, then proceed. Note: TT silently drops scopes that the app
   does not permit (see `[[reference-tastytrade-oauth-scope-widening]]`
   gotcha 1) — verifying the app config is the only way to ensure the
   wider scope will actually be granted.

3. **Rotate or rebuild the Client Secret** in the OAuth app UI. Copy
   the new value into the OS clipboard. **Do not paste it anywhere
   yet** (chat, files, terminal history are all surfaces).

4. **Run the OAuth grant flow** against this Client Secret. The flow
   redirects to a local non-resolving URL (typically
   `https://127.0.0.1/callback?code=...&state=...`). Copy the `code`
   value out of the address bar.

5. **Exchange the code for a refresh token** in a fresh local Python
   shell. The token-exchange call returns a JWT refresh token (starts
   with `eyJ`). The Client Secret used in this exchange must be byte-
   for-byte the value rotated in sub-step 3. The refresh token now in
   hand is bound to this Client Secret and only this Client Secret.

6. **Sanity-check the refresh token shape before touching storage.**
   The token must:
   - Start with `eyJ` (JWT signature header, base64url-encoded). If
     not, the OAuth flow returned the wrong token type — retry sub-step
     5 against the correct endpoint.
   - Decode (middle segment, base64url) to a JSON object with a
     `scope` key containing the scopes you requested. See § Failure-
     chain diagnosis Symptom D for the one-line scope-decode probe.
     If `scope` is missing `trade` when you requested `read+trade`,
     gotcha 1 fired — go back to sub-step 2 and widen the app config.

The operator now has a matched pair (Client Secret + refresh token) in
clipboard / RAM / paste-buffer state only. Nothing is written to
storage yet.

### Step 2 — Write the matched pair to every live load path

Perform in this order; do not skip a target that's live per the
pre-flight.

1. **Azure Key Vault** (if KV path is live):

   **Run this step from a bash session — not from PowerShell.** Use
   WSL on the operator's Windows box, OR an SSH session into the prod
   VM (with the operator's local `az login` context tunneled via
   `az login --use-device-code` or by re-authenticating inside the VM),
   OR Azure Cloud Shell. At least one bash path is essentially always
   available. PowerShell does not have process substitution (`<(...)`),
   so the only safe form (below) requires bash. Switching shells for
   one step is a small ergonomic cost; it is what closes the leak
   surface that PowerShell would otherwise force open. **There is no
   PowerShell fallback for KV writes** — see the hard-stop note after
   the bash form.

   ```bash
   # Pre-flight 2 (history disable) MUST be active before running these.
   # `read -rs` reads silently — no echo, no history entry.
   read -rs CLIENT_SECRET
   az keyvault secret set \
     --vault-name <vault-name> \
     --name TASTYTRADE-PROVIDER-SECRET \
     --file <(printf '%s' "$CLIENT_SECRET")
   unset CLIENT_SECRET

   read -rs REFRESH_TOKEN
   az keyvault secret set \
     --vault-name <vault-name> \
     --name TASTYTRADE-REFRESH-TOKEN \
     --file <(printf '%s' "$REFRESH_TOKEN")
   unset REFRESH_TOKEN
   ```

   Why this form: `--value <secret>` puts the secret on the command
   line, visible to anyone running `ps`, captured to shell history if
   Pre-flight 2 was skipped, and recorded in the Azure activity log if
   invoked via `az vm run-command`. `--file <(printf '%s' "$VAR")`
   passes the value through an anonymous pipe — `printf '%s'` avoids
   the trailing newline that a heredoc-to-file would inject (which
   would corrupt the stored secret), and the value lives only in the
   shell's variable space until `unset`. The `read -rs` capture is
   silent (no echo to terminal, no history entry).

   `az keyvault secret set` echoes only the new value's metadata (id,
   version, attributes) to stdout; it never echoes the value. Capture
   the new `version` strings — they are the freshness check in §
   Freshness verification.

   **KV secret writes require a bash session.** Reachable via WSL on
   the operator's Windows box, SSH into the prod VM, or Cloud Shell —
   at least one is essentially always available. If you somehow
   cannot reach any bash session, STOP and find one before
   proceeding. **Do NOT write KV secrets from PowerShell:** the
   plaintext sits in PS process memory in an uncloseable window
   (immutable interned strings cannot be zeroed; `Remove-Variable` is
   best-effort GC, not a wipe) and is exposed to `Get-Process` and
   ETW process-start tracers for the lifetime of the `az` invocation.
   The bash form above has no equivalent exposure window. There is
   always a bash path; use it.

   (Distinction from sub-step 3 below: the Windows registry env-var
   write IS unavoidably PowerShell — that's what the
   `Read-Host -AsSecureString` minimized-window pattern is for. KV
   writes are NEVER unavoidably PowerShell, so there is no leaky
   form documented here.)

2. **prod systemd EnvironmentFile** (if EnvironmentFile path is live):
   ```bash
   # On the prod VM via az vm run-command invoke. NEVER paste the
   # values into a chat-visible shell. Use a heredoc to a temp file
   # owned by root, then move into place:
   sudo install -m 0600 -o root -g root /dev/stdin /etc/trading-corp/tastytrade.env <<'EOF'
   TASTYTRADE_PROVIDER_SECRET=<paste-new-Client-Secret>
   TASTYTRADE_REFRESH_TOKEN=<paste-new-refresh-token>
   EOF
   ```
   `sudo install` is preferred over `cat > file` because it sets perms
   atomically; the file is never world-readable, even for a
   nanosecond. **Never** `bash source` the file for verification —
   see `[[feedback-never-bash-source-env-files]]`; values can leak via
   stderr on parse error.

3. **Operator's Windows User registry** (for local verification scripts):

   **Preferred form — `Read-Host -AsSecureString` + `[Environment]::SetEnvironmentVariable`**
   (no value on a command line; nothing for PSReadLine to capture):

   ```powershell
   # Pre-flight 2 (Set-PSReadlineOption -HistorySaveStyle SaveNothing)
   # MUST be active before running these.
   $sec = Read-Host -AsSecureString "Paste TASTYTRADE_PROVIDER_SECRET (input hidden)"
   $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
   try {
       $plain = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
       [Environment]::SetEnvironmentVariable("TASTYTRADE_PROVIDER_SECRET", $plain, "User")
       $env:TASTYTRADE_PROVIDER_SECRET = $plain   # sync in-process (gotcha 2 fix)
   } finally {
       [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
       Remove-Variable plain, sec, bstr -ErrorAction SilentlyContinue
   }

   $sec = Read-Host -AsSecureString "Paste TASTYTRADE_REFRESH_TOKEN (input hidden)"
   $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
   try {
       $plain = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
       [Environment]::SetEnvironmentVariable("TASTYTRADE_REFRESH_TOKEN", $plain, "User")
       $env:TASTYTRADE_REFRESH_TOKEN = $plain     # sync in-process (gotcha 2 fix)
   } finally {
       [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
       Remove-Variable plain, sec, bstr -ErrorAction SilentlyContinue
   }
   ```

   Why this form over `setx`:
   - `setx VAR "<value>"` puts the secret on the PSReadLine command
     line; PSReadLine writes it to disk in real time (the on-disk
     history file is buffered ahead of `Set-PSReadlineOption`
     applying, unless Pre-flight 2 ran first). `Read-Host
     -AsSecureString` reads from a hidden prompt — no echo to
     terminal, no history entry.
   - `[Environment]::SetEnvironmentVariable(..., "User")` writes
     directly to `HKCU:\Environment` (the same destination as
     `setx`), but the value lives in a SecureString until the
     `BSTR`-decode-then-zero pattern releases it, minimizing the
     plaintext window.
   - The explicit `$env:VAR = $plain` line synchronizes the
     in-process PowerShell with the registry write, closing the
     `setx`-doesn't-propagate gotcha
     (`[[reference-tastytrade-oauth-scope-widening]]` gotcha 2).
     Without this, the very PowerShell that just rotated the secret
     would keep the OLD value in its in-process env until
     close+reopen.

   **Fallback — `setx` form** (if SecureString pattern is unavailable
   in the operator's PowerShell version):
   ```powershell
   # Pre-flight 2 must be active. Even so, this leaves a brief window
   # where the secret is on a command line in the running process's
   # argv before PSReadLine's SaveNothing setting takes hold.
   setx TASTYTRADE_PROVIDER_SECRET "<paste-new-Client-Secret>"
   setx TASTYTRADE_REFRESH_TOKEN   "<paste-new-refresh-token>"
   $env:TASTYTRADE_PROVIDER_SECRET = [Environment]::GetEnvironmentVariable("TASTYTRADE_PROVIDER_SECRET", "User")
   $env:TASTYTRADE_REFRESH_TOKEN   = [Environment]::GetEnvironmentVariable("TASTYTRADE_REFRESH_TOKEN",   "User")
   Clear-History    # purge the in-session buffer that just captured the values
   ```

4. **Restart `trading-corp.service` on prod** so the new pair takes
   effect:
   ```bash
   sudo systemctl restart trading-corp
   ```
   Restart is required even on the KV-only path — `os.environ` is
   read at startup and cached for the process lifetime.

The rotation is now **storage-complete**. It is not yet **verified
fresh** until the next section's probes pass.

---

## Freshness verification (system state, not assertion)

Goal: confirm a rotation actually took, from filesystem / registry /
in-process state — not from "I rotated it." Each check verifies
**both** the operator action AND the system reflecting it.

### Check 1 — KV version advanced

```bash
az keyvault secret show --vault-name <vault-name> --name TASTYTRADE-PROVIDER-SECRET --query "{version:id, updated:attributes.updated}"
az keyvault secret show --vault-name <vault-name> --name TASTYTRADE-REFRESH-TOKEN   --query "{version:id, updated:attributes.updated}"
```

`updated` must be within the rotation window. `id` ends in a new
`version` GUID different from the pre-rotation value. **If `updated`
is older than the rotation window, the `az secret set` did not land.**

### Check 2 — env file mtime advanced (if EnvironmentFile path live)

```bash
sudo stat -c '%y %s %a %U:%G' /etc/trading-corp/tastytrade.env
```

`%y` must be within the rotation window. `%s` (size in bytes) should
have moved if the secret length changed. `%a` must be `600`. `%U:%G`
must be `root:root`. The 2026-05-22 incident's load-bearing tell was
mtime stuck at the original deploy time while the operator believed
the file had been re-bootstrapped.

### Check 3 — in-process env reflects new value (prod)

```bash
# After restart, find the new PID and probe its /proc/<PID>/environ:
PID=$(systemctl show -p MainPID --value trading-corp.service)
echo "trading-corp MainPID: $PID"
sudo cat /proc/$PID/environ | tr '\0' '\n' | grep -c '^TASTYTRADE_'   # prints 2 if both vars are loaded; 0 means systemd EnvironmentFile didn't apply
```

The `-c` count emits 0 or 2, not values. **Do not** pipe the actual
contents to stdout — `/proc/<PID>/environ` carries the raw value;
counting matches without echoing is the safe shape.

### Check 4 — in-process env vs registry parity (Windows)

```powershell
$inProc = $env:TASTYTRADE_REFRESH_TOKEN
$inReg  = [Environment]::GetEnvironmentVariable("TASTYTRADE_REFRESH_TOKEN", "User")
if (-not $inProc) { "in-process: ABSENT" } else { "in-process trailing-4: $($inProc.Substring($inProc.Length - 4, 4))" }
if (-not $inReg)  { "registry:   ABSENT" } else { "registry   trailing-4: $($inReg.Substring($inReg.Length - 4, 4))" }
"match: $($inProc -eq $inReg)"
```

`match: True` is the green signal. Trailing-4 is enough to verify a
match without echoing the secret; if both trailing-4 strings differ,
the in-process PS is stale and verification scripts run from it will
hit the old token.

### Check 5 — refresh token JWT scope is what was requested

Run `scripts/check_tt_token_scope.py`. The script reads the token
from `$env:TASTYTRADE_REFRESH_TOKEN` (default) or from stdin
(`--stdin`), decodes the JWT body, and prints only the `scope` and
`exp` claims. The token value is never echoed to stdout, never
accepted as a CLI argument, and never written to disk:

```powershell
# Local Windows shell, reading the just-rotated registry value.
# Pre-flight 2 (Set-PSReadlineOption -HistorySaveStyle SaveNothing)
# MUST be active.
.\scripts\run_capped.ps1 python scripts/check_tt_token_scope.py
```

```bash
# Prod-VM bash, reading the deployed env. Run under the trading-corp
# service user so the env is loaded the same way the running process
# loads it.
sudo -u trading-corp /home/azureuser/trading_corp/.venv/bin/python \
    scripts/check_tt_token_scope.py
```

Expected output (two lines, no token):

```
scope: read+trade
exp:   <unix-timestamp-in-the-future>
```

Acceptance:
- `scope` must contain every scope requested at grant time. **If
  `scope` is `read` after you requested `read+trade`, the OAuth app
  config didn't permit the wider scope** — gotcha 1 fired silently
  (see `[[reference-tastytrade-oauth-scope-widening]]`), the rotation
  produced a narrower token than intended, and any code path
  requiring `trade` will 403 at use time. Re-do § The atomic 2-step
  rotation Step 1 sub-step 2 (widen the app in the developer portal),
  then Step 1 sub-steps 3–6 (re-bootstrap against the widened config),
  then all of Step 2 (write the new matched pair to every live load
  path).
- `exp` must be a Unix timestamp in the future. A past `exp` means
  the token is already expired at rotation time — should not happen
  for a freshly-granted token; if it does, the OAuth grant flow
  returned an old token (storage / clipboard contamination).

If the script exits with code 3 ("failed to decode JWT body"), the
value in storage is not a JWT — Symptom B in § Failure-chain
diagnosis. If exit code 2 ("token not found"), the load path the
script is reading from has no value at all — re-check Pre-flight 1's
load-path verification.

### Check 6 — live SDK session.refresh() succeeds on prod (the only proof)

On the prod VM, via the deployed code path — never via bash source
of the env file:

```bash
sudo -u trading-corp /home/azureuser/trading_corp/.venv/bin/python -c '
import os, asyncio
from tastytrade import Session
async def main():
    s = Session(provider_secret=os.environ["TASTYTRADE_PROVIDER_SECRET"],
                refresh_token=os.environ["TASTYTRADE_REFRESH_TOKEN"])
    await s.refresh()
    print("session.refresh: OK")
asyncio.run(main())
'
```

**This is the only freshness check that proves the matched pair
authenticates end-to-end.** All five previous checks can be green
while this fails — they verify storage and shape; only this verifies
the server accepts the pair.

### Check 7 — both consuming surfaces verified

The runbook is not done until both surfaces have been smoked under
the new credentials:

- **IC division:** grade a fresh candidate via
  `POST /telemetry/iron_condor/grade` (paste an in-universe Barchart
  row). Verdict must reach gate 7 (term_structure) or beyond on real
  numbers — same criterion as
  `planning/ic_grader_section6_closure_20260523.md` § Corrected §6.
  A `NEEDS_LIVE_DATA` outcome at gate 7 means `get_atm_iv` is
  returning None, which means the new credentials are not actually
  flowing through to the provider. Investigate before declaring
  rotation done.
- **tasty_options:** run `scripts/tasty_sandbox_smoke.py` via
  `.\scripts\run_capped.ps1 python scripts/tasty_sandbox_smoke.py`
  (locally) — all 4 probes must report PASS. The smoke uses
  `Session(is_test=True)` against TT CERT, which exercises the same
  OAuth credential pair (TT doesn't issue separate sandbox creds —
  see `[[reference-tastytrade-sdk-sandbox-mode]]`).

Both green = rotation complete. Either red = rotation not complete;
diagnose via § Failure-chain diagnosis.

---

## Failure-chain diagnosis

Two rotation cycles' worth of observed symptoms. For each: the
**symptom** the operator sees, **how to detect** it without
re-leaking the secret, and the **recovery step**. The diagnosis half
is the point — a rotation runbook without it just repeats last time's
fumble.

### Symptom A — `invalid_grant: Grant revoked`

**Where:** journal on prod immediately after restart, or in the
`session.refresh()` traceback in Check 6.

**What's happening:** the refresh token currently in env was issued
under a Client Secret that no longer exists. Either the operator
rotated the Client Secret in the developer portal without re-running
the OAuth grant, or the storage path (env file / KV) was never
updated to the new pair.

**Detect:**
```bash
sudo stat -c '%y' /etc/trading-corp/tastytrade.env   # mtime before / during rotation window?
az keyvault secret show --vault-name <vault-name> --name TASTYTRADE-REFRESH-TOKEN --query "attributes.updated"
```
If either timestamp predates the OAuth grant, that storage path is
holding a revoked token.

**Recover:** re-run § The atomic 2-step rotation, sub-step 5 onward
(re-grant against the current Client Secret + re-write to all live
storage paths + restart).

### Symptom B — `invalid_grant: Invalid JWT`

**Where:** same as A.

**What's happening:** the refresh token in storage does not parse as
a JWT. The OAuth flow returned the wrong token type, OR the value
got truncated / mangled in transit (typically by a shell mishap or a
copy-paste through a privacy browser that stripped trailing
characters).

**Detect:** Check 5's JWT-scope probe. If `tok.split(".")` doesn't
yield 3 parts, or the middle segment doesn't base64-decode to JSON,
the value is not a JWT. The probe doesn't echo the value — it just
fails to decode.

**Recover:** re-run § The atomic 2-step rotation sub-step 5 in a
**standard browser** (not privacy browser — see
`[[feedback-oauth-use-standard-browser]]`); confirm the value starts
with `eyJ` before pasting it into Step 2's storage commands.

### Symptom C — `invalid_grant: Client secret mismatch`

**Where:** same as A.

**What's happening:** the refresh token IS a JWT, IS unrevoked, but
was granted under a different Client Secret than the one currently in
storage. Cross-pollination between OAuth sessions. The 2026-05-22
incident's third failure mode — second bootstrap retry had a fresh
JWT but the Client Secret in env was from the FIRST retry's session.

**Detect:** there is no in-band detector for this. The symptom IS the
detector. **Atomicity is the only prevention** — Step 2 of the
rotation writes Client Secret AND refresh token together, never one
without the other, both from the same OAuth bootstrap session.

**Recover:** discard whatever's in storage (do not try to keep one
half "because it's probably right"). Re-run § The atomic 2-step
rotation from sub-step 3, all sub-steps in one sitting, writing both
halves to all live storage paths together.

### Symptom D — `403 Forbidden: Token has insufficient scopes for this request`

**Where:** at the call site that requires the missing scope.
Read-only calls (get_atm_iv, get_market_data, get_option_chain) need
`read`; order placement needs `trade`. Most commonly seen on
tasty_options when its `place_order` is hit with a `read`-only
token.

**What's happening:** the OAuth app config did not permit the wider
scope at grant time, so TT silently downgraded the granted scope to
what the app allows. The token works for everything its actual scope
covers; it 403s on anything that needs the missing scope.

**Detect:** Check 5 — JWT scope claim. If `scope` is missing the
requested scope, the downgrade already happened.

**Caveat:** the SDK's `validate_response` in `tastytrade/utils.py`
SWALLOWS the scope-error into a DEBUG log when the error shape
doesn't match its expected key set. **Enable DEBUG logging** before
diagnostics — the surfaced `TastytradeError` ends up with an empty
message string otherwise:
```python
import logging
logging.getLogger("tastytrade").setLevel(logging.DEBUG)
```

**Recover:** widen the OAuth app's allowed scopes in
`developer.tastytrade.com`, save the app config, then re-run § The
atomic 2-step rotation from sub-step 3 (Client Secret + grant
against widened config).

### Symptom E — in-process env stale after `setx`

**Where:** local verification scripts read the wrong value after a
fresh rotation. Specifically: a verification script run from the
PowerShell that issued `setx` will read the OLD value, while a child
process (or a new PowerShell window) will read the NEW value.

**What's happening:** `setx` writes to `HKCU:\Environment` (the
Windows User-scope registry). Only NEW PowerShell processes inherit
the registry value at launch. The PowerShell that ran `setx` keeps
the OLD value in its in-process env block until close+reopen.

**Detect:** Check 4's `match: True/False` probe.

**Recover:** either close and reopen the PowerShell (loses Claude
Code session state), or refresh in-process:
```powershell
$env:TASTYTRADE_PROVIDER_SECRET = [Environment]::GetEnvironmentVariable("TASTYTRADE_PROVIDER_SECRET", "User")
$env:TASTYTRADE_REFRESH_TOKEN   = [Environment]::GetEnvironmentVariable("TASTYTRADE_REFRESH_TOKEN",   "User")
```
The in-process refresh affects the current PS only; child processes
inherit it; future PS sessions read from the registry directly.

### Symptom F — Client Secret value leaked into chat / logs / Azure activity log

**Where:** anywhere stderr is captured by a log harness. The
2026-05-22 incident: an env file containing literal `<value>`
placeholder brackets, then a `set -a; . /etc/trading-corp/tastytrade.env`
in a verification script. Bash interpreted `<` as a redirect
operator, syntax-errored, and **echoed the offending line — including
the value — to stderr**. `az vm run-command invoke` captured the
stderr into its return payload. The Client Secret landed in chat
transcript + Azure activity log.

**Detect:** scan the chat / log surface for the leaked-value pattern.
Specifically, the Client Secret has a stable shape (TT issues a
40-character alphanumeric); a grep for that shape across recent
chat transcripts is the detector. (No examples here — that would
re-leak.)

**Recover:**
1. **Rotate immediately.** Treat any leaked Client Secret as
   compromised regardless of perceived blast radius. Re-run § The
   atomic 2-step rotation against a NEW Client Secret.
2. **Record the leak forensic trail** in `runbooks/deploy_log.md` —
   leak surface (chat / Azure log / both), values still-or-no-longer
   visible (depends on whether the source is editable), risk
   assessment, remediation timeline.
3. **Never `bash source` env files thereafter** — use the
   python-direct loader from `[[feedback-never-bash-source-env-files]]`.

---

## Cross-surface impact (mandatory check)

`trading_corp/data/tastytrade_provider.py` now feeds **two divisions**:

| Surface | Calls | What breaks if rotation incomplete |
|---|---|---|
| IC division (candidate grader) | `get_atm_iv`, `get_market_data`, `get_option_chain` | Gate 7 (term_structure) returns `NEEDS_LIVE_DATA`; grader degrades to mock-style output |
| Tasty Options division (Phase 1 paper) | All of the above plus order shape via `TastytradeBroker` (paper-wrapped) | Phase 1 paper observation generates no signal; eventual Phase 2 trade-scope calls 403 |

**A rotation is not done until both surfaces have been verified live.**
Specifically: Check 7 in § Freshness verification.

---

## Don't

- **Don't** paste Client Secret, refresh token, JWT body, or full
  OAuth redirect URLs (with `?code=...`) into chat, into commits,
  into deploy_log entries, or into this runbook. Names, paths, and
  commands only.
- **Don't** `bash source` / `. file` / `set -a; . file` against the
  env file. Bash echoes the offending line on syntax error;
  `az vm run-command` captures stderr. See
  `[[feedback-never-bash-source-env-files]]`.
- **Don't** rotate the Client Secret without immediately running the
  OAuth grant against it. The matched pair must come from one
  bootstrap session. See Symptom C.
- **Don't** assume `setx` propagated to the current PowerShell. It
  doesn't. See Symptom E.
- **Don't** declare rotation done after Check 1–5 if Check 6
  (session.refresh) hasn't run. The first five verify storage shape;
  only Check 6 verifies the server accepts the pair.
- **Don't** ship the rotation alongside other code changes. Rotations
  should be standalone deploys (or no-deploy if storage-only) so a
  rotation failure is isolated from a code failure.
- **Don't** widen OAuth scope without an explicit Board decision —
  `read+trade` enables order placement on Tasty's side; the Backtester
  approval gate (`PROJECT_CONTEXT.md § 11`) is what gates the
  trading_corp side flipping `auto_execute: true`. Decouple the two.
- **Don't** edit this runbook without Board approval (CLAUDE.md §4 —
  runbooks are a recovery contract). Append-only updates with a
  "Revision history" tail are the path if the procedure changes
  materially.

---

## Related

- `[[reference-tastytrade-oauth-scope-widening]]` — the JWT scope
  decoder + `setx`-propagation gotchas in machine-readable form.
- `[[feedback-never-bash-source-env-files]]` — the
  bash-source-leak-via-stderr pattern that made the 2026-05-22
  Client Secret leak possible.
- `[[feedback-oauth-use-standard-browser]]` — the privacy-browser
  query-param-stripping gotcha that adds 20+ minutes of debugging
  to any OAuth grant.
- `[[reference-tastytrade-sdk-sandbox-mode]]` — `is_test=True` uses
  the same prod OAuth credentials against the CERT endpoint; one
  rotation covers both.
- `[[project-data-provider-deploy]]` — the 2026-05-22 deploy whose
  rotation incident produced the first half of this runbook's
  forensics.
- `[[project-tasty-options-paper-clock]]` — the second consuming
  surface; Phase 1 paper observation depends on the credentials in
  this rotation runbook.
- `planning/ic_grader_section6_closure_20260523.md` — the §6
  acceptance criterion used in Check 7 (IC verification).
- `runbooks/2026-05-25_tasty_sandbox_smoke_runbook.md` — the
  smoke runbook used in Check 7 (tasty_options verification).
- `runbooks/deploy_log.md` 2026-05-22 16:47 UTC entry — the
  three-failure-mode rotation incident whose forensics seed § Failure-
  chain diagnosis Symptoms A / B / C.

---

## Revision history

- 2026-05-26 — initial version. Consolidates 2026-05-22 rotation
  incident (Symptoms A / B / C / F) and 2026-05-25 tasty_options
  OAuth work (Symptoms D / E). No prior versions.
