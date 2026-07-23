# Session Handoff — 2026-07-22 (PMCC Bucket B deploy + git↔prod reconciliation + live-exec plan)

**For a fresh agent with zero context.** Read this top to bottom before touching anything.
Repo root: `C:/Users/AA Incorporado/cc` (resolves to `C:\Users\AA Incorporado\CC`). Prod is a
git-less Azure VM that is *ahead* of git (prod-direct hot-patches) — see the reconciliation section.

---

## 1. GIT STATE (verify before trusting)

- **`main` HEAD = `6760d6d`** — `docs: PMCC live-execution plan + BACKLOG entries`.
- **24 commits ahead of `origin/main`, 0 behind. UN-PUSHED.** (Nothing has been pushed this arc;
  do not push without explicit operator authorization.)
- **Tracked tree clean.** (Lots of untracked scratch/`*.ps1`/`*.sh` runners in the working dir —
  pre-existing, ignore them; never `git add -A`, always path-scoped.)
- Main lineage (top): `6760d6d` docs → `557a39f` reconcile robinhood.py+data_exec.py →
  `e2e2fad` reconcile secrets.py+divisions.yaml → `f1b8a86` reconcile main.py →
  `0923e60` PMCC Bucket B CLOSED → (Bucket B commits) …

## 2. PMCC BUCKET B — CLOSED + DEPLOYED LIVE + VERIFIED

- **Status: CLOSED.** 10 fixes built (B1 HOLD-precedence, B2 credit-gate, B3 old-LEAP price,
  B4 atomic-legs, B7 roll-out, B8 doc/dead-config, B9 earnings-gate, B10 15:00-ET terminal-DTE,
  B11 holiday-guard, + the roll_leap B9/B2 extension). **6 findings WITHDRAWN as endorsed design**
  (B5, B6, B3 cost/benefit-gate, B8 force-0.55, STRC, position-discovery; A3 falls with B5).
- **Deployed LIVE to prod 2026-07-22 ~17:57 UTC** (stage-by-stage, explicit per-stage auth).
  Boot verified: PID 322463, NRestarts=0, 37 secrets, 0 tracebacks; RH login 3 accounts;
  kalshi_arbitrage on the isolated Karen account $505.84; all divisions up.
- **★ The one thing boot-smoke couldn't prove is CONFIRMED:** the first live **15:00-ET
  terminal-DTE fire** landed — exactly one `scheduler/terminal_dte_pass_done` row at 19:02:57 UTC
  (15:02:57 ET), `result="no 0-DTE actions."`, 0 `terminal_dte_order_result` rows, per-day dedup
  held. B10 proven end-to-end.
- **`auto_execute` is FALSE** (robinhood_pmcc is paper, not in `--live-divisions`). Do NOT flip it.
- Detail: memory `pmcc-bucketb-deployed-live-2026-07-22`; plan `planning/pmcc_option2_bucketB_plan.md`;
  prior handoff `planning/pmcc_phase2_handoff_2026-07-22.md`. Rollback anchors on prod:
  `.bak_bucketb_20260722` (3 files).

## 3. GIT↔PROD RECONCILIATION ARC (this session's second thread)

Prod carried prod-direct hot-patches never committed to git. Four engine/config files now
**byte-mirror prod** (transcription, LF-normalized md5 verified == prod live):

| file | git commit | prod LF-norm md5 | what it carries |
|---|---|---|---|
| `trading_corp/main.py` | `f1b8a86` | `df37d7b5` | RH-auth ITEM3-WIRE + Kalshi Karen `secret_ref` broker-select (+ B10) |
| `trading_corp/utils/secrets.py` | `e2e2fad` | `e427c8e0` | RH-auth ITEM1 (`ROBINHOOD_*` KV + `_KV_AUTHORITATIVE`) + Karen `KALSHI_KAREN_*` |
| `config/divisions.yaml` | `e2e2fad` | `c8a18f69` | kalshi_arbitrage `secret_ref: kalshi_karen` |
| `trading_corp/brokers/robinhood.py` | `557a39f` | `9bd4ddff` | RH-auth 401 self-heal core (+118) |
| `trading_corp/agents/data_exec.py` | `557a39f` | `21ce7fd7` | `_on_rh_auth_change` auth hook (+25) |

- **A fresh git deploy now boots clean and self-heals** at the engine layer (`import trading_corp.main`
  OK; `main.py:1071` `_rh_mod._auth_alert_hook = data_exec._on_rh_auth_change` resolves to
  `DataExecAgent._on_rh_auth_change`; the robinhood 401 self-heal symbols are all present).
- **"Karen" = the isolated Kalshi API CREDENTIAL SET used BY the `kalshi_arbitrage` division via
  `secret_ref: kalshi_karen` — it is NOT a division.**
- **STILL un-reconciled (P3, non-boot-critical): the RH-auth WEB layer** — `trading_corp/web/routes.py`
  + templates `home.html` + `rh_session_panel.html` (dashboard Refresh-RH button + `/api/rh/session-health`
  panel). Engine boots/self-heals without them; only the dashboard panel is absent.
- Memory: `rh-auth-git-reconcile-2026-07-22`; method + partial-reconcile hazard in
  `main-prod-reconciled-2026-07-08`.

## 4. PMCC LIVE-EXECUTION PLAN — PLANNED, NO BUILD STARTED

- **`planning/pmcc_live_execution_plan.md`** — investigation + phasing only. No code, no branch,
  no spec commitments.
- **★ HEADLINE (Phase A exists to fix this):** PMCC roll legs are dispatched as INDEPENDENT
  single-leg orders, so **B4's atomicity guarantee does NOT survive live execution** — close fills +
  open rejects = a naked position at the fill layer, recreating what B4 fixed at the proposal layer.
  The atomic combo plumbing EXISTS (`place_multi_leg` → `rs.orders.order_option_spread`, one POST /
  all-or-nothing) but is used only by the iron condor; **PMCC is disconnected from it.**
- Assignment/exercise handling is **wholly absent** (not thin); paper mode structurally couldn't
  surface it. Phase D (the `auto_execute` flip) is a **separate terminal gate** requiring Phase A–C
  evidence + explicit per-decision authorization — plumbing readiness is a precondition, not a trigger.

## 5. OPEN ITEMS (status)

- **PEAD notional-cap NO-OP** — `robinhood_pead` (LIVE **real money** since 2026-06-24) has no
  enforced per-order $ cap (market orders → `ref_price=0` → `risk.py:213` cap skipped;
  `per_trade_risk_pct 1.0` off; `auto_execute_caps` unenforced — PEAD bypasses `ceo_graph`).
  **OPERATOR ATTENTION ONLY — NO fix authorized, out of scope. Do not touch PEAD.**
  Memory `pead-notional-cap-noop-2026-07-22`; BACKLOG.
- **RH-auth web-layer drift** — P3 git-hygiene (see §3). BACKLOG.
- **4-leg `roll_leap` atomicity** — UNRESOLVED. It does not map to one RH combo; whether it
  decomposes into sub-combos or is quarantined is a **Phase-A design decision**, not an
  implementation detail. Recorded in the live-exec plan.

## 6. STANDING RULES A NEW AGENT MUST INHERIT (non-negotiable)

- **Authorization is PER-DEPLOY / PER-DECISION and EXPLICIT.** No standing or autonomous "go" exists
  (even a Board "autonomous deployment approved" was NOT standing — it was corrected to per-deploy).
  Never assume a blanket go; get an explicit go for each deploy/flip.
- **`auto_execute` stays FALSE.** No prod writes / deploy / restart without an explicit go.
- **No prod sudo.** Operator has no sudo password; NOPASSWD scope = `systemctl`/`journalctl`/`sqlite3`
  for `trading-corp` only. **Root ops (incl. engine restart) go via Azure Run Command**
  (`az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "…"`).
  Memory `prod-sudo-constraint-no-password`.
- **command-paste-rule for ssh** — pipe the remote command to `bash` via STDIN (not as an ssh arg),
  pure ASCII, no wrapping; hand the operator a one-line `.ps1` runner if they must paste. Memory
  `command-paste-rule`.
- **Fork-and-stop on any test** that cannot be made valid without changing what it asserts. Never
  rewrite a test to match new behavior.
- **An unenforced rule is a defect ONLY if enforcement was INTENDED** (endorsed-design test) — a gap
  deliberately delegated to the LLM/operator is not a bug.
- **Byte-equivalence before commit; full diff before commit; no commit until operator review;
  path-scoped `git add` only.** Stop-and-report at forks; surface anomalies with detail; don't
  expand scope mid-task. Memory `working-discipline`.

## 7. RECONCILIATION METHOD (how the git↔prod arc was done — reuse verbatim)

1. **Transcription, NOT reimplementation** — capture prod's actual bytes off-box (scp to a scratch
   dir OUTSIDE the repo), branch off `main`, write prod's content in verbatim (formatting preserved).
2. **Verify LF-normalized md5 == prod live** before AND after commit (`git cat-file blob … | tr -d '\r'
   | md5sum`). If not byte-identical, STOP.
3. **Watch line-endings:** repo has `core.autocrlf=true`, no `.gitattributes`. `main.py` is stored
   **CRLF** in git; most other files LF. Transcribe to match (a whole-file diff instead of a clean
   hunk diff means you got the EOL wrong).
4. **★ Enumerate the WHOLE patch set before reconciling ANY one file** of a multi-file prod-direct
   patch. Reconciling a subset can leave git booting WORSE than either endpoint (proven this arc:
   main.py's hook line without data_exec.py = fresh-deploy `AttributeError`). Read the deploy
   artifacts (e.g. `deploy_rh_auth/*.patch`) and grep for cross-file references. Reconcile
   boot-critical companions together. Memory `main-prod-reconciled-2026-07-08` (hazard + method).
5. **No deploy** — reconciliation is git catching up to prod; prod already runs it.

## 8. TEST SUITE BASELINE (do NOT treat as a regression)

- Full suite (`python -m pytest tests/ -q --ignore=tests/test_whale_autopause_epoch.py`) lands at
  **40 failures, STABLE across runs** (verified 3× baseline + 3× reconciled = all 40).
- **All 40 are pre-existing / environmental** — local **Python 3.14** vs prod's **3.12**, plus flaky
  bitunix cross-test state (robinhood_multi_leg / iron_condor / tasty / webhooks fail wholesale on
  import/dep grounds; bitunix_sfp_observer count bounces). **A new agent must NOT read 40 as a
  regression.** Prod runs 3.12 and is healthy. The PMCC suite + readiness gate pass.
- `tests/test_whale_autopause_epoch.py` is an UNTRACKED cross-branch test (belongs to
  `kalshi-poly-autopause-epoch-2026-07-20`); its `resolve_epoch` impl isn't on main — ignore it in
  suite runs (that's why the `--ignore` flag).

## 9. BRANCH INVENTORY

- **`main` @ `6760d6d`** — everything: Bucket B + all 3 reconcile commits + docs. THE trunk. Un-pushed.
- `rhauth-robinhood-dataexec-reconcile-2026-07-22` @ `557a39f` — robinhood.py+data_exec.py reconcile
  (now an ancestor of main).
- `karen-rhauth-secrets-divisions-reconcile-2026-07-22` @ `e2e2fad` — secrets.py+divisions.yaml
  reconcile (ancestor of main).
- `main-prod-reconcile-2026-07-22` @ `f1b8a86` — main.py reconcile (ancestor of main).
- `pmcc-bucketb-final-2026-07-22` @ `0923e60` — Bucket B close-out (ancestor of main).
- `pmcc-bucketb-phase0/phase1/phase2/phase2.5-*` — historical Bucket-B phase branches (superseded by
  `0923e60`; keep for history, don't build on).
- **`kalshi-poly-autopause-epoch-2026-07-20` @ `40d6166`** — the PARALLEL copy-trading shadow-autopause
  work (branched off the older `6afe2cd`, NOT off recent main). It preserves prod-live copy-trader
  `autopause_mode: shadow` config + `resolve_epoch` that is deliberately kept OFF main. Do not merge
  it into main without operator direction.

---

*Session close: 2026-07-22. No new work pending in this file — this is a state snapshot. Everything
above is committed on `main` @ `6760d6d` (un-pushed) except the memory files (auto-persisted under the
agent memory store). auto_execute FALSE; no prod writes this session beyond the explicitly-authorized
Bucket B deploy.*
