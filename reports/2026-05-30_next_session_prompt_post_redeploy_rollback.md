# Next-session prompt — post-redeploy-rollback (Plan A attempt #2 rolled back)

**Written:** 2026-05-30 at session close after the second Stage-1 rollback (Plan A attempt #2 ROLLED BACK at 23:09 UTC on `WebDeps.tasty_division` TypeError).

**State at write time:**
- `origin/main` HEAD: `309e39e` (merge of `stage1-forward-fix-2026-05-30` carrying Item 1 + Item 2 + audit + rollback entries — UNCHANGED after redeploy rollback; the buggy merge stays on main as forensic artifact since the bug pre-existed the merge).
- Prod state: **UNCHANGED.** Still on the post-17:34-rollback stable state (PID `1874494`, NRestarts=0, healthz=200). Both backup tags preserved (`pre-stage1-20260530-1230` + `pre-stage1-redeploy-20260530-2244`).
- Stage 1 + gate (a) + tasty_options + Item 1 + Item 2 are LANDED on `origin/main`. The redeploy bug is in pre-existing code (since 2026-05-24 tasty_options build), not in any forward-fix work.

---

## Recommended next: Forward-fix Item 3 + Item 4 (no prod deploy yet)

The main-to-prod deploy is still BLOCKED on Item 3 (WebDeps `tasty_division` field missing on dataclass). Item 4 is a defense-in-depth gate.

### Read first (in order)

1. **Memory entries**
   - `[[stage1-redeploy-rolled-back-2026-05-30]]` — today's second rollback.
   - `[[webdeps-tasty-division-latent-bug-2026-05-30]]` — the defect to fix.
   - `[[stage1-deploy-rolled-back-2026-05-30]]` — first rollback (now closed by Items 1 + 2).
   - `[[pre-deploy-filesystem-audit-discipline]]` — the Finding #9 gate that validated audit-not-stale this session.
   - `[[mocks-dont-catch-sdk-shape]]` + `[[verify-premises-against-ground-truth]]` + `[[no-documented-leaky-escape-hatch]]` — discipline anchors.

2. **Reports + audits**
   - `runbooks/deploy_log.md` "## 2026-05-30 22:43-23:09 UTC" entry — full second-rollback timeline + root cause.
   - `runbooks/deploy_log.md` "## 2026-05-30 17:22-17:34 UTC" entry — first-rollback timeline (Items 1 + 2 closure context).
   - `reports/2026-05-30_uncommitted_prod_surgical_edits_audit.md` (on `origin/main` now) — the prod-vs-git audit.
   - `BACKLOG.md` "P1 — Stage-1 prod-deploy BLOCKED" entry — Items 1+2 closed, Items 3+4 open.

3. **Code surfaces**
   - `trading_corp/main.py` lines 1239 + 1603 + 1932 + 1972 — where `tasty_division` is constructed + passed.
   - `trading_corp/web/app.py:31` — `WebDeps` dataclass (the surface to add `tasty_division: Any = None` to).
   - `tests/test_secrets_completeness.py` — the AST completeness test to extend.

### Item 3 — Forward-fix WebDeps tasty_division (the blocker)

1. **Add field on WebDeps.** In `trading_corp/web/app.py` line ~78 (next to `ic_division: Any = None`), add `tasty_division: Any = None`. Mirror the existing optional-field comment pattern.
2. **Run the test gate.** Should be 2044/28/3 worktree = 2046/26/3 main-baseline equivalent (no regression). The completeness test as it exists today does NOT cover this case — so the test gate is necessary but not sufficient for the structural class.
3. **Verify the fix locally** via the same import sanity check pattern: `python -c "from trading_corp.main import run; print('ok')"` — should return cleanly (not the gate of interest, but a smoke). The REAL gate is Item 4.
4. **Commit + merge to main.** Branch off `309e39e`. Single commit for the field add. Push as PR or directly per operator (prior session got direct-to-main authorization with explicit operator sign-off).

### Item 4 — Extend AST completeness test to cover `<DataclassName>(X=...)` kwarg patterns

Currently `tests/test_secrets_completeness.py` covers `secrets.X` attribute-access patterns. Extend to cover ALL dataclass-construction kwargs called in `main.py`:

1. **Parse `main.py` for `Call` AST nodes** whose `func` is a `Name` matching a known dataclass (`WebDeps`, `Secrets`, future dataclasses). Build the list dynamically by scanning the imports + dataclass definitions in the imported modules.
2. **For each such call**, enumerate the keyword arguments and assert each is in `fields(cls)`.
3. **Fail loudly** with the file path + line number + offending kwarg + the dataclass's actual fields. Same shape as the existing completeness test's failure messages.
4. **Add a same-class test** that constructs `WebDeps(...)` with every kwarg `main.py` passes, against the actual `WebDeps` class. This catches the case where main.py passes a `field=None` that WebDeps doesn't take.

Item 4 is REQUIRED before the next prod-deploy attempt because the bug class is structural — Item 3 fixes the symptom (tasty_division) but the same class can recur with other dataclasses.

### (Optional, recommended) Item 4b — Pre-deploy startup-equivalent dry-run gate

Beyond the AST test: a `pytest`-time fixture that constructs a full `Secrets()` + minimal mock collaborators and invokes the early portion of `run()` (up through `_start_web_server`), trapping any TypeError/AttributeError. Bounded by a short timeout (5s). Catches the broader class of "runtime TypeError in startup code path".

Stronger than Item 4 alone because Item 4 only catches kwarg-name bugs at construction; this catches everything pre-web-bind.

### After Item 3 + Item 4 land

Repeat Plan A attempt #3 per the same prompt pattern:
- Pre-deploy gates: test gate + gate (c) md5-diff + CRLF cross-verify + Finding #9 filesystem audit + YAML pre-check + import-graph audit + transfer set verification.
- Backup tag: `pre-stage1-redeploy-<YYYYMMDD>-<HHMM>` (distinct from `pre-stage1-20260530-1230` and `pre-stage1-redeploy-20260530-2244`).
- 18-file whole-file transfer via the chunked `az vm run-command --scripts @file` pattern this session validated.
- HARD STOP before restart for phone-in-hand RH-pickle device-push coordination.
- Hard stops on NRestarts increment beyond first, healthz failure within 10 min, etc.

### Alternative session paths (if operator defers Item 3/4)

- **Tastytrade C-1 rotation** — P1 ceiling 2026-06-12 per `[[reference-tastytrade-refresh-token-no-self-rotation]]`. Independent of Stage-1 deploy.
- **N+2 Phase 3 (live exit path)** — separate Stage-1 work, also unblocks live trading.
- **Finding #10 architectural-review decisions** — 8 queued, no-deploy.

### Discipline standard (carry forward)

- Operator-supervised. STOP-and-report at every fork.
- Worktree isolation; main working tree untouched.
- Per-file md5-verify on every transfer + every rollback.
- NEW filesystem-audit gate per `[[pre-deploy-filesystem-audit-discipline]]` — re-probe prod state before any whole-file transfer.
- Hard stops on NRestarts increment (operator's threshold: more than once or sustained pattern).
- No forward-fix in a deploy session; no scope expansion mid-task.

---

## State verification commands (for fresh session start)

```bash
cd "C:\Users\AA Incorporado\cc"
git rev-parse origin/main      # should equal 309e39e
git log --oneline 309e39e -10  # should show the merge + Items 1+2 + rollback entries
git worktree list               # stage1-redeploy-2026-05-30 worktree may still exist; safe to remove
```

```bash
# Prod state probe (read-only, operator-supervised)
az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm \
  --command-id RunShellScript \
  --scripts 'systemctl is-active trading-corp; systemctl show trading-corp --property=MainPID --property=NRestarts; date -u'
curl https://trading.jacksumner.com/healthz  # expect 200
```
