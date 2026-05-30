# BitUnix Panic-Halt Runbook

**Last verified:** 2026-05-30, code surfaces at `origin/main` commit `03f3261`.

**Owner:** operator performs the visible halt + flatten actions; this runbook
documents the sequence. No credential values, no order details copy-pasted into
chat. Names, paths, and commands only.

**Companion runbook:** if the trigger is (or might be) a credential compromise,
read `runbooks/bitunix_credential_compromise.md` FIRST — Section 2 of that
runbook cross-references back here for the halt half, and credential rotation
without a halt-first can extend the exposure window.

---

## What this is / when to use

The operator-visible procedure to **stop bitunix futures trading and flatten
open positions** when something is wrong: bad strategy behavior, suspicious
fills, broker connectivity oddness, account anomaly, or just "this feels wrong
and I want to be safe while I investigate."

This is **not** the everyday stop procedure — that's `auto_execute: false` in
`config/strategies.yaml` + a planned restart. This is the **incident**
procedure, which prioritizes "no new exposure and no open exposure" over "clean
graceful shutdown."

**Prerequisite for live-order relevance:** as of `2026-05-30`, prod runs
`4985bbe + 03:57 sed-overlay` (pre-Stage-1-merge), so `BitunixBroker.place_order`
on prod is still a `NotImplementedError` stub — accidental live placement is
structurally impossible. The first prod-deploy of `main` (gated on P1 (a) REST
resilience + (b) this runbook + (c) md5-diff) is what makes this runbook
load-bearing. Until that deploy, the procedures below run against a
paper-wrapped surface and serve as practice + verification rehearsal.

---

## Decision criteria

| Symptom | Right response |
|---|---|
| One strategy emitting bad-looking orders, others fine | **Targeted halt** (§ A) — flip `bitunix_futures.auto_execute: false`, flatten only the bitunix surface, leave the rest of the system running. |
| Multiple strategies misbehaving / system-wide anomaly | **Full panic halt** (§ A.3) — `sudo systemctl stop trading-corp`, flatten on the bitunix UI, investigate. |
| Suspicious fills + can't determine cause in <2 min | **Halt now, diagnose later.** Treat as panic; resume requires explicit operator decision per § Resume. |
| Suspected credential compromise (leaked key, unauthorized portal activity, stolen device) | **`runbooks/bitunix_credential_compromise.md` FIRST**, which links back to § A here. |
| Connectivity issue (broker down, REST 5xx storm, snapshot stale) | **`auto_execute: false` + monitor.** The broker self-latch (`_halt_new_orders`) already refuses new orders on `BitunixPositionModeMismatch`; REST flakiness alone does NOT auto-halt today (filed as P1 gate (a)). Operator decides whether to flatten or wait. |
| "Bot fighting me" — I want to close a position manually and stop the bot from re-opening | **`auto_execute: false` + manual close on bitunix UI.** Do NOT just close on the UI without flipping the flag first — without the flag, the strategy may immediately re-enter on the next bar. |
| Operator unsure | Treat as panic. Cheap to halt; expensive to under-react. |

Resume conditions for each are in § Resume; don't resume until those are
met.

---

## Section A — Halt order placement

Three independent paths. Use them in this order; each is more drastic than
the last. **Path A.1 is hot-reloaded (no restart, sub-second effect).**

### A.1 — Primary: flip `auto_execute: false` (hot-reload, no restart)

`config/strategies.yaml` line ~1021 `bitunix_futures.auto_execute` is re-read
on **every** order decision (mtime-cached); flipping it to `false` stops new
entries within one decision cycle. No process restart needed.

**On prod:**
```bash
# Backup then sed-in-place — preserves CRLF and other lines.
BASE=/home/azureuser/trading_corp
TAG=.pre-panic-halt-$(date -u +%Y%m%dT%H%M%SZ)
sudo cp -p $BASE/config/strategies.yaml $BASE/config/strategies.yaml$TAG
sudo sed -i '/^bitunix_futures:/,/^[a-z]/ s/^  auto_execute: true$/  auto_execute: false/' \
    $BASE/config/strategies.yaml
# Verify the flip landed on the bitunix line only:
sudo grep -n -A 4 '^bitunix_futures:' $BASE/config/strategies.yaml | grep auto_execute
```

Expected output: `auto_execute: false` on the `bitunix_futures` block; other
strategies' `auto_execute` values untouched.

Service does NOT need restart — `bitunix_futures_observer` re-reads YAML on
the next decision tick. Within ~1s of the file save, the bot stops placing
new entries. Audit trail: `would_have_placed` rows continue (the strategy
still proposes; the executor refuses).

**Why this is primary:** it's reversible by editing the same line back, and
it preserves all the audit/observation infrastructure. Use it 95% of the
time.

### A.2 — Secondary: broker self-latch (structural backstop, no operator action needed)

`BitunixBroker._halt_new_orders` is a process-memory bool that the broker
flips to `True` and raises `BitunixPositionModeMismatch` on (see
`trading_corp/brokers/bitunix.py:687-703`). Any later `place_order` call
short-circuits at the latch check. The mismatch consumer in
`data_exec.py:_handle_position_mode_mismatch` (`:223-289`) writes a
`position_mode_mismatch_detected` audit row and pushes a `safety_alert`
telegram.

**The operator does not invoke this directly.** It fires automatically when
the broker observes an account state that doesn't match expectations (the
ONE_WAY position mode invariant from the 2026-05-29 operator decision).
Document the existence here so that during an incident, the operator knows
the bot has a *structural* halt already, even if A.1 is somehow bypassed.

If the operator wants to **manually** trigger the broker self-latch (e.g.
to force a confirmation-of-halt audit row), the supported entrypoint is
the `data_exec.flatten_division("bitunix_futures")` call in § B.2 — its
preamble latches `_halt_new_orders = True` (see `bitunix.py:865-866` inside
`flatten()`). The flatten and the latch ride together; you don't latch
without flattening from this path.

### A.3 — Tertiary: stop the service entirely

If A.1 fails (YAML edit can't land, file system issue, or the operator wants
to also stop all other divisions): kill the systemd service. Restart is
manual.

```bash
sudo systemctl stop trading-corp
# Verify:
systemctl is-active trading-corp     # expect: inactive
sudo systemctl status trading-corp | head -8
```

This stops every division (Polymarket scanners, PMCC, Kalshi paper, etc.),
not just bitunix. Use only when the wider system is suspect or A.1 is not
viable. Resume = § Resume's full-restart path.

---

## Section B — Flatten open positions

Three paths. **The BitUnix UI path is primary** because it doesn't depend
on the bot being healthy.

### B.1 — Primary: BitUnix portal UI "Close All Positions"

After login at `https://www.bitunix.com` (operator's BitUnix account, the
same account whose API key is in KV `BITUNIX-FUTURES-API-KEY`):

1. Navigate to **Futures** → **Position list**.
2. Above the open-positions table is a **"Close All"** button (or
   per-position "Close" actions if the bulk button isn't present in your
   UI version).
3. Confirm. BitUnix submits market-close orders for every open position.

Why primary: this works even when the bot is stopped, the API is down,
or the credentials are compromised (you're authenticating with the
operator's portal session, not the API key). It's the fastest and most
robust path.

**Verification:** the same Position list must show **zero positions** after
the close. Wait ~5–10 seconds for fills to land and the list to refresh.

### B.2 — Secondary: bot-side `flatten_division` (only if bot is healthy)

`data_exec.flatten_division("bitunix_futures")` (`agents/data_exec.py:326-450`)
runs the broker's `flatten()` kill-switch (`brokers/bitunix.py:861-876`),
which:
1. Sets `_halt_new_orders = True` with `_halt_reason = "flatten() kill-switch invoked"`.
2. Calls `cancel_all_orders()` (`bitunix.py:834`) — cancels every resting
   order account-wide.
3. Calls `close_all_position()` (`bitunix.py:851`) — submits market-close
   for every open position.
4. Re-snapshots and verifies `positions_after == 0`; writes
   `flatten_account_executed` audit on success or `flatten_account_failed`
   audit + escalated `🚨 FLATTEN FAILED` telegram on failure.

**Bot-side entrypoint** (only viable when the service is up; if you ran
A.3, this path is unavailable until you restart per § Resume):

There is no admin HTTP endpoint for `flatten_division` today. The supported
manual invocation is a short Python REPL on the prod VM, using the loaded
deps:

```bash
# On prod, as the running service user, with the project's venv.
sudo -u azureuser KEY_VAULT_URI=https://kv-tc-vtwbowt3wtkpy.vault.azure.net/ \
    /home/azureuser/trading_corp/venv/bin/python -c '
import asyncio
from trading_corp.main import build_deps, get_settings
async def main():
    deps = await build_deps(get_settings())
    await deps.data_exec.flatten_division("bitunix_futures")
asyncio.run(main())
'
```

**Use sparingly.** This spins up a parallel deps tree in a one-shot process,
which means the live trading-corp service ALSO holds connections to
bitunix. The kill-switch path doesn't conflict (idempotent flatten + halt
latch), but the spin-up takes ~30s. The UI path (B.1) is faster and
broker-independent.

**Verification:** the call returns silently on success. Confirm via:
- `flatten_account_executed` row in `audit_event` (positions_before > 0 →
  positions_after == 0).
- BitUnix portal UI shows zero positions.
- `safety_alert` telegram "✅ Flatten executed on `bitunix_futures`" arrives.

If `flatten_account_failed` row appears, the verify-via-snapshot failed —
manually close any remaining positions via § B.1 and audit the failure
mode before resuming.

### B.3 — Fallback: per-position manual close on BitUnix UI

If B.1's bulk "Close All" is absent in your UI version, or fails for any
position: close per-position from the same Position list. Each row has a
"Close" action (market-close at the live mark). Verify zero positions
after each.

---

## Section C — Halt verification

After § A + § B, confirm the system is actually safe — don't trust
"I clicked the buttons." Each check verifies independent state:

### C.1 — Process state (only relevant if you took § A.3)

```bash
systemctl is-active trading-corp        # expect: inactive (A.3) OR active (A.1 only)
systemctl show -p MainPID --value trading-corp
```

If A.1 only: service is still `active`, MainPID stable, bot is just refusing
new orders.

### C.2 — Zero positions on BitUnix UI

`https://www.bitunix.com` → Futures → Position list. **Empty.**

If non-empty: re-run § B.1 or § B.3 on the stragglers. Do NOT proceed to
§ D until empty.

### C.3 — Bot agrees account is flat (only if A.1 only — service is up)

The bot's view of positions comes from `BitunixBroker.snapshot()`. A
divergence between "UI says 0" and "snapshot says N>0" means the broker
is reading stale data — the bot may re-enter when it sees a position
where there isn't one (or, worse, where there IS one but it doesn't
match expectations). Verify:

```bash
# On prod, value-blind probe of the broker's view.
sudo -u azureuser KEY_VAULT_URI=https://kv-tc-vtwbowt3wtkpy.vault.azure.net/ \
    /home/azureuser/trading_corp/venv/bin/python -c '
import asyncio
from trading_corp.brokers.bitunix import BitunixBroker
from trading_corp.utils.secrets import load_secrets
async def main():
    s = load_secrets()
    b = BitunixBroker(
        api_key=s.bitunix_futures_api_key,
        api_secret=s.bitunix_futures_api_secret,
        paper=False,
    )
    await b.connect()
    snap = await b.snapshot()
    print(f"snapshot positions: {len(snap.positions or [])}")
    await b.disconnect()
asyncio.run(main())
'
```

Expected: `snapshot positions: 0`. If non-zero, the UI close hasn't fully
propagated — wait 30s and re-run. If still non-zero, the position is real
and § B did not fully flatten.

### C.4 — Audit row spot-check

Confirm the safety side-effects landed:

```bash
sudo sqlite3 /home/azureuser/trading_corp/data/trading_corp.db \
    "SELECT ts, kind, json_extract(payload_json, '\$.division') AS division
     FROM audit_event
     WHERE kind IN ('flatten_account_executed','flatten_account_failed',
                    'flatten_account_noop_already_flat',
                    'position_mode_mismatch_detected','telegram_notification_failed')
       AND ts >= datetime('now', '-30 minutes')
     ORDER BY ts DESC LIMIT 20;"
```

Expected: one `flatten_account_executed` (or `_noop_already_flat`) row for
`bitunix_futures` within the incident window. A `flatten_account_failed`
row is a NOT-DONE signal — the flatten reported failure, do NOT resume.

### C.5 — Telegram confirmation received

`✅ Flatten executed on bitunix_futures` (success) or
`🚨 FLATTEN FAILED on bitunix_futures` (failure) message landed in the
operator's chat. **Audit-success-is-confirmed-delivery** (per
`[[telegram-audit-success-is-confirmed-delivery]]`): if the audit row
exists but the telegram did not arrive, look for a
`telegram_notification_failed` row in the same audit query above —
that's the explicit "push failed" trail.

---

## Section D — Post-incident

Before deciding to resume:

1. **Audit log review for the incident window.** Query the strategy's
   activity rail for the ±30 min window around the halt — `would_have_placed`,
   `filled`, `webhook_received`, `alert_ignored`, `agent_error`,
   `position_mode_mismatch_detected`:

   ```bash
   sudo sqlite3 /home/azureuser/trading_corp/data/trading_corp.db \
       "SELECT ts, actor, kind, substr(payload_json,1,200) AS payload
        FROM audit_event
        WHERE (json_extract(payload_json, '\$.strategy') = 'bitunix_futures'
               OR json_extract(payload_json, '\$.division') = 'bitunix_futures')
          AND ts >= datetime('now', '-30 minutes')
        ORDER BY ts;"
   ```

2. **Determine root cause vs. symptom.** A halt is a symptom-management
   tool. The follow-up question is *why* — was it a strategy bug, a broker
   API regression, an operator misconfiguration, a market move? Until you
   know which, do not resume — you'll re-trigger.

3. **Decide:** resume immediately, resume with config change, or escalate
   (defer resume; investigate further; consult Board if available).

4. **Document the incident in `runbooks/deploy_log.md`** with a section
   header `## YYYY-MM-DD HH:MM UTC — Bitunix panic-halt — <one-line cause>`
   even if no deploy happened. Operational incidents belong in the deploy
   log because they're load-bearing context for future
   "did anything change recently?" questions. Include: trigger, halt
   command sequence used, flatten path used, audit-row evidence, decision
   on resume timing.

---

## Section E — Resume

Resume only after **all** of:

- [ ] Root cause known (or explicitly accepted as "monitor for recurrence
      with the resume").
- [ ] If a config change is the fix: change applied + reviewed + (where
      relevant) test-gated.
- [ ] If a code change is the fix: PR'd through normal review + merge +
      deploy gate per `[[feedback-deploy-import-graph-audit]]`.
- [ ] BitUnix portal UI shows zero positions (re-verify; market can move
      between halt and resume).
- [ ] `audit_event` query in § C.4 has no `flatten_account_failed` rows
      since the halt.
- [ ] Pre-flip md5-diff (`scripts/bitunix_prod_surface_md5diff.py`) is
      clean — confirms prod surface matches the source you're trusting.
- [ ] Operator-explicit decision to resume, recorded in
      `runbooks/deploy_log.md`.

### E.1 — Resume from A.1 (auto_execute false)

```bash
BASE=/home/azureuser/trading_corp
sudo sed -i '/^bitunix_futures:/,/^[a-z]/ s/^  auto_execute: false$/  auto_execute: true/' \
    $BASE/config/strategies.yaml
sudo grep -n -A 4 '^bitunix_futures:' $BASE/config/strategies.yaml | grep auto_execute
```

Expect: `auto_execute: true`. Within ~1s the bot resumes accepting strategy
proposals (it had been queuing them as `would_have_placed` during the
halt). The first real order after resume is the live test — operator
watches the dashboard activity rail.

### E.2 — Resume from A.3 (service stopped)

```bash
sudo systemctl start trading-corp
# Wait for the 6-min Robinhood device-challenge + web-bind cycle (per the
# pattern in deploy_log.md — RH device challenge fires on operator's phone
# during startup; web command center binds last).
sleep 30 && systemctl is-active trading-corp && systemctl show -p MainPID --value trading-corp
# Full healthz check after web bind:
curl -s -o /dev/null -w "%{http_code}\n" https://trading.jacksumner.com/healthz
```

Expected: `active`; MainPID changes from pre-halt; `healthz` returns `200`.

If you also flipped A.1 during the incident, flip it back per E.1.

### E.3 — Resume after credential rotation

See `runbooks/bitunix_credential_compromise.md` § Post-rotation verification
+ § Resume. Order matters: verify new credentials work end-to-end FIRST,
then run E.1 to allow auto-execute, then resume strategies.

---

## Don't

- **Don't** flatten before halting. § B without § A means the bot is still
  running and will re-enter immediately after the flatten lands. Halt
  first, flatten second. Always.
- **Don't** assume a UI close means the bot's view is consistent. § C.3
  is what proves the snapshot agrees.
- **Don't** restart the service to "force a halt" — restart re-fires the
  Robinhood device challenge on the operator's phone and re-binds web
  after ~6 min. Use A.1 (sub-second, no restart) for the halt itself;
  reserve restart for E.2 (resume) or A.3 (system-wide stop).
- **Don't** edit this runbook without Board approval (CLAUDE.md § 4 —
  runbooks are a recovery contract). Append-only updates with a
  `Revision history` tail are the path if the procedure changes
  materially.
- **Don't** declare the halt done after § A alone. § B (flatten) + § C
  (verify) are non-skippable — a halt without a flatten leaves the
  existing position un-managed until manual closure.
- **Don't** resume without § D's root-cause step. "It seems fine now" is
  not a root cause.
- **Don't** rely on Telegram for confirmation alone — § C.4 audit-row
  spot-check is the source of truth (audit_event is canonical;
  telegram is best-effort delivery per
  `[[telegram-audit-success-is-confirmed-delivery]]`).

---

## Related

- `runbooks/bitunix_credential_compromise.md` — credential rotation
  procedure; cross-references § A here for the halt prerequisite.
- `runbooks/deploy_log.md` — record incidents here even when no deploy
  happens; future "did anything change?" queries need this.
- `runbooks/2026-05-29_bitunix_live_readiness_audit.md` § 10 — original
  readiness-audit text that motivated this runbook (Stage-1 SMALL-MEDIUM
  gap, P1 pre-deploy gate).
- `scripts/bitunix_prod_surface_md5diff.py` — pre-resume verification
  that the prod surface matches git (P1 gate (c), shipped 2026-05-30
  commit `59c4b06`).
- `trading_corp/brokers/bitunix.py:861-876` — `flatten()` kill-switch
  implementation (halt latch + cancel_all_orders + close_all_position).
- `trading_corp/agents/data_exec.py:326-450` — `flatten_division`
  consumer with snapshot-verify discipline.
- `[[bitunix-order-path-safety-pattern]]` — design notes on the
  mode-mismatch consumer + confirmed-delivery discipline.
- `[[telegram-audit-success-is-confirmed-delivery]]` — why audit_event
  is the source of truth, not telegram.

---

## Revision history

- 2026-05-30 — initial version (P1 gate (b) closure, architectural-review
  Finding #2 Readiness #11). Code surfaces verified at `origin/main` commit
  `03f3261`. Prod still at `4985bbe + 03:57 sed-overlay`; runbook becomes
  load-bearing on the first prod-deploy of `main` (gated on remaining P1
  items).
