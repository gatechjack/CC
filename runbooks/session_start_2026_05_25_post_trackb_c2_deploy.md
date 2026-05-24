# Next-session pickup prompt (post TRACK B-DEPLOY C-2)

*Written 2026-05-24 ~21:30 UTC at the end of a session that shipped + verified the C-2 webhook risk-gate fix (`19ff0da`) to prod. Synthetic gates + real-HTTP forcing-hook test both passed. Deploy_log entry committed as `dcdd0ef` and pushed to origin. A parallel operator session also landed 7 commits this window (kalshi_weather autopsy + new pickup brief for that thread).*

---

Paste this into a fresh Claude Code session at `C:/Users/AA Incorporado/cc`:

---

Resuming after the 2026-05-24 ~21:30 UTC TRACK B-DEPLOY session. **`origin/main` head: `dcdd0ef`** (deploy_log close for TRACK B C-2). Prior commits in this session window:

- `dcdd0ef` — deploy_log: TRACK B C-2 deployed + verified via real-HTTP forcing-hook test *(this thread)*
- `7f6dc6d` — runbooks: next-session pickup prompt (post kalshi_weather autopsy) *(parallel-operator thread)*
- `84ceea9` — backlog: EOS snapshot 2026-05-24 ~20:00 UTC (kalshi_weather post-xref 24h autopsy) *(parallel)*
- `0ab8daa` — housekeeping: keep verified kalshi_weather corpus-fetch driver *(parallel)*
- `239e99c`, `ecc3367` — kalshi_weather reports *(parallel)*
- `475a2a2`, `89bafba`, `02f465f`, `ca00600` — prior-session work *(parallel)*
- `db6d805` — runbook: add strategy_harness_inventory.md (phantom-pointer fix) *(this thread, earlier)*

**Prod state is STABLE and CONVERGED:**
- Service PID `1296508` (started 2026-05-24 ~21:01 UTC, web bound 21:02:17 UTC), healthz `{"status":"ok","mode":"PAPER"}`.
- File state on prod = C-2 fix code from `19ff0da`. md5 for `web/webhooks.py` (CRLF) = `6fed0aa89c103ba475bd8901a8ab434a`; `agents/risk.py` (LF) = `49e4d138b41d78ce0e670a2b06c2fbc5`; `agents/research/trade_confirmation_consult.py` (LF) = `26c0c896875a6235932da1e86a0701e9`. Byte-for-byte matches git blob `19ff0da`. Zero `TRACKB` residue from forcing hooks.
- Local `main` ≡ `origin/main` ≡ `dcdd0ef` ≡ what's running on prod (semantically).

## Read first

1. **`BACKLOG.md` top entry** (EOS 2026-05-24 ~21:30 UTC, this thread) — wrap state, board picks, forward-watch.
2. **`runbooks/deploy_log.md`** TWO most recent entries: **2026-05-24 16:55 UTC** (TRACK B C-2 — verification record + forcing-hook recipe + forward-watch SQL + rollback) and the prior 14:40 UTC observer cap-bump entry.
3. **Memory:** `[[project-security-tracks-fbd-shipped-2026-05-23]]` (updated body — C-2 NOW DEPLOYED), `[[feedback-forcing-hook-real-path-verification]]` (NEW — the discipline lesson on synthetic-only vs real-HTTP-path proof).
4. **Parallel-thread brief:** `runbooks/session_start_2026_05_25_post_kalshi_weather_autopsy.md` — kalshi_weather observation-week pickup (Tracks A/B/C in that file are SEPARATE scope from this brief's board items).

═══════════════════════════════════════════════════════════════════════════
## Forward-watch obligation (carries forward — not a track to start, a watch to maintain)
═══════════════════════════════════════════════════════════════════════════

The C-2 fix is verified by SYNTHETIC + FORCED-REAL-PATH gates. It is NOT verified by NATURAL TV traffic yet because `lord_otter` and `market_cypher` strategies are currently **DISABLED in config** — every alert during the verification window (31 webhook_received: Otter 2 + Cypher 29) hit `alert_ignored` with reason `"lord_otter strategy is disabled in config"` before consult could fire.

**When the strategies are re-enabled** (operator decision, separate task), the FIRST FEW natural push_backs MUST audit a `risk_rejected/source=llm_push_back` row. If they don't, the wiring is broken in a way synthetic + forced-real-path tests missed; **rollback per the recipe in deploy_log**.

Surface query:
```sql
SELECT ts, json_extract(payload_json, '$.via'), json_extract(payload_json, '$.symbol'),
       substr(json_extract(payload_json, '$.reason'), 1, 80)
  FROM audit_event
 WHERE kind='risk_rejected'
   AND json_extract(payload_json, '$.source')='llm_push_back'
   AND ts >= '2026-05-24T16:55:43'
 ORDER BY ts DESC LIMIT 10;
```

Rollback (only if forward-watch shows wiring broken):
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-trackb-c2-20260524; BASE=/home/azureuser/trading_corp;
for f in trading_corp/web/webhooks.py trading_corp/agents/risk.py trading_corp/agents/research/trade_confirmation_consult.py; do
  mv \$BASE/\$f.\$TAG \$BASE/\$f
done
sudo systemctl restart trading-corp.service
"
```

═══════════════════════════════════════════════════════════════════════════
## What to work on next — Board picks ONE (5 items, original 7 minus closes)
═══════════════════════════════════════════════════════════════════════════

Two closed this session: **TRACK B-DEPLOY** (C-2 webhook risk-gate fix shipped + verified) and **Housekeeping phantom-pointer** (`strategy_harness_inventory.md` committed).

CRITICAL queue now: **C-2 closed, C-6 closed earlier today; only C-1 (secret rotation) remains as a CRITICAL.**

### TRACK A — C-1 Secret rotation (1–3h, coordination-heavy, §4-adjacent)

The most consequential remaining CRITICAL. Every broker portal, Anthropic console, BotFather, KV upload. **Sequence with the NEW P1 NOPASSWD:ALL fix** (both touch VM state in the same envelope). Partial rotation is worse than none. Do not start without uninterrupted time.

### P1 — Deferred 43-package upgrade from C-6 lockfile drift (multi-session, audited one-at-a-time)

The bumps the 2026-05-24 15:14 UTC C-6 correction reversed. Full table in `BACKLOG.md` P1 entry. **NOT a single batch.** Sequence:

1. **anthropic 0.97 → 0.104** — highest risk class. Per `[[feedback-mocks-dont-catch-sdk-shape]]`: real-SDK smoke test + live authenticated call. Audit `agents/llm.py`, `agents/research/*`, `agents/strategies/kalshi_crypto_arb.py` for return-shape dependencies before bumping.
2. **cryptography 47 → 48** — major. Check release notes for deprecations in `utils/secrets.py` (KV path), mTLS broker adapters, `web/webhooks.py` HMAC.
3. **langgraph 1.1.10 → 1.2.1 + langchain-core 1.3.2 → 1.4.0** — check `graph/ceo_graph.py` for deprecated APIs.
4. **starlette 1.0.0 → 1.1.0 + fastapi 0.136.1 → 0.136.3** — web layer.
5. ~30 patch/minor bumps — low individual risk.

Each bump = its own audit + deploy + soak. **No "and while we're at it".**

### TRACK C — `strategies.yaml` schema + mtime + audit (2h, §4)

C-3 fix. Pydantic schema + mtime cache + `strategies_yaml_reloaded` audit row. Touches `graph/ceo_graph.py:113-267`. §4 protected.

### TRACK E — Tastytrade KV consolidation (1h, §4)

P1 from `[[feedback-tastytrade-env-vars-bypass-kv]]`. Patch list in that memory. Standalone since the AM SDK fix already shipped. §4 protected.

### NEW P1 — fix `azureuser` `NOPASSWD:ALL` sudo (1h, §4 VM)

Filed in `8d72dcc`. `/etc/sudoers.d/*` has `azureuser ALL=(ALL) NOPASSWD:ALL` — partially undermines the C-4 remediation. Replace blanket NOPASSWD with narrow allowlist. Edit via `visudo` only; keep a second SSH session open as testing pad before closing the first.

### (Parallel thread) kalshi_weather observation-week brief

`runbooks/session_start_2026_05_25_post_kalshi_weather_autopsy.md` has its own A/B/C tracks. Don't confuse the alphabet — those are SEPARATE from TRACK A/C/E above.

═══════════════════════════════════════════════════════════════════════════
## Untracked files in working tree at session end
═══════════════════════════════════════════════════════════════════════════

ONLY `docs/Deployment notes.txt` (operator-owned, ~640 KB, last touched 2026-05-20). **The 3 other untracked from the prior handoff are explained:**
- `scripts/fetch_kalshi_weather_corpus.py` → committed by parallel-operator thread in `0ab8daa` ("housekeeping: keep verified kalshi_weather corpus-fetch driver"); now tracked.
- `classify_losses.py` → operator-removed from working tree (their own loss-analysis artifact; no commit touched it).
- `decode_losses.py` → operator-removed from working tree (same).

═══════════════════════════════════════════════════════════════════════════
## Hard rules — don't skip
═══════════════════════════════════════════════════════════════════════════

- **CLAUDE.md §1 invariants** — risk gate single chokepoint (now restored for the LLM-skip path via C-2); audit before each branch; paper default; `scripts/run_capped.ps1` wrap for Python; `runbooks/` no-edit without approval.
- **CLAUDE.md §4** — webhook/risk path changes need in-session approval. C-2 deploy is closed; further changes to `web/webhooks.py`, `agents/risk.py`, `agents/research/trade_confirmation_consult.py` need fresh §4 approval.
- **Deploys append to `runbooks/deploy_log.md`** per template.
- **Forward-watch on first natural push_back** — see top of this file.

═══════════════════════════════════════════════════════════════════════════
## Discipline notes from this session (worth keeping)
═══════════════════════════════════════════════════════════════════════════

- **Synthetic-on-loaded-module ≠ real-prod-HTTP-path proof.** New memory `[[feedback-forcing-hook-real-path-verification]]` captures the lesson + the reusable recipe (payload-marker-gated forcing branch in webhook handler + consult function; HMAC + KV secret; POST from localhost; revert + restart + `grep -c <MARKER>` = 0). The deploy_log entry at 16:55 UTC has the full recipe + the actual prod audit rows quoted.
- **`! ssh ...` inline-command newline split bug** still applies — keep `systemctl status <unit>` on one line, or use heredoc/for-loop.
- **Sleep blocked in Bash with run_in_background.** Use `until <check>; do sleep N; done` pattern in a single ssh script when polling for a remote state change; OR use the Monitor tool for streaming events.
- **Parallel operator commits during a long deploy.** Always re-check `git log origin/main..HEAD` AND `git log HEAD..origin/main` before commit/push. This session: 7 operator commits landed on origin while TRACK B was in flight; clean fast-forward push because my local was already on top of them (the harness fetched along the way).
- **Confirm with `git log --oneline <range> -- <file>`** before claiming you didn't touch a file. Three files were "vanishing" from working tree this session; investigation showed 1 committed by operator + 2 deleted by operator, none touched by me.
