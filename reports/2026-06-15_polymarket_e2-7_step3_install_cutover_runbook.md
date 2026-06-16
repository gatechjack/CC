# E2·7 Step 3 — install / cutover / OP·E (paste-safe, OPERATOR-run, flat window)

**PREP ARTIFACT — nothing here is executed by a Claude session. Every block is GT_Jack's to run.**
§4 / `82fda13`: operator runs all installs, restarts, the live flip, the order. This is the **corrected**
Step-3 runbook — see the changelog at the bottom for the 7 fixes vs the prior draft (all verified by a
read-only prod probe 2026-06-15).

**Prod facts (read-only-probed 2026-06-15):**
- Host `azureuser@trading.jacksumner.com` · VM `tc-prod-vm` · dir `trading_corp` (underscore) · venv
  `/home/azureuser/trading_corp/venv` · py3.12.13.
- **systemd unit = `trading-corp.service` (HYPHEN).** The directory/module is `trading_corp` (underscore);
  only the `.service` unit token is hyphenated.
- Live ExecStart today: `… venv/bin/python -X utf8 -m trading_corp --live --brokers bitunix` (PID 2727670).
  Broker/division args are **ExecStart CLI flags** (not env, not a config file).
- Passwordless (NOPASSWD) ssh-sudo: `systemctl {restart,start,stop,status,daemon-reload} trading-corp*`,
  `journalctl -u trading-corp*`. **General file edits need a sudo password (unavailable via ssh)** → unit
  edits go via `az vm run-command` (root), per the 2026-06-13 go-live pattern.

**Pre-conditions before you start this file:**
- Step-1 guard preview vs the LIVE venv = clean (~23 NEW, **1 ALLOWED EXCEPTION** `setuptools 82.0.1→80.10.2`,
  **0 CHANGED**). Anything under CHANGED → STOPPED, not here.
- Step-2 done: `e2-7-deps-lock-fix-2026-06-15` (+ validation) merged to main and **pushed**; the prep branch's
  stale `deploy/` NOT merged.
- **Flat window confirmed: no open Bitunix position, no imminent Bitunix entry.** Live Bitunix shares the venv
  — the 3B restart bounces it.

> Note: a few diagnostic lines below are long single commands (one ssh invocation). Action/critical-path
> commands are kept ≤~100 chars for clean paste.

---

## 3A — Install-only (NO restart)

> The additive guard logs the ALLOWED EXCEPTION here (setuptools downgrade) and proceeds. No service bounce —
> lazy imports mean live Bitunix and paper PCT keep running on already-imported modules; the new pkgs sit on
> disk unused until 3B.

```
# PROD IS NOT A GIT CHECKOUT — /home/azureuser/trading_corp has no .git (`git rev-parse` →
# "not a git repository", probe-confirmed). There is NO git fetch/merge/status on prod; ALL artifacts
# (lock, txt, the deploy script) arrive by scp / ssh-stdin from the operator's LOCAL checkout.
# `git push origin main` (already done) updated GitHub CANONICAL only — it did NOT reach prod. Canonical
# is git (origin/main); prod is kept in sync by the operator's FILE COPY, not a pull.

# (i) scp the install inputs from the LOCAL checkout to prod /tmp (deploy_e1_lock.sh reads + md5-gates
#     /tmp/requirements.{lock,txt}, aborts if absent). Run from the local deploy dir:
cd "C:\Users\AA Incorporado\cc\deploy\polymarket_e1"
scp requirements.lock requirements.txt azureuser@trading.jacksumner.com:/tmp/
# >>> the script md5-gates these: lock a47fc93e2103bd4687ac8bd8717759c4 / txt 2aee61909bc22cf4fdf6f68ca5166fa3.

# (ii) md5-diff pre-state (engineering gate): capture the installed set BEFORE install (read-only)
ssh azureuser@trading.jacksumner.com "cd trading_corp && venv/bin/pip freeze | md5sum"

# (iii) install-only, hashed, NO restart. The SCRIPT also arrives by stdin (NOT a prod-side copy) — pipe
#       deploy_e1_lock.sh through ssh→bash; it reads /tmp/requirements.{lock,txt} (BASE is absolute):
gc deploy_e1_lock.sh -Raw | ssh azureuser@trading.jacksumner.com "tr -d '\r'|bash"
# >>> expect in output: "ALLOWED EXCEPTION (web3 6.11 pkg_resources fix, scoped): setuptools: 82.0.1 -> 80.10.2"
#     then "ADDITIVE OK — N new packages to add, 1 allowed exception(s), 0 unexpected changes". The "(no restart
#     performed)" footer confirms it did not bounce the service.
```

**3A pass-check (all must hold before 3B):**
```
# setuptools downgraded on disk to 80.10.2 (the running process still holds the old one until 3B)
ssh azureuser@trading.jacksumner.com "cd trading_corp && venv/bin/pip show setuptools | grep Version"
# >>> expect: Version: 80.10.2

# live Bitunix STILL RUNNING, NOT bounced — MainPID + ActiveEnterTimestamp UNCHANGED from before 3A
ssh azureuser@trading.jacksumner.com "systemctl show trading-corp.service -p MainPID -p ActiveEnterTimestamp"
# >>> expect: same MainPID (2727670 unless it restarted for an unrelated reason) and same ActiveEnterTimestamp
#     as before 3A. If it bounced → incident, see 3A abort.
```

**STOP after 3A.** Deps on disk, nothing restarted, PCT still paper. Proceed to 3B only in the (still) flat window.

### 3A abort
- Install fails mid-run → roll back to the md5 pre-state set (reinstall the prior pinned versions); do NOT
  restart to "fix" it (a restart is what pulls a half-changed set into the live process). Restore first, re-run
  the step-1 guard preview, then re-attempt.
- Bitunix bounced unexpectedly during install → treat as incident: confirm Bitunix came back clean on the OLD
  modules, confirm no live order was affected, and DEFER the cutover (don't carry an unplanned restart into the
  timeline).

---

## 3B — Cutover: arm PCT live, single restart

> The one venv bounce. Live Bitunix re-imports on the new locked deps here. **Arming requires TWO ExecStart
> changes, not one** (see below), applied via `az vm run-command` (the unit file is root-owned and general sudo
> needs a password unavailable over ssh), then `daemon-reload` (NOPASSWD ssh-sudo) BEFORE the restart.
>
> **Order — every edit lands BEFORE the ONE restart so they ride it together (no extra bounce):**
> **3B.0** flip the `divisions.yaml` broker line → **3B.1** ExecStart arm → **3B.2** daemon-reload →
> **3B.3** single restart → verify.

### Why two args (critical — the slug alone is a silent no-op)
The E2·4 gate (`main.py:1980-1982`): `is_live_division = family_live_capable AND slug ∈ live_divisions`, where
`family_live_capable = (mode==LIVE and family in --brokers)`. Today `--brokers bitunix` → the **polymarket family
is not live-capable**, so adding `--live-divisions polymarket_copy_trading` *alone* leaves PCT **PAPER** with no
error. You must add **both**: `polymarket` to `--brokers` **and** the slug to `--live-divisions`. Arb's slug
`polymarket_arbitrage` is deliberately **left out** of `--live-divisions` → arb runs under `--brokers polymarket`
but stays read-only/paper (E2·4 per-division isolation; the `--live-divisions` help text and `main.py:1976-1978`
use exactly this example).

**Corrected ExecStart (only the broker/division args change; everything else byte-identical to the live unit):**
```
ExecStart=/usr/bin/xvfb-run --auto-servernum --server-args="-screen 0 1280x800x24" /home/azureuser/trading_corp/venv/bin/python -X utf8 -m trading_corp --live --brokers bitunix polymarket --live-divisions polymarket_copy_trading
```

### 3B.0 — flip PCT's `divisions.yaml` broker line: `paper → polymarket` (DO THIS FIRST, before 3B.1)
**Why (the silent-stay-paper gap):** the construction branch is chosen by `family = division.broker`
(`main.py:1971`), **not** by `--brokers`. PCT's prod entry is `broker: paper` → `family == "paper"` → PCT builds
the PAPER broker no matter what the ExecStart args say. So the broker line MUST be flipped to `polymarket` first,
or the whole cutover is a silent no-op.

**Pre-edit stale-file check (operator — flagged, NOT resolved here):** prod's `config/divisions.yaml` is dated
**May 31** and may be stale vs origin/main. The targeted broker-line edit below is safe *because* it touches only
that one line and preserves the rest — but if prod is materially stale the operator should reconcile first.
Read-only diff:
```
scp azureuser@trading.jacksumner.com:trading_corp/config/divisions.yaml /tmp/prod_divisions.yaml
# then diff /tmp/prod_divisions.yaml vs config/divisions.yaml in the local checkout (read-only)
```

**Delivery — a TARGETED in-place edit (NOT a full-file scp, NOT git).** Prod's `config/divisions.yaml` is
**azureuser-owned** (`-rw-r--r-- azureuser`), so a direct ssh edit works — **no sudo/az** (unlike the root-owned
unit file). Run a small `@file` via stdin (mirrors the deploy/arm pattern; keeps the paste line short). Create
`flip_pct_broker.sh` locally:
```
#!/usr/bin/env bash
set -euo pipefail
F=/home/azureuser/trading_corp/config/divisions.yaml
n=$(grep -c 'broker: paper' "$F")        # guard: broker:paper must be UNIQUE to PCT
[ "$n" = "1" ] || { echo "ABORT: 'broker: paper' x$n (expected 1=PCT); use a slug-ranged edit"; exit 1; }
cp -p "$F" "$F.bak-pre-pct-flip-$(date -u +%Y%m%d-%H%M)"
sed -i 's/broker: paper/broker: polymarket/' "$F"
echo "flipped:"; grep -n -A1 'slug: polymarket_copy_trading' "$F" | grep broker
```
Deliver + run it (azureuser-owned → no sudo):
```
gc flip_pct_broker.sh -Raw | ssh azureuser@trading.jacksumner.com "tr -d '\r'|bash"
```
- >>> expect `flipped: broker: polymarket` under `polymarket_copy_trading`. If the guard ABORTS (`broker: paper`
  not unique), STOP and use a slug-ranged sed (`/slug: polymarket_copy_trading/,/- slug:/ s/broker: paper/broker:
  polymarket/`) instead.

**`standby` (optional, cosmetic):** PCT's entry is `standby: true` — a UI badge only, **no functional gate**
(the setup loop gates on `enabled`, not `standby`; probe-confirmed). Flip to `false` for UI accuracy if you like;
it is **not required** for live trading.

**Timing — rides the SAME single restart.** `divisions.yaml` is read once at startup (`_read_yaml` is
`@lru_cache`; broker build `main.py:547`), so this flip takes effect only on the next restart — and that's the
**same single 3B restart (3B.3) below.** Do this edit NOW (before 3B.1); it loads on that one restart — **no
extra Bitunix bounce.**

### 3B.1 — edit ExecStart via `az vm run-command` (root; @file, NOT inline — inline gets mangled)
Create a local `@file` `arm_pct.sh` (backs up the unit, appends the two args to the ExecStart line, prints the
result so the az output shows it):
```
#!/usr/bin/env bash
set -euo pipefail
U=/etc/systemd/system/trading-corp.service
cp -p "$U" "$U.bak-pre-pct-arm-$(date -u +%Y%m%d-%H%M)"
sed -i 's# --brokers bitunix$# --brokers bitunix polymarket --live-divisions polymarket_copy_trading#' "$U"
echo "NEW ExecStart:"; grep '^ExecStart=' "$U"
```
Invoke it (operator's Azure CLI, authenticated; fill the resource group — same one used at go-live):
```
az vm run-command invoke -g <RESOURCE_GROUP> -n tc-prod-vm --command-id RunShellScript --scripts @arm_pct.sh
```
- >>> read the command output: the `NEW ExecStart:` line MUST show `--brokers bitunix polymarket --live-divisions
  polymarket_copy_trading`. The `sed` anchors on the line ending in `--brokers bitunix`; if it didn't match
  (ExecStart already differs), STOP — do not daemon-reload an unexpected unit.

### 3B.2 — daemon-reload (NOPASSWD ssh-sudo) — REQUIRED before restart
```
ssh azureuser@trading.jacksumner.com "sudo systemctl daemon-reload"
```
> Without this the restart reuses systemd's cached OLD ExecStart and **PCT stays paper silently.**

### 3B.3 — the single restart (NOPASSWD ssh-sudo)
```
ssh azureuser@trading.jacksumner.com "sudo systemctl restart trading-corp.service"
```

### 3B.4 — confirm Bitunix came back CLEAN before any PCT order
```
ssh azureuser@trading.jacksumner.com "systemctl status trading-corp.service --no-pager | head -20"
ssh azureuser@trading.jacksumner.com "journalctl -u trading-corp.service -n 60 --no-pager | tail -50"
# >>> expect: service active; Bitunix connected/resuming; NO web3/pkg_resources traceback (that would mean the
#     setuptools fix didn't take under the real restart); no error spew.
```

### 3B.5 — verify the ARM took (ground truth) — two steps to stay paste-safe
```
ssh azureuser@trading.jacksumner.com "systemctl show trading-corp.service -p MainPID --value"
# >>> note the new MainPID, then (substitute <PID>):
ssh azureuser@trading.jacksumner.com "tr '\0' ' ' </proc/<PID>/cmdline; echo"
# >>> expect the cmdline to carry BOTH: --brokers bitunix polymarket --live-divisions polymarket_copy_trading
```

### 3B.6 — verify the ISOLATION (E2·4 anti-half-flip, checked live)
There is no single per-division "live/paper" log line; verify via the live broker's bootstrap instead:
```
ssh azureuser@trading.jacksumner.com "journalctl -u trading-corp.service -n 200 --no-pager | grep -iE 'preflight|approv|funder|polymarketlive|connect'"
# >>> expect: the polymarket connect() PREFLIGHT ran (funded/approved checks) — that only happens for the LIVE
#     PolymarketLiveBroker, i.e. PCT armed live. The read-only PolymarketBroker (arb) has no connect preflight.
```
- PCT live: preflight present (above) + at OP·E it places a real tx with `execution_mode='live'`.
- Arb paper: arb's slug is absent from `--live-divisions` (confirmed in 3B.5) → it resolves the read-only
  `PolymarketBroker` by construction (E2·4 tests assert this) → arb never places. Confirm operationally that
  **no live tx / `execution_mode='live'` row appears for `polymarket_arbitrage`** at/after OP·E.
- **Silent-stay-paper closed (ties to 3B.0):** the preflight's PRESENCE also confirms the 3B.0 broker flip took —
  with the old `broker: paper`, PCT would resolve the read-only broker and emit **no preflight at all**. Preflight
  present ⇒ `broker: polymarket` loaded **and** both args armed ⇒ a LIVE `PolymarketLiveBroker`. (No preflight
  despite the ExecStart args = the broker flip didn't land — re-check 3B.0.)

**3B pass-check:** service active + Bitunix healthy; no web3/pkg_resources failure in logs; `/proc/cmdline` carries
both args; polymarket preflight ran (PCT live); `connect()` preflight PASSED (wallet provisioned — 119.978358
USDC.e, 6/6 approvals). If preflight ABORTS (unfunded/unapproved) → STOP, something changed on-chain; PCT stays paper.

### 3B abort
- Bitunix not clean after restart → abort the PCT flip; Bitunix recovery first. If the new deps are implicated,
  reinstall the md5 pre-state and restart again. Do NOT start PCT live on an unhealthy shared venv.
- `connect()` preflight fails → do NOT place; investigate funding/approvals on-chain (trust the gate); PCT stays paper.

### 3B disarm (back PCT out of live — same mechanism, NOT a flag toggle)
Re-run 3B.1's `az vm run-command @file` with a sed that **removes** the args (drop `--live-divisions
polymarket_copy_trading`, and `--brokers ... polymarket` too if backing out entirely), e.g.
`sed -i 's# polymarket --live-divisions polymarket_copy_trading##'` → then **daemon-reload → restart**. PCT reverts
to the read-only `PolymarketBroker`, nothing placeable. (Editing the unit + reload + restart is the only path —
there is no runtime flag.)

---

## 3C — OP·E: the $1 real-money shakedown

> First and ONLY real-money validation (no CLOB sandbox). One ~$1 copy, watched live. This is the real-SDK
> exercise the mocked E2·6 tests could not satisfy.

Pre-conditions: 3B clean (Bitunix healthy, PCT armed per 3B.5/3B.6, preflight passed). Sizing flat ~$1
(E2·3 default: 120 × 0.00833 × 1.0 = ~$1, clamped [0.50, 2.00]).

**Trigger:** one pinned-whale BUY routes live — the loop's live branch calls `data_exec.place()` (E2·6 gate
`isinstance(broker, Broker)`), synthesized-FAK (E2·2): GTC → poll `fak_poll_seconds` → cancel remainder → FillEvent.

**Watch the log live and confirm the things mocks could not prove:**
```
ssh azureuser@trading.jacksumner.com "journalctl -u trading-corp.service -f --no-pager"
```
- Order went **LIVE** (real tx), not paper.
- **`execution_mode='live'`** written to `proposed_order` + `paper_trade_record` (E2·5).
- **Synthesized-FAK on a real book** — filled / partial / no-fill in window?
  - **Partial** → confirm entry write-back (E2·6 `record_entry_fill`): recorded position = ACTUAL filled qty, not
    intended ~$1. Live proof of the invariant.
  - **No-fill** → `NoFillInWindow` → benign skip (log.info, no alarm, no position); `discard_entry` left no phantom lot.
- **`size_matched` truthfulness** (carry-forward / issue #245): confirm it matches tokens actually received; flag
  if it overstates.

**⚠ Watch the EXIT of this position by hand.** The exit-side write-back is NOT built (E5 gap): if this $1 position
is later SOLD and the exit no-fills/partials, the strategy pops the position and believes it's flat while holding a
residual lot. Manually reconcile if it happens. This is the gap that must close before ANY scaling.

### 3C abort
- Order behaves unexpectedly (wrong size/token, unreconcilable status) → disarm PCT (3B disarm → paper). $1 is the
  blast radius by design.

---

## After a clean OP·E — do NOT scale yet
1. **Close the exit-side E5 reconciliation gap** (issue #245 root) — prerequisite to scaling past $1.
2. **Copy-roster review** of the 5 pinned whales — gates conviction sizing (YAML flip `sizing.conviction.enabled:
   true`); NOT needed for flat-$1.
3. Proportional sizing later → revisit static `bankroll_usdc ~120` vs a live-balance read.

## Forward note (carry)
`setuptools<81` is a MITIGATION, not the durable fix: 81+ removes `pkg_resources`, breaking web3 6.11. The pin
must hold; the real fix is a future web3 upgrade. Don't let a careless lock regen bump setuptools past 81. (The
scoped additive-guard exception is pinned to exactly `82.0.1→80.10.2`, so a different setuptools would re-trip
the guard — that's intentional.)

---

## One-screen
1. **[OP]** Step-1 guard preview vs live venv → clean (0 CHANGED, 1 allowed exception) before anything.
2. **[OP]** Merge fix branch (+validation), NOT the stale prep `deploy/`. Push.
3. **[OP][flat]** 3A: **scp lock+txt to /tmp** (NO git on prod — it's a file-deployed copy) → pip-freeze md5 →
   install-only via the **piped** `deploy_e1_lock.sh` (no restart). Verify setuptools 80.10.2, Bitunix MainPID
   un-bounced. STOP.
4. **[OP][flat]** 3B: **3B.0 flip `divisions.yaml` `broker: paper → polymarket` FIRST** (else silent-stay-paper) →
   `az vm run-command @file` ExecStart with **`--brokers bitunix polymarket` AND `--live-divisions
   polymarket_copy_trading`** → `daemon-reload` → ONE restart `trading-corp.service` (the broker flip rides it) →
   Bitunix clean, no web3 traceback, `/proc/cmdline` both args, polymarket preflight ran (PCT live / arb paper).
5. **[OP]** 3C: one $1 live copy. Confirm live tx, `execution_mode='live'`, FAK behavior, write-back if partial,
   `size_matched`. **Watch the exit by hand.**
6. Don't scale: close the E5 exit gap + roster review first.

---

## Changelog — 9 fixes vs the prior Step-3 draft (all verified read-only on prod 2026-06-15)
1. **Unit name** → `trading-corp.service` (HYPHEN) everywhere; the underscore `trading_corp` is dir/module only.
2. **3A /tmp staging** added — `deploy_e1_lock.sh` reads + md5-gates `/tmp/requirements.{lock,txt}` and aborts if
   absent; the prior draft would abort (txt was never staged).
3. **3A git-status guard** added before the ff-only merge (diverged/locally-edited checkout surfaces visibly).
4. **3B arming = BOTH args** — `--brokers bitunix polymarket` AND `--live-divisions polymarket_copy_trading`; the
   slug alone leaves PCT silently paper (E2·4 family-live-capable gate).
5. **3B mechanism** — ExecStart edit via `az vm run-command @file` (general sudo needs a password unavailable over
   ssh), then `sudo systemctl daemon-reload` (NOPASSWD ssh-sudo) BEFORE restart, then restart.
6. **3B post-restart verify** — `/proc/<MainPID>/cmdline` (arm took) + the polymarket `connect()` preflight in the
   journal (PCT live; arb has no preflight → paper). No guessed per-division log line (none exists).
7. **3B disarm** corrected to the real mechanism (az `@file` ExecStart edit dropping the args → daemon-reload →
   restart), not a flag toggle.
8. **Delivery model: git → scp (the big one).** Prod (`/home/azureuser/trading_corp`) is NOT a git checkout
   (probe: `git rev-parse` → "not a git repository") — it's a file-deployed copy. **Removed** 3A's `git fetch` /
   `git merge --ff-only origin/main` / `git status` block entirely; ALL artifacts (lock, txt, the deploy script)
   now arrive by **scp / ssh-stdin** from the LOCAL checkout (the script is piped `gc … | ssh "tr -d '\r'|bash"`,
   not run against a prod-side copy). `git push origin main` updates GitHub **canonical only** — it does NOT reach
   prod; the operator keeps prod in sync by file copy.
9. **3B.0 — `divisions.yaml` broker flip (new step-0).** `family = division.broker` (`main.py:1971`), so PCT's
   `broker: paper` builds a PAPER broker regardless of `--brokers`/`--live-divisions` (silent-stay-paper). Added a
   **targeted in-place** `broker: paper → polymarket` edit (azureuser-owned file → no sudo/az, unlike the unit
   edit) **before** the ExecStart arm; it **rides the SAME single restart** (config read-once at startup via
   `_read_yaml` `@lru_cache`). `standby` flagged cosmetic (UI-only). Plus a stale-prod-file operator check
   (read-only diff before editing) and a 3B.6 verify tie-in (preflight presence confirms the flip took).
