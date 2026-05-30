# Uncommitted prod surgical-edits audit — 2026-05-30

**Scope:** read-only direct-probe audit of every `*.pre-stage1-20260530-1230` backup file preserved on `tc-prod-vm` after the 17:22–17:34 UTC Stage 1 deploy rollback, plus extended audit of `config/risk.yaml` (not in deploy transfer set) and systemd EnvironmentFile keys (names only).

**Trigger:** rollback root-cause (latent `secrets.odds_api_key` AttributeError at `main.py:1087`) revealed that prod's `secrets.py` carried an uncommitted field. Item 1 (commit `71ff0a5`) round-tripped the field + populator. This audit (Item 2) determined whether `odds_api_key` was the only such divergence or one of N siblings that must reach `main` before the next deploy attempt.

**Result:** the audit found **one (1) uncommitted prod-only addition** across all 14 audited surfaces — `odds_api_key` in `secrets.py` — and it manifests in **four sublocations**, of which Item 1 round-tripped two. The remaining two locations (`_SECRET_KEY_NAMES` line 49, `expected_env_vars` line 241) are closed by Item 2 commit `ffbb09b` on the same branch. All other observed divergences are Category C (main-ahead-of-prod, will be normalized by the next clean deploy).

---

## TL;DR

| | |
|---|---|
| **Files audited (with backup)** | 13 |
| **Extended surfaces** | `config/risk.yaml`, systemd unit + drop-ins, EnvironmentFile keys, running-process env keys |
| **Live-vs-backup md5 probe** | 13/13 EQ (clean rollback verified; live working tree = backup byte-identical) |
| **Category A — uncommitted prod-only additions requiring git round-trip** | **1 symbol (`odds_api_key`), 4 sublocations: 2 closed by commit `71ff0a5`, 2 closed by commit `ffbb09b`** |
| **Category B — needs human review** | 0 |
| **Category C — main-ahead-of-prod (deploy will normalize)** | 11 files, ~84 prod-only line tokens (all stale code or comment drifts) |
| **Branch carrying the fix** | `stage1-forward-fix-2026-05-30` (off `stage1-deploy-2026-05-30`; carries the deploy_log + BACKLOG rollback entries forward); pushed to origin |
| **HEAD** | `ffbb09b` |
| **Test gate** | 2046 passed / 26 failed / 3 errors (baseline `2044/26/3` + 2 new tests). Zero regressions in the 2044 baseline pass set. |
| **Deploy-unblock status** | All Item 1 + Item 2 round-trips landed on branch. Pending: operator merge to `origin/main`. The next deploy attempt is a separate session. |

---

## Method

1. **Bundle on prod (one az call):** `tar -czf /tmp/backups_2026_05_30.tgz $(find . -name '*.pre-stage1-20260530-1230')` → md5 `ce78074a85f1ee8cdaf3dedc54e4e715`, 183521 bytes. 13 files captured.
2. **Live-vs-backup parity probe (one az call):** for each file, `md5sum live` vs `md5sum backup` — all 13/13 EQ. Confirms the rollback restored prod byte-identically to the pre-deploy state; the backups ARE the live working tree.
3. **Pull (62 chunks):** base64-encoded tarball pulled in 4000-byte b64 chunks via `dd if=/tmp/backups_2026_05_30.tgz.b64 bs=4000 count=1 skip=N status=none` over `az vm run-command`. ~33s per chunk on this VM extension cadence; total wall ~38 min. End-to-end verified by md5 round-trip locally (`ce78074a85f1ee8cdaf3dedc54e4e715` ✓).
4. **LF-normalize both sides:** prod files normalized via `text.replace("\r\n","\n").replace("\r","\n")`; main fetched via `git show origin/main:<path>` (which is LF-native regardless of `autocrlf`).
5. **AST symbol scan for `.py`:** `ast.walk` enumerates `FunctionDef`/`AsyncFunctionDef`/`ClassDef`/`AnnAssign`/`Assign` names. **For YAML:** top-level key regex.
6. **`git log --all -S "<symbol>" -- <path>`** per symbol — empty result = git-invisible (uncommitted prod-only). Non-empty = needs human review (could be a value the symbol set crosses a commit in some other path, or the symbol is real but on a stale-prod side too).
7. **Extended surfaces:** `config/risk.yaml` pulled in 3 chunks; systemd unit + drop-ins enumerated; EnvironmentFile keys listed (names only, no values).

**Discipline carriers (per the brief):**
- `[[verify-premises-against-ground-truth]]` — direct-probe-first; no claim about prod made without an az-probed verification.
- `[[reference-az-run-command-stdout-cap]]` — chunked-b64 pattern; per-chunk validation by total b64 length + decoded md5.
- Operator-supervised; STOP-and-report per finding (this report is the per-finding report).

---

## File-by-file results

### 1. config/divisions.yaml
- **Live=Backup:** `3e14dc5dccacac9e3fa3b45c4a04b165` ✓
- **vs `origin/main`:** **+0 / -26 lines** (main has `tasty_options` division registration block that prod doesn't have yet — known main-ahead per BACKLOG "tasty_options" entry).
- **Verdict:** Category C — no findings.

### 2. config/strategies.yaml
- **Live=Backup:** `61dd355082f936016810337058d30cd0` ✓
- **vs `origin/main`:** **+5 / -81 lines.** YAML top-level key delta: `tasty_options_iron_condor` is main-only.
- **Per-line investigation of the 5 prod-only lines:** all 5 are **comment-text drifts** where the YAML VALUE is identical between prod + main:
  - Line 1021 `auto_execute: true` — same value; comment text differs (`# NO per-trade HITL — caps are the gate` on prod vs `# mtime-hot-reloaded runtime kill switch (per-order read)` on main).
  - Line 1231/1233 `require_all: false` — same value; main adds inline comment `# 2026-05-27: was true; loosen to >=2 of 3 per replay (9606b9f synthesis)`.
  - Line 1232/1234 `min_validators_passed: 2` — same value; main adds inline comment `# 2026-05-27: required when require_all=false (defaults to 0 = PA-disabled)`.
  - Line 1492/1494 kalshi_weather_arb `enabled: false` — same value; em-dash (`—`) on main vs ASCII hyphen on prod in the comment.
  - Line 1546/1548 kalshi_crypto_arb `enabled: false` — same em-dash vs ASCII hyphen drift in the comment.
- **Verdict:** Category C — no behavioral divergence; main-ahead in comments only. Next deploy normalizes.

### 3. config/risk.yaml (extended audit — not in deploy transfer set, no `*.pre-stage1-20260530-1230` backup)
- **Probed via standalone `base64 -w0` pull** (3 chunks, ~100s wall).
- **md5 after LF-normalize:** prod `8296e915514fb58b5b1b97b650b05ebb` = main `8296e915514fb58b5b1b97b650b05ebb` ✓
- **Verdict:** byte-identical LF-normalized; the live md5 difference observed in the initial probe (`1d053d426dd31add7153884b4339e7e9` vs `8296e915514fb58b5b1b97b650b05ebb`) is entirely CRLF (prod) vs LF (main). No prod-only edit. No findings.

### 4. trading_corp/agents/data_exec.py
- **Live=Backup:** `a67b89c3508af462671836b04682757a` ✓
- **vs main:** **+3 / -392 lines.** Prod-only: `from typing import Iterable` import, `__init__(self, logger: LoggerAgent, *, dry_run: bool = False)` signature, `fill = await broker.place_order(order)` line — all stale signatures of pre-Stage-1 code.
- **Verdict:** Category C — main has the Stage-1 broker-write surgical refactor that prod hasn't received yet.

### 5. trading_corp/agents/divisions/bitunix_futures_observer.py
- **Live=Backup:** `ec2a0f74fb51001d9e58f7616a25f9de` ✓
- **vs main:** **+56 / -592 lines.** Prod-only lines are pre-Stage-1 paper-write-back logic (literal patterns: `StrategyState(strategy="bitunix_futures")`, `order.status = "would_have_placed"`, `PaperTradeRecord.from_order(...)`, etc.); main has the entry-path-scoped version per `[[bitunix-live-entry-path-2026-05-29]]`.
- **Verdict:** Category C — Stage-1 entry-path merge pending deploy.

### 6. trading_corp/agents/risk.py
- **Live=Backup:** `dfe9f54c71da183e2ad2f5909323f012` ✓
- **vs main:** **+1 / -14 lines.** Prod-only: one halt-reason-string format that main has updated.
- **Verdict:** Category C — small refactor on main.

### 7. trading_corp/brokers/bitunix.py
- **Live=Backup:** `61b406fa218900b15e5f2d2366cc7579` ✓
- **vs main:** **+21 / -928 lines.** Prod-only lines are Phase-1 read-only `NotImplementedError` shims that main has replaced with Phase-4 broker-write (`_request` + `place_order` + `_observe_fill` + `cancel_order` + flatten/close primitives, ~928 LOC of new code).
- **Verdict:** Category C — Stage-1 broker-write merge pending deploy.

### 8. trading_corp/comms/telegram_commands.py
- **Live=Backup:** `6862042590d58f81fe786a0e362529e0` ✓
- **vs main:** **+1 / -1 lines.** Prod-only: `StrategyState(strategy="robinhood_pmcc")` call-signature update.
- **Verdict:** Category C — small refactor on main.

### 9. trading_corp/graph/ceo_graph.py
- **Live=Backup:** `9038855877008dce274f534efcfc6eb9` ✓
- **vs main:** **+2 / -12 lines.** Prod-only: legacy `StrategyState(...)` instantiation pattern.
- **Verdict:** Category C — small refactor on main.

### 10. trading_corp/main.py
- **Live=Backup:** `e9b6da138c915d25dbecb857537e51cb` ✓
- **vs main:** **+10 / -184 lines.** Prod-only: 10x `StrategyState(strategy=agent.name, halted=False)` legacy instantiations. Main-only symbols include the tasty_division wiring (`_tasty_account_factory`, `_tasty_strategy_state_factory`, `_tasty_broker`, `_execution_mode`, `tasty_*` task constructors) — pending deploy.
- **Verdict:** Category C — Stage-1 wiring + tasty_options + StrategyState API refactor pending deploy.

### 11. trading_corp/persistence/models.py
- **Live=Backup:** `71108b3342ca0b3d4912fec2055f4356` ✓
- **vs main:** **+0 / -96 lines.** Main-only: `from_persistence`, `persist_halt`, `clear_halt`, `_AGENT_STATE_ACTOR` (per `[[bitunix-live-entry-path-2026-05-29]]` cross-process halt state).
- **Verdict:** Category C — Stage-1 persistence merge pending deploy.

### 12. trading_corp/utils/secrets.py **← Category A finding (sole)**
- **Live=Backup:** `ad434ab24e259524f8cdc026869063e2` ✓
- **vs main (origin/main pre-fix):** **+4 / -45 lines.** Main-only symbols: `tastytrade_provider_secret`, `tastytrade_refresh_token` (`[[tastytrade-refresh-token-no-self-rotation]]` rotation work on main, pending deploy).
- **Prod-only additions (4 sublocations of the same symbol):**

  | Sub | Line | Context | Item-1 (`71ff0a5`) | Item-2 (`ffbb09b`) |
  |---|---|---|---|---|
  | A | 49 | `_SECRET_KEY_NAMES` tuple (between `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PEM`) | — | ✅ added |
  | B | 144 | `Secrets` dataclass field | ✅ added | — |
  | C | 241 | `expected_env_vars` tuple (between `KALSHI_PRIVATE_KEY_PEM` and `APIFY_API_TOKEN`) | — | ✅ added |
  | D | 320 | `load_secrets()` populator line | ✅ added | — |

- **`git log --all -S "odds_api_key" -- trading_corp/utils/secrets.py` (pre-Item 1):** EMPTY across all branches. The symbol had never been on git. Confirms Category A (uncommitted prod-only addition).
- **Load-bearing verification (direct probe of systemd env sources):**
  - `/etc/systemd/system/trading-corp.service` has `Environment="KEY_VAULT_URI=https://kv-tc-vtwbowt3wtkpy.vault.azure.net/"`.
  - Drop-in `/etc/systemd/system/trading-corp.service.d/override.conf` adds `EnvironmentFile=/etc/trading-corp/tastytrade.env`.
  - `/etc/trading-corp/tastytrade.env` contains only `TASTYTRADE_PROVIDER_SECRET` + `TASTYTRADE_REFRESH_TOKEN` (no `ODDS_API_KEY`).
  - `/home/azureuser/trading_corp/.env` does NOT exist.
  - → `ODDS_API_KEY` must come from KV via `_populate_from_keyvault()`, which iterates `expected_env_vars`. Sublocation C is therefore LOAD-BEARING; without it the next clean deploy would silently stop pulling `ODDS-API-KEY` from KV, breaking `kalshi_sports_scout` + `kalshi_sports_arb_observer` to stub mode.
- **Verdict:** Category A — round-tripped to git on branch `stage1-forward-fix-2026-05-30` across 2 commits (`71ff0a5` + `ffbb09b`).

### 13. trading_corp/web/routes.py
- **Live=Backup:** `936c7f4e476f783916f8869aa714d15a` ✓
- **vs main:** **+3 / -3 lines.** Prod-only: legacy `StrategyState(...)` call signatures.
- **Verdict:** Category C — small refactor on main.

### 14. trading_corp/web/webhooks.py
- **Live=Backup:** `86db1afec568a871b8a6e634c3b37a64` ✓
- **vs main:** **+2 / -2 lines.** Same StrategyState refactor pattern.
- **Verdict:** Category C — small refactor on main.

---

## Extended-surface findings

### Systemd unit + drop-ins
- `Environment=KEY_VAULT_URI=https://kv-tc-vtwbowt3wtkpy.vault.azure.net/` (load-bearing — drives the KV pull at startup).
- `Environment=PYTHONIOENCODING=utf-8`, `Environment=PYTHONUNBUFFERED=1`.
- Drop-in `/etc/systemd/system/trading-corp.service.d/override.conf` adds:
  - `Environment=TELEGRAM_NOTIFICATION_ONLY=true`
  - `EnvironmentFile=/etc/trading-corp/tastytrade.env`
- Drop-in `/etc/systemd/system/trading-corp.service.d/override.conf.pre-data-provider-deploy-20260521` exists as a backup (pre-2026-05-22 tastytrade data-provider deploy; do-not-delete).

**Verdict:** no surgical-edit surface here that isn't either tracked in the repo (`infra/main.bicep` for the base unit) or documented in `runbooks/tastytrade_oauth_rotation.md` for the drop-in.

### EnvironmentFile keys (`/etc/trading-corp/tastytrade.env`, names only)
- `TASTYTRADE_PROVIDER_SECRET`
- `TASTYTRADE_REFRESH_TOKEN`

**Cross-reference vs `secrets.py`:** both keys are read via `_env("TASTYTRADE_PROVIDER_SECRET")` + `_env("TASTYTRADE_REFRESH_TOKEN")` at line 347-348 of main's secrets.py. Match. No drift.

### Running-process environ
- The `pgrep -f python.*trading_corp` probe captured an interactive SSH session's environ, not the systemd-spawned process's environ (likely a permissions or naming-collision artifact). Re-probing via the actual systemd PID (`systemctl status trading-corp` PID) is a follow-up if a discrepancy is suspected; no such suspicion surfaced from the file-level audit.

---

## Severity-ranked remediation list

| Severity | What | Where | Status |
|---|---|---|---|
| **P1 — load-bearing** | Add `"ODDS_API_KEY"` to `Secrets` dataclass + `load_secrets()` populator (sublocations B + D). Without these, next deploy crashes at startup (the original 17:22 UTC AttributeError). | `trading_corp/utils/secrets.py` lines 158 + 351 | ✅ LANDED on branch `stage1-forward-fix-2026-05-30` commit `71ff0a5` (pushed). |
| **P1 — load-bearing** | Add `"ODDS_API_KEY"` to `expected_env_vars` tuple (sublocation C). Without it, next deploy silently stops pulling `ODDS-API-KEY` from KV, forcing kalshi_sports_* into stub mode. | `trading_corp/utils/secrets.py` line ~272 (new line) | ✅ LANDED on branch `stage1-forward-fix-2026-05-30` commit `ffbb09b` (pushed). |
| **P2 — defense-in-depth** | Add `"ODDS_API_KEY"` to `_SECRET_KEY_NAMES` tuple (sublocation A). Closes the `KEY=value` log-redaction gap for `ODDS_API_KEY=<value>` lines. | `trading_corp/utils/secrets.py` line ~50 | ✅ LANDED on branch `stage1-forward-fix-2026-05-30` commit `ffbb09b` (pushed). |
| **P3 — discipline** | New AST-based completeness test against the `Secrets`-dataclass surface. Catches this class of bug at test time, not deploy time. | `tests/test_secrets_completeness.py` | ✅ LANDED on branch `stage1-forward-fix-2026-05-30` commit `71ff0a5` (pushed). |
| **P3 — standing rule** | File memory entry `[[prod-vs-git-filesystem-audit-discipline]]` codifying the pre-deploy filesystem audit pattern. | `memory/feedback_prod_vs_git_filesystem_audit_discipline.md` | ⏳ EOS — pending. |
| **P3 — review surface** | Add Finding #1e (5th drift-class locus) + Finding #9.g (pre-deploy filesystem audit recommendation) to architectural review on branch `stage1-architectural-review-2026-05-30`. | `reports/2026-05-30_stage1_bitunix_live_engine_architectural_review.md` | ⏳ Item 4 — pending operator (a)/(b)/(c) decision. |

---

## Deploy unblock criteria

| | |
|---|---|
| Item 1 (`secrets.odds_api_key` field + populator + completeness test) | ✅ on branch (`71ff0a5`); pushed; NOT merged |
| Item 2 (audit + remaining 2 sublocations + extended-surface verification) | ✅ on branch (`ffbb09b`); pushed; NOT merged |
| Operator merges branch → `origin/main` | ⏳ pending |
| `[[prod-vs-git-filesystem-audit-discipline]]` standing rule active for next pre-deploy | ⏳ pending |
| New deploy session per Plan A (18-file whole-file transfer + pre-flight gates + RH-pickle-coordinated restart) | ⏳ separate session |

**Net effect on prod:** zero. No bytes touched. Tarball at `/tmp/backups_2026_05_30.tgz` remains in place for forensics (cleanable next session); `*.pre-stage1-20260530-1230` backups remain in place (do-not-delete).

---

## Cross-references

- **Branch carrying Item 1 + Item 2 + audit:** `stage1-forward-fix-2026-05-30` HEAD `ffbb09b`; pushed to origin; NOT merged.
- **Original rollback entry:** `runbooks/deploy_log.md` "## 2026-05-30 17:22–17:34 UTC" (on branch `stage1-deploy-2026-05-30` — `58a1807` + `a8a8b66`; carried forward by `stage1-forward-fix-2026-05-30`).
- **Memory entries referenced:** `[[stage1-deploy-rolled-back-2026-05-30]]`, `[[verify-premises-against-ground-truth]]`, `[[mocks-dont-catch-sdk-shape]]`, `[[no-documented-leaky-escape-hatch]]`, `[[reference-az-run-command-stdout-cap]]`, `[[tastytrade-refresh-token-no-self-rotation]]`, `[[bitunix-live-entry-path-2026-05-29]]`, `[[branch-tests-must-cover-existing-fixtures-not-only-new-tests]]`.
- **Architectural review** (separate branch, not merged): `reports/2026-05-30_stage1_bitunix_live_engine_architectural_review.md` — Findings #1e + #9.g pending Item 4 application.
- **BACKLOG**: "Stage-1 prod-deploy BLOCKED" P1 entry updated to reflect Item 1 + Item 2 LANDED status (separate commit).

---

## Audit log (chronological, UTC)

- **18:25** — Item 1 (odds_api_key field + populator + completeness AST test) shipped on branch `stage1-forward-fix-2026-05-30` commit `71ff0a5` + pushed.
- **18:30** — 13 prod backups bundled into `/tmp/backups_2026_05_30.tgz` (md5 `ce78074a85f1ee8cdaf3dedc54e4e715`, 183521 bytes).
- **18:46** — Pull v1 (Python subprocess on Windows) started; hung silently at chunk 21 because `subprocess.run` was passing `az.CMD` directly to `CreateProcess` — Windows requires `shell=True` (cmd.exe) for `.CMD` interpretation. Killed.
- **19:01** — Live-vs-backup md5 parity probe (one az call, ~33s wall): all 13 EQ. Rollback verified byte-identical.
- **19:07** — Local md5 (origin/main LF-normalized) baseline computed; size deltas surfaced (prod is mostly main-ahead-of-prod by large margins; only `webhooks.py` + `risk.yaml` are LARGER on prod, both turned out to be CRLF artifacts).
- **19:15** — Pull v3 (native PowerShell) started; 62 chunks × ~33s each.
- **19:53** — Pull v3 completed (38 min wall); md5 verified `ce78074a85f1ee8cdaf3dedc54e4e715` ✓.
- **19:55** — Diff harness ran; ~1 Category-A symbol (`odds_api_key`) + ~84 Category-C symbol tokens. Investigation of small `+N` diffs confirmed all C-class (stale code / comment drifts).
- **20:02** — Extended-surface probe: systemd unit, drop-ins, EnvironmentFile keys, /home/azureuser/trading_corp/.env (not present). Confirmed `ODDS_API_KEY` must come from KV → `expected_env_vars` is load-bearing.
- **20:04** — `config/risk.yaml` 3-chunk pull; LF-normalized md5 EQ vs main; no findings.
- **20:08** — Item 2 commit `ffbb09b` (the 2 missing ODDS_API_KEY sublocations) + secrets tests re-green; commit pushed (separately from the audit report commit).
