# Next-session pickup prompt (post C-6 lockfile correction)

*Written 2026-05-24 ~15:30 UTC at the end of a TRACK D-DEPLOY session that shipped — but only after catching that the original `4086221` lockfile was generated from `requirements.txt` against current PyPI (silently bumping 43 packages). Regenerated from prod's actual running freeze, reversed the disk install, achieved three-way convergence (disk ≡ lock ≡ process, all OLD). No process restart. PID 1237405 unchanged throughout.*

---

Paste this into a fresh Claude Code session at `C:/Users/AA Incorporado/cc`:

---

Resuming after the 2026-05-24 ~15:30 UTC C-6 lockfile-correction session. **Two commits on `origin/main`** pushed this session (head now `7a3e439`):

- `7a3e439` — deploy_log: backfill commit SHA in 2026-05-24 15:14 UTC entry
- `e5556ef` — lockfile: regenerate against prod running versions (C-6 correction)

Plus an earlier fix this session (`e5efa06`, copy-trader NameError) and pre-session work pushed during D's flow (`05ba56c`, `0bcb2ba`, `2dd12bf`).

**Prod state is STABLE and CONVERGED:**
- Process PID 1237405 (xvfb-run wrapper) + 1237421 (python child) — Apr-30 venv build, never restarted this session.
- Disk packages == running process == lockfile pins. All OLD versions. `pip install --dry-run --require-hashes -r requirements.lock` reports 137 "Requirement already satisfied", zero "Would install".
- Lockfile md5 on prod = `c1d1db5f2a435ab9ba797b8448ca3287`.
- Backup of pre-correction (bad) lockfile preserved at `/home/azureuser/trading_corp/requirements.lock.bad-bump-20260524`.

## Read first

1. **`BACKLOG.md` top entry** (EOS 2026-05-24 ~15:30 UTC) — canonical wrap. Lists prod state, what's held, untracked-files inventory.
2. **`runbooks/deploy_log.md` top entry** (2026-05-24 15:14 UTC, commit `e5556ef`) — full verification record incl. rollback recipe.
3. **Memory:** `[[project-security-tracks-fbd-shipped-2026-05-23]]` (updated body — C-6 deploy + correction), `[[feedback-lockfile-regen-from-running-state]]` (new — the lesson).

═══════════════════════════════════════════════════════════════════════════
## What to work on next — Board picks ONE
═══════════════════════════════════════════════════════════════════════════

The CRITICAL queue: C-6 closed. C-2 still patched-but-not-deployed (HELD). C-1 secret rotation unchanged.

### TRACK B-DEPLOY — Push C-2 webhook risk-gate fix to prod (1–2h, gated)

`19ff0da` — highest-leverage item still queued. §4 webhook-path change → needs **explicit in-session approval**. The acceptance test is the load-bearing piece: trigger or wait for a real `push_back`, then verify in DB that the LLM veto is now an audited reject row.

Steps:
1. Backup on prod: `web/webhooks.py`, `agents/risk.py`, `agents/research/trade_confirmation_consult.py`.
2. `scp` the 3 files (mind the 28 KB `az --scripts` cap — use scp).
3. `sudo systemctl restart trading-corp`.
4. **Post-deploy verification (load-bearing):** wait for a real push_back; confirm in DB:
   ```sql
   SELECT ts, payload FROM audit_event
    WHERE kind='risk_rejected'
      AND json_extract(payload,'$.source')='llm_push_back'
    ORDER BY ts DESC LIMIT 1;
   ```
   No row in the first few real consults → deploy didn't take → rollback.
5. Append deploy_log entry per template.

**Rollback:** swap files back, `systemctl restart`. Skip-risk-gate bypass resumes — known-bad but operational.

**Why this needs its own session:** "deploy and walk away" is wrong here. You need to actively watch for the first real push_back to confirm the fix landed. Don't bundle with passive work.

### P1 — Deferred 43-package upgrade from C-6 lockfile drift (multi-session, audited one-at-a-time)

The bumps the C-6 correction reversed. Full table in `BACKLOG.md` P1 entry. **NOT a single batch.** Sequence:

1. **anthropic 0.97 → 0.104** — highest risk class. Per `[[feedback-mocks-dont-catch-sdk-shape]]`: real-SDK smoke test + live authenticated call. Audit `agents/llm.py`, `agents/research/*`, `agents/strategies/kalshi_crypto_arb.py` for return-shape dependencies before bumping.
2. **cryptography 47 → 48** — major. Check release notes for deprecations in `utils/secrets.py` (KV path), mTLS broker adapters, `web/webhooks.py` HMAC.
3. **langgraph 1.1.10 → 1.2.1 + langchain-core 1.3.2 → 1.4.0** — check `graph/ceo_graph.py` for deprecated APIs.
4. **starlette 1.0.0 → 1.1.0 + fastapi 0.136.1 → 0.136.3** — web layer.
5. ~30 patch/minor bumps last — low individual risk.

Each bump = its own audit + deploy + soak. **No "and while we're at it".**

### TRACK A — C-1 Secret rotation (1–3h, coordination-heavy, unchanged)

The most consequential remaining CRITICAL. Every broker portal, Anthropic console, BotFather, KV upload. Sequence with the P1 NOPASSWD:ALL fix (both touch VM state in the same envelope). Do not start without uninterrupted time.

### TRACK C — `strategies.yaml` schema + mtime + audit (2h, §4)

C-3 fix. Pydantic schema + mtime cache + `strategies_yaml_reloaded` audit row. Touches `graph/ceo_graph.py:113-267`. §4 protected.

### TRACK E — Tastytrade KV consolidation (1h, §4)

P1 "Tastytrade env vars bypass KV path". Patch list in `[[feedback-tastytrade-env-vars-bypass-kv]]`. Standalone since the AM SDK fix already shipped. §4 protected.

### NEW P1 — fix `azureuser` `NOPASSWD:ALL` sudo (1h, §4 VM)

Filed in `8d72dcc`. `/etc/sudoers.d/*` has `azureuser ALL=(ALL) NOPASSWD:ALL` — partially undermines the C-4 remediation. Replace blanket NOPASSWD with narrow allowlist. Edit via `visudo` only; keep a second SSH session open as testing pad before closing the first.

### Housekeeping — phantom-pointer cleanup

`runbooks/strategy_harness_inventory.md` is untracked on disk but referenced by memory `[[reference-strategy-harness-inventory]]`. Either:
- (a) `git add` + commit the file standalone (it IS substantive, May 22 20:59, 5.6 KB), OR
- (b) Update the memory to remove the pointer.

Low effort, defensible cleanup task to slot between bigger work.

═══════════════════════════════════════════════════════════════════════════
## Untracked files in working tree at session start
═══════════════════════════════════════════════════════════════════════════

Same 4 as session end of 2026-05-24 ~15:30 UTC. Inventory:

- `decode_losses.py` — operator-created 2026-05-24 ~15:13 UTC inline; weather-arb resolved-trade base64 chunks for loss decoding. **Operator's analysis artifact — leave alone unless they ask.**
- `runbooks/strategy_harness_inventory.md` — substantive runbook (May 22), referenced by memory `[[reference-strategy-harness-inventory]]`. **Phantom pointer — see housekeeping above.**
- `scripts/fetch_kalshi_weather_corpus.py` — pre-session paginated prod-DB extraction script (May 22). Likely from kalshi_weather P3 work.
- `docs/Deployment notes.txt` — long-standing operator notes (May 20, 640 KB). **Operator-owned.**

═══════════════════════════════════════════════════════════════════════════
## Hard rules — don't skip
═══════════════════════════════════════════════════════════════════════════

- **CLAUDE.md §1 invariants** — risk gate single chokepoint; audit before each branch; paper default; `scripts/run_capped.ps1` wrap for Python; `runbooks/` no-edit without approval.
- **CLAUDE.md §4** — webhook/risk path changes need in-session approval (TRACK B applies).
- **Deploys append to `runbooks/deploy_log.md`** per template.
- **No restart for restart's sake.** Process PID 1237405 has been alive since Apr-30; the venv install + correction did NOT restart it. Same restart-avoidance applies to TRACK B (when it eventually deploys, restart is REQUIRED — that's fine, that's the design).

═══════════════════════════════════════════════════════════════════════════
## Discipline notes from prior session (worth keeping)
═══════════════════════════════════════════════════════════════════════════

- **`! ssh ...` inline command wrapping bug** — Claude Code's prompt wrap insertion of newlines split `systemctl status <unit>` twice this session. Workarounds: keep `status` adjacent to unit name on same line, use a `for u in ...` loop, or put long commands in a heredoc.
- **Lockfile regen rule** (NEW memory `[[feedback-lockfile-regen-from-running-state]]`): reproducibility locks must compile from prod's `pip freeze`, NOT from `requirements.txt` against current PyPI — the latter silently bumps. Verification chain: PID unchanged + `pip install --dry-run` zero changes + `diff` of pre vs post freeze exit 0 + journal clean.
- **PM watchlist `--merge` cron landed clean today 13:08:07 UTC** (197 → 329 whales, +132 net). First weekly fire of windowed-union path validated. Next fire Sun 2026-05-31 13:12:45 UTC. No action needed.
