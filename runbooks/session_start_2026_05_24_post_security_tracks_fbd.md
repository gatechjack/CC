# Next-session pickup prompt (post security tracks F+B+D)

*Written 2026-05-23 ~20:30 UTC at the end of a security-remediation session that shipped three tracks back-to-back: TRACK F (VM verification), TRACK B (CRITICAL C-2 fix), TRACK D (CRITICAL C-6 lockfile). No deploys this session; prod is unchanged.*

---

Paste this into a fresh Claude Code session at `C:/Users/AA Incorporado/cc`:

---

Resuming after the 2026-05-23 security-tracks F+B+D session. **Four commits on `origin/main`** (last session pushed):
- `4086221` — security: hash-pinned `requirements.lock` + pin TV deps (closes CRITICAL C-6)
- `19ff0da` — security: route LLM push_back through risk gate + reject side flips (closes CRITICAL C-2)
- `8d72dcc` — backlog: 3 new VM-security anomalies from §7 spree
- `d1402b5` — runbooks: VM-side security state verified 2026-05-23 (§7 spree, 13/13 checks)

**Prod is UNCHANGED** from the bitunix bias-TTL + flip-detection deploy 2026-05-23 15:52 UTC. None of the 4 commits above are on prod yet — both C-2 and C-6 are CLOSED IN CODE, NOT DEPLOYED.

## Read first

1. **`BACKLOG.md` top entry (EOS 2026-05-23 ~20:30 UTC)** — canonical wrap of last session. Lists the 4 commits + deploy gating + 3 new anomalies + 2 erratum findings.
2. **`runbooks/2026-05-23_vm_security_state.md`** — TRACK F output. 13/13 §7 checks completed; per-check verdict + cross-reference to the report. Drives which HIGH-severity items in the report still need work.
3. **`reports/2026-05-21_security_review.md`** §5 (roadmap). C-2 and C-6 now closed-in-code; A/C/E and the new P1 NOPASSWD:ALL are the next high-leverage picks.
4. **Memory (auto-loaded):** `project_security_tracks_fbd_shipped_2026_05_23.md`, `reference_uv_pip_compile_cross_platform.md`, updated `project_security_review_2026_05_22.md`.

═══════════════════════════════════════════════════════════════════════════
## What to work on next — Board picks ONE
═══════════════════════════════════════════════════════════════════════════

The CRITICAL queue has shifted. Two of the three "drain or freeze prod" items are now patched-but-not-deployed; the third (C-1 secret rotation) is unchanged.

### TRACK B-DEPLOY — Push the C-2 fix to prod (1–2h, gated)

`19ff0da` is the highest-leverage item still queued. Webhook-path change — needs **explicit in-session approval** per CLAUDE.md §4. Steps:

1. Back up on prod: `web/webhooks.py`, `agents/risk.py`, `agents/research/trade_confirmation_consult.py` (`/tmp/backup_<file>_<ts>.bak`).
2. `scp` the 3 files OR `az vm run-command invoke` to overwrite (mind the 28KB `--scripts` cap — use scp).
3. `sudo systemctl restart trading-corp`.
4. **Post-deploy verification (load-bearing):** trigger or wait for a real push_back; verify in the DB:
   ```sql
   SELECT ts, payload FROM audit_event
    WHERE kind='risk_rejected'
      AND json_extract(payload,'$.source')='llm_push_back'
    ORDER BY ts DESC LIMIT 1;
   ```
   The whole point of the fix is making the LLM veto visible as an audited reject. If no row lands in the first few real consults, the deploy didn't take.
5. Append a deploy_log entry per the template.

**Rollback:** swap the backed-up files back; `systemctl restart`. Skip risk gate re-evaluation will resume bypassing as before — known-bad but operational.

### TRACK D-DEPLOY — Install the lockfile on prod (30m + 2h soak, gated)

`4086221` shipped a 3,310-line `requirements.lock`. Deploy steps:

1. `scp requirements.lock azureuser@trading.jacksumner.com:/home/azureuser/trading_corp/`.
2. In a paper-mode window: `/home/azureuser/trading_corp/venv/bin/pip install --require-hashes -r requirements.lock 2>&1 | tee /tmp/pip_install_<ts>.log`.
3. `pip list` diff before/after — if no package versions changed, no restart needed. If anything changed, restart trading-corp + soak ~2h paper before considering this a clean install.
4. Append deploy_log entry.

**Lower blast radius than TRACK B-DEPLOY** — no risk-path code change, just dep version locking. But still needs a soak window.

### TRACK A — Secret rotation (1–3h, coordination-heavy, unchanged)

C-1, the most consequential remaining CRITICAL. Same shape as the original brief — every broker portal, Anthropic console, BotFather, KV upload. Do not start without uninterrupted time. Sequence with the new P1 NOPASSWD:ALL fix (both touch VM state in the same envelope).

### TRACK C — `strategies.yaml` schema + mtime + audit (2h, §4)

C-3 fix. Pydantic schema + mtime cache + `strategies_yaml_reloaded` audit row. Touches `graph/ceo_graph.py:113-267`. §4 protected — needs in-session approval at start.

### TRACK E — Tastytrade KV consolidation (1h, §4)

Filed in BACKLOG as P1 "Tastytrade env vars bypass KV path". Patch list in `feedback_tastytrade_env_vars_bypass_kv.md`. Standalone since the AM SDK fix already shipped. §4 protected (touches `utils/secrets.py` KV-fetch path).

### NEW P1 from this branch — fix `azureuser` `NOPASSWD:ALL` sudo (1h, §4 VM)

Filed in `8d72dcc`. `/etc/sudoers.d/*` includes `azureuser ALL=(ALL) NOPASSWD:ALL` — partially undermines the C-4 remediation that's already done (service runs as `azureuser`). Replace blanket NOPASSWD with narrow allowlist. Edit via `visudo` only; keep a second SSH session open to test before closing the first. Classic VM lockout pattern.

═══════════════════════════════════════════════════════════════════════════
## Hard rules — don't skip
═══════════════════════════════════════════════════════════════════════════

- **CLAUDE.md §4 protected paths** apply to all deploy/code-change tracks above. Get explicit in-session approval before changing files in the protected list.
- **Paper-mode default still active.** No track above warrants `--live`.
- **CLAUDE.md §1 invariant 6:** every Python invocation that touches `trading_corp/` or `tests/` runs through `scripts\run_capped.ps1`.
- **Deploy is its own gate.** Code-shipped ≠ fix. The post-deploy verification step on TRACK B-DEPLOY (audit row check) is the load-bearing proof.

═══════════════════════════════════════════════════════════════════════════
## Environment sync state
═══════════════════════════════════════════════════════════════════════════

| Surface | State |
|---|---|
| Local working tree | Clean except 3 pre-existing untracked files (not mine; carry-over from prior sessions) |
| Local `main` | `4086221` (== `origin/main`) |
| `origin/main` | `4086221` (pushed end of session) |
| Prod (`tc-prod-vm`) | UNCHANGED since 2026-05-23 15:52 UTC (bitunix deploy). MainPID 1185736. None of this session's 4 commits are on prod. |
| Memory | NEW `project_security_tracks_fbd_shipped_2026_05_23.md`, NEW `reference_uv_pip_compile_cross_platform.md`, UPDATED `project_security_review_2026_05_22.md` |

`git log --oneline -6`:
```
4086221 security: hash-pinned requirements.lock + pin TV deps (C-6 fix)
19ff0da security: route LLM push_back through risk gate + reject side flips (C-2 fix)
8d72dcc backlog: file 3 VM-security anomalies from 2026-05-23 §7 spree
d1402b5 runbooks: VM-side security state verified 2026-05-23 (§7 spree)
c2c4faa backlog: EOS snapshot 2026-05-23 ~16:35 UTC - bitunix bias-TTL + flip-detection LIVE
7d34dbe deploy_log: bias TTL 90 -> 30 + flip-opportunity detection shipped 2026-05-23 15:52 UTC
```

═══════════════════════════════════════════════════════════════════════════
## Stop conditions
═══════════════════════════════════════════════════════════════════════════

Bail OUT (do NOT push through) if:

- A deploy track is touching webhook code AND you didn't get Board approval at session start.
- Post-deploy verification for TRACK B-DEPLOY does not produce a `risk_rejected` row with `source=llm_push_back` within a real-consult window. Roll back; investigate.
- TRACK D-DEPLOY `pip install --require-hashes` reports hash-mismatch on any package — DO NOT use `--no-deps` or `--ignore-hashes` workarounds. Re-generate the lockfile via the canonical uv command (see `[[reference-uv-pip-compile-cross-platform]]`).
- The NOPASSWD:ALL fix bricks your sudoers — DO NOT panic. Test in a second SSH session BEFORE closing the first. If you've already broken it, the VM is recoverable via Azure portal serial console.

═══════════════════════════════════════════════════════════════════════════
## Reference — anomalies surfaced last session (for context, not action)
═══════════════════════════════════════════════════════════════════════════

- **H-17 in the security review is stale.** Report says prod is Python 3.10.12; the venv that runs trading_corp is actually 3.12.13. Lockfile was generated for 3.12. Mention in any future report refresh.
- **`tvdatafeed` is NOT on PyPI** (HTTP 404). The line in requirements.txt was unversioned and never installed. Commented out (not deleted). The runtime gate (`ENABLE_TRADINGVIEW=1`) can't be turned on without finding an alternate source.
- **5 pre-existing failures in `tests/test_webhooks_return_fast.py`** — all `AttributeError: '_Deps' object has no attribute 'bitunix_observer'`. Pre-existing fixture gap (file 1+ week old; zero diff against current HEAD). Out of scope for this branch but worth fixing whenever someone next touches that file.

End of pickup brief.
