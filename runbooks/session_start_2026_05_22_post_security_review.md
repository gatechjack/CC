# Next-session pickup prompt (2026-05-22 — post security review)

*Written 2026-05-22 at the end of the security-review session. Sole work
thread: comprehensive InfoSec audit. No code changes, no deploy. Report
committed as `e88d663`.*

---

Paste this into a fresh Claude Code session at `C:/Users/AA Incorporado/cc`:

---

Resuming after the 2026-05-22 security-review session. `e88d663` (security
report) is committed locally on `main`, on top of `92d6018` (deploy_log)
and `a6885a5` (data-provider abstraction). **3 ahead of `origin/main`,
NOT pushed.** Prod is unchanged from the 2026-05-22 10:33 UTC data-provider
deploy. No security-review findings have been remediated yet.

Read the **EOS snapshot at the top of `BACKLOG.md`** (`2026-05-22
(post-security-review)`) first — it's the canonical record of where this
branch left off.

## Headlines from last session

- **Comprehensive security audit** of `trading_corp/` + Azure architecture
  ran via Opus + 4 parallel Sonnet Explore agents (webhook attack surface,
  LLM integration, dependency/supply chain, deploy/ops). Output is a
  1,324-line report at `reports/2026-05-21_security_review.md`. Committed
  as `e88d663`. No code changes.
- **7 CRITICAL findings.** Three could each, alone, drain or freeze the
  prod account: (1) the workstation `.env` appears to hold live secrets,
  (2) the `TradeConfirmation push_back` LLM verdict at `web/webhooks.py:582`
  and `:826` returns BEFORE `RiskAgent.evaluate()` runs (verified — the
  return is at line 582, the risk gate at line 623), (3)
  `_check_auto_execute` re-reads `config/strategies.yaml` per-order with no
  mtime cache and no schema validation (already flagged in
  `docs/sharp_edges.md` but treated as harmless).
- **17 HIGH, 22 MEDIUM, 13 LOW** findings. Full table in the report.
  Prioritized roadmap at §5: Immediate (≤24h), Short-term (≤2w),
  Medium-term (≤8w). VM-side verification command list at §7.
- **Tastytrade env-var KV-bypass finding** surfaced when the operator
  was editing `/etc/trading-corp/tastytrade.env` on prod. `TASTYTRADE_*`
  vars are read from `os.environ` but are NOT in
  `utils/secrets.py:192-222 expected_env_vars`, so they bypass KV +
  redaction. Logged as a new P1 BACKLOG item; **bundle with the AM SDK
  bug fix** since both touch the same provider.

## Read first

1. **`BACKLOG.md` top entry (EOS 2026-05-22 post-security-review)** —
   canonical wrap. Lists the 7 CRITICAL items and the env sync state.
2. **`reports/2026-05-21_security_review.md`** — full report. The §5
   roadmap is the operational checklist; §3 is the findings table.
3. **`BACKLOG.md` new sections** — `## P0 — Security review remediation
   roadmap` and `## P1 — Tastytrade env vars bypass KV path`.
4. **Memory (auto-loaded):** `project_security_review_2026_05_22.md`,
   `feedback_tastytrade_env_vars_bypass_kv.md`.

═══════════════════════════════════════════════════════════════════════════
## What to work on next — Board picks ONE of these tracks
═══════════════════════════════════════════════════════════════════════════

The security review is intentionally a menu, not a march. Pick ONE based
on what feels right today. Each is self-contained.

### TRACK A — Critical secret rotation (the most consequential, 1–3h)

S-1 in the BACKLOG / C-1 in the report. The `.env` on the dev workstation
has been treated as compromised; rotation today is the single most
leveraged hour of work. Coordination-heavy (every broker portal,
Anthropic console, BotFather, Telegram, KV upload). After rotation,
depopulate the workstation `.env` to just `KEY_VAULT_URI=...`.

**Don't start this without uninterrupted time** — partial rotation is
worse than no rotation if it strands the trading service mid-cycle.

### TRACK B — `push_back` LLM bypass (1–2h, no coordination needed)

S-2 in the BACKLOG / C-2 in the report. Patch `web/webhooks.py:582-593`
and `:826-845` (Cypher mirror) so that `consult.decision == "skip"` routes
through `RiskAgent.evaluate()` with a `forced_reject_reason` parameter
rather than bypassing it. Also patch `consult_research_for_trade_confirmation`
to disallow `suggested_modifications.side` flips (BUY↔SELL).

**Smallest blast radius. Easiest to test in paper mode. Best pick if you
have a short window today.** Risk gate is the chokepoint per CLAUDE.md
§1 invariant 1 — restoring that invariant for real is the win.

### TRACK C — `strategies.yaml` hot-reload validation (2h)

S-3 in the BACKLOG / C-3 in the report. Add Pydantic schema validation
plus mtime caching plus a `strategies_yaml_reloaded` audit row on every
change. Touch `graph/ceo_graph.py:113-267` and add a new schema file under
`config/`. Tests in `tests/test_strategies_yaml.py` (new). Long-term, move
`auto_execute_caps` into KV — separate ticket.

### TRACK D — Dependency lockfile (30m–1h, then ~2h soak)

S-6 in the BACKLOG / C-6 in the report. `pip-compile --generate-hashes`
or `uv lock`. Pin `tvdatafeed` and `tradingview-ta` to specific versions
(they currently have no version specifier at all). Update the deploy
script to use `pip install --require-hashes -r requirements.lock`. Soak
in paper mode for a session before rolling to prod.

### TRACK E — Bundle Tastytrade KV consolidation with the AM SDK bug fix

If the AM SDK-bug fix (per `session_start_2026_05_22_data_provider_am_fix.md`)
is still pending or you're picking that up first, fold the
`TASTYTRADE_*` → KV migration into that same fix branch. See the
P1 BACKLOG entry "Tastytrade env vars bypass KV path" for the exact
patch list. **One deploy, two improvements, same risk envelope.**

### TRACK F — VM-side verification spree (1h)

Run the 13 commands in `reports/2026-05-21_security_review.md` §7 on
`tc-prod-vm` and report results back. Useful to do BEFORE deciding on the
HIGH-severity items in the report (some may already be fine — e.g., the
Caddyfile might already have HSTS configured). Output goes into a new
runbook `runbooks/2026-05-22_vm_security_state.md`.

═══════════════════════════════════════════════════════════════════════════
## Hard rules — don't skip
═══════════════════════════════════════════════════════════════════════════

- **CLAUDE.md §4 — "Things to ask before doing"** applies to most of the
  security tracks above. Track B touches webhook code path (single-risk
  chokepoint). Track A touches secrets handling + KV fetch path. Track C
  touches risk-orchestration logic. Get explicit in-session approval
  before changing files in the protected list.
- **Paper-mode default still active.** No track above warrants a
  `--live` change.
- **CLAUDE.md §1 invariant 6:** every Python invocation that touches
  `trading_corp/` or `tests/` runs through `.\scripts\run_capped.ps1`.
- **The AM SDK-bug fix branch** (per
  `session_start_2026_05_22_data_provider_am_fix.md`) is a separate
  workstream. If it's still pending, that has the hard 13:45 UTC deadline
  before the IC scan; security-review tracks don't.

═══════════════════════════════════════════════════════════════════════════
## Environment sync state
═══════════════════════════════════════════════════════════════════════════

| Surface | State |
|---|---|
| Local working tree | Clean except `docs/Deployment notes.txt` untracked (pre-existing, not mine) |
| Local `main` | `e88d663` (security review) > `92d6018` (deploy_log) > `a6885a5` (data-provider) > `origin/main` |
| `origin/main` | 3 behind local. Not pushed. Operator's standing decision. |
| Prod (`tc-prod-vm`) | Unchanged since 2026-05-22 10:33 UTC. PID 1044543 paper mode. None of the security findings remediated. |
| Memory | New: `project_security_review_2026_05_22.md`, `feedback_tastytrade_env_vars_bypass_kv.md` |

`git log --oneline -5`:
```
e88d663 reports: comprehensive security review (2026-05-21)
0df5697 runbooks: session wrap (2026-05-22 ~11:00 UTC) — BACKLOG EOS + AM-fix pickup
92d6018 runbooks: log data-provider deploy (a6885a5, 2026-05-22 10:33 UTC) — degraded, 2 SDK bugs queued for AM
a6885a5 data: MarketDataProvider abstraction + Tastytrade primary + 1e-5 fix
ad2f1df runbooks: next-session pickup — post-B7+B9 reconciler deploy (2026-05-22)
```

═══════════════════════════════════════════════════════════════════════════
## Stop conditions
═══════════════════════════════════════════════════════════════════════════

Bail OUT (do NOT push through) if:

- The track is touching the live broker path (Otter/Cypher webhook, PMCC
  scan, `risk.py`, `data_exec.py`, broker adapters) and you didn't get
  Board approval at session start.
- A "fix" for a security finding starts cascading into a refactor (e.g.,
  patching `push_back` requires reorganizing `RiskAgent.evaluate()`'s
  signature). Stop, report, ask.
- Rotation in Track A surfaces a credential you can't access (e.g.,
  Robinhood requires SMS that's unavailable). Halt rotation; do NOT leave
  half-rotated state.
- Any track requires editing CLAUDE.md, `runbooks/` (other than appending
  new files), or `infra/main.bicep` without a co-located deploy plan.

End of pickup brief.
